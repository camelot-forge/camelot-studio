"""Home (`home.md`, P2): what this daemon serves, which projects this machine
has opened, whether a project's client still matches what was recorded — and
the four doors that change what the daemon serves (`/home/open`'s switch,
create, import, clone).

The read half (slice 2a) never lies about the disk: `exists`, `project` and
the health are read *now*, never from the registry. The writing half (slice
2b) writes inside a project's folder and nowhere else — never into a client —
and the switch warns about what it leaves behind instead of refusing.
"""

import json
import os
import signal
import subprocess
from pathlib import Path

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


def home_app(root, project=None, config_dir=None, **kwargs):
    """The daemon as `create_app` builds it — the session is reachable on
    `app.state` for the tests that need the lock to be real. `config_dir` is
    only passed when a test names one: the default is the machine's own, and a
    test must never write there."""
    if config_dir is not None:
        kwargs["config_dir"] = config_dir
    return create_app(
        ServeConfig(
            root=root,
            watch=False,
            open_browser=False,
            project=project,
            **kwargs,
        )
    )


def home_client(root, project=None, config_dir=None, **kwargs) -> TestClient:
    return TestClient(home_app(root, project, config_dir, **kwargs), base_url="http://127.0.0.1")


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


def test_rebase_reads_the_base_and_the_two_decisions_write_it(base, machine, tmp_path):  # noqa: F811
    """P8: the view is read-only, `keep-ours` pins, and the row says so after."""
    project = pj.new(tmp_path / "camelot-hills", client=base)
    (project.root / pj.MACHINE_FILE).write_text(json.dumps({"client": str(base)}), encoding="utf-8")
    client = home_client(base, config_dir=machine)

    clean = client.get("/api/v1/home/rebase", params={"path": str(project.root)}).json()
    assert clean["clean"] is True and clean["decision"] == ""

    (base / "gamedata.mpk").write_bytes(b"moved under us")
    view = client.get("/api/v1/home/rebase", params={"path": str(project.root)}).json()
    assert view["clean"] is False and view["moved"] == 1
    assert [item["resource"] for item in view["resources"]] == ["gamedata.mpk"]
    assert view["journal"] == []  # nothing in the journal wrote it

    kept = client.post(
        "/api/v1/home/rebase", json={"path": str(project.root), "decision": "keep-ours"}
    ).json()
    assert kept["decision"] == "keep-ours" and kept["written"] == 1

    found = client.get("/api/v1/home/health", params={"path": str(project.root)}).json()
    assert found["state"] == "pinned" and found["pinned"] == 1
    assert "kept ours" in found["detail"]


def test_rebase_adopts_and_refuses_a_decision_it_does_not_know(base, machine, tmp_path):  # noqa: F811
    project = pj.new(tmp_path / "camelot-hills", client=base)
    (project.root / pj.MACHINE_FILE).write_text(json.dumps({"client": str(base)}), encoding="utf-8")
    client = home_client(base, config_dir=machine)
    (base / "gamedata.mpk").write_bytes(b"moved under us")

    refused = client.post(
        "/api/v1/home/rebase", json={"path": str(project.root), "decision": "maybe"}
    )
    assert refused.status_code == 400
    assert "keep-ours" in refused.json()["detail"]

    adopted = client.post(
        "/api/v1/home/rebase", json={"path": str(project.root), "decision": "adopt"}
    ).json()
    assert adopted["written"] == 1
    health = client.get("/api/v1/home/health", params={"path": str(project.root)}).json()
    assert health["state"] == "ok" and health["pinned"] == 0


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


