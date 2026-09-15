# AGENTS.md — Camelot Studio

Guidelines for humans and AI agents working in this repository.

## Engine fidelity (the first rule)

Camelot Studio reproduces **what the DAoC client itself does** — same tables,
same order, same values. No approximations, and above all **no partial
fixes**:

- The engine overwrites node transforms wholesale: animation channels are
  applied **absolute, statics included**. Dropping "static" channels or
  rebasing a few bones onto our bind broke the pose chain (a dropped
  neck/hand rest bowed the head and sheared the gloves); the fix was to apply
  everything the way the client does.
- When the engine composites passes we don't have (base + decal / dark map),
  **bake the composite** instead of approximating with a single sheet.
- Wrap, tint multiply, sRGB… follow the engine's texture stage, not the
  convenient default.
- When the engine's behaviour is unclear, **stop and find it** (Ghidra on
  `game.dll`, the client tables, the emulator packets) — never guess. The
  proof corpus is `workspace/notes/character-rules.md`; the session
  ground rules ship as the `camelot-forge` skill at the workspace root.

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

## Code navigation (MCP)

Two MCP servers overlap on purpose; use each for its strength:

- **`codegraph` — discovery and impact.** One `codegraph_explore` call replaces
  a grep/glob/read loop: "where is X", "who calls X", routes, and the blast
  radius of a change. It is auto-synced, so trust it as current. Use it
  **first** to locate code across Python, TypeScript and Svelte.
- **`serena` — precise symbol work.** LSP-backed read and edit:
  `find_symbol` (exact body), `find_referencing_symbols` (authoritative
  references), `replace_symbol_body` / `insert_*_symbol`. Use it to edit at
  the symbol level instead of rewriting whole files, after codegraph told you
  where to look.
- When both could answer "find a symbol": locate with `codegraph`, then edit
  with `serena`.
- Keep raw grep/read for what neither can answer (binary research, docs,
  configs, `work/` scripts).

## Browser verification (web/)

- After a change that affects the running UI, verify it in a real browser
  instead of guessing: delegate to the `web-verifier` subagent
  (`Task` / `@web-verifier`). It reports console and network errors and
  captures screenshots.
- **Playwright is the default browser tool** (headless, deterministic, already
  a devDependency of `web/`). It drives the bundled Chromium headlessly, so no
  browser window pops up on the host and results are CI-reproducible.
- **Chrome DevTools MCP is reserved for real-user sessions** — inspecting a
  page in an authenticated/logged-in state (cookies, session, extensions) that
  is too costly to reproduce via Playwright `storageState`. Don't use it for
  routine UI checks: it opens a browser on the host, which is noisy and not
  headless by default.
- The subagent is read-only — it reports issues, the main agent fixes them.
  Do the browser pass yourself only when explicitly needed in-thread.

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

## Misc

- If you use a tool and have an issue with it, stop what you are doing and tell the user about it. We must have a perfect workflow and not fallback to another tool.
