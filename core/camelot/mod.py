"""The deployment document a pack carries.

A pack does not ship SQL, and it does not choose ids. It declares how many
ids it needs per table, and describes its rows as columns and values with a
logical name for each row that other rows may reference.

Two consequences, and they are the reason for the shape:

- **overlap is impossible.** No writer picks an id, so two rows cannot claim
  the same one and there is no conflict policy to design;
- **a pack cannot execute arbitrary statements** against a server's
  database.

Both rules were written when a pack was a mod from a stranger; since
2026-10-14 the product is one server's content suite (one project, numbered
releases), so what they buy is smaller — but they are cheaper to keep than to
prove unnecessary.

The **client section** is the payload's list of destinations: what the mod
replaces on a player's machine (a loose file, an entry of an archive, a row
of a table, or the client's own binary, patched in place), each naming the
pack member that carries its bytes — except a patch, whose bytes are the
recipe itself, never a redistributed file. The other consumer,
`camelot.clientinstall`, applies it; a server that only wants the rows
ignores it.
"""
from __future__ import annotations

import json
import re

__all__ = [
    "CAPABILITY_PREFIX",
    "CLIENT_ID_MAX",
    "CLIENT_ID_MIN",
    "DEPLOYMENT_FILE",
    "MOD_ID_RE",
    "capability_ceiling",
    "check_client_item",
    "check_mod_id",
    "claims",
    "client_archive_entry",
    "client_gamedata_add",
    "client_gamedata_set",
    "client_loose",
    "client_patch",
    "client_patch_items",
    "deployment",
    "deployment_bytes",
    "effective_ceiling",
    "is_capability",
]

#: Where the document lives inside a pack, beside the target projection.
DEPLOYMENT_FILE = "targets/opendaoc/mod.json"


def deployment(mod_id: str, version: str, rows, *, priority: int = 100,
               client=None, requires=()) -> dict:
    """The deployment document for a set of declarative rows.

    `rows` are mappings with a `table`, a logical `id` and a `values`
    mapping. The allocations are counted from them rather than passed in, so
    a declared count can never disagree with what is actually written.

    `client` is the payload's item list (`client_loose`,
    `client_archive_entry`, `client_gamedata_set`, `client_gamedata_add`,
    `client_patch`). It is validated here, so a pack whose own destinations
    contradict each other never leaves the build.

    `requires` names what the pack needs: other mods, and the client
    capabilities its payload grants or relies on
    (`client.modelids.<n>`). It travels in the document and is what the band
    of a `client_gamedata_add` is checked against (`effective_ceiling`).
    """
    cleaned: list[dict] = []
    counts: dict[str, int] = {}
    for row in rows:
        table = row.get("table")
        logical_id = row.get("id")
        if not table:
            raise ValueError("a row must name the table it belongs to")
        if not logical_id:
            raise ValueError("a row must have a logical id")
        cleaned.append({
            "table": table,
            "id": logical_id,
            "values": dict(row.get("values") or {}),
        })
        counts[table] = counts.get(table, 0) + 1

    client_items = [dict(item) for item in (client or [])]
    names = [str(name) for name in requires]
    # validates; consumers re-derive the claims, under the ceiling the pack's
    # own declarations grant.
    claims(client_items, ceiling=effective_ceiling(names, client_items))

    return {
        "mod": {"id": mod_id, "version": version, "priority": priority},
        "requires": names,
        "allocations": [
            {"table": table, "count": counts[table]} for table in sorted(counts)
        ],
        "rows": cleaned,
        "client": client_items,
    }


def deployment_bytes(doc: dict) -> bytes:
    """Canonical JSON, so two builds of one corpus produce the same bytes."""
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# --- the client half: destinations a payload replaces ------------------------
#
# An item names one destination and the pack member carrying its bytes. Rows
# are edited a field at a time rather than replaced whole, so two mods editing
# different fields of one row compose; nothing here touches a byte the way a
# table editor would have to (see `camelot.clientinstall`).

