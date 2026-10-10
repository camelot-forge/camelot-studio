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
from dataclasses import dataclass
from typing import NamedTuple

from .mpak import MpakArchive


class SlotTexture(NamedTuple):
    """One sheet to embed: export slot, file name, DDS bytes, optional tint.

    The tint is the engine's packed per-slot colour (``fig3tints.csv``):
    the viewer *multiplies* the sheet by it, which is how the client paints
    skin tones and most hair colours. ``double_sided`` carries the owning
    fig3 piece's rendering flag when the table provides one.
    """

    slot: str
    name: str
    data: bytes
    tint: tuple[int, int, int] | None = None
    double_sided: bool | None = None


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


_RACE_ROW_MARKERS = ("cata", "stone", "shade", "mummy", "undead", "old", "king", "skeleton")
_REALM_PREFIXES = {"albion", "midgard", "hibernia", "hibernian"}


def figure_name_for_desc(client_dir: str, desc: str) -> str | None:
    """A monnifs figure Name matching a race description (`Saracen Female`).

    `monnifs.csv` spells the player rows differently from fig3map: word order
    varies ("Male Norse" vs "Norse Male") and the Hibernian rows carry a
    realm prefix ("Hibernian Elf male"). Word-set equality, plus a realm-only
    prefix tolerance and a player-row preference, picks the classic player
    figure, whose animation set is the player idle.
    """
    target = frozenset(norm_name(word) for word in desc.split() if word)
    fallback = None
    for row in _read_table(client_dir, "monnifs.csv")[2:]:
        if len(row) <= MONNIFS_NAME or not row[MONNIFS_NAME].strip():
            continue
        row_desc = row[MONNIFS_DESC].strip() if len(row) > MONNIFS_DESC else ""
        if not row_desc:
            continue
        words = frozenset(norm_name(word) for word in row_desc.split() if word)
        if words != target and not (target <= words and words - target <= _REALM_PREFIXES):
            continue
        name = row[MONNIFS_NAME].strip()
        if not any(marker in row_desc.lower() for marker in _RACE_ROW_MARKERS):
            return name
        if fallback is None:
            fallback = name
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


# --- editing: the row ops a client payload applies ---------------------------
#
# A payload edits tables inside ``gamedata.mpk`` a row at a time rather than
# shipping a whole file, so two mods editing different fields of one row (or
# adding different rows) compose. Fidelity is the whole game: the client reads
# latin-1 files with CRLF endings and stops reading a table at a blank line
# (measured -- a split/join round-trip inserted one, and the server then had a
# mob no client could see, `notes`). Every edit therefore
# keeps the untouched lines byte-identical and re-encodes only what it touched.

#: The archive that carries the tables; a payload's row ops land in it.
GAMEDATA_ARCHIVE = _GAMEDATA

#: Every table opens with two header rows: a group row, then the names.
HEADER_ROWS = 2

#: Row ids live in the first column of every table the row ops can edit.
ID_COLUMN = 0


class TableError(ValueError):
    """Raised when a table cannot be parsed, resolved or edited."""


def _find_row(rows: list[list[str]], id_) -> int | None:
    wanted = str(id_).strip()
    for index, row in enumerate(rows[HEADER_ROWS:], start=HEADER_ROWS):
        if row and row[ID_COLUMN].strip() == wanted:
            return index
    return None


def _encode_line(fields) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(fields)
    try:
        return buffer.getvalue().encode("latin-1")
    except UnicodeEncodeError as error:
        raise TableError(
            f"a value cannot be written in the table's latin-1 encoding: {error}"
        ) from error


@dataclass
class Table:
    """One gamedata CSV, kept beside the bytes it was parsed from.

    ``lines`` holds each line without its terminator; editing replaces the
    touched entries in ``lines`` and :func:`encode_table` joins them back, so
    an untouched line can never drift, however the original was spelled.
    """

    name: str
    raw: bytes
    lines: list[bytes]
    rows: list[list[str]]
    newline: bytes
    trailing: bool

    @property
    def names(self) -> list[str]:
        """The header row that carries the field names."""
        return self.rows[HEADER_ROWS - 1] if len(self.rows) >= HEADER_ROWS else []

    @property
    def width(self) -> int:
        """The field count of the table's own rows.

        An added row must be exactly this wide: the client stops reading a
        table at a line shorter than its neighbours, the same way it stops at
        a blank one (measured, `notes` Part 4 P5-F).
        """
        return max([0] + [len(row) for row in self.rows])

    def column_index(self, name: str) -> int:
        """The index of *name* in the header, or raise.

        Matching is case-insensitive on the exact spelling: the tables carry
        names like ``#``, ``Expansion `` (trailing space), and ``Only``
        twice, so an ambiguous name must be given by index instead.
        """
        wanted = str(name).strip().casefold()
        matches = [i for i, n in enumerate(self.names) if n.strip().casefold() == wanted]
        if not matches:
            raise TableError(f"{self.name}: no column named {name!r}")
        if len(matches) > 1:
            raise TableError(
                f"{self.name}: column {name!r} is ambiguous (indices {matches}); "
                "give the index"
            )
        return matches[0]

    def row_index(self, id_) -> int | None:
        """The first row carrying *id* in the id column, or None."""
        return _find_row(self.rows, id_)

    def row_values(self, index: int, columns: dict) -> dict:
        row = self.rows[index]
        return {name: (row[idx] if len(row) > idx else "") for name, idx in columns.items()}


