"""Publishing a release to the `deploy` branch (`remote.md` §D2, §D5).

The desk builds the feed — the one step that needs a client — and commits the
release's payloads and that feed to a branch of the project's own repository.
The branch is the transport a server this machine does not reach can pull, and
this module is its only writer (`camelot project publish`).

The commit is built with git's plumbing: a temporary index seeded from the
content branch's tree, the payload paths force-added, `write-tree`, then
`commit-tree`. The working branch, the working tree and the real index are
never touched, so a publish is safe from a CI checkout and from a desk with
uncommitted work.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import feed as feed_mod
from . import feedbuild
from . import journal as journal_mod
from . import project as project_mod
from . import release as release_mod

__all__ = ["BRANCH", "FORMAT", "PublishError", "publish"]

FORMAT = "camelot.publish/1"
#: The branch the server pulls, and the payload paths it carries on top of the
#: content tree (`remote.md` §D2). Not `deploy`: the project already has a
#: `deploy/` directory (the log), and `git log deploy` is ambiguous.
BRANCH = "camelot-deploy"
PAYLOADS = ("releases", "feed")
MARKER = "publish.json"


class PublishError(ValueError):
    """Anything a publish refuses, with the reason in the message."""


def _git(root, *args, env=None, check=True) -> str:
    """One git command, in the project's repository. Nothing here is
    interactive: every refusal is a line the operator can act on."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except FileNotFoundError as error:
        raise PublishError("git is not installed: a publish needs it") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise PublishError(f"git {' '.join(args)}: {detail[-1] if detail else result.returncode}")
    return result.stdout


def _identity(root) -> dict:
    author = project_mod.author_name(root)
    return {
        "GIT_AUTHOR_NAME": author,
        "GIT_AUTHOR_EMAIL": f"{author}@camelot.local",
        "GIT_COMMITTER_NAME": author,
        "GIT_COMMITTER_EMAIL": f"{author}@camelot.local",
    }


def _feed_target(root, feed_url, server) -> tuple[str, str]:
    """The two values the feed carries. Given, or from the environment, or from
    the feed that is already there — never guessed (`F §D4`)."""
    where = Path(root) / feed_mod.FEED
    recorded = {}
    record = where / feed_mod.RECORD_FILE
    if record.is_file():
        try:
            recorded = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            recorded = {}
    launcher = feed_mod.launcher_of(where) if where.is_dir() else {}
    base_url = (
        feed_url
        or os.environ.get("CAMELOT_FEED_URL")
        or recorded.get("base_url")
        or launcher.get("feed")
    )
    game = server or os.environ.get("CAMELOT_GAME_SERVER") or launcher.get("server")
    if not base_url:
        raise PublishError(
            "no feed URL: pass --feed-url or set CAMELOT_FEED_URL "
            "(a feed already built names one)"
        )
    if not game:
        raise PublishError(
            "no game address: pass --server or set CAMELOT_GAME_SERVER "
            "(a feed already built names one)"
        )
    return str(base_url), str(game)


def _check_artifacts(root, record) -> None:
    """The record names the files; a publish refuses when one is missing,
    before it builds anything (`remote.md` §D5)."""
    number = int(record.get("number") or 0)
    artifacts = record.get("artifacts") or {}
    server = artifacts.get("server") or {}
    client = artifacts.get("client") or {}
    wanted = [
        ("the server pack", server.get("pack")),
        ("the rows", server.get("rows")),
        ("the client patchlist", client.get("patchlist")),
    ]
    if not server.get("pack"):
        raise PublishError(f"release {number:04d}: its record names no server pack")
    for what, relative in wanted:
        if not relative:
            continue
        if not (Path(root) / str(relative)).is_file():
            raise PublishError(
                f"release {number:04d}: {what} is recorded as {relative!r}, which is not "
                "there — rebuild it (`camelot project verify --rebuild`) or cut again"
            )


