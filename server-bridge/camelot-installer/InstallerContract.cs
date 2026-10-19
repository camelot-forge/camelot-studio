// Camelot Installer — the documents (`server-install.md` §2, §D5).
//
// The Installer is the only writer of a server's own rows (S §D1). This file is
// the half of it that touches no database: the folder Deploy hands over, the
// state it keeps, and the evidence each attempt leaves.
//
// Every document here has a counterpart in `core/camelot/installer.py` and
// `core/camelot/remote.py`, and the two must agree. A server that ran
// `camelot env apply` once and the C# Installer after must not tell two
// stories: the Console reads what the desk's CLI wrote, and the desk's CLI
// reads what the C# wrote.
//
// The formats are versioned and refused by name when they are not ours. A
// document this build does not read is a document it does not half-read.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace DOL.GS.Camelot
{
    /// <summary>Anything the contract refuses, with the reason in the message.</summary>
    internal class InstallerError : Exception
    {
        public InstallerError(string message) : base(message) { }
    }

    /// <summary>The Installer's folder, as paths and as documents.</summary>
    internal static class InstallerContract
    {
        public const string StateFormat = "camelot.installer.state/1";
        public const string AppliedFormat = "camelot.installer.applied/1";
        public const string ReversedFormat = "camelot.installer.reversed/1";
        public const string WantedFormat = "camelot.wanted/1";
        public const string ReverseFormat = "camelot.reverse/1";

        public const string StateFile = "state.json";
        public const string ReleasesDir = "releases";
        public const string AppliedDir = "applied";
        public const string ReversedDir = "reversed";
        public const string WantedFile = "wanted.json";
        public const string ReverseFile = "reverse.json";
        public const string RowsFile = "rows.json";
        public const string AppliedFile = "applied.json";
        public const string ReversedFile = "reversed.json";

        /// <summary>UTC, whole seconds: the spelling `journal.now()` writes, and
        /// the only one both halves of the suite sort correctly.</summary>
        public static string Now()
        {
            return DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);
        }

        /// <summary>A release number as it appears in a path and in `state.json`.</summary>
        public static string Name(int number)
        {
            return number.ToString("0000", CultureInfo.InvariantCulture);
        }

        // --- the folder --------------------------------------------------------

        public static string StatePath(string root)
        {
            return Path.Combine(root, StateFile);
        }

        public static string ReleasesFolder(string root, int number)
        {
            return Path.Combine(root, ReleasesDir, Name(number));
        }

        public static string AppliedPath(string root, int number)
        {
            return Path.Combine(root, AppliedDir, Name(number), AppliedFile);
        }

        public static string ReversedPath(string root, int number)
        {
            return Path.Combine(root, ReversedDir, Name(number), ReversedFile);
        }

        public static string ReverseAskPath(string root)
        {
            return Path.Combine(root, ReverseFile);
        }

        public static string WantedPath(string root)
        {
            return Path.Combine(root, WantedFile);
        }

        /// <summary>The releases Deploy has put in the folder, in order — what the
        /// step has to read, regardless of what `state.json` records.</summary>
        public static List<int> HandedOver(string root)
        {
            var where = Path.Combine(root, ReleasesDir);
            if (!Directory.Exists(where))
                return new List<int>();
            var found = new List<int>();
            foreach (var path in Directory.GetDirectories(where))
            {
                var name = Path.GetFileName(path);
                if (name.Length > 0 && name.All(char.IsDigit) && int.TryParse(name, out var number))
                    found.Add(number);
            }
            found.Sort();
            return found;
        }

        // --- reading and writing ----------------------------------------------

        /// <summary>The document at *path*, or null when it is not there. A missing
        /// file is a fact — no state, no ask — never an empty one.</summary>
        public static JObject Read(string path)
        {
            if (!File.Exists(path))
                return null;
            try
            {
                return JObject.Parse(File.ReadAllText(path));
            }
            catch (Exception e) when (e is IOException || e is JsonException || e is UnauthorizedAccessException)
            {
                throw new InstallerError($"{path}: {e.Message}");
            }
        }

        /// <summary>Write a document whole: it is read by every other surface, and a
        /// half document is worse than none.</summary>
        public static void Write(string path, JObject document)
        {
            var folder = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(folder))
                Directory.CreateDirectory(folder);
            File.WriteAllText(path, Sorted(document).ToString(Formatting.Indented) + "\n");
        }

        /// <summary>The state, with `updated` stamped. The one document whose
        /// timestamp is not the caller's business.</summary>
        public static void WriteState(string path, JObject state)
        {
            state["updated"] = Now();
            Write(path, state);
        }

        /// <summary>What the Installer writes before it has applied anything: the
        /// project it serves, the baseline it was built on, an empty record.</summary>
        public static JObject EmptyState(string project)
        {
            return new JObject
            {
                ["format"] = StateFormat,
                ["project"] = project ?? string.Empty,
                ["baseline"] = new JObject(),
                ["releases"] = new JObject(),
                ["updated"] = Now(),
            };
        }

        /// <summary>
        /// The state this environment keeps, refused by name when the format is not
        /// one this build reads.
        ///
        /// The desk's CLI also refuses a state another *project* wrote. The
        /// Installer has no project name to check against — the folder it reads is
        /// the project's own checkout — so it preserves the name the state carries
        /// and writes an empty one where there is none.
        /// </summary>
        public static JObject ReadState(string root)
        {
            var path = StatePath(root);
            var document = Read(path);
            if (document == null)
                return EmptyState(string.Empty);
            var format = document.Value<string>("format");
            if (format != StateFormat)
            {
                throw new InstallerError(
                    $"{path}: format {format ?? "(none)"} is not one this build reads "
                    + $"(it reads {StateFormat})");
            }
            return document;
        }

        /// <summary>What the state says about one release, or null — never a guess
        /// from the files: "applied" is a recorded fact (S §D8).</summary>
        public static JObject Released(JObject state, int number)
        {
            return ((state["releases"] as JObject) ?? new JObject())[Name(number)] as JObject;
        }

        /// <summary>The releases a document names, in the order it names them.</summary>
        public static List<int> Numbers(JToken token)
        {
            var found = new List<int>();
            if (!(token is JArray array))
                return found;
            foreach (var item in array)
            {
                if (item.Type == JTokenType.Integer)
                    found.Add(item.Value<int>());
                else if (int.TryParse(item.ToString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var number))
                    found.Add(number);
            }
            return found;
        }

        /// <summary>
        /// The row patches a handed-over release carries, flattened in the order
        /// they were written: `rows.json` is what the cut puts beside the pack when
        /// the range patched rows (`release.py`), and a release that patched none
        /// has none.
        /// </summary>
        public static List<JObject> ReadRows(string releaseFolder)
        {
            var path = Path.Combine(releaseFolder, RowsFile);
            var document = Read(path);
            var found = new List<JObject>();
            if (document == null)
                return found;
            var patches = document["patches"] as JObject;
            if (patches == null)
                return found;
            foreach (var zone in patches.Properties().OrderBy(p => p.Name, StringComparer.Ordinal))
            {
                foreach (var patch in zone.Value as JArray ?? new JArray())
                {
                    if (patch is JObject row)
                        found.Add(row);
                }
            }
            return found;
        }

        // --- the one thing both languages must agree on ------------------------

        /// <summary>
        /// The keys sorted, everywhere, the way Python's `json.dumps(sort_keys=True)`
        /// writes them. Two writers of one document should produce one file: an
        /// operator diffing a server's state against the desk's is looking for the
        /// one row that moved, not for a reordered file.
        /// </summary>
        private static JToken Sorted(JToken token)
        {
            if (token is JObject obj)
            {
                var sorted = new JObject();
                foreach (var property in obj.Properties().OrderBy(p => p.Name, StringComparer.Ordinal))
                    sorted[property.Name] = Sorted(property.Value);
                return sorted;
            }
            if (token is JArray array)
                return new JArray(array.Select(Sorted));
            return token;
        }
    }
}
