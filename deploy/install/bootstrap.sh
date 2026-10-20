#!/bin/sh
# The server's first run, idempotent:
#
#   deploy key -> baseline -> deploy checkout -> machine.json -> feed seed ->
#   database provision (once) -> atlas_api / api_password.
#
# Every step checks its own result, so a restart of the stack repeats nothing
# destructive. The database provision runs only while `serverproperty` is
# absent; an existing environment is never dropped.
set -eu

# A missing credential must fail, not wait on a terminal that is not there.
export GIT_TERMINAL_PROMPT=0

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

: "${CAMELOT_ENVIRONMENT:?set CAMELOT_ENVIRONMENT}"
: "${CAMELOT_DB_NAME:?set CAMELOT_DB_NAME}"
: "${CAMELOT_DB_PASSWORD:?set CAMELOT_DB_PASSWORD}"
: "${CAMELOT_DB_HOST:=db}"
: "${CAMELOT_DB_PORT:=3306}"
: "${CAMELOT_DEPLOY_REPO:?set CAMELOT_DEPLOY_REPO}"
: "${CAMELOT_DEPLOY_KEY_B64:?set CAMELOT_DEPLOY_KEY_B64}"
: "${CAMELOT_RELEASE_PUBLIC_KEY:?set CAMELOT_RELEASE_PUBLIC_KEY}"
: "${CAMELOT_BASELINE_REPO:=https://github.com/OpenDAoC/OpenDAoC-Database.git}"
: "${CAMELOT_FEED_URL:?set CAMELOT_FEED_URL}"
: "${CAMELOT_GAME_SERVER:?set CAMELOT_GAME_SERVER}"
: "${CAMELOT_API_PASSWORD:?set CAMELOT_API_PASSWORD}"

