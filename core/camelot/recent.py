"""The projects this machine has opened, newest first (`home.md` §D1).

A list, not a record: five entries, and each one is a path, a name and a date.
Everything else Home shows about a project — its health, the work waiting, the
last release — is read *from the project* when the page asks; a list that
carried copies would be a second truth.

It lives in the machine's config directory, never inside a project and never
in a repository. A malformed file is refused **by name** and rebuilt empty on
the next write: it is a convenience, not a record.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from . import paths

FORMAT = "camelot.recent/1"
FILE = "recent.json"

#: The operator's number, argued and settled (`home.md` §D1): "il n'existe même
#: pas 20 serveurs en ligne dans le monde". Five, then the oldest falls off.
LIMIT = 5


class RecentError(Exception):
    """Anything this module refuses to do, with the reason in the message."""


def _now() -> str:
    """The same stamp the project writes (`2026-10-16T20:49:21Z`): one format
    for every `at` this suite records."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def path(directory: str | os.PathLike | None = None) -> Path:
    """The registry's own file — `directory` is for tests and for a caller
    that keeps its machine files elsewhere."""
    base = Path(directory) if directory is not None else paths.config_dir()
    return base / FILE


def load(directory: str | os.PathLike | None = None) -> dict:
    """The document as written. Missing is normal (a fresh machine); a
    malformed one is refused by name, and `record` will rewrite it."""
    file = path(directory)
    if not file.is_file():
        return {"format": FORMAT, "projects": []}
    try:
        document = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecentError(f"{file}: {error}") from error
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        found = document.get("format") if isinstance(document, dict) else None
        raise RecentError(
            f"{file}: format {found or '(none)'} is not one this build reads (it reads {FORMAT})"
        )
    if not isinstance(document.get("projects"), list):
        raise RecentError(f"{file}: projects must be a list")
    return document


def entries(directory: str | os.PathLike | None = None) -> list[dict]:
    """The list, normalised and in order. An entry this build cannot read is
    dropped rather than crashing a page over a convenience."""
    found: list[dict] = []
    for item in load(directory).get("projects") or ():
        if not isinstance(item, dict) or not item.get("path"):
            continue
        raw = str(item["path"])
        found.append(
            {
                "path": raw,
                "name": str(item.get("name") or Path(raw).name),
                "opened": str(item.get("opened") or ""),
            }
        )
    return found[:LIMIT]


def record(
    root: str | os.PathLike,
    *,
    name: str | None = None,
    at: str | None = None,
    directory: str | os.PathLike | None = None,
) -> dict:
    """Put a project at the front of the list.

    Opening a project that was already there moves it up and refreshes its
    date; past `LIMIT`, the oldest falls off. A malformed file is simply
    rewritten — the project itself is the record, this is the shortcut.
    """
    project = Path(root).expanduser()
    if not project.is_dir():
        raise RecentError(f"{project}: no such directory")
    resolved = project.resolve()
    try:
        known = entries(directory)
    except RecentError:
        known = []
    entry = {
        "path": str(resolved),
        "name": str(name or resolved.name),
        "opened": str(at or _now()),
    }
    kept = [entry] + [item for item in known if item["path"] != entry["path"]]
    _write({"format": FORMAT, "projects": kept[:LIMIT]}, directory)
    return entry


def forget(root: str | os.PathLike, directory: str | os.PathLike | None = None) -> bool:
    """Drop one entry — the folder itself is never touched. Returns whether
    there was something to drop."""
    target = str(Path(root).expanduser().resolve())
    try:
        known = entries(directory)
    except RecentError:
        known = []
    kept = [item for item in known if item["path"] != target]
    _write({"format": FORMAT, "projects": kept}, directory)
    return len(kept) != len(known)


def _write(document: dict, directory: str | os.PathLike | None = None) -> None:
    """Atomic, the same temp-file dance as `serve/cache.py`: a reader never
    sees half a file, and a crash mid-write leaves the old one."""
    file = path(directory)
    file.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(file.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=1, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, file)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
