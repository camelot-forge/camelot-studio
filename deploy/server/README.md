# The server stack

What runs on the machine that serves the game (`notes/remote.md` §D10):
the database, OpenDAoC, and the job that applies a release.

| Service | What it is |
|---|---|
| `db` | MariaDB 10.6, the environment's own database. Publishes on **loopback only** (`127.0.0.1:3307`). |
| `core` | OpenDAoC, pinned by digest. Host network: the game's ports are the host's, and its API binds `127.0.0.1:5000` where the Console reads it. |
| `camelot-core` | A one-shot job running the `camelot` CLI — the Installer's step until the C# Installer exists (S §D10). It is the only container that opens the database. |

The feed service and the Console are their own directories; `deploy/compose.yaml`
includes all four, and this file runs alone for a server that wants only these.

## First run

```sh
cp .env.example .env
$EDITOR .env          # the shard's name, the password, the three paths
```

Then the checkout this job reads: a clone of the project on the `camelot-deploy`
branch, with this machine's `machine.json` in it (`machine.json.example`):

```sh
ssh-keygen -t ed25519 -f /srv/forge/keys/deploy -N ''    # add the .pub as a
                                                         # read-only deploy key
GIT_SSH_COMMAND='ssh -i /srv/forge/keys/deploy' \
  git clone --branch camelot-deploy --single-branch git@github.com:org/shard.git /srv/forge/deploy
cp deploy/server/machine.json.example /srv/forge/deploy/machine.json
$EDITOR /srv/forge/deploy/machine.json
```

`machine.json` is in the project's `.gitignore`: a pull never touches it. Its
`environments.<env>.database` and `.env`'s `CAMELOT_DB_NAME` are **the same name
in two files** — the tool reads one, the emulator's connection string the other.
It carries no `db` block: on a server the connection comes from the job's
`CAMELOT_DB_*`.

```sh
docker compose up -d db
./camelot.sh provision        # the baseline in, then every handed-over release
docker compose up -d          # db, core
./camelot.sh status
```

`provision` **drops and re-imports** the environment's database. It refuses to
drop one this project did not create, so it is safe to run against a machine that
already has other databases.

## The Console's API

OpenDAoC's API is off until the server property `atlas_api` is true, and the host
that serves it is started **at boot only** — a `/serverproperties refresh` in game
does not bring it up. Both properties live in the server's own `serverproperty`
table:

```sh
./camelot.sh api '<the api password>'
./camelot.sh restart          # the API host starts at boot
curl http://127.0.0.1:5000/stats
```

The Console then reads `/stats`, `/stats/uptime` and `/utils/query_clients`, and
its **Restart** action sends `/utils/shutdown/{api_password}`. Without a
supervisor that action is a stop: `core` runs with `restart: unless-stopped`,
which is the supervisor here.

## The job

`camelot.sh` wraps `docker compose run --rm camelot-core …`:

```sh
./camelot.sh apply            # write the wanted release's rows (the Installer's step)
./camelot.sh reverse          # put back what one release replaced (a repair)
./camelot.sh verify           # the database's tables and the folder, read back
./camelot.sh logs             # the game server's output
```

`reverse` is refused while a later release is applied, and it does not undo the
players' side: the feed never goes back, so anything already seen is undone by a
new release (`S §D17`).

The job mounts the checkout read-write (a pull is the Console's, but a fallback
should not need a different container), the environment folder read-write — the
only place anything of ours writes — and the baseline read-only.

The apply itself is the same code the desk runs (`camelot env apply`), so the day
the C# Installer takes over, nothing else changes.

## Moving the emulator

The image is pinned by digest in `compose.yaml`. To move it:

```sh
podman pull ghcr.io/opendaoc/opendaoc-core:latest
podman inspect --format '{{index .RepoDigests 0}}' ghcr.io/opendaoc/opendaoc-core:latest
# put the digest in .env as CAMELOT_CORE_IMAGE=…@sha256:…
docker compose up -d core
```

A new emulator reads the same database; the schema it expects is OpenDAoC's own,
and a release never writes a table the project does not own (`S §D4`).

## What to back up

| What | Where |
|---|---|
| the players and the rows | `mysqldump` of the environment's database |
| what is applied, and what releases replaced | `<env>/server/camelot/state.json` and `applied/` |
| the checkout | it is a clone: re-clone the branch |
| the baseline | OpenDAoC's repository |
