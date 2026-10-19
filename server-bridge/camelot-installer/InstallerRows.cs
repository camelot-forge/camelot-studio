// Camelot Installer — the server's own rows, through the server's own layer.
//
// A release ships columns and values, never SQL (S §1), so every write here goes
// through `DataObject`. Nothing in this file knows a table's columns by heart:
// they are read off the type the server registered, and a release that names a
// column that type does not have is refused by name before anything is written.
//
// Two facts about the seam shape this file, both read out of the server's source
// (`CoreDatabase/`) rather than guessed:
//
// - `SaveObject(IEnumerable<DataObject>)` opens a transaction per table, which
//   is as close to "one release, one transaction" (S §D11) as a script gets: the
//   connection string is not exposed, so a script cannot open its own. The
//   layer's per-row failures are caught inside that transaction and the rest of
//   the batch commits, so a failed batch can leave part of a release written;
// - `AddObject` and `DeleteObject` are not transactional and answer `true` even
//   when the whole statement threw (`AddObjectImpl` returns an empty result list
//   on an exception, and the caller reads an empty list as no failure). A row
//   that was removed is therefore **read back** instead of trusted.
//
// The first fact is why `Apply` puts back what it wrote when a batch fails: the
// state is not written for a failed release, so without the repair the next boot
// would find those rows "already what the release says", skip them, and lose
// their originals for good.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using DOL.Database;
using DOL.Database.Attributes;
using DOL.Logging;
using Newtonsoft.Json.Linq;

namespace DOL.GS.Camelot
{
    /// <summary>What the ownership map says about one table.</summary>
    internal sealed class TableClaim
    {
        public TableClaim(string key, bool write)
        {
            Key = key;
            Write = write;
        }

        /// <summary>The column a row is named by.</summary>
        public string Key { get; }

        /// <summary>Whether the suite ever writes that table.</summary>
        public bool Write { get; }
    }

    /// <summary>
    /// One table of the server, as the Installer needs it: its type, its key
    /// column, and every column it carries — including the key the layer adds
    /// itself (`DataTableHandler`: a type that declares no primary key gets
    /// `ObjectId` as one, under the column `<Table>_ID`).
    /// </summary>
    internal sealed class InstallerTable
    {
        private static readonly Logger log =
            LoggerManager.Create(MethodBase.GetCurrentMethod().DeclaringType);

        /// <summary>
        /// The tables this project owns (`server-install.md` §D4), mirroring
        /// `core/camelot/targets/opendaoc.py`'s `OWNED_TABLES`: the key column, and
        /// whether a release may patch it. A release that names anything else is
        /// refused by name, before anything is written.
        /// </summary>
        private static readonly Dictionary<string, TableClaim> Owned =
            new Dictionary<string, TableClaim>(StringComparer.OrdinalIgnoreCase)
            {
                ["mob"] = new TableClaim("Mob_ID", true),
                ["worldobject"] = new TableClaim("WorldObject_ID", true),
                ["door"] = new TableClaim("Door_ID", true),
                ["path"] = new TableClaim("PathID", false),
                // The client's own tables: ours in the client half, and the
                // server's copy is the loader's to import — the suite edits the
                // client's entries and never these rows.
                ["items"] = new TableClaim("Id_nb", false),
                ["monsters"] = new TableClaim("ID", false),
                ["spells"] = new TableClaim("SpellID", false),
                ["styles"] = new TableClaim("StyleID", false),
                ["loot"] = new TableClaim("LootTemplate", false),
                ["shops"] = new TableClaim("MerchantID", false),
            };

        private static readonly Dictionary<string, InstallerTable> Cache =
            new Dictionary<string, InstallerTable>(StringComparer.OrdinalIgnoreCase);

        private readonly Dictionary<string, ElementBinding> _byColumn =
            new Dictionary<string, ElementBinding>(StringComparer.OrdinalIgnoreCase);

        private readonly MethodInfo _select;

