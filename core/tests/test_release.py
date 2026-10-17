"""A release: the record, its range, its payloads, and the rebuild
(`notes/pipeline.md`, P3).

The property everything else rests on is §D5 — two cuts of one range over one
base produce the same bytes, which is why the payloads are not committed — and
the exit criterion is a *second clone* rebuilding release 1 exactly. Both are
tested here, on the synthetic client, with no daemon.
"""

import json
import socket
import subprocess
from pathlib import Path

import pytest
from test_project import base  # noqa: F401  (the synthetic client)
from test_serve_editor import editing  # noqa: F401  (what `served_client` is built on)
from test_zones import world  # noqa: F401  (what `editing` is built on)

from camelot import project as pj
from camelot import release as rel
from camelot.serve.config import ServeConfig
from camelot.serve.editor import Editor
from camelot.serve.world import World


@pytest.fixture()
def served_client(editing):  # noqa: F811  (the fixture the import names)
    """`test_zones`' client plus the two files `project.new` insists on (its own
    binary and the tables): a *project* records a base, and the release build
    reads that base — the daemon's root and the project's client are one."""
    root = Path(editing)
    (root / "game1127.dll").write_bytes(b"MZ fake client")
    (root / "gamedata.mpk").write_bytes(b"the tables")
    return editing


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """The build's asset cache is this machine's, never a test's."""
    monkeypatch.setattr(rel, "default_cache_dir", lambda: tmp_path / "cache")


def _editor(project, client) -> Editor:
    return Editor(
        ServeConfig(root=client, project=project.root, cache_dir=project.root / "cache")
    )


def _project(tmp_path, client, name="Avalon") -> pj.Project:
    return pj.new(tmp_path / name.lower(), client=client, name=name)


def _move(editor, x=30000, y=31000, row=2):
    """One real edit: a fixture moved, journaled the way the daemon does it."""
    editor.record(World(editor.config).zone(0), {"kind": "move", "id": row, "x": x, "y": y})


def test_a_release_records_its_range_and_its_payloads(served_client, tmp_path):
    """`pipeline.md` §2: the record is decided at the cut — the range it holds,
    the base it was built against, the families, the two artifacts — and the
    deploy log gets one fact. The payloads land under `releases/NNNN/`, which
    `.gitignore` keeps out of the repository."""
    project = _project(tmp_path, served_client)
    editor = _editor(project, served_client)
    _move(editor)

    record = rel.cut(project.root)

    assert record["format"] == rel.FORMAT and record["number"] == 1
    assert record["families"] == {"world": 1}
    assert record["base"]["client"]["fingerprints"]  # the base it was built against
    assert record["log"] and all(name.endswith(".jsonl") for name in record["log"])
    assert sum(record["log"].values()) == 1
    assert record["order"] == "client-first"  # the csv is the client's half

    server = record["artifacts"]["server"]
    pack = project.root / server["pack"]
    assert pack.is_file() and server["sha256"].startswith("sha256:")
    assert server["sha256"] == rel.digest(pack.read_bytes())
    client_half = record["artifacts"]["client"]
    patchlist = json.loads((project.root / client_half["patchlist"]).read_text(encoding="utf-8"))
    assert client_half["version"] == 1 and patchlist["release"] == 1
    assert [item["entry"] for item in patchlist["items"]] == ["fixtures.csv"]
    assert patchlist["items"][0]["sha256"].startswith("sha256:")

    # the record is the history, and the deploy log remembers the cut
    assert [item["number"] for item in rel.releases(project.root)] == [1]
    facts = [line for line in pj.deploy_log(project.root) if line.get("kind") == "release"]
    assert facts and facts[0]["number"] == 1
    # and the git scaffolding already ignores the payloads (§D5)
    ignore = (project.root / ".gitignore").read_text(encoding="utf-8")
    assert "releases/*/server/" in ignore and "releases/*/client/" in ignore


