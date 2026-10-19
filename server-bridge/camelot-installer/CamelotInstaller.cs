// Camelot Installer — the plugin.
//
// Runs once at boot through the server's IDatabaseUpdater extension point, after
// the scripts are compiled and the properties are loaded: the same hook the mod
// host used (`server-bridge/mod-loader.md`). It honours the Console's ask for a
// reversal, applies the releases Deploy handed over that the state does not
// record, and records them.
//
// Three properties hold the design together:
//
// - **the Installer is the only writer of a server's own rows** (S §D1). Nothing
//   else in the suite opens that database: the Console writes files, Deploy
//   hands over a folder, the Studio never talks to a server;
// - **a failure never stops the server** (§D10). `UpdateDatabase()` treats a
//   thrown exception as fatal — the server refuses to boot, which under
//   `restart: unless-stopped` is a crash loop — so everything here is caught per
//   release and recorded instead;
// - **what it reads and writes is a contract** (S §2), not an implementation:
//   the desk's own `camelot env apply` writes the same documents, so an
//   environment can be brought up by either one and the Console cannot tell.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using DOL.GS.DatabaseUpdate;
using DOL.GS.ServerProperties;
using DOL.Logging;
using Newtonsoft.Json.Linq;

namespace DOL.GS.Camelot
{
    // The attribute is what the server's updater loop looks for; without it the
    // class is compiled and then skipped in silence.
    [DatabaseUpdate]
    public class CamelotInstaller : IDatabaseUpdater
    {
        private static readonly Logger log =
            LoggerManager.Create(MethodBase.GetCurrentMethod().DeclaringType);

        /// <summary>Where the Installer's folder is looked for, relative to the
        /// server root. The default is what `camelot env provision` writes: the
        /// environment's server half is the server root, and `camelot/` sits
        /// inside it (`server-install.md` §2).</summary>
        [ServerProperty("camelot", "installer_directory",
            "Directory holding the Installer's folder (releases/, state.json), relative to the server root",
            "camelot")]
        public static string INSTALLER_DIRECTORY;

        public void Update()
        {
            var root = ResolveDirectory();
            if (root == null)
            {
                if (log.IsWarnEnabled)
                    log.Warn($"installer directory '{INSTALLER_DIRECTORY}' does not exist; no release loaded");
                return;
            }

            try
            {
                ReverseAsked(root);
            }
            catch (Exception e)
            {
                log.Error($"the Console's ask was not honoured: {e.Message}");
            }

            try
            {
                ApplyHanded(root);
            }
            catch (Exception e)
            {
                log.Error($"the releases handed over were not applied: {e.Message}");
            }
        }

        private static string ResolveDirectory()
        {
            var configured = string.IsNullOrWhiteSpace(INSTALLER_DIRECTORY) ? "camelot" : INSTALLER_DIRECTORY;
            var root = GameServer.Instance.Configuration.RootDirectory;
            var full = Path.IsPathRooted(configured) ? configured : Path.Combine(root, configured);
            return Directory.Exists(full) ? full : null;
        }

        // --- what is handed over ----------------------------------------------

        private static void ApplyHanded(string root)
        {
            var handed = InstallerContract.HandedOver(root);
            if (handed.Count == 0)
                return;

            var state = InstallerContract.ReadState(root);
            foreach (var number in handed)
            {
                if (InstallerContract.Released(state, number) != null)
                    continue;
                try
                {
                    ApplyOne(root, state, number);
                }
                catch (Exception e)
                {
                    // Oldest first, and the first failure stops the run: a later
                    // release's rows can depend on an earlier one's, so applying 6
                    // after 5 failed would build content nobody described (§D10).
                    log.Error($"release {number:0000} was not applied: {e.Message}");
                    log.Error(
                        $"release {number:0000} stays wanted and not applied; "
                        + "the server starts on the content it already holds");
                    break;
                }
            }
        }

