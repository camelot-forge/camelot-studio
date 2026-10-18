"""The feed: the client tree the players should have, and the two files a launcher reads.

Measured against `OpenDAoC-Launcher`'s own sources (2026-10-18, `master`), not against
prose — the shapes below are what its code does:

- `version-new.txt` — one integer (`Convert.ToInt32`), compared with the version the
  launcher stored for itself: strictly greater means "update available".
- `patchlist-new.txt` — line 1 is the **base URL** the files are fetched from
  (`base + path`, so it ends with `/`), then one line per client file:
  `path` TAB `md5` TAB `size`, where the MD5 is lowercase hex. The launcher walks
  **every** listed file, MD5s the local one, and downloads the ones that differ or are
  missing.
- paths are relative, with `/`; the launcher writes them beside itself (its own working
  directory is the client's folder) and creates the directories it needs.

So the patchlist lists the *whole* tree, and the feed hosts only the files that differ
from the base (`C §2`). One walk serves both ends, which is why they live in one module:
`build()` writes a feed, `plan()`/`update()` read one.

What never travels: our own store (`.camelot/`, the author's pristine copies) and the
client's `logs/` — neither is part of what a player needs, and both differ from the base
by nature.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import clientinstall
from . import journal as journal_mod
from . import project as project_mod
from . import release as release_mod

FORMAT = "camelot.feed/1"

VERSION_FILE = "version-new.txt"
PATCHLIST_FILE = "patchlist-new.txt"
RECORD_FILE = "release.json"
#: The project's own directory for the players' feed (C §D4).
FEED = "feed"

#: What the walk never lists, whatever the tree holds.
EXCLUDED = (".camelot", "logs")

__all__ = [
    "EXCLUDED",
    "FEED",
    "FORMAT",
    "PATCHLIST_FILE",
    "RECORD_FILE",
    "VERSION_FILE",
    "FeedError",
    "build",
    "plan",
    "read",
    "read_patchlist",
    "read_version",
    "update",
    "walk",
    "write_patchlist",
    "write_version",
]


class FeedError(ValueError):
    """Anything a feed refuses, with the reason in the message."""


# --- the walk ------------------------------------------------------------------


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skip(rel: str) -> bool:
    """Our store and the client's logs, at any depth — neither is part of what a player
    needs, and both differ from the base by nature."""
    return any(part in EXCLUDED for part in rel.split("/"))


def walk(root) -> dict[str, dict]:
    """Every file under *root*, by relative path with `/`: `{path: {"md5", "size"}}`.

    The digests are MD5 because the protocol is (`C §3`): the launcher verifies by MD5,
    and a stronger digest here would only mean computing two.
    """
    found: dict[str, dict] = {}
    base = Path(root)
    if not base.is_dir():
        raise FeedError(f"{root}: not a directory")
    for item in sorted(base.rglob("*")):
        if not item.is_file() or item.is_symlink():
            continue
        rel = str(item.relative_to(base)).replace("\\", "/")
        if _skip(rel):
            continue
        found[rel] = {"md5": _md5(item), "size": item.stat().st_size}
    return found


# --- the two files -------------------------------------------------------------


def write_version(path, number: int) -> Path:
    """`version-new.txt`: one integer, newline-terminated (the launcher trims it)."""
    where = Path(path)
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(f"{int(number)}\n", encoding="utf-8")
    return where


def read_version(text: str) -> int:
    """The integer a feed publishes, refused by name when it is not one."""
    found = str(text).strip()
    try:
        return int(found)
    except ValueError:
        raise FeedError(f"version {found[:40]!r} is not an integer") from None


def write_patchlist(path, base_url: str, tree: dict[str, dict]) -> Path:
    """`patchlist-new.txt`: the base URL, then one `path \\t md5 \\t size` per file.

    The base URL is written as given and must end with `/`: the launcher composes
    `base + path` verbatim.
    """
    where = Path(path)
    if not str(base_url).endswith("/"):
        raise FeedError(f"the base URL must end with '/': {base_url!r}")
    lines = [str(base_url)]
    for rel, facts in sorted(tree.items()):
        lines.append(f"{rel}\t{facts['md5']}\t{facts['size']}")
    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return where


def read_patchlist(text: str) -> tuple[str, list[dict]]:
    """The base URL and the entries, read the way the launcher reads them: lines split
    on `\\n` with empty ones dropped, fields on tabs with empty ones dropped."""
    lines = [line for line in str(text).split("\n") if line.strip()]
    if not lines:
        raise FeedError("the patch list is empty")
    base_url = lines[0].strip()
    entries: list[dict] = []
    for number, line in enumerate(lines[1:], start=2):
        parts = [part for part in line.split("\t") if part.strip()]
        if len(parts) < 3:
            raise FeedError(f"line {number}: expected `path \\t md5 \\t size`, got {line[:60]!r}")
        entries.append({"path": parts[0], "md5": parts[1].lower(), "size": int(parts[2])})
    return base_url, entries


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
        if _skip(str(rel).replace("\\", "/")):
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
    scratch=None,
) -> dict:
    """Write the players' feed for what the project holds now.

    Refused when the staged tree is **missing** files the base has: the protocol
    replaces and adds, it never removes (`C §D7`), so a feed that lost a file would
    leave players holding content the shard no longer ships.
    """
    root = Path(project.root)
    where = Path(out) if out is not None else root / FEED
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
    staged = stage(client, packs, space)
    try:
        tree = walk(staged)
        before = walk(client)
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
        for rel, facts in sorted(changed.items()):
            target = where / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staged / rel, target)
        write_version(where / VERSION_FILE, version)
        write_patchlist(where / PATCHLIST_FILE, base_url, tree)
        record = {
            "format": FORMAT,
            "release": int((releases[-1] if releases else {}).get("number") or 0),
            "version": version,
            "built": journal_mod.now(),
            "files": len(tree),
            "published": len(changed),
            "bytes": sum(facts["size"] for facts in changed.values()),
            "base_url": base_url,
            "changed": sorted(changed),
        }
        (where / RECORD_FILE).write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    finally:
        shutil.rmtree(space, ignore_errors=True)
    return {
        **record,
        "path": str(where),
        "version": version,
        "version_file": str(where / VERSION_FILE),
        "patchlist": str(where / PATCHLIST_FILE),
    }


# --- the consumer --------------------------------------------------------------


def read(where) -> dict:
    """A feed, read back: the two protocol files and our own record when it is there."""
    root = Path(where)
    version_path = root / VERSION_FILE
    patchlist_path = root / PATCHLIST_FILE
    if not version_path.is_file() or not patchlist_path.is_file():
        raise FeedError(
            f"{root}: no feed — {VERSION_FILE} and {PATCHLIST_FILE} are what a launcher reads"
        )
    base_url, entries = read_patchlist(patchlist_path.read_text(encoding="utf-8"))
    found = {
        "path": str(root),
        "format": FORMAT,
        "version": read_version(version_path.read_text(encoding="utf-8")),
        "base_url": base_url,
        "entries": entries,
        "files": len(entries),
        "bytes": sum(entry["size"] for entry in entries),
        "record": {},
    }
    record = root / RECORD_FILE
    if record.is_file():
        try:
            found["record"] = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            found["record"] = {}
    return found


def _source_of(base_url: str):
    """Where a file comes from: a directory, or a URL base."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme in ("http", "https"):
        return "url"
    if parsed.scheme == "file":
        return "dir"
    return "dir"


