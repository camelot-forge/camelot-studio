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
DOLSharp / OpenDAoC adapters
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

DOLSharp and OpenDAoC are target platforms, not part of the canonical Studio
schema. Studio should produce a portable content pack and generate target
projections for each server family:

- a neutral logical model, appearance, slot, animation and dependency model;
- a client profile describing the supported DAoC build and asset mappings;
- a DOLSharp projection using its supported data packages and script/plugin
  mechanisms;
- an OpenDAoC projection using its supported data packages and script/plugin
  mechanisms;
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
  targets/dolsharp/...
  targets/opendaoc/...
  client/patch-manifest.json
  checksums.lock
```

`pack.json` should carry a schema version, pack ID/version, client profile,
dependencies, entry points, target support and artifact hashes. The canonical
manifests use namespaced logical IDs; generated target files map those IDs to
DOLSharp or OpenDAoC values. Optional runtime plugins should be small adapters
for creation, live appearance changes, validation or restoration, not the
source of truth for asset metadata.

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
2. pin an OpenDAoC source commit, a database schema/commit and an image tag;
3. choose and document whether the server data profile is the official 1.65
   database, a later client profile, or a tested compatibility overlay;
4. verify the Release/Docker artifact contains the required runtime XML and
   script assets;
5. validate ID ranges, model bit packing, character persistence and client
   login/creation before adding new asset behavior;
6. use a pinned image and reproducible deployment metadata rather than
   `latest` for acceptance tests.

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
- validate the current 30 race/gender builder rows in a real browser, then
  add and validate the missing traditional fig3 rows;
- document intentional refusals and fallback policy.

Every change must ship with tests and, for visual behavior, a reproducible
manual verification case.

## Phase 2 — Define the server-facing content contract

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

## Phase 3 — Build the server content pack

Implement an offline packaging command or module that:

1. reads the client corpus through `$DAOC_CLIENT`;
2. resolves the required content and appearance references;
3. produces manifests, glTF/JSON artifacts and validation reports;
4. assembles a deterministic candidate patch/pack;
5. rejects missing, ambiguous or unverified references;
6. records client-version compatibility and a reproducible build identifier.

The initial target is a locally generated content pack, not a hosted service.

## Phase 4 — Validate the OpenDAoC integration first, then DOLSharp

Prove the complete path for OpenDAoC first, then repeat the contract tests for
DOLSharp:

```text
Studio pack -> target adapter -> server state -> client appearance
```

Test character creation, persistence, equipment, visual variants, animation
selection, spawn and reload. Use the emulator and the real client as
acceptance environments. Add a bridge or daochook integration only after the
manifest-only path is understood and reproducible.

## Phase 5 — Editing and productization

Only after the server pack works end to end:

- add the writers needed for in-place content replacement;
- provide safe KFA/mesh editing and re-rig workflows;
- validate MPAK output against the real client;
- add useful search, collections and annotations to the workbench;
- package the tools for repeatable server-developer use;
- separate public implementation documentation from private research.

## Explicitly deferred

Until Phases 0–4 are complete, defer broad UI expansion, MCP endpoints,
multi-root workspaces, desktop packaging and speculative features that do not
shorten the Studio-to-server delivery loop.

## Decisions recorded

- The canonical client corpus is `DAOC CLIENT V2`, resolved through
  `$DAOC_CLIENT`; its exact build version must be recorded in the client
  profile.
- The pack must support both DOLSharp and OpenDAoC through separate target
  adapters, without making either server the canonical data model.
- OpenDAoC is the first adapter and first end-to-end acceptance target.
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
