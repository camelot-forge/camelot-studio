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
from test_zones import FIXTURES, world  # noqa: F401  (pytest fixture)

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


def test_an_op_the_client_already_shows_is_already_applied(editing, tmp_path):
    """`apply` writes the edit into the client; the journal then describes a
    state the base already carries. Replaying it is a no-op, not a conflict
    (the operator hit exactly this after their first in-game check) — while a
    row that differs from both the expectation and the values still refuses."""
    edits = tmp_path / "session"
    client = editor_client(editing, edits)
    edit(client, {"kind": "move", "id": 2, "x": 30000, "y": 31000})
    assert client.post("/api/v1/world/mod/apply").status_code == 200
    # the client now carries the move, and the journal still holds the op
    zone = client.get("/api/v1/world/zone/0").json()
    pine = next(fixture for fixture in zone["fixtures"] if fixture["id"] == 2)
    assert (pine["x"], pine["y"]) == (30000, 31000)
    assert zone["warnings"] == []
    assert client.get("/api/v1/world/edit").json()["zones"][0]["ops"]

    # a real conflict (neither the expectation nor the target matches) refuses
    response = edit(client, {"kind": "move", "id": 2, "x": 1, "y": 1,
                             "expect": {"X": "999", "Y": "999"}})
    assert response.status_code == 400
    assert "expected '999'" in response.json()["detail"]


def test_revert_empties_the_journal_and_its_build(tmp_path, editing):
    """A pack left behind by a reverted journal would claim an edit that no
    longer exists — the build outputs go with the journal. The client's
    *installed* half is untouched (uninstall reads its own stored state)."""
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
