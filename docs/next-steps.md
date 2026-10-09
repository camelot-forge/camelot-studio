# Next steps: Server Content Pack Pipeline

This document defines the next work after the current Studio viewer and
character-builder milestones. The documentation/code truth audit is closed;
unresolved findings are now an explicit implementation backlog rather than
hidden assumptions. No server-facing contract should be designed from stale or
unverified claims.

## Goal

Produce a reproducible content pipeline for DAoC server development:

```text
DAoC client corpus
        |
        v
Camelot core
  - binary readers and writers
  - gamedata resolution
  - conversion and validation
        |
        v
versioned manifests + artifacts + reports
        |
        v
OpenDAoC adapter
        |
        v
real DAoC client verification
```

The canonical pack is server-neutral: it must not depend on either server's
database tables, script directories or C# types. The server must consume
stable IDs, manifests, glTF/JSON artifacts and reports. It must not duplicate
NIF, KFA, MPAK or fig3 parsing in C#.

## Evidence policy

The audit must distinguish evidence from inference. The preferred order is:

1. Reproducible observation in the real DAoC client or a real browser session.
2. Executable code and automated tests in `camelot-studio`.
3. Client tables, binary corpus evidence, emulator behavior and Ghidra findings.
4. Prose documents in `camelot-studio` and `workspace`.

A user observation is a valuable reproduction case, but it does not by itself
prove the engine's complete algorithm. When an observation conflicts with a
spec, retain the observation, inspect the implementation and client tables, and
record the result as confirmed, disproved or unresolved.

## Server neutrality and package boundary

**OpenDAoC is the target platform**, and the only one. It is not part of the
canonical Studio schema -- the contract stays neutral so the pack is portable
-- but there is one projection, one adapter and one deployment path to build
and keep working:

- a neutral logical model, appearance, slot, animation and dependency model;
- a client profile describing the supported DAoC build and asset mappings;
- an OpenDAoC projection mapping logical ids onto its own tables and wire
  format;
- an OpenDAoC plugin implementing the install/update/uninstall lifecycle;
- target-specific ID maps, range checks and compatibility metadata.

The projections may share most of their structure, but they must not be assumed
to produce identical database or script files. A pack can therefore be
server-agnostic while still requiring a small adapter for each supported
server. Ordinary asset deployment should use data, generated XML and optional
script plugins; a server-core change is reserved for genuinely new protocol or
runtime capabilities.

### Impact of an asset change

| Change | Client side | Server side |
|---|---|---|
| Replace an existing asset while preserving its numeric ID | Patched client data/pack; no client executable change | Usually no core change |
| Add an asset mapped to a compatible existing ID or slot | Client resources/tables must expose it | Data projection, ID mapping or script/plugin may be needed; core usually unchanged |
| Add a model, appearance or animation requiring a new ID | Client resources/tables must know the ID; client code may be required for new protocol fields | Adapter/plugin or packet-library work is required |
| Add gameplay, a new creation screen or a new wire behavior | Usually client and server work | Core, plugin and protocol work are likely |

A stock client cannot render an unknown asset merely because a server sends a
new ID. The client distribution and the server projection must be released as
one compatible content unit, with an explicit fallback or rejection policy for
unpatched clients.

### Portable pack layout

The first pack format should be declarative and data-first, not an arbitrary
C# plugin. A proposed layout is:

```text
content-pack/
  pack.json
  manifest/content.json
  manifest/appearance.json
  manifest/animations.json
  profiles/client-<build>.json
  targets/opendaoc/...
  client/patch-manifest.json
  checksums.lock
```

`pack.json` should carry a schema version, pack ID/version, client profile,
dependencies, entry points, target support and artifact hashes. The canonical
manifests use namespaced logical IDs; generated target files map those IDs to
OpenDAoC values. The runtime plugin is the mod loader of the section under
Phase 4, not a collection of small adapters.