def parse_table(name: str, raw: bytes) -> Table:
    """Parse a table, remembering how to write it back byte-faithfully."""
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    remainder = raw.replace(b"\r\n", b"") if newline == b"\r\n" else raw.replace(b"\n", b"")
    if b"\n" in remainder or b"\r" in remainder:
        raise TableError(f"{name}: mixed line endings")
    trailing = raw.endswith(newline)
    body = raw[: -len(newline)] if trailing else raw
    lines = body.split(newline) if body else []
    rows: list[list[str]] = []
    for line in lines:
        try:
            row = next(csv.reader([line.decode("latin-1")]), [])
        except csv.Error as error:
            raise TableError(f"{name}: unreadable row: {error}") from error
        rows.append([str(field) for field in row])
    return Table(name=name, raw=raw, lines=lines, rows=rows, newline=newline, trailing=trailing)


def encode_table(table: Table) -> bytes:
    """The table's bytes: untouched lines exactly as parsed."""
    if not table.lines:
        return table.newline if table.trailing else b""
    out = table.newline.join(table.lines)
    if table.trailing:
        out += table.newline
    return out


def apply_row_ops(table: Table, ops) -> Table:
    """Apply pack row ops in order, checking each `expect` against the image.

    Each op is a resolved payload item (:mod:`camelot.mod`): ``set`` replaces
    named columns of an existing row; ``add`` appends one row, writing the id
    into the id column. ``expect`` is compared before the op lands, so a pack
    built against a different client (or reading an earlier mod's edit) is
    refused with both values named rather than applied blind.
    """
    lines = list(table.lines)
    rows = [list(row) for row in table.rows]
    trailing = table.trailing
    for op in ops:
        action = op.get("action")
        id_ = int(op["id"])
        columns = {name: int(index) for name, index in (op.get("columns") or {}).items()}
        values = {name: str(value) for name, value in (op.get("values") or {}).items()}
        expect = {name: str(value) for name, value in (op.get("expect") or {}).items()}
        for name in list(values) + list(expect):
            if name not in columns:
                raise TableError(f"{table.name}#{id_}: no column index for {name!r}")
        if any(index == ID_COLUMN for index in columns.values()):
            raise TableError(f"{table.name}#{id_}: {action} may not touch the id column")
        if action == "set":
            index = _find_row(rows, id_)
            if index is None:
                raise TableError(f"{table.name}: no row with id {id_}")
            row = rows[index]
            for name, wanted in expect.items():
                current = row[columns[name]] if len(row) > columns[name] else ""
                if current != wanted:
                    raise TableError(
                        f"{table.name}#{id_}.{name}: expected {wanted!r}, found {current!r}"
                    )
            width = max([len(row)] + [columns[name] + 1 for name in values])
            row.extend([""] * (width - len(row)))
            for name, value in values.items():
                row[columns[name]] = value
            lines[index] = _encode_line(row)
        elif action == "add":
            if _find_row(rows, id_) is not None:
                raise TableError(f"{table.name}: row {id_} already exists")
            width = max([table.width, 1] + [index + 1 for index in columns.values()])
            row = [""] * width
            row[ID_COLUMN] = str(id_)
            for name, value in values.items():
                row[columns[name]] = value
            rows.append(row)
            lines.append(_encode_line(row))
            trailing = True
        else:
            raise TableError(f"{table.name}#{id_}: unknown row action {action!r}")
    return Table(name=table.name, raw=table.raw, lines=lines, rows=rows,
                 newline=table.newline, trailing=trailing)


def edit_table(raw: bytes, name: str, ops) -> bytes:
    """Apply row ops to one table's bytes, byte-faithful outside the edits."""
    return encode_table(apply_row_ops(parse_table(name, raw), ops))


def read_table_bytes(client_dir: str, name: str) -> bytes:
    """The exact bytes of one table inside the client's gamedata.mpk."""
    archive = MpakArchive(os.path.join(client_dir, GAMEDATA_ARCHIVE))
    try:
        if name.lower() not in archive.entries:
            raise TableError(f"{GAMEDATA_ARCHIVE}: no table named {name!r}")
        return archive.read(name)
    finally:
        archive.close()


class ClientTables:
    """A client's tables, read for the build side (:mod:`camelot.mod`).

    The builder resolves column names, checks ids and captures the values a
    payload expects; this is the object it talks to, kept thin so tests can
    put a fake in its place.
    """

    def __init__(self, client_dir: str):
        self.client_dir = client_dir
        self._tables: dict[str, Table] = {}

    def table(self, name: str) -> Table:
        key = name.lower()
        if key not in self._tables:
            self._tables[key] = parse_table(name, read_table_bytes(self.client_dir, name))
        return self._tables[key]

    def column_index(self, table_name: str, column: str) -> int:
        return self.table(table_name).column_index(column)

    def column_name(self, table_name: str, index: int) -> str:
        table = self.table(table_name)
        if not 0 <= index < len(table.names):
            raise TableError(f"{table_name}: no column at index {index}")
        return table.names[index]

    def has_row(self, table_name: str, id_) -> bool:
        return self.table(table_name).row_index(id_) is not None

    def row_values(self, table_name: str, id_, columns) -> dict:
        table = self.table(table_name)
        index = table.row_index(id_)
        if index is None:
            raise TableError(f"{table_name}: no row with id {id_}")
        return table.row_values(index, {name: table.column_index(name) for name in columns})
