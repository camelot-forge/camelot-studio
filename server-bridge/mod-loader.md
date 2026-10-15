# Server installer — design

> Supersedes the 2026-10-05 mod-loader design (the reversal is dated
> 2026-10-14): the product is one server's content suite, so the Installer
> applies *one release* of *one server* — there are no third-party mods to
> arbitrate between, and no portability between servers to design for. The
> design being implemented is `workspace/notes/server-install.md`
> (phase P5); nothing here is built yet. The hook measurements this rests on
> are in `workspace/notes`.

## What this is

The server's half of a deploy: it turns a **release**'s server package into
rows in the server's own database, records what it did, and can reverse it.
Applied twice, it changes nothing the second time; reversed, the rows it
touched go back to the values they held.

It is a small C# script in the server's script folder, discovered through the
`IDatabaseUpdater` extension point (the `[DatabaseUpdate]` attribute) and run
once at boot, after the database is initialized and the scripts are compiled.

It is not a service, a daemon or a package manager: no socket, no port, no
database of its own, no dependency graph. One folder in, one state file out.

The old mod loader arbitrated ids between third-party mods; with one content
set that arbitration is unnecessary. Three of its ideas survive intact and
are adopted here:

- a package ships declarative rows, never SQL;
- the server owns the write;
- the applied set is recorded.

## The contract: a folder and `state.json`

Beside the server (not inside it), one folder the operator can back up:

```text
<server>/camelot/
  releases/0009/…            # the server packages, as built
  state.json                 # the applied releases, per table, with originals
  applied/0009/applied.json  # one line per row: written, skipped, or refused
```

`state.json` is the whole contract, and the other surfaces read it: Deploy's
dry run compares against it, the Console shows it. Per release and per table
it records the rows written, the values they held before (the reversal data),
and the ids allocated. Nothing else is stored and nothing is derived: the
file is the truth a later deploy, a dry run or a rollback reads.

## Declarative rows, never SQL

A package ships **rows** — a table, a key, column names and values — never
statements. The Installer generates the SQL itself, which is what lets it
record originals and keeps a package from executing arbitrary statements
against a database. No writer chooses an id either: the ids a release uses
are the ones the project allocated, and the release carries them.

## Content, and state: the ownership map

The database holds content *and* state. The Installer only ever writes
content, on a published map of what that means:

| Ours (content) | Theirs (state) — never written, never copied |
|---|---|
| `mob`, `worldobject`, `door`, `path` (spawns and world rows) | `character`, `inventory`, `house*`, `guild`, `account` |
| `gamedata` tables: items, monsters, spells, styles, loot, shops | sessions, chat, logs, statistics |
| the release's own files | anything written at runtime by players |

The map lives in the code as a table (`OWNED_TABLES`), is reported by
`camelot server tables`, and is what the Installer validates a release
against: a release that names a table outside the map is **refused by name**,
before anything is written. *Consequence, and it is a good one:* the local
test server is never a copy of production — it is a fresh baseline plus the
content, and an `expect` check is legitimate on our tables only.

## Applying a release

At boot the Installer applies the releases marked as *wanted* for its
environment, in order; on demand the Console asks it to apply or reverse one.
Both paths write the same records.

**Idempotent.** Applying release N twice changes nothing the second time —
the state says so, and the second run is a recorded no-op.

**Recorded.** Every row's previous values are written to `state.json` before
the row is touched, so a reversal is exact: the recorded values back for an
edit, the whole row for a removal. A new row's id goes to a tombstone when
the row is removed — never reused, not even after a rollback.

**Reversible.** Reversing release N writes back the originals it recorded and
marks the release reversed. In the pipeline, a rollback is a *forward
release* carrying earlier content, never a backwards movement.

**One transaction per release.** Half-applied is impossible by construction:
the rows of one release go in one transaction; if any statement fails, the
transaction is rolled back and the state file is not touched, and the log
says what failed and where.

**The id-space check.** The release carries the ids it allocated and the
id-space snapshot it allocated against. The Installer compares the snapshot
with the server's actual tables: equal → apply; grown but no collision →
apply and report; collision → **refuse, naming the id and the row that holds
it**.

**Offline-safe.** No network, no downloads: everything the Installer needs is
in `camelot/releases/<n>/`. A server that cannot reach the internet is still
updated by copying a folder.

## What it deliberately does not do

- **No client files.** A release's client half is applied on the clients:
  `camelot client install` on the operator's machines, the feed and a
  launcher on the players'. The server never touches a client.
- **No scripts, no schema.** The server compiles its script folder once,
  *before* the hook runs, and a schema change cannot be written by us at all.
  Both travel as **manual steps** attached to the release (`needs.manual`),
  each ticked in the deploy log; a release whose steps are unticked stays
  **visibly incomplete** in the log, in the Console and in `verify`.
- **No reload.** The running world reads its own rows once, at startup. The
  release *names* the ritual it needs (`needs.reload` — `doors`, `mob`,
  `object` — and `needs.restart`); a human performs it in game or the Console
  performs it, and the deploy log records the result.
- **No dependency resolution, no priorities.** One release of one server:
  there is nothing to arbitrate between and no ordering to negotiate.
- **No second writer.** No other part of the suite writes a server's tables:
  the Console asks the Installer, Deploy hands it a folder, and the Studio
  never talks to a server.

## The unmanaged fallback

A server that does not run the Installer can still be written directly: that
is `--db-write` today, and it remains, behind Deploy, as the documented
fallback. Same rows, fewer guarantees — no record on the server, no
idempotence, and the originals live on the operator's machine instead of in
`state.json`.

## The hook, and the hazard

`UpdateDatabase()` is reached once at boot, after the scripts are compiled —
the same moment the DOL XML packages already use. The measured hazard:
`if (!UpdateDatabase()) return false;` — an exception escaping the hook makes
the server **refuse to boot**. The Installer catches everything, records the
failure in its own records, and returns; one bad release must never stop a
server from starting.

## Status

Proposal, 2026-10-14; implemented by phase P5 of the vision. The C# is a
later piece, and its contract comes first: `core/tests/` carries a **fake
installer** that reads the same folder and writes the same `state.json`, so
the Studio side is testable without a server.
