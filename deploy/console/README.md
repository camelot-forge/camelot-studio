# The Console

What is live on a running server, read from the Installer's own state, plus the
one action an admin needs: **Install
update** — pull the published release, restart the game server, and wait for
the Installer to record it. The page also shows who is connected, by name. The
underlying Pull, Restart and Reverse actions remain available in the page's
folds. Every attempt writes one line to an audit log; the environment folder is
read-only except for `server/camelot/`, where the handover and the wanted marker
land.

The channel was measured (M-3, 2026-10-18): OpenDAoC's API on `:5000`
(`atlas_api`) is read-only apart from `/utils/shutdown`, and the server has no
console stdin. The page names that boundary.

## Local

```sh
cp .env.example .env
# edit CAMELOT_SERVER_ENV (the client/ + server/ folder), the password, and the
# API values if you want the Restart action
docker compose up --build
```

Open `http://127.0.0.1:8770/` and authenticate as `operator` with the password
from `.env`. `/healthz` is unauthenticated for the container's probe;
`/api/live` returns the same document as JSON.

Without Docker, the same app runs from the checkout; the audit defaults to
`<root>/server/camelot/console/audit.jsonl`:

```sh
camelot console \
  --root /srv/forge/env --env production \
  --project /srv/forge/project --feed /srv/forge/project/feed \
  --game-server 127.0.0.1:10300 \
  --api-url http://127.0.0.1:5000 --api-password "$CAMELOT_API_PASSWORD" \
  --allow-restart
```

`--project` is what makes the reload readable: the desktop records it in the
project's `deploy/log.jsonl`. A server without that file gets `unknown`, not a
guess. `--feed` is the read side (the players' version); `--feed-out` is where
the pull mirrors the branch's feed. The server install points both at
`/feed-out`.

## Actions (7b)

`Restart` is off by default. With `--allow-restart` (or
`CAMELOT_CONSOLE_ALLOW_RESTART=1`), it needs three things:

- the game server's API: `--api-url` / `CAMELOT_CONSOLE_API_URL` and
  `--api-password` / `CAMELOT_CONSOLE_API_PASSWORD`;
- a writable audit log: `--audit` / `CAMELOT_CONSOLE_AUDIT` (the container
  uses `/audit/audit.jsonl`, mounted from `CAMELOT_CONSOLE_AUDIT_SOURCE`);
- the page's confirmation and its form token; a POST without either is
  refused, and the refusal is audited too.

The Console sends `GET /utils/shutdown/{api_password}`. **A supervisor must
bring the server back**: `Restart=always` in a systemd unit, a Docker
`restart: unless-stopped` policy, or equivalent. Without one, the action is a
stop. The page says this next to the button, and the audit line records who,
what, when, the target and the result.

Measured (M-3): `/utils/shutdown` is the only remote action OpenDAoC exposes.
`/reload`, messages, kicks and bans are in-game GM commands.

## Install update (the admin flow)

**Install update** is one button and one audit line. It runs three steps and
shows them as they happen, refreshing the page every 3 seconds:

1. **pull** — the same fetch, signature check, hash check, handover and feed
   mirror as below;
2. **restart** — `GET /utils/shutdown/{api_password}`; the supervisor (Docker's
   `restart: unless-stopped`, systemd, …) starts the game server again;
3. **apply** — the Console polls the Installer's `state.json` until the pulled
   release is recorded. A full boot takes tens of seconds; `UPDATE_TIMEOUT`
   (180 s) bounds the wait, and a release that is not recorded is reported, never
   called a success.

It requires `CAMELOT_CONSOLE_ALLOW_RESTART=1`, the API URL and password, the
deploy checkout, and a writable audit. Without them the button is disabled and
the page says which piece is missing. Connected players see the restart; the
page says so before the click.

The audit line is `action: update`, `result: updated` or `error`, with the
release number and the step that failed. A Console restart forgets the
in-progress state; the Installer's `state.json` remains the truth.

## Pull (7d)

The pull needs a **checkout of the `camelot-deploy` branch** on the server and
this machine's `machine.json` in it (naming the environment — a server-only
environment has `"client": false`, `D §D3`). Create it once:

