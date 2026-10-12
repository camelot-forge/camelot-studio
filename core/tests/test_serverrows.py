"""The server's world rows: the snapshot format, its readers, its filters.

The schema these mirror is measured, not assumed (`notes/db-schema.py`
against the workshop's OpenDAoC, 2026-10-12): `mob` 97,737 rows, `worldobject`
799, `door` 432, `path` 459, `pathpoints` 19,782. No test here needs MySQL:
the readers are handed a connection, and the fake below records what it was
asked for; the snapshot path is a plain file.

One rule to keep honest: a zone's rows are the rows inside its world
rectangle (`>= left, < left + width` — a border row belongs to the zone that
owns it), and a patrol is kept *whole* once one of its points is inside, so a
line that crosses the zone is drawn complete rather than clipped at a guess.
"""
import json

import pytest

from camelot import serverrows
from camelot.serverrows import (
    SCHEMA,
    ServerRowsError,
    Snapshot,
    parse_dsn,
    parse_server_config,
)

RECT = (548864, 483328, 65536, 65536)  # Camelot Hills, measured (zones.OffsetX/Y 67/59)
REGION = 1
ZONE = 0
#: `rows_in_rect`/`in_rect` take the zone's identity: region for `mob` and
#: `worldobject`, zone id for `door` (its `InternalID` carries it).
WHERE = {"region": REGION, "zone_id": ZONE,
         "x": RECT[0], "y": RECT[1], "width": RECT[2], "height": RECT[3]}


def mob(row_id: str, x: int, y: int, **extra) -> dict:
    return {"Mob_ID": row_id, "Name": row_id, "X": x, "Y": y, "Z": 0,
            "Region": REGION, "PathID": None, **extra}


def point(path_id: str, step: int, x: int, y: int) -> dict:
    return {"PathPoints_ID": f"{path_id}-{step}", "PathID": path_id,
            "Step": step, "X": x, "Y": y, "Z": 100, "MaxSpeed": 150,
            "WaitTime": 0, "TriggerName": ""}


ROWS = {
    "mob": [
        mob("m-in", 550000, 490000, PathID="Patrol_One"),
        mob("m-out", 700000, 490000),
        mob("m-border", 548864 + 65536, 490000),  # exactly on the east border: the next zone's
        # World coordinates are per region: another region's row lands in the
        # same rectangle and is *not* this zone's (the operator saw exactly
        # this as mobs in the air, 2026-10-12).
        mob("m-elsewhere", 550000, 490000, Region=165),
    ],
    "worldobject": [
        {"WorldObject_ID": "o-in", "Name": "banner", "X": 570000, "Y": 490000, "Z": 0,
         "Region": REGION, "Model": 1315},
        {"WorldObject_ID": "o-out", "Name": "far", "X": 1, "Y": 1, "Z": 0,
         "Region": REGION, "Model": 1},
        {"WorldObject_ID": "o-elsewhere", "Name": "camp", "X": 570000, "Y": 490000,
         "Z": 5000, "Region": 165, "Model": 1},
    ],
    "door": [
        {"Door_ID": "d-in", "Name": "keep gate", "X": 575000, "Y": 533000, "Z": 0,
         "Type": 0, "Heading": 0, "InternalID": 40123},
        {"Door_ID": "d-out", "Name": "elsewhere", "X": 100, "Y": 100, "Z": 0,
         "Type": 0, "Heading": 0, "InternalID": 0},
        # A door carries its zone in `InternalID` (the server's own
        # `DoorMgr.LoadDoor`): zone 165's mile gate sits in this rectangle.
        {"Door_ID": "d-foreign", "Name": "Midgard Mile Gate", "X": 555250,
         "Y": 536197, "Z": 4256, "Type": 1, "Heading": 1013,
         "InternalID": 165102401},
    ],
    "path": [{"Path_ID": "p1-id", "PathID": "Patrol_One", "PathType": 3},
             {"Path_ID": "p2-id", "PathID": "Patrol_Two", "PathType": 3}],
    "pathpoints": [
        # Patrol_One crosses the zone: step 2 is inside, the whole line is kept.
        point("Patrol_One", 1, 500000, 490000),
        point("Patrol_One", 2, 575000, 490000),
        point("Patrol_One", 3, 700000, 490000),
        # Patrol_Two never enters it.
        point("Patrol_Two", 1, 1, 1),
        point("Patrol_Two", 2, 2, 2),
    ],
}


