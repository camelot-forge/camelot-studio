# Mod loader — design

> Status: proposal, 2026-10-05. Nothing here is built yet. Decisions marked
> **[?]** need settling before coding; the rest is grounded in measurements
> recorded in `workspace/notes`.

## What this is

The piece that turns a pack from *data a human applies* into *a mod an
operator installs, updates and removes*. It is an OpenDAoC plugin: a C#
script in the server's script folder, discovered through the `IDatabaseUpdater`
extension point.

It does **not** replace the server's own configuration, and it is not a
package manager with dependencies and a repository. It runs one folder of
mods, deterministically, at boot.

## What it does not do

- **No client files.** A mod that brings a model or a texture needs NIF/MPAK
  writing, which does not exist yet. The loader can copy client payloads
  into place if a pack ships them, but nothing produces them today, and
  two mods patching the same client file have no precedence rule.
- **No script mods.** A mod that ships `.cs` (new NPC behaviour, a command)
  cannot be managed at runtime: the server compiles the script folder once,
  *before* `UpdateDatabase()` runs. Such a mod needs its files placed before
  boot, by an installer, not by this loader.
- **No dependency resolution.** A mod that requires another declares it; the
  loader reports a missing requirement as a failure and applies neither.
  Ordering is by explicit priority, not by graph.

## Lifecycle

One boot, three verbs, decided by what is on disk versus what the provenance
table says:

| on disk | provenance | action |
|---|---|---|
| absent | absent | nothing |
| present, same version | present | nothing |
| present, new version | present | **update** |
| present | absent | **install** |
| absent | present | **uninstall** |

Uninstall is the case the server has no mechanism for today, and the reason
provenance exists at all.

## The pack gains a deployment section

The loader must not understand fig3, `npctemplate` or any other table's
semantics. The pack declares its own scope; the loader executes it.

```json
{
  "mod": {"id": "daoc.studio.spawns", "version": "0.2.1", "priority": 100},
  "requires": [],
  "scope": [
    {"table": "npctemplate", "id_range": [100000000, 100000905]}
  ],
  "data": ["sql/npctemplate.sql"]
}
```

- **`table` + `id_range`** — the only thing the loader needs to know to
  remove this mod later. Removal is `DELETE FROM <table> WHERE <key> BETWEEN
  <lo> AND <hi>`; the key column is the table's primary-key column, declared
  in a small built-in map for the tables we support.
- **`data`** — SQL files, applied in order. SQL rather than the DOL XML
  package format, for three reasons: the XML format requires the mod author
  to know internal C# class names, it validates nothing, and it carries no
  scope. SQL is also what `OpenDAoC-Database` itself ships, so it is the
  project's own idiom.
- **`priority`** — load order between mods. Two mods conflicting is a
  **range overlap**, detected before anything is applied.

**[?] The key-column map.** Removal needs the column to delete on. Options:
put it in the pack (a mod could lie), or keep a small built-in map in the
loader for supported tables (safer, but the loader must know tables). The
second is preferable: the loader stays small if the map is small, and a mod
cannot remove rows outside what it declared.

## Provenance

One table, created by the loader if absent:

```sql
CREATE TABLE IF NOT EXISTS `camelot_mod` (
  `ModId`      VARCHAR(255) NOT NULL,
  `Version`    VARCHAR(64)  NOT NULL,
  `TableName`  VARCHAR(255) NOT NULL,
  `IdLow`      BIGINT       NOT NULL,
  `IdHigh`     BIGINT       NOT NULL,
  `AppliedAt`  DATETIME     NOT NULL,
  `Result`     VARCHAR(255) NOT NULL,
  PRIMARY KEY (`ModId`, `TableName`)
);
```

`Result` is `SUCCESS` or the failure text. A mod that fails to apply is
recorded, not swept away — the next boot retries it, and an operator can see
what went wrong without reading the server log.

**No row-level provenance.** Recording every inserted row would make the
table as large as the content and would still not survive an operator
editing the rows by hand. The id range is the contract: a mod owns a span,
and removal deletes exactly that span. It is coarser, and it is honest about
what it can guarantee.

## Conflict detection

Before applying anything:

1. every installed mod's ranges, plus every pending mod's ranges, are
   collected per table;
2. any two ranges on one table that overlap are a **hard failure** — nothing
   is applied, and the conflict is named in the log and in the provenance
   table.

This is the reason to reserve ranges rather than let mods write wherever.
An overlap is not always wrong (two mods might intentionally extend one
another), so **[?] should an overlap be a hard failure or a warning resolved
by `priority`?** Hard failure is safer and simpler; priority-based merging is
more flexible and much more work.

## Where it hooks, and the hazard

`UpdateDatabase()`, reached once at boot, after `CompileScripts()` and
`Properties.InitProperties` and before the world managers. The mod folder is
a server property, defaulting to `scripts/mods`, read through
`ServerProperties` like every other path in the server.

**The hazard:** `if (!UpdateDatabase()) return false;` — an exception
escaping the loader makes the server **refuse to boot**. The loader therefore
catches everything per mod, records the failure, and continues. One bad mod
must never stop a server from starting, which is also how
`AutoXMLDatabaseUpdate` behaves.

## Failure policy

| situation | behaviour |
|---|---|
| unreadable manifest | that mod is skipped and recorded; the boot continues |
| scope overlaps another mod | nothing is applied; the conflict is recorded |
| a required mod is absent | that mod is skipped and recorded |
| SQL fails partway | the mod's transaction is rolled back; recorded as failed |
| a data file is missing | the mod is skipped and recorded |

Nothing is ever half-applied: each mod's data goes in one transaction.

## Open questions

- **[?] key-column map** — built into the loader, or declared by the pack?
- **[?] overlap policy** — hard failure, or resolved by priority?
- **[?] client payloads** — does the pack carry them now, with the loader
  copying them, or does that wait for Phase 5 and a precedence rule?
- **[?] mod folder layout** — one directory per mod, or the `.tar.gz` packs
  our builder emits, extracted by the loader on first sight?
