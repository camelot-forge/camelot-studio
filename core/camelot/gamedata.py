"""Camelot Studio — DAoC gamedata tables, textures and animation links.

DAoC figures carry no texture in the NIF (except rare embedded cases): the
texture is resolved at runtime from the data tables packed in ``gamedata.mpk``:

    figure NIF  ->  monnifs.csv (Name = NIF basename)  ->  monnifs ID
                ->  monsters.csv (# = monnifs ID)      ->  skin IDs per slot
                ->  skins/mskins/pskins.csv (ID)       ->  texture name + archive
                ->  figures/skins/skinNNN.mpk          ->  the .dds bytes

The same ``monnifs.csv`` row carries the animation set (``Anim Set`` column),
which links the figure to its .kfa files through ``anims.csv`` and
``animnifs.csv`` (see :func:`camelot.anims.resolve_anim_from_gamedata`).

The archive number is the skin table's ``Num`` column (e.g. Boar body skin
109 -> Num 3 -> ``figures/skins/skin003.mpk`` -> ``boar.dds``). Texture names
in the tables use ``.tga`` while the archive entry is ``.dds``; matching is
done on the basename without extension.

Each skin table has its own archive directory (case varies on disk) and its
own prefix, so lookups go through :data:`_SKIN_TABLE_DIRS`. The Catacombs
appearance sheets (faces, hair) are not always in the archive the tables
claim — :func:`texture_bytes` falls back to a scan of the skin archives.
"""
from __future__ import annotations

import csv
import io
import os
from typing import NamedTuple

from .mpak import MpakArchive


class SlotTexture(NamedTuple):
    """One sheet to embed: export slot, file name, DDS bytes, optional tint.

    The tint is the engine's packed per-slot colour (``fig3tints.csv``):
    the viewer *multiplies* the sheet by it, which is how the client paints
    skin tones and most hair colours.
    """

    slot: str
    name: str
    data: bytes
    tint: tuple[int, int, int] | None = None


# Column indices (the CSVs carry two header rows: a group row then names).
MONNIFS_DESC = 1
MONNIFS_NAME = 2
MONNIFS_SET = 3  # Anim Set id -> anims.csv row (animations live there too)
MONSTERS_NIF = 2
ANIMNIFS_FILE = 2  # .kfa filename
MONSTERS_SLOTS = {"Body": 3, "Head": 4, "Arms": 5, "Gloves": 6,
                  "Lbody": 7, "Legs": 8, "Boots": 9, "Cloak": 10, "Helm": 11}
SKINS_NAME = 2
SKINS_ARCHIVE = 4

_SKIN_TABLES = ("skins.csv", "mskins.csv", "pskins.csv")
# table -> (top folder, folder, file prefix)
_SKIN_TABLE_DIRS = {
    "skins.csv": ("figures", "skins", "skin"),
    "mskins.csv": ("figures", "Mskins", "mskin"),
    "pskins.csv": ("items", "pskins", "pskin"),
}
_TEXTURE_DIRS = (("figures", "skins"), ("figures", "Mskins"), ("items", "pskins"))
_GAMEDATA = "gamedata.mpk"

# Tables are static once the client is installed; the exporter reads them
# several times per figure (skins, face map, hair colour map…).
_TABLE_CACHE: dict[tuple[str, str], list[list[str]]] = {}


