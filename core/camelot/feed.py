"""The feed's protocol: the two files a launcher reads, and the tree behind them.

This module is the **players' half of the code**, and it depends on nothing else in
`camelot` — a test asserts it (`test_the_players_half_depends_on_nothing_of_ours`).
What a player receives must contain no path that writes a server's rows, reads a
project's journal or reaches a database; what it needs is here and nowhere else:
the walk, the two files, the plan, the update.

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

Producing a feed is the *other* half, and it is the operator's: `feedbuild.py` composes
this module with the project, the releases and the author's install.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FORMAT = "camelot.feed/1"

VERSION_FILE = "version-new.txt"
PATCHLIST_FILE = "patchlist-new.txt"
RECORD_FILE = "release.json"
#: The shard's own settings, beside the protocol's two files (ours, and the one thing a
#: launcher needs that the protocol does not carry: where the game is, and what the shard
#: is called). A player is told one URL — the feed's — and everything else comes from it.
LAUNCHER_FILE = "launcher.json"
#: The project's own directory for the players' feed (C §D4).
FEED = "feed"

#: What the walk never lists, whatever the tree holds.
EXCLUDED = (".camelot", "logs")

__all__ = [
    "EXCLUDED",
    "FEED",
    "FORMAT",
    "LAUNCHER_FILE",
    "PATCHLIST_FILE",
    "RECORD_FILE",
    "VERSION_FILE",
    "FeedError",
    "launcher_of",
    "peek",
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


# --- reading a feed back -------------------------------------------------------


def _get_text(base: str, name: str) -> str:
    """One of the two files, from a directory or from a URL — a player's machine has no
    feed folder, so a URL is the normal case and a folder is the convenient one."""
    if _source_of(base) == "url":
        url = base.rstrip("/") + "/" + name
        try:
            with urllib.request.urlopen(url, timeout=30) as answer:
                return answer.read().decode("utf-8", "replace")
        except urllib.error.URLError as error:
            raise FeedError(f"{url}: {error}") from error
    path = Path(urllib.parse.unquote(urllib.parse.urlparse(base).path or base)) / name
    if not path.is_file():
        raise FeedError(f"{path}: no {name} — that is not a feed")
    return path.read_text(encoding="utf-8")


def launcher_of(where) -> dict:
    """A shard's own settings, as a feed carries them (`launcher.json`). Missing is a
    fact: a launcher then has to be told where the game is — and a feed read over HTTP
    may not carry the file at all, which is not an error either."""
    base = str(where)
    if _source_of(base) == "url":
        try:
            text = _get_text(base, LAUNCHER_FILE)
        except FeedError:
            return {}
    else:
        path = Path(base) / LAUNCHER_FILE
        if not path.is_file():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {}
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return document if isinstance(document, dict) else {}


def peek(base) -> dict:
    """What a feed *announces*, without its list: the version, and the shard's own
    settings. That is what a launcher shows before it decides to walk a client — the
    patchlist can be megabytes, and a page that loads in a second is worth one."""
    base = str(base)
    return {
        "path": base,
        "version": read_version(_get_text(base, VERSION_FILE)),
        "launcher": launcher_of(base),
    }


def read(where) -> dict:
    """A feed, read back: the two protocol files (from a folder or over HTTP) and our
    own record when it is there."""
    base = str(where)
    base_url, entries = read_patchlist(_get_text(base, PATCHLIST_FILE))
    version = read_version(_get_text(base, VERSION_FILE))
    found = {
        "path": base,
        "format": FORMAT,
        "version": version,
        "base_url": base_url,
        "entries": entries,
        "files": len(entries),
        "bytes": sum(entry["size"] for entry in entries),
        "record": {},
        "launcher": {},
    }
    if _source_of(base) != "url":
        record = Path(base) / RECORD_FILE
        if record.is_file():
            try:
                found["record"] = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                found["record"] = {}
    found["launcher"] = launcher_of(base)
    return found


# --- bringing a client up ------------------------------------------------------


def _source_of(base_url: str) -> str:
    """Where a file comes from: a directory, or a URL base."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme in ("http", "https"):
        return "url"
    return "dir"


def _fetch(base_url: str, rel: str, local_root=None, *, timeout=30) -> bytes:
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
            with urllib.request.urlopen(url, timeout=timeout) as answer:
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
    local = None if _source_of(str(where)) == "url" else str(where)
    written: list[dict] = []
    for row in found["wanted"]:
        data = _fetch(found["base_url"], row["path"], local)
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