#: The band a mod-added client id must live in: **1..2499** — every id the
#: client can still resolve. The ceiling is 2500 because the client's model
#: table is 2500 entries wide (measured 2026-10-10 by reading the running
#: client's memory): a row at 2500
#: makes the client **stop parsing the table**, dropping every later row —
#: which is why ids above the ceiling never rendered. The earlier readings (a
#: signed 16-bit ceiling, then a 12-bit one) were both wrong.
#:
#: The floor is 1, not the retail maximum: the free ids *inside* the shipped
#: range (the table's gaps — 2069 measured readable, appended out of id order
#: even) are usable too, and the real rule is "the id must be free".
#: `modbuild` enforces that against the reference client and refuses an id
#: the shipped server content references (`camelot.modelusage`) — such an id
#: would change live content instead of adding to it. `camelot client
#: free-ids` lists the pool: ~253 gap ids plus ~674 shipped rows nothing
#: references.
#:
#: The ceiling is **liftable**, and the ladder lifted it: a pack declares
#: `requires: ["client.modelids.32768"]`, a client that carries the patch
#: (`camelot.clientpatch`) accepts ids to 32767, and the ceiling becomes the
#: pack's own declarations (`capability_ceiling`).
CLIENT_ID_MIN = 1
CLIENT_ID_MAX = 2499

#: A requirement naming the client itself rather than another mod; the
#: installer verifies the client really carries it. The prefix is reserved:
#: a mod id may not begin with it.
CAPABILITY_PREFIX = "client."

#: What a mod id may look like; it becomes a directory name in the store.
MOD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

#: A row's id is its identity; row ops may not write it (kept in step with
#: `gamedata.ID_COLUMN`, the column an `add` puts its id in).
CLIENT_ID_COLUMN = 0


# --- capabilities and the id ceiling ------------------------------------------

def is_capability(name) -> bool:
    """True when a requirement names the client itself, not another mod."""
    return isinstance(name, str) and name.startswith(CAPABILITY_PREFIX)


def check_mod_id(mod_id, where: str) -> None:
    """Refuse anything that is not a plain mod id (capabilities are separate)."""
    if not isinstance(mod_id, str) or not MOD_ID_RE.match(mod_id) or is_capability(mod_id):
        raise ValueError(
            f"{where}: invalid mod id {mod_id!r} "
            f"(the {CAPABILITY_PREFIX!r} prefix is reserved for capabilities)"
        )


def capability_ceiling(requires) -> int | None:
    """The highest client id a set of capability names allows, or None.

    Today only ``client.modelids.<n>`` moves a ceiling: the client's model
    table is `<n>` entries wide, so the highest usable id is `<n> - 1`. An
    unknown capability changes nothing here — it is refused where it can be
    verified, by the installer.
    """
    ceilings = []
    prefix = f"{CAPABILITY_PREFIX}modelids."
    for name in requires or []:
        text = str(name)
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            ceilings.append(int(text[len(prefix):]) - 1)
    return max(ceilings) if ceilings else None


def effective_ceiling(requires, items) -> int:
    """The ceiling a pack's own declarations grant (CLIENT_ID_MAX without any).

    A patch *provides* what its ``provides`` names, so a pack that carries the
    model-id patch may itself use the raised band without requiring it.
    """
    names = [str(name) for name in (requires or [])]
    names += [
        str(capability)
        for item in items or []
        if isinstance(item, dict) and item.get("kind") == "patch"
        for capability in item.get("provides") or []
    ]
    return capability_ceiling(names) or CLIENT_ID_MAX


def _client_path(value, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{what} must be a non-empty path")
    normalised = value.strip().replace("\\", "/").strip("/")
    if not normalised or ".." in normalised.split("/"):
        raise ValueError(f"{what} must be a path inside the client, got {value!r}")
    return normalised


def _member_path(value) -> str:
    member = _client_path(value, "member")
    if not member.startswith("client/"):
        raise ValueError(f"a payload member lives under client/, got {value!r}")
    return member


def _table_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("a row op must name its table")
    return value.strip()


def _row_id(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"row id must be an integer, got {value!r}") from None


def _columns_map(columns) -> dict:
    if not isinstance(columns, dict) or not columns:
        raise ValueError("a row op needs the column indices it resolved to")
    resolved: dict[str, int] = {}
    for name, index in columns.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"a column name must be a non-empty string, got {name!r}")
        try:
            resolved[name] = int(index)
        except (TypeError, ValueError):
            raise ValueError(
                f"column {name!r}: index must be an integer, got {index!r}"
            ) from None
        if resolved[name] < 0:
            raise ValueError(f"column {name!r}: negative index {index!r}")
    return resolved


