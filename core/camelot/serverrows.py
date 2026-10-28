"""The server's world rows — mobs, statics, doors and patrols — as data.

Spec §5: the daemon reads the server's *own* MySQL behind a flag, and without
it the viewer works from an exported snapshot; measured data travels, a live
connection is a convenience, never a requirement. This module is both halves:
`Snapshot` (the JSON that carries rows between machines, no credentials in
it) and the live readers (`read_rows`, `read_rows_in_rect`).

The columns are the server's own, measured 2026-10-12 against OpenDAoC:
`mob` 97,737 rows, `worldobject` 799,
`door` 432, `path` 459, `pathpoints` 19,782. They are *selected* to what the
viewer draws and the inspector shows — no column is renamed, none is
invented, and everything else stays in the database where it lives.

Two rules, both measured rather than chosen — and the first one was learned
the hard way (an operator saw mobs in the air, 2026-10-12):

- a zone owns the rows **of its own region** inside its world rectangle
  (`zones.OffsetX/Y` × 8192, 65536 wide — spec §1.2). World coordinates are
  *per region*: regions 51, 102, 163, 165, 202, 234–241 all have rows whose
  X/Y fall inside Camelot Hills' rectangle, and drawing them there showed a
  camp and a herd floating 2,500 units up that the game never shows. So
  `mob`/`worldobject` rows require `Region == zone.Region`, the rectangle is
  `>= left, < left + width` (a border row has one owner), and `door` rows
  follow the server's own rule (`DoorMgr.LoadDoor`): the door's zone is
  `InternalID / 1 000 000` (the doors are region-less in the schema);
- a patrol is kept **when a mob of the zone walks it** (`mob.PathID`): the
  server walks a patrolling mob along its path, and no mob anywhere links
  the global `BT_*` travel routes (measured: 0 rows) — drawing those put
  lines at z 7,879 across the whole map.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

__all__ = [
    "COLUMNS",
    "KEY_COLUMNS",
    "SCHEMA",
    "TABLE_NAMES",
    "ServerRowsError",
    "Snapshot",
    "apply_patches",
    "connect",
    "delete_rows",
    "export_snapshot",
    "insert_rows",
    "mysql_errors",
    "parse_dsn",
    "parse_server_config",
    "read_rows",
    "read_rows_by_key",
    "read_rows_in_rect",
    "rows_in_rect",
]

#: The snapshot's schema tag: a reader that does not know it refuses the file
#: rather than half-loading a future export.
SCHEMA = "camelot.server-rows/1"

#: The selected columns per table, in the schema's own spelling.
COLUMNS: dict[str, tuple[str, ...]] = {
    "mob": (
        "Mob_ID",
        "ClassType",
        "Name",
        "Suffix",
        "Guild",
        "X",
        "Y",
        "Z",
        "Heading",
        "Region",
        "Model",
        "Size",
        "Level",
        "Realm",
        "Flags",
        "AggroLevel",
        "AggroRange",
        "Gender",
        "PathID",
        "NPCTemplateID",
    ),
    "worldobject": (
        "WorldObject_ID",
        "ClassType",
        "Name",
        "X",
        "Y",
        "Z",
        "Heading",
        "Region",
        "Model",
        "Emblem",
        "Realm",
    ),
    "door": (
        "Door_ID",
        "Name",
        "Type",
        "X",
        "Y",
        "Z",
        "Heading",
        "InternalID",
        "Guild",
        "Level",
        "Realm",
        "Flags",
        "IsPostern",
        "Locked",
        "State",
    ),
    "path": ("Path_ID", "PathID", "PathType"),
    "pathpoints": (
        "PathPoints_ID",
        "PathID",
        "Step",
        "X",
        "Y",
        "Z",
        "MaxSpeed",
        "WaitTime",
        "TriggerName",
    ),
}

TABLE_NAMES: tuple[str, ...] = tuple(COLUMNS)

#: The id column each table is ordered by, so two reads agree.
_ORDER = {
    "mob": "Mob_ID",
    "worldobject": "WorldObject_ID",
    "door": "Door_ID",
    "path": "PathID",
    "pathpoints": "PathID, Step",
}


class ServerRowsError(ValueError):
    """A snapshot this reader does not understand, or an unusable input."""


# --- the connection strings ---------------------------------------------------


def parse_server_config(path: str) -> dict[str, Any]:
    """`host/port/user/password/database` from an OpenDAoC `serverconfig.xml`.

    The operator's own file already holds the answer; reading it here saves
    copying a password into a shell history (and nothing here writes it down).
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ServerRowsError(f"{path}: {error}") from error
    match = re.search(r"<DBConnectionString>([^<]+)</DBConnectionString>", text)
    if not match:
        raise ServerRowsError(f"{path}: no <DBConnectionString>")
    fields = dict(part.split("=", 1) for part in match.group(1).split(";") if "=" in part)
    try:
        port = int(fields.get("Port", 3306))
    except ValueError as error:
        raise ServerRowsError(f"{path}: Port {fields.get('Port')!r} is not a number") from error
    return {
        "host": fields.get("Server", "localhost"),
        "port": port,
        "user": fields.get("UserId", "root"),
        "password": fields.get("Password", ""),
        "database": fields.get("Database", "opendaoc"),
    }