def norm_name(s: str) -> str:
    """Case/separator-insensitive key for table name matching.

    The tables spell names differently from the files (`FrostGiant` in
    monnifs vs `Frost_Giant.NIF`, `Boar Keggars` vs `Boar_Keggers.nif`), so
    exact string compares miss real links.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


def _read_table(client_dir: str, name: str) -> list[list[str]]:
    key = (client_dir, name.lower())
    cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return cached
    path = os.path.join(client_dir, _GAMEDATA)
    a = MpakArchive(path)
    try:
        if name.lower() not in a.entries:
            rows: list[list[str]] = []
        else:
            raw = a.read(name)
            rows = list(csv.reader(io.StringIO(raw.decode("latin-1"))))
    finally:
        a.close()
    _TABLE_CACHE[key] = rows
    return rows


def _find_skin(skin_id: str, tables: dict[str, list[list[str]]]):
    """(texture name, archive num, table name) for a skin id, or None."""
    for name in _SKIN_TABLES:
        table = tables.get(name)
        if not table:
            continue
        for row in table:
            if len(row) > SKINS_ARCHIVE and row[0].strip() == skin_id:
                return row[SKINS_NAME].strip(), row[SKINS_ARCHIVE].strip(), name
    return None


def _archive_path(client_dir: str, table_name: str, archive_num: str) -> str | None:
    sub = _SKIN_TABLE_DIRS.get(table_name)
    if sub is None:
        return None
    try:
        num = int(archive_num)
    except ValueError:
        return None
    top, folder, prefix = sub
    root = os.path.join(client_dir, top, folder)
    if not os.path.isdir(root):
        return None
    want = f"{prefix}{num:03d}.mpk".lower()
    for filename in os.listdir(root):
        if filename.lower() == want:
            return os.path.join(root, filename)
    return None


def _read_skin_archive(client_dir: str, table_name: str, archive_num: str,
                       texture_name: str) -> bytes | None:
    path = _archive_path(client_dir, table_name, archive_num)
    if path is None:
        return None
    base = os.path.splitext(texture_name)[0].lower()
    a = MpakArchive(path)
    try:
        for entry in a.entries:
            if os.path.splitext(os.path.basename(entry))[0].lower() == base:
                return a.read(entry)
    finally:
        a.close()
    return None


def texture_bytes(client_dir: str, texture_name: str) -> bytes | None:
    """Search every skin archive for a texture, whatever the tables claim.

    The Catacombs face sheets (`Sar_f_Head01.dds`) list archive 0 in the
    tables but ship in a later archive of the matching directory.
    """
    base = os.path.splitext(os.path.basename(texture_name))[0].lower()
    for sub in _TEXTURE_DIRS:
        root = os.path.join(client_dir, *sub)
        if not os.path.isdir(root):
            continue
        for archive in sorted(os.listdir(root)):
            if not archive.lower().endswith((".mpk", ".npk")):
                continue
            a = MpakArchive(os.path.join(root, archive))
            try:
                for entry in a.entries:
                    if os.path.splitext(os.path.basename(entry))[0].lower() == base:
                        return a.read(entry)
            finally:
                a.close()
    return None


def _read_table_skin(client_dir: str, tables: dict[str, list[list[str]]],
                     skin_id: str) -> tuple[str, bytes] | None:
    """(texture name, bytes) for a skin id, table archive then scan fallback."""
    found = _find_skin(skin_id.strip(), tables)
    if not found:
        return None
    tex_name, archive_num, table_name = found
    data = _read_skin_archive(client_dir, table_name, archive_num, tex_name)
    if data is None:
        data = texture_bytes(client_dir, tex_name)
    return (tex_name, data) if data is not None else None


def skin_texture(client_dir: str, skin_id: str) -> tuple[str, bytes] | None:
    """(texture name, bytes) for a skins/mskins/pskins id."""
    tables = {n: _read_table(client_dir, n) for n in _SKIN_TABLES}
    return _read_table_skin(client_dir, tables, skin_id)


def figure_desc(client_dir: str, nif_path: str) -> str | None:
    """The monnifs description of a figure NIF (e.g. 'Saracen Female')."""
    base = os.path.splitext(os.path.basename(nif_path))[0].lower()
    base_norm = norm_name(base)
    fallback: str | None = None
    for row in _read_table(client_dir, "monnifs.csv"):
        if len(row) <= MONNIFS_NAME or not row[MONNIFS_NAME].strip():
            continue
        name = row[MONNIFS_NAME].strip().lower()
        if name == base:
            return row[MONNIFS_DESC].strip() if len(row) > MONNIFS_DESC else ""
        if fallback is None and norm_name(name) == base_norm:
            fallback = row[MONNIFS_DESC].strip() if len(row) > MONNIFS_DESC else ""
    return fallback


def resolve_figure_skins(client_dir: str, nif_path: str) -> list[SlotTexture]:
    """Resolve a figure's skin sheets -> [SlotTexture, ...] (no tints).

    Only the slots the monster actually sets (non-zero skin IDs) are returned.
    """
    base = os.path.splitext(os.path.basename(nif_path))[0].lower()
    base_norm = norm_name(base)
    tables = {n: _read_table(client_dir, n) for n in ("monnifs.csv", "monsters.csv", *_SKIN_TABLES)}

    monnifs_id = None
    fallback = None
    for row in tables["monnifs.csv"]:
        if len(row) <= MONNIFS_NAME or not row[MONNIFS_NAME].strip():
            continue
        name = row[MONNIFS_NAME].strip().lower()
        if name == base:
            monnifs_id = row[0].strip()
            break
        if fallback is None and norm_name(name) == base_norm:
            fallback = row[0].strip()
    if monnifs_id is None:
        monnifs_id = fallback
    if monnifs_id is None:
        # pets / special figures are not in monnifs: fall back to the NIF's own
        # NiSourceTexture names, searching the skin archives for them.
        return _resolve_from_nif_names(client_dir, nif_path)

    monster = None
    for row in tables["monsters.csv"]:
        if len(row) > MONSTERS_NIF and row[MONSTERS_NIF].strip() == monnifs_id:
            monster = row
            break
    if monster is None:
        return _resolve_from_nif_names(client_dir, nif_path)

    out: list[SlotTexture] = []
    for slot, idx in MONSTERS_SLOTS.items():
        skin_id = monster[idx].strip() if len(monster) > idx else ""
        if skin_id in ("", "0"):
            continue
        found = _read_table_skin(client_dir, tables, skin_id)
        if found is None:
            continue
        tex_name, data = found
        out.append(SlotTexture(slot, tex_name, data))
    return out or _resolve_from_nif_names(client_dir, nif_path)


def _nif_texture_names(nif_path: str) -> list[str]:
    """The NiSourceTexture file names referenced by a figure (may be empty)."""
    try:
        from . import nifutil

        data = nifutil.load(nif_path)
    except Exception:  # noqa: BLE001
        return []
    names = []
    for block in data.blocks:
        if type(block).__name__ == "NiSourceTexture":
            fn = getattr(block, "file_name", None)
            if fn:
                names.append(fn.decode("latin1"))
    return names


def _resolve_from_nif_names(client_dir: str, nif_path: str) -> list[SlotTexture]:
    names = _nif_texture_names(nif_path)
    if not names:
        return []
    bases = {os.path.splitext(n)[0].lower(): n for n in names}
    out: list[SlotTexture] = []
    seen: set[str] = set()
    for sub in _TEXTURE_DIRS:
        root = os.path.join(client_dir, *sub)
        if not os.path.isdir(root):
            continue
        for arch in sorted(os.listdir(root)):
            if not arch.lower().endswith(".mpk"):
                continue
            a = MpakArchive(os.path.join(root, arch))
            try:
                for entry in a.entries:
                    stem = os.path.splitext(entry)[0].lower()
                    if stem in bases and stem not in seen:
                        seen.add(stem)
                        out.append(SlotTexture("Body", bases[stem], a.read(entry)))
            finally:
                a.close()
    return out
