// Camelot mod loader — the plugin.
//
// Runs once at boot through the server's IDatabaseUpdater extension point,
// after the scripts are compiled and the server properties are loaded. It
// reads a folder of mod archives, decides what to install, update or remove,
// and does it.
//
// Three properties hold the design together:
//
// - a mod never chooses an id. It declares how many it needs and the loader
//   allocates, records and writes, so two mods cannot overlap by
//   construction and the same mod installs on two servers under different
//   ids;
// - a mod ships no SQL. Rows are columns and values, written through the
//   server's own DataObject layer, so a third-party mod cannot execute what
//   it likes against a database;
// - one bad mod never stops a server. The hook's caller treats a false
//   return as a failed boot, so everything here is caught per mod and
//   recorded in CamelotMod rather than thrown.

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using DOL.Database;
using DOL.Database.Attributes;
using DOL.GS.DatabaseUpdate;
using DOL.GS.ServerProperties;
using DOL.Logging;

namespace DOL.GS.CamelotMods
{
    // The attribute is what the server's updater loop looks for; without it
    // the class is compiled and then skipped in silence.
    [DatabaseUpdate]
    public class CamelotModLoader : IDatabaseUpdater
    {
        private static readonly Logger log =
            LoggerManager.Create(MethodBase.GetCurrentMethod().DeclaringType);

        /// <summary>Where mod archives are looked for, relative to the
        /// server root.</summary>
        [ServerProperty("camelot", "mod_directory",
            "Directory holding Camelot mod packs (.tar.gz), relative to the server root",
            "scripts/mods")]
        public static string MOD_DIRECTORY;

        public void Update()
        {
            var directory = ResolveDirectory();
            if (directory == null)
            {
                if (log.IsWarnEnabled)
                    log.Warn($"mod directory '{MOD_DIRECTORY}' does not exist; no mods loaded");
                return;
            }

            var present = new Dictionary<string, ModPack>(StringComparer.OrdinalIgnoreCase);
            foreach (var path in Directory.GetFiles(directory, "*.tar.gz").OrderBy(p => p, StringComparer.Ordinal))
            {
                try
                {
                    var pack = ModPack.Load(path);
                    if (present.ContainsKey(pack.Document.ModId))
                    {
                        Fail(pack.Document.ModId, null,
                            $"two packs claim the id: {path} and an earlier file");
                        continue;
                    }
                    present[pack.Document.ModId] = pack;
                }
                catch (Exception e)
                {
                    // Recorded and skipped: an unreadable or tampered pack
                    // must not take the server down with it.
                    log.Error($"mod pack refused: {path}: {e.Message}");
                }
            }

            foreach (var pack in present.Values.OrderBy(p => p.Document.Priority))
                Apply(pack);

            RemoveAbsent(present.Keys);
        }

        private static string ResolveDirectory()
        {
            var configured = string.IsNullOrWhiteSpace(MOD_DIRECTORY) ? "scripts/mods" : MOD_DIRECTORY;
            var root = GameServer.Instance.Configuration.RootDirectory;
            var full = Path.IsPathRooted(configured) ? configured : Path.Combine(root, configured);
            return Directory.Exists(full) ? full : null;
        }

        // ---------------------------------------------------------------- apply

