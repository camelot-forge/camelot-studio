# Camelot Studio

An **open-source** tool suite for inspecting, converting, validating and
previewing DAoC client content (models, textures, animations) — built for the
private-server community.

> **Status: M1 delivered; documentation audit closed (Sept 2026)** — core v0.1
> (NIF/KFA reading, glTF export, round-trip, batch validation, a **100% legacy
> reader** for NetImmerse 4.x), MPAK reader/writer, the client's canonical KFA
> bone table (`animnode.dat`, 0..174), and a **SvelteKit + three.js figure
> viewer** (`web/`) that plays a selected idle. Animation links come from the
> client's tables and are mapped through the engine's bone table; risky pairs
> are refused by an empirical deformation check and remain in the bind pose.
> State: `docs/status.md`. Format reference: `docs/formats.md`.

> **Where this is going (2026-10-14).** The intent, in one line: make
> Camelot Forge **the Unity of DAoC servers** — one project folder a shard's
> team opens to author, version, release and ship the content of its server,
> the way a game team works on a game project. Concretely: this tool becomes
> the content suite for **one** OpenDAoC server and its client; one project
> holds the whole content of one shard, and content leaves it as numbered,
> immutable **releases** — a server package and a client update — promoted
> unchanged through environments (`local`, optional `staging`, `production`)
> and published as a feed the players' launcher reads. The project, releases,
> environments, the local test server and the players' launcher are built
> (P1–P6); the Console (P7) and the re-base (P8) are next. The design lives in
> the private workspace's `notes/`.

**Principle: engine fidelity.** The project aims to reproduce the client's
own behaviour — same tables, order and values — and to make every approximation
explicit. Animation channels are applied absolute (statics included, following
the engine's transform overwrite). When a client material stage is not yet
reproduced, the current limitation is documented rather than silently faked;
see `docs/status.md` and `docs/formats.md` for the verified scope.

## Why this project?

The Niftools ecosystem (official Blender addon) has been inactive since 2024
and no longer supports Blender ≥ 4.0. Camelot Studio focuses on the DAoC
semantic layer that generic NetImmerse/Gamebryo tooling does not cover:
`.kfa` animations address the client's bone table, fig3 pieces attach through
`bonelist`, and the MPAK archives plus `gamedata.mpk` tables glue everything
together. Camelot Studio covers that layer on top of formats handled by pyffi
(BSD-3).

## Architecture

```
camelot-studio/
├── core/            # Python 3.11+: camelot CLI (NIF/KFA, glTF, roundtrip, batch…)
├── web/             # (M1) Svelte + three.js viewer: figures & animations
│   └── launcher/    # (P6) the players' page: one feed, Update, Play
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
.venv/bin/camelot gltf "$DAOC_CLIENT/figures/Boar.NIF" --out work/boar

# channels of a .kfa animation file
.venv/bin/camelot kfa "$DAOC_CLIENT/anims/BOAR_idle.kfa"

# roundtrip (read → write → read) validation
.venv/bin/camelot roundtrip "$DAOC_CLIENT/figures/Boar.NIF"

# bulk validation from a manifest of files
.venv/bin/camelot batch core/tests/manifest.example.txt --out work/batch.txt

# local asset library + viewer (daemon; serves the built web/ UI + API)
.venv/bin/camelot serve
```

### The players' launcher

`camelot launcher` is the players' own surface: a page on `127.0.0.1` and the
two things a browser cannot do — update the client and start the game. A
distributed launcher carries `shard.json` beside the executable:

```json
{ "name": "Camelot Hills", "feed": "https://patch.example/" }
```

The page never asks for the feed URL. `--feed` / `CAMELOT_FEED_URL` override it
for a development run; the game address comes from the feed's `launcher.json`,
with `--server` / `CAMELOT_GAME_SERVER` as an override.

```sh
.venv/bin/camelot launcher --feed /path/to/feed/ --client "$DAOC_CLIENT"
```

The page's source is `web/launcher/`; its built `dist/` is what the command
serves, and `cd web/launcher && pnpm install && pnpm build` rebuilds it. On
Linux the game runs under Wine (`wine` and `wineprefix` in the launcher's
settings); on Windows, directly.

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
export DAOC_CLIENT="/path/to/DAOC CLIENT V2"   # add to ~/.zshrc / ~/.bashrc
```

The game client itself is never in git — sync it between your machines with
your usual tool (Syncthing, NAS, external drive).

## Documentation

- `docs/formats.md` — DAoC client format reference (tables, NIF versions,
  KFA architecture, MPAK, legacy 4.x block layout, remaining cases).
- `docs/status.md` — project state, limits and evidence boundaries.
- `docs/next-steps.md` — documentation audit and server-pack roadmap.
- `AGENTS.md` — repo rules for contributors and agents.

## License

MIT (pyffi is BSD-3; no game assets are distributed).