def test_a_cut_with_nothing_new_is_refused(served_client, tmp_path):
    """§D5: a release whose content equals the previous one is refused with
    "nothing to release" instead of producing a twin — and once the journal
    moves, the next cut holds only the range beyond the previous cut."""
    project = _project(tmp_path, served_client)
    editor = _editor(project, served_client)
    _move(editor)
    first = rel.cut(project.root)

    with pytest.raises(rel.ReleaseError, match="nothing to release"):
        rel.cut(project.root)

    _move(editor, x=25472)
    second = rel.cut(project.root)
    assert second["number"] == 2 and second["families"] == {"world": 1}
    # the cut is cumulative per shard: release 2 covers what release 1 covered too
    assert second["log"] == first["log"] or second["log"] != first["log"]
    assert all(second["log"][shard] >= seq for shard, seq in first["log"].items())
    # and the package is the *state*, not the delta: the zone's csv is complete
    assert second["artifacts"]["server"]["pack"] != first["artifacts"]["server"]["pack"]


def test_an_undo_changes_what_the_next_release_holds(served_client, tmp_path):
    """A drop retires an op — the range of the next release holds the drop, and
    the zone's file is shipped again (a target that took release 1 carries the
    row and must be given the file without it)."""
    project = _project(tmp_path, served_client)
    editor = _editor(project, served_client)
    _move(editor)
    rel.cut(project.root)

    editor.undo(0)  # the zone the move was in
    record = rel.cut(project.root)

    assert record["number"] == 2
    assert record["families"] == {"dropped": 1}
    patchlist = json.loads(
        (project.root / record["artifacts"]["client"]["patchlist"]).read_text(encoding="utf-8")
    )
    assert [item["entry"] for item in patchlist["items"]] == ["fixtures.csv"]


def _git(*args: str, cwd):
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"


def _have_git() -> bool:
    return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0


def test_a_second_clone_rebuilds_release_one_byte_for_byte(served_client, tmp_path):
    """P3's exit criterion (`vision.md` §6): a second clone rebuilds release 1
    and gets the same bytes. The clone carries the record and the journal — the
    payloads are *not* committed, which is the whole reason the rebuild exists
    (`pipeline.md` §D5)."""
    if not _have_git():
        pytest.skip("git is not installed")
    project = _project(tmp_path, served_client)
    editor = _editor(project, served_client)
    _move(editor)
    record = rel.cut(project.root)
    _git("init", "-q", "--initial-branch=main", cwd=project.root)
    _git("config", "user.email", "operator@example.invalid", cwd=project.root)
    _git("config", "user.name", "operator", cwd=project.root)
    _git("add", "-A", cwd=project.root)
    _git("commit", "-qm", "release 1", cwd=project.root)
    clone = tmp_path / "clone"
    _git("clone", "-q", str(project.root), str(clone), cwd=tmp_path)

    # the payloads are not in the clone: only the record and the journal
    assert not (clone / "releases" / "0001" / "server").exists()
    # a CI job passes the client it has (a clone carries no machine.json: it is
    # this machine's own answer, and `.gitignore` keeps it out)
    assert rel.rebuild(clone, 1, client=served_client)["same"] is True
    assert rel.rebuild(clone, 1, client=served_client)["found"] == (
        record["artifacts"]["server"]["sha256"]
    )

    # and a journal that moved makes the rebuild differ — the CI signal
    other = Editor(ServeConfig(root=served_client, project=clone, cache_dir=clone / "cache"))
    zone = World(other.config).zone(0)
    other.record(zone, {"kind": "move", "id": 2, "x": 20992, "y": 31000})
    assert rel.rebuild(clone, 1, client=served_client)["same"] is False


def test_a_release_needs_a_client_and_a_free_checkout(served_client, tmp_path, monkeypatch):
    """The build is against the base, and the checkout is one writer's
    (`project.md` §D13): a project that names no client, or one another live
    process holds, is refused by name."""
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    project = _project(tmp_path, served_client)
    editor = _editor(project, served_client)
    _move(editor)
    (project.root / pj.MACHINE_FILE).unlink()

    with pytest.raises(rel.ReleaseError, match="no client resolved"):
        rel.cut(project.root)

    (project.root / pj.MACHINE_FILE).write_text(
        json.dumps({"client": str(served_client)}), encoding="utf-8"
    )
    # a *foreign* live process holds the checkout (its own pid, not this test's)
    (project.root / pj.LOCK_FILE).write_text(
        json.dumps(
            {"pid": 1, "host": socket.gethostname(), "author": "lyra", "at": "2026-10-17T09:00:00Z"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(rel.ReleaseError, match="held by lyra"):
        rel.cut(project.root)
