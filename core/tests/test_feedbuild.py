"""Producing a feed — the operator's half of `C` (`feedbuild.py`, P6).

The producer composes the protocol with the project, its releases and the author's
install. What it owes: a patchlist of the **whole** staged tree, the files that differ
from the base and nothing else, a refusal when a file would have to be *removed* (the
protocol cannot), and the shard's own settings beside the protocol's two files.

The helpers live in `test_feed.py` (the protocol) and are imported here: a release is
what a feed is built from, not the other way round.
"""

import json

import pytest
from test_feed import _md5, _published, client  # noqa: F401  (the synthetic client)
from test_pipeline import _project
from test_release import served_client  # noqa: F401  (the fixture `client` asks for)
from test_serve_editor import editing  # noqa: F401  (the fixture `served_client` asks for)
from test_zones import world  # noqa: F401  (the fixture `editing` asks for)

from camelot import feed, feedbuild


def test_the_feed_publishes_only_what_differs_from_the_base(
    tmp_path,
    client,  # noqa: F811  (the fixture the import names)
):
    """`C §2`: the patchlist lists the whole tree, and the feed hosts the files that
    differ. What did not change is verified against the player's own copy."""
    project = _published(tmp_path, client)

    report = feedbuild.build(project, base=client, base_url="https://shard.example/patch/")

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


def test_the_feed_refuses_a_tree_that_lost_a_file(
    tmp_path,
    client,  # noqa: F811  (the fixture the import names)
    monkeypatch,
):
    """A release can remove a client file (the author's install does, exactly), and the
    protocol cannot (`C §D7`): a feed built from a tree that lost one would leave
    players holding content the shard stopped shipping, so it is refused."""
    project = _published(tmp_path, client)
    staged = feedbuild.stage

    def staged_then_removed(base, packs, out):
        where = staged(base, packs, out)
        (where / "game1127.dll").unlink()
        return where

    monkeypatch.setattr(feedbuild, "stage", staged_then_removed)

    with pytest.raises(feedbuild.FeedError, match="cannot remove a file"):
        feedbuild.build(project, base=client, base_url="https://shard.example/patch/")


def test_a_project_with_no_client_half_has_no_feed_to_build(
    tmp_path,
    client,  # noqa: F811  (the fixture the import names)
):
    """A feed is built from releases: a project that never cut one has nothing to give
    the players yet, and says so."""
    project = _project(tmp_path, client)

    with pytest.raises(feedbuild.FeedError, match="no release carries a client half"):
        feedbuild.build(project, base=client, base_url="https://shard.example/patch/")


def test_the_feed_carries_the_shard_s_own_settings(
    tmp_path,
    client,  # noqa: F811  (the fixture the import names)
):
    """A player is told **one URL** — the feed's — and everything else comes from it: the
    shard's name, where the game is, and the version to compare with. The protocol's two
    files stay exactly the protocol's; this sits beside them (`launcher.json`)."""
    project = _published(tmp_path, client)

    report = feedbuild.build(
        project,
        base=client,
        base_url="https://avalon.example/patch/",
        server="play.avalon.example:10300",
    )

    where = project.root / feed.FEED
    shard = feed.launcher_of(where)
    assert shard["shard"] == "Avalon"
    assert shard["feed"] == "https://avalon.example/patch/"
    assert shard["server"] == "play.avalon.example:10300"
    assert shard["version"] == report["version"] == 1
    assert feed.read(where)["launcher"]["server"] == "play.avalon.example:10300"
    assert feed.launcher_of(tmp_path / "nowhere") == {}
