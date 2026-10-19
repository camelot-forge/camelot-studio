# The `camelot-core` job (`remote.md` §D5, §D10): the Installer's step on a
# server, and the only container that opens the environment's database.
#
# It runs the same `camelot` CLI the desk runs — `env provision`, `env status`,
# `env apply`, `env verify`. It is one of the two ways to run the Installer on a
# server; the other is the C# plugin (`server-bridge/camelot-installer/`), which
# writes the same documents, so the Console's calls do not change between them
# (S §D10). This job is the one that also provisions a fresh machine.
FROM python:3.13-slim

WORKDIR /app
COPY core/camelot /app/camelot

# `git` and an ssh client: the job can also run the fallback pull
# (`project pull --remote …`) when the Console is down, which fetches the deploy
# branch (`remote.md` §D4).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

# The one extra Python dependency: the driver the server's own MySQL needs
# (`serverrows.connect`; the `mysql` extra of core/pyproject.toml).
RUN pip install --no-cache-dir "pymysql>=1.1"

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# The CLI itself: `docker compose run --rm camelot-core env apply production
# --dir /deploy`. With no argument it prints its own help and exits.
ENTRYPOINT ["python", "-m", "camelot.cli"]
CMD ["--help"]