def _commit(root, number, marker, *, at=None, branch=BRANCH) -> str:
    """One commit on *branch*, built from the content branch's tree plus the
    payloads and the marker. Returns its sha.

    The "nothing to publish" check compares the payload tree — the marker is
    excluded on purpose: its timestamp changes on every run, and a publish that
    would only rewrite the marker is a no-op.
    """
    handle, index = tempfile.mkstemp(prefix="camelot-publish-")
    os.close(handle)
    env = {**os.environ, **_identity(root), "GIT_INDEX_FILE": index}
    if at:
        env["GIT_AUTHOR_DATE"] = str(at)
        env["GIT_COMMITTER_DATE"] = str(at)
    try:
        _git(root, "read-tree", "HEAD", env=env)
        _git(root, "add", "-f", "--", *PAYLOADS, env=env)
        payload_tree = _git(root, "write-tree", env=env).strip()
        previous = _git(
            root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False
        ).strip()
        if previous:
            changed = _git(
                root, "diff-tree", "-r", "--name-only", previous, payload_tree
            ).splitlines()
            if not [name for name in changed if name != MARKER]:
                raise PublishError(
                    "nothing to publish: the deploy branch already carries these bytes"
                )
        blob = subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input=json.dumps(marker, indent=1, ensure_ascii=False) + "\n",
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
        if blob.returncode != 0:
            detail = (blob.stderr or blob.stdout).strip().splitlines()
            raise PublishError(
                f"git hash-object: {detail[-1] if detail else blob.returncode}"
            )
        _git(
            root,
            "update-index",
            "--add",
            "--cacheinfo",
            f"100644,{blob.stdout.strip()},{MARKER}",
            env=env,
        )
        tree = _git(root, "write-tree", env=env).strip()
        args = ["commit-tree", tree]
        if previous:
            args += ["-p", previous]
        args += ["-m", f"publish: release {int(number):04d}"]
        commit = _git(root, *args, env=env).strip()
        _git(root, "update-ref", f"refs/heads/{branch}", commit, *([previous] if previous else []), env=env)
        return commit
    finally:
        Path(index).unlink(missing_ok=True)


def publish(
    root,
    number=None,
    *,
    build=True,
    feed_url=None,
    server=None,
    remote=None,
    at=None,
) -> dict:
    """Build the feed (unless *build* is false), commit the payloads and the
    feed to the `deploy` branch, and record one `publish` line in the deploy
    log. *remote* pushes the branch when given.

    The feed build needs the pristine base client (`remote.md` §D5); with
    *build* false, the `feed/` directory must already hold what the players
    should read.
    """
    root = Path(root).expanduser()
    project = project_mod.load(root)
    records = release_mod.releases(root)
    if not records:
        raise PublishError(f"{project.name}: no release yet — `camelot project release` cuts one")
    number = int(number) if number is not None else int(records[-1]["number"])
    record = release_mod.load(root, number)
    _check_artifacts(root, record)
    if not _git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip():
        raise PublishError(
            "the project has no commit yet: the deploy branch mirrors the content "
            "branch's tree, so commit the project first"
        )

    if build:
        target = _feed_target(root, feed_url, server)
        try:
            feedbuild.build(
                project,
                base=project_mod.client_root(project),
                base_url=target[0],
                server=target[1],
            )
        except (feedbuild.FeedError, feed_mod.FeedError) as error:
            raise PublishError(str(error)) from error

    where = root / feed_mod.FEED
    try:
        read_back = feed_mod.read(where)
    except feed_mod.FeedError as error:
        raise PublishError(
            f"{where}: {error} — build the feed first (`camelot project feed`)"
        ) from error
    published = read_back.get("record") or {}
    feed_facts = {
        "version": read_back["version"],
        "release": published.get("release"),
        "base_url": read_back["base_url"],
        # What the feed carries (the changed files) — not what its patchlist
        # lists: the list names the whole client tree, and the launcher
        # downloads only what differs from the player's copy (`F §D3`).
        "published": int(published.get("published") or 0),
        "bytes": int(published.get("bytes") or 0),
        "listed": read_back["files"],
    }

    marker = {
        "format": FORMAT,
        "release": int(number),
        "feed": feed_facts,
        "at": journal_mod.now(at),
        "author": project_mod.author_name(root),
    }
    commit = _commit(root, number, marker, at=at)
    pushed = None
    if remote:
        _git(root, "push", str(remote), f"{BRANCH}:{BRANCH}")
        pushed = str(remote)
    fact = project_mod.record_deploy(
        root,
        {
            "kind": "publish",
            "release": int(number),
            "feed": read_back["version"],
            "branch": BRANCH,
            "commit": commit,
            "remote": pushed,
            "outcome": "ok",
        },
    )
    return {
        "release": int(number),
        "branch": BRANCH,
        "commit": commit,
        "feed": feed_facts,
        "pushed": pushed,
        "fact": fact,
    }
