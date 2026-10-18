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
> (P1–P6); the Console's read-only phase (P7a) is built; the re-base (P8) is
> next. The design lives in the private workspace's `notes/`.

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
├── deploy/          # (F, P7) the feed and Console containers
├── packaging/       # (P6) the players' launcher package
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
with `--server` / `CAMELOT_GAME_SERVER` as an override. `Play` is refused while
the client is behind the feed — the page disables it and the process re-checks
(`C §D17`); `Update` is the only way through.

```sh
.venv/bin/camelot launcher --feed /path/to/feed/ --client "$DAOC_CLIENT"
```

The page's source is `web/launcher/`; its built `dist/` is what the command
serves, and `cd web/launcher && pnpm install && pnpm build` rebuilds it. On
Linux the game runs under Wine (`wine` and `wineprefix` in the launcher's
settings); on Windows, directly.

The player package (`camelot-launcher`, PyInstaller) is built by
`packaging/launcher/`; see its README for the output and `shard.json`.

### The Console

`camelot console` is the read-only face of a **running** server (P7a). It
reads the Installer's `server/camelot/state.json` and applied evidence, the
feed's version, the game port and, when given, OpenDAoC's read API on `:5000`
(`atlas_api`). It writes nothing.

```sh
.venv/bin/camelot console --root /path/to/env --env production \
  --project /path/to/project --feed /path/to/project/feed \
  --game-server 127.0.0.1:10300
```

The page is on `http://127.0.0.1:8770/`; `/api/live` is the same document as
JSON. Beyond loopback a password is required (`--password` or
`CAMELOT_CONSOLE_PASSWORD`). The container is `deploy/console/`; M-3's verdict
(what the server can actually be told) is in `notes/console.md` §D5.
Measured 2026-10-18: OpenDAoC exposes a read API and `/utils/shutdown`;
`/reload` and the messages remain in-game GM commands.

### The remote channel (first pieces)

`camelot project publish N` builds the feed (the one step that needs a client)
and commits the release's payloads and that feed to a **`camelot-deploy`**
branch of the project's repository — the transport a server this machine does
not reach will pull (`D §D2`). The commit is built with git's plumbing, so the
working branch and its index are never touched; the branch carries
`publish.json` (release, feed version, bytes, author) and one `publish` line
lands in the deploy log.

An environment declared `"client": false` in `machine.json` is **server-only**:
the dry run names the release's client half as the feed's and writes only the
server half, and `verify` does not look for a client. That is what unblocks a
VPS deploy.

The server side of the same flow is in: `camelot project pull` (and the
Console's **Pull** action, phase 7d) fetches that branch, recomputes the
release's hashes and refuses a mismatch, copies the server half into
`<env>/server/camelot/releases/NNNN/`, writes `wanted.json` for the Installer
and mirrors the feed into the served directory. It never applies rows; the
page shows the wanted release as `pending` until the state file records it.

The one action (7b) is **Restart**, off unless `--allow-restart` is passed;
it needs the server's API (`--api-url`, `--api-password`) and a writable audit
log (`--audit`, default `<root>/server/camelot/console/audit.jsonl`). It sends
`GET /utils/shutdown`, so a supervisor (systemd `Restart=always`, a Docker
restart policy) must start the server again; without one the action is a stop.
Each attempt — refused or not — writes one audit line, and the page shows them.

### The server, as containers

`deploy/` is the server side: `deploy/compose.yaml` includes three parts —
`server/` (MariaDB 10.6, OpenDAoC **pinned by digest**, and the `camelot-core`
job that applies a release), `feed/` (`feedserve`) and `console/`. Each part
also runs alone, with its own `.env.example` and README. One `.env` drives the
whole stack, and the same files run on a desk and on a VPS: what changes is the
public feed URL, the game's address and the secrets.

```sh
cp deploy/.env.example deploy/.env && $EDITOR deploy/.env
docker compose up -d db
deploy/server/camelot.sh provision     # the baseline, then the releases it carries
docker compose up -d                   # db, core, feed, console
```

The job runs the same `camelot` CLI the desk runs, and importing it pulls
neither `pyffi` nor `fastapi`: a server writes rows, and has no client to parse
and no page to serve. `docs/admin.md` is the operator's runbook — the desk, the
server, the daily flow from an edit to a player's update, backups, refusals;
`deploy/README.md` maps the containers.

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

- `docs/admin.md` — **running a shard**: the desk, the server, the daily flow
  from an edit to a player's update, a new VPS, backups, refusals. Start here
  if you are the operator, not a contributor.
- `docs/formats.md` — DAoC client format reference (tables, NIF versions,
  KFA architecture, MPAK, legacy 4.x block layout, remaining cases).
- `docs/status.md` — project state, limits and evidence boundaries.
- `docs/next-steps.md` — documentation audit and server-pack roadmap.
- `AGENTS.md` — repo rules for contributors and agents.

## License

MIT (pyffi is BSD-3; no game assets are distributed).