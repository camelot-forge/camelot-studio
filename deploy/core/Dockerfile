# OpenDAoC's image, plus the Installer plugin. The base stays pinned by digest:
# a new OpenDAoC release is a new CAMELOT_CORE_IMAGE, then a rebuild of this
# image. The plugin is copied into the script folder the server compiles at
# boot (`server-install.md` §D10), so a Pull plus a Restart is the whole
# operator flow.
ARG CAMELOT_CORE_IMAGE=ghcr.io/opendaoc/opendaoc-core@sha256:89da37855ab3629273884652b646f3bc2a84398193926646d6721341a58d0d3e

FROM ${CAMELOT_CORE_IMAGE}

COPY server-bridge/camelot-installer/*.cs /app/scripts/camelot-installer/
