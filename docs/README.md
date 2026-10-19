# Documentation

- **`formats.md`** — DAoC client format reference: data tables & lookup
  chain, NIF versions, figures/fig3 architecture, KFA channels, MPAK
  archives, the NetImmerse 4.x reader block layout, coverage & remaining
  cases, swap constraints, prototype-1 protocol.
- **`status.md`** — current project state: delivered CLI, architecture
  decisions, known limits, environment gotchas, next steps, cheat sheet.
- **`next-steps.md`** — documentation/code truth audit and roadmap toward a
  reproducible DAoC server content pack.
- **`install.md`** — installing the suite: the desk and the server, from two
  empty machines to a shard whose players update themselves. The way in, and
  the document to read before `admin.md`.
- **`admin.md`** — the operator's runbook: the desk, the server, the daily flow
  from an edit to a player's update, a new VPS, backups, and what each refusal
  means. Different audience from the three above: it describes what to *run*,
  not how the tool is built.

The root `README.md` is the front page (what/why/quick start); `AGENTS.md`
holds repo rules for humans and agents (language, testing, comments,
format ownership, legal).

## A note on `workspace/` paths

Some documents here cite paths under `workspace/` — the private
research workshop that holds the reverse-engineering proof corpus, the
audit reports and the working notes. **It is a separate repository and is
not distributed with this one**, so those paths are evidence pointers
rather than links: they name where a claim was established, and the claim
itself is stated in the text. Nothing here requires access to them.
