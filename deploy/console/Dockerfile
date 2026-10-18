# The Console (`A §D2`): the read-only face of a running server, plus the
# actions the channel allows (restart, pull). Same image locally and online;
# the environment folder is mounted read-only except for `server/camelot/`,
# and the operator credential comes from the environment.
FROM python:3.13-slim

# git and an ssh client: the pull fetches the deploy branch (`remote.md` §D4).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY core/camelot /app/camelot

ENV PYTHONUNBUFFERED=1 \
    CAMELOT_CONSOLE_ROOT=/env \
    CAMELOT_CONSOLE_HOST=0.0.0.0 \
    CAMELOT_CONSOLE_PORT=8770

EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; port = os.environ.get('CAMELOT_CONSOLE_PORT', '8770'); urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3)"]

ENTRYPOINT ["python", "-m", "camelot.console"]
