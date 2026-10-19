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
- Some MANDATORY elements when you write to me or when you write code/documents/UI:
  - No catchphrases
  - No bullshit AI made up phrases
  - the action and its result, in its own words
- Worked examples of that rule (what was wrong, what replaced it):
  `workspace/notes/ui-modernization.md` § Copy.

### One fact per sentence (mandatory, documents and UI copy)

Apply one test to every sentence: **what does the reader know, or what can they
do, after reading it that they could not before?** If the answer is nothing,
delete the sentence. Four patterns fail that test every time:

- **Slogans and scene-setting openers.** "From two empty machines to a shard
  whose players update themselves" is a tagline. Start with the first thing the
  reader has to do.
- **Negations that only deny.** "The desk never reaches the server, and the
  server never reaches the desk." "The Windows package is the players' launcher,
  not the studio." State what is: "The studio runs on Linux and macOS." A
  negation earns its place only when the reader would otherwise act on the wrong
  assumption, and that mistake costs them something.
- **The project's history.** Documents describe the system as it is today: not
  what it used to do, not what it stopped asking for, not which behaviour
  changed. Git history, `workspace/blog.md` and the specs carry that.
- **What the reader does not have to do.** "The launcher never asks for a URL"
  tells them nothing; if the tool does not ask, there is nothing to say. Say what
  it asks for, or say nothing at all.

This covers `README.md`, `docs/`, the per-directory READMEs, CLI help text and
the web UI's copy alike.

## Testing (TDD by default)

- **Every new feature must ship with tests** — write the test first when
  possible (test-driven). No PR merges a feature without its tests passing.
- Tests live in `core/tests/` (pytest) and `web/` (Vitest/Svelte checks).
  Run the relevant suites with:
  ```sh
  .venv/bin/python -m pytest core/tests -q
  cd web && pnpm test && pnpm check && pnpm lint && pnpm build
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

### The Playwright MCP (the agent drives a browser in-thread)

`browser_navigate` / `browser_snapshot` / `browser_click` / `browser_type` /
`browser_evaluate` / `browser_console_messages` / `browser_network_requests`
are in the agent's own tool list: a real browser, no subagent, no screenshot
round-trip. Use it to *read* the running app — a label, the status bar, the
console after a click, a question the probes do not assert. The probes stay
the contract: they are versioned, deterministic and run per slice; an MCP
session is not a regression net.

Verified working 2026-10-16, on `/world` and `/` (a figure loaded from the
library) against the operator's daemon: 0 page errors; the page loads and the
inspector's cards read as they should. The one console error seen — a 404 on
`/api/v1/assets/<id>/anim` — is *expected*: a figure with no animation answers
404 and the card says so. Snapshots and console logs land in `.playwright-mcp/`
at the **workspace root** — outside both repositories, so nothing to ignore.

Setup on this host (Zed `context_servers.playwright`):

```json
"args": [
  "@playwright/mcp@latest", "--headless", "--isolated",
  "--executable-path", "/home/you/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
]
```

The path goes in **unquoted**: wrapped in quotes, Playwright looks for a file
whose name contains them and reports `executable doesn't exist at
"…/chrome"` — the failure mode to recognise before doubting the binary.

Without `--executable-path` the server asks for the **Chrome channel** and
fails to start ("Chromium distribution 'chrome' is not found").
`npx playwright install chrome` is no way out on this host: it switches to root
and refuses Pika (`ERROR: cannot install on pika distribution`). The MCP pins
its **own alpha Playwright** (`@playwright/mcp@0.0.83` → `1.64.0-alpha-…`),
whose Chromium revision is not the one `web/` installed (`1.63.0` →
`chromium-1243`) — which is why the explicit `--executable-path` is the
combination that works, and why it must be re-checked after an MCP upgrade
(`ls ~/.cache/ms-playwright`). Fallback if that build ever mismatches over the
protocol: `npx playwright install chromium` (a download, no apt) and a
`--config` file with `{"browser": {"browserName": "chromium"}}`.

Caveats:

- **WebGL.** Verified 2026-10-16: the canvas renders and the app reads it
  back (GL `GPU stall due to ReadPixels` warnings, 0 console errors). The
  probes' `--use-gl=swiftshader --enable-unsafe-swiftshader` stays what makes
  *them* deterministic on a GPU-less runner; it is not a limitation of the MCP
  on this host.
- **Never write into the operator's daemon.** Port 8760 is their session on
  `/tmp/ux-session` (`notes` in the workspace; the product's own
  default port): read there, never edit. Anything that edits gets its own
  scratch daemon (`--project /tmp/<name>`, port 8761+ so it never takes the
  session's) — the rule the probes already follow.
- The app is served from `web/build`: `pnpm build`, then reload. The running
  daemon serves the new files without a restart (static assets are read per
  request).

## Comments

- Don't over-comment. Comments must explain **intent, constraints or
  trade-offs**.
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
- Don't overthink things, don't be zealous, be direct when you think and talk.
- If you already proved something, don't do it twice and don't lose time.
