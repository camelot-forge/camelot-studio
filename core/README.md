# core — Camelot Studio (Python)

The binary-format and conversion layer of Camelot Studio. It owns DAoC
NIF/KFA/MPAK/gamedata handling; the web viewer, Blender integration and server
adapters consume its JSON/glTF artifacts.

## Main modules

```text
camelot/
├── nifutil.py       # pyffi NIF access, matrices and axis conversion
├── kfa.py           # KFA channels and key data
├── anims.py         # gamedata animation links, timing and sampling
├── appearance.py    # fig3 skin, eye, tint and decal table data
├── fig3.py          # fig3 tables, pieces and attachment
├── playerfig.py     # native player fig3 preview assembly
├── gltf.py          # glTF export, skinning and texture sidecars
├── legacy.py        # NetImmerse 4.x mesh reader
├── gamedata.py      # gamedata table/archive resolution, and the row editor
├── mpak.py          # MPAK reader/writer
├── contract.py      # the server-facing content contract (manifest)
├── assemble.py      # pack assembly: tar.gz, PACK.json, checksums.lock
├── mod.py           # the deployment document: server rows + client payload
├── modbuild.py      # an authoring directory -> a pack
├── clientinstall.py # apply a pack's client half to a client directory
├── serve/           # local FastAPI/uvicorn asset daemon
└── cli.py           # camelot command-line entry point
```

## Commands

```sh
camelot info   figures/Boar.NIF
camelot kfa    anims/BOAR_idle.kfa
camelot gltf   figures/Boar.NIF --out work/boar
camelot gltf   figures/Boar.NIF --out work/boar --skinned
camelot roundtrip figures/Boar.NIF
camelot batch  tests/manifest.example.txt --out work/batch.txt
camelot mpak list archive.mpk
camelot mpak get archive.mpk path/inside/archive
camelot mpak pack output.mpk archive-name file...
camelot mod build --dir mymod --client "$DAOC_CLIENT" --out mymod.tar.gz
camelot client install mymod.tar.gz --client "$DAOC_CLIENT"
camelot client status --client "$DAOC_CLIENT"
camelot client verify --client "$DAOC_CLIENT"
camelot client uninstall mymod --client "$DAOC_CLIENT"
camelot serve --root "$DAOC_CLIENT"
```

`$DAOC_CLIENT` selects the client corpus and enables corpus tests. The current
project profile uses the `DAOC CLIENT V2` directory; the exact build and
relevant hashes belong in a generated client profile rather than in this
README.

## Tests

```sh
.venv/bin/python -m pytest core/tests -q
DAOC_CLIENT="/path/to/DAOC CLIENT V2" .venv/bin/python -m pytest core/tests -q
```

Corpus tests skip when `DAOC_CLIENT` is absent. The web tests, type-check and
build run from `web/` with pnpm.

## Current limits

- pyffi may reorder blocks on write; round-trip validation compares parsed
  block multisets, not byte identity.
- The built-in NetImmerse 4.x mesh reader is read-only; a 4.x writer is not
  implemented.
- The current glTF skinning path uses the stored `NiSkinData` transform
  (`inverseBind = S·meshWorld⁻¹`) and is corpus-validated, not a complete
  proof of every client path.
- The native player builder previews fig3 appearance data and a selected idle
  for the 18 playable races, with positional hair styles (`Bald` included) and
  the hair `S/T/M` decal baked in. Complete `pskins`/equipment resolution and
  explicit animation-state selection are not implemented.
- A mod's client half replaces existing destinations (loose file, archive
  entry, gamedata row edit); a payload cannot *add* an archive entry yet, and
  new *geometry* waits on a NIF writer (`docs/status.md`).

See `docs/status.md` and `docs/formats.md` for the current evidence, and
`docs/next-steps.md` for the roadmap. The client half of a mod travels in
the pack and is applied by `camelot client install`; models the client has
never read still wait on a NIF writer, and the in-client ladder that closes
Phase 5 is `notes/phase5-client-assets.md` in the workspace.
