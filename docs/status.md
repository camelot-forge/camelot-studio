# Project status

State of Camelot Studio (M0 → M0.5, Sept 2026). Byte-level format facts:
`docs/formats.md`. If you are a new contributor or agent, read this, then
`AGENTS.md` for repo rules.

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
| `camelot serve [--root DIR] [--port N] [--no-open]…` | local daemon: indexes the client, serves the viewer + a JSON API (FastAPI/uvicorn) |

**Coverage**: full client NIF corpus = **5087/5089 parse** (the 2 remaining
are 10.1.0.0 housing files failing inside pyffi — see `formats.md` §7).
`core/camelot/legacy.py` reads 100% of the client's NetImmerse 4.x meshes
(read-only; a 4.x writer is M2/M3 work for in-place replacement).

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
- **glTF skinning**: `NiSkinData.bone_list` offsets are dead data in DAoC
  figures (match neither inverse nor direct world matrices — the engine
  computes the bind from the node tree). inverseBind = inv(exported
  worldBind) — glTF standard, correct at the bind pose (diagnostic: 7e-15);
  to be confirmed by real rendering in M1.
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
DAOC_CLIENT="/path/to/DAoC client" .venv/bin/python -m pytest core/tests -q
```

97 tests: matrices/axis/CLI/legacy-reader/gLTF/MPAK units on synthetic
buffers, plus corpus tests (parse expectations, kfa summaries, roundtrip,
glTF export, MPAK repack) that auto-skip without `DAOC_CLIENT`.

## Known limits

1. **Write**: pyffi reorders blocks on write (roundtrip validated as
   multisets; worth watching for diff-friendly editing).
2. **Legacy 4.x write**: not implemented (see above).
3. **Bone index table & animation policy** (settled 2026-09-13, do not
   regress):
   - The client's `figures/animnode.dat` (175 names, index = KFA channel) is
     the mapping's backbone (`load_bone_table`); offset/rotation matching
     (`map_kfa_to_nodes`) validates the pair and reports its source
     (`with_source=True`: `"table"` or `"offsets"`).
   - The animation link uses the game's gamedata chain (`monnifs` -> `anims`
     -> `animnifs`), which resolved the "no animation" figures (Arachite02 ->
     `sect_idle`, amazon -> `Hoplite_Idle`, amb_bat -> `amb_bat_fly`, 2HWolf
     -> `2HW_idle`…). Filename conventions + player aliases remain the
     fallback.
   - **Only vetted pairs animate** (`mapping_is_vetted`): table-mapped pairs,
     or offset mappings covering >= 0.9 of translated channels. Partial
     subsets (amazon 17/43, Bear 13/32, CATA boar 19/40) are refused to bind
     — they mix another rig's limbs and walk on air. 306 vetted links, ~280
     animate after the `skin_stretch` gate (AfancB/dracolich style rigs whose
     stored skin bind disagrees with the node tree are refused), 169 partials
     refused, 48 without a mapping.
   - **All translations apply on a vetted pair** (`kfa_translation_trust`):
     the root track is the grounding (root bob with planted feet); dropping
     it made 2HWolf/3head/Zombie/Ettin/Wolf float 1-2.3 units — with it the
     min-Y span is <= 0.34 through the loop (verified in the viewer).
     `skeleton.py` stays a DFS reference only.
   - Open: engine-exact skinning using the stored `NiSkinData` transforms
     (`inverseBind = S·meshWorld⁻¹·axis⁻¹`, `formats.md` §3) would fix the
     `skin_stretch`-refused rigs instead of binding them, but needs the
     viewer grounding reworked (the engine bind of AfancB is rigidly shifted
     302 units).
4. **pyffi 10.1.0.0**: 2 housing files fail (see `formats.md` §7).
5. **MPAK header checksum**: the two *open* header fields (`formats.md` §5)
   are written as deterministic placeholders; whether the game validates
   them needs in-game confirmation.
6. **Figure textures**: auto-resolved from gamedata for **525/617 figures
   (85%)** (monster chain + NIF-name fallback). Skin slots map to meshes by
   node name (body/head/arms…). Still unresolved: player/class figures (the
   figure is a base mesh; skin/armour is per-character), creatures/pets
   absent from `monsters.csv`, Cata/LOD variants (`formats.md` §3).

## Environment gotchas

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

1. ~~pytest suite~~ — **done**: 97 tests (unit + corpus tiers), see
   `docs/status.md` §Testing.
2. GitHub: org + repos created, first commits pushed; CI in place
   (`.github/workflows/ci.yml`: ruff lint + pytest on 3.11/3.12).
   Switch `camelot-studio` to public when ready.
3. ~~MPAK writer~~ — **done**: `core/camelot/mpak.py` + `camelot mpak`
   CLI, unit + corpus repack tests, header `byte 13` decoded; the two
   *open* header checksums documented (`formats.md` §5).
4. Idle coverage: Boar is proven end-to-end; the gamedata animation chain
   (`monnifs → anims → animnifs`, Known limits #3) linked 475 figures and
   **306 vetted pairs animate** (~280 after the stretch gate) with planted
   feet; all reported floats (2HWolf, 3head, Zombie, Ettin, Wolf, items) are
   fixed and user-verified. Next: validate a few more families with the
   `anim_check.mjs` pattern, then revisit the refused rigs via engine-exact
   skinning.
5. ~~M1: Svelte + three.js viewer~~ — **idle playback delivered**: `web/`
   SvelteKit (Svelte 5 runes, Vite, Vitest) loads the skinned `.glb`
   (`GET /api/v1/assets/{id}/model?skinned=true`) plus joint rotation tracks
   (`GET /api/v1/assets/{id}/anim`; the figure's KFA comes from the gamedata
   chain, filename fallback, and only **vetted rig pairs** play — partial
   matches answer 404 and the viewer shows the bind pose) and
   loops the idle with play/pause **at the game's own rate** (`animnifs.csv`
   frames/fps → `{rate, duration}`; classic idles play ~5x slower than their
   key stamps). Validated end-to-end on Boar
   (`web/scripts/anim_check.mjs`: skinned bind ≈ static at IoU 0.89, GPU
   skinned vertices == CPU bake to 1e-4, feet planted through the loop).
   resolves the figure's DDS from gamedata (`core/camelot/gamedata.py`) and
   embeds it (`CS_camelot_texture`); the viewer loads it with three.js
   `DDSLoader`. Multi-slot → per-mesh mapping is open.
   **three.js gotchas (documented, handled)**: r186 `GLTFLoader` binds skins
   with an identity bind matrix while `SkinnedMesh` (AttachedBindMode) keeps
   refreshing `bindMatrixInverse` from the live world — parented meshes
   (Body offset + `__axes__`) then render in the wrong frame. The viewer
   re-binds with the live bind world (`rebindSkinnedMeshes`, unit-tested).
   Likewise `Box3.setFromObject` on a `SkinnedMesh` measures CPU-skinned
   positions (not bind geometry), which grounded models ~0.5 units high —
   grounding/camera use `bindBoxOf` (geometry bounds × mesh world) instead.
6. M2: Blender addon + server-bridge (artifact injection, in-game testing).

## Command cheat sheet

```sh
cd "Code"
export DAOC_CLIENT="/path/to/DAoC client"          # optional but recommended
# corpus validation (all client NIFs), ~12 min read-only
camelot-studio/.venv/bin/camelot batch notes --read-only --out work/report.txt
# walk one file's blocks (debug)
TARGET="DAOC CLIENT/figures/Ettin.NIF" python3 notes
# glTF export
camelot-studio/.venv/bin/camelot gltf "DAOC CLIENT/figures/Boar.NIF" --out work/boar
# quick load sanity
camelot-studio/.venv/bin/python -c "from camelot.legacy import load_legacy; d = load_legacy('../DAOC CLIENT/figures/Ettin.NIF'); print(len(d.blocks))"
```