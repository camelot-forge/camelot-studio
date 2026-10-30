# Camelot Studio

An open-source tool suite for authoring, versioning, releasing and shipping the
content of an OpenDAoC server and its client. The repository is `camelot-studio`;
the desk application is **Camelot Studio** (`camelot studio`).

One project folder holds a shard's whole content. Every edit lands in a journal;
a **release** is cut from a range of that journal and travels unchanged through
environments (`local`, optional `staging`, `production`) to the players'
launcher. The current state is `docs/status.md`.

**Principle: engine fidelity.** The suite reproduces the client's own behaviour,
same tables, order and values, and documents every approximation it still makes.
`docs/formats.md` is the format reference and `docs/status.md` the evidence
boundaries.

## Quick start

```sh
git clone https://github.com/camelot-forge/camelot-studio.git
cd camelot-studio
./bootstrap.sh                             # .venv, pyffi patched, core/ installed
.venv/bin/camelot --help
export DAOC_CLIENT="/path/to/DAOC CLIENT V2"
```

`bootstrap.sh` needs Python 3.11+ and network access on the first run. On
Debian and Ubuntu, install the `venv` module first: `sudo apt install
python3-venv`. The browser interface also needs Node 20+ and `pnpm` (`cd web &&
pnpm install && pnpm build`).

The studio runs on Linux and macOS, and in WSL2 on Windows. The players' launcher
is built for Windows and runs under Wine on Linux; macOS is not packaged. The
server runs on Linux. CI runs on Ubuntu, with a macOS Python job.
`docs/install.md` walks the whole install, desk and server; `docs/admin.md` is
the runbook once it is done.

## Architecture

```
camelot-studio/
├── core/            # Python 3.11+: the camelot CLI, and every binary format
├── web/             # Svelte + three.js: the world editor, the figure viewer
│   └── launcher/    # the players' page: one feed, automatic updates, Play
├── deploy/          # the server stack: MariaDB, OpenDAoC, the feed, the Console
├── packaging/       # the players' launcher package (PyInstaller)
├── server-bridge/   # the Installer: the C# plugin an OpenDAoC server runs
├── addon/           # planned: a Blender addon around core/
└── docs/            # format specs, the runbook, the status
```

Layer contract: **glTF + JSON**. Binary formats live only in `core/`; `web/`,
`addon/` and `server-bridge/` consume artifacts.

## The desk

```sh
# first run: no client to name — Home asks for one
.venv/bin/camelot serve

# a project: the whole content of one server in one folder
.venv/bin/camelot project new camelot-hills --client "$DAOC_CLIENT" --git
.venv/bin/camelot project populate --all-zones --dir camelot-hills

# this machine's answers (client, database, key), then read them back
.venv/bin/camelot project machine --dir camelot-hills \
  --client "$DAOC_CLIENT" \
  --db-config ~/daoc/env-local/server/config/serverconfig.xml \
  --release-key ~/daoc/keys/camelot-release
.venv/bin/camelot project check --dir camelot-hills

# the browser: the world editor, the asset library, Deploy, the Journal
.venv/bin/camelot studio camelot-hills --edit
```

`studio` reads `machine.json`, checks it, starts the daemon and opens the browser;
`--edit` is the explicit permission to patch the server's existing world rows.
Every patch records the originals it read, so a revert is exact. With no path it
opens this folder, then the newest recent project, then Home.

