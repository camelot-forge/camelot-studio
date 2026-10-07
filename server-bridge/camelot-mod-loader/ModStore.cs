// Camelot mod loader — provenance, allocation and the reference checks.
//
// The registry is what makes removal possible. The server's own XML package
// mechanism records only a package name and a hash, so it cannot know which
// rows a package wrote and cannot undo them; this table records the id block
// each mod owns, and removal deletes exactly that block.
//
// Blocks are allocated by the manager, never chosen by a mod, which is what
// makes overlap impossible rather than something to detect.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using DOL.Database;
using DOL.Database.Attributes;
using DOL.Logging;

namespace DOL.GS.CamelotMods
{
    /// <summary>One mod's claim on one table.</summary>
    [DataTable(TableName = "CamelotMod")]
    public class CamelotModRecord : DataObject
    {
        private int m_id;
        private string m_modId = string.Empty;
        private string m_version = string.Empty;
        private string m_tableName = string.Empty;
        private int m_idLow;
        private int m_idHigh;
        private string m_appliedAt = string.Empty;
        private string m_result = string.Empty;

        [PrimaryKey(AutoIncrement = true)]
        public int CamelotModID
        {
            get { return m_id; }
            set { Dirty = true; m_id = value; }
        }

        [DataElement(Varchar = 255, AllowDbNull = false, Index = true)]
        public string ModId
        {
            get { return m_modId; }
            set { Dirty = true; m_modId = value; }
        }

        [DataElement(Varchar = 64, AllowDbNull = false)]
        public string Version
        {
            get { return m_version; }
            set { Dirty = true; m_version = value; }
        }

        [DataElement(Varchar = 255, AllowDbNull = false, Index = true)]
        public string TableName
        {
            get { return m_tableName; }
            set { Dirty = true; m_tableName = value; }
        }

        [DataElement(AllowDbNull = false)]
        public int IdLow
        {
            get { return m_idLow; }
            set { Dirty = true; m_idLow = value; }
        }

        [DataElement(AllowDbNull = false)]
        public int IdHigh
        {
            get { return m_idHigh; }
            set { Dirty = true; m_idHigh = value; }
        }

        [DataElement(Varchar = 64, AllowDbNull = false)]
        public string AppliedAt
        {
            get { return m_appliedAt; }
            set { Dirty = true; m_appliedAt = value; }
        }

        [DataElement(Varchar = 255, AllowDbNull = false)]
        public string Result
        {
            get { return m_result; }
            set { Dirty = true; m_result = value; }
        }
    }

    /// <summary>
    /// Which column carries a table's allocated id, and what has to be
    /// checked before its rows may be deleted.
    ///
    /// Both are built-in rather than declared by a pack. A mod that could
    /// name its own key column, or assert that nothing references it, could
    /// step outside the block it was given.
    /// </summary>
    internal static class ModSchema
    {
        /// <summary>Table (lower case) -> the column the loader allocates.</summary>
        private static readonly Dictionary<string, string> KeyColumns =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            // DbNpcTemplate declares no key at all, which is why this cannot
            // be read from the type.
            ["npctemplate"] = "TemplateId",
        };

