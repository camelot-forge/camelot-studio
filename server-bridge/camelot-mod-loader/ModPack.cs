// Camelot mod loader — pack reading.
//
// A pack is a .tar.gz, and it is read entirely in memory: nothing is ever
// extracted to disk, which removes the one real hazard of accepting archives
// from third parties. The archive is covered by the checksums.lock the
// builder emits, and every member it lists is verified before anything is
// applied -- a tampered pack must not reach the database.
//
// gzip and tar come from SharpZipLib, which the server already loads for its
// own MPK handling, so a script can reference it. System.Formats.Tar is not
// loaded and could not be used without changing the server configuration.

using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using ICSharpCode.SharpZipLib.GZip;
using ICSharpCode.SharpZipLib.Tar;
using Newtonsoft.Json.Linq;

namespace DOL.GS.CamelotMods
{
    /// <summary>One row as the pack describes it: a table, a logical name and
    /// values with no id of its own.</summary>
    public sealed class ModRow
    {
        public string Table;
        public string Id;
        public JObject Values;
    }

    /// <summary>A pack's deployment document (<c>targets/opendaoc/mod.json</c>).</summary>
    public sealed class ModDocument
    {
        public string ModId;
        public string Version;
        public int Priority;
        public List<string> Requires = new List<string>();
        public List<ModAllocation> Allocations = new List<ModAllocation>();
        public List<ModRow> Rows = new List<ModRow>();
        public bool DeclaresClientPayloads;

        public static ModDocument Parse(byte[] json)
        {
            var root = JObject.Parse(Encoding.UTF8.GetString(json));
            var doc = new ModDocument();
            var mod = root["mod"] as JObject;
            if (mod == null)
                throw new InvalidDataException("mod.json has no 'mod' section");
            doc.ModId = (string)mod["id"];
            doc.Version = (string)mod["version"];
            doc.Priority = mod["priority"]?.Value<int>() ?? 100;
            if (string.IsNullOrWhiteSpace(doc.ModId))
                throw new InvalidDataException("mod.json has no mod id");
            if (string.IsNullOrWhiteSpace(doc.Version))
                throw new InvalidDataException($"{doc.ModId}: mod.json has no version");

            foreach (var r in root["requires"] as JArray ?? new JArray())
                doc.Requires.Add((string)r);

            foreach (var a in root["allocations"] as JArray ?? new JArray())
            {
                doc.Allocations.Add(new ModAllocation
                {
                    Table = (string)a["table"],
                    Count = a["count"]?.Value<int>() ?? 0,
                });
            }

            foreach (var r in root["rows"] as JArray ?? new JArray())
            {
                doc.Rows.Add(new ModRow
                {
                    Table = (string)r["table"],
                    Id = (string)r["id"],
                    Values = r["values"] as JObject ?? new JObject(),
                });
            }

            doc.DeclaresClientPayloads = (root["client"] as JArray)?.Count > 0;
            return doc;
        }
    }

    public sealed class ModAllocation
    {
        public string Table;
        public int Count;
    }

    /// <summary>An archive held in memory, with its members verified.</summary>
    public sealed class ModPack
    {
        /// <summary>A pack larger than this is refused rather than buffered,
        /// so a hostile archive cannot exhaust the server's memory.</summary>
        private const long MaxUncompressedBytes = 512L * 1024 * 1024;

        public string Path;
        public ModDocument Document;
        private readonly Dictionary<string, byte[]> members = new Dictionary<string, byte[]>();

        public byte[] Member(string name)
        {
            return members.TryGetValue(name, out var bytes) ? bytes : null;
        }

        /// <summary>
        /// Read, verify and parse a pack. Throws with a reason on anything
        /// wrong: the caller records the failure and carries on, because one
        /// bad mod must never stop a server from booting.
        /// </summary>
        public static ModPack Load(string path)
        {
            var pack = new ModPack { Path = path };
            pack.ReadMembers();
            pack.VerifyChecksums();

            var doc = pack.Member("targets/opendaoc/mod.json");
            if (doc == null)
                throw new InvalidDataException($"{path}: no targets/opendaoc/mod.json inside");
            pack.Document = ModDocument.Parse(doc);
            return pack;
        }

        private void ReadMembers()
        {
            long total = 0;
            using (var file = File.OpenRead(Path))
            using (var gzip = new GZipInputStream(file))
            using (var tar = new TarInputStream(gzip, Encoding.UTF8))
            {
                TarEntry entry;
                while ((entry = tar.GetNextEntry()) != null)
                {
                    if (entry.IsDirectory)
                        continue;

                    // A member name is untrusted: refuse anything that could
                    // escape the archive, even though nothing is written out.
                    var name = entry.Name.Replace('\\', '/').TrimStart('/');
                    if (name.Contains(".."))
                        throw new InvalidDataException($"{Path}: unsafe member name {entry.Name}");

                    using (var buffer = new MemoryStream())
                    {
                        tar.CopyEntryContents(buffer);
                        total += buffer.Length;
                        if (total > MaxUncompressedBytes)
                            throw new InvalidDataException($"{Path}: archive too large");
                        members[name] = buffer.ToArray();
                    }
                }
            }
        }

        private void VerifyChecksums()
        {
            var lockBytes = Member("checksums.lock");
            if (lockBytes == null)
                throw new InvalidDataException($"{Path}: no checksums.lock inside");

            foreach (var line in Encoding.UTF8.GetString(lockBytes).Split('\n'))
            {
                var trimmed = line.Trim();
                if (trimmed.Length == 0)
                    continue;
                // "<sha256>  <path>", the format the builder writes.
                var split = trimmed.IndexOf("  ", StringComparison.Ordinal);
                if (split <= 0)
                    continue;
                var expected = trimmed.Substring(0, split);
                var name = trimmed.Substring(split + 2);

                var actual = Member(name);
                if (actual == null)
                    throw new InvalidDataException($"{Path}: {name} is listed but missing");
                using (var sha = SHA256.Create())
                {
                    var digest = BitConverter.ToString(sha.ComputeHash(actual))
                        .Replace("-", string.Empty).ToLowerInvariant();
                    if (digest != expected)
                        throw new InvalidDataException(
                            $"{Path}: {name} does not match its checksum");
                }
            }
        }
    }
}