        private void Apply(ModPack pack)
        {
            var doc = pack.Document;
            try
            {
                foreach (var requirement in doc.Requires)
                {
                    if (ModStore.Existing(requirement, "any") == null &&
                        ModStore.For(requirement).Count == 0)
                    {
                        Fail(doc.ModId, doc.Version, $"requires {requirement}, which is not installed");
                        return;
                    }
                }

                foreach (var allocation in doc.Allocations)
                {
                    if (ModSchema.KeyColumnFor(allocation.Table) == null)
                    {
                        Fail(doc.ModId, doc.Version,
                            $"table {allocation.Table} has no allocatable key in this loader");
                        return;
                    }
                    if (ModSchema.TypeForTable(allocation.Table) == null)
                    {
                        Fail(doc.ModId, doc.Version,
                            $"table {allocation.Table} has no DataObject type in this server");
                        return;
                    }
                }

                var rowsByTable = doc.Rows.GroupBy(r => r.Table, StringComparer.OrdinalIgnoreCase);

                // Already applied at this version: leave it alone rather than
                // rewriting the block on every boot.
                var records = ModStore.For(doc.ModId);
                if (records.Count > 0 && records.All(r =>
                        string.Equals(r.Version, doc.Version, StringComparison.Ordinal) &&
                        r.Result == "SUCCESS"))
                {
                    if (log.IsDebugEnabled)
                        log.Debug($"{doc.ModId} {doc.Version} already applied");
                    NoteClientPayloads(doc);
                    return;
                }

                foreach (var group in rowsByTable)
                {
                    var table = group.Key;
                    var record = ModStore.Existing(doc.ModId, table);
                    if (record == null)
                    {
                        ModStore.Allocate(doc.ModId, table, group.Count());
                        record = ModStore.Existing(doc.ModId, table);
                    }

                    var needed = group.Count();
                    var available = record.IdHigh - record.IdLow + 1;
                    if (needed > available)
                    {
                        // Refused rather than reallocated: in-game data may
                        // already point at the existing block.
                        Fail(doc.ModId, doc.Version,
                            $"{table} needs {needed} ids but owns {available}; reinstall the mod");
                        return;
                    }

                    WriteRows(doc.ModId, table, group.ToList(), record.IdLow);
                    ModStore.Record(record, doc.Version, "SUCCESS");
                }

                if (log.IsInfoEnabled)
                    log.Info($"{doc.ModId} {doc.Version}: {doc.Rows.Count} rows in {rowsByTable.Count()} table(s)");
                NoteClientPayloads(doc);
            }
            catch (Exception e)
            {
                Fail(doc.ModId, doc.Version, e.Message);
            }
        }

        /// <summary>
        /// One line when a pack carries a client half. The loader cannot apply
        /// it -- client files live on players' machines -- and saying so keeps
        /// "installed" from meaning "finished" without anyone being told.
        /// </summary>
        private static void NoteClientPayloads(ModDocument doc)
        {
            if (doc.DeclaresClientPayloads && log.IsInfoEnabled)
                log.Info($"{doc.ModId}: {doc.ClientPayloads} client payload item(s); "
                    + "apply them on each client with `camelot client install`");
        }

        /// <summary>
        /// Write a table's rows into the block the mod owns.
        ///
        /// The block is cleared first, which makes this idempotent: a mod
        /// that failed halfway is repaired by the next boot rather than left
        /// half-applied, and an update replaces the previous version's rows
        /// instead of accumulating them.
        /// </summary>
        private void WriteRows(string modId, string table, List<ModRow> rows, int idLow)
        {
            var type = ModSchema.TypeForTable(table);
            var keyColumn = ModSchema.KeyColumnFor(table);
            var keyProperty = type.GetProperty(keyColumn) ?? type.GetProperty(keyColumn, BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);
            if (keyProperty == null)
                throw new InvalidDataException($"{table}: no property for key column {keyColumn}");

            var existing = SelectBlock(type, keyColumn, idLow, idLow + BlockSize(table));
            if (existing.Count > 0)
                GameServer.Database.DeleteObject(existing.Cast<DataObject>().ToList());

            var id = idLow;
            foreach (var row in rows)
            {
                var instance = (DataObject)Activator.CreateInstance(type);
                foreach (var value in row.Values)
                {
                    var property = type.GetProperty(value.Key,
                        BindingFlags.IgnoreCase | BindingFlags.Public | BindingFlags.Instance);
                    if (property == null || !property.CanWrite)
                        throw new InvalidDataException($"{table}: no writable column {value.Key}");
                    property.SetValue(instance, Convert.ChangeType(value.Value.ToObject<object>(),
                        Nullable.GetUnderlyingType(property.PropertyType) ?? property.PropertyType));
                }
                keyProperty.SetValue(instance, Convert.ChangeType(id, keyProperty.PropertyType));
                instance.AllowAdd = true;
                if (!GameServer.Database.AddObject(instance))
                    throw new InvalidDataException($"{table}: could not write row {row.Id}");
                id++;
            }
        }

