# camelot-installer — the OpenDAoC plugin that applies a release

Turns the folder Deploy hands over into the server's own rows. One C# plugin,
discovered by the server at boot through `IDatabaseUpdater`; no core change.

It is the Installer of `server-install.md` §D10 on a real server, where the desk
cannot reach the database. It replaces the `camelot-core` container job
(`deploy/server/`) at the same job: same folder, same two documents, same order,
so the Console's calls do not change.

## Install

1. copy this folder into `<server>/scripts/camelot-installer/`
2. add the reference the plugin needs to `<ScriptAssemblies>` in
   `config/serverconfig.xml`:

   ```xml
   <ScriptAssemblies>Newtonsoft.Json</ScriptAssemblies>
   ```

   The server compiles scripts against the assemblies already loaded, and
   `Newtonsoft.Json` is not loaded yet when compilation runs, so without this the
   compile produces a zero-byte assembly and the server refuses to start. The DLL
   already ships in the server's output.
3. leave the folder it reads where `camelot env provision` put it: `<server>/camelot/`.
   The server property `installer_directory` moves it (default `camelot`, relative
   to the server root).

**This is a manual step, once** (§D15): the server compiles its scripts *before*
any hook runs, so the Installer cannot install itself and a release cannot carry
it. Everything after it is the folder.

## What it does at boot

1. **the Console's ask first** — `camelot/reverse.json`, newest release first.
   Each reversal writes `reversed/NNNN/reversed.json`, takes the release out of
   `state.json`, deletes `releases/NNNN/` (the folder *is* the ask, which is why
   a pull puts it back) and drops it from `wanted.json`. An ask that names a
   release which is not applied any more is skipped and said so: a stale ask is
   not a broken environment;
2. **then what is handed over** — every `releases/NNNN/` the state does not
   record, oldest first. A release's rows go to `state.json` and
   `applied/NNNN/applied.json` when they land.

## What it guarantees

- **One writer.** Nothing else in the suite opens a server's database: the
  Console writes files, Deploy hands over a folder, the Studio never talks to a
  server (§D1).
- **A failure never stops the server.** `UpdateDatabase()` treats a thrown
  exception as fatal — the server refuses to boot, which under
  `restart: unless-stopped` is a crash loop — so every failure is caught and
  recorded (§D10). The releases are applied oldest first and the **first failure
  stops the run**: a later release's rows can depend on an earlier one's.
- **A refused release writes nothing.** Every row is checked before any write —
  the table is one we own, the columns are the table's own, the values fit them,
  the row is there — and each row that fails is a `refused` line in
  `applied/NNNN/applied.json`. The state is not touched, so the release stays
  wanted and not applied and the Console shows it pending.
- **A half-applied release is put back.** The database layer's batch save opens a
  transaction per table, and a row that fails inside it is swallowed while the
  rest of the batch commits. When a batch fails the Installer writes the recorded
  originals back, so the state and the database agree that the release did not
  land. (The reversal needs no such repair: a row already put back reads as
  "already what it was", so an interrupted reversal is finished by running it
  again.)
- **The Installer is idempotent.** A release the state records is not applied
  again, and a row that already holds the release's values is `skipped`, not
  rewritten (§D14).
- **A removal keeps the whole row**, so reversing it re-inserts the row rather
  than a guess (§D14, §D17).

## What it does not do

- **No reload, no restart.** `needs.reload` and `needs.restart` are in-game admin
  commands (`/reload doors`, `/reload mob`) — a release *names* the ritual, a
  human or the Console performs it (§D2). The state the Installer writes carries
  an empty `needs` for exactly this reason: the ritual reaches the server through
  the deploy log, which is what the Console reads for it, and a server applied by
  the desk's CLI carries the list the desk knew.
- **No client files.** The client half is the players' launcher's (C) and the
  feed's; a server has no client.
- **No ids allocated.** A patch names a row that exists; the loader's allocated
  blocks are the channel for *new* rows.
- **No project name to check.** The desk's CLI refuses a `state.json` another
  project wrote. The Installer reads the folder the project's own deploy checkout
  filled, so it preserves the name the state carries and writes an empty one
  where there is none.

## Verifying the plugin compiles

`check/check.csproj` compiles the three files against a built server without
running one:

```sh
DaocServerRelease=/path/to/OpenDAoC-Core/Release \
  dotnet build server-bridge/camelot-installer/check/check.csproj
```

That catches signature drift against the server's own assemblies. It does not
replace booting it: table registration, script compilation and the plugin's own
behaviour only show up in a real run. Walking a release, a refusal and a reversal
against a real MariaDB is what found the two things this file is shaped by — that
`SaveObject` is the only transactional seam a script gets, and that `AddObject`
and `DeleteObject` answer `true` even when the whole statement threw.
