"""Producing a feed: the tree the players should have, and what they can fetch.

The **operator's** half (`C §2`, §D1): a staged tree — the base client with every
release's client half applied through the author's own install, never hand-patched —
gives the patch list of the whole tree and the files that differ from the base.

It composes `feed.py` (the protocol, which the players' half owns) with the project, its
releases and `clientinstall`. Nothing here is shipped to a player: the launcher reads
what this wrote.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import clientinstall, feed
from . import journal as journal_mod
from . import project as project_mod
from . import release as release_mod

__all__ = ["FeedError", "build", "stage"]


class FeedError(ValueError):
    """Anything a feed refuses, with the reason in the message."""


# --- the staged tree -----------------------------------------------------------


def _link_tree(source, target) -> None:
    """A copy of *source* in *target* whose files are **hardlinks**.

    Safe here because the author's install replaces files (temp + rename,
    `clientinstall`), never writes them in place: the base client is left alone, and a
    5 GB tree costs nothing to stage. Nothing else may write through these links — a
    plain `write_bytes` on a linked file edits the base's inode.
    """
    source, target = Path(source), Path(target)
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            continue
        rel = item.relative_to(source)
        if feed._skip(str(rel).replace("\\", "/")):
            continue
        where = target / rel
        if item.is_dir():
            where.mkdir(parents=True, exist_ok=True)
            continue
        where.parent.mkdir(parents=True, exist_ok=True)
        try:
            where.hardlink_to(item)
        except OSError:
            shutil.copy2(item, where)


def stage(base, packs, out) -> Path:
    """The tree the players should have: the base client, with each pack's client half
    applied **in order** through the author's own machinery (`C §2`, §D1).

    The base is never written: the packs land in *out*.
    """
    where = Path(out)
    if where.exists():
        shutil.rmtree(where)
    where.mkdir(parents=True)
    _link_tree(Path(base), where)
    for pack in packs:
        clientinstall.install_pack(str(where), str(pack))
    return where


# --- the producer --------------------------------------------------------------


def _client_packs(root) -> list[Path]:
    """The packs of every release, oldest first — the same file holds both halves, and
    a release with no client half installs nothing."""
    found: list[Path] = []
    for record in release_mod.releases(root):
        pack = ((record.get("artifacts") or {}).get("server") or {}).get("pack")
        if not pack:
            continue
        path = Path(root) / str(pack)
        if path.is_file():
            found.append(path)
    return found


def _version_of(root) -> int:
    """`C §D6`: the last release whose client half changed — the number the players'
    launcher compares against."""
    number = 0
    for record in release_mod.releases(root):
        if (record.get("artifacts") or {}).get("client"):
            number = int(record.get("number") or 0)
    return number


def build(
    project: project_mod.Project,
    *,
    base=None,
    out=None,
    base_url: str = "http://127.0.0.1/feed/",
    server: str = "",
    scratch=None,
    on_step=None,
) -> dict:
    """Write the players' feed for what the project holds now.

    Refused when the staged tree is **missing** files the base has: the protocol
    replaces and adds, it never removes (`C §D7`), so a feed that lost a file would
    leave players holding content the shard no longer ships.

    *server* is where the game is (`host:port`), and it is written beside the protocol's
    two files (`launcher.json`): a player is told one URL, and the shard's own settings
    come from it.

    *on_step* is the page's progress: `staging`, `walking`, `writing` (counted),
    then `done`. It is a listener, never a second implementation.
    """

    def step(stage: str, done: int = 0, total: int = 0) -> None:
        if on_step is not None:
            on_step(stage, done, total)

    root = Path(project.root)
    where = Path(out) if out is not None else root / feed.FEED
    client = base or project_mod.client_root(project)
    if client is None:
        raise FeedError(
            f"{project.name}: no client resolved — write {project_mod.MACHINE_FILE}, "
            "set $DAOC_CLIENT, or pass --client"
        )
    packs = _client_packs(root)
    if not packs:
        raise FeedError(
            "no release carries a client half yet — `camelot project release` is what makes one"
        )
    space = Path(scratch) if scratch is not None else where / ".staged"
    if space.exists():
        shutil.rmtree(space)
    step("staging")
    staged = stage(client, packs, space)
    try:
        step("walking")
        tree = feed.walk(staged)
        before = feed.walk(client)
        removed = sorted(set(before) - set(tree))
        if removed:
            raise FeedError(
                f"{len(removed)} file(s) the base has are not in the staged tree, and the "
                f"protocol cannot remove a file from a player's client: {removed[0]}"
            )
        changed = {rel: facts for rel, facts in tree.items() if before.get(rel) != facts}
        version = _version_of(root)
        releases = release_mod.releases(root)
        where.mkdir(parents=True, exist_ok=True)
        step("writing", 0, len(changed))
        for number, (rel, facts) in enumerate(sorted(changed.items()), start=1):
            target = where / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged / rel, target)
            step("writing", number, len(changed))
        feed.write_version(where / feed.VERSION_FILE, version)
        feed.write_patchlist(where / feed.PATCHLIST_FILE, base_url, tree)
        shard = {
            "format": feed.FORMAT,
            "shard": project.name,
            "feed": base_url,
            "server": str(server or ""),
            "version": version,
            "built": journal_mod.now(),
        }
        (where / feed.LAUNCHER_FILE).write_text(
            json.dumps(shard, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        record = {
            "format": feed.FORMAT,
            "release": int((releases[-1] if releases else {}).get("number") or 0),
            "version": version,
            "built": journal_mod.now(),
            "files": len(tree),
            "published": len(changed),
            "bytes": sum(facts["size"] for facts in changed.values()),
            "base_url": base_url,
            "changed": sorted(changed),
        }
        (where / feed.RECORD_FILE).write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        step("done", len(changed), len(changed))
    finally:
        shutil.rmtree(space, ignore_errors=True)
    return {
        **record,
        "path": str(where),
        "version": version,
        "version_file": str(where / feed.VERSION_FILE),
        "patchlist": str(where / feed.PATCHLIST_FILE),
    }
