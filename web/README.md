# web — (M1) figure viewer

SvelteKit (Svelte 5, runes) + three.js viewer for the figures exported by
`core`. Loads the layer contract artifacts — `.gltf` + `.bin` (+ optional
`.skin.json`) — produced by `camelot gltf`. Ships as a **static site**
(adapter-static, SPA): no Python dependency for end users.

Design system: vanilla CSS, adapted from Pictiúr's tokens (dark theme,
`Space Grotesk`, accent `#2dd4a7`), organised in Svelte components.

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

## How to use

Export a figure with the core CLI, then drop the artifacts into the viewer:

```sh
cd ../.. && .venv/bin/camelot gltf "/path/to/Boar.NIF" --out work/boar
```

Open the viewer and drop `work/boar.gltf` together with `work/boar.bin` (and
optionally `work/boar.skin.json` for the bone rig). A single `.glb` also works.

### Library mode (`camelot serve`)

Start the local daemon on your client directory; it serves this viewer _and_
the API on one origin, so there is no CORS to configure:

```sh
.venv/bin/camelot serve            # root = $DAOC_CLIENT, opens the browser
```

The left **Library** panel lists figures (search + type filter, paginated),
loads a `.glb` from the daemon when you click one, and refreshes live over SSE
when the client changes. Drag & drop still works when no daemon is running.

For development, run the daemon and Vite side by side — Vite proxies `/api` to
`http://127.0.0.1:8760` (override with `CAMELOT_API`):

```sh
.venv/bin/camelot serve --no-open     # terminal 1
pnpm dev                              # terminal 2 (browser: localhost:5173)
```

To point a static build at a daemon on another origin, use
`?api=http://localhost:8760` (persisted in `localStorage`).

## Structure

```
src/
├── app.css                       # design system (tokens, buttons, inputs)
├── lib/
│   ├── api/client.ts             # typed client for the `camelot serve` API
│   ├── skeleton.ts               # canonical KFA bone index -> name (mirrors core)
│   ├── gltf/load.ts              # drop .gltf+.bin / .glb -> THREE.Scene
│   └── components/
│       ├── Header.svelte
│       ├── DropZone.svelte       # drag & drop / browse
│       ├── Library.svelte        # daemon browser: search, filters, SSE
│       ├── Viewer.svelte         # three.js scene, orbit, wireframe, axes
│       └── Sidebar.svelte        # model info + bone rig panel
└── routes/
    ├── +layout.svelte            # shell, app.css, head
    └── +page.svelte              # Library + DropZone <-> Viewer + Sidebar
```

The bone table in `skeleton.ts` mirrors `core/camelot/skeleton.py` (indices
0..53 verified; 54+ open). Keep the two in sync.
