"""The feed: the two files a launcher reads, and the tree behind them (`C`, P6).

The shapes here are the launcher's own, measured from its sources (2026-10-18):
`version-new.txt` is one integer, `patchlist-new.txt` is a base URL then one
`path \\t md5 \\t size` per file of the **whole** tree, paths are relative with `/`,
and the MD5 is lowercase hex. A producer that wrote anything else would be
producing a feed nobody reads.

Everything runs on the synthetic client, with no daemon and no network.
"""

import hashlib
import json
from pathlib import Path

import pytest
from test_pipeline import _editor, _environment, _move, _project  # noqa: F401
from test_release import served_client  # noqa: F401  (what `client` wraps)
from test_serve_editor import editing  # noqa: F401  (the fixture `served_client` asks for)
from test_zones import world  # noqa: F401  (the fixture `editing` asks for)

from camelot import feed
from camelot import release as rel


@pytest.fixture()
def client(request):
    """`test_release`'s synthetic project client, under this module's own name."""
    return request.getfixturevalue("served_client")


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rel, "default_cache_dir", lambda: tmp_path / "cache")


def _md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes(), usedforsecurity=False).hexdigest()


def _spoil(path, data=b"what the player had before") -> None:
    """Give a player's copy a different file — **unlink first**: these copies share
    inodes with the base client (the staging hardlinks its files), and writing through
    one would edit the base's own bytes."""
    path = Path(path)
    path.unlink()
    path.write_bytes(data)


def _published(tmp_path, client, *, name="Avalon"):
    """A project with one fixture moved and one release cut — the smallest thing
    that has a client half."""
    project = _project(tmp_path, client, name=name)
    _move(_editor(project, client))
    rel.cut(project.root)
    return project


# --- the protocol, as the launcher reads it ------------------------------------


def test_the_version_file_is_one_integer_the_launcher_can_parse(tmp_path):
    """`Convert.ToInt32(text)` on the whole body: an integer, and whitespace is the
    only thing around it we may add."""
    path = feed.write_version(tmp_path / feed.VERSION_FILE, 12)

    assert path.read_text(encoding="utf-8") == "12\n"
    assert feed.read_version(path.read_text(encoding="utf-8")) == 12
    assert feed.read_version(" 7 ") == 7

    with pytest.raises(feed.FeedError, match="not an integer"):
        feed.read_version("version 2")


def test_the_patchlist_is_a_base_url_and_one_line_per_file(tmp_path):
    """Line 1 is the base the launcher composes `base + path` from, so it ends with
    `/`; the rest is `path \\t md5 \\t size`, lowercase hex, `/` separators."""
    tree = {
        "figures/Boar.NIF": {"md5": "ab" * 16, "size": 12},
        "game1127.dll": {"md5": "cd" * 16, "size": 34},
    }

    path = feed.write_patchlist(
        tmp_path / feed.PATCHLIST_FILE, "https://shard.example/patch/", tree
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "https://shard.example/patch/"
    assert lines[1:3] == [
        "figures/Boar.NIF\t" + "ab" * 16 + "\t12",
        "game1127.dll\t" + "cd" * 16 + "\t34",
    ]
    base_url, entries = feed.read_patchlist(path.read_text(encoding="utf-8"))
    assert base_url == "https://shard.example/patch/"
    assert [entry["path"] for entry in entries] == ["figures/Boar.NIF", "game1127.dll"]
    assert entries[0]["size"] == 12

    with pytest.raises(feed.FeedError, match="must end with"):
        feed.write_patchlist(tmp_path / "x.txt", "https://shard.example/patch", tree)


def test_the_walk_lists_the_tree_and_leaves_our_own_store_out(tmp_path):
    """`.camelot/` (the author's pristine copies) and the client's `logs/` are never
    part of what a player needs — and they differ from the base by nature."""
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "Boar.NIF").write_bytes(b"a boar")
    (tmp_path / ".camelot" / "pristine").mkdir(parents=True)
    (tmp_path / ".camelot" / "pristine" / "Boar.NIF").write_bytes(b"a boar")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "client.log").write_bytes(b"noise")

    found = feed.walk(tmp_path)

    assert list(found) == ["figures/Boar.NIF"]
    assert found["figures/Boar.NIF"]["size"] == 6
    assert found["figures/Boar.NIF"]["md5"] == _md5(tmp_path / "figures" / "Boar.NIF")


# --- the producer --------------------------------------------------------------