        private InstallerTable(string name, TableClaim claim, Type type, string tableName)
        {
            Name = name;
            Claim = claim;
            Type = type;
            TableName = tableName;

            var members = type.GetMembers()
                .Select(member => new ElementBinding(member))
                .Where(binding => binding.IsDataElementBinding && binding.Relation == null)
                .ToArray();

            var declared = members.FirstOrDefault(binding => binding.PrimaryKey != null);
            var objectId = type.GetProperty("ObjectId");
            if (objectId == null)
                throw new InstallerError($"{name}: {type.Name} has no ObjectId property");

            if (declared != null && declared.PrimaryKey.AutoIncrement)
            {
                // The table numbers its own rows; there is no `<Table>_ID` column.
                Key = declared;
                Columns = members;
            }
            else
            {
                // Either the type declares a key of its own, and `ObjectId` rides
                // along as a unique column, or it declares none and `ObjectId` is
                // the key. Both are the handler's rule, spelled the same way.
                Key = declared ?? new ElementBinding(objectId, new PrimaryKey(), tableName + "_ID");
                Columns = declared == null
                    ? members.Concat(new[] { Key }).ToArray()
                    : members.Concat(new[] { new ElementBinding(objectId, new DataElement { Unique = true }, tableName + "_ID") }).ToArray();
            }

            foreach (var column in Columns)
                _byColumn[column.ColumnName] = column;

            if (!string.Equals(Key.ColumnName, claim.Key, StringComparison.OrdinalIgnoreCase))
            {
                throw new InstallerError(
                    $"{name}: this server's {type.Name} is keyed on {Key.ColumnName}, and the "
                    + $"ownership map says {claim.Key} — the two have to agree before a row is "
                    + "written");
            }

            var method = typeof(IObjectDatabase).GetMethod("SelectObject", new[] { typeof(WhereClause) });
            if (method == null)
                throw new InstallerError("this server's database layer has no SelectObject(WhereClause)");
            _select = method.MakeGenericMethod(type);
        }

        /// <summary>The release's own spelling of the table (`door`, `mob`, …).</summary>
        public string Name { get; }

        public TableClaim Claim { get; }

        public Type Type { get; }

        /// <summary>The server's own spelling, from its `[DataTable]`.</summary>
        public string TableName { get; }

        public ElementBinding Key { get; }

        public ElementBinding[] Columns { get; }

        public static bool IsOwned(string table)
        {
            return table != null && Owned.ContainsKey(table);
        }

        public static bool IsWritable(string table)
        {
            return table != null && Owned.TryGetValue(table, out var claim) && claim.Write;
        }

        /// <summary>The table, resolved against the server's own assemblies.</summary>
        public static InstallerTable For(string table)
        {
            if (!Owned.TryGetValue(table ?? string.Empty, out var claim))
                throw new InstallerError($"{table}: this project does not own that table");
            lock (Cache)
            {
                if (Cache.TryGetValue(table, out var found))
                    return found;
                var type = TypeForTable(table);
                if (type == null)
                    throw new InstallerError($"{table}: no DataObject type in this server is that table");
                var attribute = type.GetCustomAttributes<DataTable>(false).FirstOrDefault();
                found = new InstallerTable(table.ToLowerInvariant(), claim, type, attribute.TableName);
                Cache[table] = found;
                return found;
            }
        }

        /// <summary>
        /// The DataObject type behind a table name, or null. Reflection is not
        /// decoration: the layer keys its table registry by the concrete type, so
        /// calling it with the base type fails with "DataObject is not registered".
        /// </summary>
        private static Type TypeForTable(string table)
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

        // --- reading -----------------------------------------------------------

        /// <summary>The row that key names, or null.</summary>
        public DataObject Load(string key)
        {
            var where = DB.Column(Key.ColumnName).IsEqualTo(key);
            return (DataObject)_select.Invoke(GameServer.Database, new object[] { where });
        }

        public ElementBinding Binding(string column)
        {
            if (_byColumn.TryGetValue(column ?? string.Empty, out var binding))
                return binding;
            throw new InstallerError($"{Name}: no column named {column}");
        }

        /// <summary>
        /// A value as a release carries it: text. A `datetime` comes out the way
        /// Python's `str()` spells it, and a flag as `1`/`0`, which is what the
        /// desk's own `SELECT *` reads back — two writers of one state file have to
        /// render the same row the same way, or every apply after the first looks
        /// like a change.
        /// </summary>
        public static string Text(object value)
        {
            if (value == null)
                return null;
            if (value is string text)
                return text;
            if (value is bool flag)
                return flag ? "1" : "0";
            if (value is DateTime moment)
                return moment.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);
            if (value is IFormattable formattable)
                return formattable.ToString(null, CultureInfo.InvariantCulture);
            return value.ToString();
        }