```sh
ssh-keygen -t ed25519 -f /srv/forge/keys/deploy -N ''   # add the .pub as a
                                                        # read-only deploy key
GIT_SSH_COMMAND='ssh -i /srv/forge/keys/deploy' \
  git clone --branch camelot-deploy --single-branch git@github.com:org/shard.git \
  /srv/forge/deploy
```

Then, on each pull, the Console:

1. fetches and fast-forwards that checkout (read-only key; it never pushes);
2. reads `publish.json` and the newest release, and recomputes the sha256 of
the pack, the rows and the client patchlist — a mismatch is refused;
3. copies the **server half** into `<env>/server/camelot/releases/NNNN/` — the
client half stays in the feed;
4. writes `<env>/server/camelot/wanted.json` — the ask, for the Installer;
5. mirrors the pulled `feed/` into the directory the feed service serves
(files are added or replaced);
6. writes one audit line.

The pull **hands over and stops**; the Installer applies the rows (`S §D1`). On a
server that is `camelot env apply <env>`, or the C# plugin at boot; the Console's
page shows the wanted release as `pending` until the state file records it. The
server install bakes the plugin into `core`, so the admin flow is one **Install
update** click.

## Reverse (7e)

**Reverse** writes an ask, `<env>/server/camelot/reverse.json`, naming the newest
applied release; the Console holds no database handle (`S §D1`). The Installer's
step (`camelot env apply`, or the C# Installer at boot) honours it first, newest
first, then applies what is handed over, and clears the file.

The reversal repairs the server: the rows the release changed go back, and the
release leaves the state and the environment's folder (a pull is what puts it
back), while the feed moves forward, so a player whose launcher already took that
release's client half keeps it (`S §D17`). Use it in the hours after a bad apply;
anything the players have already seen is undone by a new release.

The button is off when nothing is applied, a release the ask names that is not
applied any more is skipped and said so, and every attempt writes one audit
line, refused or not. The page shows the ask as *asked* until the state stops
recording the release.

## Online

The same image and compose file. The compose service uses **host networking**
so the Console can reach the API on `127.0.0.1:5000` and the game port; put a
TLS reverse proxy in front of `127.0.0.1:8770` and change:

```dotenv
CAMELOT_SERVER_ENV=/srv/forge/env
CAMELOT_CONSOLE_ENV=production
CAMELOT_CONSOLE_PASSWORD=<a real secret>
CAMELOT_GAME_SERVER=127.0.0.1:10300
CAMELOT_CONSOLE_API_URL=http://127.0.0.1:5000
CAMELOT_API_PASSWORD=<the server's api_password>
CAMELOT_CONSOLE_ALLOW_RESTART=1
CAMELOT_DEPLOY_CHECKOUT=/srv/forge/deploy
CAMELOT_FEED_SOURCE=/srv/forge/feed
```

Host networking is Linux-native; on Docker Desktop, run the Console from the
checkout or bind OpenDAoC's API wider. The Console reads the environment and
writes two asks: the handover and the reversal. It holds no database handle.

## Tailnet

The Console can stay on `127.0.0.1` and still be reachable from a phone or
another machine: Tailscale serves it on the tailnet with a real certificate.

```sh
sudo tailscale serve --bg 8770
sudo tailscale serve status
```

The URL is `https://<machine>.<tailnet>.ts.net/`. Nothing changes in the
compose: the Console keeps its loopback bind, and `tailscaled` proxies to it on
the host. Only the tailnet reaches it; `tailscale funnel` is the public one and
is not what this is.

HTTPS certificates must be enabled for the tailnet (the admin console's **DNS**
page). Without them, `tailscale serve` fails and says so.

This is the exposure for a private or test shard: no tunnel to keep open, no
port on the host, TLS from Tailscale, and the operator password still in front.

## Player counts

`CAMELOT_CONSOLE_API_URL` and `CAMELOT_API_PASSWORD` also turn on the read half
of OpenDAoC's API: `/stats`, `/stats/uptime`, and the online client list behind
`/utils/query_clients`. The server property `atlas_api` must be true first.

## Security

Beyond loopback the Console requires the operator password. The action form
carries a per-process token, so a cross-site POST cannot trigger a restart,
and the audit write happens before the page answers. Use HTTPS in front; the
credential travels in a Basic header.