def _values_map(values) -> dict:
    if not isinstance(values, dict) or not values:
        raise ValueError("a row op needs values to write")
    return {str(name): str(value) for name, value in values.items()}


def _check_row_op(item: dict) -> None:
    covered = set(item["values"]) | set(item.get("expect") or {})
    missing = covered - set(item["columns"])
    if missing:
        raise ValueError(
            f"{item['table']}#{item['id']}: no column index for {sorted(missing)}"
        )
    if CLIENT_ID_COLUMN in item["columns"].values():
        raise ValueError(
            f"{item['table']}#{item['id']}: cannot write the id column; "
            "a row's id is its identity"
        )


def client_loose(path: str, *, member: str | None = None) -> dict:
    """One loose client file a payload replaces (a figure, an animation)."""
    path = _client_path(path, "path")
    return {"kind": "loose", "path": path, "member": _member_path(member or f"client/{path}")}


def client_archive_entry(archive: str, entry: str, *, member: str | None = None) -> dict:
    """One file inside a client archive a payload replaces (a texture)."""
    archive = _client_path(archive, "archive")
    entry = _client_path(entry, "entry")
    return {
        "kind": "archive_entry",
        "archive": archive,
        "entry": entry,
        "member": _member_path(member or f"client/entries/{archive}/{entry}"),
    }


def client_gamedata_set(table: str, id_: int, values, *, expect, columns) -> dict:
    """Replace named fields of one existing table row.

    `expect` carries the values the row had when the pack was built; the
    installer checks it before the edit lands, which is what catches a client
    that is not the one the pack was built against.
    """
    item = {
        "kind": "gamedata_row",
        "action": "set",
        "table": _table_name(table),
        "id": _row_id(id_),
        "columns": _columns_map(columns),
        "values": _values_map(values),
        "expect": _values_map(expect) if expect else {},
    }
    _check_row_op(item)
    return item


def client_gamedata_add(table: str, id_: int, values, *, columns,
                        ceiling: int = CLIENT_ID_MAX) -> dict:
    """Add one table row; *id* must live in the band the pack may use (D5).

    *ceiling* is the highest id the pack's declared capabilities allow
    (`capability_ceiling`); the default is the no-patch band.
    """
    item = {
        "kind": "gamedata_row",
        "action": "add",
        "table": _table_name(table),
        "id": _row_id(id_),
        "columns": _columns_map(columns),
        "values": _values_map(values),
    }
    if not CLIENT_ID_MIN <= item["id"] <= ceiling:
        hint = "" if ceiling != CLIENT_ID_MAX else (
            "; a higher band needs `requires: [\"client.modelids.<n>\"]` "
            "and a client that carries the patch"
        )
        raise ValueError(
            f"added id {item['id']} is outside the reserved band "
            f"{CLIENT_ID_MIN}..{ceiling}{hint}"
        )
    _check_row_op(item)
    return item


def client_patch(file: str, *, provides, expect, write, append_section=None) -> dict:
    """One binary patch to a client file, as a recipe of offsets and bytes.

    The recipe is data, not code: ``expect`` states the bytes the pristine
    file must hold at each address (the refusal that catches a wrong client
    build — or an already-patched one), ``write`` the bytes to put there, and
    ``append_section`` the PE section to append and point them at. Offsets are
    virtual addresses, hex with an explicit ``0x``; values are hex byte
    strings. The installer checks ``expect`` before it writes a byte, and
    uninstalling restores the stored original exactly, like every other
    destination.

    ``provides`` names the client capabilities the patch grants
    (``client.modelids.<n>``); it is required, because a patch changes the
    client itself and a pack must be attributable for it. Recipes are
    generated against a pristine client (`camelot.clientpatch`), not written
    by hand, though the format allows it.
    """
    item = {
        "kind": "patch",
        "file": _client_path(file, "file"),
        "provides": _capability_names(provides),
        "expect": _offset_bytes(expect, "expect"),
        "write": _offset_bytes(write, "write"),
    }
    if append_section is not None:
        item["append_section"] = _append_section(append_section)
    _check_patch(item)
    return item


