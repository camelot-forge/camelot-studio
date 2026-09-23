"""Appearance resolution — the client tables' appearance pickers.

Maps the DAoC character-creation structure (7 `eCharFacePart` bytes per
the OSS emulators, `notes/character-rules.md` §11) onto the client CSV
tables it resolves through:

| attribute | client table | engine storage |
|---|---|---|
| face 1..7     | `fig3facemap.csv`   | 8-int rows, `DAT_00df4e80` |
| hairstyle 1..N| `fig3map.csv` Hair slot | the per-race Hair id list |
| hair color 1..16 | `fig3haircolormap.csv` | 16 × (S, T, M) per (race, style) |
| eye color 1..8 | `fig3eyecolormap.csv` | 8 eyes per desc row |
| skin tone 1..8 | `fig3skincolormap.csv` | 8 skin rows per desc row (→ tints) |
| decal         | `fig3decalmap.csv` | 16 per race |

All ids are `skins.csv`/`monnifs.csv`-reflective ids resolvable via
:func:`camelot.gamedata.skin_texture` / `texture_bytes`.
"""
from __future__ import annotations

from . import gamedata


def _col_row_map(client_dir: str, csv_name: str) -> dict[int, list[str]]:
    """`fig3<name>.csv` rows keyed by description id (column 0).

    The client CSVs ship two header rows; the engine reads the id, then
    the data cells (8 ints for the eye/skin/facemaps, 16 triplets for the
    hair colormap).
    """
    out: dict[int, list[str]] = {}
    for row in gamedata._read_table(client_dir, csv_name):
        if len(row) < 2 or not row[0].strip().isdigit():
            continue
        out[int(row[0].strip())] = row
    return out


def _facemap(client_dir: str) -> dict[int, list[str]]:
    return _col_row_map(client_dir, "fig3facemap.csv")


def _eyecolormap(client_dir: str) -> dict[int, list[str]]:
    return _col_row_map(client_dir, "fig3eyecolormap.csv")


def _skincolormap(client_dir: str) -> dict[int, list[str]]:
    return _col_row_map(client_dir, "fig3skincolormap.csv")


def _haircolormap_rows(client_dir: str) -> dict[int, list[list[str]]]:
    out: dict[int, list[list[str]]] = {}
    for row in gamedata._read_table(client_dir, "fig3haircolormap.csv"):
        if len(row) < 2:
            continue
        key = row[0].strip()
        if not key.isdigit():
            continue
        out.setdefault(int(key), []).append(row)
    return out


def _hair_triplets(client_dir: str, race_e_id: int, style: int):
    """The 16 (S, T, M) triplets of a (race, style) row."""
    out: list[tuple[str, str, str]] = []
    for row in _haircolormap_rows(client_dir).get(race_e_id, []):
        raw_style = row[1].strip()
        if not raw_style.isdigit():
            continue
        if int(raw_style) != int(style or 1):
            continue
        cells = row[3:]
        for index in range(0, len(cells), 3):
            slot = cells[index:index + 3]
            out.append(tuple(
                (slot[i].strip() if len(slot) > i else "") for i in range(3)))
        break
    return out


def face_sheet(client_dir: str, race_e_id: int, face: int):
    """The `fig3facemap.csv` cell → face sheet, keyed (race, face index).

    Engine row: ``[id, desc, 8 cells, (trailing empties)]``, face 1-based."""
    row = _facemap(client_dir).get(race_e_id, [None])
    if row is None or len(row) < 2:
        return None
    cells = [cell for cell in row[2:] if cell.strip()]
    cell = cells[min(max(face, 1), len(cells)) - 1].strip()
    return gamedata.skin_texture(client_dir, cell) if cell else None


def eye_sheet(client_dir: str, race_e_id: int, eye: int):
    """`fig3eyecolormap.csv` row = ``[id, desc, 8 eye cells]``; 1-based."""
    row = _eyecolormap(client_dir).get(race_e_id, [None])
    if row is None or len(row) < 2:
        return None
    cells = [cell for cell in row[2:] if cell.strip()]
    cell = cells[min(max(eye, 1), len(cells)) - 1].strip()
    return gamedata.skin_texture(client_dir, cell) if cell else None


def skin_tint(client_dir: str, race_e_id: int, tone: int):
    """The tint id for (race, skin tone) — `fig3skincolormap` volts."""
    row = _skincolormap(client_dir).get(race_e_id, [None])
    if row is None or len(row) < 2:
        return None
    cells = [cell for cell in row[2:] if cell.strip()]
    cell = cells[min(max(tone, 1), len(cells)) - 1].strip()
    return int(cell) if cell.isdigit() else None


_WHITE = (255, 255, 255)


