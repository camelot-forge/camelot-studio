"""`camelot project publish`: the deploy branch (`remote.md` §D2, §D5).

The commit is built with git's plumbing on a synthetic project — no client,
no corpus: what is asserted is the branch's content, the marker, the log line,
and that the working branch and its index are never touched.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from camelot import project as pj
from camelot import publish as pub

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="publish needs git")


def _identity() -> dict:
    return {
        "GIT_AUTHOR_NAME": "tester",
        "GIT_AUTHOR_EMAIL": "tester@camelot.local",
        "GIT_COMMITTER_NAME": "tester",
        "GIT_COMMITTER_EMAIL": "tester@camelot.local",
    }


def _git(root, *args, env=None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


def _project(tmp_path) -> Path:
    """A project with a git repository and one commit — the state a publish
    mirrors (`remote.md` §D2)."""
    root = tmp_path / "avalon"
    pj.new(root, name="Avalon")
    env = {**os.environ, **_identity()}
    _git(root, "init", "--quiet")
    (root / "notes.md").write_text("# Avalon\n", encoding="utf-8")
    _git(root, "add", "-A", env=env)
    _git(root, "commit", "-m", "start", env=env)
    return root


def _release(root, number=1) -> dict:
    server = root / "releases" / f"{number:04d}" / "server"
    client = root / "releases" / f"{number:04d}" / "client"
    server.mkdir(parents=True)
    client.mkdir(parents=True)
    (server / "forge.tar.gz").write_bytes(b"pack bytes")
    (client / "patchlist.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    record = {
        "format": "camelot.release/1",
        "number": number,
        "cut_at": "2026-10-18T10:00:00Z",
        "families": {"world": 1},
        "order": "server-only",
        "needs": {"manual": [], "reload": [], "restart": False},
        "artifacts": {
            "server": {
                "pack": f"releases/{number:04d}/server/forge.tar.gz",
                "sha256": "sha256:x",
            },
            "client": {
                "patchlist": f"releases/{number:04d}/client/patchlist.json",
                "version": number,
                "sha256": "sha256:y",
            },
        },
        "ids": {},
        "base": {"client": {"build": "1.127", "fingerprints": {}}},
    }
    (root / "releases" / f"{number:04d}" / "release.json").write_text(
        json.dumps(record, indent=1), encoding="utf-8"
    )
    return record


def _feed(root, version=1, release=1) -> Path:
    where = root / "feed"
    where.mkdir(parents=True, exist_ok=True)
    (where / "version-new.txt").write_text(f"{version}\n", encoding="utf-8")
    (where / "patchlist-new.txt").write_text("http://127.0.0.1/feed/\n", encoding="utf-8")
    (where / "release.json").write_text(
        json.dumps(
            {
                "format": "camelot.feed/1",
                "release": release,
                "version": version,
                "files": 0,
                "bytes": 0,
                "base_url": "http://127.0.0.1/feed/",
                "built": "2026-10-18T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (where / "launcher.json").write_text(
        json.dumps(
            {
                "format": "camelot.feed/1",
                "shard": "Avalon",
                "feed": "http://127.0.0.1/feed/",
                "server": "127.0.0.1:10300",
                "version": version,
            }
        ),
        encoding="utf-8",
    )
    return where


def _names(root, branch=pub.BRANCH) -> list[str]:
    return _git(root, "ls-tree", "-r", "--name-only", branch).splitlines()


def test_publish_commits_the_payloads_and_the_feed_to_the_deploy_branch(tmp_path):
    root = _project(tmp_path)
    _release(root)
    _feed(root)
    branch_before = _git(root, "symbolic-ref", "--short", "HEAD")
    head_before = _git(root, "rev-parse", "HEAD")

    report = pub.publish(root, 1, build=False)

    assert report["branch"] == pub.BRANCH
    assert report["release"] == 1
    assert report["feed"]["version"] == 1
    assert _git(root, "rev-parse", f"refs/heads/{pub.BRANCH}") == report["commit"]

    names = _names(root)
    for wanted in (
        "releases/0001/server/forge.tar.gz",
        "releases/0001/client/patchlist.json",
        "feed/version-new.txt",
        "publish.json",
        "project.json",
        "notes.md",
    ):
        assert wanted in names

    marker = json.loads(_git(root, "show", f"{pub.BRANCH}:publish.json"))
    assert marker["format"] == pub.FORMAT
    assert marker["release"] == 1
    assert marker["feed"]["version"] == 1
    assert marker["feed"]["base_url"] == "http://127.0.0.1/feed/"

    # The working branch, its head and its tree were not touched; only the
    # deploy log (tracked, append-only) has a new line.
    assert _git(root, "symbolic-ref", "--short", "HEAD") == branch_before
    assert _git(root, "rev-parse", "HEAD") == head_before
    status = _git(root, "status", "--porcelain", "--untracked-files=no").splitlines()
    assert status == ["M deploy/log.jsonl"]
    facts = [
        json.loads(line)
        for line in (root / "deploy" / "log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert facts[-1]["kind"] == "publish"
    assert facts[-1]["release"] == 1
    assert facts[-1]["commit"] == report["commit"]


def test_publishing_twice_without_changes_is_refused(tmp_path):
    root = _project(tmp_path)
    _release(root)
    _feed(root)
    pub.publish(root, 1, build=False)

    with pytest.raises(pub.PublishError, match="nothing to publish"):
        pub.publish(root, 1, build=False)


def test_publish_pushes_when_a_remote_is_named(tmp_path):
    root = _project(tmp_path)
    _release(root)
    _feed(root)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)

    report = pub.publish(root, 1, build=False, remote=str(remote))

    assert report["pushed"] == str(remote)
    assert _git(remote, "rev-parse", f"refs/heads/{pub.BRANCH}") == report["commit"]


def test_publish_refuses_a_release_without_its_bytes(tmp_path):
    root = _project(tmp_path)
    record = _release(root)
    (root / str(record["artifacts"]["server"]["pack"])).unlink()
    _feed(root)

    with pytest.raises(pub.PublishError, match="the server pack is recorded"):
        pub.publish(root, 1, build=False)


def test_publish_refuses_a_project_without_a_commit(tmp_path):
    root = tmp_path / "avalon"
    pj.new(root, name="Avalon")
    _release(root)
    _feed(root)

    with pytest.raises(pub.PublishError, match="no commit yet"):
        pub.publish(root, 1, build=False)


def test_publish_refuses_a_project_without_a_release(tmp_path):
    root = _project(tmp_path)
    _feed(root)

    with pytest.raises(pub.PublishError, match="no release yet"):
        pub.publish(root, None, build=False)


def test_the_feed_target_reads_the_feed_that_is_already_built(tmp_path, monkeypatch):
    """No environment, no flag: the feed beside the project is the answer the
    page and the CLI both use (`F §D4`)."""
    root = tmp_path / "avalon"
    feed = root / "feed"
    feed.mkdir(parents=True)
    (feed / "release.json").write_text(
        json.dumps({"base_url": "https://feed.example/"}), encoding="utf-8"
    )
    (feed / "launcher.json").write_text(
        json.dumps({"server": "play.example:10300"}), encoding="utf-8"
    )
    monkeypatch.delenv("CAMELOT_FEED_URL", raising=False)
    monkeypatch.delenv("CAMELOT_GAME_SERVER", raising=False)

    assert pub.feed_target(root) == ("https://feed.example/", "play.example:10300")
    assert pub.feed_target(root, "https://other.example/", "other:1") == (
        "https://other.example/",
        "other:1",
    )
