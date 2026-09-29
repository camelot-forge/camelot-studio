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
| `camelot pack --out DIR [--id ID] [--version V] [--material M] [--dry-run]` | build the server content pack: versioned manifest, validation report, checksums lock |
| `camelot serve [--root DIR] [--port N] [--no-open]…` | local daemon: indexes the client, serves the viewer + a JSON API (FastAPI/uvicorn) |

**Coverage snapshot**: a retained full-corpus run reported **5087/5089**
client NIFs parsed (the two remaining are 10.1.0.0 housing files failing
inside pyffi — see `formats.md` §7). Re-run and version the report against
`DAOC_CLIENT V2` before treating the count as a current KPI.
`core/camelot/legacy.py` reads the retained NetImmerse 4.x mesh corpus
(read-only; a 4.x writer is not implemented).

## Server content pack (Phases 2-3, first cut)

`camelot pack` reads the client corpus and emits the **server-neutral
contract** the roadmap asks for. It is an offline, reproducible build — no
daemon, no server, no proprietary bytes in the output.

```sh
DAOC_CLIENT="…/DAOC CLIENT V2" .venv/bin/camelot pack --out ./pack \
  --id daoc.studio --version 0.1.0
```

```text
pack/
  manifest/content.json     the contract (schema 1.0.0)
  reports/validation.json   what resolved, what did not, and the timings
  checksums.lock            SHA-256 per artifact
```

Against the V2 corpus: **36 figures, 324 appearance slots, 5958 animation
states, 0 unresolved**, in ~4.5 s, byte-identical on rebuild.

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

**Not yet done**: the per-target projections (`targets/dolsharp`,
`targets/opendaoc`), the glTF/JSON artifacts themselves, and the deployment
path. Those are Phases 3-4 and need the OpenDAoC acceptance gate first —
see `docs/next-steps.md`.

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

Dated V2 baseline (2026-09-24): 326 core tests passed and 4 failed. The
failures cover a changed `Boar_CATA` animation link, two legacy version
expectations and one historical Ettin bbox golden; they require a separate
data/test decision before being treated as regressions or as an accepted
baseline.
The web suite passed 42/42; `pnpm check` reports zero errors and three existing
warnings.

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
5. **MPAK header checksum**: the two *open* header fields (`formats.md` §5)
   are written as deterministic placeholders; whether the game validates
   them needs in-game confirmation.
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
(`workspace/notes/character-rules.md` §8.18).

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

1. Use the closed audit as the implementation backlog; do not reopen stale
   documentation claims as requirements.
2. Verify the native builder against `DAOC_CLIENT V2`, prioritizing skin-tone
   scope, eye fidelity, armour materials and real browser rendering. The
   18-race list, positional hair styles (`Bald`) and the hair `S/T/M` decal
   bake shipped 2026-09-23; `workspace/notes/builder-fidelity-gaps.md`
   tracks the remaining points.
3. Close only the fidelity gaps confirmed by that audit; keep tests and
   structured visual reproduction cases with each change.
4. Define and implement the OpenDAoC-first neutral content-pack adapter, then
   validate the complete client/server path before adding DOLSharp.
5. Defer Blender integration and broad product features until the server pack
   loop is reproducible.

## Command cheat sheet

```sh
# Run from the camelot-studio repository root.
export DAOC_CLIENT="/path/to/DAOC CLIENT V2"        # optional but recommended
# Corpus validation (read-only; long-running).
.venv/bin/camelot batch ../workspace/notes \
  --read-only --out /tmp/camelot-corpus-report.txt
# Inspect and export a client asset.
.venv/bin/camelot info "$DAOC_CLIENT/figures/Ettin.NIF"
.venv/bin/camelot gltf "$DAOC_CLIENT/figures/Boar.NIF" --out /tmp/boar
```