        private static int BlockSize(string table)
        {
            var record = ModStore.All().FirstOrDefault(r =>
                string.Equals(r.TableName, table, StringComparison.OrdinalIgnoreCase));
            return record == null ? 0 : record.IdHigh - record.IdLow;
        }

        /// <summary>
        /// The rows of one table whose key falls in a range.
        ///
        /// Reflection is not decoration: the database layer keys its table
        /// registry by the concrete DataObject type, so calling it with the
        /// base type fails with "DataObject is not registered" -- the table
        /// the loader was told to write is the only one it can ask about.
        /// </summary>
        private static System.Collections.IList SelectBlock(Type type, string column, int low, int high)
        {
            var method = GameServer.Database.GetType()
                .GetMethod("SelectObjects", new[] { typeof(WhereClause) });
            var generic = method.MakeGenericMethod(type);
            var where = DB.Column(column).IsGreaterOrEqualTo(low)
                .And(DB.Column(column).IsLessOrEqualTo(high + 1));
            return (System.Collections.IList)generic.Invoke(GameServer.Database, new object[] { where });
        }

        // ------------------------------------------------------------- removal

        /// <summary>
        /// Remove the mods whose archives are gone -- after checking that
        /// nothing in game points at their rows.
        /// </summary>
        private void RemoveAbsent(IEnumerable<string> installedIds)
        {
            var present = new HashSet<string>(installedIds, StringComparer.OrdinalIgnoreCase);
            foreach (var modId in ModStore.All().Select(r => r.ModId).Distinct().ToList())
            {
                if (present.Contains(modId))
                    continue;
                try
                {
                    Uninstall(modId);
                }
                catch (Exception e)
                {
                    Fail(modId, null, e.Message);
                }
            }
        }

        private void Uninstall(string modId)
        {
            foreach (var record in ModStore.For(modId))
            {
                var inUse = ModStore.ReferenceCount(record.TableName, record.IdLow, record.IdHigh);
                if (inUse > 0)
                {
                    // Refused, and not offered as a flag: deleting rows that
                    // players already reference would break their characters.
                    // An operator who truly wants it can do it in SQL, which
                    // is a decision rather than a convenience.
                    Fail(modId, record.Version,
                        $"{record.TableName} rows are in use ({inUse} reference(s)); not removed");
                    continue;
                }

                var type = ModSchema.TypeForTable(record.TableName);
                var keyColumn = ModSchema.KeyColumnFor(record.TableName);
                if (type == null || keyColumn == null)
                {
                    Fail(modId, record.Version, $"cannot remove rows from {record.TableName}");
                    continue;
                }
                var rows = SelectBlock(type, keyColumn, record.IdLow, record.IdHigh);
                if (rows.Count > 0)
                    GameServer.Database.DeleteObject(rows.Cast<DataObject>().ToList());

                ModStore.Forget(modId);
                if (log.IsInfoEnabled)
                    log.Info($"{modId}: removed from {record.TableName}");
            }
        }

        private void Fail(string modId, string version, string reason)
        {
            log.Error($"mod {modId}: {reason}");
            try
            {
                // Recorded against every block the mod owns: a failure that
                // leaves no trace in CamelotMod is a failure an operator
                // cannot see without reading the server log.
                foreach (var record in ModStore.For(modId))
                    ModStore.Record(record, version ?? record.Version, reason);
            }
            catch (Exception e)
            {
                log.Error($"mod {modId}: could not record the failure: {e.Message}");
            }
        }
    }
}
