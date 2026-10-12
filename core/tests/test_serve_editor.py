"""The editor: the journal, the working tables, and the publish path (6.3).

The synthetic client is `test_zones`' (`world`), plus a `csv000.mpk` carrying
`fixtures.csv` — the archive the client reads (W-1 measured it) and the one
the editor edits and patches. Nothing here needs MySQL nor the real client;
the in-game half of the ladder (W-5) is the operator's run.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_serverrows import FakeConnection
from test_zones import FIXTURES, world  # noqa: F401  (pytest fixture)

from camelot import serverrows
from camelot.serve.app import create_app
from camelot.serve.config import ServeConfig
from camelot.zones import WorldIndex


def editor_client(root, edits) -> TestClient:
    return TestClient(
        create_app(ServeConfig(root=root, watch=False, open_browser=False, edits=edits)),
        base_url="http://127.0.0.1",
    )


@pytest.fixture
def editing(world):  # noqa: F811  (the fixture from test_zones)
    """The synthetic client plus the `csv000.mpk` the editor works on.

    `test_zones`' zone 0 ships only `dat000.mpk`; the editor targets `csv`,
    so the session's zone has both — `dat` still answers the entries only it
    carries, exactly like the real client.
    """
    from camelot.mpak import write_mpak

    write_mpak(f"{world}/zones/zone000/csv000.mpk", "csv000.mpk",
               [("fixtures.csv", FIXTURES)])
    return world


def edit(client, op, zone=0):
    return client.post(f"/api/v1/world/zone/{zone}/edits", json=op)


def test_without_an_authoring_directory_the_editor_is_off(world):  # noqa: F811
    client = editor_client(world, None)
    state = client.get("/api/v1/world/edit").json()
    assert state["available"] is False
    response = edit(client, {"kind": "move", "id": 2, "x": 0, "y": 0})
    assert response.status_code == 503
    assert "--edits" in response.json()["detail"]


def test_a_move_is_journaled_and_the_zone_payload_shows_it(editing, tmp_path):
    edits = tmp_path / "session"
    client = editor_client(editing, edits)
    response = edit(client, {"kind": "move", "id": 2, "x": 30000, "y": 30000,
                             "expect": {"X": "32768", "Y": "32768"}})
    assert response.status_code == 200
    fixture = response.json()["fixture"]
    assert (fixture["x"], fixture["y"]) == (30000, 30000)
    assert fixture["ground"] is True

    zone = client.get("/api/v1/world/zone/0").json()
    pine = next(row for row in zone["fixtures"] if row["id"] == 2)
    assert (pine["x"], pine["y"]) == (30000, 30000)

    # the journal is on disk: a second daemon (a new app) reads the same edit
    assert (edits / "journal.json").is_file()
    again = editor_client(editing, edits)
    state = again.get("/api/v1/world/edit").json()
    assert state["zones"][0]["id"] == 0
    assert len(state["zones"][0]["ops"]) == 1
    assert state["mod"]["id"] == "session"  # no mod.json: the directory names the mod


def test_an_edit_made_against_a_stale_read_is_refused(editing, tmp_path):
    """`expect` is what the user saw; a viewer that moved on cannot overwrite
    what someone else changed in the meantime."""
    client = editor_client(editing, tmp_path / "session")
    response = edit(client, {"kind": "move", "id": 2, "x": 1, "y": 1,
                             "expect": {"X": "999", "Y": "32768"}})
    assert response.status_code == 400
    assert "expected '999'" in response.json()["detail"]
    assert client.get("/api/v1/world/edit").json()["zones"] == []
    zone = client.get("/api/v1/world/zone/0").json()
    assert [row["id"] for row in zone["fixtures"]] == [1, 2]  # untouched


def test_a_move_writes_z_only_where_the_z_counts(editing, tmp_path):
    """W-3: the client drops a `Ground = 1` row on the terrain whatever its
    Z says, so the editor never invents one there — a ground-free row's Z is
    the user's own (the synthetic zone's own rows are both ground rows, so
    the ground-free one is added first)."""
    client = editor_client(editing, tmp_path / "session")
    ground_pine = edit(client, {"kind": "move", "id": 2, "x": 30000, "y": 30000,
                                "z": 5000}).json()["fixture"]
    assert ground_pine["z"] == 1234  # the row's own Z, untouched

    house = edit(client, {"kind": "add", "nif_id": 10, "x": 20000,
                          "y": 20000}).json()["fixture"]
    assert house["ground"] is False
    moved = edit(client, {"kind": "move", "id": house["id"], "x": 21000,
                          "y": 22000, "z": 700}).json()["fixture"]
    assert moved["z"] == 700  # Ground = 0: the Z is what the user sees


def test_an_add_builds_the_row_from_the_catalog(editing, tmp_path):
    """The catalogue's radius and Ground flag are the client's own hints; a
    ground entry is written the client's way (`z = 0, Ground = 1`, W-3), and
    an entry the client does *not* drop lands at the terrain height."""
    zone = WorldIndex(editing).open(0)
    client = editor_client(editing, tmp_path / "session")
    row = edit(client, {"kind": "add", "nif_id": 11, "x": 30000, "y": 31000,
                        "name": "New Pine"}).json()["fixture"]
    assert row["id"] == 3 and row["name"] == "New Pine"
    assert row["ground"] is True and row["z"] == 0
    assert (row["scale"], row["angle"], row["radius"], row["unique_id"]) == (100, 0, 8, 3)

    house = edit(client, {"kind": "add", "nif_id": 10, "x": 20000,
                          "y": 20000}).json()["fixture"]
    assert house["ground"] is False
    assert house["z"] == pytest.approx(zone.height_at(20000, 20000))

    payload = client.get("/api/v1/world/zone/0").json()
    assert [fixture["id"] for fixture in payload["fixtures"]] == [1, 2, 3, 4]


def test_remove_and_undo_are_exact(editing, tmp_path):
    client = editor_client(editing, tmp_path / "session")
    assert edit(client, {"kind": "remove", "id": 2}).status_code == 200
    zone = client.get("/api/v1/world/zone/0").json()
    assert [fixture["id"] for fixture in zone["fixtures"]] == [1]

    response = client.delete("/api/v1/world/zone/0/edits")
    assert response.status_code == 200
    assert response.json()["op"]["action"] == "remove"
    zone = client.get("/api/v1/world/zone/0").json()
    assert [fixture["id"] for fixture in zone["fixtures"]] == [1, 2]
    assert client.get("/api/v1/world/edit").json()["zones"] == []
    # undoing an empty journal is a no-op, not an error
    assert client.delete("/api/v1/world/zone/0/edits").status_code == 200


def test_publishing_twice_says_what_is_already_there(editing, tmp_path):
    """The operator pressed publish twice: the second press is not a mistake
    about an empty journal, it is a session already written on both sides."""
    client = editor_client(editing, tmp_path / "session")
    edit(client, {"kind": "move", "id": 2, "x": 30000, "y": 31000})
    assert client.post("/api/v1/world/mod/apply").status_code == 200
    again = client.post("/api/v1/world/mod/apply")
    assert again.status_code == 400
    assert "already" in again.json()["detail"]


def test_build_needs_something_to_publish(editing, tmp_path):
    client = editor_client(editing, tmp_path / "session")
    response = client.post("/api/v1/world/mod/build")
    assert response.status_code == 400
    assert "journal holds no edit" in response.json()["detail"]


def test_apply_installs_the_edited_zone_and_the_client_reads_it_back(editing, tmp_path):
    """6.3's promise end to end: an edit in the viewer becomes a pack through
    the same `mod build` path, `apply` installs it, and reopening the client
    reads the edited row — asserted from the client's own files."""
    edits = tmp_path / "session"
    client = editor_client(editing, edits)
    assert edit(client, {"kind": "move", "id": 2, "x": 12345,
                         "y": 23456}).status_code == 200

    response = client.post("/api/v1/world/mod/apply")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["install"]["mod"] == "session"
    assert (edits / "session.tar.gz").is_file()
    assert (edits / "fixtures-zone000.csv").is_file()
    assert json.loads((edits / "client.patch.json").read_text())["archive"] == [
        {"archive": "zones/zone000/csv000.mpk", "entry": "fixtures.csv",
         "file": "fixtures-zone000.csv"}]

    zone = WorldIndex(editing).open(0)
    pine = next(fixture for fixture in zone.fixtures() if fixture.id == 2)
    assert (pine.x, pine.y) == (12345, 23456)

    # uninstall is exact: the client's own bytes come back
    from camelot import clientinstall

    clientinstall.uninstall(editing, "session")
    zone = WorldIndex(editing).open(0)
    pine = next(fixture for fixture in zone.fixtures() if fixture.id == 2)
    assert (pine.x, pine.y) == (32768, 32768)