Visual regression evidence uses both a structured record and a screenshot when
available. The record includes race, gender, selected options, expected and
actual result, client/browser build, pack version, relevant IDs and artifact
hashes.

## OpenDAoC first adapter

OpenDAoC is the first server target. The canonical Studio pack remains
server-neutral; this only prioritizes the first projection, validation loop and
deployment documentation.

The official documentation was last updated on 2026-07-22. Its current
integration-relevant statements are:

- the server accepts clients running `game.dll` 1.127;
- the recommended database is MariaDB, tested up to 10.6;
- the Docker deployment uses a MariaDB 10.6 service, a dummy database volume
  and the `ghcr.io/opendaoc/opendaoc-core:latest` image;
- the API is an operational read API on port 5000, not a content-ingestion or
  character-appearance API;
- the full database is maintained separately in `OpenDAoC-Database` and is
  described as a 1.65 baseline.

Sources: [client](https://www.opendaoc.com/docs/client), [database](https://www.opendaoc.com/docs/database),
[Docker setup](https://www.opendaoc.com/docs/core/docker-setup/), and
[API](https://www.opendaoc.com/docs/core/api/).

The local OpenDAoC checkout does not perfectly match that published guide. The
audit must treat the source tree as authoritative for the chosen commit and
record these differences rather than silently copying the guide:

- the projects target .NET 10 locally, while the manual page still says .NET 8;
- the current entry project and configuration are `CoreServer`, while the
  manual page still refers to `DOLServer`;
- the checkout does not contain the `DummyDB.sql` file referenced by the guide;
- `CoreServer.csproj` requires a local `CoreServer/config/serverconfig.xml`,
  which is absent from this checkout and must be created from the example
  before a local build;
- the current configuration uses game port `10300` and region port `10400`,
  while the manual page still says `10301`;
- script compilation and XML package deployment need explicit release-artifact
  verification; an empty runtime script directory can otherwise look healthy.

The first OpenDAoC adapter acceptance gate is therefore:

1. pin the `DAOC CLIENT V2` profile, exact client build and relevant hashes;
   **measured 2026-09-24: the corpus is client build `1.130`**
   (`game.dll` `VS_VERSION_INFO`), while the OpenDAoC page states the server
   accepts `1.127`. This gap must be resolved — by pinning a 1.127 corpus or
   by confirming 1.130 compatibility — before any acceptance test passes;
2. pin an OpenDAoC source commit, a database schema/commit and an image tag;
3. choose and document whether the server data profile is the official 1.65
   database, a later client profile, or a tested compatibility overlay;
4. verify the Release/Docker artifact contains the required runtime XML and
   script assets;
5. validate ID ranges, model bit packing, character persistence and client
   login/creation before adding new asset behavior;
6. use a pinned image and reproducible deployment metadata rather than
   `latest` for acceptance tests.

**Gate status 2026-09-30 — the server side passes, the client side cannot
run here.** Measured, in `workspace/notes`:

- the server builds, starts and serves: 0 build errors, MariaDB with 110
  tables and 34 545 `itemtemplate` rows, listening on 10300 with real
  script compilation. The missing `serverconfig.xml` blocker is cleared;
- three documentation corrections: the data path is **SQL/MariaDB, not
  XML** (the audit's XML-package concern is obsolete), the compose file
  publishes **no port for the database** (a host-run server fails on every
  query while the config looks right), and the project targets **1.65**
  data, not the current client;
- the documented version gate is a red herring: `client_version_min/max`
  are both `-1` ("any") and never fire. The real gate is the packet-lib
  registry, matched by **exact equality**, topping out at 1129 while a
  1.130 client would send 1130;
- **a retail client cannot connect directly.** It must go through
  `connect.exe`, a memory patcher .NET that the OpenDAoC client ships;
  pointing `login.dat` at port 10300 deadlocks, because that port is a
  *game* port and the retail flow expects a *login server*.

The *appearance* test remains **not executed**, and no client-side return is
assumed anywhere in the pipeline. The projection data it would have confirmed
is already extracted and testable without a client: the server's `race` ids
(1..18 playable) and `itemtemplate.Model`/`Item_Type`, with a two-entry
name-normalisation table (`Norse` -> `Norseman`, `Half Ogre` -> `HalfOgre`).

The *client acceptance* ladder — a different question from the appearance
projection — **was executed** (2026-10-07): a rewritten MPAK container, a
modified table and a new model id all reach the world, and the mod loader's
server half deploys the matching row. Records:
`workspace/notes/`.

The official 1.127 client statement and 1.65 database baseline are not, by
themselves, proof that every asset and appearance mapping is compatible. That
compatibility is an explicit audit result, not an assumption.

## Phase 0 — Documentation and code truth audit (closed)

Before extending the server integration, reconcile all claims across both
repositories.

### Scope

- Inventory every Markdown document and research note.
- Classify each claim as delivered, partial, prototype, historical, stale or
  unresolved.
- Map important claims to current code, tests, CLI commands, API endpoints and
  user-visible behavior.
- Identify duplicate or contradictory metrics, milestones and terminology.
- Preserve historical research, but label it clearly and remove it from the
  current-state path.

### Character-builder audit

The builder receives a focused pass because visual behavior is the current
risk area. Verify every relevant control against the real browser and, where
possible, the real client:

- all races currently exposed by the 15-entry builder and both genders;
- skin tone, including the actual tint/color map values;
- face selection and the fig3 texture assembly;
- hair style and hair color, including the `S/T/M` color-map behavior;
- eye color and eye-sheet composition;
- hair decals and the base/decal compositing order;
- body, cloak, armor, gloves, legs and boots material/tint behavior;
- model alignment, scale, attachment, bind pose and animation;
- every supported animation state and every deliberately unsupported case.

The audit found that the current documentation's claims about skin tones,
hair, `pskins`, hair decals and empty `S` cells require explicit reproduction
before being treated as complete.

### Deliverables

- A claim matrix with: claim, document, code symbol or endpoint, test, user
  observation, evidence level and action.
- A short list of documentation corrections, preserving links to the evidence.
- A list of real-client/browser reproduction cases, including expected versus
  actual results, structured fields and screenshots where available.
- A canonical, versioned statement of supported and unsupported behavior in
  `docs/status.md` and `docs/formats.md`.
- No implementation of the server pipeline should depend on an unresolved
  claim.

### Exit criteria

- Contradictory current-state claims are removed, corrected or explicitly
  marked as unresolved.
- Every supported builder and viewer behavior is backed by code plus a test
  or a documented manual verification.
- Visual observations are reproducible and include the selected race, gender,
  options, browser/client build and relevant output.
- User-facing documentation agrees with the implementation and with the
  canonical client corpus.

## Audit closure

The first audit pass is closed. The private evidence report is
`workspace/notes`; it records the
259/263 V2 Python baseline, the 29-test web baseline, the tested browser
controls, the four V2 test failures and the OpenDAoC build/configuration
blocker. Exhaustive visual coverage and implementation fixes are carried by
the phases below.

## Phase 1 — Close current Studio fidelity gaps

After Phase 0, implement only the gaps confirmed by the audit:

- complete fig3 skin-tone and hair-color handling without heuristic fallbacks;
- reproduce the engine's hair/decal and armor material compositing;
- close unresolved animation and texture-coverage cases;
- validate the 36 race/gender builder rows (18 races × 2) in a real browser;
  the three traditional fig3 rows shipped 2026-09-23, the per-piece hair
  sidedness flag 2026-09-24;
- document intentional refusals and fallback policy.

Every change must ship with tests and, for visual behavior, a reproducible
manual verification case.

**Status: closed 2026-09-24.** The skin-tone/hair-colour tints, the hair `M`
decal bake, the positional `Bald` slots, the per-piece Double Sided flag, the
`pskins` armour materials and the named animation states all shipped with
tests; the 36 rows are covered by a corpus test and a Playwright pass
(`web/scripts/armour_check.mjs`). Remaining items are the item-level
**extension** and **dye**, which are server notions and moved to Phase 2.
The backlog detail is in the private workshop's `builder-fidelity-gaps.md`.

## Phase 2 — Define the server-facing content contract (first cut done)

Create a versioned manifest schema for the server-facing pipeline. It should
describe, without embedding proprietary client binaries:

- figure and asset IDs;
- model and animation references;
- race/gender and character appearance fields;
- slot names, visual variants and extension values;
- gamedata lookup keys and fallback rules;
- required assets, textures and state mappings;
- source client version, schema version and generation metadata;
- validation status and unresolved references.

Each server target should consume this contract through a small adapter. The
adapters must not reimplement client table or fig3 semantics.

**Delivered (2026-09-24) as `core/camelot/contract.py`, schema `1.0.0`.** The
manifest covers the fields above except the per-item extension and the dye,
which cannot exist yet: both are server concepts (the dye table is not in
the client at all). Covered today: logical ids, figure/rig ids, appearance
slots (piece, sheet, tint, sidedness) with their `gamedata` keys, animation
states keyed by animnifs id with `loop`/`clamp` and the contributing anim
set, the client build read from `game.dll`, the schema version, and the
validation status with its unresolved references.

**Findings that change the roadmap:**

- the V2 corpus reports **client build `1.130`**, read from `game.dll`'s
  `VS_VERSION_INFO` — the OpenDAoC docs state the server accepts `game.dll`
  1.127. That gap is now a recorded fact in every manifest instead of an
  assumption, and it is the first item of the OpenDAoC acceptance gate;
- an `anims.csv` set is **not a clean per-figure namespace**: the Cata Troll
  male (set 263, 182 slots) borrows cat clips for slots like `Back`
  (`Cat_WB_HM.kfa`). The manifest records the contributing set per state so
  an adapter can reason about the rig a clip was authored on;
- a null tint is meaningful, not missing data: skin tone 1 and hair colour 1
  carry no tint (the client paints them white).

**Still open in Phase 2**: the target projections and the adapter interfaces.
The schema is stable enough to write an OpenDAoC projection against.

## Phase 3 — Build the server content pack (manifest + reports done)

Implement an offline packaging command or module that:

1. reads the client corpus through `$DAOC_CLIENT`;
2. resolves the required content and appearance references;
3. produces manifests, glTF/JSON artifacts and validation reports;
4. assembles a deterministic candidate patch/pack;
5. rejects missing, ambiguous or unverified references;
6. records client-version compatibility and a reproducible build identifier.

The initial target is a locally generated content pack, not a hosted service.

**Delivered (2026-09-24)**: `camelot contract --out DIR` does steps 1, 2, 3
(manifests + reports), 5 and 6 — it reads `$DAOC_CLIENT`, resolves the
references, writes `manifest/content.json` + `reports/validation.json` +
`checksums.lock`, refuses to publish on an unresolved reference, and records
the client build. Against V2: 36 figures, 324 appearance slots, 5958
animation states, 0 unresolved, ~4.5 s, byte-identical on rebuild.

**Delivered (2026-10-06)**: the pack carries a **deployment document**
instead of an XML data package -- declarative rows plus an allocation
request, designed in `server-bridge/mod-loader.md`. A mod declares how many
ids it needs and describes its rows; the manager allocates, records and
writes. No mod chooses an id, so overlap is impossible rather than
detected, and no mod ships SQL, so a third-party mod cannot execute what it
likes against a database.

**Delivered (2026-10-07)**: the loader
(`server-bridge/camelot-mod-loader/`) consumes the document on a booted
server — allocation, row writes and the provenance table verified in real
boots, including an end-to-end probe whose pack row spawns a client model id
that ships in no archive.

**Now delivered** (2026-10-08): the client half. A pack's `client` items
(loose file, archive entry, gamedata row edit) travel inside the pack, and
`camelot client install` applies them to a client directory with claims
resolved by priority, every touched file rebuilt from the client's own
originals; `camelot mod build` assembles the pack from an authoring
directory. The design, and the in-client ladder that still closes it, are
`notes/phase5-client-assets.md` in the workspace.

## Phase 4 — Validate the OpenDAoC integration

Prove the complete path against OpenDAoC:

```text
Studio pack -> target adapter -> server state -> client appearance
```

Test character creation, persistence, equipment, visual variants, animation
selection, spawn and reload. Use the emulator and the real client as
acceptance environments. Add a bridge or daochook integration only after the
manifest-only path is understood and reproducible.

### The target is a mod ecosystem, not a one-off pack (2026-10-05)

The intended product is not a single pack for one server. It is a format many
authors publish against, so a server operator can add, update and remove mods
at will — a restart at most, never a reinstall.

That is a different design from what exists today, and the difference is worth
naming precisely:

| | today | a mod ecosystem needs |
|---|---|---|
| what it does | adds and replaces rows | adds, updates and **removes** |
| provenance | none: `AutoXMLUpdate` stores only package, hash and result | which rows came from which mod |
| conflicts | two mods touching one creature: last write wins, silently | detectable, and resolvable by policy |
| lifecycle | apply once per file hash | install / update / uninstall as first-class verbs |

**The DOL XML package mechanism is not the right foundation for this**, aside
from its defects. The format requires a mod author to know internal C# class
names (`DbNpcTemplate`, not the table name `NpcTemplate`); it validates
nothing, so a misspelled element is silently dropped; and it has no schema
version, no provenance and no conflict handling. It is a data-seeding tool,
and it should stay one.

**What is needed is a mod loader**, and the server does not have to change
much for it:

- the server compiles C# scripts natively, so a loader can be a **plugin**:
  it reads a pack manifest, applies or removes rows, and keeps provenance in
  its own table. No core change;
- if the loader should live in the core instead, the requirement is one
  **provenance table** plus recording what was written. Moderate, not major.

**Id ranges are what make mods compose.** A mod reserves a span (this pack
uses `100_000_000`, verified free against the server's own `npctemplate`
rows); removal then deletes exactly that span, and two mods whose spans
overlap are detectably in conflict rather than silently overwriting each
other. The allocation has to be deliberate for this to scale past a handful
of authors.

**The bottleneck is not the server, it is the client.** A mod that reuses
existing assets (this pack's creatures) deploys today, and since 2026-10-07 so
does one that changes client *tables* or adds model ids: `camelot mpak repack`
output is accepted by the 1.127 client (header carriers recomputed,
`formats.md` §5). What still cannot ship is a mod that *brings* a NIF — no
writer yet — and, for anything client-side, the *delivery*: nothing carries
the rewritten archive to the client, and two mods patching the same client
files have no precedence mechanism at all. Client-side composition needs its
own answer before any of this can be promised to third-party authors.

**Why OpenDAoC and not DOLSharp.** OpenDAoC is the maintained,
community-used server; DOLSharp is its older ancestor. The comparative
measurement behind that choice -- the extension point, its timing and the
database surface, checked across both trees -- is in
`workspace/notes`, kept private because it
is evidence for a decision rather than part of the product.

**Vocabulary, corrected.** What the pack does today is *add with explicit
uninstall*, not a removable overlay. The gap between the two is exactly
provenance, and it should not be papered over in the documentation.

**The mod loader is a plugin, and the hook is verified portable** (2026-10-05).
Measurements against OpenDAoC, not assumptions:

- the extension point is `IDatabaseUpdater` plus a `[DatabaseUpdate]`
  attribute. Both servers discover it the same way -- reflect over
  `ScriptMgr.GameServerScripts`, keep the classes implementing the interface,
  instantiate and call `Update()`. The attribute file is identical in the two
  trees, down to the line numbers;
- it runs from `UpdateDatabase()` once at startup, after the database is
  initialized and the scripts are compiled. That is the right moment for a
  loader and the same moment the DOL XML packages already use;
- the database surface a loader needs is `IObjectDatabase`,
  `AddObject(DataObject)`, `AddObject(IEnumerable<DataObject>)` and
  `SelectAllObjects<T>`, reached through the interface rather than through
  concrete types.

**One hazard to design around.** `UpdateDatabase()` returning `false` makes
the server refuse to start (`if (!UpdateDatabase()) return false;`). A mod
loader that lets an exception escape can therefore stop a server from
booting because of one bad mod. `AutoXMLDatabaseUpdate` catches everything
internally and only logs; a loader must do the same, per mod, and report the
failure in its own provenance table.

**What this settles.** The loader is a plugin, not a core change: multi-server
reach, no fork to maintain, and independence from the XML defects -- our path
stops depending on that fix, though the fix is still worth upstreaming for
everyone else.

## Phase 5 — Editing and productization

The server pack works end to end, the MPAK output *is* validated against the
real client (2026-10-07), and the client delivery half is built (2026-10-08):
`camelot mod build` assembles a pack from an authoring directory,
`camelot client install/uninstall/status/verify` applies it with claims and
priority, and the loader notes each pack's payload count. Remaining:

- run the in-client ladder that closes the phase (P5-A…P5-F, workspace
  `notes/phase5-client-assets.md`);
- add the writer still needed for in-place content replacement (NIF);
- provide safe KFA/mesh editing and re-rig workflows;
- add useful search, collections and annotations to the workbench;
- package the tools for repeatable server-developer use;
- separate public implementation documentation from private research.

## Explicitly deferred

Until Phases 0–4 are complete, defer broad UI expansion, MCP endpoints,
multi-root workspaces, desktop packaging and speculative features that do not
shorten the Studio-to-server delivery loop.

## Decisions recorded (continued)

- **The product is a mod ecosystem, not a single pack.** The pack format is
  the mod format: a manifest of logical ids, target projections and checksums
  is what a mod author publishes and an operator installs. See the section
  under Phase 4.
- **Mods must be installable, updatable and removable**, with a server
  restart at most. This makes *provenance* a requirement, not a nicety: the
  server has to know which rows belong to which mod.
- **The DOL XML data package is not the mod substrate.** It stays a
  data-seeding tool. The mod path is a loader — a plugin first, since that
  needs no core change.
- **Id ranges are the composition mechanism.** A mod reserves a span; removal
  is exact and overlapping claims are detectable.
- **The client side is the real constraint**, not the server: assets a mod
  *brings* need NIF writing (MPAK writing is done and validated in-client),
  and client-file conflicts between mods have no precedence mechanism today.

- The canonical client corpus is `DAOC CLIENT V2`, resolved through
  `$DAOC_CLIENT`; its exact build version must be recorded in the client
  profile.
- **OpenDAoC is the only target.** The contract stays server-neutral so the
  pack is portable, but there is one projection, one adapter and one
  deployment path: OpenDAoC's. DOLSharp is superseded and is not supported;
  see the decision note under Phase 4.
- OpenDAoC is the end-to-end acceptance target.
- Visual observations are stored as structured reproduction cases plus
  screenshots when available; no single storage format needs to be chosen by
  the user.

## Decisions to settle before Phase 2

- Which server adapter should be implemented and validated first, and which
  deployment path is the primary acceptance scenario?
- Which IDs and appearance fields are already authoritative on each server,
  and which must be introduced by a bridge?
- What is the acceptable policy for unresolved or client-specific content:
  reject, quarantine, or explicitly versioned fallback?
