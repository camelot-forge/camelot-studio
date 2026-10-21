# The server install

One compose file, named volumes, and a bootstrap job. It installs OpenDAoC and
everything Camelot Forge attaches to it: the database, the feed, the Console,
and the Installer's job.

The desk stack (`deploy/compose.yaml`) keeps host paths for local testing. This
directory is the server's; a VPS needs no prepared directory tree.

## What must exist before the first run

- Docker with Compose v2.
- A **project repository** (private) with the `camelot-deploy` branch. Publish
  once from the desk before installing; the bootstrap clones that branch.
- A **read-only deploy key** on that project repository.
- The desk's **release public key**.
- A **feed URL** the players can reach, and the game's public address.
- Ports **10300/tcp** and **10400/tcp+udp** open and forwarded to the machine.
  The feed and the Console sit behind an HTTP proxy with TLS (80/443).

## 1. The deploy key

The key reads the **project** repository, never the studio repository.

```sh
ssh-keygen -t ed25519 -N '' -C camelot-deploy -f deploy_key
```

Add `deploy_key.pub` to the project repository: Settings → Deploy keys. Leave
write access off. With the GitHub CLI:

```sh
gh repo deploy-key add deploy_key.pub --repo your-org/your-project
```

The private key is what goes into `CAMELOT_DEPLOY_KEY_B64`, on one line:

```sh
base64 < deploy_key | tr -d '\n'
```

## 2. The release key

On the desk, if this shard has none yet:

```sh
ssh-keygen -t ed25519 -C camelot-release -f ~/daoc/keys/camelot-release
```

Name it in the desk project's `machine.json`:

```json
{ "release_key": "/home/you/daoc/keys/camelot-release" }
```

The **public** half goes into `CAMELOT_RELEASE_PUBLIC_KEY`; the private half
never leaves the desk. The Console refuses a release whose record it cannot
verify against that key.

## 3. The checkout and the values

```sh
git clone git@github.com:camelot-forge/camelot-studio.git
cd camelot-studio/deploy/install
./install.sh          # writes .env, generates the passwords, and exits
$EDITOR .env          # the five FILL values
./install.sh
```

`install.sh` generates the three passwords on its first run and exits. Fill the
five `FILL` values, then run it again:

```dotenv
CAMELOT_FEED_URL=https://feed.example/
CAMELOT_GAME_SERVER=play.example:10300
CAMELOT_DEPLOY_REPO=git@github.com:your-org/your-project.git
CAMELOT_DEPLOY_KEY_B64=<the base64 above>
CAMELOT_RELEASE_PUBLIC_KEY="ssh-ed25519 AAAA... you@desk"
```

The database name in `CAMELOT_DB_NAME` is the name the project's environment
uses on the server. The desk's project has its own; this machine's is the one
`env provision` creates.

## 4. Install

```sh
./install.sh
```

It runs `docker compose up -d` and waits for `camelot-bootstrap`. The bootstrap:

1. writes the deploy key, `known_hosts` and the ssh config;
2. writes `release.pub` into the keys volume;
3. clones OpenDAoC's complete database into the baseline volume;
4. clones the project's `camelot-deploy` branch and writes this machine's
   `machine.json`;
5. seeds the feed so `/version-new.txt` answers before the first Pull;
6. provisions the environment's database, once: it skips a database that already
   has `serverproperty`, so a restart never drops players' rows;
7. sets `atlas_api` and `api_password`, so the Console's API is on when the game
   server boots.

A bootstrap failure stops the dependent services and prints its log. The common
one is a missing `camelot-deploy` branch: publish from the desk, then rerun
`./install.sh`.

## 5. Coolify

Coolify runs the same file. Create a compose resource from the `camelot-studio`
repository, set the compose file to `deploy/install/compose.yaml`, and put the
values of `.env` in the resource's environment.

- Give the **feed** service a domain (`feed.example`), HTTPS, container port
  **8080**. Coolify's proxy reaches the container on the compose network.