def test_a_publish_commits_the_client_ops(editing, tmp_path):
    """`apply` writes the edit into the client, so the journal **commits**: its
    client ops go (the client carries them now) and the next edit starts from
    what the client holds. The operator hit exactly this on 2026-10-12: an op
    recorded before a publish no longer applied against the published base.
    A row that differs from both the expectation and the values still refuses."""
    edits = tmp_path / "session"
    client = editor_client(editing, edits)
    assert edit(client, {"kind": "move", "id": 2, "x": 30000,
                         "y": 31000}).status_code == 200
    assert client.post("/api/v1/world/mod/apply").status_code == 200

    state = client.get("/api/v1/world/edit").json()
    assert state["zones"] == []          # committed: the client holds the op
    assert state["published"]["ops"] == 1
    zone = client.get("/api/v1/world/zone/0").json()
    pine = next(fixture for fixture in zone["fixtures"] if fixture["id"] == 2)
    assert (pine["x"], pine["y"]) == (30000, 31000)
    assert zone["warnings"] == []        # the journal applies again

    # the next edit starts from the client's own row, and journals
    again = edit(client, {"kind": "move", "id": 2, "x": 30500, "y": 31000})
    assert again.status_code == 200, again.json()
    assert client.get("/api/v1/world/edit").json()["zones"][0]["ops"]

    # a real conflict (neither the expectation nor the target matches) refuses
    response = edit(client, {"kind": "move", "id": 2, "x": 1, "y": 1,
                             "expect": {"X": "999", "Y": "999"}})
    assert response.status_code == 400
    assert "expected '999'" in response.json()["detail"]