        public string Text(DataObject row, string column)
        {
            return Text(Binding(column).GetValue(row));
        }

        /// <summary>Every column of a row, as text — what a removal records.</summary>
        public JObject Texts(DataObject row, IEnumerable<string> columns = null)
        {
            var found = new JObject();
            foreach (var column in columns ?? Columns.Select(binding => binding.ColumnName))
                found[column] = Text(row, column);
            return found;
        }

        // --- writing -----------------------------------------------------------

        /// <summary>
        /// Text into the type the column carries. This is where a value that does
        /// not fit is refused, and it happens before anything is written: the
        /// layer's own conversion (`DatabaseSetValue`) swallows a failure and logs
        /// it, which would leave the column untouched and the release "applied".
        /// </summary>
        public static object Coerce(string text, Type type, string what)
        {
            var target = Nullable.GetUnderlyingType(type) ?? type;
            if (target == typeof(string))
                return text;
            if (string.IsNullOrEmpty(text))
            {
                if (Nullable.GetUnderlyingType(type) != null)
                    return null;
                throw new InstallerError($"{what}: an empty value cannot go into a {target.Name} column");
            }
            if (target == typeof(bool))
            {
                switch (text.Trim().ToLowerInvariant())
                {
                    case "1": case "true": case "yes": case "on": return true;
                    case "0": case "false": case "no": case "off": return false;
                }
                throw new InstallerError($"{what}: {text} is not a yes or a no");
            }
            if (target == typeof(Guid))
            {
                if (Guid.TryParse(text, out var parsed))
                    return parsed;
                throw new InstallerError($"{what}: {text} is not an id");
            }
            if (target.IsEnum)
            {
                if (Enum.IsDefined(target, text))
                    return Enum.Parse(target, text);
                throw new InstallerError($"{what}: {text} is not one of {string.Join(", ", Enum.GetNames(target))}");
            }
            if (target == typeof(DateTime))
            {
                if (DateTime.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.None, out var moment))
                    return moment;
                throw new InstallerError($"{what}: {text} is not a date");
            }
            try
            {
                return Convert.ChangeType(text, target, CultureInfo.InvariantCulture);
            }
            catch (Exception e) when (e is FormatException || e is InvalidCastException || e is OverflowException)
            {
                throw new InstallerError($"{what}: {text} does not fit a {target.Name} column");
            }
        }

        /// <summary>A column a patch may name: known, and never the key.</summary>
        public ElementBinding Patchable(string column)
        {
            var binding = Binding(column);
            if (string.Equals(binding.ColumnName, Key.ColumnName, StringComparison.OrdinalIgnoreCase))
                throw new InstallerError($"{Name}: a patch may not touch its key column");
            return binding;
        }

        /// <summary>Set one column of a row, refusing anything that does not fit.</summary>
        public void Set(DataObject row, string column, string text)
        {
            var binding = Binding(column);
            binding.SetValue(row, Coerce(text, binding.ValueType, $"{Name}.{column}"));
        }

        /// <summary>A whole row, from a removal's record: every column, the key
        /// included — which is why a removal records every column (§D14).</summary>
        public DataObject Insert(JObject values)
        {
            var row = (DataObject)Activator.CreateInstance(Type);
            foreach (var column in Columns)
            {
                var recorded = values[column.ColumnName];
                if (recorded == null)
                {
                    throw new InstallerError(
                        $"{Name}: the recorded row does not carry {column.ColumnName}");
                }
                var text = recorded.Type == JTokenType.Null ? null : recorded.ToString();
                column.SetValue(row, Coerce(text, column.ValueType, $"{Name}.{column.ColumnName}"));
            }
            row.AllowAdd = true;
            return row;
        }

        /// <summary>
        /// Put one recorded row back (§D17), and say which way it went: the values
        /// it had for the columns a write changed, or the whole row for one that was
        /// removed. A row that is already what it was is a `skip`, not a write.
        /// </summary>
        public RestoreResult Restore(string key, JObject values)
        {
            var current = Load(key);
            if (current == null)
            {
                var row = Insert(values);
                if (!GameServer.Database.AddObject(row))
                    throw new InstallerError($"{Name} {key}: the row could not be put back");
                if (Load(key) == null)
                    throw new InstallerError($"{Name} {key}: the row is not there after the insert");
                return new RestoreResult("reinsert", values.Properties().Select(p => p.Name).ToList());
            }

            var changed = new Dictionary<string, string>();
            foreach (var value in values.Properties())
            {
                if (string.Equals(value.Name, Key.ColumnName, StringComparison.OrdinalIgnoreCase))
                    continue;
                var text = value.Value.Type == JTokenType.Null ? null : value.Value.ToString();
                if (Text(current, value.Name) != text)
                    changed[value.Name] = text;
            }
            if (changed.Count == 0)
                return new RestoreResult("skip", new List<string>());

            foreach (var pair in changed)
                Set(current, pair.Key, pair.Value);
            if (!GameServer.Database.SaveObject(current))
                throw new InstallerError($"{Name} {key}: the database refused the restore");
            return new RestoreResult("restore", changed.Keys.ToList());
        }
    }

    /// <summary>Which way one row went back, and the columns that moved.</summary>
    internal sealed class RestoreResult
    {
        public RestoreResult(string action, List<string> columns)
        {
            Action = action;
            Columns = columns;
        }

        public string Action { get; }

        public List<string> Columns { get; }
    }

    /// <summary>What one attempt left behind: the evidence, and what the state needs.</summary>
    internal sealed class RowResult
    {
        public JObject Evidence { get; set; }
        public JObject Originals { get; set; }
        public JObject Tables { get; set; }
        public int Refused { get; set; }
    }

    /// <summary>The step itself: one release's rows, and the other direction.</summary>
    internal static class RowStep
    {
        private static readonly Logger log =
            LoggerManager.Create(MethodBase.GetCurrentMethod().DeclaringType);

        /// <summary>
        /// Apply one release's rows.
        ///
        /// Every row is checked first — the table is one we write, the columns are
        /// the table's own, the values fit them, the row is there — and a row that
        /// fails is recorded as `refused` rather than thrown, because a thrown
        /// exception out of `UpdateDatabase()` is a server that will not start
        /// (§D10). A release with any refusal writes nothing at all and stays
        /// wanted and not applied.
        /// </summary>
        public static RowResult Apply(List<JObject> rows, int number)
        {
            var lines = new List<JObject>();
            var originals = new JObject();
            var writes = new List<DataObject>();
            var removals = new List<KeyValuePair<InstallerTable, DataObject>>();
            var refused = 0;

            foreach (var patch in rows)
            {
                var tableName = patch.Value<string>("table") ?? string.Empty;
                var key = patch.Value<string>("key") ?? string.Empty;
                var removed = patch.Value<bool?>("deleted") ?? false;

                if (!InstallerTable.IsOwned(tableName))
                {
                    Refuse(lines, tableName, key, "this project does not own that table");
                    refused++;
                    continue;
                }
                if (!InstallerTable.IsWritable(tableName))
                {
                    Refuse(lines, tableName, key, "the suite never writes that table");
                    refused++;
                    continue;
                }
                if (key.Length == 0)
                {
                    Refuse(lines, tableName, key, "a row patch needs its key");
                    refused++;
                    continue;
                }

                InstallerTable table;
                DataObject current;
                try
                {
                    table = InstallerTable.For(tableName);
                    current = table.Load(key);
                }
                catch (Exception e)
                {
                    Refuse(lines, tableName, key, e.Message);
                    refused++;
                    continue;
                }

                if (current == null)
                {
                    if (removed)
                    {
                        Skip(lines, table, key, "the row is not there any more");
                        continue;
                    }
                    Refuse(
                        lines,
                        tableName,
                        key,
                        $"no row with {table.Key.ColumnName} {key} — the release cannot write it");
                    refused++;
                    continue;
                }

                if (removed)
                {
                    // A removal keeps the whole row as its original: putting a
                    // release back is putting the row back (§D14).
                    originals[table.Name] = ((JObject)originals[table.Name] ?? new JObject());
                    ((JObject)originals[table.Name])[key] = table.Texts(current);
                    removals.Add(new KeyValuePair<InstallerTable, DataObject>(table, current));
                    lines.Add(Line(table, key, "remove", "the release removes it"));
                    continue;
                }

                var values = patch["values"] as JObject ?? new JObject();
                if (values.Count == 0)
                {
                    Refuse(lines, tableName, key, "a patch needs at least one value");
                    refused++;
                    continue;
                }

                var changed = new Dictionary<string, string>();
                var bad = false;
                foreach (var value in values.Properties())
                {
                    var text = value.Value.Type == JTokenType.Null ? null : value.Value.ToString();
                    try
                    {
                        var binding = table.Patchable(value.Name);
                        InstallerTable.Coerce(text, binding.ValueType, $"{table.Name}.{value.Name}");
                    }
                    catch (InstallerError e)
                    {
                        Refuse(lines, tableName, key, e.Message);
                        refused++;
                        bad = true;
                        break;
                    }
                    if (table.Text(current, value.Name) != text)
                        changed[value.Name] = text;
                }
                if (bad)
                    continue;

                if (changed.Count > 0)
                {
                    originals[table.Name] = ((JObject)originals[table.Name] ?? new JObject());
                    ((JObject)originals[table.Name])[key] = table.Texts(current, changed.Keys);
                    foreach (var pair in changed)
                        table.Set(current, pair.Key, pair.Value);
                    writes.Add(current);
                    lines.Add(
                        Line(
                            table,
                            key,
                            "write",
                            $"{changed.Count} value(s): "
                            + string.Join(", ", changed.Keys.OrderBy(name => name, StringComparer.Ordinal))));
                }
                else
                {
                    Skip(lines, table, key, "already what the release says");
                }
            }

            var result = new RowResult
            {
                Originals = originals,
                Refused = refused,
                Evidence = Document("camelot.installer.applied/1", number, lines),
            };
            result.Tables = Counts(lines);
            if (refused > 0)
            {
                if (log.IsErrorEnabled)
                {
                    log.Error(
                        $"release {number:0000}: {refused} row(s) refused; nothing was written and "
                        + "the release stays not applied");
                }
                return result;
            }

            if (writes.Count > 0 || removals.Count > 0)
            {
                try
                {
                    Write(writes, removals);
                }
                catch (Exception e)
                {
                    PutBack(originals);
                    throw new InstallerError($"{e.Message} (what was written has been put back)");
                }
            }

            if (log.IsInfoEnabled)
            {
                log.Info(
                    $"release {number:0000}: {result.Evidence.Value<int>("written")} written, "
                    + $"{result.Evidence.Value<int>("removed")} removed, "
                    + $"{result.Evidence.Value<int>("skipped")} already there");
            }
            return result;
        }

        /// <summary>
        /// Put back what one release replaced (§D17): the recorded originals, and
        /// nothing else. The feed does not go back — this is a repair, not a
        /// rollback.
        ///
        /// A row that fails stops the release, and the state keeps it: the rows
        /// already put back read as "already what it was" on the next attempt, so a
        /// reversal interrupted halfway is finished by running it again. That is
        /// why this direction needs no repair of its own.
        /// </summary>
        public static RowResult Reverse(JObject originals, int number, JArray needs)
        {
            var lines = new List<JObject>();
            foreach (var table in (originals ?? new JObject()).Properties()
                         .OrderBy(p => p.Name, StringComparer.Ordinal))
            {
                var map = InstallerTable.For(table.Name);
                foreach (var row in ((JObject)table.Value).Properties()
                             .OrderBy(p => p.Name, StringComparer.Ordinal))
                {
                    var key = row.Name;
                    RestoreResult restored;
                    try
                    {
                        restored = map.Restore(key, (JObject)row.Value);
                    }
                    catch (InstallerError)
                    {
                        throw;
                    }
                    catch (Exception e)
                    {
                        throw new InstallerError($"{table.Name} {key}: {e.Message}");
                    }
                    var why = restored.Action == "reinsert"
                        ? "the release removed it"
                        : restored.Action == "restore"
                            ? $"{restored.Columns.Count} value(s): "
                                + string.Join(", ", restored.Columns.OrderBy(name => name, StringComparer.Ordinal))
                            : "already what it was";
                    lines.Add(Line(map, key, restored.Action, why));
                }
            }

            var evidence = new JObject
            {
                ["format"] = "camelot.installer.reversed/1",
                ["release"] = number,
                ["at"] = InstallerContract.Now(),
                ["rows"] = new JArray(lines),
                ["restored"] = lines.Count(line => line.Value<string>("action") == "restore"),
                ["reinserted"] = lines.Count(line => line.Value<string>("action") == "reinsert"),
                ["skipped"] = lines.Count(line => line.Value<string>("action") == "skip"),
                // The reversal changes the same rows the release did: the ritual it
                // named is the ritual this one needs.
                ["needs"] = needs ?? new JArray(),
            };
            return new RowResult { Evidence = evidence, Originals = new JObject(), Tables = new JObject() };
        }

        // --- the two writes ----------------------------------------------------

        private static void Write(List<DataObject> writes, List<KeyValuePair<InstallerTable, DataObject>> removals)
        {
            if (writes.Count > 0 && !GameServer.Database.SaveObject(writes))
                throw new InstallerError("the database refused the write");

            if (removals.Count == 0)
                return;

            GameServer.Database.DeleteObject(removals.Select(entry => entry.Value).ToList());
            // The layer's delete is not transactional and answers true even when
            // the statement threw, so the rows are read back instead of trusted.
            var left = removals
                .Where(entry => entry.Key.Load(entry.Value.ObjectId) != null)
                .Select(entry => $"{entry.Key.Name} {entry.Value.ObjectId}")
                .ToList();
            if (left.Count > 0)
                throw new InstallerError($"still there after the delete: {string.Join(", ", left)}");
        }

        /// <summary>
        /// Put the recorded originals back, after a batch that failed halfway. Never
        /// throws: it runs while the Installer is already reporting a failure, and a
        /// repair that took the server down would be worse than the half-write it is
        /// repairing.
        /// </summary>
        private static void PutBack(JObject originals)
        {
            foreach (var table in (originals ?? new JObject()).Properties())
            {
                InstallerTable map;
                try
                {
                    map = InstallerTable.For(table.Name);
                }
                catch (Exception e)
                {
                    log.Error($"{table.Name}: could not be put back: {e.Message}");
                    continue;
                }
                foreach (var row in ((JObject)table.Value).Properties())
                {
                    try
                    {
                        map.Restore(row.Name, (JObject)row.Value);
                    }
                    catch (Exception e)
                    {
                        log.Error($"{table.Name} {row.Name}: could not be put back: {e.Message}");
                    }
                }
            }
        }

        // --- the evidence ------------------------------------------------------

        private static JObject Document(string format, int number, List<JObject> lines)
        {
            return new JObject
            {
                ["format"] = format,
                ["release"] = number,
                ["at"] = InstallerContract.Now(),
                ["rows"] = new JArray(lines),
                ["written"] = lines.Count(line => line.Value<string>("action") == "write"),
                ["removed"] = lines.Count(line => line.Value<string>("action") == "remove"),
                ["skipped"] = lines.Count(line => line.Value<string>("action") == "skip"),
                ["refused"] = lines.Count(line => line.Value<string>("action") == "refused"),
            };
        }

        private static JObject Counts(List<JObject> lines)
        {
            var tables = new JObject();
            foreach (var line in lines)
            {
                var table = line.Value<string>("table") ?? string.Empty;
                var counts = tables[table] as JObject;
                if (counts == null)
                {
                    counts = new JObject { ["written"] = 0, ["removed"] = 0, ["skipped"] = 0 };
                    tables[table] = counts;
                }
                var action = line.Value<string>("action");
                var counter = action == "write" ? "written" : action == "remove" ? "removed" : "skipped";
                counts[counter] = counts.Value<int>(counter) + 1;
            }
            return tables;
        }

        private static JObject Line(InstallerTable table, string key, string action, string why)
        {
            return new JObject
            {
                ["table"] = table.Name,
                ["key"] = key,
                ["action"] = action,
                ["why"] = why,
            };
        }

        private static void Refuse(List<JObject> lines, string table, string key, string why)
        {
            lines.Add(
                new JObject
                {
                    ["table"] = table ?? string.Empty,
                    ["key"] = key ?? string.Empty,
                    ["action"] = "refused",
                    ["why"] = why,
                });
        }

        private static void Skip(List<JObject> lines, InstallerTable table, string key, string why)
        {
            lines.Add(Line(table, key, "skip", why));
        }
    }
}
