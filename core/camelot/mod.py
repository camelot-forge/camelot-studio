"""The mod deployment document a pack carries.

A pack does not ship SQL, and it does not choose ids. It declares how many
ids it needs per table, and describes its rows as columns and values with a
logical name for each row that other rows may reference.

Two consequences, and they are the reason for the shape:

- **overlap is impossible.** No mod picks an id, so two mods cannot claim
  the same one and there is no conflict policy to design;
- **a mod cannot execute arbitrary statements** against a server's database,
  which matters when the mods come from strangers.

The **client section** is the payload's list of destinations: what the mod
replaces on a player's machine (a loose file, an entry of an archive, a row
of a table), each naming the pack member that carries its bytes. The other
consumer, `camelot.clientinstall`, applies it; a server that only wants the
rows ignores it.
"""
from __future__ import annotations

import json

__all__ = [
    "CLIENT_ID_MAX",
    "CLIENT_ID_MIN",
    "DEPLOYMENT_FILE",
    "check_client_item",
    "claims",
    "client_archive_entry",
    "client_gamedata_add",
    "client_gamedata_set",
    "client_loose",
    "client_patch_items",
    "deployment",
    "deployment_bytes",
]

#: Where the document lives inside a pack, beside the target projection.
DEPLOYMENT_FILE = "targets/opendaoc/mod.json"


def deployment(mod_id: str, version: str, rows, *, priority: int = 100,
               client=None) -> dict:
    """The deployment document for a set of declarative rows.

    `rows` are mappings with a `table`, a logical `id` and a `values`
    mapping. The allocations are counted from them rather than passed in, so
    a declared count can never disagree with what is actually written.

    `client` is the payload's item list (`client_loose`,
    `client_archive_entry`, `client_gamedata_set`, `client_gamedata_add`).
    It is validated here, so a pack whose own destinations contradict each
    other never leaves the build.
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
    claims(client_items)   # validates; consumers re-derive the claims

    return {
        "mod": {"id": mod_id, "version": version, "priority": priority},
        "requires": [],
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
# table editor would have to (see `camelot.clientinstall` and the design in
# the workspace, `notes/phase5-client-assets.md`).

#: The band a mod-added client id must live in: **1..2499** — every id the
#: client can still resolve. The ceiling is 2500 because the client's model
#: table is 2500 entries wide (measured 2026-10-10 by reading the running
#: client's memory, `notes/client-memwatch.py`): a row at 2500
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
CLIENT_ID_MIN = 1
CLIENT_ID_MAX = 2499

#: A row's id is its identity; row ops may not write it (kept in step with
#: `gamedata.ID_COLUMN`, the column an `add` puts its id in).
CLIENT_ID_COLUMN = 0


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


def client_gamedata_add(table: str, id_: int, values, *, columns) -> dict:
    """Add one table row; *id* must live in the reserved band (D5)."""
    item = {
        "kind": "gamedata_row",
        "action": "add",
        "table": _table_name(table),
        "id": _row_id(id_),
        "columns": _columns_map(columns),
        "values": _values_map(values),
    }
    if not CLIENT_ID_MIN <= item["id"] <= CLIENT_ID_MAX:
        raise ValueError(
            f"added id {item['id']} is outside the reserved band "
            f"{CLIENT_ID_MIN}..{CLIENT_ID_MAX}"
        )
    _check_row_op(item)
    return item


def check_client_item(item, *, band: bool = True) -> None:
    """Validate one resolved payload item, as a pack would carry it.

    ``band=False`` skips the reserved-band rule. The band is a build-time
    concern: reading back what an installed mod already carries must survive
    a later band change (the ceiling probe that measured it relies on this,
    and so would any pack built before the constants moved).
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
        elif band and not CLIENT_ID_MIN <= _row_id(item["id"]) <= CLIENT_ID_MAX:
            raise ValueError(
                f"added id {item['id']} is outside the reserved band "
                f"{CLIENT_ID_MIN}..{CLIENT_ID_MAX}"
            )
        _check_row_op(item)
    else:
        raise ValueError(f"unknown client item kind {kind!r}")


def claims(items, *, band: bool = True) -> list[str]:
    """The destinations a set of payload items claims, lower-cased to group.

    Validates every item on the way, so an untrusted pack cannot be planned
    without its items holding together. Two items of one mod claiming one
    destination is refused: a pack must be unambiguous about its own content.
    ``band=False`` is for items already installed (see `check_client_item`).
    """
    out: list[str] = []
    for item in items:
        check_client_item(item, band=band)
        kind = item["kind"]
        if kind == "loose":
            out.append(f"loose:{item['path'].lower()}")
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


def client_patch_items(patch: dict, tables) -> list[dict]:
    """Resolve an authoring `client.patch.json` body against a client.

    *tables* is the client's gamedata, duck-typed: ``column_index(table,
    name)``, ``column_name(table, index)``, ``has_row(table, id)`` and
    ``row_values(table, id, names)`` (`camelot.gamedata.ClientTables`
    implements it). This is where a mod's column names become indices, its
    `expect` values are captured, and its ids are bounded -- at build time,
    where the author sees the refusal. A column name that repeats in the
    header (`Body` appears four times in `monsters.csv`) is written
    ``Body@3``: name first, the index that disambiguates it second.
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
            items.append(client_gamedata_add(table, id_, values, columns=columns))
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
