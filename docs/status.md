# Project status

State of Camelot Studio. Byte-level format facts: `docs/formats.md`. A new
contributor or agent reads this, then `AGENTS.md` for the repo rules.

## What is delivered

`core/` — a `camelot` CLI (Python ≥3.11, pyffi 2.2.3 backbone):

| Command | Purpose |
|---|---|
| `camelot info <f>` | NIF version, block histogram, named nodes, textures |
| `camelot kfa <f>` | .kfa animated bone channels (bone indices, key counts) |
| `camelot gltf <f> --out <prefix> [--skinned] [--pose A.kfa --pose-time T --bake-pose]` | glTF 2.0 export (Z-up→Y-up axis node) + `<prefix>.skin.json` sidecar |
| `camelot roundtrip <f>` | read → write → read (multiset compare; legacy 4.x = N/A) |
| `camelot batch <manifest> --out <report> [--read-only] [--limit N]` | bulk validation, live progress; `$DAOC_CLIENT` rewrites `DAOC CLIENT/...` manifest paths |
| `camelot mpak list <archive>` / `get <archive> <entry>` / `pack <out> <name> <files…>` | MPAK reader/writer (`.mpk`/`.npk`) |
| `camelot contract --out DIR [--id ID] [--version V] [--material M] [--figures ID…] [--add-figure ID:KIND:RIG[:ROW]] [--target NAME] [--package P.tar.gz] [--no-states] [--no-artifacts] [--no-targets] [--dry-run]` | build the server content **pack**: contract, glTF artifacts, target projections, validation report, checksums lock, deployable archive |
| `camelot mod build --dir DIR --client DIR --out P.tar.gz [--creatures N] [--used-models F]` | assemble a **mod pack** from an authoring directory (loose files, archive entries, table row ops, binary patch specs) |
| `camelot client install P.tar.gz` / `uninstall MOD` / `status` / `verify` / `free-ids` | apply, remove, inspect and read back a pack's client half; list the model ids a mod may use |
| `camelot client patch [--max-id N] [--restore]` | raise the client's model-id ceiling in place (unmanaged clients; `.orig` backup, byte-exact restore) |
| `camelot server export --out F [--config C\|--dsn D]` | export the server's world rows (`mob`/`worldobject`/`door`/`path`) to a snapshot the viewer reads; needs `pymysql` (extra `mysql`) |
| `camelot server tables` | the server tables this project owns — the map the Installer validates a release against |
| `camelot project new\|machine\|check\|info\|verify\|import\|populate\|release\|releases\|deploy\|promote\|publish\|envs` | the project: one server's whole content in one folder (release, dry run, deploy to an environment); `machine` writes this machine's `machine.json` from an environment's `serverconfig.xml`, `check` reads it back and probes the database, and both refuse a config that opens another database than the environment names; `publish` commits a release's payloads and its feed to the `camelot-deploy` branch — the transport a server this machine does not reach pulls — and **signs the release record** with `machine.json`'s `release_key` |
| `camelot env provision\|status\|verify\|apply\|reverse\|reload [ENV]` | an environment's server half: its own database built from OpenDAoC's baseline, what it holds, the Installer's step, the read-back; an environment declared `"client": false` in `machine.json` is **server-only** — the client half belongs to the feed; `reverse` puts back what one release replaced, in one transaction, and withdraws it from the folder and the ask |
| `camelot serve [--root DIR] [--port N] [--server-rows F] [--db-config F] [--db-write] [--env N] [--edits D] [--no-open]…` | local daemon: indexes the client, serves the viewer + a JSON API (FastAPI/uvicorn); with no root it starts the **front door** (Home, no client: the create modal names one and the daemon adopts it); with `--project`, the client and the environment's `db_config` come from `machine.json` (`--env`, default `local`); `--server-rows` (a snapshot) or `--db-config` (the server's own `serverconfig.xml`, live) adds the server's rows, read-only; `--edits` turns on the **world editor** (6.3: select, move/rotate/scale with snap, catalog add, remove, the undo journal, `build`/`apply`/`revert`/`restore`); `--db-write` lets it also patch the server's **existing** world rows (B2: a keep's own doors move with the keep) |
| `camelot studio [DIR] [--env N] [--edit] [--port N] [--no-open] [--no-live]` | the desk's one verb: reads `machine.json`, runs the same walk as `project check` (and refuses before starting on a missing file, a config that opens another database, or a database that does not answer), then starts the daemon and opens the browser; `--edit` adds `--db-write` |
| `camelot console --root DIR […]` | the running server's Console: one **Install update** button (pull, restart, then wait for the Installer's state), the players connected by name, the applied release and rows, the reload recorded in the project's log, the feed's version, the game port and the read API; **Pull** and **Restart** remain as separate actions, and `--allow-restart` enables the shutdown a supervisor turns into a restart; `--deploy` enables the pull — fetch the `camelot-deploy` branch, check who signed the release record, verify its hashes, hand the server half over, write the wanted marker, mirror the feed; `--release-key` is the operator's `.pub`, and without one the page says the server does not check signatures; **Reverse** writes the ask for the other direction (`reverse.json`, honoured by the Installer's step); one audit line per attempt; a password is required beyond loopback |