case "$CAMELOT_DEPLOY_REPO" in
  ssh://*@*/*)
    DEPLOY_HOST=${CAMELOT_DEPLOY_REPO#ssh://*@}
    DEPLOY_HOST=${DEPLOY_HOST%%/*}
    ;;
  *@*:*)
    DEPLOY_HOST=${CAMELOT_DEPLOY_REPO#*@}
    DEPLOY_HOST=${DEPLOY_HOST%%:*}
    ;;
  *)
    die "CAMELOT_DEPLOY_REPO must be an SSH URL (git@host:owner/repo.git)"
    ;;
esac

mkdir -p /root/.ssh /keys /deploy /baseline /feed /env/server/camelot
chmod 700 /root/.ssh

say "the deploy key"
printf '%s' "$CAMELOT_DEPLOY_KEY_B64" | base64 -d > /root/.ssh/id_ed25519
chmod 600 /root/.ssh/id_ed25519
ssh-keygen -y -P '' -f /root/.ssh/id_ed25519 >/dev/null 2>&1 \
  || die "CAMELOT_DEPLOY_KEY_B64 is not an unencrypted SSH private key"
ssh-keyscan -H "$DEPLOY_HOST" > /root/.ssh/known_hosts 2>/dev/null \
  || die "ssh-keyscan $DEPLOY_HOST failed: is the network up?"
cat > /root/.ssh/config <<EOF
Host $DEPLOY_HOST
  IdentityFile /root/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking yes
EOF
chmod 600 /root/.ssh/config

say "the release public key"
printf '%s\n' "$CAMELOT_RELEASE_PUBLIC_KEY" > /keys/release.pub
chmod 644 /keys/release.pub
ssh-keygen -l -f /keys/release.pub >/dev/null 2>&1 \
  || die "CAMELOT_RELEASE_PUBLIC_KEY is not an SSH public key"

if [ ! -f /baseline/.camelot ]; then
  say "cloning the baseline"
  rm -rf /tmp/opendaoc-db
  git clone --depth 1 "$CAMELOT_BASELINE_REPO" /tmp/opendaoc-db
  [ -d /tmp/opendaoc-db/opendaoc-db-core ] \
    || die "$CAMELOT_BASELINE_REPO carries no opendaoc-db-core"
  cp -a /tmp/opendaoc-db/opendaoc-db-core/. /baseline/
  rm -rf /tmp/opendaoc-db
  touch /baseline/.camelot
fi

if [ ! -d /deploy/.git ]; then
  say "cloning the deploy branch"
  git ls-remote --exit-code --heads "$CAMELOT_DEPLOY_REPO" camelot-deploy >/dev/null 2>&1 \
    || die "$CAMELOT_DEPLOY_REPO has no camelot-deploy branch: publish from the desk first"
  rm -rf /tmp/deploy
  git clone --branch camelot-deploy --single-branch "$CAMELOT_DEPLOY_REPO" /tmp/deploy
  cp -a /tmp/deploy/. /deploy/
  rm -rf /tmp/deploy
fi

say "this machine's machine.json"
CAMELOT_ENVIRONMENT="$CAMELOT_ENVIRONMENT" CAMELOT_DB_NAME="$CAMELOT_DB_NAME" \
python - <<'PY'
import json
import os

path = "/deploy/machine.json"
try:
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
except (OSError, json.JSONDecodeError):
    document = {}
if not isinstance(document, dict):
    document = {}
document.setdefault("author", "the server")
environments = document.get("environments")
if not isinstance(environments, dict):
    environments = {}
environments[os.environ["CAMELOT_ENVIRONMENT"]] = {
    "root": "/env",
    "client": False,
    "database": os.environ["CAMELOT_DB_NAME"],
    "baseline": "/baseline",
}
document["environments"] = environments
text = json.dumps(document, indent=1) + "\n"
try:
    with open(path, encoding="utf-8") as handle:
        current = handle.read()
except OSError:
    current = None
if text != current:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print("wrote /deploy/machine.json")
PY

if [ ! -f /feed/version-new.txt ] || [ ! -f /feed/patchlist-new.txt ] \
   || [ ! -f /feed/launcher.json ]; then
  say "seeding the feed"
  CAMELOT_FEED_URL="$CAMELOT_FEED_URL" CAMELOT_GAME_SERVER="$CAMELOT_GAME_SERVER" \
  python - <<'PY'
import json
import os

feed = os.environ["CAMELOT_FEED_URL"].rstrip("/") + "/"
with open("/feed/version-new.txt", "w", encoding="utf-8") as handle:
    handle.write("0\n")
with open("/feed/patchlist-new.txt", "w", encoding="utf-8") as handle:
    handle.write(feed + "\n")
with open("/feed/launcher.json", "w", encoding="utf-8") as handle:
    handle.write(json.dumps({"feed": feed, "server": os.environ["CAMELOT_GAME_SERVER"]}) + "\n")
PY
fi

say "the database"
PROVISIONED=0
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  PROVISIONED=$(python - <<'PY'
import os
import sys

import pymysql

try:
    conn = pymysql.connect(
        host=os.environ["CAMELOT_DB_HOST"],
        port=int(os.environ["CAMELOT_DB_PORT"]),
        user="root",
        password=os.environ["CAMELOT_DB_PASSWORD"],
        connect_timeout=5,
    )
except Exception:
    sys.exit(1)
name = os.environ["CAMELOT_DB_NAME"]
with conn.cursor() as cur:
    cur.execute("SHOW DATABASES LIKE %s", (name,))
    if cur.fetchone() is None:
        print(0)
    else:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name='serverproperty'",
            (name,),
        )
        print(1 if cur.fetchone()[0] else 0)
conn.close()
PY
) && break
  sleep 2
  [ "$attempt" -eq 10 ] && die "the database never became reachable"
done

if [ "$PROVISIONED" = "0" ]; then
  say "provisioning $CAMELOT_ENVIRONMENT"
  python -m camelot.cli env provision "$CAMELOT_ENVIRONMENT" --yes --dir /deploy
else
  say "the environment is already provisioned"
fi

say "the Console's API properties"
CAMELOT_DB_NAME="$CAMELOT_DB_NAME" CAMELOT_API_PASSWORD="$CAMELOT_API_PASSWORD" \
CAMELOT_DB_HOST="$CAMELOT_DB_HOST" CAMELOT_DB_PORT="$CAMELOT_DB_PORT" \
CAMELOT_DB_PASSWORD="$CAMELOT_DB_PASSWORD" \
python - <<'PY'
import os

import pymysql

conn = pymysql.connect(
    host=os.environ["CAMELOT_DB_HOST"],
    port=int(os.environ["CAMELOT_DB_PORT"]),
    user="root",
    password=os.environ["CAMELOT_DB_PASSWORD"],
    database=os.environ["CAMELOT_DB_NAME"],
    charset="utf8mb3",
)
sql = """
INSERT INTO `serverproperty`
  (`Category`, `Key`, `Description`, `DefaultValue`, `Value`, `LastTimeRowUpdated`, `ServerProperty_ID`)
VALUES
  ('atlas', 'atlas_api', 'Enables the API endpoints on the port :5000', 'False', 'True', NOW(), NULL),
  ('atlas', 'api_password', 'Set the password to access certain API commands as shutdown', '', %s, NOW(), NULL)
ON DUPLICATE KEY UPDATE `Value` = VALUES(`Value`)
"""
with conn.cursor() as cur:
    cur.execute(sql, (os.environ["CAMELOT_API_PASSWORD"],))
conn.commit()
conn.close()
PY

say "bootstrap complete"
