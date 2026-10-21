# Deploying on Coolify

Coolify runs the server install as one Docker Compose application: it builds the
images, terminates TLS for the feed, and keeps the named volumes. This page is
the production walk. The generic install is `deploy/install/README.md`; the
bare-VPS path is `install.sh` in that directory.

## What Coolify owns, and what it does not

| Coolify | Camelot Forge |
|---|---|
| the `camelot-studio` repository | the project repository (`your-org/your-project`) |
| the four images (`core`, `camelot-core`, `feed`, `console`) | the deploy key that reads `camelot-deploy` |
| the proxy and the feed's TLS | the game's TCP/UDP ports |
| the compose application | the Console's Pull, Restart and Reverse |

`camelot-studio` is the only repository linked to Coolify. The project
repository is private and is cloned by `camelot-bootstrap` with the deploy key;
Coolify never sees it.

## Before you start

- a Coolify instance on a server with Docker, reachable at a public HTTPS URL;
- a domain for the feed (`feed.example.com`) and one for the game
  (`play.example.com`), both pointing at that server;
- ports **80/443** open for Coolify's proxy, and **10300/tcp**,
  **10400/tcp+udp** forwarded to the game server;
- a private project repository with the `camelot-deploy` branch, published once
  from the desk;
- the deploy key and the release public key (below).

The feed and the Console are not the same exposure. The feed is public HTTP
behind Coolify's proxy. The Console is an operator page on loopback; keep it
behind an SSH tunnel. The database and OpenDAoC's API stay on loopback too.

## The two keys

### Deploy key

The key reads the **project** repository. It never goes into Coolify's Git
source; it goes into `CAMELOT_DEPLOY_KEY_B64`.

```sh
ssh-keygen -t ed25519 -N '' -C camelot-deploy -f deploy_key
gh repo deploy-key add deploy_key.pub --repo your-org/your-project
base64 < deploy_key | tr -d '\n'
```

Leave write access off. If GitHub answers `Deploy keys are disabled for this
repository`, enable `deploy_keys_enabled_for_repositories` in the organization
settings first.

### Release key

The key signs the release record on the desk. The server gets only the public
half.

```sh
ssh-keygen -t ed25519 -C camelot-release -f ~/daoc/keys/camelot-release
```

Name the private half in the desk's `machine.json` as `release_key`. Put the
contents of `~/daoc/keys/camelot-release.pub` in `CAMELOT_RELEASE_PUBLIC_KEY`,
on one line, without quotes in the Coolify field.

The two keys never swap roles: the deploy key reads the repository, the release
key checks the release.

## Choose the source

### Public repository, no auto-deploy

`camelot-studio` is public. This is the simplest first deployment: it needs no
GitHub App, no webhook and no redirect.

1. Open the target project and environment, then **+ New**.
2. Choose **Public Repository** and paste
   `https://github.com/camelot-forge/camelot-studio`.
3. Select **Check Repository**.

Redeploy by hand when you pull a new `main`.

### GitHub App, auto-deploy on push

Use this when a push to `camelot-studio` should redeploy the stack. The source
needs a public base URL that GitHub can reach; it is not the feed's domain and
not a website that happens to answer.

1. **Sources → New source → GitHub App**.
2. Enter the organization (`camelot-forge`) and a unique app name.
3. In **Webhook endpoint**, choose **Use a custom endpoint** when the instance
   endpoint is wrong, and enter **only the Coolify base URL**:
   ```text
   https://coolify.example.com
   ```
   No `/webhooks`, no trailing path. Coolify appends
   `/webhooks/source/github/events`.
4. Verify that GitHub can reach that base URL. A `404` or `405` on a GET is
   normal; connection refused is not.
5. **Register with GitHub**, then **Install repositories** and select
   `camelot-studio`.

If the source form shows an endpoint that is not Coolify (a marketing site, a
parking page, another host), fix **Settings → Instance → Instance's URL** first.
The GitHub redirect is built from that value.

The GitHub App covers `camelot-studio` only. Do not add the project repository:
the deploy key is its access path, and the content branch is not a Coolify
source.

## Create the application