def parse_dsn(dsn: str) -> dict[str, Any]:
    """`mysql://user:password@host:port/database`, for a server without a
    config file to read (or a password given on purpose)."""
    url = urlsplit(dsn)
    database = url.path.strip("/")
    if url.scheme not in ("mysql", "mariadb") or not url.hostname or not database:
        raise ServerRowsError(f"expected mysql://user:password@host:port/database, got {dsn!r}")
    return {
        "host": url.hostname,
        "port": url.port or 3306,
        "user": unquote(url.username or "root"),
        "password": unquote(url.password or ""),
        "database": database,
    }


def connect(connection: dict[str, Any]):
    """Open the MySQL connection; `pymysql` is an optional dependency."""
    try:
        import pymysql
    except ImportError as error:  # pragma: no cover - exercised by the absence
        raise ServerRowsError(
            "reading the live database needs pymysql (pip install 'camelot-studio[mysql]')"
        ) from error
    return pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True, **connection)


def mysql_errors() -> tuple[type[BaseException], ...]:
    """What a live read raises: our own error plus the driver's, resolved
    lazily (pymysql is optional). Catching blind `Exception` instead would
    also swallow the programming errors this tool must not hide."""
    errors: tuple[type[BaseException], ...] = (ServerRowsError, OSError)
    try:
        import pymysql
    except ImportError:
        return errors
    return (*errors, pymysql.MySQLError)


# --- the live readers ---------------------------------------------------------


def _select(table: str) -> str:
    return f"SELECT {', '.join(COLUMNS[table])} FROM {table}"


def _rect_clause() -> str:
    return "X >= %s AND X < %s AND Y >= %s AND Y < %s"


def read_rows(
    connect_: Callable[[], Any], *, tables: Sequence[str] = TABLE_NAMES
) -> dict[str, list[dict]]:
    """Every row of the tables, with the selected columns. Read-only."""
    out: dict[str, list[dict]] = {}
    database = connect_()
    try:
        with database.cursor() as cursor:
            for table in tables:
                cursor.execute(f"{_select(table)} ORDER BY {_ORDER[table]}")
                out[table] = list(cursor.fetchall())
    finally:
        database.close()
    return out