        /// <summary>Table (lower case) -> tables and columns that may point
        /// at one of its rows.</summary>
        private static readonly Dictionary<string, string[]> ReferencedBy =
            new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase)
        {
            ["npctemplate"] = new[] { "Mob.NPCTemplateID" },
        };

        /// <summary>The DataObject type behind a table name, or null.</summary>
        public static Type TypeForTable(string table)
        {
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try { types = assembly.GetTypes(); }
                catch (ReflectionTypeLoadException e) { types = e.Types.Where(t => t != null).ToArray(); }

                foreach (var type in types)
                {
                    if (type == null || !type.IsClass || type.IsAbstract)
                        continue;
                    if (!typeof(DataObject).IsAssignableFrom(type))
                        continue;
                    var attribute = type.GetCustomAttributes<DataTable>(false).FirstOrDefault();
                    if (attribute != null && string.Equals(attribute.TableName, table, StringComparison.OrdinalIgnoreCase))
                        return type;
                }
            }
            return null;
        }

        public static string KeyColumnFor(string table)
        {
            return KeyColumns.TryGetValue(table, out var column) ? column : null;
        }

        public static string[] ReferencedByFor(string table)
        {
            return ReferencedBy.TryGetValue(table, out var refs) ? refs : new string[0];
        }
    }

    /// <summary>Provenance, allocation and the uninstall checks.</summary>
    internal static class ModStore
    {
        private static readonly Logger log =
            LoggerManager.Create(MethodBase.GetCurrentMethod().DeclaringType);

        /// <summary>Where the first block starts. Far above the shipped ids
        /// (the server's own npctemplate rows run to 90,162,132, then jump
        /// to 601,580,490) and well inside an int.</summary>
        private const int AllocationBase = 100000000;

        /// <summary>Blocks are rounded up to this, so an update that adds
        /// content still fits and does not force a reinstall. Unused ids cost
        /// nothing; a stable-id violation costs in-game data.</summary>
        private const int AllocationGranularity = 1000;

        public static List<CamelotModRecord> All()
        {
            try { return GameServer.Database.SelectAllObjects<CamelotModRecord>(); }
            catch (Exception e)
            {
                log.Error($"could not read CamelotMod: {e.Message}");
                return new List<CamelotModRecord>();
            }
        }

        public static List<CamelotModRecord> For(string modId)
        {
            return All().Where(r => string.Equals(r.ModId, modId, StringComparison.OrdinalIgnoreCase)).ToList();
        }

        /// <summary>
        /// The block a mod already owns for a table, or null. Allocation is
        /// stable across updates because in-game data may already point at
        /// those ids.
        /// </summary>
        public static CamelotModRecord Existing(string modId, string table)
        {
            return All().FirstOrDefault(r =>
                string.Equals(r.ModId, modId, StringComparison.OrdinalIgnoreCase) &&
                string.Equals(r.TableName, table, StringComparison.OrdinalIgnoreCase));
        }

        /// <summary>
        /// A block big enough for <paramref name="count"/> rows, high enough
        /// to clear every block already handed out. The existing game rows
        /// are not consulted: the base is far above all of them by design,
        /// and reading a 13k-row table to prove it would cost more than the
        /// design costs.
        /// </summary>
        public static void Allocate(string modId, string table, int count)
        {
            var next = AllocationBase;
            foreach (var record in All())
            {
                if (string.Equals(record.TableName, table, StringComparison.OrdinalIgnoreCase) &&
                    record.IdHigh >= next)
                    next = record.IdHigh + 1;
            }

            var size = Math.Max(AllocationGranularity,
                (int)Math.Ceiling(count / (double)AllocationGranularity) * AllocationGranularity);

            GameServer.Database.AddObject(new CamelotModRecord
            {
                ModId = modId,
                Version = string.Empty,
                TableName = table,
                IdLow = next,
                IdHigh = next + size - 1,
                AppliedAt = DateTime.UtcNow.ToString("o"),
                Result = "ALLOCATED",
            });
        }

        public static void Record(CamelotModRecord record, string version, string result)
        {
            record.Version = version;
            record.Result = result;
            record.AppliedAt = DateTime.UtcNow.ToString("o");
            GameServer.Database.SaveObject(record);
        }

        public static void Forget(string modId)
        {
            var records = For(modId);
            if (records.Count > 0)
                GameServer.Database.DeleteObject(records);
        }

        /// <summary>
        /// How many rows of the referring tables point into the block. Used
        /// before an uninstall: deleting a mod whose content is already in
        /// use would leave players pointing at rows that no longer exist.
        /// </summary>
        public static int ReferenceCount(string table, int low, int high)
        {
            var total = 0;
            foreach (var reference in ModSchema.ReferencedByFor(table))
            {
                var parts = reference.Split('.');
                if (parts.Length != 2)
                    continue;
                var type = ModSchema.TypeForTable(parts[0]);
                if (type == null)
                {
                    // A referring table we cannot read is treated as a
                    // reference, not as an absence of one.
                    log.Warn($"cannot inspect {reference} while checking {table}; assuming in use");
                    return int.MaxValue;
                }
                var count = CountInRange(type, parts[1], low, high);
                if (count < 0)
                    return int.MaxValue;   // same reasoning: refuse on doubt
                total += count;
            }
            return total;
        }

        private static int CountInRange(Type type, string column, int low, int high)
        {
            try
            {
                var method = typeof(GameServer).GetProperty("Database").PropertyType
                    .GetMethod("SelectObjects")!;
                var generic = method.MakeGenericMethod(type);
                var where = DB.Column(column).IsGreaterOrEqualTo(low)
                    .And(DB.Column(column).IsLessOrEqualTo(high));
                var rows = generic.Invoke(GameServer.Database, new object[] { where }) as System.Collections.IEnumerable;
                if (rows == null)
                    return -1;
                var count = 0;
                foreach (var unused in rows)
                    count++;
                return count;
            }
            catch (Exception e)
            {
                log.Error($"could not count {type.Name}.{column}: {e.Message}");
                return -1;
            }
        }
    }
}