The same loop is in the interface: Home creates a project (client, and the
environment's `serverconfig.xml`), Deploy publishes the pending edits, and the
settings fold holds the feed URL and the game address.

```sh
.venv/bin/camelot project release --dir camelot-hills      # cut the next release
.venv/bin/camelot project deploy 1 --env local --dir camelot-hills --dry-run
.venv/bin/camelot project publish 1 --remote origin --dir camelot-hills
```

`publish` builds the feed and commits the release's payloads to a
**`camelot-deploy`** branch, signed with the desk's release key. The commit is
built with git's plumbing, so the working branch and its index stay as they are.

## The players' launcher

The launcher's page runs on `127.0.0.1`; the process around it updates the
client and starts the game. A distributed launcher carries `shard.json` beside
the executable:

```json
{
  "name": "Camelot Hills",
  "feed": "https://patch.example/",
  "launcher": "https://patch.example/launcher/"
}
```

`launcher` is where the launcher's own zip and manifest are published. The page
shows **Restart and update** when the manifest carries a greater version than
the package's `version.txt`. The build writes the key from
`packaging/launcher/build.py --launcher …`.

`--feed` and `--server` override the feed's own values for a development run.
When the page opens it **updates the client by itself** to what the feed
publishes; `Play` opens when that is done. A feed that cannot be read leaves an
already-installed client playable; a first install needs the feed.

```sh
.venv/bin/camelot launcher --feed /path/to/feed/ --client "$DAOC_CLIENT"
```

The page's source is `web/launcher/`; its built `dist/` is what the command
serves. On Linux the game runs under Wine (`wine` and `wineprefix` in the
launcher's settings); on Windows it runs natively. The package is built by
`packaging/launcher/`.

## The Console

`camelot console` is the running server's face: what is applied, who is
connected, and one action — **Install update** (pull, restart, wait for the
Installer). Pull, Restart and Reverse remain under the page's folds.

```sh
.venv/bin/camelot console --root /path/to/env --env production \
  --project /path/to/project --feed /path/to/project/feed \
  --game-server 127.0.0.1:10300 --deploy /path/to/checkout \
  --release-key /keys/camelot-release.pub
```

The page is on `http://127.0.0.1:8770/`; `/api/live` is the same document as
JSON. Beyond loopback a password is required (`CAMELOT_CONSOLE_PASSWORD`).

**Pull** fetches the `camelot-deploy` branch, checks the release's signature and
its hashes, hands the server half over, writes `wanted.json` and mirrors the
feed. It stops there: the Installer applies the rows.

**Restart** needs `--allow-restart`, the server's API (`--api-url`,
`--api-password`) and a supervisor to start the server again. **Reverse** writes
an ask; the Installer's next run puts back the rows one release changed.

## The server, as containers

`deploy/install/` is the server install: one `compose.yaml`, named volumes, and
`./install.sh` (or a Coolify compose resource). It clones the OpenDAoC baseline
and the `camelot-deploy` branch, provisions the database once and seeds the feed.
`deploy/compose.yaml` is the desk's bench, with host paths, and includes three
parts, each of which also runs alone: `server/` (MariaDB 10.6, OpenDAoC **pinned
by digest**, and the `camelot-core` job), `feed/` (`feedserve`) and `console/`.
One `.env` drives the whole stack.

```sh
cd deploy/install
./install.sh          # writes .env, generates the passwords, and exits
$EDITOR .env          # the five FILL values
./install.sh
```

The Installer also runs as a **C# plugin** inside the server itself
(`server-bridge/camelot-installer/`), where the server's own boot applies the
release.

## Documentation

- `docs/install.md` — installing the suite: the desk and the server.
- `docs/coolify.md` — deploying the server on Coolify: the compose application,
  the GitHub App, the domain and the first update.
- `docs/admin.md` — running a shard: the daily flow, backups, and what each
  refusal means.
- `docs/formats.md` — DAoC client format reference.
- `docs/status.md` — project state, limits, and the evidence behind each claim.
- `AGENTS.md` — repo rules for contributors and agents.

## Contributing

The rules are in [AGENTS.md](AGENTS.md); the short version is in
[CONTRIBUTING.md](CONTRIBUTING.md).

```sh
.venv/bin/python -m pytest core/tests -q     # corpus tests skip without DAOC_CLIENT
cd web && pnpm install && pnpm test && pnpm check && pnpm lint && pnpm build
```

## Security

Report a vulnerability through [SECURITY.md](SECURITY.md). Do not open a public
issue for a secret, a live server, or a way to write outside a project folder.

## License

MIT (pyffi is BSD-3; no game assets are distributed). Third-party notices,
including the font licence, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