The server side also ships as containers: `deploy/compose.yaml` is one entry
point that `include`s the three parts, and each part runs alone — `server/`
(MariaDB 10.6, OpenDAoC **pinned by digest**, and the `camelot-core` job that
runs the Installer's step), `feed/` (`feedserve`), `console/`. The job runs the
same `camelot` CLI the desk runs, so importing `camelot.cli` pulls neither
`pyffi` nor `fastapi` (a server writes rows: it has no client to parse and no
page to serve — `test_the_server_needs_no_client_reader_and_no_daemon`).
`deploy/README.md` maps the containers; `docs/admin.md` is the operator's
runbook.

`deploy/install/` is the server install: one compose file with named volumes and
a `camelot-bootstrap` job, driven by `./install.sh`. It clones the baseline and
the deploy branch, writes `machine.json`, provisions the database once and seeds
the feed, so a VPS needs no prepared directory tree. Its `core` is built from the
pinned OpenDAoC digest plus the C# Installer (`deploy/core/Dockerfile`), so the admin flow is **Install update** on the Console.

The Installer also exists as a **C# plugin**
(`server-bridge/camelot-installer/`): copied once into the server's script
folder, compiled at boot, run through `IDatabaseUpdater`.
It honours the Console's ask, applies what the folder holds oldest first, writes
the same `state.json`, `applied/` and `reversed/` the desk's CLI writes, and
never lets a failure reach `UpdateDatabase()` — a thrown exception there is a
server that will not start. Both ways of running it produce one folder
and one state; the Console cannot tell them apart.

A release travels **signed** (`camelot/sign.py`): `publish` signs the
release record with `ssh-keygen -Y` (OpenSSH's SSHSIG, Ed25519, namespace
`camelot-release`) and the signature rides in the branch beside it; `pull` checks
it against the operator's `.pub` **before** it believes the hashes the record
carries — the hashes are written in the record, so only the signature makes them
mean anything. A server with no key hands the release over and reports that it
did not check; the Console's page says so.

**Coverage snapshot**: a retained full-corpus run reported **5087/5089**
client NIFs parsed (the two remaining are 10.1.0.0 housing files failing
inside pyffi — see `formats.md` §7). Re-run and version the report against
`DAOC_CLIENT V2` before treating the count as a current KPI.
`core/camelot/legacy.py` reads the retained NetImmerse 4.x mesh corpus
(read-only; a 4.x writer is not implemented).

## Server content contract (Phases 2-3, first cut)

`camelot contract` reads the client corpus and emits the **server-neutral
contract** the roadmap asks for. It is an offline, reproducible build — no
daemon, no server, no proprietary bytes in the output.

```sh
DAOC_CLIENT="…/DAOC CLIENT V2" .venv/bin/camelot contract --out ./pack \
  --id daoc.studio --version 0.1.0
```

```text
pack/
  manifest/content.json     the contract (schema 1.0.0)
  artifacts/<id>.glb        the content itself, one packed GLB per figure
  reports/validation.json   what resolved, what did not, and the timings
  checksums.lock            SHA-256 per artifact
```

Against the V2 corpus: **36 figures, 324 appearance slots, 5958 animation
states, 36 GLB artifacts, 0 unresolved**, 42 MB, ~31 s, byte-identical on
rebuild. The GLBs are the *only* Studio-authored bytes in the pack; the
client's own NIF/DDS are never copied.

**The contract** carries namespaced logical ids (`daoc.player.tro_m`), the
rig, per-slot appearance (piece, sheet, engine tint, `fig3parts` Double Sided
flag) with the `gamedata` key each came from, and per-state animation
(slot, KFA, animnifs id, `loop`/`clamp`, game duration, contributing anim
set). It also carries the **client build read from `game.dll`'s
`VS_VERSION_INFO`** — the V2 corpus is `1.130`, not the `1.127` the OpenDAoC
docs state, which is exactly the kind of assumption the pack is meant to
replace with a recorded fact.

Three properties are enforced by tests rather than convention:

- **Deterministic** — same corpus, same bytes. The wall-clock timing lives
  only in the report, never in hashed content.
- **Honest** — a reference the client tables did not resolve is recorded as
  `unresolved` and the build **refuses to publish** (`--dry-run` reports
  without raising). It is never guessed.
- **Byte-free** — sheet *names* are in the contract, sheet *bytes* never
  are; the glTF and DDS live in artifacts beside it.

**Adding your own content**: `--add-figure daoc.custom.sword:object:sword`
appends a figure the stock client has no row for. It lands beside the
built-in rows with its own logical id and **never renumbers them** — a server
may already have persisted those ids. `--figures 2 13` restricts the build
to specific fig3 rows (a note in the manifest, not an unresolved reference:
a deliberate restriction is not a missing answer).

**What this is not yet**: an *installable* pack. The GLBs are Studio-authored
glTF, not the client's own NIF/DDS, so a stock client cannot load them
directly — deployment needs a target projection plus a client patch
manifest, which is Phase 3 step 4 and Phase 4. The name is `contract` rather
than `pack` for exactly that reason.

**The OpenDAoC projection** (`core/camelot/targets/opendaoc.py`) maps each
figure onto the server's own vocabulary, read from its source and tables
rather than guessed: the `race` ids 1..18, the wire byte
(`Race = byte & 0x1F`, `Gender = (byte >> 7) & 1`) and the **model
bitfield** — the handler reads the body size as `(CreationModel >> 11) & 3`
and autobans a zero one, so the projection refuses to emit one. All 36
figures project, covering race ids 1..18 with no gap.

The one thing the corpus spelling would have broken: `fig3descriptions.csv`
says `Norse` and `Half Ogre` where the server's `race` table says
`Norseman` and `HalfOgre`. Matching on the client's spelling alone would
have dropped two of the eighteen races — a failure that would only surface
in-game. A projection refuses an unknown race rather than defaulting.

**The deployment document** (`core/camelot/mod.py`) is what a pack carries
for the loader. The pack declares *how many* ids it needs per table and describes
its rows
as columns and values; it ships no SQL and never chooses an id. The manager
allocates, records and writes, so overlap between writers is impossible by
construction rather than detected, and nothing a pack carries can execute
arbitrary statements against a database. The `client` section carries the pack's
client half: the destinations it replaces on players' machines (a loose file, an
archive entry, a gamedata row edit), applied by `camelot client install`
with claims resolved by priority.