def read_rows_in_rect(
    connect_: Callable[[], Any],
    *,
    region: int,
    zone_id: int,
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict[str, list[dict]]:
    """The rows a zone holds: the same rule as `rows_in_rect`, in SQL.

    `connect_` is injected: the caller opens the connection, these readers
    never do — tests run without MySQL.
    """
    rect = (x, x + width, y, y + height)
    out: dict[str, list[dict]] = {}
    database = connect_()
    try:
        with database.cursor() as cursor:
            for table in ("mob", "worldobject"):
                cursor.execute(
                    f"{_select(table)} WHERE {_rect_clause()} AND Region = %s "
                    f"ORDER BY {_ORDER[table]}",
                    (*rect, region),
                )
                out[table] = list(cursor.fetchall())

            # A door carries its zone in `InternalID` (DoorMgr.LoadDoor).
            cursor.execute(
                f"{_select('door')} WHERE InternalID DIV 1000000 = %s ORDER BY {_ORDER['door']}",
                (zone_id,),
            )
            out["door"] = list(cursor.fetchall())

            cursor.execute(
                f"SELECT PathID FROM mob WHERE {_rect_clause()} "
                "AND Region = %s AND PathID IS NOT NULL",
                (*rect, region),
            )
            path_ids = {row["PathID"] for row in cursor.fetchall() if row["PathID"]}

            ids = sorted(path_ids)
            holes = ", ".join(["%s"] * len(ids))
            if ids:
                cursor.execute(
                    f"{_select('pathpoints')} WHERE PathID IN ({holes}) "
                    f"ORDER BY {_ORDER['pathpoints']}",
                    ids,
                )
                out["pathpoints"] = list(cursor.fetchall())
                cursor.execute(
                    f"{_select('path')} WHERE PathID IN ({holes}) ORDER BY {_ORDER['path']}", ids
                )
                out["path"] = [row for row in cursor.fetchall() if row["PathID"] in path_ids]
            else:
                out["pathpoints"] = []
                out["path"] = []
    finally:
        database.close()
    return {table: out.get(table, []) for table in TABLE_NAMES}


# --- the write half: patches to rows that already exist -----------------------

#: The key column of each world table the editor may *patch*. These are the
#: server's own primary keys (GUIDs): nothing is allocated here — a patch
#: names a row that exists, which is why a patch is server-specific by nature
#: (the loader's allocated blocks remain the channel for *new* rows).
KEY_COLUMNS = {"mob": "Mob_ID", "worldobject": "WorldObject_ID", "door": "Door_ID"}


def _check_patch(table: str, values: dict) -> dict[str, str]:
    """A patch writes the table's own columns — never SQL, never its key."""
    if table not in KEY_COLUMNS:
        raise ServerRowsError(
            f"{table!r} is not a patchable table ({', '.join(sorted(KEY_COLUMNS))})"
        )
    if not values:
        raise ServerRowsError(f"{table}: a patch needs at least one value")
    cleaned: dict[str, str] = {}
    for column, value in values.items():
        if column not in COLUMNS[table]:
            raise ServerRowsError(f"{table}: no column named {column!r}")
        if column == KEY_COLUMNS[table]:
            raise ServerRowsError(f"{table}: a patch may not touch its key column")
        cleaned[column] = str(value)
    return cleaned


def read_rows_by_key(connect_: Callable[[], Any], table: str, keys) -> dict[str, dict]:
    """The rows those keys name, by key — the originals a patch records."""
    if table not in KEY_COLUMNS:
        raise ServerRowsError(f"{table!r} is not a patchable table")
    wanted = [str(key) for key in keys]
    if not wanted:
        return {}
    holes = ", ".join(["%s"] * len(wanted))
    database = connect_()
    try:
        with database.cursor() as cursor:
            cursor.execute(f"{_select(table)} WHERE {KEY_COLUMNS[table]} IN ({holes})", wanted)
            return {str(row[KEY_COLUMNS[table]]): row for row in cursor.fetchall()}
    finally:
        database.close()


def apply_patches(connect_: Callable[[], Any], patches, *, deletions=(), insertions=()) -> int:
    """Write patches to existing rows, in one transaction; returns the count.

    Every patch is validated first (known table, known columns, never the
    key), the row is looked up before it is touched — a patch that matches
    nothing is an error, not a silent no-op — and the whole set commits
    together or not at all. *deletions* are rows to remove in the same
    transaction (a release's removal): `{"table": …, "key": …}`, and a row
    that is gone is not an error — a removal that already happened is the
    state the caller asked for. *insertions* are whole rows to put back (a
    reversal's re-insertion): `{"table": …, "values": {…}}`, every column of
    the table's own list, the key included, and a row that is **there** is an
    error — the caller believes it is gone.
    """
    prepared: list[tuple[str, str, dict[str, str]]] = []
    for patch in patches:
        table = patch.get("table", "")
        cleaned = _check_patch(table, patch.get("values") or {})
        if not patch.get("key"):
            raise ServerRowsError(f"{table}: a patch needs the row's key")
        prepared.append((table, str(patch["key"]), cleaned))
    doomed: list[tuple[str, str]] = []
    for item in deletions:
        table, key = str(item.get("table") or ""), str(item.get("key") or "")
        if table not in KEY_COLUMNS:
            raise ServerRowsError(
                f"{table!r} is not a patchable table ({', '.join(sorted(KEY_COLUMNS))})"
            )
        if not key:
            raise ServerRowsError(f"{table}: a removal needs the row's key")
        doomed.append((table, key))
    restored: list[tuple[str, dict[str, str | None]]] = []
    for item in insertions:
        table = str(item.get("table") or "")
        if table not in KEY_COLUMNS:
            raise ServerRowsError(
                f"{table!r} is not a patchable table ({', '.join(sorted(KEY_COLUMNS))})"
            )
        given = item.get("values") or {}
        values = {
            column: (None if given[column] is None else str(given[column]))
            for column in COLUMNS[table]
            if column in given
        }
        holes = [column for column in COLUMNS[table] if column not in values]
        if holes:
            # A re-insertion is a *whole* row, from the record of a removal. A
            # hole is not something to guess at: it is a malformed record.
            raise ServerRowsError(
                f"{table}: the recorded row does not carry {', '.join(holes)}"
            )
        if not values[KEY_COLUMNS[table]]:
            raise ServerRowsError(f"{table}: a re-inserted row needs its key")
        restored.append((table, values))
    if not prepared and not doomed and not restored:
        return 0

    database = connect_()
    try:
        for table, key, values in prepared:
            with database.cursor() as cursor:
                cursor.execute(f"{_select(table)} WHERE {KEY_COLUMNS[table]} = %s", (key,))
                if cursor.fetchone() is None:
                    raise ServerRowsError(f"{table}: no row with {KEY_COLUMNS[table]} {key!r}")
        for table, values in restored:
            key = values[KEY_COLUMNS[table]]
            with database.cursor() as cursor:
                cursor.execute(f"{_select(table)} WHERE {KEY_COLUMNS[table]} = %s", (key,))
                if cursor.fetchone() is not None:
                    raise ServerRowsError(
                        f"{table}: a row with {KEY_COLUMNS[table]} {key!r} is there — "
                        "nothing was removed to put back"
                    )
        database.begin()
        try:
            with database.cursor() as cursor:
                for table, key, values in prepared:
                    columns = ", ".join(f"`{column}` = %s" for column in values)
                    cursor.execute(
                        f"UPDATE `{table}` SET {columns} WHERE {KEY_COLUMNS[table]} = %s",
                        (*values.values(), key),
                    )
                for table, key in doomed:
                    cursor.execute(f"DELETE FROM `{table}` WHERE {KEY_COLUMNS[table]} = %s", (key,))
                for table, values in restored:
                    columns = ", ".join(f"`{column}`" for column in COLUMNS[table])
                    holes = ", ".join(["%s"] * len(COLUMNS[table]))
                    cursor.execute(
                        f"INSERT INTO `{table}` ({columns}) VALUES ({holes})",
                        tuple(values[column] for column in COLUMNS[table]),
                    )
            database.commit()
        except Exception:
            database.rollback()
            raise
    finally:
        database.close()
    return len(prepared) + len(doomed) + len(restored)


def delete_rows(connect_: Callable[[], Any], table: str, keys) -> int:
    """Remove whole rows, in one transaction; each key must exist first."""
    if table not in KEY_COLUMNS:
        raise ServerRowsError(f"{table!r} is not a patchable table")
    wanted = [str(key) for key in keys]
    if not wanted:
        return 0
    database = connect_()
    try:
        for key in wanted:
            with database.cursor() as cursor:
                cursor.execute(f"{_select(table)} WHERE {KEY_COLUMNS[table]} = %s", (key,))
                if cursor.fetchone() is None:
                    raise ServerRowsError(f"{table}: no row with {KEY_COLUMNS[table]} {key!r}")
        database.begin()
        try:
            with database.cursor() as cursor:
                for key in wanted:
                    cursor.execute(f"DELETE FROM `{table}` WHERE {KEY_COLUMNS[table]} = %s", (key,))
            database.commit()
        except Exception:
            database.rollback()
            raise
    finally:
        database.close()
    return len(wanted)


def insert_rows(connect_: Callable[[], Any], table: str, rows) -> int:
    """Re-insert whole rows — a removal's reversal, from the recorded
    originals: every column of the table's own list, the key included."""
    if table not in KEY_COLUMNS:
        raise ServerRowsError(f"{table!r} is not a patchable table")
    prepared = []
    for row in rows:
        values = {column: str(row.get(column, "")) for column in COLUMNS[table]}
        if not values[KEY_COLUMNS[table]]:
            raise ServerRowsError(f"{table}: a re-inserted row needs its key")
        prepared.append(values)
    if not prepared:
        return 0
    columns = ", ".join(f"`{column}`" for column in COLUMNS[table])
    holes = ", ".join(["%s"] * len(COLUMNS[table]))
    database = connect_()
    try:
        database.begin()
        try:
            with database.cursor() as cursor:
                for values in prepared:
                    cursor.execute(
                        f"INSERT INTO `{table}` ({columns}) VALUES ({holes})",
                        tuple(values[column] for column in COLUMNS[table]),
                    )
            database.commit()
        except Exception:
            database.rollback()
            raise
    finally:
        database.close()
    return len(prepared)


# --- the snapshot -------------------------------------------------------------


def rows_in_rect(
    rows: dict[str, list[dict]],
    *,
    region: int,
    zone_id: int,
    x: float,
    y: float,
    width: float,
    height: float,
) -> dict[str, list[dict]]:
    """The rows a zone holds — its region's, inside its rectangle; patrols
    are the ones its mobs walk (see the module docstring for the why)."""

    def inside(row: dict) -> bool:
        return x <= row.get("X", -1) < x + width and y <= row.get("Y", -1) < y + height

    def mine(row: dict) -> bool:
        return inside(row) and int(row.get("Region", -1)) == region

    mobs = [row for row in rows.get("mob", []) if mine(row)]
    objects = [row for row in rows.get("worldobject", []) if mine(row)]
    doors = [
        row
        for row in rows.get("door", [])
        if int(row.get("InternalID", 0) or 0) // 1_000_000 == zone_id
    ]

    path_ids = {mob["PathID"] for mob in mobs if mob.get("PathID")}
    kept = sorted(
        (point for point in rows.get("pathpoints", []) if point["PathID"] in path_ids),
        key=lambda point: (point["PathID"], point["Step"]),
    )
    paths = sorted(
        (row for row in rows.get("path", []) if row["PathID"] in path_ids),
        key=lambda row: row["PathID"],
    )
    return {"mob": mobs, "worldobject": objects, "door": doors, "path": paths, "pathpoints": kept}


class Snapshot:
    """An exported copy of the server's world rows, portable as one JSON.

    The snapshot is what travels: an operator with a DB exports once, a modder
    without MySQL opens the viewer on the file. `source` records where it came
    from (`host`, `database`) and deliberately never the credentials.
    """

    def __init__(self, rows: dict[str, list[dict]], source: dict[str, Any] | None = None):
        self.rows = {table: list(rows.get(table, [])) for table in TABLE_NAMES}
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> Snapshot:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as error:
            raise ServerRowsError(f"{path}: {error}") from error
        except json.JSONDecodeError as error:
            raise ServerRowsError(f"{path}: {error}") from error
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ServerRowsError(
                f"{path}: this snapshot is {document.get('schema')!r}; "
                f"this build understands {SCHEMA}"
            )
        if not isinstance(document.get("rows"), dict):
            raise ServerRowsError(f"{path}: the snapshot has no `rows`")
        return cls(document["rows"], source=document.get("source"))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "source": self.source,
                    "counts": self.counts(),
                    "rows": self.rows,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def counts(self) -> dict[str, int]:
        return {table: len(self.rows.get(table, [])) for table in TABLE_NAMES}

    def in_rect(
        self, *, region: int, zone_id: int, x: float, y: float, width: float, height: float
    ) -> dict[str, list[dict]]:
        return rows_in_rect(
            self.rows, region=region, zone_id=zone_id, x=x, y=y, width=width, height=height
        )


def export_snapshot(
    connection: dict[str, Any], out: str | Path, *, connect_: Callable[[dict], Any] = connect
) -> Snapshot:
    """Read every table once and write the snapshot; no credential lands in it."""
    rows = read_rows(lambda: connect_(connection))
    snapshot = Snapshot(
        rows,
        source={"kind": "mysql", "host": connection["host"], "database": connection["database"]},
    )
    snapshot.save(out)
    return snapshot
