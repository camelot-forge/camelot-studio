"""Camelot Studio — DAoC gamedata tables and figure texture resolution.

DAoC figures carry no texture in the NIF (except rare embedded cases): the
texture is resolved at runtime from the data tables packed in ``gamedata.mpk``:

    figure NIF  ->  monnifs.csv (Name = NIF basename)  ->  monnifs ID
                ->  monsters.csv (# = monnifs ID)      ->  skin IDs per slot
                ->  skins/mskins/pskins.csv (ID)       ->  texture name + archive
                ->  figures/skins/skinNNN.mpk          ->  the .dds bytes

The archive number is the skin table's ``Num`` column (e.g. Boar body skin
109 -> Num 3 -> ``figures/skins/skin003.mpk`` -> ``boar.dds``). Texture names
in the tables use ``.tga`` while the archive entry is ``.dds``; matching is
done on the basename without extension.
"""
from __future__ import annotations

import csv
import io
import os

from .mpak import MpakArchive

# Column indices (the CSVs carry two header rows: a group row then names).
MONNIFS_NAME = 2
MONSTERS_NIF = 2
MONSTERS_SLOTS = {"Body": 3, "Head": 4, "Arms": 5, "Gloves": 6,
                  "Lbody": 7, "Legs": 8, "Boots": 9, "Cloak": 10, "Helm": 11}
SKINS_NAME = 2
SKINS_ARCHIVE = 4

_SKIN_TABLES = ("skins.csv", "mskins.csv", "pskins.csv")
_GAMEDATA = "gamedata.mpk"


def _read_table(client_dir: str, name: str) -> list[list[str]]:
    path = os.path.join(client_dir, _GAMEDATA)
    a = MpakArchive(path)
    try:
        if name.lower() not in a.entries:
            return []
        raw = a.read(name)
    finally:
        a.close()
    return list(csv.reader(io.StringIO(raw.decode("latin-1"))))


def _find_skin(skin_id: str, tables: dict[str, list[list[str]]]):
    for name in _SKIN_TABLES:
        table = tables.get(name)
        if not table:
            continue
        for row in table:
            if len(row) > SKINS_ARCHIVE and row[0].strip() == skin_id:
                return row[SKINS_NAME].strip(), row[SKINS_ARCHIVE].strip()
    return None


def resolve_figure_skins(client_dir: str, nif_path: str) -> list[tuple[str, str, bytes]]:
    """Resolve a figure's skin textures -> [(slot, name, dds_bytes), ...].

    Only the slots the monster actually sets (non-zero skin IDs) are returned.
    """
    base = os.path.splitext(os.path.basename(nif_path))[0].lower()
    tables = {n: _read_table(client_dir, n) for n in ("monnifs.csv", "monsters.csv", *_SKIN_TABLES)}

    monnifs_id = None
    for row in tables["monnifs.csv"]:
        if len(row) > MONNIFS_NAME and row[MONNIFS_NAME].strip().lower() == base:
            monnifs_id = row[0].strip()
            break
    if monnifs_id is None:
        return []

    monster = None
    for row in tables["monsters.csv"]:
        if len(row) > MONSTERS_NIF and row[MONSTERS_NIF].strip() == monnifs_id:
            monster = row
            break
    if monster is None:
        return []

    out: list[tuple[str, str, bytes]] = []
    for slot, idx in MONSTERS_SLOTS.items():
        skin_id = monster[idx].strip() if len(monster) > idx else ""
        if skin_id in ("", "0"):
            continue
        found = _find_skin(skin_id, tables)
        if not found:
            continue
        tex_name, archive_num = found
        data = _read_skin_archive(client_dir, archive_num, tex_name)
        if data is not None:
            out.append((slot, tex_name, data))
    return out


def _read_skin_archive(client_dir: str, archive_num: str, texture_name: str) -> bytes | None:
    try:
        num = int(archive_num)
    except ValueError:
        return None
    path = os.path.join(client_dir, "figures", "skins", f"skin{num:03d}.mpk")
    if not os.path.exists(path):
        return None
    base = os.path.splitext(texture_name)[0].lower()
    a = MpakArchive(path)
    try:
        for entry in a.entries:
            if os.path.splitext(entry)[0].lower() == base:
                return a.read(entry)
    finally:
        a.close()
    return None
