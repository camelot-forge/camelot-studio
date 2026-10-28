# AGENTS.md

Rules for humans and AI agents working in this repository.

## Engine fidelity (the first rule)

Camelot Studio reproduces **what the DAoC client itself does** — same tables,
same order, same values. No approximations, and no partial fixes:

- The engine applies animation channels **absolute, statics included**. Dropping
  "static" channels or rebasing a few bones onto our bind broke the pose chain.
- When the engine composites passes we do not have (base + decal / dark map),
  **bake the composite** instead of approximating with one sheet.
- Wrap, tint multiply and sRGB follow the engine's texture stage, not the
  convenient default.
- When the engine's behaviour is unclear, **stop and measure it** (the client
  tables, the emulator packets, the binary) — never guess.

## Language and voice

- **Everything in this repository is written in English**: code comments,
  docstrings, READMEs, docs, commit messages, issue titles, CLI help text.
- Code identifiers are English.
- Questions, PR descriptions and reviews are in English.
- Documents and UI copy state one fact per sentence. If a sentence does not
  tell the reader something they can do or know afterwards, delete it.
- Do not use slogans, taglines, or claims about what the reader does not have to
  do.
- Describe the system as it is today. Git history carries what changed.

## Tests

- **Every feature ships with tests.** Write the test first when possible.
- Tests live in `core/tests/` (pytest) and `web/` (Vitest, `svelte-check`).
- The two tiers:
  - **Unit tests** need no game files.
  - **Corpus tests** read the real DAoC client through `$DAOC_CLIENT`; they skip
    automatically when it is unset.
- Before a pull request:

```sh
.venv/bin/python -m pytest core/tests -q
cd web && pnpm test && pnpm check && pnpm lint && pnpm build
```

## Code navigation

- Use the editor's language server to find symbols and references.
- Use `grep` for the questions a language server cannot answer: docs, configs,
  binary formats, generated files.
- Read the module's docstring before changing its contract.

## Browser checks

- A UI change is verified in a real browser, not guessed from the code.
- Playwright is the browser tool: it drives the bundled Chromium headlessly and
  is already a dev dependency of `web/`.
- The probes under `web/scripts/` are the contract. Run the relevant one against
  a scratch daemon; a screenshot is evidence, not a regression test.
- A probe edits its own scratch project. Never point one at a daemon serving a
  project you care about.

## Comments

- Comments explain intent, constraints or trade-offs.
- Docstrings describe the why and the contract, not the implementation.
- Do not restate the code.

## Structure

- Binary formats live only in `core/` (pyffi plus the camelot layers).
- `web/`, `addon/` and `server-bridge/` consume artifacts (glTF/JSON); they never
  parse NIF/MPK themselves.
- The layer contract is **glTF + JSON**.

## Documentation

- Keep the set small. The five entry points are:
  - `README.md` — the front page, and the index of everything below.
  - `docs/install.md` — installing the desk and the server.
  - `docs/admin.md` — the operator's runbook.
  - `docs/formats.md` — the DAoC format reference. Update it when a new format
    fact is established.
  - `docs/status.md` — project state, limits, and the evidence behind each claim.
- A fact has one home. Other documents link to it.
- A directory's own `README.md` owns what is specific to that directory.

## Research workflow (binary format discovery)

- Walk the file programmatically before reading a hexdump. Find block boundaries
  by scanning, then test layout hypotheses against the known block end.
- `pyffi`'s `nif.xml` is a reference, not the truth. The file bytes are
  authoritative.
- Keep long validations observable: print progress and errors as they happen.

## Legal

- Never commit game assets (files from the DAoC client, extracted CSVs, original
  NIF/DDS files). Tests read the client through `$DAOC_CLIENT`.
- The repository ships tools and documentation only.

## Working style

- Be direct. If a tool fails, say so and stop rather than silently falling back
  to another one.
- Do not repeat a proof you already have.
- Do not over-engineer: the smallest change that satisfies the contract wins.
