"""The project container: the file, the base, the lock, the scaffolding.

The design is `workspace/notes/project.md`. None of this needs a
client or the game: the base is a handful of files a fixture writes, which is
the point of the format — a project is testable without 2.3 GB.
"""

import json
import socket
import subprocess
import sys

import pytest

from camelot import project as pj
from camelot.cli import main
from camelot.mpak import write_mpak


@pytest.fixture()
def base(tmp_path):
    """A synthetic base with the three shapes a real one uses: a plain file
    (the client's binary), a big archive (the tables), and an entry *inside*
    an archive (the zone index)."""
    root = tmp_path / "client"
    (root / "zones").mkdir(parents=True)
    (root / "game1127.dll").write_bytes(b"MZ fake client")
    (root / "gamedata.mpk").write_bytes(b"fake tables")
    write_mpak(str(root / "zones" / "zones.mpk"), "zones", [("zones.dat", b"the index")])
    return root


@pytest.fixture()
def project(tmp_path, base):
    return pj.new(
        tmp_path / "camelot-hills", client=base, author="Michael", created="2026-10-15T00:00:00Z"
    )


# -- creating ----------------------------------------------------------------


def test_new_writes_the_file_and_the_shape(project):
    """A fresh clone must have the project's shape: git does not track empty
    directories, so each one carries its `.gitkeep`."""
    assert project.name == "camelot-hills"
    assert project.format == pj.FORMAT
    assert project.created == "2026-10-15T00:00:00Z"
    assert project.cache == "shared"
    assert project.environments == ["local", "production"]
    written = json.loads((project.root / pj.PROJECT_FILE).read_text())
    assert written["format"] == pj.FORMAT
    for relative in pj._DIRECTORIES:
        assert (project.root / relative).is_dir(), relative
    assert (project.root / "notes.md").is_file()
    assert (project.root / "deploy" / "log.jsonl").is_file()
    assert (project.root / pj.MACHINE_FILE).is_file()  # the client we passed
    assert (project.root / f"{pj.MACHINE_FILE}.example").is_file()


def test_new_records_the_base_by_fingerprint(project, base):
    """The base is *referenced*: three resources, three hashes — including one
    read out of an archive entry."""
    recorded = project.fingerprints
    assert set(recorded) == {"game1127.dll", "gamedata.mpk", "zones/zones.mpk#zones.dat"}
    assert all(value.startswith("sha256:") for value in recorded.values())
    assert len(set(recorded.values())) == 3
    assert (
        pj.fingerprint_resource(base, "zones/zones.mpk#zones.dat")
        == recorded["zones/zones.mpk#zones.dat"]
    )


def test_new_refuses_a_missing_resource_by_name(tmp_path, base):
    """Half a base is worse than no base: a project that claims to have read a
    file it never saw would lie in every report that follows."""
    (base / "gamedata.mpk").unlink()
    with pytest.raises(pj.ProjectError, match="gamedata.mpk"):
        pj.new(tmp_path / "half", client=base)


def test_new_refuses_a_directory_that_already_holds_a_project(project):
    with pytest.raises(pj.ProjectError, match="already lives here"):
        pj.new(project.root)


def test_new_without_a_client_is_allowed_and_says_so(tmp_path):
    """A project may exist before its base is at hand — the Home flow creates
    one, then points it at a client."""
    fresh = pj.new(tmp_path / "later")
    assert fresh.fingerprints == {}
    assert pj.info(fresh)["client"] is None


def test_new_refuses_a_bad_cache_policy(tmp_path):
    with pytest.raises(pj.ProjectError, match="cache must be"):
        pj.new(tmp_path / "x", cache="wherever")


def test_new_refuses_an_environment_name_that_is_not_a_slug(tmp_path):
    with pytest.raises(pj.ProjectError, match="environment names"):
        pj.new(tmp_path / "x", environments=["Production Nord"])