def test_a_daemon_that_serves_a_project_lists_it(tmp_path, base, monkeypatch, capsys):  # noqa: F811
    """The one writer of the recents: a daemon serving a project is the only
    thing that knows one was opened (`home.md` §D1) — and only a *project*
    belongs in the list: a legacy authoring directory is not one. Its browser
    also lands on the world editor, where the operator works (`home.md` §D6)."""
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
    banner = capsys.readouterr().out
    assert "/world" in banner  # a project: the editor is where the work is
    listed = recent.entries(tmp_path / "config" / "camelot-studio")
    assert [entry["name"] for entry in listed] == [project.name]
    assert listed[0]["path"] == str(project.root)

    # and without a project the browser lands on Home, the front door
    assert main(["serve", "--root", str(base), "--no-open", "--no-watch"]) == 0
    assert "/home" in capsys.readouterr().out


def test_the_lock_is_dropped_when_the_daemon_is_asked_to_stop(base, tmp_path, monkeypatch):  # noqa: F811
    """P §D13: the daemon drops the lock when it stops — and it stops *by
    signal*, which is the one path a `finally` never runs on (uvicorn
    re-raises the signal once its graceful shutdown is done, so the call after
    `uvicorn.run(...)` is never reached). The handler uvicorn restores before
    re-raising is ours, so this is where the lock has to go."""
    project = pj.new(tmp_path / "avalon", client=base, name="Avalon")
    session = home_app(base, project=project.root, config_dir=tmp_path / "config").state.session
    assert session.claim()["held"] is True
    assert pj.lock_state(project.root) is not None

    before = signal.getsignal(signal.SIGTERM)
    real_signal = signal.signal  # the restored call must not land in `calls`
    try:
        session.release_on_signal()
        handler = signal.getsignal(signal.SIGTERM)
        # what the signal would do, with nothing actually signalled
        calls: list[tuple] = []
        monkeypatch.setattr(signal, "signal", lambda signum, fn: calls.append((signum, fn)))
        monkeypatch.setattr(os, "kill", lambda pid, signum: calls.append((pid, signum)))
        handler(signal.SIGTERM, None)
    finally:
        real_signal(signal.SIGTERM, before)

    assert pj.lock_state(project.root) is None  # the lock was dropped
    assert calls == [(signal.SIGTERM, signal.SIG_DFL), (os.getpid(), signal.SIGTERM)]


# --- the doors (slice 2b) -----------------------------------------------------


def served(client: TestClient) -> str | None:
    """What the daemon serves right now, by name."""
    payload = client.get("/api/v1/home").json()
    return payload["project"]["name"] if payload["project"] else None


@pytest.fixture()
def served_client(editing):  # noqa: F811
    """`test_zones`' client plus the one file `project.new` insists on (its own
    binary, recorded as the base): the switch tests need a *project* whose base
    is this client — the daemon's root and the project's client are one."""
    root = Path(editing)
    (root / "game1127.dll").write_bytes(b"MZ fake client")
    (root / "gamedata.mpk").write_bytes(b"the tables")
    return editing


def test_open_switches_the_daemon_and_says_what_it_leaves(served_client, tmp_path, machine):
    """D3's switch: another project is served without a restart, and the answer
    says what the one being left still holds — a line, not a wall. The lock
    hands over: the old checkout is free, the new one is this daemon's."""
    avalon = pj.new(tmp_path / "avalon", client=served_client, name="Avalon")
    cotswold = pj.new(tmp_path / "cotswold", client=served_client, name="Cotswold")
    app = home_app(served_client, project=avalon.root, config_dir=machine)
    assert app.state.session.claim()["held"] is True
    client = TestClient(app, base_url="http://127.0.0.1")

    assert (
        client.post(
            "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 30000, "y": 31000}
        ).status_code
        == 200
    )
    answer = client.post("/api/v1/home/open", json={"path": str(cotswold.root)}).json()
    assert answer["opened"] is True and answer["name"] == "Cotswold"
    assert answer["left"] == {"path": str(avalon.root), "name": "Avalon", "pending": 1}
    assert answer["state"]["available"] is True
    assert answer["state"]["dir"] == str(cotswold.root)
    assert answer["state"]["pending"] == {"fixtures": 0, "rows": 0}  # Cotswold's own
    # the daemon serves it now, and the machine's list knows the visit
    assert served(client) == "Cotswold"
    assert [entry["name"] for entry in recent.entries(machine)] == ["Cotswold", "Avalon"]
    # the lock handed over
    assert pj.lock_state(avalon.root) is None
    holder = pj.lock_state(cotswold.root)
    assert holder is not None and holder.pid == os.getpid()


