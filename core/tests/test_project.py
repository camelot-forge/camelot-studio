"""The project container: the file, the base, the lock, the scaffolding.

None of this needs a client or the game: the base is a handful of files a
fixture writes, which is the point of the format — a project is testable
without 2.3 GB.
"""

import json
import os
import socket
import subprocess
import sys

import pytest

from camelot import project as pj
from camelot.cli import main
from camelot.mpak import write_mpak


@pytest.fixture()
def base(tmp_path):
    """The synthetic client, as a fixture (see `synthetic_base`)."""
    return synthetic_base(tmp_path)


def synthetic_base(tmp_path):
    """A synthetic base with the three shapes a real one uses: a plain file
    (the client's binary), a big archive (the tables), and an entry *inside*
    an archive (the zone index). Plus two zones — one of them in an UPPER-CASE
    folder, the way the retail extraction disagrees with itself."""
    root = tmp_path / "client"
    (root / "zones").mkdir(parents=True)
    (root / "game1127.dll").write_bytes(b"MZ fake client")
    (root / "gamedata.mpk").write_bytes(b"fake tables")
    write_mpak(str(root / "zones" / "zones.mpk"), "zones", [("zones.dat", b"the index")])
    write_mpak(str(root / "gamedata.mpk"), "gamedata", [("items.csv", b"the items")])
    for folder in ("zone000", "ZONE008"):
        (root / "zones" / folder).mkdir(parents=True, exist_ok=True)
    write_mpak(
        str(root / "zones" / "zone000" / "csv000.mpk"),
        "csv000",
        [("fixtures.csv", b"the placements"), ("nifs.csv", b"the catalogue")],
    )
    write_mpak(
        str(root / "zones" / "zone000" / "dat000.mpk"),
        "dat000",
        [("sector.dat", b"the metadata"), ("terrain.pcx", b"the heightmap")],
    )
    write_mpak(
        str(root / "zones" / "ZONE008" / "csv008.mpk"),
        "csv008",
        [("fixtures.csv", b"another zone's placements")],
    )
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


def test_claim_is_the_daemon_s_own_way_to_take_the_checkout(project):
    """A long-running process takes the checkout for its session (`project.md`
    §D13): the second one is told who holds it, by name, instead of guessing —
    and a 6.3 authoring directory that is not a project has nothing to lock."""
    held = pj.claim(project.root, author="Michael")
    assert held["held"] is True and held["holder"]["author"] == "michael"
    assert pj.lock_state(project.root) is not None  # a bare path names the project too

    second = pj.claim(project.root, author="Lyra")
    assert second["held"] is False
    assert second["by"]["author"] == "michael" and second["by"]["pid"] == os.getpid()

    assert pj.release(project.root) is True
    assert pj.claim(project.root, author="Lyra")["held"] is True  # free again
    assert pj.release(project.root) is True
    assert pj.release(project.root) is False  # nothing to let go

    plain = project.root.parent / "authoring"
    plain.mkdir()
    assert pj.claim(plain, author="Michael") == {"held": True, "holder": None}
    assert pj.release(plain) is False


def test_ids_are_derived_and_a_malformed_record_is_refused_by_name(project):
    """`ids.json` is derived (`project.md` §D8): missing is normal, since the
    build recomputes it — and a format this build does not read is refused by
    name, never silently started over."""
    assert pj.ids(project.root) == {"format": "camelot.ids/1", "zones": {}, "tombstones": {}}
    written = pj.record_ids(project.root, {"0": {"the-shard.jsonl:1": 9}}, {"0": {7}})
    assert written == {
        "format": "camelot.ids/1",
        "zones": {"0": {"the-shard.jsonl:1": 9}},
        "tombstones": {"0": [7]},
    }
    assert pj.ids(project.root) == written  # a build reads back what it wrote

    (project.root / pj.IDS_FILE).write_text(json.dumps({"format": "camelot.ids/9"}))
    with pytest.raises(pj.ProjectError, match="camelot.ids/9"):
        pj.ids(project.root)


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


def test_cli_serve_takes_the_checkout_and_lets_it_go(tmp_path, base, capsys, monkeypatch):
    """The daemon claims the checkout it serves and drops the lock when it
    stops (`project.md` §D13) — a crashed daemon must not leave a project
    unwritable, and `studio` is the operator's way in."""
    import uvicorn

    project = pj.new(tmp_path / "camelot-hills", client=base)
    (project.root / pj.MACHINE_FILE).write_text(
        json.dumps({"author": "Michael", "client": str(base)})
    )
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)
    # the machine's list of projects is not a test's to write (`home.md` §D1)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert main(["studio", str(project.root), "--no-live", "--no-open"]) == 0
    out = capsys.readouterr().out
    assert "held by michael" in out
    assert pj.lock_state(project.root) is None  # taken for the run, then released


