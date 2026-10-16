"""The machine's list of recent projects (`home.md` §D1).

Five entries, a path, a name, a date — and nothing that belongs to a project.
The registry is a convenience: it is refused by name when it is malformed and
rebuilt empty, and it never stands between the operator and their folder.
"""

import json
import sys

import pytest

from camelot import paths, recent


@pytest.fixture()
def machine(tmp_path):
    """A machine's own config directory, so the tests never touch the real
    one."""
    return tmp_path / "config"


def test_a_missing_registry_is_empty_and_a_record_makes_one(machine, tmp_path):
    assert recent.entries(machine) == []
    project = tmp_path / "camelot-hills"
    project.mkdir()

    entry = recent.record(
        project, name="Camelot Hills", at="2026-10-16T12:00:00Z", directory=machine
    )
    assert entry == {
        "path": str(project.resolve()),
        "name": "Camelot Hills",
        "opened": "2026-10-16T12:00:00Z",
    }
    assert recent.entries(machine) == [entry]
    document = json.loads((machine / recent.FILE).read_text(encoding="utf-8"))
    assert document["format"] == "camelot.recent/1"
    # the name falls back to the folder's own
    assert recent.record(project, directory=machine)["name"] == "camelot-hills"


def test_opening_again_moves_it_up_and_refreshes_the_date(machine, tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
        recent.record(tmp_path / name, directory=machine)
    assert [item["name"] for item in recent.entries(machine)] == ["c", "b", "a"]

    recent.record(tmp_path / "a", at="2026-10-16T18:00:00Z", directory=machine)
    found = recent.entries(machine)
    assert [item["name"] for item in found] == ["a", "c", "b"]  # moved, not copied
    assert found[0]["opened"] == "2026-10-16T18:00:00Z"


def test_five_entries_and_the_oldest_falls_off(machine, tmp_path):
    for index in range(7):
        folder = tmp_path / f"server{index}"
        folder.mkdir()
        recent.record(folder, directory=machine)
    found = recent.entries(machine)
    assert len(found) == recent.LIMIT == 5
    assert [item["name"] for item in found] == [f"server{i}" for i in reversed(range(2, 7))]


def test_forget_drops_the_entry_and_never_the_folder(machine, tmp_path):
    folder = tmp_path / "avalon"
    folder.mkdir()
    recent.record(folder, directory=machine)

    assert recent.forget(folder, directory=machine) is True
    assert recent.entries(machine) == []
    assert folder.is_dir()  # a list, not a record
    assert recent.forget(folder, directory=machine) is False  # nothing to drop


def test_a_malformed_registry_is_refused_by_name_and_rebuilt(machine, tmp_path):
    machine.mkdir(parents=True)
    (machine / recent.FILE).write_text("{oh no", encoding="utf-8")
    with pytest.raises(recent.RecentError, match=recent.FILE):
        recent.entries(machine)

    # the next record starts over: the convenience never blocks the operator
    folder = tmp_path / "avalon"
    folder.mkdir()
    recent.record(folder, directory=machine)
    assert [item["name"] for item in recent.entries(machine)] == ["avalon"]


def test_a_foreign_format_is_refused_by_name(machine):
    machine.mkdir(parents=True)
    (machine / recent.FILE).write_text(
        json.dumps({"format": "camelot.recent/9", "projects": []}), encoding="utf-8"
    )
    with pytest.raises(recent.RecentError, match="camelot.recent/9"):
        recent.entries(machine)


def test_recording_a_folder_that_is_not_there_is_refused(machine, tmp_path):
    with pytest.raises(recent.RecentError, match="no such directory"):
        recent.record(tmp_path / "ghost", directory=machine)


def test_entries_drop_what_the_build_cannot_read(machine, tmp_path):
    machine.mkdir(parents=True)
    (machine / recent.FILE).write_text(
        json.dumps(
            {
                "format": "camelot.recent/1",
                "projects": [{"path": "/somewhere"}, "junk", {"name": "no path"}],
            }
        ),
        encoding="utf-8",
    )
    found = recent.entries(machine)
    assert found == [{"path": "/somewhere", "name": "somewhere", "opened": ""}]


def test_the_directories_follow_the_machine_s_conventions(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths.config_dir() == tmp_path / "cfg" / paths.APP
    assert paths.cache_dir() == tmp_path / "cache" / paths.APP

    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert paths.config_dir() == tmp_path / ".config" / paths.APP

    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.config_dir() == tmp_path / "Library" / "Application Support" / paths.APP
    assert paths.cache_dir() == tmp_path / "Library" / "Caches" / paths.APP