def _capability_names(values) -> list[str]:
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("a patch must declare the capability it provides")
    names = []
    for value in values:
        if not is_capability(value):
            raise ValueError(
                f"a capability must start with {CAPABILITY_PREFIX!r}, got {value!r}"
            )
        names.append(str(value))
    return sorted(set(names))


def _offset_bytes(values, what: str) -> dict:
    """A map of virtual address -> hex bytes, canonicalised for the JSON.

    Offsets are hex with an explicit ``0x`` (a bare number would be ambiguous
    between decimal and hex); values are even-length hex strings.
    """
    if not isinstance(values, dict) or not values:
        raise ValueError(f"a patch needs a non-empty {what!r} map")
    out: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.startswith("0x"):
            raise ValueError(f"{what}: {key!r} must be a hex offset with a 0x prefix")
        try:
            offset = int(key, 16)
        except ValueError:
            raise ValueError(f"{what}: {key!r} is not a hex offset") from None
        if not isinstance(value, str) or not value or len(value) % 2:
            raise ValueError(
                f"{what} {key}: expected an even-length hex string, got {value!r}"
            )
        try:
            bytes.fromhex(value)
        except ValueError:
            raise ValueError(f"{what} {key}: {value!r} is not hex") from None
        out[f"0x{offset:x}"] = value.lower()
    return out


def _append_section(value) -> dict:
    if not isinstance(value, dict):
        raise TypeError("append_section must be an object")
    name = value.get("name")
    if (not isinstance(name, str) or not name or len(name) > 8
            or not name.isascii() or not name.isprintable()):
        raise ValueError(
            f"append_section: the name must be 1..8 printable ASCII characters, "
            f"got {name!r}"
        )
    try:
        size = int(value.get("size"))
        vaddr = int(value.get("vaddr"))
    except (TypeError, ValueError):
        raise ValueError("append_section: 'size' and 'vaddr' must be integers") from None
    if size <= 0 or size % 0x1000:
        raise ValueError("append_section: size must be a positive multiple of 0x1000")
    if vaddr <= 0 or vaddr % 0x1000:
        raise ValueError("append_section: vaddr must be a positive multiple of 0x1000")
    return {"name": name, "size": size, "vaddr": vaddr}


def _check_patch(item: dict) -> None:
    _client_path(item.get("file"), "file")
    _capability_names(item.get("provides"))
    _offset_bytes(item.get("expect"), "expect")
    _offset_bytes(item.get("write"), "write")
    if item.get("append_section") is not None:
        _append_section(item["append_section"])


def check_client_item(item, *, ceiling: int | None = CLIENT_ID_MAX) -> None:
    """Validate one resolved payload item, as a pack would carry it.

    ``ceiling=None`` skips the band rule. The band is a build-time concern:
    reading back what an installed mod already carries must survive a later
    band change (the ceiling probe that measured it relies on this, and so
    would any pack built before the constants moved).
    """
    if not isinstance(item, dict):
        raise TypeError(f"a client item must be an object, got {item!r}")
    kind = item.get("kind")
    if kind == "loose":
        _client_path(item.get("path"), "path")
        _member_path(item.get("member"))
    elif kind == "archive_entry":
        _client_path(item.get("archive"), "archive")
        _client_path(item.get("entry"), "entry")
        _member_path(item.get("member"))
    elif kind == "patch":
        _check_patch(item)
    elif kind == "gamedata_row":
        action = item.get("action")
        if action not in ("set", "add"):
            raise ValueError(f"{item.get('table')!r}: unknown row action {action!r}")
        _table_name(item.get("table"))
        _row_id(item.get("id"))
        _columns_map(item.get("columns"))
        _values_map(item.get("values"))
        if action == "set":
            if item.get("expect"):
                _values_map(item["expect"])
        elif ceiling is not None and not CLIENT_ID_MIN <= _row_id(item["id"]) <= ceiling:
            raise ValueError(
                f"added id {item['id']} is outside the reserved band "
                f"{CLIENT_ID_MIN}..{ceiling}"
            )
        _check_row_op(item)
    else:
        raise ValueError(f"unknown client item kind {kind!r}")


