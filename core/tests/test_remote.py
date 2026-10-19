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
from test_sign import _key  # a real key pair, made by ssh-keygen

from camelot import project as pj
from camelot import publish as pub
from camelot import remote as rem
from camelot import sign as sign_mod

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


# --- the signature (`remote.md` §D7, M-14) -------------------------------------


needs_ssh = pytest.mark.skipif(
    not sign_mod.available(), reason="signing a release needs OpenSSH 8.0 or later"
)


def _signed(tmp_path, *, name="release"):
    """A published, signed release and the checkout a server would pull."""
    source = _project(tmp_path)
    key = _key(tmp_path, name)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False, release_key=key)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    return source, key.with_name(f"{name}.pub"), env, _server(tmp_path, source, env)


@needs_ssh
def test_the_deploy_branch_carries_the_signature_and_the_marker_names_the_key(tmp_path):
    """The desk signs the release *record* — which carries the sha256 of every
    artifact — and the branch carries the signature beside it, never in the
    working tree."""
    source, _public, _env, _checkout = _signed(tmp_path)

    blob = _git(source, "show", f"{pub.BRANCH}:releases/0001/release.json.sig")
    assert blob.startswith("-----BEGIN SSH SIGNATURE-----")
    marker = json.loads(_git(source, "show", f"{pub.BRANCH}:{pub.MARKER}"))
    assert marker["signature"]["key"].startswith("SHA256:")
    assert marker["signature"]["path"] == "releases/0001/release.json.sig"
    # nothing was left in the tree the operator works in
    assert not (source / "releases" / "0001" / "release.json.sig").exists()


@needs_ssh
def test_a_signed_release_checks_out_at_the_pull(tmp_path):
    _source, public, _env, checkout = _signed(tmp_path)

    report = rem.pull(checkout, "production", release_key=public)

    assert report["signature"]["checked"] is True
    assert report["signature"]["key"].startswith("SHA256:")
    assert report["wrote"]


@needs_ssh
def test_a_release_record_rewritten_after_signing_is_refused(tmp_path):
    """M-14's exit criterion, and the whole reason the signature exists: an
    attacker who owns the branch rewrites the pack **and** the hash the record
    carries for it, so the hash check passes and only the signature refuses."""
    _source, public, env, checkout = _signed(tmp_path)
    pack = checkout / "releases" / "0001" / "server" / "forge.tar.gz"
    record_path = checkout / "releases" / "0001" / "release.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    pack.write_bytes(b"tampered")
    record["artifacts"]["server"]["sha256"] = pj.digest(pack.read_bytes())
    record_path.write_text(json.dumps(record, indent=1), encoding="utf-8")

    with pytest.raises(rem.PullError, match="signature does not check out"):
        rem.pull(checkout, "production", release_key=public)

    assert not (env / "server" / "camelot" / "releases" / "0001").exists()
    # and the hashes alone would have let it through, which is the point
    report = rem.pull(checkout, "production")
    assert report["signature"]["checked"] is False
    assert (env / "server" / "camelot" / "releases" / "0001" / "forge.tar.gz").read_bytes() == (
        b"tampered"
    )


@needs_ssh
def test_a_server_configured_with_a_key_refuses_an_unsigned_release(tmp_path):
    """A branch that carries no signature is not a branch this server hands over:
    the check is a check, not a suggestion."""
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False)
    env = tmp_path / "env-production"
    (env / "server").mkdir(parents=True)
    checkout = _server(tmp_path, source, env)
    public = _key(tmp_path, "release").with_name("release.pub")

    with pytest.raises(rem.PullError, match="carries no release.json.sig"):
        rem.pull(checkout, "production", release_key=public)


@needs_ssh
def test_a_server_without_a_key_says_so_rather_than_pretending(tmp_path):
    """No key configured is the state every server was in before there was one.
    It is reported, so a Console can say this server does not check who signed a
    release — never hidden."""
    _source, _public, _env, checkout = _signed(tmp_path)

    report = rem.pull(checkout, "production")

    assert report["signature"] == {
        "checked": False,
        "key": None,
        "why": "no release key is configured: this server does not check who signed "
        "a release",
    }
    assert report["wrote"]


@needs_ssh
def test_signing_an_already_published_release_is_a_publish(tmp_path):
    """A desk that published unsigned and then made a key has something to publish:
    the branch gains a signature, and "nothing to publish" is about the *payloads*,
    which did not move."""
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    pub.publish(source, 1, build=False)
    key = _key(tmp_path)

    report = pub.publish(source, 1, build=False, release_key=key)

    assert report["signature"]["key"].startswith("SHA256:")
    assert _git(source, "show", f"{pub.BRANCH}:releases/0001/release.json.sig")


@needs_ssh
def test_publishing_the_same_signed_release_twice_is_still_nothing_to_publish(tmp_path):
    """The signature is re-made on every run and Ed25519 is deterministic, so a
    second publish of the same release is a no-op — not an empty commit."""
    source = _project(tmp_path)
    _release(source)
    _feed(source)
    key = _key(tmp_path)
    pub.publish(source, 1, build=False, release_key=key)
    head = _git(source, "rev-parse", f"refs/heads/{pub.BRANCH}")

    with pytest.raises(pub.PublishError, match="nothing to publish"):
        pub.publish(source, 1, build=False, release_key=key)

    assert _git(source, "rev-parse", f"refs/heads/{pub.BRANCH}") == head


def test_a_desk_without_a_key_publishes_unsigned_and_says_so(tmp_path):
    source = _project(tmp_path)
    _release(source)
    _feed(source)

    report = pub.publish(source, 1, build=False)

    assert report["signature"] is None
    marker = json.loads(_git(source, "show", f"{pub.BRANCH}:{pub.MARKER}"))
    assert marker["signature"] is None
    tree = _git(source, "ls-tree", "-r", "--name-only", pub.BRANCH).splitlines()
    assert "releases/0001/release.json.sig" not in tree
    assert "releases/0001/release.json" in tree
