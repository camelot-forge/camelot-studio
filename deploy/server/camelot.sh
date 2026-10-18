#!/bin/sh
# The Installer's step, and the server's two API properties, from the host.
#
#   ./camelot.sh provision        build the environment's database from the baseline
#   ./camelot.sh apply            write the wanted release's rows — the Installer's step
#   ./camelot.sh reverse          put back what one release replaced (a repair, not a rollback)
#   ./camelot.sh status           what the environment holds
#   ./camelot.sh verify           read it back: the database and the folder
#   ./camelot.sh api <password>   set atlas_api and api_password in the server's table
#   ./camelot.sh restart          restart the game server (it reads its config at boot)
#   ./camelot.sh logs             follow the game server's output
#
# It drives the compose project: the whole stack's `deploy/compose.yaml` when this
# checkout has it, else this directory's own. `docker compose` or `podman compose`,
# whichever is installed.
#
# Every verb but `api`, `restart` and `logs` runs `camelot-core` against the
# environment named by CAMELOT_ENVIRONMENT — the stack's `.env`, or this shell's own.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")
file=${CAMELOT_COMPOSE_FILE:-}
if [ -z "$file" ]; then
	if [ -f "$root/compose.yaml" ]; then file="$root/compose.yaml"; else file="$here/compose.yaml"; fi
fi

compose() {
	if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
		docker compose -f "$file" "$@"
	elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
		podman compose -f "$file" "$@"
	else
		echo "no compose provider: install docker compose or podman compose" >&2
		exit 1
	fi
}

env_name() {
	# The environment's name: this shell's value, else the stack's `.env` — which
	# is read, never executed.
	if [ -n "${CAMELOT_ENVIRONMENT:-}" ]; then
		printf '%s' "$CAMELOT_ENVIRONMENT"
		return
	fi
	env_file=$(dirname "$file")/.env
	if [ -f "$env_file" ]; then
		sed -n 's/^CAMELOT_ENVIRONMENT=//p' "$env_file" | tail -1 | tr -d '"'
		return
	fi
	printf 'production'
}

usage() {
	sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
}

case "${1:-}" in
	provision)
		shift
		compose run --rm camelot-core env provision "$(env_name)" --yes --dir /deploy "$@"
		;;
	apply)
		shift
		compose run --rm camelot-core env apply "$(env_name)" --dir /deploy "$@"
		;;
	reverse)
		shift
		compose run --rm camelot-core env reverse "$(env_name)" --dir /deploy "$@"
		;;
	status)
		shift
		compose run --rm camelot-core env status "$(env_name)" --dir /deploy "$@"
		;;
	verify)
		shift
		compose run --rm camelot-core env verify "$(env_name)" --dir /deploy "$@"
		;;
	api)
		password=${2:-}
		if [ -z "$password" ]; then
			echo "usage: $0 api <api password>" >&2
			exit 2
		fi
		# A quote in the password would end the literal early.
		escaped=$(printf '%s' "$password" | sed "s/'/''/g")
		# The db service carries both the root password and the database name, so
		# nothing has to be parsed out of `.env` here.
		compose exec -T db sh -c 'exec mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$CAMELOT_DB_NAME"' <<SQL
INSERT INTO \`serverproperty\`
  (\`Category\`, \`Key\`, \`Description\`, \`DefaultValue\`, \`Value\`, \`LastTimeRowUpdated\`, \`ServerProperty_ID\`)
VALUES
  ('atlas', 'atlas_api', 'Enables the API endpoints on the port :5000', 'False', 'True', NOW(), NULL),
  ('atlas', 'api_password', 'Set the password to access certain API commands as shutdown', '', '$escaped', NOW(), NULL)
ON DUPLICATE KEY UPDATE \`Value\` = VALUES(\`Value\`);
SQL
		echo "atlas_api and api_password are set. The API host is started at boot only,"
		echo "so the server has to restart before it answers: $0 restart"
		;;
	restart)
		compose restart core
		;;
	logs)
		compose logs -f --tail=100 core
		;;
	*)
		usage
		exit 2
		;;
esac
