# core — Camelot Studio (Python)

The binary-format and conversion layer. It owns DAoC NIF/KFA/MPAK/gamedata
handling; `web/` and `server-bridge/` consume its JSON/glTF artifacts.

## Commands

```sh
camelot info      figures/Boar.NIF                        # header and blocks
camelot kfa       anims/BOAR_idle.kfa                     # animation channels
camelot gltf      figures/Boar.NIF --out work/boar        # add --skinned for the rig
camelot roundtrip figures/Boar.NIF                        # read -> write -> read
camelot batch     tests/manifest.example.txt --out work/batch.txt
camelot mpak      list archive.mpk
camelot serve     --root "$DAOC_CLIENT"                   # asset library and viewer
```

The pack tools (`camelot mod build`, `camelot client install|uninstall|status|
verify`) and the release tools (`project`, `env`, `publish`, `console`,
`launcher`) sit on the same layer. `camelot --help` lists every command and
`docs/status.md` carries the table with each one's arguments.

`$DAOC_CLIENT` selects the client corpus and enables the corpus tests.

## Where things live

```text
camelot/
├── nifutil.py kfa.py legacy.py        # NIF/KFA on pyffi, and the 4.x reader
├── gltf.py glb.py dxt.py png.py       # glTF export, GLB packing, image codecs
├── mpak.py gamedata.py pcx.py         # archives, tables, zone maps
├── fig3.py figure.py playerfig.py     # figures: classic body, fig3 head and hair
├── appearance.py anims.py creatures.py skeleton.py
│                                      # appearance tables, animation links, rig
├── zones.py                           # the client's world: index, terrain, placements
├── contract.py assemble.py mod.py modbuild.py
│                                      # the content contract, and a pack as one unit
├── clientinstall.py clientpatch.py clientfs.py
│                                      # a pack's client half, and the client binary
├── journal.py project.py release.py pipeline.py
│                                      # the project: edits, releases, deploys
├── envs.py installer.py serverrows.py # an environment, and the rows a release writes
├── feed.py feedbuild.py feedserve.py launcher.py
│                                      # the feed the players read, and the launcher
├── publish.py remote.py sign.py console.py
│                                      # the camelot-deploy branch, its pull, its Console
├── serve/                             # the local daemon: asset index, /world, the editor
└── cli.py                             # the entry point
```

## Tests

```sh
.venv/bin/python -m pytest core/tests -q                          # unit tests
DAOC_CLIENT="/path/to/client" .venv/bin/python -m pytest core/tests -q
```

Corpus tests skip when `DAOC_CLIENT` is absent.

## Limits

`docs/status.md` § Known limits, with the measurement behind each one.
