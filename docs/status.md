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
| `camelot gltf <f> --out <prefix> [--skinned]` | glTF 2.0 export (Z-up→Y-up axis node) + `<prefix>.skin.json` sidecar |
| `camelot roundtrip <f>` | read → write → read (multiset compare; legacy 4.x = N/A) |
| `camelot batch <manifest> --out <report> [--read-only] [--limit N]` | bulk validation, live progress; `$DAOC_CLIENT` rewrites `DAOC CLIENT/...` manifest paths |
| `camelot mpak list <archive>` / `get <archive> <entry>` / `pack <out> <name> <files…>` | MPAK reader/writer (`.mpk`/`.npk`) |

**Coverage**: full client NIF corpus = **5087/5089 parse** (the 2 remaining
are 10.1.0.0 housing files failing inside pyffi — see `formats.md` §7).
`core/camelot/legacy.py` reads 100% of the client's NetImmerse 4.x meshes
(read-only; a 4.x writer is M2/M3 work for in-place replacement).

## Architecture decisions

- **Language/stack**: Python core (pyffi ecosystem) + TypeScript/Svelte web
  viewer (M1) + Python Blender addon (M2) + artifacts bridge to the C#
  OpenDAoC server (M2). C# never touches binaries — it consumes our
  glTF/JSON artifacts.
- **Layer contract**: `core/` owns all binary formats; `web/`, `addon/`,
  `server-bridge/` only consume glTF/JSON sidecars.
- **glTF skinning**: `NiSkinData.bone_list` offsets are dead data in DAoC
  figures (match neither inverse nor direct world matrices — the engine
  computes the bind from the node tree). inverseBind = inv(exported
  worldBind) — glTF standard, correct at the bind pose (diagnostic: 7e-15);
  to be confirmed by real rendering in M1.
- **glTF axis**: vertices/normals stay in NIF (Z-up) space and the synthetic
  root node (`__axes__`) applies the axis conversion once (no double
  rotation). **The DAoC client has no consistent up axis** — modern
  Gamebryo 10.x figures export upright (Boar, skeleton, BritonM1), but some
  legacy 4.x figures (Ettin, Zombie) are natively Y-up and export lying flat.
  The viewer normalises scale to ~5 units and offers a 90° rotate control.

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
3. **Bone index table**: KFA indices **0..53 mapped to the Bip01 skeleton**
   (index = DFS pre-order of `skeleton.nif` minus `Scene Root`, verified vs
   `2HW_idle.kfa`; `core/camelot/skeleton.py`). Indices **54..114 still
   open** — extra shared bones (fingers/twists/creature appendages) absent
   from `skeleton.nif`; needs a full-skeleton reference or game.dll analysis.
4. **pyffi 10.1.0.0**: 2 housing files fail (see `formats.md` §7).
5. **MPAK header checksum**: the two *open* header fields (`formats.md` §5)
   are written as deterministic placeholders; whether the game validates
   them needs in-game confirmation.
6. **Figure textures**: auto-resolved from gamedata for **525/617 figures
   (85%)**; the rest are player/class figures (customisable), creatures/pets
   absent from `monsters.csv`, and Cata/LOD variants. Only the Body/first
   skin slot is applied; multi-slot → per-mesh mapping is open
   (`formats.md` §3).

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
4. Bone index table research (KFA indices → Bip01 skeleton): **0..53 done**,
   indices 54+ open (see Known limits #3).
5. ~~M1: Svelte + three.js viewer~~ — **initial delivered**: `web/` SvelteKit
   (Svelte 5 runes, Vite, Vitest) static viewer loading `.gltf`+`.bin`
   (+`.skin.json`); design system adapted from Pictiúr. Animated `.kfa`
   playback is future work (needs index table 54+ and a KFA reader here).
   **Textures**: `camelot gltf` resolves the figure's DDS from gamedata
   (`core/camelot/gamedata.py`) and embeds it (`CS_camelot_texture`); the
   viewer loads it with three.js `DDSLoader`. Multi-slot → per-mesh mapping
   is open.
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