def test_the_feed_publishes_only_what_differs_from_the_base(tmp_path, client):
    """`C §2`: the patchlist lists the whole tree, and the feed hosts the files that
    differ. What did not change is verified against the player's own copy."""
    project = _published(tmp_path, client)

    report = feed.build(project, base=client, base_url="https://shard.example/patch/")

    where = project.root / feed.FEED
    read_back = feed.read(where)
    listed = {entry["path"]: entry for entry in read_back["entries"]}
    base_tree = feed.walk(client)
    assert read_back["version"] == report["version"] == 1
    assert read_back["base_url"] == "https://shard.example/patch/"
    assert set(base_tree) <= set(listed)
    for name, facts in base_tree.items():
        if name not in report["changed"]:
            assert listed[name]["md5"] == facts["md5"]  # unchanged: the player's own
    # the fixture the editor moved is the one the feed carries, and its listed digest
    # is the *staged* one — that is what a player downloads
    assert report["changed"] == ["zones/zone000/csv000.mpk"]
    for name in report["changed"]:
        assert (where / name).is_file()
        assert listed[name]["md5"] == _md5(where / name)
        assert listed[name]["size"] == (where / name).stat().st_size
    assert json.loads((where / feed.RECORD_FILE).read_text())["published"] == 1


def test_the_feed_refuses_a_tree_that_lost_a_file(tmp_path, client, monkeypatch):
    """A release can remove a client file (the author's install does, exactly), and the
    protocol cannot (`C §D7`): a feed built from a tree that lost one would leave
    players holding content the shard stopped shipping, so it is refused."""
    project = _published(tmp_path, client)
    staged = feed.stage

    def staged_then_removed(base, packs, out):
        where = staged(base, packs, out)
        (where / "game1127.dll").unlink()
        return where

    monkeypatch.setattr(feed, "stage", staged_then_removed)

    with pytest.raises(feed.FeedError, match="cannot remove a file"):
        feed.build(project, base=client, base_url="https://shard.example/patch/")


def test_a_project_with_no_client_half_has_no_feed_to_build(tmp_path, client):
    project = _project(tmp_path, client)

    with pytest.raises(feed.FeedError, match="no release carries a client half"):
        feed.build(project, base=client, base_url="https://shard.example/patch/")


# --- the consumer --------------------------------------------------------------


def test_an_update_writes_what_differs_and_reads_it_back(tmp_path, client):
    """`C §D11`: every written file is hashed again. The client here is a copy whose
    files were put back to something else — what a player's client is."""
    project = _published(tmp_path, client)
    report = feed.build(project, base=client, base_url="https://shard.example/patch/")
    player = tmp_path / "player"
    feed._link_tree(client, player)
    for name in report["changed"]:
        _spoil(player / name)

    plan = feed.plan(project.root / feed.FEED, player)

    assert plan["files"] == report["files"]
    assert [row["path"] for row in plan["wanted"]] == report["changed"]
    assert all(row["state"] == "differs" for row in plan["wanted"])

    done = feed.update(project.root / feed.FEED, player)

    assert [row["path"] for row in done["written"]] == report["changed"]
    for name in report["changed"]:
        assert _md5(player / name) == _md5(project.root / feed.FEED / name)
    # and the second run has nothing to do
    again = feed.update(project.root / feed.FEED, player)
    assert again["wanted"] == [] and again["written"] == []


def test_a_missing_file_is_downloaded_not_refused(tmp_path, client):
    """A player who never had the file gets it: `File.Exists` false is a download, not
    an error."""
    project = _published(tmp_path, client)
    report = feed.build(project, base=client, base_url="https://shard.example/patch/")
    player = tmp_path / "player"
    feed._link_tree(client, player)
    (player / report["changed"][0]).unlink()

    done = feed.update(project.root / feed.FEED, player)

    assert [row["state"] for row in done["wanted"]] == ["missing"]
    assert (player / report["changed"][0]).is_file()


def test_a_file_the_feed_does_not_publish_is_refused_by_name(tmp_path, client):
    """A feed is a folder: what it does not carry it cannot hand over. (The base URL is
    the folder here, so nothing tries the network.)"""
    project = _published(tmp_path, client)
    where = project.root / feed.FEED
    report = feed.build(project, base=client, base_url=f"{where}/")
    for name in report["changed"]:
        (where / name).unlink()
    player = tmp_path / "player"
    feed._link_tree(client, player)
    _spoil(player / report["changed"][0], b"stale")

    with pytest.raises(feed.FeedError, match="does not publish it"):
        feed.update(where, player)


def test_the_dry_run_says_what_it_would_write_and_writes_nothing(tmp_path, client):
    project = _published(tmp_path, client)
    report = feed.build(project, base=client, base_url="https://shard.example/patch/")
    player = tmp_path / "player"
    feed._link_tree(client, player)
    _spoil(player / report["changed"][0], b"stale")
    before = _md5(player / report["changed"][0])

    said = feed.update(project.root / feed.FEED, player, dry_run=True)

    assert said["dry_run"] is True and said["written"] == []
    assert [row["state"] for row in said["wanted"]] == ["differs"]
    assert _md5(player / report["changed"][0]) == before