def test_open_warns_and_never_refuses_while_edits_wait(served_client, tmp_path, machine):
    """The journal *is* the save (`home.md` §D3.1): a project with unsaved work
    is left exactly like an empty one — the answer says what it keeps, and
    coming back shows the same pending count."""
    first = pj.new(tmp_path / "first", client=served_client, name="First")
    second = pj.new(tmp_path / "second", client=served_client, name="Second")
    client = home_client(served_client, project=first.root, config_dir=machine)
    client.post(
        "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 30000, "y": 31000}
    )

    answer = client.post("/api/v1/home/open", json={"path": str(second.root)})
    assert answer.status_code == 200
    assert answer.json()["left"]["pending"] == 1
    # and coming back shows the same pending count: nothing was saved *for* the
    # operator, and nothing was lost
    back = client.post("/api/v1/home/open", json={"path": str(first.root)}).json()
    assert back["state"]["pending"] == {"fixtures": 1, "rows": 0}


def test_switching_to_a_held_project_runs_read_only(served_client, tmp_path, machine):
    """P §D13 through Home: another live process holds the checkout, so the
    switch succeeds read-only — the holder is named and every write refuses."""
    avalon = pj.new(tmp_path / "avalon", client=served_client, name="Avalon")
    held = pj.new(tmp_path / "held", client=served_client, name="Held")
    pj.lock(held.root, author="michael")
    app = home_app(served_client, project=avalon.root, config_dir=machine)
    app.state.session.claim()
    client = TestClient(app, base_url="http://127.0.0.1")

    answer = client.post("/api/v1/home/open", json={"path": str(held.root)}).json()
    assert answer["state"]["read_only"] is True
    assert answer["state"]["holder"].startswith("michael (pid ")
    refused = client.post(
        "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 1, "y": 1}
    )
    assert refused.status_code == 400 and "read-only" in refused.json()["detail"]
    assert pj.lock_state(avalon.root) is None  # the old lock was handed over
    assert pj.lock_state(held.root) is not None  # and the held one is untouched


def test_the_client_is_re_resolved_from_the_project(base, machine, tmp_path):  # noqa: F811
    """D3.3: the client comes from the *project* — `machine.json` first — never
    inherited from the daemon's `--root`, and the asset index follows it."""
    from camelot.mpak import write_mpak

    other = tmp_path / "other-client"
    (other / "zones" / "zone000").mkdir(parents=True)
    (other / "game1127.dll").write_bytes(b"MZ other")
    (other / "gamedata.mpk").write_bytes(b"other tables")
    write_mpak(str(other / "zones" / "zones.mpk"), "zones", [("zones.dat", b"x")])
    here = pj.new(tmp_path / "here", client=base, name="Here")
    there = pj.new(tmp_path / "there", client=other, name="There")
    app = home_app(base, project=here.root, config_dir=machine)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/v1/assets").json()["total"] >= 2  # the base's index

    answer = client.post("/api/v1/home/open", json={"path": str(there.root)}).json()
    assert answer["warning"] == ""
    payload = client.get("/api/v1/home").json()
    assert payload["daemon"]["client"] == str(other)
    assert payload["daemon"]["zones"] == 1  # the base ships two
    # the index was rebuilt for the new client, in place
    assert app.state.index.root == other.resolve()
    assert client.get("/api/v1/assets").json()["total"] == 1


