#!/usr/bin/env bash
#
# Deploy a Camelot pack onto an OpenDAoC server and prove the server took it.
#
# The server loads XML packages at startup and records the result in its
# AutoXMLUpdate table. This script copies the package where the server reads
# it, points the server at *this* deployment's database (the shipped example
# points at a port the repo's own compose does not publish), and then reads
# that table back, because an empty runtime directory looks perfectly healthy
# and the server logs nothing obvious either way.
#
# Usage:
#   server-bridge/deploy.sh --pack PACK.tar.gz [OPTIONS]
#
# It writes only to --work and, when a server root is given, that server's
# own package directory and config. It never touches the pack itself.
set -euo pipefail

PACK=""
WORK="${HOME}/.camelot-deploy"
SERVER_ROOT=""
DB_HOST="127.0.0.1"
DB_PORT="3307"
DB_NAME="opendaoc"
DB_USER="root"
DB_PASSWORD="my-secret-pw"
VERIFY_ONLY="no"
DB_CONTAINER="${CAMELOT_DB_CONTAINER:-opendaoc-db}"

die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }

# The database is often a container, and the host frequently has no mysql
# client at all -- which would otherwise read as "the server never loaded
# the package" and send someone chasing the wrong problem.
query() {
    local sql="$1"
    if command -v mariadb >/dev/null 2>&1; then
        mariadb --skip-column-names --batch -h "$DB_HOST" -P "$DB_PORT" \
            -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" -e "$sql" 2>/dev/null || true
    elif command -v mysql >/dev/null 2>&1; then
        mysql --skip-column-names --batch -h "$DB_HOST" -P "$DB_PORT" \
            -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" -e "$sql" 2>/dev/null || true
    elif docker inspect "$DB_CONTAINER" >/dev/null 2>&1; then
        docker exec "$DB_CONTAINER" mariadb --skip-column-names --batch \
            -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" -e "$sql" 2>/dev/null || true
    fi
}
note() { printf '  %s\n' "$*"; }

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'OPTIONS'

Options:
  --pack PATH          the .tar.gz to deploy (required)
  --work DIR           where the pack is unpacked (default: ~/.camelot-deploy)
  --server-root DIR    an OpenDAoC build output containing CoreServer
  --db-host HOST       database host      (default: 127.0.0.1)
  --db-port PORT       database port      (default: 3307)
  --db-name NAME       database name      (default: opendaoc)
  --db-user USER       database user      (default: root)
  --db-password PASS   database password  (default: my-secret-pw)
  --verify-only        do not start anything, only read the server's record
OPTIONS
}

while [ $# -gt 0 ]; do
    case "$1" in
        --pack) PACK="${2:?}"; shift 2 ;;
        --work) WORK="${2:?}"; shift 2 ;;
        --server-root) SERVER_ROOT="${2:?}"; shift 2 ;;
        --db-host) DB_HOST="${2:?}"; shift 2 ;;
        --db-port) DB_PORT="${2:?}"; shift 2 ;;
        --db-name) DB_NAME="${2:?}"; shift 2 ;;
        --db-user) DB_USER="${2:?}"; shift 2 ;;
        --db-password) DB_PASSWORD="${2:?}"; shift 2 ;;
        --verify-only) VERIFY_ONLY="yes"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

[ -n "$PACK" ] || { usage; exit 2; }
[ -f "$PACK" ] || die "no such pack: $PACK"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)" \
    || die "no python interpreter found"

# ---------------------------------------------------------------- unpacking

mkdir -p "$WORK"
STAGE="$WORK/pack"
rm -rf "$STAGE" "$WORK/tmp"
mkdir -p "$STAGE"

printf 'unpacking %s\n' "$PACK"
tar xzf "$PACK" -C "$STAGE"

[ -f "$STAGE/PACK.json" ] || die "not a Camelot pack: no PACK.json inside"
note "pack: $(grep -o '"name": "[^"]*"' "$STAGE/PACK.json" | head -1 | cut -d'"' -f4)"

# The XML packages are what this deployment actually changes on the server.
PACKAGES=()
while IFS= read -r file; do PACKAGES+=("$file"); done < <(
    find "$STAGE/targets" -name '*.xml' 2>/dev/null | sort
)
if [ "${#PACKAGES[@]}" -eq 0 ]; then
    note "WARNING: this pack ships no XML package; deploying it changes nothing"
fi

# ------------------------------------------------------------- server root

if [ -z "$SERVER_ROOT" ]; then
    for candidate in \
        "$HOME/OpenDAoC-Core/Release" \
        "$REPO_ROOT/../OpenDAoC-Core/Release" \
        ; do
        if [ -d "$candidate" ]; then SERVER_ROOT="$candidate"; break; fi
    done
fi
if [ -n "$SERVER_ROOT" ]; then
    [ -d "$SERVER_ROOT" ] || die "no such server root: $SERVER_ROOT"
    note "server root: $SERVER_ROOT"
fi

# ------------------------------------------------------------------ config

CONFIG="$WORK/serverconfig.xml"
PYTHONPATH="${REPO_ROOT}/core${PYTHONPATH:+:$PYTHONPATH}" \
"$PYTHON" - "$CONFIG" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DB_PASSWORD" <<'PY'
import sys

from camelot import deploy

path, host, port, name, user, password = sys.argv[1:7]
with open(path, "wb") as fh:
    fh.write(deploy.server_config_xml(
        db_host=host, db_port=int(port), db_name=name,
        db_user=user, db_password=password,
    ))
PY
note "wrote $CONFIG"

# ----------------------------------------------------------------- deploy

if [ -n "$SERVER_ROOT" ] && [ "$VERIFY_ONLY" = "no" ]; then
    INSERT="$SERVER_ROOT/scripts/dbupdater/insert"
    mkdir -p "$INSERT"
    for package in ${PACKAGES[@]+"${PACKAGES[@]}"}; do
        cp "$package" "$INSERT/"
        note "copied $(basename "$package") -> $INSERT"
    done
    cp "$CONFIG" "$SERVER_ROOT/config/serverconfig.xml"
    note "installed serverconfig.xml (database $DB_HOST:$DB_PORT)"
fi

# ----------------------------------------------------------------- verify

if [ "${#PACKAGES[@]}" -gt 0 ]; then
    printf '\nverifying the server took the packages\n'
    for package in ${PACKAGES[@]+"${PACKAGES[@]}"}; do
        name="insert/$(basename "$package")"
        hash="$(PYTHONPATH="${REPO_ROOT}/core${PYTHONPATH:+:$PYTHONPATH}" \
                 "$PYTHON" -c '
import sys
from camelot import deploy
print(deploy.sha256_file(sys.argv[1]))
' "$package" 2>/dev/null || true)"
        rows="$(query "SELECT FilePackage, LoadResult, FileHash FROM AutoXMLUpdate;")"
        if [ -z "$rows" ]; then
            note "$name: cannot read AutoXMLUpdate (no client, or the server never ran)"
            continue
        fi
        if printf '%s\n' "$rows" | grep -F "$name" | grep -q "SUCCESS"; then
            note "$name: LOADED"
        else
            note "$name: NOT LOADED -- check the server log for this file"
        fi
    done
fi

printf '\nnext: start the server and watch its log\n'
printf '  cd %s && ./CoreServer\n' "${SERVER_ROOT:-<server root>}"
printf 'the client needs the 1.127 build and connect.exe, never camelot.exe alone:\n'
printf '  connect.exe game1127.dll %s:%s <account> <password>\n' "$DB_HOST" "10300"