1. **+ New → Public Repository** (or the GitHub App source).
2. **Build strategy / Build pack**: **Compose**.
3. **Branch**: `main`.
4. **Base Directory**: `deploy/install`.
5. **Docker Compose Location**: `compose.yaml`.
6. Enable **Preserve Repository During Deployment**. The `camelot-bootstrap`
   service bind-mounts `./bootstrap.sh`; without it, Docker can create a
   directory where the file should be.
7. Leave **Compose deployment** on the managed default, not **Raw**. Coolify
   adds the proxy labels the feed's domain needs.

The compose content should list six services: `db`, `camelot-bootstrap`, `core`,
`camelot-core`, `feed`, `console`.

## Environment

Use `deploy/install/.env.example` as the list of values. The five `FILL` values
are yours; the three passwords are generated by `install.sh` on a bare VPS, so
generate them yourself in Coolify:

```sh
for i in 1 2 3; do openssl rand -hex 16; done
```

```dotenv
CAMELOT_ENVIRONMENT=production
CAMELOT_SHARD_NAME=Camelot Hills
CAMELOT_SHARD_SHORT=CAMELOT
CAMELOT_DEPLOY_REPO=git@github.com:your-org/your-project.git
CAMELOT_DEPLOY_KEY_B64=<base64 of the private deploy key>
CAMELOT_RELEASE_PUBLIC_KEY=ssh-ed25519 AAAA... you@desk
CAMELOT_BASELINE_REPO=https://github.com/OpenDAoC/OpenDAoC-Database.git
CAMELOT_DB_NAME=forge_project_production
CAMELOT_DB_PASSWORD=<secret 1>
CAMELOT_DB_PORT=3307
CAMELOT_GAME_PORT=10300
CAMELOT_REGION_PORT=10400
CAMELOT_CORE_UID=1000
CAMELOT_CORE_GID=1000
CAMELOT_CORE_CPU=4
CAMELOT_CORE_IMAGE=ghcr.io/opendaoc/opendaoc-core@sha256:89da37855ab3629273884652b646f3bc2a84398193926646d6721341a58d0d3e
CAMELOT_FEED_URL=https://feed.example.com/
CAMELOT_GAME_SERVER=play.example.com:10300
CAMELOT_CONSOLE_PASSWORD=<secret 2>
CAMELOT_CONSOLE_HOST=127.0.0.1
CAMELOT_CONSOLE_PORT=8770
CAMELOT_CONSOLE_GAME_SERVER=127.0.0.1:10300
CAMELOT_CONSOLE_API_URL=http://127.0.0.1:5000
CAMELOT_API_PASSWORD=<secret 3>
CAMELOT_CONSOLE_ALLOW_RESTART=1
CAMELOT_CONSOLE_DEPLOY_REMOTE=origin
```

The `set CAMELOT_...` strings Coolify may show are Compose's required-variable
messages, not defaults. Replace them.

`CAMELOT_DB_PASSWORD` is fixed when the `db` volume is first initialized. Set it
before the first start. If the volume already ran with another password, remove
`db-data` while there is no player data.

## Domain

Add one public domain:

- **Service**: `feed`
- **Domain**: `https://feed.example.com:8080` (or `https://feed.example.com`
  with the port field set to `8080`)
- **Protocol**: `https`

The `:8080` is the container's internal port; visitors still use `443`.

Leave `db`, `camelot-bootstrap`, `camelot-core`, `core` and `console` without a
domain. `core` is the game's TCP/UDP server, `console` is host-networked on
`127.0.0.1:8770`, and the two jobs have no HTTP.

The feed service has no host `ports` mapping. Coolify's proxy reaches it on the
compose network; a host proxy on a bare VPS needs a compose override instead.

## The game's ports

Coolify proxies HTTP and terminates the feed's TLS. It does not forward the
game: `core` listens on the host's own network, and two things in front of the
host decide whether a player reaches it.

The host's firewall:

```sh
sudo ufw allow 10300/tcp
sudo ufw allow 10400/tcp
sudo ufw allow 10400/udp
sudo ufw status
```

The NAT in front of the host — a home router or the provider's firewall — must
send **10300/tcp** and **10400/tcp+udp** to the host's private address.

