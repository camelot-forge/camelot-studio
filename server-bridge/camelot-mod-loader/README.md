# camelot-mod-loader — the OpenDAoC mod plugin

Turns a folder of Camelot packs into mods an operator installs, updates and
removes. One C# plugin, discovered by the server at boot through
`IDatabaseUpdater`; no core change.

## Install

1. copy this folder into `<server>/scripts/camelot-mods/`
2. put `.tar.gz` packs in `<server>/scripts/mods/`
3. add the two references the plugin needs to `<ScriptAssemblies>` in
   `config/serverconfig.xml`:

   ```xml
   <ScriptAssemblies>ICSharpCode.SharpZipLib,Newtonsoft.Json</ScriptAssemblies>
   ```

   The server compiles scripts against the assemblies already loaded, and
   neither of these is loaded yet when compilation runs, so without this the
   compile produces a zero-byte assembly and the server refuses to start.
   Both DLLs already ship in the server's output.

## What it guarantees

- **A mod never chooses an id.** It declares how many it needs; the loader
  allocates a rounded block, records it in `CamelotMod`, and writes every
  row. Two mods cannot overlap by construction, and the same mod installs on
  two servers under different ids.
- **A mod ships no SQL.** Rows are columns and values, written through the
  server's own `DataObject` layer, so a third-party mod cannot execute what
  it likes.
- **Nothing is half-applied.** A table's block is cleared and rewritten, so
  a mod that failed midway is repaired by the next boot rather than left
  broken.
- **One bad mod never stops a server.** `UpdateDatabase()` returning false
  makes the server refuse to boot, so every failure is caught per mod and
  recorded rather than thrown.
- **Uninstall refuses to destroy data in use.** Removing a mod whose rows
  something already references is refused, with the count and the table; the
  rows are left alone.

## What it does not do

- no client files, and no client payloads: two mods patching the same client
  file have no precedence rule yet (Phase 5);
- no script mods: the server compiles the script folder once, before this
  runs, so a mod shipping `.cs` needs an installer, not this;
- no dependency resolution: a missing requirement skips that mod.

## Verifying the plugin compiles

`check/check.csproj` compiles the three files against a built server without
running one:

```sh
DaocServerRelease=/path/to/OpenDAoC-Core/Release \
  dotnet build server-bridge/camelot-mod-loader/check/check.csproj
```

That catches signature drift against the server's own assemblies. It does not
replace booting it: table registration, script compilation and the loader's
own behaviour only show up in a real run — the checksum verification and the
`[DatabaseUpdate]` attribute were both wrong in ways only a boot revealed.


## The client-side MPAK test (does the client accept a container we wrote?)

Not a server matter, but it lives here because it gates the same ecosystem.
The client's textures and its `gamedata` tables are MPAK archives; models and
animations are loose files. Writing archives is therefore the prerequisite
for any mod that brings a texture or a new id.

Three tests, cheapest first. Stop at the first failure.

```sh
# A: content unchanged, container ours. Any observable difference is the
#    container being rejected.
.venv/bin/camelot mpak repack "$CLIENT/gamedata.mpk" /tmp/testA/gamedata.mpk
# B: one entry's content changed -- does the client read modified data?
.venv/bin/camelot mpak repack "$CLIENT/gamedata.mpk" /tmp/testB/gamedata.mpk \
    --replace "monnifs.csv=/tmp/testB/monnifs.csv"
# C: a new monnifs row -- a new id pointing at an existing NIF, no writer needed
```

Back the archive up first; the client should be tested on a copy.

The header's undecoded six bytes (offsets 5..10) are carried across and byte
17 is recomputed (`(entries ^ 12) & 0xFF`, decoded from all 1306 archives in
the client). If test A fails, the six bytes are validated by the client and
must be reversed before anything else.
