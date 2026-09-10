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

## Structure

```
src/
├── app.css                       # design system (tokens, buttons, inputs)
├── lib/
│   ├── skeleton.ts               # canonical KFA bone index -> name (mirrors core)
│   ├── gltf/load.ts              # drop .gltf+.bin / .glb -> THREE.Scene
│   └── components/
│       ├── Header.svelte
│       ├── DropZone.svelte       # drag & drop / browse
│       ├── Viewer.svelte         # three.js scene, orbit, wireframe, axes
│       └── Sidebar.svelte        # model info + bone rig panel
└── routes/
    ├── +layout.svelte            # shell, app.css, head
    └── +page.svelte              # DropZone <-> Viewer + Sidebar
```

The bone table in `skeleton.ts` mirrors `core/camelot/skeleton.py` (indices
0..53 verified; 54+ open). Keep the two in sync.
