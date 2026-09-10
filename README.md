# Camelot Studio

An **open-source** tool suite for editing DAoC client content (models,
textures, animations) — built for the private-server community.

> **Status: M0 → M1 delivered** — core v0.1 (NIF/KFA reading, glTF export,
> round-trip, batch validation, a **100% legacy reader** for NetImmerse 4.x),
> MPAK reader/writer, canonical bone index table (0..53), and a **SvelteKit
> + three.js figure viewer** (`web/`). State: `docs/status.md`. Format
> reference: `docs/formats.md`.

## Why this project?

The Niftools ecosystem (official Blender addon) has been inactive since 2024
and no longer supports Blender ≥ 4.0. Beyond that, nothing exists for DAoC
semantics: `.kfa` animations address bones **by index** of a canonical
skeleton, fig3 pieces attach to the skeleton through `bonelist`, and the
MPAK archives plus `gamedata.mpk` tables glue everything together. Camelot
Studio covers that "DAoC layer" on top of the NetImmerse/Gamebryo formats
(itself handled by pyffi, BSD-3).

## Architecture

```
camelot-studio/
├── core/            # Python 3.11+: camelot CLI (NIF/KFA, glTF, roundtrip, batch…)
├── web/             # (M1) Svelte + three.js viewer: figures & animations
├── addon/           # (M2) Blender addon: UI shell around core/
├── server-bridge/   # (M2) artifacts/integration with the OpenDAoC emulator (C#)
└── docs/            # DAoC format specs & journals (contract between layers)
```

Layer contract: **glTF + JSON** (core exports, web/Blender consumes). DAoC
binary formats live only in `core/`.

## Quick start

```sh
./bootstrap.sh            # creates .venv (pyffi + numpy + setuptools, patches pyffi)
.venv/bin/python -m pip install -e ./core   # optional; bootstrap does it
.venv/bin/camelot --help
```

Examples:

```sh
# info + glTF export of a figure (bind pose, skinned)
.venv/bin/camelot gltf "DAOC CLIENT/figures/Boar.NIF" --out work/boar

# channels of a .kfa animation file
.venv/bin/camelot kfa "DAOC CLIENT/anims/BOAR_idle.kfa"

# roundtrip (read → write → read) validation
.venv/bin/camelot roundtrip "DAOC CLIENT/figures/Boar.NIF"

# bulk validation from a manifest of files
.venv/bin/camelot batch core/tests/manifest.example.txt --out work/batch.txt
```

Set `DAOC_CLIENT` to your client directory to validate against the real
corpus (`camelot batch` rewrites `DAOC CLIENT/...` manifest paths from it).

## Setting up a new machine (Linux/macOS)

```sh
# 1. Python ≥ 3.11 (macOS: `brew install python@3.12` or python.org installer)
# 2. SSH key + git identity (add the public key to GitHub > Settings > SSH keys)
ssh-keygen -t ed25519 -C "you@machine"
git config --global user.name "Your Name"
git config --global user.email "you@users.noreply.github.com"

# 3. Clone, bootstrap, test (corpus tests skip without DAOC_CLIENT)
git clone git@github.com:camelot-forge/camelot-studio.git
cd camelot-studio && ./bootstrap.sh
.venv/bin/python -m pytest core/tests -q

# 4. Point at your local DAoC client copy (synced separately — not in git)
export DAOC_CLIENT="/path/to/DAoC client"   # add to ~/.zshrc / ~/.bashrc
```

The game client itself is never in git — sync it between your machines with
your usual tool (Syncthing, NAS, external drive).

## Documentation

- `docs/formats.md` — DAoC client format reference (tables, NIF versions,
  KFA architecture, MPAK, legacy 4.x block layout, remaining cases).
- `docs/status.md` — project state, limits, next steps, cheat sheet.
- `AGENTS.md` — repo rules for contributors and agents.

## License

MIT (pyffi is BSD-3; no game assets are distributed).