def test_a_client_no_machine_names_warns_and_stays(served_client, machine, tmp_path, monkeypatch):
    """D3.3's last fallback: the project names no client, so the daemon keeps
    the one it already reads — and says so, because that base may not be this
    project's."""
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    here = pj.new(tmp_path / "here", client=served_client, name="Here")
    bare = pj.new(tmp_path / "bare", name="Bare")
    client = home_client(served_client, project=here.root, config_dir=machine)

    answer = client.post("/api/v1/home/open", json={"path": str(bare.root)}).json()
    assert "names no client" in answer["warning"]
    assert served(client) == "Bare"
    assert client.get("/api/v1/home").json()["daemon"]["client"] == str(served_client)


def test_open_refuses_what_is_not_a_project_and_nothing_moves(world, machine, tmp_path):  # noqa: F811
    here = pj.new(tmp_path / "here", name="Here")
    client = home_client(world, project=here.root, config_dir=machine)

    refused = client.post("/api/v1/home/open", json={"path": str(world)})
    assert refused.status_code == 400 and "not a project" in refused.json()["detail"]
    assert served(client) == "Here"


def test_new_records_the_whole_client_and_opens_it(base, machine, tmp_path):  # noqa: F811
    """D4: create, then record every zone the client ships and the tables an
    edit reads — a partial record would make `client ok` a lie by omission."""
    client = home_client(base, config_dir=machine)
    root = tmp_path / "servers" / "avalon"
    answer = client.post(
        "/api/v1/home/new",
        json={
            "name": "Avalon",
            "dir": str(root),
            "client": str(base),
            "staging": True,
            "open": True,
        },
    ).json()
    assert answer["opened"] is True and answer["name"] == "Avalon"
    assert answer["zones"] == 2 and answer["resources"] > 5
    project = pj.load(root)
    assert project.data["environments"] == ["local", "staging", "production"]
    assert "zones/zone000/csv000.mpk#fixtures.csv" in project.fingerprints
    assert "gamedata.mpk#items.csv" in project.fingerprints  # shipped, so recorded
    assert "gamedata.mpk#monsters.csv" not in project.fingerprints  # never shipped
    assert (root / pj.MACHINE_FILE).is_file()
    assert served(client) == "Avalon"
    assert [entry["name"] for entry in recent.entries(machine)] == ["Avalon"]


def test_new_can_create_without_opening(base, machine, tmp_path):  # noqa: F811
    """The modal's "Open it in the World editor", unchecked: the project is
    written and listed, and the daemon keeps serving what it served."""
    client = home_client(base, config_dir=machine)
    answer = client.post(
        "/api/v1/home/new",
        json={"name": "Avalon", "dir": str(tmp_path / "avalon"), "open": False},
    ).json()
    assert answer["opened"] is False and answer["state"] is None
    assert answer["name"] == "Avalon" and answer["resources"] > 5
    assert served(client) is None
    assert [entry["name"] for entry in recent.entries(machine)] == ["Avalon"]


def test_new_uses_the_daemons_own_client_when_none_is_named(base, machine, tmp_path):  # noqa: F811
    client = home_client(base, config_dir=machine)
    answer = client.post(
        "/api/v1/home/new", json={"name": "Avalon", "dir": str(tmp_path / "avalon")}
    ).json()
    assert answer["opened"] is True
    assert pj.machine(answer["path"])["client"] == str(base)


def test_new_refuses_a_client_that_is_not_there(base, machine, tmp_path):  # noqa: F811
    client = home_client(base, config_dir=machine)
    refused = client.post(
        "/api/v1/home/new",
        json={"name": "X", "dir": str(tmp_path / "x"), "client": str(tmp_path / "ghost")},
    )
    assert refused.status_code == 400 and "no client directory" in refused.json()["detail"]
    assert not (tmp_path / "x").exists()  # refused before anything was written


def test_new_refuses_a_folder_that_is_already_a_project(base, machine, tmp_path):  # noqa: F811
    existing = pj.new(tmp_path / "avalon", client=base)
    client = home_client(base, config_dir=machine)
    refused = client.post(
        "/api/v1/home/new", json={"name": "Avalon", "dir": str(existing.root)}
    )
    assert refused.status_code == 400 and "already lives here" in refused.json()["detail"]


