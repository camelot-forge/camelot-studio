# Project status

State of Camelot Studio (M1 delivered; documentation audit closed Sept 2026).
Byte-level format facts: `docs/formats.md`. If you are a new contributor or
agent, read this, then `AGENTS.md` for repo rules.

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
| `camelot project new\|open\|info\|verify\|import\|populate\|release\|releases\|deploy\|promote\|publish\|envs` | the project: one server's whole content in one folder (release, dry run, deploy to an environment); `publish` commits a release's payloads and its feed to the `camelot-deploy` branch — the transport a server this machine does not reach pulls (D §D2/§D5) — and **signs the release record** with `machine.json`'s `release_key` (D §D7) |
| `camelot env provision\|status\|verify\|apply\|reverse\|reload [ENV]` | an environment's server half: its own database built from OpenDAoC's baseline, what it holds, the Installer's step, the read-back; an environment declared `"client": false` in `machine.json` is **server-only** — the client half belongs to the feed (D §D3); `reverse` puts back what one release replaced, in one transaction, and withdraws it from the folder and the ask — a **repair**, not a rollback (S §D17) |
| `camelot serve [--root DIR] [--port N] [--server-rows F] [--db-config F] [--db-write] [--edits D] [--no-open]…` | local daemon: indexes the client, serves the viewer + a JSON API (FastAPI/uvicorn); `--server-rows` (a snapshot) or `--db-config` (the server's own `serverconfig.xml`, live) adds the server's rows, read-only; `--edits` turns on the **world editor** (6.3: select, move/rotate/scale with snap, catalog add, remove, the undo journal, `build`/`apply`/`revert`/`restore`); `--db-write` lets it also patch the server's **existing** world rows (B2: a keep's own doors move with the keep) |
| `camelot console --root DIR [--env NAME] [--project DIR] [--feed DIR] [--game-server H:P] [--api-url U] [--api-password P] [--allow-restart] [--deploy DIR] [--deploy-remote NAME] [--release-key F] [--feed-out DIR] [--audit F] [--host H] [--port N]` | the running server's Console (P7): the applied release and rows, the reload recorded in the project's log, the feed's version, the game port and the read API; `--allow-restart` enables the one server action (shutdown + supervisor); `--deploy` enables **Pull** — fetch the `camelot-deploy` branch, check who signed the release record, verify its hashes, hand the server half over, write the wanted marker, mirror the feed (D §D4); `--release-key` is the operator's `.pub`, and without one the page says the server does not check signatures (D §D7); **Reverse** writes the ask for the other direction (`reverse.json`, honoured by the Installer's step, S §D17); one audit line per attempt; a password is required beyond loopback |

The server side also ships as containers: `deploy/compose.yaml` is one entry
point that `include`s the three parts, and each part runs alone — `server/`
(MariaDB 10.6, OpenDAoC **pinned by digest**, and the `camelot-core` job that
runs the Installer's step), `feed/` (`feedserve`), `console/`. The job runs the
same `camelot` CLI the desk runs, so importing `camelot.cli` pulls neither
`pyffi` nor `fastapi` (a server writes rows: it has no client to parse and no
page to serve — `test_the_server_needs_no_client_reader_and_no_daemon`).
`deploy/README.md` maps the containers; `docs/admin.md` is the operator's
runbook.

The Installer also exists as a **C# plugin**
(`server-bridge/camelot-installer/`): copied once into the server's script
folder, compiled at boot, run through `IDatabaseUpdater` like the mod host was.
It honours the Console's ask, applies what the folder holds oldest first, writes
the same `state.json`, `applied/` and `reversed/` the desk's CLI writes, and
never lets a failure reach `UpdateDatabase()` — a thrown exception there is a
server that will not start (S §D10). Both ways of running it produce one folder
and one state; the Console cannot tell them apart. Its README says what it does
not do (no reload, no restart, no ids allocated).

A release travels **signed** (`camelot/sign.py`, D §D7): `publish` signs the
release record with `ssh-keygen -Y` (OpenSSH's SSHSIG, Ed25519, namespace
`camelot-release`) and the signature rides in the branch beside it; `pull` checks
it against the operator's `.pub` **before** it believes the hashes the record
carries — the hashes are written in the record, so only the signature makes them
mean anything. A server with no key is not a server that fails: it hands the
release over and reports that it did not check, and the Console's page says so.

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
for the loader, and it settles two things the XML package format could not.
The pack declares *how many* ids it needs per table and describes its rows
as columns and values; it ships no SQL and never chooses an id. The manager
allocates, records and writes, so overlap between writers is impossible by
construction rather than detected, and nothing a pack carries can execute
arbitrary statements against a database. *(2026-10-14: the third-party
framing is retired — the pack is one release of one server's content, not a
mod; `docs/next-steps.md`.)* The `client` section carries the pack's client
half: the destinations it replaces on players' machines (a loose file, an
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
(P5-A…P5-F pass; see the workspace report
`notes/test-p5-report.md`), with P5-F's server half still to be
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
byte-exactly reversible. The world editor's design is the private
workspace's `notes/phase6-world-editor.md`; the formats are in
`formats.md` §11.

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
1.127 client under Wine: a rewritten container, modified table data and a new
model id all reach the world, and the loader's server half deploys the
matching row and spawn. Records — including the traps this run paid for — are
in `workspace/notes/`.

## Architecture decisions

- **Language/stack**: Python core (pyffi ecosystem) + TypeScript/Svelte web
  viewer (M1) + Python Blender addon (M2) + artifacts bridge to the C#
  OpenDAoC server (M2). C# never touches binaries — it consumes our
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
  `(x,y,z)→(x,z,−y)` (`formats.md` §2 "Up axis"): the former version-based
  "early 4.x is X-down" rule was a pre-row-major artifact and is gone. **The
  world and the figures now share it** (`nifutil.WORLD_AXIS_CONVERSION` *is*
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
on purpose, since that comparison is what they assert. Measured on the
workshop machine, 2026-10-12: the full sequential run was 666 s, 72 % of it
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
client — the recipe and its hashes are in the workspace's
`notes` §2.3) the suite was **535 passed / 0 failed** with
Phase 6.0, then **554 passed / 0 failed** with 6.2's world API (2026-10-11),
with `OPENDAOC_RACE_SQL` pointing at OpenDAoC-Database's `race.sql`; without
it one test skips. The web suite is 47 passed / 3 skipped, `pnpm check`
reports zero errors and three existing warnings (measured on the test
machine, 2026-10-11).

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
     clavicle breathing) drift from the bind and used to refuse
     amazon/rat/bear/cata_lion/badger/amb_blackcat — all of which deform
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
in one coherent family instead of the mixed placeholder sheets the builder
used before. The default is the client's first family (`Hard Leather`, pskin
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
live client plays for the male Troll player is still to confirm
(the private workshop's `character-rules.md` §8.18).

## Environment gotchas

- **Browser verification**: Playwright is the default tool (headless, via the
  `web-verifier` subagent). Chrome DevTools MCP is reserved for real logged-in
  sessions only — routine UI checks must not open a browser on the host.
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

The canonical roadmap is `docs/next-steps.md`. The immediate order is:

1. **Phase 6 — the world editor** (decided 2026-10-11; design in the private
   workspace, `notes/phase6-world-editor.md`): 6.0 the zone data layer
   (`core/camelot/zones.py` — `zones.dat`, the PCX heightmaps, `fixtures.csv`,
   `sector.dat`), then 6.1 the client experiment (which zone archive is read,
   and whether a rewritten one is accepted) — one session, and it gates the
   rest; then the `/world` route, the editor, and spawn publishing through the
   existing pack chain. 6.0–6.2 are done and passed their in-game checks
   (the world frame, the ground layer composition, the patch seams).
   **The agreed order for what follows** (operator + agent, 2026-10-11;
   the *why* and costs are the workspace spec's §11):
   **(a)** the `Z` vs `Ground` experiment (W-3) — **done 2026-10-12**
   (`Ground = 1` wins, measured in game); **(b)** the **spawn layer,
   read-only** — **done 2026-10-12**: `/world` draws the server's `mob`,
   `worldobject`, `door` and `path` rows from a snapshot (`--server-rows`) or
   the live MySQL (`--db-config`), its own region's rows only (world
   coordinates are per region — the first cut mixed seven regions into
   Camelot Hills; corrected the same evening, rules read from the server:
   the `Region` column, `door.InternalID / 1 000 000`, and the patrols a
   zone's mobs walk). 2,075 mobs / 21 statics / 9 doors / 3 patrols in
   Camelot Hills (counts and shots in the workspace report);
   **(c)** the editor (gizmos, snap, add/remove, the undo journal,
   the authoring directory) **plus the publish path** — **done 2026-10-12**:
   `--edits DIR` opens an editing session (the journal lives in the
   project's authoring directory; `build` runs the same `mod build` path and
   `apply` the same `client install`); the viewer carries the gizmo and the
   panel (the workspace report has the byte-level proof and the W-5 recipe);
   **(c2)** editing the server's own rows — **done 2026-10-12**: a fixture
   move carries the rows that stand inside its own meshes (B2, the case W-1
   measured — Camelot Hills' keep carries its two doors, and, as measured,
   the 27 NPCs and 2 banners its court holds, which the panel names before
   anything is written); a row is moved or removed on its own too, and both
   land through the same journal: `apply` patches the database behind the
   explicit `--db-write`, recording every original it read, and `revert` puts
   them back exactly. Such an edit is server-only — no pack, no client half.
   **A publish commits**: the client carries the journal's ops afterwards, so
   they leave it and the next edit starts from what the client holds (an op
   recorded before a publish could no longer apply against the published base
   — the operator hit exactly that). The rows the session writes are recorded
   in a per-session ledger the first time they are touched, so `revert` and
   `restore` always mean *the state the session started from*, whatever was
   published in between;
   **What the game shows is a fourth layer**: a running server reads its rows
   once, at startup, so a written patch is in the database but not on screen
   until the server's own `/reload doors`, `/reload mob`, `/reload object`
   (region-scoped, admin) or a restart — measured from the server's own
   commands on 2026-10-12, and the panel says so. And because the installed
   client half belongs to the *client*, a journal-only `revert` leaves it
   behind: `restore` is the call that puts the client's half, the rows and
   the journal back together (the uninstall first, so a refusal changes
   nothing);
   **(d)** the sky, fog and
   the day/night time (a slider first — the server owns the real clock,
   `formats.md` §11), then terrain/texture editing and the dungeon/city
   variants.

**The UX pass (2026-10-12, after B2's in-game check).** The editor moved out
of the viewer's sidebar into its own floating, draggable panel, and the mouse
grammar was rewritten: **left is selection** (click, box, shift adds) — and a
drag that starts *on the selection* grabs it, because the middle button is not
guaranteed to exist; the **middle button** does the same (shift+middle rotate,
alt+middle lift, ctrl+middle scale). Right orbits, shift+right pans, the wheel
zooms, and the scene never shows the browser's own menu. The panel reads like
a repository: the publish button carries the count of unpublished changes, and
the client's own installed half, the journal and the written rows are named
separately. Verified by `web/scripts/world_mouse_probe.mjs` (real pointer
events through the page's own path).

**The UI modernization, slice 1 (2026-10-14): the shared shell.** The three
pages now wear the same frame: a 60 px **app bar** (brand, context chip, the
**Figures · Builder · World** switch, page actions) and a 28 px **status
bar**; the rule that maps a pathname to its section is `lib/nav.ts`
(unit-tested), and the design tokens the drafts asked for (axis colors, the
control/size/radius/type scale) live in `app.css`. The old one-off headers
are gone (`Header.svelte` deleted; the builder's hand-rolled header and the
world's `h1` replaced). Verified in the real browser against the running
daemon (figures, builder, world, and a loaded figure + Camelot Hills) with
zero console errors; `pnpm test/check/lint/build` all green. The Penpot
reference is the file's “Drafts” and “Design system” pages; decisions in
`workspace/notes/ui-modernization.md`.

**Slice 2a (same day): the world editor's tools.** The panel gave two jobs
away: a floating **View/Edit chip above a vertical tool strip** on the canvas
(Select, Move, Rotate, Scale, Add, Measure + the snap magnet, with
V/W/E/R/A/M shortcuts — View steps the strip back to select + measure and
hides the panel) and a docked **status bar** that took the cursor's ground
readout, the gesture reminder, the measure line, the journal summary and the
build stamp out of the panel. Mono left this page too (the zero-mono
decision; `code` stays mono on purpose). Verified against the running daemon:
Edit = 7 tools + the panel, View = 2 tools, no panel; the readout tracks the
ground (`37838, 24981 · z 2135`); zero console errors; `pnpm
test/check/lint/build` green. Next: **slice 2b — the Unity-style gizmo back on
the selection**, over the existing mouse grammar; then the viewer/builder
transport, bones and equipment panels.

**Slice 2b (same day): the gizmo.** The selection carries three arrows: X red
(east), Y green (north), Z blue (up, DAoC's own height axis). The axis mapping
and the ray/axis projection live in `lib/world/gizmo.ts` (unit-tested). A drag
that starts *on an arm* moves the selection along **that axis only**,
quantized by the same steps the gestures use (256 units, 16 for heights); the
arrows keep their size on screen (scaled by the camera's distance every
frame), the pointer turns into a `pointer` over them, and the ground grammar
is untouched (drag the selection, the middle button, shift-pan, the wheel).
A ground row refuses the vertical arrow the way the grammar always has (W-3:
its `z` belongs to the terrain, never invented); **holding shift during a
gizmo drag** holds the snap off — whole units, the way Blender's own fine
move works (the status bar says `free (shift)` while it holds), and the
arrows win over the ground's shift+left pan *on the arrows only*: everywhere
else shift still pans the map. Verified in the browser
against the running daemon: a drag on the red arrow moved the Keep **+512 on
`x` only** (2×256 — the snap) and journaled **one fixture op + the 31 carried
rows** (the `carry` behaviour, B2), and the same drag under shift moved it
**+178** (off the grid, y untouched); hover shows the pointer; zero console
errors; `pnpm test/check/lint/build` green. Next: the viewer/builder
transport, bones and equipment panels.

**Slice 3a (same day): the figure viewer.** The read-only page now has the
drafts' shell and tools. A **vertical strip in the same column as the
editor's** (grip, fit, turntable, wireframe, grid, axes, snapshot | ↺ / axis
chip / ↻) floats over the viewport and **drags by its grip** (a click puts it
back at 12,12; the spot is remembered). A **floating animation transport**
bottom-centre: play/pause, `1.24 / 2.40 s` read-out, a scrubber ticked with
the clip's **own key times**, a loop switch and a speed chip — and the client's
`clamp` rule is obeyed: a one-shot **stops on its last frame**, its loop switch
is refused (not silently overridden) and it restarts when played again. The
right dock is now **Inspect**: Model (file, meshes, triangles, bones, skins —
the sidecar's count when there is one), **Animation** (the figure's *own* anim
set, surfaced by a new `/assets/{id}/states` + `?state=` pair on the daemon:
the client's `monnifs → anims → animnifs` chain, the slot's `loop`/`clamp`
flag and its **game timing** — a boar's idle is 5.33 s, not its file's
1.07 s), Bones (search by name *or* channel index, the canonical table behind
a disclosure, the eye that overlays `SkeletonHelper`) and Skins (per-slot
sheets), plus the Character card for figures that carry one. The status bar
reads **fps · tris · meshes · bones**, the gestures and the clip on screen;
`F` frames, space plays. Refusals are the daemon's own: a clip whose channels
do not bind comes back `404 no tracks for state: X` and the row says so
(measured: **64 of the boar's 96 slots** are refused this way), which is what
the draft's “pair refusé” meant. Verified in the browser against the running
daemon by `web/scripts/viewer_probe.mjs` — **46 checks**, all green (strip
order and width, transport clock/scrub/ticks/clamp/speed, the four cards,
the refused pair, one lit row per clip, the grip drag + reload, wireframe,
the bones eye, snapshot PNG and a **glTF export re-parsed as a real GLB**).
Three bugs the browser found and that are fixed: the scene effect re-read its
own `$state` handles (1001 rebuilds per figure — it now works on locals and
only writes them), a synchronous first render-loop call had made the clock a
dependency of the scene's setup, and the transport's play button on a
finished one-shot did nothing (it now starts over). `web/src/lib/transport.ts`
and `lib/inspect.ts` are unit-tested (105 web tests). Next: **slice 3b — the
builder's rail and its own inspector**, then the journal tab and the dock
system (resize grip + pin).

**Slice 3b (same day): the character builder.** The rail is now a dock of the
same grammar (head, sections, foot) with the drafts' four sections: the gender
segment, **the 18 races** with their realm tags (the draft said 15 — the
daemon's roster has 18, so the meta reads what the list holds), the appearance
steppers as a `− value +` row (`1 / 7`), and gear & state. The foot carries the
operator's **Randomize / Reset** (new) and the size of the space they sample:
“114,688 combinations — every pick re-assembles the figure”, computed per race
by `lib/appearance.ts` (`combinationCount`, `randomAppearance`,
`clampAppearance` — a race switch pulls every pick back into the new range
instead of leaving a face index pointing nowhere; 13 unit tests). The
inspector shows the **Character** summary (race, gender, face, hair + colour,
skin, eye, cloak) and **Equipment**, which lists the pieces the assembled
figure actually carries (Eye / Head / Hair / Gloves / Arms / Boots / Legs /
Body) in the material asked for — read-only on purpose: the assembly picks one
piece per slot, and the per-variant picker belongs to a *base* figure (the
figures page still offers it). The viewport is the viewer's own: same strip,
same transport, same gestures, same status-bar grammar, and **Export glTF is
the primary action** (the shared `lib/gltf/export.ts` now serves both pages).
The three pre-existing `svelte-check` warnings are gone: the race list is real
buttons and two dead selectors left. The daemon's character route now names a
refused state the way the asset route does (measured on race 4/true: 0 of its
164 slots are refused — the fig3 rig binds them all, so the builder's refusal
note is a safety net, not a common case). Verified in the browser by
`web/scripts/builder_probe.mjs` — **32 checks**, all green (sections and race
list, the gender switch, a stepper re-assembling, a colour pick only
re-forming its sheet, Randomize inside every range, Reset, the armour material
re-assembling, the state select, the count in the foot, the two cards, the
strip/transport/status bar shared with the viewer, a snapshot PNG and the
GLB export re-parsed). `pnpm test/check/lint/build` green — **check is now 0
errors and 0 warnings**.

**The dock system, and the world editor's Journal (same day).** “My UI, not
ours” is now a mechanism: one `Dock.svelte` carries every panel — the figures'
library and inspector, the builder's rail and inspector, the world's navigator
and editor — with a **resize grip** on its inner edge (260–640 px, drag;
double-click resets), a **pin** that lets it float over the scene (dragged by
its head) and a per-dock memory in `camelot.dock.<id>` (width, pin, spot), so
a layout survives a reload. `app.css` gained the frame, the tab strip and the
badge. Two things the browser taught it: a press on a dock's head started a
**native text drag**, whose `pointercancel` ended the gesture after one move
(fixed with `preventDefault`, `user-select: none`, and treating
`pointercancel` as a release) — and a floating dock needs its own track, so
the world's navigator and the figures' docks now live beside the stage, not
over it.

**The world page was rebuilt around them, as draft 1 drew it.** The left
column is the **Navigator** (client status, region, zone search and list, the
go-to and region search, the zone's own counts, layers, fixtures, the lint
check); the right one is the **Editor**, a dock with two tabs — **Inspect**
(the selection's own fields, its ground-row z rule spelled out, the rows it
carries, the add flow) and **Journal** — and the app bar carries the session
chip, **undo** and **save**. The journal tab is the readable history the
draft asked for: one line per op, with **both sides** (`expect` / `originals`
on one side, what was written on the other), grouped per zone, the server
rows beside them, the count in the tab's badge, and the publish chain
(build / revert / restore all) with the reload hints and the pack line under
it. *(2026-10-14: the pivot replaces this chain with **releases** — one
server package and one client update — and **environments** (`local`,
optional `staging`, `production`) with promotion between them; phases P3–P5
of the private workspace's `notes/`. None of that is implemented
yet.)* `lib/world/journal.ts` holds the reading, unit-tested (11 tests). The
last hex palette went with it: the page now uses the same tokens and dock
grammar as the rest of the studio, mono included.

Verified in the browser: `web/scripts/dock_probe.mjs` — **28 checks**, all
green (head/grip/pin on both pages, the width floor and the remembered width,
floating out of the flow, dragging by the head, the spot surviving a reload,
the world's tabs and their badge, a fixture picked in the navigator landing
in the inspector, a typed field journaling one fixture op **and the 31 rows
it carries**, the head's count matching the daemon's, the journal line read
with both sides, Delete removing and journaling, and the session left clean).
The three older harnesses still pass (46 viewer + 32 builder + the world
grammar probe, whose tool clicks now go through the keys the strip
documents).
**The project's first slice (2026-10-15).** The spec set in the private
workspace (`notes/`) makes the product the content suite of one server;
its first phase is the container. `camelot project new|info|verify`
(`core/camelot/project.py`) create and read a project: `project.json` (the
schema `camelot.project/1`, the base fingerprints, the cache policy, the
environments), the git scaffolding (a `.gitignore` for the derived data and
for this machine's own file, a `.gitattributes` with the LFS rules and the
union merge of the deploy log) and `machine.json` — untracked on purpose,
because nothing tracked may be machine-specific. The base is **referenced by
fingerprint** and never copied: the three resources a real client is read
through (`game1127.dll`, `gamedata.mpk`, `zones/zones.mpk#zones.dat` — 5.9 MB,
582 KB and 9.8 KB) hash in **0.4 s** on the operator's rig, which answers M-4;
`verify` reads them back as `ok` / `moved` / `missing`, naming the resource
that moved. 22 unit tests, none of which needs a client (the synthetic base is
three files a fixture writes); `ruff check` and `ruff format --check` clean.

**The journal's kernel (same day).** `core/camelot/journal.py` holds the
concurrency model the whole team story rests on: **one append-only shard per
author-session** (`journal/<at>-<author>.jsonl`, never shared — two sessions in
the same second get two files), a **total order** (timestamp, ties broken by
the shard's name, the shard's own `seq` inside), a **cut** a release can cover
(`shard -> last seq`, so "pending" is computed and never stored), and **drops
as history**: undo and revert append a `drop` naming an op (`shard:seq`) and
the replay skips it — a shard is only ever appended to. `replay()` applies the
ops in that order and requires each one's `expect` to be what the state holds
at its own point: two sessions that moved two fixtures merge without a word,
two blind edits on *one* fixture are refused with the shard, the line, the row
and both values, and an op on an existing row that carries no `expect` is
refused rather than trusted. 19 tests, including the semantic half of the team
test (the git half is a harness); the suite is 471 passed.

**The editor consumes it (same day).** `serve/editor.py` keeps no mutable
`journal.json` any more: the ops are appended to their shard, a **publish is a
fact** recorded in `deploy/log.jsonl` (the keys it covered, the rows it wrote),
and what is *pending* is computed from the two. So **nothing about a session is
mutable on disk**: the `originals` ledger is derived (the first patch of a row,
in journal order — what `revert` restores to) and the panel's `applied` flag is
derived too (a line the log covers), instead of being written back into the
journal. Undo **appends a drop**; a line a publish covered is refused instead
("the client and the server's rows carry it — use revert"), because dropping it
would leave a live change with no journal entry behind it. A legacy single-file
`journal.json` (6.3) is **migrated once**, on first read: every op becomes a
line, every patch a `rows` line with the originals it already carried, the
patches that were already written become a *publish* fact, and the file is moved
aside as `journal.json.migrated` — a session is never read from two places.
The flag is now `--project DIR`; `--edits` stays as the older name of the same
directory. Measured on the operator's own client: a legacy session migrates,
reads back with its op and its patch, and the deploy log records what happened;
the editor's 26 tests pass.

**Populate, import, and the team test (same day).** `camelot project populate`
records the resources an *edit* depends on — a zone's placements, its catalogue
and its metadata, read through `csv` first and `dat` for what only it carries,
the resolution the reader itself uses — and a table whole out of
`gamedata.mpk`. Nothing the viewer only *looks* at is fingerprinted (the
heightmaps, the ground patches): a drift check that cried wolf on every visual
asset would be noise, and M-2 is the measurement that could change that.
Measured on the operator's client: **565 resources over 402 zone folders in
0.64 s**, a 65 KB `project.json`, and `camelot project verify` reads all 568
back in **0.61 s** — so the drift check is silent at every scale we have.
Resource names are written *canonically* and read through the client's own case
rule (`zone000` beside `ZONE008`), so a project populated on one machine opens
on another. A recorded resource whose bytes moved is **refused by name** unless
`--update` says otherwise: adopting a moved base silently is what re-base (P8)
exists for.

`camelot project import DIR` makes a 6.3 authoring directory a project **in
place**: the same scaffolding `new` writes (and never over an operator's own
notes, `.gitignore` or machine file), the legacy journal migrated to a shard by
the kernel's own converter, the facts it implies (what 6.3 said was written,
then the migration) recorded in the deploy log, the mod identity written to
`mod.json` when the journal was its only home, and `journal.json.migrated` left
beside the shard. Measured on a copy of the operator's session: imported, the
scaffolding in place, and the daemon opens it (`--project`, `available: true`)
with nothing lost.

The **team test** is `core/tests/test_project_team.py`: two clones of one
project, a commit each, a `git pull` — two shards merge as a union with no
conflict, the deploy log keeps both sides' lines (its `.gitattributes` rule),
the merged journal reads in the timestamp order, and a line another session
wrote blind against the same row is **refused by name** when the journal is
read back. That closes P1's exit criteria: the container, the base, the shards,
the refusals, and a session that opens as a project.

**Home, the front door (2026-10-16, P2 of the workspace's `notes/`).**
Two slices. The daemon now serves `/home`: what *it* reads (the client, its
zone folders, the port, the build), the projects this machine has opened —
five at most, from `recent.json` in the per-user config directory
(`~/.config/camelot-studio`; never in a project, never in git) — and one
`verify` per row when the page asks (0.61 s for 565 resources, so the page
paints first and the rows fill in). A row carries the work waiting too, counted
the way the editor's panel counts it (`pending_counts`: distinct fixture ids,
distinct `table:key`).

The doors followed: **open** switches the running daemon to another project
without a restart. One `Session` (`core/camelot/serve/session.py`) owns the
shared config and the checkout's lock, so a switch mutates in place and no
router is rebuilt: the lock hands over (read-only when another live process
holds the copy), the client is re-resolved *from the project* (`machine.json`,
then `$DAOC_CLIENT`, then the current root — and that last fallback says so),
the per-project caches are dropped (the asset index, the zone index, the
server-rows snapshot, the editor's journal and its live connection) while the
exported artefacts stay (they are keyed by path and mtime), the file watch
follows the new root, and the visit is recorded. Leaving a project with
unpublished edits is a **warning line, never a wall** — the journal *is* the
save, so an Alt+F4 costs nothing but the lock. **Create** writes
`project.new` then records the whole client (`populate` over every zone folder
plus the tables it ships: `items.csv`, `monsters.csv`, `objects.csv`),
**import** turns a 6.3 edits folder into a project where it stands, and
**clone** runs `git clone` inside the daemon (the operator's own credentials;
no key ever reaches the browser) and writes this machine's untracked
`machine.json` into the fresh clone. `web/scripts/home_probe.mjs` drives all
four against a real client — **55 checks, green** (a project created from the
browser, an edit surviving a switch away and back, a clone that carries its
journal) — and the four older harnesses still pass. 531 tests.

**Releases, first slice (2026-10-17, P3 of the same spec set).**
`core/camelot/release.py` cuts a release and nothing else — a deploy is the
next slice. A release **is its range**: the journal lines beyond the previous
release's cut, an op *and* a drop (`journal.cut()`/`pending()` were written for
exactly this), and a range that is empty is refused with "nothing to release"
instead of producing a twin of the release before it (`pipeline.md` §D5).
`releases/NNNN/release.json` is the record and it is committed: the cut, the
base it was built against, the families, the ids the range allocated, the two
artifacts with their `sha256`, the publication **order** (§D6: the client half
first whenever the release carries one) and the tables a row patch will make
the server re-read. The payloads are not committed — `.gitignore` already
excluded `releases/*/server/` and `releases/*/client/` — because the record
plus the journal plus the base rebuild them: `camelot project verify --rebuild
N` is what a CI job runs, and the test proves a **second clone** (a real `git
clone`, payloads absent) rebuilds release 1 byte for byte. The server half is
the pack `camelot mod build` already produces; the client half is what that
pack would install on a client, named with its hashes in a `patchlist.json` —
the material the feed will publish. Measured on the operator's client: one
edit cut to release 0001 in one call, the rebuild reads the same `sha256`
back, a second cut says there is nothing to release. 540 tests. Two kernel
bugs fell out of it, both fixed with their tests: two journal shards opened in
the same second sorted the *later* one first (a chain of ops replayed out of
order), and the pack's identity came from the project's **folder** instead of
its name (a clone in another directory built different bytes).
2. Keep the delivery chain honest while it grows: the loader's own used-id
   export, the `objects` cap-5000 recipe, and the ids above 32767 (the signed
   16-bit wire) are the next measurements.
3. The NIF writer (P5-B) is the gate on mods that *bring* new geometry; it
   runs parallel to Phase 6, not inside it.
4. Blender integration and broad product features stay deferred until the
   world editor can publish a map mod end to end.

## Command cheat sheet

```sh
# Run from the camelot-studio repository root.
export DAOC_CLIENT="/path/to/DAOC CLIENT V2"        # optional but recommended
# Corpus validation (read-only; long-running). The manifest is a list of
# client paths; `core/tests/manifest.example.txt` shows the format and the
# one the workshop uses is not shipped here.
.venv/bin/camelot batch /path/to/manifest.txt \
  --read-only --out /tmp/camelot-corpus-report.txt
# Inspect and export a client asset.
.venv/bin/camelot info "$DAOC_CLIENT/figures/Ettin.NIF"
.venv/bin/camelot gltf "$DAOC_CLIENT/figures/Boar.NIF" --out /tmp/boar
# Build a mod pack and apply its client half (build against a *pristine*
# copy of the client; `client patch` is the unmanaged ceiling lift).
.venv/bin/camelot mod build --dir mymod --client /path/to/clean-client \
  --out mymod.tar.gz
.venv/bin/camelot client install mymod.tar.gz --client "$DAOC_CLIENT"
.venv/bin/camelot client verify --client "$DAOC_CLIENT"
.venv/bin/camelot client free-ids --client "$DAOC_CLIENT"
# The server's own world rows (mob/worldobject/door/path) in /world: export
# them once from the server's MySQL (needs pymysql: pip install '.[mysql]'),
# then serve the snapshot — or read the live DB through the server's config.
.venv/bin/camelot server export --config /path/to/serverconfig.xml \
  --out server-rows.json
.venv/bin/camelot serve --root "$DAOC_CLIENT" --server-rows server-rows.json
# The project (P1 of the private workspace's `notes/`): the whole content
# of one server in one folder. `new` records the base by fingerprint (three
# resources, ~0.4 s on the operator's rig) and writes the git scaffolding; the
# derived data (ids.json, the cache, the release payloads, the feed) stays out
# of git, and this machine's own paths live in the untracked machine.json.
.venv/bin/camelot project new camelot-hills --client "$DAOC_CLIENT" --git
.venv/bin/camelot project info --dir camelot-hills
.venv/bin/camelot project verify --dir camelot-hills
# Releases (P3): cut the next one, read the history, rebuild one from the
# journal plus the base and compare the bytes (what a CI job runs). The record
# is committed; the payloads under releases/NNNN/ are not.
.venv/bin/camelot project release --dir camelot-hills
.venv/bin/camelot project releases --dir camelot-hills
.venv/bin/camelot project verify --dir camelot-hills --rebuild 1
# An environment's server half (P5 of `notes/`): `provision` drops the
# environment's *own* database and imports OpenDAoC's baseline into it (110
# files, 72 MB, ~15 s on the operator's machine), then starts the Installer's
# record over; the environment never gets the database the authoring server
# edits. `deploy` hands the release over, `apply` writes its rows from here
# (what the C# Installer does at boot on a real server), `reload` records the
# in-game ritual the release named, `verify` reads it all back. This machine's
# answers (the folder, the database, the baseline, the connection) live in the
# untracked machine.json.
.venv/bin/camelot env provision local --yes --dir camelot-hills
.venv/bin/camelot env status local --dir camelot-hills
.venv/bin/camelot project deploy 1 --env local --dir camelot-hills
.venv/bin/camelot env apply local --dir camelot-hills
.venv/bin/camelot env reload local --dir camelot-hills
.venv/bin/camelot env verify local --dir camelot-hills
# A bad apply is repaired on the server, and it is a repair, not a rollback: the
# originals the release recorded go back in one transaction (a column to the
# value it had, a removed row re-inserted whole), the release leaves the folder
# and the state — a pull is what puts it back — and `wanted.json` loses it. The
# feed does not go back: a step back for the players is a new release (S §D17).
.venv/bin/camelot env reverse local --dir camelot-hills
.venv/bin/camelot env reverse local --release 6 --dir camelot-hills   # or name one
.venv/bin/camelot server tables                      # the tables this project owns
# Home is the front door (P2 of `notes/`): the daemon serves it at /home.
# The browser lands there when the daemon was started without a project, and on
# /world when it was started with one. From the page, Open / New project /
# Import a folder / Clone a repository switch the *running* daemon to another
# project — the checkout's lock hands over, the client is re-resolved from the
# project, and the page says what the project being left still holds. No
# restart, and no door ever writes into a client.
.venv/bin/camelot project open camelot-hills        # verify it, then serve it
# The world editor (6.3): the project's directory (--edits is its older name).
# The journal's shards, the build outputs and the deploy log live in DIR;
# `apply` installs through the same path as `camelot client install` (and
# refuses while the game runs). Open /world, click an object, move it.
.venv/bin/camelot serve --root "$DAOC_CLIENT" --project /path/to/project
# To let a move carry the rows inside a fixture (a keep's own doors, a gate on
# a wall) or to edit a server row on its own, the daemon needs the server's
# database: `--db-config` reads it, `--db-write` is the explicit permission to
# write it. Every patch records the originals it read, so `revert` is exact.
.venv/bin/camelot serve --root "$DAOC_CLIENT" --edits /path/to/authoring \
  --db-config /path/to/serverconfig.xml --db-write
# A running game server reads its own rows once, at startup — a written patch
# is in the database but not on screen until it is told (in game, admin):
#   /reload doors      (DoorMgr.Init, all zones)
#   /reload mob        (the region you stand in)   /reload object  (same)
# `restore` (the panel's "restore all") puts the installed client half, the
# written rows and the journal back together; `client uninstall MOD` does the
# client's half alone.
```
# The server, as containers (`deploy/`): the database, OpenDAoC (pinned by
# digest) and the `camelot-core` job — the Installer's step — plus the feed
# service and the Console. One `.env` for the whole stack; the three values that
# differ between a desk and a VPS are the public feed URL, the game's address
# and the secrets. `docs/admin.md` is the runbook.
cp deploy/.env.example deploy/.env && $EDITOR deploy/.env
docker compose up -d db
deploy/server/camelot.sh provision     # the baseline, then the releases the
                                       # checkout already carries
docker compose up -d                   # db, core, feed, console
deploy/server/camelot.sh api '<the api password>'   # atlas_api + api_password
deploy/server/camelot.sh restart       # the API host starts at boot only
docker compose logs -f core            # the game server's own output
