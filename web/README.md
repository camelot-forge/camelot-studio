# web — figure viewer and character builder

SvelteKit (Svelte 5, runes) + three.js viewer for the artifacts exported by
`core`. It can run as a static drop-in viewer for `.gltf`/`.glb` files and, when
served by `camelot serve`, also provides the client asset library and the
native fig3 character builder.

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

## Verifying in a browser

The app is served from `build/` by `camelot serve`: `pnpm build`, then reload —
no daemon restart (static files are read per request).

Two levels, and they do not replace each other:

- **The probes are the contract** — `scripts/dock_probe.mjs`,
  `world_mouse_probe.mjs`, `viewer_probe.mjs`, `builder_probe.mjs`, driven
  headless with `--use-gl=swiftshader --enable-unsafe-swiftshader` so the
  canvas renders on a machine with no GPU:
  `node scripts/dock_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux`.
  Versioned and deterministic; a copy pass does not need the gesture probes,
  but `dock_probe` asserts labels and does.
- **The Playwright MCP is for reading** — the agent drives a real browser from
  its own tool list (snapshot, click, console, network) for a question the
  probes do not assert. Setup, the Chrome-channel pitfall and the WebGL
  caveat: `../AGENTS.md` § The Playwright MCP.

Either way: port 8762 is the operator's own daemon — read it, never edit
through it; anything that writes gets a scratch daemon and its own project
(`--project /tmp/<name>`, port 8763+), which is what the probes do.

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

The supported daemon mode is same-origin (`camelot serve` serves the built
web app) or the Vite development proxy. The daemon intentionally does not
enable CORS for an arbitrary static-build origin.

## Native builder scope

`/builder` previews the current fig3 player pipeline: race/gender, face, hair
style/color, eye color, skin tone, cloak and a selected idle. A freshly loaded
route waits for a race selection before assembling the first model. It exposes
the 18 playable races; hair styles are positional (`Bald` included) and the
hair `S/T/M` triplet is served with the `M` decal baked in. Complete
`pskins`/equipment resolution and explicit animation-state selection are not
implemented yet. A successful preview is not a complete asset or server-payload
validation.

## Structure

```text
src/
├── app.css                       # design system (tokens, buttons, inputs)
├── lib/
│   ├── api/client.ts             # typed client for the `camelot serve` API
│   ├── anim.ts                   # sampled animation tracks and timing
│   ├── character.ts              # classic/class-NPC mesh classification
│   ├── gltf/load.ts              # glTF/GLB -> THREE.Scene
│   ├── skeleton.ts               # structural skeleton reference only
│   └── components/
│       ├── Header.svelte
│       ├── DropZone.svelte       # drag & drop / browse
│       ├── Library.svelte        # daemon browser: search, filters, SSE
│       ├── Viewer.svelte         # scene, orbit, wireframe, axes
│       ├── Sidebar.svelte
│       ├── CharacterPanel.svelte # classic/class-NPC variant picker
│       └── Stepper.svelte
└── routes/
    ├── +layout.svelte
    ├── +page.svelte              # library + drop zone
    └── builder/+page.svelte      # native fig3 appearance preview
```

The KFA channel table is loaded by `core` from the client's
`figures/animnode.dat` and contains 175 names. `src/lib/skeleton.ts` is only a
structural DFS reference for the human skeleton; it is not the canonical KFA
index table and must not be described as one.
