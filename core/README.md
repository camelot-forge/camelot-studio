# core — Camelot Studio (Python)

The heart of the suite: DAoC binary formats + conversions (glTF). No
dependency on Blender or the server.

```
camelot/
├── nifutil.py   # read/write via pyffi, matrices, axis conversion
├── kfa.py       # .kfa channels (bone indices, keys)
├── gltf.py      # glTF 2.0 export (bind pose, skinned)
├── legacy.py    # NetImmerse 4.x reader (pyffi fallback)
└── cli.py       # "camelot" entry point
```

## Commands

```sh
camelot info   <figures/Boar.NIF>     # version, blocks, nodes, textures
camelot kfa    <anims/BOAR_idle.kfa>  # channels, bone indices, keys
camelot gltf   <file.nif> --out pre   # glTF bind pose (+ .bin, + .skin.json)
camelot gltf   <file.nif> --out pre --skinned  # + JOINTS/WEIGHTS (experimental)
camelot roundtrip <file>              # read→write→read (multiset compare)
camelot batch  tests/manifest.example.txt --out work/batch.txt   # bulk validation
```

Set `DAOC_CLIENT` to your client directory so batch/tests resolve
`DAOC CLIENT/...` manifest paths automatically.

## Tests

```sh
.venv/bin/python -m pytest tests -q                    # units only
DAOC_CLIENT=/path/to/client .venv/bin/python -m pytest tests -q  # + corpus
```

Corpus tests need the real client and skip automatically otherwise.

## Limits (see docs/status.md)
- **Write**: pyffi reorders blocks (roundtrip validated as multisets).
- **Meshed NetImmerse 4.x NIFs**: pyffi 2.2.3 fails, but the built-in
  `legacy.py` reader now covers 100% of the client's 4.x NIFs (read-only;
  a 4.x writer is M2/M3 work for in-place client replacement).
- **Bind matrices**: NiSkinData offsets look unusable; inverseBind
  = inv(worldBind) of the exported skeleton (glTF standard, correct at the
  bind pose) — to be confirmed by real rendering.

Roadmap: web viewer (web/), bone index table (→ kfa in the glTF), MPAK
write, gamedata JSON tables, set packager.