- Leave **core** without a domain. It binds the game ports on the host.
- The **Console** stays on loopback. Reach it through a tunnel:
  `ssh -L 8770:127.0.0.1:8770 you@server`, then `http://127.0.0.1:8770/`.

Coolify can also build the images: the compose file carries their build
contexts, and the repository is the whole source.

## 6. The first release

The order is desk → branch → Console → Installer:

1. on the desk, publish at least one release:
   ```sh
   camelot project publish 1 --remote origin
   ```
2. open the Console. It listens on `127.0.0.1:8770`; use an SSH tunnel:
   ```sh
   ssh -L 8770:127.0.0.1:8770 you@server
   ```
   then `http://127.0.0.1:8770/`, authenticated as `operator` with
   `CAMELOT_CONSOLE_PASSWORD`;
3. **Pull**. It fetches the branch, verifies the signature and the hashes,
   copies the server half into the environment, writes `wanted.json`, and mirrors
   the feed;
4. restart the game server:
   ```sh
   docker compose --env-file .env -f compose.yaml restart core
   ```
   The Installer plugin applies the handed-over release at boot, before the world
   loads. The Console then shows the release applied.
5. the players' launchers read the feed URL and update the client.

The `camelot-core` job remains the fallback for a server without the plugin, or
for an apply without a restart:

```sh
docker compose --env-file .env -f compose.yaml run --rm camelot-core \
  env apply production --dir /deploy
```

## Ports

| Port | Where | What |
|---|---|---|
| 10300/tcp | public | the game |
| 10400/tcp+udp | public | the region |
| 80/443 | public | the feed, through the host's proxy |
| 8080 | internal | the feed container; the proxy reaches it on the compose network |
| 8770 | loopback | the Console; use the tunnel or a proxy on the host |
| 3307 | loopback | the database; never public |

The feed service listens on `8080` and declares it with `expose`; a proxy
(Caddy, Traefik, nginx, Coolify) on the same Docker network serves
`CAMELOT_FEED_URL` and adds TLS. A host proxy on a bare VPS needs a compose
override with a `ports` mapping, for example `127.0.0.1:8080:8080`. The Console
uses host networking to reach OpenDAoC's API on `127.0.0.1:5000`; a containerised
proxy cannot reach that loopback. Use the SSH tunnel for the Console, or set
`CAMELOT_CONSOLE_HOST=0.0.0.0` and give the proxy the host's Docker gateway as
its upstream.

## Backups

| What | How |
|---|---|
| the players and rows | `docker compose exec -T db sh -c 'exec mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" "$CAMELOT_DB_NAME"' > forge.sql` |
| applied releases and asks | the `forge-env` and `forge-camelot` volumes |
| the feed | the `forge-feed` volume (reproducible from the branch) |
| the deploy key | the `forge-ssh` volume (a secret; or regenerate it) |
| the release public key | the `forge-keys` volume |

A volume archive. `docker volume ls` shows the prefix the compose project chose:

```sh
docker run --rm -v camelot-install_forge-env:/data:ro -v "$PWD":/backup \
  alpine tar czf /backup/forge-env.tgz -C /data .
```

The project branch is reproducible from the desk's project; the database is not.

## Upgrading

**OpenDAoC** keeps its own cadence. Change `CAMELOT_CORE_IMAGE` to a new digest,
then:

```sh
docker compose --env-file .env -f compose.yaml up -d --build core
docker compose --env-file .env -f compose.yaml run --rm camelot-core \
  env verify production --dir /deploy
```

Do not run Watchtower on the game server. A release of OpenDAoC may change its
schema; `env verify` and `camelot server tables` are what tells you before the
players see it.

**Camelot Forge** is the checkout. Pull a new commit, rebuild the images,
and leave the volumes alone:

```sh
git pull
docker compose --env-file .env -f compose.yaml up -d --build
```

The content releases travel on `camelot-deploy`; Pull applies them. The two
cadences are independent.
