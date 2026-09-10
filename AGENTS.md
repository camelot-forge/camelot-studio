# AGENTS.md — Camelot Studio

Guidelines for humans and AI agents working in this repository.

## Language

- **Everything in this repository is written in English**: code comments,
  docstrings, READMEs, docs, commit messages, issue titles, CLI help text.
- Code identifiers (variable/function names) are English.
- Questions, PR descriptions and reviews are in English too.

## Testing (TDD by default)

- **Every new feature must ship with tests** — write the test first when
  possible (test-driven). No PR merges a feature without its tests passing.
- Tests live in `core/tests/` (pytest) and run with:
  ```sh
  .venv/bin/python -m pytest core/tests -q
  ```
- Two tiers of tests:
  - **Unit tests** — no game files needed (pure functions: matrices, axis
    conversion, header sniffing, JSON sidecar logic, path/env expansion…).
  - **Corpus tests** — validate against the real DAoC client; they are
    **skipped automatically** when `DAOC_CLIENT` is not set:
    ```sh
    export DAOC_CLIENT=/path/to/DAoC/client
    ```

## Comments

- Don't over-comment. Comments must explain **intent, constraints or
  trade-offs** — never restate the code.
- Docstrings describe the *why* and the contract, not the implementation.

## Struct

- Binary formats only live in `core/` (single source of truth: pyffi +
  camelot layers). `web/`, `addon/`, `server-bridge/` consume artifacts
  (glTF/JSON), never parse NIF/MPK themselves.

## Docs

- The docs are intentionally small:
  - `README.md` — front page (what/why/quick start), for users.
  - `docs/README.md` — index of the docs.
  - `docs/formats.md` — DAoC format reference; **update it when a new
    format fact is established**.
  - `docs/status.md` — project state, limits, next steps.
- Keep the doc set small: when a fact is settled, fold it into
  `formats.md`/`status.md` rather than adding a new file.

## Research workflow (binary format discovery)

- **Walker first, hexdump second**: `notes <file>` prints every
  block (offset + type + size) and stops at the failing one. Never hand-count
  byte offsets — find block boundaries programmatically (scan for
  `[u32 len 4..60][printable type]`), then **brute-force layout hypotheses
  against the known block end** before encoding the winner in `legacy.py`.
- **pyffi's nif.xml is a 90% reference; the file bytes are authoritative.**
  DAoC exporters drift from documented version ranges. When pyffi reads a
  file our reader does not, ask pyffi what it sees (it is a working oracle
  for KFA/10.x).
- Keep long validations observable: `camelot batch` prints progress and
  errors live; a silent long run looks hung.

## Legal

- Never commit game assets (files from `DAOC CLIENT/`, extracted CSVs,
  DDS/NIF originals). Tests must read the client through `$DAOC_CLIENT`,
  never embed it.
- The repo ships tools and docs only.