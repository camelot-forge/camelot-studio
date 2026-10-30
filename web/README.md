# web — the Studio's browser app

SvelteKit (Svelte 5, runes) + three.js. It runs two ways: as a static drop-in
viewer for `.gltf`/`.glb` files, and served by `camelot serve`, where it reads
the daemon's API as well — the client's asset library, the world editor, the
character builder and Deploy.

Design system: vanilla CSS adapted from Pictiúr's tokens (dark theme,
`Space Grotesk`, accent `#2dd4a7`), organised in Svelte components.

## Destinations

| Route      | What it is                                                           |
| ---------- | -------------------------------------------------------------------- |
| `/home`    | what the daemon serves, the machine's projects, the ways in          |
| `/world`   | the world editor: terrain, fixtures, NPC/object previews, placements |
| `/`        | Figures: the asset library, the drop zone, the viewer, the inspector |
| `/builder` | the native fig3 character builder                                    |
| `/deploy`  | publish the pending edits, test them locally, inspect the feed       |
| `/render`  | offscreen renders, for the screenshot harness                        |

## Stack

- SvelteKit 2 + Svelte 5 (runes) + Vite, TypeScript
- three.js (GLTFLoader + OrbitControls)
- Vitest (unit) · ESLint · Prettier
- `@lucide/svelte` icons

## Commands

```sh
pnpm dev         # dev server
pnpm build       # static export -> build/
pnpm check       # svelte-check
pnpm test        # vitest run
pnpm lint        # prettier --check + eslint
pnpm format      # prettier --write
```

## Running it

`camelot serve` serves the built app and the API on one loopback origin, which
is the supported arrangement:

```sh
.venv/bin/camelot serve --no-open     # terminal 1
pnpm build                            # then reload the page
```

For front-end work, Vite proxies `/api` to the daemon
(`CAMELOT_API=http://127.0.0.1:8763 pnpm dev` points it at a scratch one):

```sh
.venv/bin/camelot serve --no-open     # terminal 1
pnpm dev                              # terminal 2 (localhost:5173)
```

The daemon serves the `build/` it found at startup: run `pnpm build`, then
restart the daemon.

## Using it

Export a figure with the core CLI, then drop the artifacts into the viewer:

```sh
cd .. && .venv/bin/camelot gltf "/path/to/Boar.NIF" --out work/boar
```

`work/boar.gltf` and `work/boar.bin` together are one drop; `work/boar.skin.json`
adds the bone rig, and a single `.glb` also works. The left **Library** panel
does the same from the daemon, with search, a type filter and live refresh over
SSE.

## The launcher's page (`launcher/`)

A separate Vite build: the players' page reads one feed and starts the game, and
shares the design tokens with the Studio by file (`src/app.css`).
`cd launcher && pnpm install && pnpm build` writes `dist/`, which
`camelot launcher` serves.

## Structure

```text
src/
├── app.css                     # design system (tokens, buttons, inputs)
├── lib/
│   ├── api/client.ts           # typed client for the `camelot serve` API
│   ├── nav.ts home.ts          # the destinations, and Home's own facts
│   ├── anim.ts appearance.ts races.ts character.ts   # figure data
│   ├── inspect.ts transport.ts # the inspector's cards, the transport bar
│   ├── world/                  # terrain, ground, gizmo, carried, journal, entities, catalog, viewer
│   ├── gltf/load.ts            # glTF/GLB -> THREE.Scene
│   └── components/             # AppBar, Dock, Inspector, Library, Viewer, …
└── routes/                     # home, world, builder, deploy, render, figures
```

`AGENTS.md` § Browser checks and the probes under `scripts/` cover how a change
here gets checked.