def test_import_makes_an_edits_folder_a_project_in_place(base, machine, tmp_path):  # noqa: F811
    """The import door (`project.md` §D14): the folder stays where it is, its
    own files stay, and the daemon can serve it at once."""
    folder = tmp_path / "old-session"
    folder.mkdir()
    (folder / "mod.json").write_text(json.dumps({"id": "oldsession"}), encoding="utf-8")
    (folder / "notes.md").write_text("mine", encoding="utf-8")
    client = home_client(base, config_dir=machine)

    answer = client.post(
        "/api/v1/home/import",
        json={"dir": str(folder), "client": str(base), "open": True},
    ).json()
    assert answer["opened"] is True and answer["name"] == "oldsession"
    assert (folder / pj.PROJECT_FILE).is_file()
    assert (folder / "notes.md").read_text(encoding="utf-8") == "mine"  # never overwritten
    assert served(client) == "oldsession"


def test_import_refuses_a_folder_with_nothing_to_import(base, machine, tmp_path):  # noqa: F811
    empty = tmp_path / "empty"
    empty.mkdir()
    refused = home_client(base, config_dir=machine).post(
        "/api/v1/home/import", json={"dir": str(empty)}
    )
    assert refused.status_code == 400
    assert "not an authoring directory" in refused.json()["detail"]


def _git(*args: str, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _have_git() -> bool:
    return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0


def test_clone_runs_git_and_opens_the_copy(base, machine, tmp_path):  # noqa: F811
    """The clone door: `git clone` by the daemon (the operator's own
    credentials), and the copy is served like any project — with this machine's
    own `machine.json`, since the repository cannot carry one."""
    if not _have_git():
        pytest.skip("git is not installed")
    origin = pj.new(tmp_path / "origin" / "avalon", client=base, name="Avalon")
    _git("init", "-q", "--initial-branch=main", cwd=origin.root)
    _git("config", "user.email", "operator@example.invalid", cwd=origin.root)
    _git("config", "user.name", "operator", cwd=origin.root)
    _git("add", "-A", cwd=origin.root)
    _git("commit", "-qm", "the project", cwd=origin.root)

    client = home_client(base, config_dir=machine)
    root = tmp_path / "servers" / "avalon"
    answer = client.post(
        "/api/v1/home/clone",
        json={"url": str(origin.root), "dir": str(root), "client": str(base), "open": True},
    ).json()
    assert answer["opened"] is True and answer["name"] == "Avalon"
    assert (root / pj.PROJECT_FILE).is_file()
    assert pj.machine(root)["client"] == str(base)  # this machine's own answer
    assert served(client) == "Avalon"


def test_clone_says_when_the_repository_is_not_a_project(base, machine, tmp_path):  # noqa: F811
    if not _have_git():
        pytest.skip("git is not installed")
    origin = tmp_path / "plain"
    origin.mkdir()
    (origin / "readme.md").write_text("nothing to see", encoding="utf-8")
    _git("init", "-q", "--initial-branch=main", cwd=origin)
    _git("config", "user.email", "operator@example.invalid", cwd=origin)
    _git("config", "user.name", "operator", cwd=origin)
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "plain", cwd=origin)

    client = home_client(base, config_dir=machine)
    root = tmp_path / "clone"
    answer = client.post("/api/v1/home/clone", json={"url": str(origin), "dir": str(root)}).json()
    assert answer["opened"] is False
    assert "not a project" in answer["warning"] and "Import" in answer["warning"]
    assert (root / "readme.md").is_file()  # the clone itself succeeded


def test_clone_refuses_a_folder_that_exists(base, machine, tmp_path):  # noqa: F811
    root = tmp_path / "there"
    root.mkdir()
    refused = home_client(base, config_dir=machine).post(
        "/api/v1/home/clone", json={"url": "https://example.invalid/x.git", "dir": str(root)}
    )
    assert refused.status_code == 400 and "already exists" in refused.json()["detail"]