def test_a_snapshot_round_trips_through_json(tmp_path):
    """The snapshot is the portable half of the DB layer (spec §5): what a
    live export wrote, a viewer without MySQL must read back exactly."""
    snapshot = Snapshot(ROWS, source={"kind": "mysql", "host": "localhost",
                                      "database": "opendaoc"})
    path = tmp_path / "rows.json"
    snapshot.save(path)
    again = Snapshot.load(path)
    assert again.rows["mob"] == ROWS["mob"]
    assert again.counts() == {"mob": 4, "worldobject": 3, "door": 3, "path": 2,
                              "pathpoints": 5}
    assert again.source["database"] == "opendaoc"


def test_a_snapshot_the_reader_does_not_understand_is_refused(tmp_path):
    """A future schema must fail loudly on an old reader, not half-load."""
    path = tmp_path / "rows.json"
    path.write_text(json.dumps({"schema": "camelot.server-rows/99", "rows": {}}))
    with pytest.raises(ServerRowsError, match="camelot.server-rows/99"):
        Snapshot.load(path)


def test_a_snapshot_without_its_rows_is_refused(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps({"schema": SCHEMA}))
    with pytest.raises(ServerRowsError, match="rows"):
        Snapshot.load(path)


def test_the_rect_filter_keeps_what_the_zone_holds():
    """A zone's rows are its *region's* rows inside its world rectangle; the
    border belongs to the zone whose rectangle starts there, not to both, and
    a door belongs to the zone its `InternalID` names (the server's own rule)
    whatever its coordinates."""
    got = Snapshot(ROWS).in_rect(**WHERE)
    assert [row["Mob_ID"] for row in got["mob"]] == ["m-in"]
    assert [row["WorldObject_ID"] for row in got["worldobject"]] == ["o-in"]
    assert [row["Door_ID"] for row in got["door"]] == ["d-in", "d-out"]


def test_only_the_patrols_the_zone_s_mobs_walk_are_kept():
    """The rule is the server's: a patrolling mob walks its path, so a path
    is zone content when a mob of the zone links it — kept *whole* (its
    points beyond the rectangle included, the client walks the whole line).
    Patrol_Two crosses the rectangle but nobody walks it: dropped."""
    got = Snapshot(ROWS).in_rect(**WHERE)
    assert [row["PathID"] for row in got["path"]] == ["Patrol_One"]
    assert [row["Step"] for row in got["pathpoints"]] == [1, 2, 3]
    assert [row["X"] for row in got["pathpoints"]] == [500000, 575000, 700000]


def test_a_patrol_reached_only_by_a_mob_outside_the_zone_is_not_kept():
    """The link has to come from a mob the zone owns: another region's mob
    pointing at the same path makes it that region's route, not ours."""
    rows = dict(ROWS)
    rows["mob"] = [mob("m-in", 550000, 490000),
                   mob("m-other", 550000, 490000, Region=165, PathID="Patrol_Two")]
    got = Snapshot(rows).in_rect(**WHERE)
    assert got["path"] == [] and got["pathpoints"] == []


# --- the live half ------------------------------------------------------------

class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.connection.statements.append((sql, params))
        self.result = self.connection.answers.pop(0)

    def fetchall(self):
        return self.result