def claims(items, *, ceiling: int | None = CLIENT_ID_MAX) -> list[str]:
    """The destinations a set of payload items claims, lower-cased to group.

    Validates every item on the way, so an untrusted pack cannot be planned
    without its items holding together. Two items of one mod claiming one
    destination is refused: a pack must be unambiguous about its own content.
    A whole client file claims ``file:<path>`` — a loose replacement and a
    binary patch of one file are one destination, so they cannot silently
    layer. ``ceiling=None`` is for items already installed (see
    `check_client_item`).
    """
    out: list[str] = []
    for item in items:
        check_client_item(item, ceiling=ceiling)
        kind = item["kind"]
        if kind == "loose":
            out.append(f"file:{item['path'].lower()}")
        elif kind == "patch":
            out.append(f"file:{item['file'].lower()}")
        elif kind == "archive_entry":
            out.append(f"entry:{item['archive'].lower()}#{item['entry'].lower()}")
        elif item["action"] == "add":
            out.append(f"row:{item['table'].lower()}#{item['id']}")
        else:
            for index in sorted(set(item["columns"].values())):
                out.append(f"row:{item['table'].lower()}#{item['id']}#{index}")
    duplicated = sorted({claim for claim in out if out.count(claim) > 1})
    if duplicated:
        raise ValueError(f"two client items claim the same destination: {duplicated}")
    return out


def client_patch_items(patch: dict, tables, *, ceiling: int = CLIENT_ID_MAX) -> list[dict]:
    """Resolve an authoring `client.patch.json` body against a client.

    *tables* is the client's gamedata, duck-typed: ``column_index(table,
    name)``, ``column_name(table, index)``, ``has_row(table, id)`` and
    ``row_values(table, id, names)`` (`camelot.gamedata.ClientTables`
    implements it). This is where a mod's column names become indices, its
    `expect` values are captured, and its ids are bounded -- at build time,
    where the author sees the refusal. A column name that repeats in the
    header (`Body` appears four times in `monsters.csv`) is written
    ``Body@3``: name first, the index that disambiguates it second.

    ``ceiling`` is the band the pack's declared capabilities allow
    (`camelot.mod.capability_ceiling`); `binary` specs are resolved by the
    builder, which knows the client's binary.
    """
    if not isinstance(patch, dict):
        raise TypeError("a client patch must be an object")
    items: list[dict] = []
    for spec in patch.get("archive") or []:
        items.append(client_archive_entry(
            spec["archive"], spec["entry"], member=spec.get("member"),
        ))
    for op in patch.get("gamedata") or []:
        table = op["table"]
        for id_text, values in (op.get("set") or {}).items():
            id_ = _row_id(id_text)
            names = list(values)
            columns = {name: _resolve_column(tables, table, name) for name in names}
            expect = tables.row_values(table, id_, names)
            items.append(client_gamedata_set(
                table, id_, values, expect=expect, columns=columns,
            ))
        for add in op.get("add") or []:
            id_ = _row_id(add["id"])
            values = add["values"]
            columns = {name: _resolve_column(tables, table, name) for name in values}
            if tables.has_row(table, id_):
                raise ValueError(f"{table}: id {id_} already exists; `add` needs a free id")
            items.append(client_gamedata_add(
                table, id_, values, columns=columns, ceiling=ceiling,
            ))
    return items


def _resolve_column(tables, table: str, key: str) -> int:
    """One authoring column key -> its index (`Name`, or `Body@3`).

    The second form exists because the client's headers repeat names
    (`Body` four times in `monsters.csv`); it checks that the index really
    carries that name, so a misnumbered escape hatch is still refused.
    """
    if "@" not in key:
        return tables.column_index(table, key)
    name, _, text = key.rpartition("@")
    try:
        index = int(text)
    except ValueError:
        raise ValueError(f"{table}: {key!r} needs a numeric index after '@'") from None
    actual = tables.column_name(table, index)
    if actual.strip().casefold() != name.strip().casefold():
        raise ValueError(f"{table}: {key!r} names {actual!r}")
    return index