def _fetch(base_url: str, rel: str, local_root=None) -> bytes:
    """One file of a feed: from the feed's own directory when it is there (a feed is a
    folder, and a folder is the easiest thing to test against), otherwise over HTTP —
    which is what a player's launcher does."""
    if local_root is not None:
        beside = Path(local_root) / rel
        if beside.is_file():
            return beside.read_bytes()
    if _source_of(base_url) == "url":
        url = base_url.rstrip("/") + "/" + rel
        try:
            with urllib.request.urlopen(url, timeout=30) as answer:
                return answer.read()
        except urllib.error.URLError as error:
            raise FeedError(f"{url}: {error}") from error
    path = Path(urllib.parse.unquote(urllib.parse.urlparse(base_url).path or base_url)) / rel
    if not path.is_file():
        raise FeedError(f"{path}: the feed does not publish it")
    return path.read_bytes()


def plan(where, client) -> dict:
    """What an update would do to *client*: one row per file of the feed's list.

    The launcher's own rule (`C §3`), not ours: a file whose MD5 differs, or that is not
    there, is downloaded; everything else is left alone.
    """
    found = read(where)
    target = Path(client)
    rows: list[dict] = []
    for entry in found["entries"]:
        path = target / entry["path"]
        theirs = _md5(path) if path.is_file() else None
        if theirs is None:
            state = "missing"
        elif theirs == entry["md5"]:
            state = "ok"
        else:
            state = "differs"
        rows.append({**entry, "state": state, "theirs": theirs})
    wanted = [row for row in rows if row["state"] != "ok"]
    return {
        "path": found["path"],
        "version": found["version"],
        "base_url": found["base_url"],
        "client": str(target),
        "files": len(rows),
        "rows": rows,
        "wanted": wanted,
        "bytes": sum(row["size"] for row in wanted),
    }


def update(where, client, *, dry_run=False, on_file=None) -> dict:
    """Write what the plan wants, and read every written file back (`C §D11`).

    *dry_run* is the plan alone: nothing is read, nothing is written.
    """
    found = plan(where, client)
    if dry_run:
        return {**found, "dry_run": True, "written": []}
    target = Path(client)
    written: list[dict] = []
    for row in found["wanted"]:
        data = _fetch(found["base_url"], row["path"], found["path"])
        path = target / row["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".camelot-tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        read_back = _md5(path)
        if read_back != row["md5"]:
            raise FeedError(
                f"{row['path']}: written bytes hash {read_back}, the feed says {row['md5']}"
                " — the client is not the one the feed describes"
            )
        written.append({**row, "state": "written", "theirs": read_back})
        if on_file is not None:
            on_file(row["path"])
    return {**found, "dry_run": False, "written": written}
