"""The pull: a server taking the `camelot-deploy` branch (`remote.md` §D4).

A real git clone plays the server: the source project publishes, the clone
follows the branch, and the pull verifies, hands the server half over and marks
it wanted. No database is involved — the Installer's step is not the pull's.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from camelot import project as pj
from camelot import publish as pub
from camelot import remote as rem

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the pull needs git")


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
    pack = server / "forge.tar.gz"
    pack.write_bytes(f"pack {number}".encode())
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
                "sha256": pj.digest(pack.read_bytes()),
            },
            "client": {
                "patchlist": f"releases/{number:04d}/client/patchlist.json",
                "version": number,
                "sha256": pj.digest((client / "patchlist.json").read_bytes()),
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
    (where / "zones" / "Zone000").mkdir(parents=True, exist_ok=True)
    (where / "version-new.txt").write_text(f"{version}\n", encoding="utf-8")
    (where / "patchlist-new.txt").write_text(
        "http://127.0.0.1/feed/\nzones/Zone000/csv000.mpk\tabcdef\t12\n", encoding="utf-8"
    )
    (where / "release.json").write_text(
        json.dumps(
            {
                "format": "camelot.feed/1",
                "release": release,
                "version": version,
                "files": 1,
                "published": 1,
                "bytes": 12,
                "base_url": "http://127.0.0.1/feed/",
                "built": "2026-10-18T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (where / "launcher.json").write_text(
        json.dumps({"format": "camelot.feed/1", "shard": "Avalon", "feed": "http://x/", "server": "y:1"}),
        encoding="utf-8",
    )
    (where / "zones" / "Zone000" / "csv000.mpk").write_bytes(b"zone " + str(version).encode())
    return where


def _server(tmp_path, source, env) -> Path:
    """The server's checkout: a clone of the deploy branch, with this machine's
    own `machine.json` (untracked) naming the environment."""
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", "--branch", pub.BRANCH, str(source), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    (checkout / pj.MACHINE_FILE).write_text(
        json.dumps({"environments": {"production": {"root": str(env), "client": False}}}),
        encoding="utf-8",
    )
    return checkout


def test_pull_hands_over_the_server_half_and_marks_it_wanted(tmp_path):
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = _server(tmp_path, source, env)
    served = tmp_path / "served-feed"

    report = rem.pull(checkout, "production", feed=served)

    assert report["release"] == 1
    assert report["env"] == "production"
    assert report["applied"] is False
    assert report["fetched"] is False
    pack = env / "server" / "camelot" / "releases" / "0001" / "forge.tar.gz"
    assert pack.is_file() and pack.read_bytes() == b"pack 1"
    assert not (env / "client").exists()
    assert not (env / "server" / "camelot" / "releases" / "0001" / "patchlist.json").exists()
    marker = json.loads(Path(report["wanted"]).read_text(encoding="utf-8"))
    assert marker["format"] == rem.FORMAT
    assert marker["releases"] == [1]
    assert (served / "version-new.txt").read_text(encoding="utf-8") == "1\n"
    assert (served / "zones" / "Zone000" / "csv000.mpk").read_bytes() == b"zone 1"
    assert report["feed"]["files"] == 5


def test_a_second_pull_writes_nothing(tmp_path):
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = _server(tmp_path, source, env)
    served = tmp_path / "served-feed"
    rem.pull(checkout, "production", feed=served)

    report = rem.pull(checkout, "production", feed=served)

    assert report["wrote"] == []
    assert len(report["unchanged"]) == 1
    assert report["feed"]["files"] == 0


def test_a_tampered_artifact_is_refused(tmp_path):
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = _server(tmp_path, source, env)
    (checkout / "releases" / "0001" / "server" / "forge.tar.gz").write_bytes(b"tampered")

    with pytest.raises(rem.PullError, match="do not hash"):
        rem.pull(checkout, "production")

    assert not (env / "server" / "camelot" / "releases" / "0001").exists()


def test_pull_fetches_when_a_remote_is_named(tmp_path):
    source = _project(tmp_path)
    _release(source)
    _feed(source, version=1, release=1)
    pub.publish(source, 1, build=False)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = _server(tmp_path, source, env)
    _release(source, 2)
    _feed(source, version=2, release=2)
    pub.publish(source, 2, build=False)

    report = rem.pull(checkout, "production", remote="origin")

    assert report["fetched"] is True
    assert report["release"] == 2
    assert report["head"] == _git(source, "rev-parse", f"refs/heads/{pub.BRANCH}")
    assert (env / "server" / "camelot" / "releases" / "0002" / "forge.tar.gz").is_file()


def test_pull_refuses_a_checkout_without_a_marker(tmp_path):
    source = _project(tmp_path)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = tmp_path / "checkout"
    subprocess.run(
        ["git", "clone", "--quiet", str(source), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
    )
    (checkout / pj.MACHINE_FILE).write_text(
        json.dumps({"environments": {"production": {"root": str(env), "client": False}}}),
        encoding="utf-8",
    )

    with pytest.raises(rem.PullError, match="no marker"):
        rem.pull(checkout, "production")
