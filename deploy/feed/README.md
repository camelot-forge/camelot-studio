# The feed service

Serves a published feed and rewrites its two environment-specific values on the
way out. The feed directory stays
URL-agnostic: the same files work locally and online.

## Local

```sh
cp .env.example .env
# edit CAMELOT_FEED_SOURCE (the project's feed/), CAMELOT_GAME_SERVER,
# and CAMELOT_FEED_URL (http://127.0.0.1:8080/ for a direct local run)
docker compose up --build
```

Then:

```sh
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/patchlist-new.txt | head -1
```

A player's `shard.json` (or a development run's `CAMELOT_FEED_URL`) points at
`http://127.0.0.1:8080/`.

## Online

The same image and compose file; change the two values:

```dotenv
CAMELOT_FEED_URL=https://patch.example/
CAMELOT_GAME_SERVER=play.example:10300
```

Put a TLS reverse proxy in front (Caddy, Traefik, nginx) and map the public
`/` to the container's `:8080`. The feed service speaks HTTP and holds no
secrets.

In the server install (`deploy/install/`), the feed has no host `ports`
mapping: Coolify's proxy reaches the container on the compose network. A host
proxy needs a compose override with a `ports` mapping. Publishing `8080` on the
host is what produces `port is already allocated`.

## What it rewrites

- `patchlist-new.txt`: the first line becomes `CAMELOT_FEED_URL`;
- `launcher.json`: `feed` and `server` become `CAMELOT_FEED_URL` and
  `CAMELOT_GAME_SERVER`.

Everything else is served unchanged. A feed without `launcher.json` answers
404 for that file; the launcher then reports that the feed does not say where
the game is.
