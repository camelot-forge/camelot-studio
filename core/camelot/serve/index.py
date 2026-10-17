"""Filesystem index of a DAoC client: loose files + MPAK archive entries.

The index is deliberately *cheap*: it never parses NIF/KFA payloads, only
enumerates candidates and archive directories. Expensive metadata (NIF
version, blocks, textures, bones) is resolved lazily by the API layer and
cached separately.

Asset identity is an opaque, URL-safe id (base64url of a canonical
``<origin>:<location>`` string) so it can travel as a single path segment
without colliding with route separators. The human-readable ``path`` is
carried in the payload.
"""
from __future__ import annotations

import base64
import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from .. import __version__
from ..mpak import MpakArchive, MpakError
from . import cache

CACHE_NAME = "index.json"
# Bump when the cached row shape changes (types, fields) so stale caches are
# rebuilt automatically instead of leaking old values.
CACHE_SCHEMA = 2

_TYPE_BY_EXT = {".nif": "nif", ".kfa": "kfa", ".dds": "dds", ".tga": "tga"}
_ARCHIVE_EXT = {".mpk", ".npk"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def _encode_id(raw: str) -> str:
    return base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode_id(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad).decode("utf-8")


def make_id(origin: str, location: str) -> str:
    return _encode_id(f"{origin}:{location}")


def _type_for(name: str) -> str:
    return _TYPE_BY_EXT.get(os.path.splitext(name)[1].lower(), "other")


@dataclass(frozen=True)
class Asset:
    id: str
    type: str
    name: str
    path: str
    origin: str  # "loose" | "mpak"
    size: int
    mtime: float
    archive: str | None = None
    entry: str | None = None

    @staticmethod
    def from_row(row: dict) -> Asset:
        return Asset(**row)


def _loose_asset(root: Path, rel: str, st: os.stat_result) -> Asset:
    name = os.path.basename(rel)
    return Asset(
        id=make_id("loose", rel),
        type=_type_for(name),
        name=name,
        path=rel,
        origin="loose",
        size=st.st_size,
        mtime=st.st_mtime,
    )


def _archive_assets(root: Path, rel: str, st: os.stat_result, entries: Iterable[str]) -> list[Asset]:
    out: list[Asset] = []
    for entry in entries:
        location = f"{rel}!{entry}"
        out.append(
            Asset(
                id=make_id("mpak", location),
                type=_type_for(entry),
                name=os.path.basename(entry),
                path=location,
                origin="mpak",
                size=st.st_size,
                mtime=st.st_mtime,
                archive=rel,
                entry=entry,
            )
        )
    return out


class AssetIndex:
    """In-memory index with a JSON cache for fast restarts."""

    def __init__(self, root: str | os.PathLike, cache_dir: str | os.PathLike,
                 *, include_archives: bool = True):
        self.root = Path(root).expanduser().resolve()
        self.cache_dir = Path(cache_dir)
        self.include_archives = include_archives
        self.assets: list[Asset] = []
        self.by_id: dict[str, Asset] = {}
        self.archives = 0
        self.errors = 0
        self.scanned_at = 0.0

    # -- scan ---------------------------------------------------------------
    def repoint(self, root: str | os.PathLike) -> int:
        """Serve another client root, in place (`home.md` §D3.4): the routers
        hold *this* object, so it is re-pointed rather than rebuilt. The index
        is rescanned from disk; the exported artefacts under `cache/` are keyed
        by file path and mtime, so they stay valid and a switch never
        re-exports what it already has."""
        self.root = Path(root).expanduser().resolve()
        self.assets = []
        self.by_id = {}
        self.archives = 0
        self.errors = 0
        self.scanned_at = 0.0
        return self.scan(use_cache=False)

    def scan(self, *, use_cache: bool = True, save_cache: bool = True) -> int:
        """(Re)build the index. Returns the number of assets."""
        path = self.cache_dir / CACHE_NAME
        if use_cache:
            cached = cache.read_json(path)
            if self._cache_matches(cached):
                self._load_rows(cached["assets"], cached.get("archives", 0),
                                cached.get("errors", 0), cached.get("scanned_at", 0.0))
                return len(self.assets)

        assets: list[Asset] = []
        archives = 0
        errors = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                full = Path(dirpath) / fn
                rel = full.relative_to(self.root).as_posix()
                ext = full.suffix.lower()
                try:
                    st = full.stat()
                except OSError:
                    errors += 1
                    continue
                if ext in _ARCHIVE_EXT:
                    archives += 1
                    if not self.include_archives:
                        continue
                    try:
                        archive = MpakArchive(str(full))
                        try:
                            entries = archive.entries
                        finally:
                            archive.close()
                    except (MpakError, OSError):
                        errors += 1
                        continue
                    assets.extend(_archive_assets(self.root, rel, st, entries))
                elif ext in (".nif", ".kfa"):
                    assets.append(_loose_asset(self.root, rel, st))

        self.assets = assets
        self.archives = archives
        self.errors = errors
        self.scanned_at = time.time()
        self._reindex()
        if save_cache:
            cache.write_json(path, {
                "root": str(self.root),
                "version": __version__,
                "schema": CACHE_SCHEMA,
                "scanned_at": self.scanned_at,
                "archives": archives,
                "errors": errors,
                "assets": [asdict(a) for a in assets],
            })
        return len(assets)

    def _cache_matches(self, cached) -> bool:
        return (
            isinstance(cached, dict)
            and cached.get("root") == str(self.root)
            and cached.get("version") == __version__
            and cached.get("schema") == CACHE_SCHEMA
            and isinstance(cached.get("assets"), list)
        )

    def _load_rows(self, rows, archives: int, errors: int, scanned_at: float) -> None:
        self.assets = [Asset.from_row(r) for r in rows]
        self.archives = archives
        self.errors = errors
        self.scanned_at = scanned_at
        self._reindex()

    def _reindex(self) -> None:
        self.by_id = {a.id: a for a in self.assets}

    # -- query --------------------------------------------------------------
    def get(self, asset_id: str) -> Asset | None:
        return self.by_id.get(asset_id)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in self.assets:
            out[a.type] = out.get(a.type, 0) + 1
        return dict(sorted(out.items()))

    def top_dirs(self, limit: int = 24) -> dict[str, int]:
        """Asset counts per top-level folder (figures, items…) for browsing.

        Counts include archive entries, whose path is ``<archive>!<entry>``
        under the archive's own folder, so they match what the ``dir`` filter
        returns.
        """
        out: dict[str, int] = {}
        for a in self.assets:
            seg = a.path.split("/", 1)[0] if "/" in a.path else "(root)"
            out[seg] = out.get(seg, 0) + 1
        ranked = sorted(out.items(), key=lambda kv: -kv[1])
        return dict(ranked[:limit])

    def query(self, *, type: str | None = None, q: str | None = None,
              dir: str | None = None, sort: str = "path",
              page: int = 1, limit: int = 100) -> tuple[list[Asset], int]:
        items = self.assets
        if type:
            items = [a for a in items if a.type == type]
        if dir:
            prefix = dir.rstrip("/") + "/"
            items = [a for a in items if a.path.startswith(prefix)]
        if q:
            needle = q.lower()
            items = [a for a in items if needle in a.name.lower() or needle in a.path.lower()]

        if sort == "name":
            items = sorted(items, key=lambda a: (a.name.lower(), a.path.lower()))
        elif sort == "size":
            items = sorted(items, key=lambda a: -a.size)
        elif sort == "type":
            items = sorted(items, key=lambda a: (a.type, a.path.lower()))
        else:
            items = sorted(items, key=lambda a: a.path.lower())

        total = len(items)
        page = max(page, 1)
        limit = max(min(limit, 1000), 1)
        start = (page - 1) * limit
        return items[start : start + limit], total