`hostname -I` prints that address. It also prints Docker's bridges; the LAN
address is the one the forward must name. Check the game on the host, then on
the LAN address:

```sh
nc -vz 127.0.0.1 10300
nc -vz 192.0.2.10 10300   # the LAN address hostname -I printed
```

If the first answers and the second refuses, the host's firewall is still
closed. If both answer, only the NAT is left. A dropped packet times out; a
port with no listener refuses. A timeout on the LAN address is the host's
firewall, not the game.

Check the public address from a phone on mobile data, not from the LAN: a router
without NAT loopback refuses from inside even when the forward is correct.

```sh
nc -vz play.example.com 10300
nc -vz play.example.com 10400
```

The game server's log says `Server is now listening for incoming connections on
0.0.0.0:10300` in every case. It listens on the host; the log says nothing about
what reaches it.

A client that shows **press ESC** at the end of its loading never reached the
game. It read the feed, started, and failed to open its socket to `10300`.

## First deploy

Select **Deploy**. Coolify pulls MariaDB and OpenDAoC, builds the four images and
starts the services in order:

1. `db` becomes healthy;
2. `camelot-bootstrap` clones the OpenDAoC baseline and the project's
   `camelot-deploy` branch, writes this machine's `machine.json`, seeds the
   feed, provisions `forge_project_production` once, and sets `atlas_api` and
   `api_password`;
3. `core`, `feed` and `console` start;
4. `camelot-core` runs `env status` and exits.

`camelot-bootstrap` and `camelot-core` finish as `exited 0`. That is normal, not
a failure. The `core` service is host-networked; give it no domain.

Coolify may show **Running (unknown)** and **No health check configured**. The
containers have their own health checks; the application-level check is not
needed for routing. The warning is informational.

## Verify

```sh
curl -s https://feed.example.com/healthz
curl -s https://feed.example.com/version-new.txt
curl -s https://feed.example.com/launcher.json
```

Before the first Pull, `/healthz` answers with the feed and game addresses,
`version-new.txt` is `0`, and `release.json` is a 404. That is the seed.

On the server, check the game and the API:

```sh
curl -s http://127.0.0.1:5000/stats
```

The API is pinned to `127.0.0.1:5000` by the compose (`ASPNETCORE_URLS`). The
OpenDAoC image's .NET default is `8080`, which other services may hold. The log
must contain `Now listening on: http://127.0.0.1:5000` and `Game WebAPI open for
connections.`

## The first release

1. On the desk, from the project directory (or with `--dir`), publish:
   ```sh
   camelot project publish 1 --remote origin
   ```
2. Open the Console through a tunnel:
   ```sh
   ssh -L 8770:127.0.0.1:8770 you@server
   ```
   then `http://127.0.0.1:8770/`, as `operator` with
   `CAMELOT_CONSOLE_PASSWORD`.
3. **Pull**. It fetches `camelot-deploy`, checks the signature, verifies the
   hashes, copies the server half, writes `wanted.json`, and mirrors the feed.
4. **Restart**. The C# Installer runs at boot, before the world loads, and
   applies the handed-over release.
5. The Console shows `wanted` = `applied`, `pending` empty, and the feed's
   version matches the server.

The `camelot-core` job is the fallback, for an apply without a restart or a
server without the plugin:

```sh
docker compose --env-file .env -f compose.yaml run --rm camelot-core \
  env apply production --dir /deploy
```

On the Coolify host, that command runs from the application's directory,
`/data/coolify/applications/<uuid>`, which root owns. `cd` is a shell builtin,
so `sudo cd` does not exist: open `sudo -i` and `cd` there.

## Updates

**Camelot Forge.** Push to `main`; Coolify redeploys (GitHub App) or you click
**Redeploy**. `camelot-bootstrap` is idempotent: it skips the existing baseline,
checkout and database, and the named volumes stay.

**OpenDAoC.** Change `CAMELOT_CORE_IMAGE` to a new digest and rebuild the `core`
image:

```sh
docker compose --env-file .env -f compose.yaml up -d --build core
docker compose --env-file .env -f compose.yaml run --rm camelot-core \
  env verify production --dir /deploy
```

