"""Home, the read half (`home.md`, P2 slice 2a): what this daemon serves,
which projects this machine has opened, and — one at a time — whether a
project's client still matches what was recorded.

Nothing here writes: opening, creating and cloning are slice 2b. The one
thing this suite watches for is that the rows never lie: `exists`, `project`
and the health are read from the disk *now*, never from the registry.
"""

import json

import pytest
from fastapi.testclient import TestClient
from test_project import base  # noqa: F401  (the synthetic client with a binary)
from test_serve_editor import editing  # noqa: F401
from test_zones import FIXTURES, world  # noqa: F401

from camelot import project as pj
from camelot import recent
from camelot.serve.app import create_app
from camelot.serve.config import ServeConfig


@pytest.fixture()
def machine(tmp_path):
    """This machine's own directory — never the real `~/.config`."""
    return tmp_path / "config"


def home_client(root, project=None, config_dir=None, **kwargs) -> TestClient:
    return TestClient(
        create_app(
            ServeConfig(
                root=root,
                watch=False,
                open_browser=False,
                project=project,
                config_dir=config_dir,
                **kwargs,
            )
        ),
        base_url="http://127.0.0.1",
    )


def test_home_says_what_the_daemon_serves(world, machine):  # noqa: F811
    """The first question Home answers is "what does this daemon serve" — its
    client, its zones, its port, its build — before any project is named."""
    payload = home_client(world, config_dir=machine).get("/api/v1/home").json()
    assert payload["daemon"]["client"] == str(world)
    assert payload["daemon"]["zones"] >= 2  # the fixture ships zone000 and ZONE008
    assert payload["daemon"]["port"] == 8760 and payload["daemon"]["build"]
    assert payload["project"] is None
    assert payload["recents"] == [] and payload["recent_error"] == ""


def test_a_missing_registry_is_normal_and_a_malformed_one_is_named(world, machine):  # noqa: F811
    machine.mkdir(parents=True)
    (machine / recent.FILE).write_text("{oh no", encoding="utf-8")
    payload = home_client(world, config_dir=machine).get("/api/v1/home").json()
    assert payload["recents"] == []
    assert recent.FILE in payload["recent_error"]  # named, never guessed
    assert payload["daemon"]["client"]  # the page still answers


def test_the_rows_carry_what_the_disk_says_now(world, machine, tmp_path):  # noqa: F811
    """The registry is a shortcut, not a record: a folder that moved or that
    was never a project is said by the row, at the moment it is read."""
    here = tmp_path / "servers" / "avalon"
    here.mkdir(parents=True)
    gone = tmp_path / "servers" / "cotswold"
    gone.mkdir()
    recent.record(gone, directory=machine)
    recent.record(here, directory=machine)
    gone.rmdir()

    payload = home_client(world, config_dir=machine).get("/api/v1/home").json()
    rows = {row["path"]: row for row in payload["recents"]}
    assert rows[str(here.resolve())]["exists"] is True
    assert rows[str(here.resolve())]["project"] is False
    assert rows[str(gone.resolve())]["exists"] is False