class FakeConnection:
    """Records the SQL, answers the canned result sets in order."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.statements = []
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def test_the_live_reader_asks_for_the_zone_region_and_rectangle():
    """The live half runs the same rules in SQL: the rectangle *and* the
    region for `mob`/`worldobject`, the `InternalID` zone for `door` (the
    server's own `DoorMgr.LoadDoor`), and whole paths reached through the
    zone's mobs. The connection is injected — tests never open MySQL."""
    connection = FakeConnection([
        [ROWS["mob"][0]], [ROWS["worldobject"][0]], [ROWS["door"][0]],
        [{"PathID": "Patrol_One"}],
        ROWS["pathpoints"][:3], [ROWS["path"][0]],
    ])
    got = serverrows.read_rows_in_rect(lambda: connection, **WHERE)

    for sql, params in connection.statements[:2]:
        assert "BETWEEN %s AND %s" not in sql  # `>=`/`<` keeps the border single-owner
        assert "Region = %s" in sql
        assert params == (548864, 548864 + 65536, 483328, 483328 + 65536, REGION)
    assert "DIV 1000000" in connection.statements[2][0]
    assert connection.statements[2][1] == (ZONE,)
    assert "PathID IS NOT NULL" in connection.statements[3][0]
    assert "PathID IN" in connection.statements[4][0]
    assert "ORDER BY PathID, Step" in connection.statements[4][0]
    assert connection.closed
    assert [row["Mob_ID"] for row in got["mob"]] == ["m-in"]
    assert [row["Step"] for row in got["pathpoints"]] == [1, 2, 3]
    assert [row["PathID"] for row in got["path"]] == ["Patrol_One"]


def test_an_export_writes_the_rows_and_never_the_password(tmp_path):
    """The snapshot is meant to be shared (a modder, a bug report): `source`
    records where it came from and nothing that opens the database."""
    connection = FakeConnection([
        ROWS["mob"], ROWS["worldobject"], ROWS["door"], ROWS["path"],
        ROWS["pathpoints"],
    ])
    path = tmp_path / "rows.json"
    snapshot = serverrows.export_snapshot(
        {"host": "localhost", "port": 3307, "user": "root",
         "password": "s3cret", "database": "opendaoc"},
        path, connect_=lambda _connection: connection)
    assert next(sql for sql, _ in connection.statements).startswith("SELECT Mob_ID")
    assert snapshot.counts()["mob"] == 4
    document = json.loads(path.read_text())
    assert document["source"] == {"kind": "mysql", "host": "localhost",
                                  "database": "opendaoc"}
    assert "s3cret" not in path.read_text()


def test_the_columns_and_tables_are_the_measured_schema():
    """Selected columns keep the server's own vocabulary (no rename, no
    invention); the ids are the primary keys the loader will write later."""
    assert serverrows.TABLE_NAMES == ("mob", "worldobject", "door", "path", "pathpoints")
    assert "Mob_ID" in serverrows.COLUMNS["mob"]
    assert "PathID" in serverrows.COLUMNS["mob"]
    assert "WorldObject_ID" in serverrows.COLUMNS["worldobject"]
    assert "IsPostern" in serverrows.COLUMNS["door"]
    assert "WaitTime" in serverrows.COLUMNS["pathpoints"]
    assert all(column in serverrows.COLUMNS["path"] for column in ("PathID", "PathType"))


# --- the connection strings ---------------------------------------------------

def test_the_server_config_xml_yields_the_connection(tmp_path):
    """OpenDAoC operators have the answer in their own file; parsing it here
    saves copying a password into a shell history."""
    config = tmp_path / "serverconfig.xml"
    config.write_text(
        "<root><DBConnectionString>Server=localhost;Port=3307;Database=opendaoc;"
        "UserId=root;Password=s3cret;Treat Tiny As Boolean=False"
        "</DBConnectionString></root>")
    connection = parse_server_config(str(config))
    assert connection == {"host": "localhost", "port": 3307, "user": "root",
                          "password": "s3cret", "database": "opendaoc"}


def test_a_live_read_catches_the_driver_and_our_own_errors_only():
    """The route catches `mysql_errors()` rather than a blind `Exception`: a
    dead socket must not 500 the viewer, a programming error must surface."""
    assert ServerRowsError in serverrows.mysql_errors()
    assert OSError in serverrows.mysql_errors()


def test_a_config_without_a_connection_string_is_refused(tmp_path):
    config = tmp_path / "serverconfig.xml"
    config.write_text("<root></root>")
    with pytest.raises(ServerRowsError, match="DBConnectionString"):
        parse_server_config(str(config))


def test_a_dsn_parses_like_the_config():
    connection = parse_dsn("mysql://camelot:p%40ss@127.0.0.1:3307/opendaoc")
    assert connection == {"host": "127.0.0.1", "port": 3307, "user": "camelot",
                          "password": "p@ss", "database": "opendaoc"}
    assert parse_dsn("mysql://root@localhost/opendaoc")["port"] == 3306
    with pytest.raises(ServerRowsError, match="mysql://"):
        parse_dsn("postgres://localhost/db")