        private static void ApplyOne(string root, JObject state, int number)
        {
            var folder = InstallerContract.ReleasesFolder(root, number);
            var result = RowStep.Apply(InstallerContract.ReadRows(folder), number);

            // The evidence is written whether the release went in or was refused: a
            // refusal that leaves no file is a refusal nobody can read (§D10).
            InstallerContract.Write(InstallerContract.AppliedPath(root, number), result.Evidence);
            if (result.Refused > 0)
                return; // the state is not touched, so the release stays pending

            var releases = state["releases"] as JObject;
            if (releases == null)
            {
                releases = new JObject();
                state["releases"] = releases;
            }
            releases[InstallerContract.Name(number)] = new JObject
            {
                ["at"] = result.Evidence.Value<string>("at"),
                // The reload ritual is not the Installer's to know: it reaches the
                // server through the deploy log, which is what the Console reads
                // for it, and performing it is an in-game admin command (§D2).
                ["needs"] = new JArray(),
                ["originals"] = result.Originals,
                ["tables"] = result.Tables,
            };
            InstallerContract.WriteState(InstallerContract.StatePath(root), state);
            if (log.IsInfoEnabled)
                log.Info($"release {number:0000} applied");
        }

        // --- what the Console asked for ---------------------------------------

        private static void ReverseAsked(string root)
        {
            var path = InstallerContract.ReverseAskPath(root);
            var ask = InstallerContract.Read(path);
            if (ask == null)
                return;

            var asked = InstallerContract.Numbers(ask["releases"])
                .Distinct()
                .OrderByDescending(number => number)
                .ToList();
            var state = InstallerContract.ReadState(root);
            var kept = new List<int>();
            foreach (var number in asked)
            {
                if (InstallerContract.Released(state, number) == null)
                {
                    // A stale ask is not a broken environment: it is honoured by
                    // being ignored, and it goes (§D10).
                    if (log.IsInfoEnabled)
                        log.Info($"release {number:0000}: not applied any more, there is nothing to reverse");
                    continue;
                }
                try
                {
                    ReverseOne(root, state, number);
                }
                catch (Exception e)
                {
                    log.Error($"release {number:0000} could not be reversed: {e.Message}");
                    kept.Add(number);
                }
            }

            // The ask is withdrawn for what it honoured. What failed stays, so the
            // next boot tries again instead of dropping the operator's request in
            // silence.
            if (kept.Count == 0)
            {
                File.Delete(path);
                return;
            }
            ask["releases"] = new JArray(kept);
            InstallerContract.Write(path, ask);
        }

        private static void ReverseOne(string root, JObject state, int number)
        {
            var recorded = InstallerContract.Released(state, number);
            var later = ((state["releases"] as JObject) ?? new JObject())
                .Properties()
                .Select(property => int.Parse(property.Name, CultureInfo.InvariantCulture))
                .Where(found => found > number)
                .OrderBy(found => found)
                .ToList();
            if (later.Count > 0)
            {
                throw new InstallerError(
                    $"release {number:0000} cannot be reversed while release "
                    + $"{later[later.Count - 1]:0000} is applied — its values would be written "
                    + "over: reverse the newest first");
            }

            var result = RowStep.Reverse(
                recorded["originals"] as JObject, number, recorded["needs"] as JArray);
            InstallerContract.Write(InstallerContract.ReversedPath(root, number), result.Evidence);
            (state["releases"] as JObject)?.Remove(InstallerContract.Name(number));
            InstallerContract.WriteState(InstallerContract.StatePath(root), state);

            // The folder is the ask, and the ask is withdrawn — which is also why a
            // pull puts it back (§D17).
            var folder = InstallerContract.ReleasesFolder(root, number);
            if (Directory.Exists(folder))
                Directory.Delete(folder, true);
            WantedDrop(root, number);

            if (log.IsInfoEnabled)
            {
                log.Info(
                    $"release {number:0000} reversed: "
                    + $"{result.Evidence.Value<int>("restored")} restored, "
                    + $"{result.Evidence.Value<int>("reinserted")} re-inserted");
            }
        }

        /// <summary>
        /// The wanted marker loses a release that is no longer applied, so the
        /// Console stops waiting for it. The desk's `envs.reverse` does the same.
        /// </summary>
        private static void WantedDrop(string root, int number)
        {
            var path = InstallerContract.WantedPath(root);
            var wanted = InstallerContract.Read(path);
            if (wanted == null)
                return;
            var asked = InstallerContract.Numbers(wanted["releases"]);
            if (!asked.Contains(number))
                return;
            wanted["releases"] = new JArray(asked.Where(found => found != number));
            wanted["by"] = "reverse";
            wanted["at"] = InstallerContract.Now();
            InstallerContract.Write(path, wanted);
        }
    }
}