DB_CONFIG = ("<DBConnectionString>Server=localhost;Port=3307;Database=opendaoc;"
             "UserId=root;Password=x</DBConnectionString>")
#: The synthetic zone 0's world rectangle: rows are world coordinates, the
#: fixtures are zone-local, and the editor must not confuse the two.
LEFT, TOP = 16384, 24576


def door_row(**overrides) -> dict:
    row = {"Door_ID": "d1", "Name": "gate", "Type": 0, "Heading": 1024,
           "X": LEFT + 19660, "Y": TOP + 19660, "Z": 10, "InternalID": 40123,
           "Level": 0, "Realm": 1, "Flags": 0, "IsPostern": 0, "Locked": 0,
           "Guild": ""}
    row.update(overrides)
    return row


def db_client(editing, tmp_path, **kwargs) -> TestClient:
    """A client whose Editor can *patch* — with the connection injected."""
    config = tmp_path / "serverconfig.xml"
    config.write_text(DB_CONFIG)
    return TestClient(
        create_app(ServeConfig(root=editing, watch=False, open_browser=False,
                               edits=tmp_path / "session", db_config=config, **kwargs)),
        base_url="http://127.0.0.1",
    )


def test_a_fixture_move_carries_its_server_rows(editing, tmp_path, monkeypatch):
    """B2, the case W-1 measured: the keep and its doors. The browser proposes
    the pairs (it holds the fixture's own mesh box); the daemon checks them
    against the database, computes the new values in the fixture's frame —
    world rows, zone-local fixture — and records the originals it read."""
    zone = WorldIndex(editing).open(0)
    fake = FakeConnection([[door_row()]])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path)
    response = edit(client, {"kind": "move", "id": 1, "x": 20800, "y": 20800,
                             "rows": [{"table": "door", "key": "d1"}]})
    assert response.status_code == 200, response.json()
    patches = response.json()["patches"]
    assert len(patches) == 1
    patch = patches[0]
    assert (patch["table"], patch["key"]) == ("door", "d1")
    assert patch["values"]["X"] == str(LEFT + 20800)
    assert patch["values"]["Y"] == str(TOP + 20800)
    before_z = zone.height_at(19660, 19660)
    after_z = zone.height_at(20800, 20800)
    assert patch["values"]["Z"] == str(round(10 - before_z + after_z))
    assert patch["values"]["Heading"] == "1024"  # no rotation, no change
    assert patch["originals"]["X"] == str(LEFT + 19660)