def test_client_check_reads_a_folder_as_a_client(base, machine, tmp_path):  # noqa: F811
    """The create modal's own check (`home.md` §D4), before anything is
    written: a plain folder, a client, and one that is neither."""
    client = home_client(base, config_dir=machine)
    plain = tmp_path / "plain"
    plain.mkdir()

    ok = client.get("/api/v1/home/client", params={"path": str(base)}).json()
    assert ok["state"] == "ok" and ok["zones"] == 2 and "2 zones" in ok["detail"]
    assert client.get("/api/v1/home/client", params={"path": str(plain)}).json()["state"] == (
        "not-a-client"
    )
    assert client.get("/api/v1/home/client", params={"path": str(tmp_path / "x")}).json()[
        "state"
    ] == "missing"


def test_a_rows_health_carries_its_pending_work(served_client, tmp_path, machine):
    """One call, both chips: the row's health and the work waiting are read
    from the project in the same answer (`home.md` §D1)."""
    avalon = pj.new(tmp_path / "avalon", client=served_client, name="Avalon")
    client = home_client(served_client, project=avalon.root, config_dir=machine)
    client.post(
        "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 30000, "y": 31000}
    )

    found = client.get("/api/v1/home/health", params={"path": str(avalon.root)}).json()
    assert found["state"] == "ok"
    assert found["pending"] == {"fixtures": 1, "rows": 0}


def test_the_machine_list_carries_the_work_waiting(served_client, tmp_path, machine):
    """`home.md` §D1: the list carries no copies — the work waiting is read
    from each project when the page asks, so the chip's menu can say it
    without a `verify` per row."""
    avalon = pj.new(tmp_path / "avalon", client=served_client, name="Avalon")
    recent.record(avalon.root, name=avalon.name, directory=machine)
    client = home_client(served_client, project=avalon.root, config_dir=machine)
    client.post(
        "/api/v1/world/zone/0/edits", json={"kind": "move", "id": 2, "x": 30000, "y": 31000}
    )

    rows = client.get("/api/v1/home").json()["recents"]
    assert rows[0]["name"] == "Avalon" and rows[0]["pending"] == 1


def test_forget_drops_the_row_and_never_the_folder(world, machine, tmp_path):  # noqa: F811
    """`POST /home/forget` (`home.md` §D1): the entry goes, the folder stays —
    and the answer carries the list as it stands, so the page needs one round
    trip."""
    here = tmp_path / "avalon"
    here.mkdir(parents=True)
    recent.record(here, name="Avalon", directory=machine)
    client = home_client(world, config_dir=machine)

    answer = client.post("/api/v1/home/forget", json={"path": str(here)}).json()
    assert answer["forgotten"] is True and answer["recents"] == []
    assert here.is_dir()  # the folder itself is never touched

    again = client.post("/api/v1/home/forget", json={"path": str(here)}).json()
    assert again["forgotten"] is False and again["recents"] == []


def test_health_never_migrates_the_project_it_reads(world, machine, tmp_path):  # noqa: F811
    """A legacy single-file journal is *not* touched by a row's health: the
    migration is a write, and Home is a reader (`home.md` §D2)."""
    from camelot import journal as journal_mod

    folder = tmp_path / "old"
    folder.mkdir()
    (folder / pj.PROJECT_FILE).write_text(
        json.dumps({"format": pj.FORMAT, "name": "old", "base": {}}), encoding="utf-8"
    )
    legacy = folder / journal_mod.LEGACY_FILE
    legacy.write_text(json.dumps({"schema": journal_mod.LEGACY_SCHEMA, "ops": []}), encoding="utf-8")

    found = home_client(world, config_dir=machine).get(
        "/api/v1/home/health", params={"path": str(folder)}
    ).json()
    assert found["pending"] == {}
    assert legacy.is_file()  # nothing moved it aside


# -- the front door: a daemon that started before any client was named --------


