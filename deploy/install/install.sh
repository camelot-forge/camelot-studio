#!/bin/sh
# Install the server. The first run writes .env and generates the passwords;
# fill the five FILL values, then run it again.
set -eu

cd "$(dirname "$0")"

if ! docker compose version >/dev/null 2>&1; then
  echo "error: Docker Compose v2 is required" >&2
  exit 1
fi

generate() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

value_of() {
  value=$(sed -n "s/^$1=//p" .env | tail -1)
  value=${value#\"}
  value=${value%\"}
  value=${value#\'}
  value=${value%\'}
  printf '%s' "$value"
}

fresh=0
if [ ! -f .env ]; then
  cp .env.example .env
  fresh=1
fi

generated=0
for name in CAMELOT_DB_PASSWORD CAMELOT_CONSOLE_PASSWORD CAMELOT_API_PASSWORD; do
  if [ "$(value_of "$name")" = "change-me" ]; then
    value=$(generate)
    sed -i "s/^${name}=.*/${name}=${value}/" .env
    generated=1
  fi
done

if [ "$fresh" = "1" ] || [ "$generated" = "1" ]; then
  [ "$generated" = "0" ] || echo "wrote .env with generated passwords"
  echo "fill these five values, then run ./install.sh again:"
  echo "  CAMELOT_FEED_URL, CAMELOT_GAME_SERVER,"
  echo "  CAMELOT_DEPLOY_REPO, CAMELOT_DEPLOY_KEY_B64, CAMELOT_RELEASE_PUBLIC_KEY"
  exit 1
fi

missing=
for name in CAMELOT_FEED_URL CAMELOT_GAME_SERVER CAMELOT_DEPLOY_REPO \
            CAMELOT_DEPLOY_KEY_B64 CAMELOT_RELEASE_PUBLIC_KEY; do
  case "$(value_of "$name")" in
    ""|FILL|FILL*) missing="$missing $name" ;;
  esac
done
if [ -n "$missing" ]; then
  echo "error: fill these values in .env:$missing" >&2
  exit 1
fi

compose() {
  docker compose --env-file "$PWD/.env" -f "$PWD/compose.yaml" "$@"
}

compose up -d

id=$(compose ps -q camelot-bootstrap)
[ -n "$id" ] || { echo "error: camelot-bootstrap did not start" >&2; exit 1; }

while :; do
  status=$(docker inspect -f '{{.State.Status}}' "$id" 2>/dev/null || echo missing)
  [ "$status" = "exited" ] && break
  [ "$status" = "missing" ] && break
  sleep 2
done

code=$(docker inspect -f '{{.State.ExitCode}}' "$id" 2>/dev/null || echo 1)
if [ "$code" != "0" ]; then
  echo "error: the bootstrap failed (exit $code); its log follows" >&2
  compose logs --tail=80 camelot-bootstrap >&2
  exit "$code"
fi

echo "the stack is up"
feed=$(value_of CAMELOT_FEED_URL)
echo "  feed health:  ${feed%/}/healthz"
port=$(value_of CAMELOT_CONSOLE_PORT)
[ -n "$port" ] || port=8770
environment=$(value_of CAMELOT_ENVIRONMENT)
[ -n "$environment" ] || environment=production
echo "  console:      http://127.0.0.1:${port}/ (or an SSH tunnel)"
echo
echo "next: publish a release from the desk, then Pull in the Console and apply:"
echo "  docker compose --env-file .env -f compose.yaml run --rm camelot-core \\"
echo "    env apply ${environment} --dir /deploy"