def eye_composite(client_dir: str, race_e_id: int, face: int, eye_color: int):
    """The face sheet's painted eye multiplied by the `eyecolor_*` decal.

    The face sheet carries the eyeball artwork at the eyeball's UVs (sclera,
    iris, pupil, lid highlight) and the eye sheet is the engine's dark map:
    a nearly-all-white sheet whose tiny ring is the iris colour. The game
    multiplies the two (Gamebryo "Dark Map", docs/formats.md §8.10);
    three.js has no second multiply stage, so the exporter bakes the product
    into the Eyes sheet. Returns ``(name, bytes)`` like the sheet helpers.
    """
    base = face_sheet(client_dir, race_e_id, face)
    decal = eye_sheet(client_dir, race_e_id, eye_color)
    if base is None:
        return None
    if decal is None:
        return base

    import numpy as np

    from .dxt import dds_rgba, encode_a8r8g8b8

    base_data = dds_rgba(base[1])
    decal_data = dds_rgba(decal[1])
    if base_data is None or decal_data is None:
        return base  # cannot multiply these formats: keep the artwork
    aw, ah, apx = base_data
    bw, bh, bpx = decal_data
    rgb = np.frombuffer(apx, np.uint8).reshape(ah, aw, 4)[:, :, :3].astype(np.uint16)
    dec = np.frombuffer(bpx, np.uint8).reshape(bh, bw, 4)[:, :, :3]
    # nearest resample of the decal to the base's grid: both sheets use the
    # same normalised UVs, so texel (0,0) stays texel (0,0).
    dec = dec[(np.arange(ah) * bh // ah).clip(0, bh - 1)][
        :, (np.arange(aw) * bw // aw).clip(0, bw - 1)
    ].astype(np.uint16)
    product = (rgb * dec // 255).astype(np.uint8)
    rgba = np.dstack([product, np.full((ah, aw), 255, np.uint8)])
    return (decal[0], encode_a8r8g8b8(aw, ah, rgba.tobytes()))


def hair_composite(client_dir: str, base_id: str, decal_id: str):
    """The hair base sheet with its `M` decal composited (alpha overlay).

    The engine selects `ps_HairMaterialDecalShader1x` when the haircolormap
    triplet's third cell is set and passes base, tint and decal as separate
    material fields (`FUN_004174e2` -> `FUN_0042568d`). The `M` sheet is a
    1-bit-alpha cutout on the base's own UV set (fig3 hair meshes ship a
    single UV stream), so the faithful single-texture bake is
    ``base*(1-a) + decal*a`` while keeping the base's strand alpha; the
    viewer still multiplies the result by the `T` tint. Returns
    ``(name, bytes)`` like the sheet helpers, or the plain base when the
    triplet carries no decal.
    """
    base = gamedata.skin_texture(client_dir, str(base_id).strip()) if base_id else None
    if base is None:
        return None
    decal = (
        gamedata.skin_texture(client_dir, str(decal_id).strip())
        if decal_id and str(decal_id).strip()
        else None
    )
    if decal is None:
        return base

    import numpy as np

    from .dxt import dds_rgba, encode_a8r8g8b8

    base_data = dds_rgba(base[1])
    decal_data = dds_rgba(decal[1])
    if base_data is None or decal_data is None:
        return base  # cannot blend these formats: keep the artwork
    aw, ah, apx = base_data
    bw, bh, bpx = decal_data
    base_px = np.frombuffer(apx, np.uint8).reshape(ah, aw, 4)
    dec = np.frombuffer(bpx, np.uint8).reshape(bh, bw, 4)
    # nearest resample of the decal to the base's grid: both sheets use the
    # same normalised UVs, so texel (0,0) stays texel (0,0).
    dec = dec[(np.arange(ah) * bh // ah).clip(0, bh - 1)][
        :, (np.arange(aw) * bw // aw).clip(0, bw - 1)
    ].astype(np.uint16)
    alpha = dec[:, :, 3:4]
    rgb = base_px[:, :, :3].astype(np.uint16)
    blended = (rgb * (255 - alpha) + dec[:, :, :3] * alpha) // 255
    rgba = np.dstack([blended.astype(np.uint8), base_px[:, :, 3]])
    return (base[0], encode_a8r8g8b8(aw, ah, rgba.tobytes()))


def tint_rgb(client_dir: str, tint_id: int) -> tuple[int, int, int] | None:
    """(Red, Green, Blue) of a `fig3tints.csv` id — the engine's packed tint.

    The engine reads the three ints per row and packs them `(R<<16)+(G<<8)+B`;
    a white tint is the no-op (the sheet keeps its authored colours).
    """
    for row in gamedata._read_table(client_dir, "fig3tints.csv"):
        if len(row) < 5 or row[0].strip() != str(tint_id):
            continue
        try:
            return (int(row[2]), int(row[3]), int(row[4]))
        except ValueError:
            return None
    return None


def skin_tint_rgb(client_dir: str, race_e_id: int, tone: int) -> tuple[int, int, int] | None:
    """The skin-tone tint of (race, tone) — `fig3skincolormap` id → tints."""
    tint_id = skin_tint(client_dir, race_e_id, tone)
    if tint_id is None:
        return None
    rgb = tint_rgb(client_dir, tint_id)
    return rgb if rgb and rgb != _WHITE else None


def hair_tint_rgb(client_dir: str, race_e_id: int, style: int,
                  color: int) -> tuple[int, int, int] | None:
    """The hair-colour tint of (race, style, colour) — the triplet's T cell.

    Races distribute colour differently: Saracen keeps one sheet per style and
    varies the tint across the 16 colours, Lurikeen pre-colours the sheets and
    ships `No tint` (white) in T.
    """
    triplets = _hair_triplets(client_dir, race_e_id, style)
    if not triplets:
        return None
    cell = triplets[min(max(color, 1), len(triplets)) - 1]
    raw = cell[1].strip() if len(cell) > 1 else ""
    if not raw.isdigit():
        return None
    rgb = tint_rgb(client_dir, int(raw))
    return rgb if rgb and rgb != _WHITE else None


def decal_ids(client_dir: str, race_e_id: int):
    """The race's decal ids; the corpus alpha maps (first few races) ship empty rows."""
    out: dict[int, str] = {}
    for row in gamedata._read_table(client_dir, "fig3decalmap.csv"):
        if len(row) < 3 or not row[0].strip().isdigit():
            continue
        key = int(row[0].strip())
        cells = row[2:]
        per_color = {
            index: cell.strip()
            for index, cell in enumerate(cells, start=1)
            if cell.strip()
        }
        out.setdefault(key, {}).update(per_color)
    ids = out.get(race_e_id, {})
    return ids

