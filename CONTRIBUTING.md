# Contributing

Thanks for helping. This repository is the tool suite, not a game server and not
a distribution of game assets.

## Prerequisites

- Python 3.11 or newer.
- Node 20 or newer, with `pnpm`, for the browser interface.
- A DAoC client only for the corpus tests. They skip when `DAOC_CLIENT` is unset.

## Set up

```sh
git clone https://github.com/camelot-forge/camelot-studio.git
cd camelot-studio
./bootstrap.sh
cd web && pnpm install
```

## Before a pull request

```sh
.venv/bin/python -m pytest core/tests -q
cd web && pnpm test && pnpm check && pnpm lint && pnpm build
```

`pnpm lint` must pass. It is the same command CI runs.

If your change touches the running interface, verify it in a browser. The
probes under `web/scripts/` are the contract; run one against a scratch daemon.

## Rules

- `AGENTS.md` is the detailed rulebook: engine fidelity, language, tests, and
  what may be committed.
- Write in English: code, comments, docs, commit messages, issues.
- One feature per pull request, with its tests.
- Update the document that owns a changed fact. Do not add a sixth document.
- Do not commit game assets (client files, extracted CSVs, original NIF/DDS).
- Do not commit secrets: `.env`, `machine.json`, keys, tokens.

## Commits

Use a conventional subject (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`), in
the imperative. The body says what changed and why. Keep one logical change per
commit.

## Security

Do not open a public issue for a vulnerability. Follow `SECURITY.md`.