def test_the_current_project_says_its_pending_work(editing, tmp_path, machine):  # noqa: F811
    """`pending` counts the way the panel counts: distinct fixture ids, and the
    rows the database has not been given. One definition, one number."""
    session = tmp_path / "avalon"
    pj.new(session)  # the record alone: this test never reads a base
    client = home_client(editing, project=session, config_dir=machine)

    payload = client.get("/api/v1/home").json()
    assert payload["project"]["path"] == str(session)
    assert payload["project"]["name"] == "avalon"
    assert payload["project"]["pending"] == {"fixtures": 0, "rows": 0}
    assert payload["project"]["read_only"] is False and payload["project"]["holder"] == ""

    assert (
        client.post(
            "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 30000, "y": 31000}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/home").json()["project"]["pending"] == {"fixtures": 1, "rows": 0}
    # the panel says the same thing: one number, two places
    state = client.get("/api/v1/world/edit").json()
    assert state["pending"] == {"fixtures": 1, "rows": 0}


def test_health_reads_the_base_back_and_names_what_moved(base, machine, tmp_path):  # noqa: F811
    project = pj.new(tmp_path / "camelot-hills", client=base)
    (project.root / pj.MACHINE_FILE).write_text(json.dumps({"client": str(base)}), encoding="utf-8")
    client = home_client(base, config_dir=machine)

    found = client.get("/api/v1/home/health", params={"path": str(project.root)}).json()
    assert found["state"] == "ok" and found["name"] == "camelot-hills"
    assert "unchanged" in found["detail"]

    (base / "gamedata.mpk").write_bytes(b"moved under us")
    changed = client.get("/api/v1/home/health", params={"path": str(project.root)}).json()
    assert changed["state"] == "changed" and "no longer match" in changed["detail"]
    assert [finding["resource"] for finding in changed["findings"]] == ["gamedata.mpk"]

    (base / "gamedata.mpk").unlink()
    missing = client.get("/api/v1/home/health", params={"path": str(project.root)}).json()
    assert missing["state"] == "missing"


def test_health_says_when_no_client_can_be_resolved(world, machine, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    session = tmp_path / "avalon"
    pj.new(session)
    found = (
        home_client(world, config_dir=machine)
        .get("/api/v1/home/health", params={"path": str(session)})
        .json()
    )
    assert found["state"] == "no-client"
    assert pj.MACHINE_FILE in found["detail"]  # the way out is named


def test_health_refuses_what_is_not_a_project_by_name(world, machine):  # noqa: F811
    response = home_client(world, config_dir=machine).get(
        "/api/v1/home/health", params={"path": str(world)}
    )
    assert response.status_code == 400
    assert "not a project" in response.json()["detail"]


def test_browse_lists_folders_and_flags_the_projects(world, machine, tmp_path):  # noqa: F811
    """The daemon is local, so a path field needs no native picker: it lists
    the folders, flags the ones that are projects, and hides the dotfolders."""
    servers = tmp_path / "servers"
    (servers / "avalon").mkdir(parents=True)
    (servers / "hills").mkdir()
    (servers / "hills" / pj.PROJECT_FILE).write_text(
        json.dumps({"format": pj.FORMAT}), encoding="utf-8"
    )
    (servers / ".git").mkdir()

    payload = (
        home_client(world, config_dir=machine)
        .get("/api/v1/home/browse", params={"path": str(servers)})
        .json()
    )
    assert [entry["name"] for entry in payload["dirs"]] == ["avalon", "hills"]
    assert [entry["project"] for entry in payload["dirs"]] == [False, True]
    assert payload["path"] == str(servers) and payload["parent"] == str(tmp_path)

    refused = home_client(world, config_dir=machine).get(
        "/api/v1/home/browse", params={"path": str(tmp_path / "ghost")}
    )
    assert refused.status_code == 400 and "no such directory" in refused.json()["detail"]


def test_a_daemon_that_serves_a_project_lists_it(tmp_path, base, monkeypatch):  # noqa: F811
    """The one writer of the recents: a daemon serving a project is the only
    thing that knows one was opened (`home.md` §D1) — and only a *project*
    belongs in the list: a legacy authoring directory is not one."""
    import uvicorn

    from camelot.cli import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)
    project = pj.new(tmp_path / "camelot-hills", client=base)
    plain = tmp_path / "authoring"
    plain.mkdir()

    for directory in (project.root, plain):
        assert (
            main(["serve", "--root", str(base), "--no-open", "--no-watch",
                  "--project", str(directory)])
            == 0
        )
    listed = recent.entries(tmp_path / "config" / "camelot-studio")
    assert [entry["name"] for entry in listed] == [project.name]
    assert listed[0]["path"] == str(project.root)
