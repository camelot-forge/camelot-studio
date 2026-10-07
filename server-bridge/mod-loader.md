# Mod loader — design

> Status: proposal, 2026-10-05. Nothing here is built yet. Grounded in
> measurements recorded in
> `workspace/notes`.

## What this is

The piece that turns a pack from *data a human applies* into *a mod an
operator installs, updates and removes*. It is an OpenDAoC plugin: a C#
script in the server's script folder, discovered through the
`IDatabaseUpdater` extension point, running once at boot.

It is not a package manager with dependencies and a repository. It runs one
folder of mods, deterministically, at boot.

## The shape: the manager owns identity

**A mod never chooses where its rows live.** It declares *how many* ids it
needs per table, and the manager allocates them, records them, and writes
every row itself.

Three things follow, and they are the point of the design:

- **overlap is impossible by construction.** Not detected — impossible. Two
  mods cannot claim the same ids because neither of them picks ids at all.
  There is no conflict-resolution policy to design, and no range to entrust
  to a third party;
- **mods are portable between servers.** The same mod installed on two
  servers receives different ids, and neither server cares;
- **a mod author needs to know nothing about id spaces**, which is what an
  ecosystem of third-party authors requires.

## What it does not do

- **No client files.** A mod that brings a model or a texture needs NIF/MPAK
  writing, which does not exist yet, and two mods patching the same client
  file have no precedence rule.
- **No script mods.** A mod shipping `.cs` cannot be managed at runtime: the
  server compiles the script folder once, *before* the hook runs. Such a mod
  needs its files placed before boot, by an installer, not by this loader.
- **No dependency resolution.** A mod declares what it requires; a missing
  requirement skips that mod and records why. Ordering is by explicit
  priority, not by graph.

## The pack describes rows; it does not ship SQL

A pack carries declarative rows. The manager generates the SQL, which is
what makes allocation possible and what keeps a third-party mod from
executing arbitrary statements against the server's database.

```json
{
  "mod": {"id": "daoc.studio.spawns", "version": "0.2.1", "priority": 100},
  "requires": [],
  "allocations": [
    {"table": "npctemplate", "count": 906}
  ],
  "rows": [
    {
      "table": "npctemplate",
      "id": "spawn:albion_stone_minotaur",
      "values": {
        "Name": "Albion Stone Minotaur",
        "ClassType": "DOL.GS.GameNPC",
        "Model": 88,
        "Size": "50-58",
        "Level": "1-5"
      }
    }
  ]
}
```

- **`id`** is an optional logical name, local to the mod. A row that other
  rows reference needs one; a row nothing points at does not.
- **`values`** are column names and values. **No ids**: the row's key column
  is filled by the manager from the allocation.
- **references** to another row of the same mod use the logical name, and
  the manager substitutes the allocated id. References to *existing* game
  data (a client model id, a spell id) are literal and stay literal —
  only the mod's own new rows are allocated.

## What the manager has to know, and where it gets it

The manager needs a schema for each table it writes. It does not hard-code
one: it **reflects over the server's own `DataObject` types**, exactly as
`AutoXMLDatabaseUpdate` already does to guess a package's type. That yields
the column names and their types, so values can be written as parameters
rather than interpolated strings.

Two things reflection does **not** give, and which stay as small built-in
maps in the manager:

1. **the allocatable key column per table** — `npctemplate`'s `TemplateId`
   is not declared as a key (`DbNpcTemplate` declares none; see the issue on
   `AutoXMLDatabaseUpdate`), so reflection cannot find it;
2. **the reference checks used before uninstall** (below), which are
   per-table knowledge about what points at what.

Both maps grow only when support for a new table is added, and both live in
the loader rather than in a pack, so a mod cannot claim an id column or
assert that nothing references it.

## Provenance and allocation

One table, created by the loader if absent:

```sql
CREATE TABLE IF NOT EXISTS `camelot_mod` (
  `ModId`       VARCHAR(255) NOT NULL,
  `Version`     VARCHAR(64)  NOT NULL,
  `TableName`   VARCHAR(255) NOT NULL,
  `IdLow`       BIGINT       NOT NULL,
  `IdHigh`      BIGINT       NOT NULL,
  `AppliedAt`   DATETIME     NOT NULL,
  `Result`      VARCHAR(255) NOT NULL,
  PRIMARY KEY (`ModId`, `TableName`)
);
```

**Allocation is stable across updates.** A mod keeps the ids it was given
the first time, because in-game data may already point at them: an item a
player is wearing, a creature in a saved spawn. A mod's block is therefore
allocated with headroom, and an update that fits reuses it unchanged.

`Result` is `SUCCESS` or the failure text. A mod that fails is recorded and
retried on the next boot, not swept away, and an operator can see what went
wrong without reading the server log.

**No row-level provenance.** Recording every row would make the table as
large as the content and would still not survive an operator editing rows by
hand. The allocated block is the contract: the manager owns it, and removal
deletes exactly it.

## Uninstall refuses to destroy data in use

Removing a mod whose content is already referenced in game would leave
players pointing at rows that no longer exist — an item slot, a saved spawn,
a character. Before deleting anything, the manager runs the table's
reference checks and **refuses** if any of them finds a hit:

| table | check before uninstall |
|---|---|
| `npctemplate` | is any row of `mob` using a `TemplateId` in the block? |

A refusal is reported with the count and the referring table, and nothing is
deleted. Forcing the removal is deliberately **not** offered: an operator who
truly wants it can delete the rows with SQL and accept the consequences,
which is a decision made outside the tool rather than a flag that makes
destruction convenient.

## Lifecycle

One boot, decided by what is on disk versus what the provenance table says:

| on disk | provenance | action |
|---|---|---|
| absent | absent | nothing |
| present, same version | present | nothing |
| present, new version | present | **update** — rows regenerated, block reused |
| present | absent | **install** — allocate, generate, record |
| absent | present | **uninstall** — reference checks, then delete |

## Where it hooks, and the hazard

`UpdateDatabase()`, reached once at boot after `CompileScripts()` and
`Properties.InitProperties` and before the world managers. The mod folder is
a server property, defaulting to `scripts/mods`, read through
`ServerProperties` like every other path in the server.

**The hazard:** `if (!UpdateDatabase()) return false;` — an exception
escaping the loader makes the server **refuse to boot**. The loader catches
everything per mod, records the failure, and continues. One bad mod must
never stop a server from starting, which is also how `AutoXMLDatabaseUpdate`
behaves.

## Failure policy

| situation | behaviour |
|---|---|
| unreadable manifest | that mod is skipped and recorded; the boot continues |
| a required mod is absent | that mod is skipped and recorded |
| a row fails to write | the mod's transaction is rolled back; recorded as failed |
| an update would exceed the allocated block | refused, and recorded: the mod must be reinstalled |
| a reference check fails on uninstall | refused, and recorded |

Nothing is ever half-applied: each mod's rows go in one transaction.

## Open questions

- **the block size** — allocate the declared count, or round up to give
  headroom for updates? Rounding up costs nothing but space, and prevents a
  reinstall later.
- **client payloads** — does the pack carry them now, with the loader copying
  them, or does that wait for Phase 5 and a precedence rule?
- **mod folder layout** — one directory per mod, or the `.tar.gz` packs our
  builder emits, extracted by the loader on first sight?
