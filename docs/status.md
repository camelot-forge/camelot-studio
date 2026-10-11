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
| `camelot serve [--root DIR] [--port N] [--no-open]…` | local daemon: indexes the client, serves the viewer + a JSON API (FastAPI/uvicorn) |

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

**The mod deployment document** (`core/camelot/mod.py`) is what a pack
carries for the loader, and it settles two things the XML package format
could not. A mod declares *how many* ids it needs per table and describes
its rows as columns and values; it ships no SQL and never chooses an id.
The manager allocates, records and writes, so overlap between mods is
impossible by construction rather than detected, and a third-party mod
cannot execute arbitrary statements against a database. The `client`
section carries the mod's client half: the destinations it replaces on
players' machines (a loose file, an archive entry, a gamedata row edit),
applied by `camelot client install` with claims resolved by priority.

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
(P5-A…P5-F pass — see the workspace report
`notes/test-p5-report.md`), with P5-F's server half still to be
replayed through the loader with an in-band `Model`. A
stock client cannot load the Studio-authored GLBs — it reads NIF from MPAK —
so deploying new *assets* still waits on a NIF writer. Deploying new *server
data* does not: the loader consumes the deployment document on a booted
server, and a rewritten `gamedata.mpk` is accepted by the 1.127 client
(`formats.md` §5 documents the decoded header carriers). The client
*delivery* half is built (2026-10-08): `camelot mod build` assembles a pack
from an authoring directory, `camelot client install/uninstall/status/verify`
applies its payload with claims and priority — every touched file rebuilt
from the client's own originals and read back — and the loader notes each
pack's payload count at apply time.

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
zero-filled PE section, raise the table to **32768 entries** — a spawn at
model 10000 renders in game. The patch travels as a **recipe** (expected
bytes, bytes to write, the section to append — never a redistributed DLL),
`"kind": "patch"` beside the other payload kinds; the author asks for it with
`client.patch.json`'s `"binary": [{"model_ids": 32768}]`, and `mod build`
generates the recipe against a pristine client (refusing an already-patched
one, or a cap outside 2501..32768). A pack **declares the capability** it
grants and needs (`requires: ["client.modelids.32768"]`), and the installer
verifies it by reading the bytes the client will load (the two compares, the
four references, the section's size) — so a client patched by any means
satisfies it, an unknown capability is refused, and a high-id mod is refused
**before anything is written** when the patch is missing. `camelot client
patch [--restore]` does the same for unmanaged clients, with a `.orig`
backup. Field test: the built pack reproduces the validated DLL byte for byte
(sha256), and uninstall restores the original exactly.
Without the patch, a measured pool of **927 ids** is available (253 gaps plus
674 shipped rows nothing references); `camelot client free-ids` lists it from
the client itself — reading the patched ceiling out of the binary when one is
carried — and `mod build` refuses a `monsters.csv` add at an id the measured
server content references (`--used-models` replaces the list with a server's
own export).

**The client's world is readable** (Phase 6.0, 2026-10-11): `camelot/zones.py`
reads the client's own zone index (`zones.dat`: 483 zones, 358 regions, each
with the world rectangle in 8192-unit cells the server also builds), the
terrain heightmaps (256×256 PCX plus the offset map, factors per zone from
`sector.dat`), the object placements (`fixtures.csv`), the catalog, bounds,
jumps and water — and `height_at()` reproduces the client's own sampling
exactly (calibrated against the client's `fixtures.csv`: median error 0-1 on
the ground-placed zones). The world editor's design is the private
workspace's `notes/phase6-world-editor.md`; the formats are in
`formats.md` §11.

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
  "early 4.x is X-down" rule was a pre-row-major artifact and is gone. The
  viewer normalises scale to ~5 units and keeps a 90° rotate control. Open:
  `Rat.NIF` (10.x) still has its length along the horizontal axis — its tail
  dominates the bbox, visual confirmation pending.

## Testing

pytest suite in `core/tests/` (AGENTS.md TDD rule):

```sh
# unit tests only (no game files needed)
.venv/bin/python -m pytest core/tests -q
# + corpus tests against the real client (skipped otherwise)
DAOC_CLIENT="/path/to/DAOC CLIENT V2" .venv/bin/python -m pytest core/tests -q
```

The suite has unit and corpus tiers. Exact test counts are not a current
product contract; run the commands above for the authoritative local result.
Corpus tests skip without `DAOC_CLIENT`, and the web suite is run separately
from `core/`.

Dated baselines: the V2 corpus run of 2026-10-07 was 390 passed / 4 failed
(the failures — a changed `Boar_CATA` animation link, two legacy version
expectations, one Ettin bbox golden — await a data/test decision). On the
test machine's **completed corpus** (a pristine copy of the OpenDAoC 1.127
client — the recipe and its hashes are in the workspace's
`notes` §2.3) the suite is **535 passed / 0 failed** as of
2026-10-11 (Phase 6.0's zone layer included), with `OPENDAOC_RACE_SQL`
pointing at OpenDAoC-Database's `race.sql`; without it one test skips. The web
suite is 38 passed / 3 skipped, `pnpm check` reports zero errors and three
existing warnings (measured on the test machine, 2026-10-11).

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
   existing pack chain.
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
```