Do not run Watchtower on the game server. An OpenDAoC release can change its
schema, and a restart interrupts play.

**Content.** The desk publishes; the Console Pulls; the Console Restarts. The
plugin applies at boot. The two cadences are independent.

## Backups

| What | Where |
|---|---|
| the project and its journal | the desk, and the project repository |
| the database | a `mariadb-dump` of `forge_project_production` |
| the environment and the Installer's state | the `forge-env` and `forge-camelot` volumes |
| the feed | the `forge-feed` volume; reproducible from `camelot-deploy` |
| the deploy key | the desk, and the `forge-ssh` volume |
| the release key | the desk only; the server has the `.pub` |

Coolify prefixes the volume names with the resource's identifier. `docker volume
ls` shows the real names. The database and `state.json` are one artefact: restore
them together.

## When something fails

| Log | What it means |
|---|---|
| `Bind for 0.0.0.0:8080 failed: port is already allocated` | a service publishes 8080 on the host. The server install's feed must not; Coolify's proxy reaches it on the compose network. |
| `Failed to bind to address http://[::]:8080: address already in use` | the API is on the image's .NET default. The compose pins `ASPNETCORE_URLS=http://127.0.0.1:5000`; **recreate** `core` after the change, a restart keeps the old environment. |
| `installer directory 'camelot' does not exist; no release loaded` | `core` has no `forge-camelot` volume at `/app/camelot`; the plugin cannot see the releases. |
| `... has no camelot-deploy branch: publish from the desk first` | the project repository has no `camelot-deploy` branch. |
| `CAMELOT_DEPLOY_KEY_B64 is not an unencrypted SSH private key` | the base64 is the `.pub`, or it is multi-line. Use the private key, one line. |
| `Access denied for user 'root'` | the `db` volume was initialized with another password. Remove `db-data` while there is no player data. |
| `camelot-core` or `camelot-bootstrap` keeps restarting | a one-shot job without `restart: "no"`. The server install sets it on both. |
| `[Errno 111] Connection refused` on `:5000` | the API is off, failed to bind, or is on 8080. Check the `core` log. |
| Deploy key: `Deploy keys are disabled for this repository` | the organization disables deploy keys. Enable `deploy_keys_enabled_for_repositories` in the organization settings. |
| GitHub App: redirect lands on a website | the Coolify instance URL is wrong. Set **Settings → Instance → Instance's URL** to the real dashboard URL. |
| `nc: connect to play.example.com (...) port 10300: Connection refused` from outside the LAN | nothing forwards the game. Coolify proxies 80/443 only; the host's firewall and the NAT in front of it must send 10300/tcp and 10400/tcp+udp to the host. |
| the client shows **press ESC** at the end of its loading | the client never reached the game. Check `launcher.json`'s address, then the two ports from outside the LAN. The feed and the Console can be green. |

## Known cosmetic details

- The Console's **Deploy log** can be empty: the plugin writes `state.json` and
  `applied/`, not the desk's `deploy/log.jsonl`. The Live release is the truth.
- `launcher.json`'s `shard` is the project name; the game server's name is
  `CAMELOT_SHARD_NAME`.
- `release.json`'s `base_url` is the publish placeholder; the live
  `patchlist-new.txt` is rewritten to `CAMELOT_FEED_URL`.

## Checklist

- [ ] `feed.example.com` and `play.example.com` resolve to the server.
- [ ] 80/443 reach Coolify's proxy; 10300/tcp and 10400/tcp+udp reach the game
      from outside the LAN (`nc -vz play.example.com 10300`).
- [ ] The deploy key is read-only on the project repository.
- [ ] `camelot-deploy` exists and is published signed.
- [ ] The compose application uses `deploy/install/compose.yaml`, base
      `deploy/install`, Compose strategy, and preserves the repository.
- [ ] The five `FILL` values are set; no `set CAMELOT_...` remains.
- [ ] `CAMELOT_DB_PASSWORD` is set before the first `db` start.
- [ ] Only `feed` has a domain, on port 8080.
- [ ] `curl https://feed.example.com/healthz` answers.
- [ ] The Console's Pull and Restart apply release 0001.
- [ ] The log says `Game WebAPI open for connections.`