Against V2 that produces 906 declarative rows and one allocation
(`npctemplate`, 906), with no `TemplateId` anywhere in the output.

**Packaging** (`core/camelot/assemble.py`): `--package` collapses the whole
tree into one reproducible `.tar.gz` with a member-level lock. Against V2:
41 members, 14 MB, containing the contract, 36 GLBs, the OpenDAoC projection
and a self-describing `PACK.json` (id, version, client build, figure count,
unresolved list). Every member is pinned to mtime 0 and fixed ownership —
a timestamp would make two builds of one corpus differ and render the lock
meaningless. A target that cannot yet express a figure records it under
`unprojected` in its own file rather than failing the build or dropping it
silently.

**Not yet done**: NIF *writing*; the Phase 5 in-client ladder has run
(P5-A…P5-F pass; the Phase 5 report is with the project's notes), with P5-F's
server half still to be
replayed through the loader with an in-band `Model`. A stock client cannot
load the Studio-authored GLBs (it reads NIF from MPAK), so deploying new
*assets* still waits on a NIF writer. Deploying new *server data* does not:
the loader consumes the deployment document on a booted server, and a
rewritten `gamedata.mpk` is accepted by the 1.127 client (`formats.md` §5
documents the decoded header carriers). The client *delivery* half is built
(2026-10-08): `camelot mod build` assembles a pack from an authoring
directory, `camelot client install/uninstall/status/verify` applies its
payload with claims and priority. Every touched file is rebuilt from the
client's own originals and read back, and the loader notes each pack's payload
count at apply time.

**New model ids have a measured ceiling** (2026-10-10): the client's model
table is 2500 entries wide (ids 0..2499; the shipped table tops out at 2493),
and a row at 2500 makes the client **stop parsing the whole table** — every
later row is dropped, so an out-of-band row takes other mods' rows down with
it. `mod.py`'s reserved band (2494..2499) is that measurement; the table's
247 free gaps are readable too but stay out of band until the cross-reference
risk is measured.

**The ceiling is liftable by a small client patch, and the patch is a payload
kind** (measured 2026-10-10; folded into the studio the same day): two cap
compares and four array references in the loaded `game1127.dll`, plus one
zero-filled PE section, raise the table to **32768 entries**. A spawn at model
10000 renders in game. The patch travels as a **recipe** (expected bytes,
bytes to write, the section to append, never a redistributed DLL), `"kind":
"patch"` beside the other payload kinds; the author asks for it with
`client.patch.json`'s `"binary": [{"model_ids": 32768}]`, and `mod build`
generates the recipe against a pristine client (refusing an already-patched
one, or a cap outside 2501..32768). A pack **declares the capability** it
grants and needs (`requires: ["client.modelids.32768"]`), and the installer
verifies it by reading the bytes the client will load (the two compares, the
four references, the section's size). So a client patched by any means
satisfies it, an unknown capability is refused, and a high-id mod is refused
**before anything is written** when the patch is missing. `camelot client
patch [--restore]` does the same for unmanaged clients, with a `.orig` backup.
Field test: the built pack reproduces the validated DLL byte for byte
(sha256), and uninstall restores the original exactly. Without the patch, a
measured pool of **927 ids** is available (253 gaps plus 674 shipped rows
nothing references); `camelot client free-ids` lists it from the client itself
(reading the patched ceiling out of the binary when one is carried), and `mod
build` refuses a `monsters.csv` add at an id the measured server content
references (`--used-models` replaces the list with a server's own export).

**The client's world is readable** (Phase 6.0, 2026-10-11): `camelot/zones.py`
reads the client's own zone index (`zones.dat`: 483 zones, 358 regions, each
with the world rectangle in 8192-unit cells the server also builds), the
terrain heightmaps (256×256 PCX plus the offset map, factors per zone from
`sector.dat`), the object placements (`fixtures.csv`), the catalog, bounds,
jumps and water — and `height_at()` reproduces the client's own sampling
exactly (calibrated against the client's `fixtures.csv`: median error 0-1 on
the ground-placed zones). A zone's folder may live under `zones/`,
`frontiers/zones/` (zone163+), the tutorial or housing trees; its object
meshes and sheets resolve by stem (`britlog.tga` → `zones/nifs/BRITLOG.dds`),
and its ground layers are typed too (`texture_patches`, `patch_masks` —
`formats.md` §11). 6.1 measured that **the client reads `csvNNN.mpk`** for a
zone's CSV entries and that a tool-rewritten zone archive is accepted and
byte-exactly reversible. The formats are in
`formats.md` §11; the editor's rules are in `camelot/serve/editor.py` and
`docs/admin.md`.

**The world API and `/world`** (Phase 6.2, 2026-10-11): the daemon grows a
world service (`serve/world.py`, `serve/models.py`). `GET /world/index` lists
the regions, the zones and what the client ships; `GET /world/zone/{id}` gives
the zone's info, fixtures, catalog with mesh availability, bounds, water,
jumps, the ground layer model and the archive directory; `GET …/terrain`
serves the compact `CAMT` planes the browser re-samples with the client's own
height model; `GET …/mesh/{name}` a zone object as one cached glb, each mesh
carrying the sheet its own `NiTexturingProperty` names, alpha-cut sheets
declared, and the LOD level the client draws only (collision hulls and far
stand-ins dropped); `GET …/texture/{logical}/{entry}` and `GET
/world/terraintex/{name}` the sheets, DDS → PNG, cached. The web app gains
`/world` (SvelteKit + three.js): the terrain as the client triangulates it
(33×33-vertex patch meshes on the subtile diagonal), the ground composed per
patch from its `TerrainTex` layers and masks (below), fixtures instanced per
catalog mesh with their heading (**degrees**, measured, not the server's
12-bit grid) and scale, bounds/water/jump overlays, a region browser, a zone
picker, and click-to-select/recentre with a searchable fixture list and an
inspector (6.3's first slice). The scene is the client's own map orientation:
north up, east right, `+y` (south) mapped to scene `+Z`, fixture yaw `−A`. `A
= 0` puts a mesh's own `+y` (the keep's gatehouse) on the north side, and
increasing `A` turns clockwise on that map, as the in-game aerials show
(measured 2026-10-12; the earlier "`A = 0` faces south" read `A` as the
*server's* 12-bit compass). The *zone meshes* take the figures' rotation
(`(x,y,z)→(x,z,−y)`, `nifutil.WORLD_AXIS_CONVERSION`). The reflection that
shipped for a day (2026-10-11 → 12) fixed the map but stood every building
180° round: the user's aerial `/world`-vs-game comparison caught it, and so
did the keep's `door` nodes, landing 15 units from the server's rows under the
rotation against 357 under the reflection. The terrain patches share one
zone-wide normal field (a per-patch computation drew the 8×8 grid as 64 shaded
tiles) and the ground masks are clamped (a mask covers its patch exactly). The
ground is the client's layer stack, not its bake: each patch is composed from
its `TerrainTex` sheets and masks in one pass (`web/src/lib/world/ground.ts`),
the sheets tiled across the zone (`tiles = 256` is a 256-unit tile), and the
model is scored against the client's own `texXX-YY` composite (`formats.md`
§11). The first cuts drew that bake alone, then its layers 8× too densely;
both were flat washes where the game shows pebbles and grass blades. The bake
is kept as the fallback for zones that ship no layers. Fixtures are placed by
the viewer's rule: every `Ground = 1` row is dropped on the terrain and its
authored `Z` is ignored, and `Ground = 0` rows keep their `Z`
(`terrain.ts::fixtureZ`, pinned by `terrain.test.ts`). That is the client's
own rule, measured 2026-10-12 (W-3): a probe pine at `Z = 5000` with `Ground =
1` stood on the terrain in game while its `Ground = 0` twin floated at 5 000.
The editor emits `z = 0` + `Ground = 1` and never copies an authored `Z` onto
a ground row. The world reader mirrors the client's own archive preference
since 2026-10-12: `csv` first per entry, `dat` as the fallback
(`Zone.data_entry`). Taking `dat` first served an installed pack's pristine
bytes and left its rows invisible; an archive the client ships empty
(`ZONE009/csv009.mpk` is 0 bytes) reads as absent rather than as an error.
Verification: `core/tests/test_serve_world.py` (12 unit tests + 6 corpus
tests) and `web/src/lib/world/{terrain,ground}.test.ts` (sampling vectors
generated from the Python; heading, placement, triangulation and the
composition model pinned). The screenshot harness is
`web/scripts/world_shot.mjs` (Playwright).


The client-side run happened (2026-10-07), on a Linux x86-64 host with the
with the 1.127 client under Wine: a rewritten container, modified table data and a new
model id all reach the world, and the loader's server half deploys the
matching row and spawn. The format notes in `formats.md` record the traps this
run paid for.

## Architecture decisions

- **Language/stack**: Python core (pyffi ecosystem) + TypeScript/Svelte web
  viewer (M1) + Python Blender addon (M2) + artifacts bridge to the C#
  OpenDAoC server. C# never touches binaries — it consumes our
  glTF/JSON artifacts.
- **Local daemon (`camelot serve`)**: FastAPI/uvicorn owns the API *and*
  serves the static SvelteKit build on one loopback origin (no CORS). It is a
  facade over the format layer: `serve/index.py` enumerates loose files +
  MPAK entries (never parses payloads at scan time), `serve/api.py` exports
  `.glb` on demand with a disk cache, `serve/watcher.py` rescans on change and
  pushes SSE. Host/Origin allowlists guard loopback. `core/` remains the only
  layer that knows binary formats.
- **Layer contract**: `core/` owns all binary formats; `web/`, `addon/`,
  `server-bridge/` only consume glTF/JSON sidecars.
- **glTF skinning**: the exporter uses the stored `NiSkinData` transform for
  the current supported path (`inverseBind = S·meshWorld⁻¹`; CPU bake
  `inv(meshWorld)·boneAnim·S`). This is corpus-validated and visually checked
  for selected figures, not a complete Ghidra proof of every client path.
- **glTF axis**: vertices/normals stay in NIF space and the synthetic root
  node (`__axes__`) applies the axis conversion once (no double rotation).
  Every era composes **Z-up** in NIF world space, so the conversion is always
  `(x,y,z)→(x,z,−y)` (`formats.md` §2 "Up axis"). **The
  world and the figures share it** (`nifutil.WORLD_AXIS_CONVERSION` *is*
  `AXIS_CONVERSION`): the client's map is left-handed (`+y` south — the
  server's own compass, `formats.md` §11) but that decides *where a point
  goes*, not which way a NIF's own axes face — the zone meshes take the same
  rotation as the figures, and a mesh's `+y` lands on the north side. The
  reflection `(x,y,z)→(x,z,+y)` that shipped 2026-10-11 → 12 conflated the
  two and turned every building 180° round (user's aerial comparison,
  2026-10-12; the server's `door` rows agree with the rotation to 15 units and
  not with the reflection, 357). The viewer normalises scale to ~5 units
  and keeps a 90° rotate control. Open: `Rat.NIF` (10.x) still has its length
  along the horizontal axis — its tail dominates the bbox, visual confirmation
  pending.

## Testing

pytest suite in `core/tests/` (AGENTS.md TDD rule):

```sh
# the inner loop: unit tests only, no game files (seconds)
.venv/bin/python -m pytest core/tests -q -m "not corpus"
# the whole suite before a commit: corpus tests against the real client
DAOC_CLIENT="/path/to/DAOC CLIENT V2" .venv/bin/python -m pytest core/tests -q
# the corpus tier in parallel: same tests on six workers (~2.5 min)
DAOC_CLIENT="/path/to/DAOC CLIENT V2" .venv/bin/python -m pytest core/tests -q -n 6 --dist loadfile
```

The tiers are not a hand-kept list: `core/tests/conftest.py` tags every test
that asks for the `client_dir` fixture `corpus`, and the tier's heavy builds
(the player manifest, its glTF artifacts, the `camelot contract` run) are
session fixtures shared across tests — the reproducibility tests build again
on purpose, since that comparison is what they assert. Measured on a test
machine, 2026-10-12: the full sequential run was 666 s, 72 % of it
two files rebuilding the contract pack per test; it now runs in ~2:30 on six
workers (561 passed / 1 skipped), and the inner loop is ~8 s (385 passed /
1 skipped / 176 deselected). `pytest-xdist` is the one added dev dependency.
Exact test counts are not a current product contract; run the commands above
for the authoritative local result. Corpus tests skip without `DAOC_CLIENT`,
and the web suite is run separately from `core/`.

Dated baselines: the V2 corpus run of 2026-10-07 was 390 passed / 4 failed
(the failures: a changed `Boar_CATA` animation link, two legacy version
expectations, one Ettin bbox golden; they await a data/test decision). On the
test machine's **completed corpus** (a pristine copy of the OpenDAoC 1.127
client — the recipe and its hashes are with the project's notes) the suite was
**535 passed / 0 failed** with Phase 6.0, then **554 passed / 0 failed** with
6.2's world API (2026-10-11), with `OPENDAOC_RACE_SQL` pointing at
OpenDAoC-Database's `race.sql`; without it one test skips. The web suite is
138 passed / 3 skipped, `pnpm check` reports zero errors and zero warnings (the
counts move as tests are added; the commands above are the contract).

## Known limits

1. **Write**: pyffi reorders blocks on write (roundtrip validated as
   multisets; worth watching for diff-friendly editing).
2. **Legacy 4.x write**: not implemented (see above).
3. **Bone index table & animation policy** (current policy, re-audited
   2026-09-23):
   - The client's `figures/animnode.dat` (175 names, index = KFA channel) is
     the mapping's backbone (`load_bone_table`); offset/rotation matching
     (`map_kfa_to_nodes`) validates the pair and reports its source
     (`with_source=True`: `"table"` or `"offsets"`).
   - The animation link uses the game's gamedata chain (`monnifs` -> `anims`
     -> `animnifs`), which resolved the "no animation" figures (Arachite02 ->
     `sect_idle`, amazon -> `Hoplite_Idle`, amb_bat -> `amb_bat_fly`, 2HWolf
     -> `2HW_idle`…). Filename conventions + player aliases remain the
     fallback.
   - **Name-tolerant links**: monnifs lookups match alphanumerics-only keys
     (`FrostGiant`/`Frost_Giant.NIF`, `svartalfm`/`Svartalf_M.NIF` — these
     gained both animation and textures), and figures without a monnifs row
     link by their animation-set name (`Boar Keggars` ↔ `Boar_Keggers.nif`,
     Stingray, wolverine, minecarts…), keeping the same rig gates.
   - **Gamedata-linked clips map by the engine's table** (`prefer_table`):
     offset heuristics must not judge them, since animated bones (root bob,
     clavicle breathing) drift from the bind and the offset gate refuses
     amazon/rat/bear/cata_lion/badger/amb_blackcat, all of which deform
     cleanly (mean edge ratio 1.01-1.07) once mapped by name. Fallback
     (filename) links keep the conservative offset gate. The black cat's
     sitting `Idle` slot loses to its `STAND_IDLE`
     (`resolve_anim_from_gamedata` penalizes sit/tailswipe).
   - **All translations apply on a vetted pair** (`kfa_translation_trust`):
     the root track is the grounding (root bob with planted feet); dropping
     it made 2HWolf/3head/Zombie/Ettin/Wolf float 1-2.3 units — with it the
     min-Y span is <= 0.34 through the loop (verified in the viewer).
     `skeleton.py` stays a DFS reference only.
   - **Type-4 Euler rotation channels are played** (`_channel_rotation_keys`):
     classic KFAs store rotations as quaternion keys, 3 per-axis float tracks,
     or both; 116 clips are Euler-only (Beetle, sharks, snakes, ballistas) and
     were silently dropped before. They are now composed into quaternion
     tracks (Beetle animates; see `formats.md` §3).
   - **Stored-bind skinning (corpus-validated)**: the exporter uses the
     stored `NiSkinData` transform (`inverseBind = S·meshWorld⁻¹`, bake
     `inv(meshWorld)·boneAnim·S`), which rescued the stored-bind class
     (AfancB/dracolich/phoenix/soulkeeper/amb_bird/ambrat/CwHuman/Dragon01:
     mean stretch 1.01-1.07, planted feet). `skin_stretch` is an empirical
     deformation safety heuristic, not a reverse-engineered client physics
     gate. The viewer grounds on the stored bind and frames the idle's
     sampled extent. `rebindSkinnedMeshes` must keep passing an explicit
     bindMatrix or three.js would recompute (and discard) the exported
     inverse binds. Some player-race pairings remain refused.
4. **pyffi 10.1.0.0**: 2 housing files fail (see `formats.md` §7).
5. **MPAK header carriers**: decoded (`formats.md` §5) — the directory crc32
   and the two compressed stream lengths, obfuscated. **The game reads them**:
   it takes the lengths from the header instead of measuring the streams, and
   exits during the loading screen when they disagree (measured in-client, no
   message anywhere). The writer recomputes them on every rewrite; the corpus
   tests pin them against the retail archives. The 8 bytes before a record's
   decoded fields stay undecoded and are carried across.
6. **Figure textures**: the retained coverage snapshot reports 525/617
   figures resolved, but it predates the current `DAOC_CLIENT V2` profile and
   counts table resolution rather than final rendered bytes. Re-run and
   version the corpus report before treating this as a current KPI. Native
   player armour now resolves through `pskins.csv` (see below); the
   item-level equipment (extension variants, dyes) is still absent.

## Character preview status

The native `/builder` route exposes the 18 playable races and both genders.
A freshly loaded route waits for a race selection before assembling the first
model; the options response exposes seven faces, the race's positional hair
styles (eight for Briton male, `Bald` included), 16 hair colors, eight eye
colors, eight skin tones, the race's cloak variants and the 85 wearable
armour materials. Skin tone, hair style/color, eye color, cloak, armour and
gender changes returned successful model/texture requests in a browser pass
against `DAOC CLIENT V2`.

All 18 races share the same creation-screen shape on the V2 corpus: seven
faces, eight skin tones, eight eye colors and 16 hair colors each; only the
hair-style count varies (4-8, by race). Hair pieces keep their per-piece
`fig3parts` **Double Sided** flag through assembly, glTF export and the viewer
material, so the client's single-sided hair styles are not lit from behind.

**Armour** is picked from the client's `pskins.csv`: 85 of the 1953 material
families cover the full wearable set (body, arms, gloves, lbody, legs, boots)
and each declares its own sheet per piece and gender, so the figure is dressed
in one coherent family. The default is the client's first family (`Hard Leather`, pskin
301). The cloak keeps its own sheet: it belongs to the equipped cloak item,
not to the armour material.

