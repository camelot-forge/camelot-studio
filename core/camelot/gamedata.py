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
"""
from __future__ import annotations

import csv
import io
import os

from .mpak import MpakArchive

# Column indices (the CSVs carry two header rows: a group row then names).
MONNIFS_NAME = 2
MONNIFS_SET = 3  # Anim Set id -> anims.csv row (animations live there too)
MONSTERS_NIF = 2
ANIMNIFS_ID = 0
ANIMNIFS_FILE = 2  # .kfa filename
MONSTERS_SLOTS = {"Body": 3, "Head": 4, "Arms": 5, "Gloves": 6,
                  "Lbody": 7, "Legs": 8, "Boots": 9, "Cloak": 10, "Helm": 11}
SKINS_NAME = 2
SKINS_ARCHIVE = 4

_SKIN_TABLES = ("skins.csv", "mskins.csv", "pskins.csv")
_GAMEDATA = "gamedata.mpk"


def norm_name(s: str) -> str:
    """Case/separator-insensitive key for table name matching.

    The tables spell names differently from the files (`FrostGiant` in
    monnifs vs `Frost_Giant.NIF`, `Boar Keggars` vs `Boar_Keggers.nif`), so
    exact string compares miss real links.
    """
    return "".join(ch for ch in s.lower() if ch.isalnum())


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


def _resolve_from_nif_names(client_dir: str, nif_path: str) -> list[tuple[str, str, bytes]]:
    names = _nif_texture_names(nif_path)
    if not names:
        return []
    skins_dir = os.path.join(client_dir, "figures", "skins")
    if not os.path.isdir(skins_dir):
        return []
    bases = {os.path.splitext(n)[0].lower(): n for n in names}
    out: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for arch in sorted(os.listdir(skins_dir)):
        if not arch.lower().endswith(".mpk"):
            continue
        a = MpakArchive(os.path.join(skins_dir, arch))
        try:
            for entry in a.entries:
                stem = os.path.splitext(entry)[0].lower()
                if stem in bases and stem not in seen:
                    seen.add(stem)
                    out.append(("Body", bases[stem], a.read(entry)))
        finally:
            a.close()
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