def test_a_rotation_carries_the_rows_around_the_fixture(editing, tmp_path, monkeypatch):
    """`A` is degrees, 0 = the mesh's front north, 90 = east (measured). A door
    500 units in front of a fixture at `A = 0` ends up 500 units east of it
    after a quarter turn — and its 12-bit heading turns with it (both
    compasses run clockwise on a north-up map)."""
    fake = FakeConnection([[door_row(Y=TOP + 19660 - 500, Heading=0)]])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path)
    response = edit(client, {"kind": "rotate", "id": 1, "angle": 90,
                             "rows": [{"table": "door", "key": "d1"}]})
    assert response.status_code == 200, response.json()
    values = response.json()["patches"][0]["values"]
    assert values["X"] == str(LEFT + 20160)  # 19 660 + 500: east
    assert values["Y"] == str(TOP + 19660)
    assert values["Heading"] == "1024"       # a quarter turn of 4096


def test_a_row_move_is_zone_local_and_keeps_its_height(editing, tmp_path, monkeypatch):
    """A row op is zone-local like every other op — the viewer holds the rows
    in the fixtures' own space — while the row itself is written in the
    server's world coordinates (B1 measured that split), keeping its height
    above the ground the way `_placement` reads a fixture's own."""
    zone = WorldIndex(editing).open(0)
    fake = FakeConnection([[door_row()]])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    response = edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                             "x": 20000, "y": 21000})
    assert response.status_code == 200, response.json()
    patch = response.json()["patches"][0]
    assert patch["values"]["X"] == str(LEFT + 20000)
    assert patch["values"]["Y"] == str(TOP + 21000)
    offset = 10 - zone.height_at(19660, 19660)  # 10 above its own ground
    assert patch["values"]["Z"] == str(round(zone.height_at(20000, 21000) + offset))
    assert "Heading" not in patch["values"]  # a move leaves the heading alone
    assert patch["originals"]["X"] == str(LEFT + 19660)


def test_a_row_move_can_name_its_own_height(editing, tmp_path, monkeypatch):
    """The height gesture: an op that names a `z` means it — a gate lifted
    onto its wall — instead of keeping the row's height above the terrain."""
    fake = FakeConnection([[door_row()]])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    response = edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                             "x": 20000, "y": 21000, "z": 3200})
    assert response.status_code == 200, response.json()
    values = response.json()["patches"][0]["values"]
    assert values["Z"] == "3200"  # the height the op asked for
    assert values["X"] == str(LEFT + 20000)


def test_two_moves_before_an_apply_stack_on_the_rows(editing, tmp_path, monkeypatch):
    """A keep nudged twice and applied once: the second op's values start from
    the row the journal already leaves (else the first move's delta is lost),
    and its `originals` stay the database's own — a revert puts back what the
    server holds, not what the journal made."""
    fake = FakeConnection([[door_row()], [door_row()]])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    first = edit(client, {"kind": "move", "id": 1, "x": 19860, "y": 19660,
                          "rows": [{"table": "door", "key": "d1"}]})
    assert first.status_code == 200, first.json()
    assert first.json()["patches"][0]["values"]["X"] == str(LEFT + 19860)

    second = edit(client, {"kind": "move", "id": 1, "x": 20060, "y": 19660,
                           "rows": [{"table": "door", "key": "d1"}]})
    assert second.status_code == 200, second.json()
    patch = second.json()["patches"][0]
    assert patch["values"]["X"] == str(LEFT + 20060)  # 200 + 200, not the last
    assert patch["values"]["Y"] == str(TOP + 19660)
    assert patch["originals"]["X"] == str(LEFT + 19660)  # still the database's