**Animation states** are the figure's own anim set, not one auto-picked idle.
`/character/…/states` lists the `anims.csv` slots the client's tables declare
(166 for a player figure: `Idle`, `Walk`, `Run`, `Fly`, `Death`, the `EM …`
emotes…), each with its KFA, its animnifs id, the game's duration and the
`loop`/`clamp` flag; `?state=<slot>` on `/character/…/anim` plays one, and the
response carries the clip's key-density rate. A `clamp` clip is a one-shot and
the viewer holds its last frame instead of wrapping. Without `state` the
route still serves the resolver's idle, so existing callers are unchanged.

A successful route means a usable preview, not that every required mesh or
texture is present. A material is a *texture* family; the client's per-item
mesh **extension** (which of an item's variants is worn) and per-slot **dye**
are not resolved. Both are server notions — the dye table does not exist in
the client at all — and they belong to the Phase 2 appearance contract, not
to the builder.

One gamedata link is known to point at a clip authored on another rig: the
male Troll (`tro_m`) resolves `Cat_troll_idle.kfa`, whose absolute
translations sit 1.8-3.6 units off `tro_m_skeleton.nif` and sheared the face.
The native path gates such clips (`gltf.FOREIGN_RIG_MEAN_TOL`): the
translations are dropped and the idle plays rotations-only. Which clip the
live client plays for the male Troll player is still to confirm.

## Environment gotchas

- **Browser verification**: Playwright is the default tool (headless). The
  probes under `web/scripts/` are the contract; routine UI checks do not open a
  browser on the host.
- The venv (`camelot-studio/.venv`) is created by `bootstrap.sh`, which
  patches pyffi for Python ≥3.12: `setuptools` (distutils shim) and
  `time.clock() → time.perf_counter()` in pyffi's
  `object_models/{xml,xsd}/__init__.py`. Never remove `.venv`; code edits
  are live (editable install). Do not replace the patches with a
  `sitecustomize.py` (the system one shadows it).
- Terminal: this workspace's shell is `sh` — no process substitution,
  no `$(...)` in commands; prefer writing traces to files.
- pyffi `dir(obj)` is broken (`__dir__` bug): use `dir(type(obj))`.
  Matrix33/44 cells are `m_11..m_33`/`m_11..m_44`, not `[i][j]`.
- GitHub org vs monorepo: not decided; recommendation is a monorepo
  (`camelot-studio` org → one repo) with Actions (lint + pytest) once the
  test suite lands.

## Next steps (priority order)

1. **The delivery chain's own limits**: the loader's used-id export, the
   `objects` cap-5000 recipe, and the ids above 32767 (the signed 16-bit wire)
   are the next measurements.
2. **The NIF writer** is the gate on content that brings new geometry: a model
   the client has never read still waits on it. It runs parallel to the rest.
3. **The players' Windows package**: the swap was walked on a Windows 11 VM
   on 2026-10-19 — a v1 package replaced itself with v2 from a local HTTP
   server, the old folder was removed, and no console opened. The Authenticode
   signature waits for the repository to become public.
4. **Blender integration** (M2, `addon/`): a UI shell over the CLI — figures,
   fig3 parts, items and `.kfa` animations imported and exported from Blender.

The dated record of how each of these was reached — the slices, the UX passes,
the measurements — is kept with the project's own notes.

## Command cheat sheet

The contributor's own loop. The operator's commands, in the order they run, are
`docs/install.md` §5, and the daily flow is `docs/admin.md` §4.

```sh
export DAOC_CLIENT="/path/to/DAOC CLIENT V2"

# The corpus: read-only, long-running. `core/tests/manifest.example.txt` shows
# the manifest's format.
.venv/bin/camelot batch /path/to/manifest.txt --read-only --out /tmp/report.txt

# Inspect and export a client asset.
.venv/bin/camelot info "$DAOC_CLIENT/figures/Ettin.NIF"
.venv/bin/camelot gltf "$DAOC_CLIENT/figures/Boar.NIF" --out /tmp/boar

# The server's own world rows, in /world: export them once from the server's
# MySQL (needs `pymysql`), or read the live database through its config.
.venv/bin/camelot server export --config /path/to/serverconfig.xml --out rows.json
.venv/bin/camelot serve --root "$DAOC_CLIENT" --server-rows rows.json
.venv/bin/camelot server tables            # the tables this project owns

# A release, rebuilt from the journal and compared byte for byte: what a CI job
# runs.
.venv/bin/camelot project verify --dir camelot-hills --rebuild 1
```