def test_a_second_daemon_on_a_held_checkout_runs_read_only(tmp_path, base, capsys, monkeypatch):
    """Two writers would mean two journals for one session (`project.md`
    §D13): the second daemon serves, refuses every write, and names the holder
    on stderr (the panel reads the same fact from the editor's state)."""
    import uvicorn

    project = pj.new(tmp_path / "camelot-hills", client=base)
    holder = pj.lock(project.root, author="Lyra")
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)
    # the machine's list of projects is not a test's to write (`home.md` §D1)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    assert (
        main(
            [
                "serve",
                "--root",
                str(base),
                "--no-open",
                "--no-watch",
                "--project",
                str(project.root),
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "read-only" in err and "lyra" in err and str(holder.pid) in err


# -- populate: what an edit depends on ---------------------------------------


def test_populate_records_what_an_edit_depends_on(base):
    """A shelf, not a copy: a zone is recorded through the entries an *edit*
    reads (its placements, its catalogue, its metadata) and a table whole —
    never what the viewer only looks at (the heightmap, the ground patches)."""
    found = pj.populate(base, zones=[0], tables=["items.csv"])
    assert set(found) == {
        "zones/zone000/csv000.mpk#fixtures.csv",
        "zones/zone000/csv000.mpk#nifs.csv",
        "zones/zone000/dat000.mpk#sector.dat",
        "gamedata.mpk#items.csv",
    }
    assert not [resource for resource in found if "terrain" in resource]


def test_populate_records_a_zone_whose_folder_is_upper_case(base):
    """The retail extraction disagrees with itself on case (`zone000` beside
    `ZONE008`) and a resource name is written *canonically*: the read resolves
    the on-disk case, so a project populated on one machine reads on another."""
    assert pj.zone_resources(base, 8) == ["zones/zone008/csv008.mpk#fixtures.csv"]
    assert pj.read_resource(base, "zones/zone008/csv008.mpk#fixtures.csv") == (
        b"another zone's placements"
    )


def test_zones_on_disk_walks_the_folders_not_the_index(base):
    assert pj.zones_on_disk(base) == [0, 8]
    assert pj.zones_on_disk(base / "nowhere") == []


def test_populate_merges_into_the_project(project, base):
    found = pj.populate(base, zones=[0, 8])
    updated, adopted = pj.add_fingerprints(project.root, found)
    assert adopted == []
    assert len(updated.fingerprints) == 3 + len(found)  # the base's own three, plus these
    again, _ = pj.add_fingerprints(project.root, found)  # idempotent
    assert again.fingerprints == updated.fingerprints


def test_populate_refuses_to_adopt_a_moved_base_without_being_asked(project, base):
    """A recorded resource whose bytes changed is a *moved base*: adopting it
    silently is what re-base (P8) exists for, so it is refused by name."""
    found = pj.populate(base, zones=[0])
    pj.add_fingerprints(project.root, found)
    write_mpak(
        str(base / "zones" / "zone000" / "csv000.mpk"),
        "csv000",
        [("fixtures.csv", b"the placements, moved"), ("nifs.csv", b"the catalogue")],
    )
    moved = pj.populate(base, zones=[0])
    with pytest.raises(pj.ProjectError) as error:
        pj.add_fingerprints(project.root, moved)
    assert "zones/zone000/csv000.mpk#fixtures.csv" in str(error.value)
    assert "verify" in str(error.value)
    updated, adopted = pj.add_fingerprints(project.root, moved, update=True)
    assert adopted == ["zones/zone000/csv000.mpk#fixtures.csv"]
    assert (
        updated.fingerprints["zones/zone000/csv000.mpk#fixtures.csv"]
        == (moved["zones/zone000/csv000.mpk#fixtures.csv"])
    )


# -- import: a 6.3 authoring directory becomes a project ----------------------


LEGACY = {
    "schema": "camelot.editor/1",
    "mod": {"id": "ux-session", "version": "1", "priority": 100},
    "zones": {
        "0": [
            {
                "action": "set",
                "id": 1,
                "columns": {"X": 3, "Y": 4},
                "values": {"X": "25472", "Y": "47104"},
                "expect": {"X": "25344", "Y": "47104"},
            }
        ]
    },
    "patches": {
        "0": [
            {
                "table": "door",
                "key": "d1",
                "values": {"X": "9"},
                "originals": {"X": "8"},
                "applied": True,
            }
        ]
    },
    "published": {"ops": 1, "at": 1},
}


def test_import_makes_an_authoring_directory_a_project_in_place(tmp_path, base):
    """The operator's own directory becomes the project: the scaffolding is
    written around it, the legacy journal becomes one shard, and the facts a
    6.3 file implied go to the deploy log — the old file stays beside them."""
    session = tmp_path / "ux-session"
    session.mkdir()
    (session / "mod.json").write_text(json.dumps(LEGACY["mod"]))
    (session / "journal.json").write_text(json.dumps(LEGACY))
    (session / "notes.md").write_text("my own notes\n")
    report = pj.import_authoring(session, client=base, author="michael")

    assert report["project"].name == "ux-session"
    assert (session / pj.PROJECT_FILE).is_file()
    # the identity the legacy file carried survives, in the place the new model
    # reads first (`new` does not write a mod.json)
    assert json.loads((session / "mod.json").read_text())["id"] == "ux-session"
    assert (session / "notes.md").read_text() == "my own notes\n"  # ours to keep
    assert (session / "journal.json.migrated").is_file()
    assert not (session / "journal.json").is_file()

    shard = next((session / "journal").glob("*.jsonl"))
    lines = [json.loads(line) for line in shard.read_text().splitlines()]
    assert [line["kind"] for line in lines] == ["shard", "op", "op"]
    assert lines[1]["family"] == "world" and lines[1]["op"]["values"]["X"] == "25472"
    assert lines[2]["family"] == "rows" and lines[2]["rows"][0]["key"] == "d1"

    # the facts: the patch 6.3 said was written, then the migration itself
    assert [fact["kind"] for fact in report["facts"]] == ["publish", "migrated"]
    log = pj.deploy_log(session)
    assert [entry["kind"] for entry in log] == ["publish", "migrated"]
    assert log[0]["keys"] == [f"{shard.name}:2"]
    assert log[1]["shard"] == shard.name and log[1]["ops"] == 1 and log[1]["rows"] == 1


def test_import_does_not_overwrite_what_the_operator_wrote(tmp_path, base):
    session = tmp_path / "session"
    session.mkdir()
    (session / "mod.json").write_text(json.dumps(LEGACY["mod"]))
    (session / ".gitignore").write_text("mine\n")
    (session / pj.MACHINE_FILE).write_text('{"author": "lyra"}\n')
    pj.import_authoring(session, client=base)
    assert (session / ".gitignore").read_text() == "mine\n"
    assert pj.machine(session)["author"] == "lyra"


def test_import_refuses_a_project_and_a_directory_that_is_not_one(tmp_path, base):
    project = pj.new(tmp_path / "hills", client=base)
    with pytest.raises(pj.ProjectError, match="already a project"):
        pj.import_authoring(project.root, client=base)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(pj.ProjectError, match="not an authoring directory"):
        pj.import_authoring(empty, client=base)


def test_cli_populate_and_import_round_trip(tmp_path, base, capsys, monkeypatch):
    monkeypatch.delenv("DAOC_CLIENT", raising=False)
    session = tmp_path / "ux-session"
    session.mkdir()
    (session / "journal.json").write_text(json.dumps(LEGACY))
    assert main(["project", "import", str(session), "--client", str(base)]) == 0
    shown = capsys.readouterr().out
    assert "imported" in shown and "journal.json.migrated" in shown
    assert "deploy: publish" in shown
    assert main(["project", "populate", "--dir", str(session), "--zones", "0,8"]) == 0
    shown = capsys.readouterr().out
    assert "4 resource(s)" in shown
    assert "zones/zone000/csv000.mpk: 2 entries" in shown
    assert "zones/zone000/dat000.mpk: 1 entry" in shown and "zones/zone008/csv008.mpk" in shown
    assert main(["project", "verify", "--dir", str(session), "--client", str(base)]) == 0
    assert "7 resource(s): 7 ok" in capsys.readouterr().out


def test_populate_quiet_prints_the_total_and_not_one_line_per_archive(project, base, capsys):
    """`--all-zones` on a real client is 400 lines; the total is what a script reads."""
    code = main(
        ["project", "populate", "--client", str(base), "--zones", "0", "--quiet",
         "--dir", str(project.root)]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "recorded" in out and "in the project" in out
    assert "entry" not in out