def test_patching_the_server_rows_needs_the_database_and_the_flag(editing, tmp_path):
    """Reading is a convenience, writing is a decision: without --db-config
    there is nothing to read the originals from, and without --db-write the
    daemon refuses before anything lands."""
    client = editor_client(editing, tmp_path / "session")  # neither flag
    response = edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                             "x": 100, "y": 100})
    assert response.status_code == 400
    assert "--db-config" in response.json()["detail"]


def test_apply_writes_the_patch_and_revert_puts_the_originals_back(editing, tmp_path,
                                                                   monkeypatch):
    fake = FakeConnection([
        [door_row()],                 # the pair read
        [door_row()],                 # the existence check at apply
        [],                           # the UPDATE
        [door_row(X=LEFT + 20800)],   # the existence check at revert
        [],                           # the restoring UPDATE
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    assert edit(client, {"kind": "move", "id": 1, "x": 20800, "y": 20800,
                         "rows": [{"table": "door", "key": "d1"}]}).status_code == 200

    applied = client.post("/api/v1/world/mod/apply")
    assert applied.status_code == 200, applied.json()
    assert applied.json()["patches"] == 1
    # a *fixture* move has both halves: the pack went in, then the row
    assert applied.json()["pack"] is not None
    assert applied.json()["install"]["mod"] == "session"
    updates = [params for sql, params in fake.statements if sql.startswith("UPDATE")]
    assert updates[0][0] == str(LEFT + 20800)
    state = client.get("/api/v1/world/edit").json()
    assert state["rows"][0]["patches"][0]["applied"] is True

    reverted = client.post("/api/v1/world/mod/revert")
    assert reverted.status_code == 200
    assert client.get("/api/v1/world/edit").json()["rows"] == []
    updates = [params for sql, params in fake.statements if sql.startswith("UPDATE")]
    assert updates[-1][0] == str(LEFT + 19660)  # the original, put back


def test_restore_takes_the_client_half_and_the_journal_back(editing, tmp_path,
                                                           monkeypatch):
    """`revert` knows the journal; the client's installed half is the
    *client's* own authority (D6), so a full restore does both — and it does
    the client first, so a refusal leaves the session whole (the operator hit
    exactly this on 2026-10-12: a reverted journal and a client still showing
    the moved keep)."""
    fake = FakeConnection([
        [door_row()],                 # the pair read
        [door_row()],                 # the existence check at apply
        [],                           # the UPDATE
        [door_row(X=LEFT + 30000)],   # the existence check at revert
        [],                           # the restoring UPDATE
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    assert edit(client, {"kind": "move", "id": 2, "x": 30000, "y": 31000,
                         "rows": [{"table": "door", "key": "d1"}]}).status_code == 200
    assert client.post("/api/v1/world/mod/apply").status_code == 200
    state = client.get("/api/v1/world/edit").json()
    assert state["client"]["installed"] is True
    assert state["client"]["mod"] == "session"
    pine = next(row for row in client.get("/api/v1/world/zone/0").json()["fixtures"]
                if row["id"] == 2)
    assert (pine["x"], pine["y"]) == (30000, 31000)
    zone = WorldIndex(editing).open(0)
    assert next(fixture for fixture in zone.fixtures() if fixture.id == 2).x == 30000

    response = client.post("/api/v1/world/mod/restore")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["client"]["mod"] == "session"  # what was removed
    assert body["rows"] == 1                    # the server row put back
    assert body["state"]["zones"] == [] and body["state"]["rows"] == []
    assert body["state"]["client"]["installed"] is False
    # the client's own bytes are back, and so is the row
    zone = WorldIndex(editing).open(0)
    assert next(fixture for fixture in zone.fixtures() if fixture.id == 2).x == 32768
    updates = [params for sql, params in fake.statements if sql.startswith("UPDATE")]
    assert updates[-1][0] == str(LEFT + 19660)

    # a second restore has nothing to do, and says so instead of failing
    again = client.post("/api/v1/world/mod/restore")
    assert again.status_code == 200, again.json()
    assert again.json()["client"]["installed"] is False


def test_a_row_removal_is_reversible_by_reinsertion(editing, tmp_path, monkeypatch):
    """A removal records the whole row; revert re-inserts it, every column
    the table's own list holds — a world edit is exactly reversible."""
    row = door_row()
    full = {column: str(row.get(column, "")) for column in serverrows.COLUMNS["door"]}
    fake = FakeConnection([
        [row],   # the read for the removal
        [row],   # the existence check at apply
        [],      # the DELETE
        [],      # the INSERT at revert
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    response = edit(client, {"kind": "row-remove", "table": "door", "key": "d1"})
    assert response.status_code == 200, response.json()
    patch = response.json()["patches"][0]
    assert patch["deleted"] is True and patch["originals"] == full
    applied = client.post("/api/v1/world/mod/apply").json()
    assert applied["patches"] == 1
    assert applied["pack"] is None  # a server-only edit needs no pack
    assert ("DELETE FROM `door` WHERE Door_ID = %s", ("d1",)) in fake.statements
    assert client.post("/api/v1/world/mod/revert").status_code == 200
    insert = next(sql for sql, _ in fake.statements if sql.startswith("INSERT"))
    assert insert.startswith("INSERT INTO `door` (`Door_ID`, `Name`, `Type`")


def test_an_applied_patch_cannot_be_undone_away(editing, tmp_path, monkeypatch):
    """Undo must never leave a live change with no journal entry: an applied
    patch is put back by revert, which knows the originals."""
    fake = FakeConnection([
        [door_row()],   # the read for the row op
        [door_row()],   # the existence check at apply
        [],             # the UPDATE
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                  "x": 20000, "y": 20000})
    assert client.post("/api/v1/world/mod/apply").status_code == 200
    response = client.delete("/api/v1/world/zone/0/edits")
    assert response.status_code == 400
    assert "revert" in response.json()["detail"]


def test_a_publish_does_not_move_the_revert_target(editing, tmp_path, monkeypatch):
    """Two rounds of row edits with a publish between them: `revert` puts the
    *session's first* originals back, not the published ones — otherwise
    `restore all` would leave the database on the last publish."""
    fake = FakeConnection([
        [door_row()],                      # round 1: the originals
        [door_row()],                      # the existence check at apply
        [],                                # the UPDATE
        [door_row(X=LEFT + 19800)],        # round 2: the row as published
        [door_row(X=LEFT + 19800)],        # the existence check at apply
        [],                                # the UPDATE
        [door_row(X=LEFT + 20000)],        # the existence check at revert
        [],                                # the restoring UPDATE
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    for x in (19800, 20000):
        assert edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                            "x": x, "y": 19660}).status_code == 200
        assert client.post("/api/v1/world/mod/apply").status_code == 200
    state = client.get("/api/v1/world/edit").json()
    # the patches' own originals follow the database, round after round…
    assert state["rows"][0]["patches"][0]["originals"]["X"] == str(LEFT + 19660)
    assert state["rows"][0]["patches"][1]["originals"]["X"] == str(LEFT + 19800)

    assert client.post("/api/v1/world/mod/revert").status_code == 200
    updates = [params for sql, params in fake.statements if sql.startswith("UPDATE")]
    assert updates[-1][0] == str(LEFT + 19660)  # the session's first original


def test_a_journal_without_the_ledger_still_reverts(editing, tmp_path, monkeypatch):
    """A session opened *before* the ledger existed (the operator's, 2026-10-12)
    carries written patches and no originals map: revert falls back to what
    each patch recorded, rather than leaving the database on the edit."""
    fake = FakeConnection([
        [door_row()],                 # the row op's read
        [door_row()],                 # the existence check at apply
        [],                           # the UPDATE
        [door_row(X=LEFT + 24600)],   # the existence check at revert
        [],                           # the restoring UPDATE
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path, db_write=True)
    assert edit(client, {"kind": "row-move", "table": "door", "key": "d1",
                         "x": 24600, "y": 46957}).status_code == 200
    assert client.post("/api/v1/world/mod/apply").status_code == 200

    path = tmp_path / "session" / "journal.json"
    journal = json.loads(path.read_text())
    journal.pop("originals", None)          # an older session's journal
    path.write_text(json.dumps(journal))

    assert client.post("/api/v1/world/mod/revert").status_code == 200
    updates = [params for sql, params in fake.statements if sql.startswith("UPDATE")]
    assert updates[-1][0] == str(LEFT + 19660)  # the patch's own original


def test_a_published_chain_does_not_block_the_build(editing, tmp_path):
    """The operator's own state on 2026-10-12: two ops on one row, published
    (25344 → 20992 → 19968) so the client reads 19968 — the first op's
    expectation no longer matches. The chain ends where the base stands, so it
    counts as published: the build goes through instead of refusing."""
    edits = tmp_path / "session"
    client = editor_client(editing, edits)
    assert edit(client, {"kind": "move", "id": 2, "x": 20992,
                         "y": 32768}).status_code == 200
    assert edit(client, {"kind": "move", "id": 2, "x": 19968,
                         "y": 32768}).status_code == 200
    assert client.post("/api/v1/world/mod/apply").status_code == 200

    # the client now reads 19968; put the ops back, as a session saved before
    # the commit-on-publish rule would have them
    path = edits / "journal.json"
    journal = json.loads(path.read_text())
    journal["published"] = {}
    journal["zones"] = {"0": [
        {"action": "set", "id": 2, "columns": {"X": 3, "Y": 4},
         "values": {"X": "20992", "Y": "32768"}, "expect": {"X": "32768", "Y": "32768"}},
        {"action": "set", "id": 2, "columns": {"X": 3, "Y": 4},
         "values": {"X": "19968", "Y": "32768"}, "expect": {"X": "20992", "Y": "32768"}},
    ]}
    path.write_text(json.dumps(journal))

    # a new daemon reads the file: the journal is cached per instance
    fresh = editor_client(editing, edits)
    zone = fresh.get("/api/v1/world/zone/0").json()
    assert zone["warnings"] == []
    pine = next(fixture for fixture in zone["fixtures"] if fixture["id"] == 2)
    assert (pine["x"], pine["y"]) == (19968, 32768)
    built = fresh.post("/api/v1/world/mod/build")
    assert built.status_code == 200, built.json()

    # a chain that lands somewhere else still refuses
    journal["zones"]["0"][1]["values"] = {"X": "18000", "Y": "32768"}
    path.write_text(json.dumps(journal))
    again = editor_client(editing, edits)
    warnings = again.get("/api/v1/world/zone/0").json()["warnings"]
    assert any("the journal did not apply" in warning for warning in warnings), warnings


def test_the_zone_payload_shows_the_journal_patches(editing, tmp_path, monkeypatch):
    """The viewer must show what the journal recorded, before it is applied:
    the door the operator moved is drawn where the patch puts it."""
    fake = FakeConnection([
        [door_row()],                # the pair read (the edit comes first)
        [], [], [door_row()], [],    # then the spawns read: mob, object, door, paths
    ])
    monkeypatch.setattr(serverrows, "connect", lambda _connection: fake)
    client = db_client(editing, tmp_path)
    assert edit(client, {"kind": "move", "id": 1, "x": 20800, "y": 20800,
                         "rows": [{"table": "door", "key": "d1"}]}).status_code == 200
    payload = client.get("/api/v1/world/zone/0/spawns").json()
    assert payload["doors"][0]["x"] == 20800  # world 37 184 → local 20 800


def test_revert_empties_the_journal_and_its_build(tmp_path, editing):
    client = editor_client(editing, tmp_path / "session")
    edit(client, {"kind": "scale", "id": 1, "scale": 50})
    edit(client, {"kind": "rotate", "id": 2, "angle": 90})
    built = client.post("/api/v1/world/mod/build").json()
    pack = Path(built["pack"])
    csv = pack.parent / "fixtures-zone000.csv"
    assert pack.is_file() and csv.is_file()

    response = client.post("/api/v1/world/mod/revert")
    assert response.status_code == 200
    assert response.json()["zones"] == []
    assert response.json()["pack"] is None
    assert not pack.exists()
    assert not csv.exists()
    zone = client.get("/api/v1/world/zone/0").json()
    assert zone["fixtures"][0]["scale"] == 100
    assert zone["fixtures"][1]["angle"] == 2048
