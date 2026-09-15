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