def front_door(tmp_path, machine, monkeypatch):
    """Home without a client (`home.md` §D4): the modal is where one is named."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    return home_client(None, config_dir=machine)


def test_a_front_door_daemon_serves_home_without_a_client(tmp_path, machine, monkeypatch):
    client = front_door(tmp_path, machine, monkeypatch)

    payload = client.get("/api/v1/home").json()

    assert payload["daemon"]["client"] is None
    assert payload["daemon"]["zones"] == 0
    assert payload["project"] is None


def test_the_create_door_requires_a_client_when_the_daemon_has_none(
    tmp_path, machine, monkeypatch
):
    client = front_door(tmp_path, machine, monkeypatch)

    refused = client.post(
        "/api/v1/home/new", json={"name": "Avalon", "dir": str(tmp_path / "avalon")}
    )

    assert refused.status_code == 400
    assert "client folder" in refused.json()["detail"]
    assert not (tmp_path / "avalon").exists()


def test_the_front_door_adopts_the_client_it_creates(
    base, tmp_path, machine, monkeypatch  # noqa: F811
):
    client = front_door(tmp_path, machine, monkeypatch)

    answer = client.post(
        "/api/v1/home/new",
        json={
            "name": "Avalon",
            "dir": str(tmp_path / "avalon"),
            "client": str(base),
            "open": True,
        },
    ).json()

    assert answer["opened"] is True
    payload = client.get("/api/v1/home").json()
    assert payload["daemon"]["client"] == str(base)
    assert payload["daemon"]["zones"] >= 2


def test_opening_a_project_that_names_no_client_is_refused_at_the_front_door(
    tmp_path, machine, monkeypatch
):
    project = pj.new(tmp_path / "avalon", name="Avalon")
    client = front_door(tmp_path, machine, monkeypatch)

    refused = client.post("/api/v1/home/open", json={"path": str(project.root)})

    assert refused.status_code == 400
    assert "names no client" in refused.json()["detail"]
    assert "project machine" in refused.json()["detail"]


def test_serve_starts_without_a_client(tmp_path, monkeypatch, capsys):
    """The first run: no client, no project — Home is the front door."""
    import uvicorn

    from camelot.cli import main

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)

    assert main(["serve", "--no-open", "--no-watch"]) == 0
    assert "no client yet" in capsys.readouterr().out


def _serverconfig(path):
    (path / "server" / "config").mkdir(parents=True)
    file = path / "server" / "config" / "serverconfig.xml"
    file.write_text(
        "<root><DBConnectionString>"
        "Server=localhost;Port=3307;Database=forge_project_local;UserId=root;Password=x"
        "</DBConnectionString></root>",
        encoding="utf-8",
    )
    return file


def test_new_can_record_the_server_config(base, machine, tmp_path):  # noqa: F811
    """The same modal names the server: the environment, its database and its
    config are derived from the file, so the project opens with its mobs."""
    config = _serverconfig(tmp_path / "env-local")
    client = home_client(base, config_dir=machine)

    answer = client.post(
        "/api/v1/home/new",
        json={
            "name": "Avalon",
            "dir": str(tmp_path / "avalon"),
            "client": str(base),
            "db_config": str(config),
        },
    ).json()

    local = pj.machine(answer["path"])["environments"]["local"]
    assert local["db_config"] == str(config)
    assert local["database"] == "forge_project_local"
    assert local["root"] == str(tmp_path / "env-local")
    assert local["db"]["port"] == 3307


def test_new_refuses_a_server_config_that_is_not_there(base, machine, tmp_path):  # noqa: F811
    client = home_client(base, config_dir=machine)

    refused = client.post(
        "/api/v1/home/new",
        json={
            "name": "Avalon",
            "dir": str(tmp_path / "avalon"),
            "client": str(base),
            "db_config": str(tmp_path / "nope.xml"),
        },
    )

    assert refused.status_code == 400 and "serverconfig" in refused.json()["detail"]
    assert not (tmp_path / "avalon").exists()