def test_new_can_initialise_a_repository(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git is not installed")
    fresh = pj.new(tmp_path / "team", git=True)
    assert pj.is_git_repo(fresh.root)
    # and nothing else: no commit, no remote — the history is the operator's
    log = subprocess.run(
        ["git", "-C", str(fresh.root), "rev-parse", "HEAD"], capture_output=True, check=False
    )
    assert log.returncode != 0


# -- reading -----------------------------------------------------------------


def test_load_refuses_a_format_it_does_not_know(tmp_path):
    root = tmp_path / "future"
    root.mkdir()
    (root / pj.PROJECT_FILE).write_text(json.dumps({"format": "camelot.project/9"}))
    with pytest.raises(pj.ProjectError) as error:
        pj.load(root)
    message = str(error.value)
    assert "camelot.project/9" in message and pj.FORMAT in message


def test_load_refuses_a_directory_that_is_not_a_project(tmp_path):
    with pytest.raises(pj.ProjectError, match="not a project"):
        pj.load(tmp_path)


def test_verify_reads_the_base_back(project, base):
    assert [finding["state"] for finding in pj.verify(project)] == ["ok", "ok", "ok"]
    (base / "gamedata.mpk").write_bytes(b"a game update rewrote this")
    states = {finding["resource"]: finding["state"] for finding in pj.verify(project)}
    assert states["gamedata.mpk"] == "moved"
    (base / "game1127.dll").unlink()
    states = {finding["resource"]: finding["state"] for finding in pj.verify(project)}
    assert states["game1127.dll"] == "missing"
    assert states["zones/zones.mpk#zones.dat"] == "ok"


def test_verify_without_a_client_names_how_to_give_it_one(project, monkeypatch):
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    (project.root / pj.MACHINE_FILE).unlink()
    with pytest.raises(pj.ProjectError) as error:
        pj.verify(project)
    assert pj.MACHINE_FILE in str(error.value) and "DAOC_CLIENT" in str(error.value)


def test_the_machine_file_wins_over_the_environment(project, base, monkeypatch):
    """Nothing tracked holds a path (that is what makes the project portable):
    the machine's own answer comes first, the environment second."""
    monkeypatch.setenv("DAOC_CLIENT", str(project.root / "nowhere"))
    assert pj.client_root(project) == base
    (project.root / pj.MACHINE_FILE).unlink()
    assert pj.client_root(project) == project.root / "nowhere"


def test_author_comes_from_the_machine_file_then_the_os(project, tmp_path, monkeypatch):
    assert pj.author_name(project.root) == "michael"
    (project.root / pj.MACHINE_FILE).unlink()
    monkeypatch.setenv("USER", "Lyra Belacqua")
    monkeypatch.setattr(pj.getpass, "getuser", lambda: "Lyra Belacqua")
    assert pj.author_name(project.root) == "lyra-belacqua"


def test_info_answers_what_home_and_the_cli_ask(project):
    shown = pj.info(project)
    assert shown["name"] == "camelot-hills"
    assert shown["environments"] == ["local", "production"]
    assert shown["resources"] == 3
    assert shown["git"] is False
    assert shown["shards"] == [] and shown["releases"] == []
    assert shown["lock"] is None
    assert shown["author"] == "michael"


# -- the lock ----------------------------------------------------------------


def test_the_lock_names_its_holder_and_releases_cleanly(project):
    holder = pj.lock(project, author="Michael")
    assert pj.lock_state(project) == holder
    assert holder.host == socket.gethostname()
    with pytest.raises(pj.ProjectError) as error:
        pj.lock(project, author="Lyra")
    assert "michael" in str(error.value).lower() and str(holder.pid) in str(error.value)
    assert pj.unlock(project) is True
    assert pj.lock_state(project) is None
    pj.lock(project, author="Lyra")  # acquirable again
    assert pj.lock_state(project).author == "lyra"


def test_a_dead_holder_can_be_taken_only_when_asked(project):
    """A crashed session must not make a checkout unwritable forever — but the
    decision is explicit, and the holder is named either way."""
    finished = subprocess.Popen([sys.executable, "-c", ""])
    finished.wait()  # reaped: the pid is gone, the way a crashed session leaves it
    (project.root / pj.LOCK_FILE).write_text(
        json.dumps(
            {"pid": finished.pid, "host": socket.gethostname(), "author": "ghost", "at": "…"}
        )
    )
    stale = pj.lock_state(project)
    assert stale is not None and stale.is_alive is False
    with pytest.raises(pj.ProjectError, match="ghost"):
        pj.lock(project, author="Michael", take_stale=False)
    assert pj.lock(project, author="Michael").author == "michael"


def test_a_half_written_lock_is_not_a_holder(project):
    (project.root / pj.LOCK_FILE).write_text("{oh no")
    assert pj.lock_state(project) is None
    pj.lock(project, author="Michael")  # the next writer replaces it


# -- the scaffolding ---------------------------------------------------------


def test_gitignore_covers_the_machine_the_derived_and_the_payloads(project):
    rules = (project.root / ".gitignore").read_text()
    for ignored in (
        "machine.json",
        "project.lock",
        "ids.json",
        "cache/",
        "releases/*/server/",
        "feed/",
    ):
        assert ignored in rules, ignored


def test_gitattributes_union_merges_the_log_and_tracks_art_through_lfs(project):
    rules = (project.root / ".gitattributes").read_text()
    assert "deploy/log.jsonl merge=union" in rules
    assert "*.npk filter=lfs" in rules and "*.dds filter=lfs" in rules


# -- the CLI -----------------------------------------------------------------


def test_cli_new_info_and_verify_round_trip(tmp_path, base, capsys, monkeypatch):
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    assert (
        main(["project", "new", "camelot-hills", "--dir", str(tmp_path), "--client", str(base)])
        == 0
    )
    assert "camelot-hills" in capsys.readouterr().out
    assert main(["project", "info", "--dir", str(tmp_path / "camelot-hills")]) == 0
    shown = capsys.readouterr().out
    assert "camelot-hills" in shown and "local, production" in shown and "3 resources" in shown
    assert main(["project", "verify", "--dir", str(tmp_path / "camelot-hills")]) == 0
    assert "ok" in capsys.readouterr().out
    (base / "gamedata.mpk").write_bytes(b"moved")
    assert main(["project", "verify", "--dir", str(tmp_path / "camelot-hills")]) == 1
    assert "moved" in capsys.readouterr().out


def test_cli_refuses_a_directory_that_is_not_a_project(tmp_path, capsys):
    assert main(["project", "info", "--dir", str(tmp_path)]) == 1
    assert "not a project" in capsys.readouterr().err
