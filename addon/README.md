# addon — (M2) Blender addon

UI shell around the Python core (vendored pyffi). Import/export of
figures, fig3 parts, items and `.kfa` animations (via the bone index
table).

Principle: **no format logic in the addon** — everything goes through
`camelot` (CLI) so it survives Blender API changes.