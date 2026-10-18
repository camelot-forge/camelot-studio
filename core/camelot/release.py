"""A release: an immutable, numbered cut of the project's content (`pipeline.md`).

`releases/NNNN/release.json` is the record, and it is committed. The payloads
beside it are not — `.gitignore` covers `releases/*/server/` and
`releases/*/client/` — because a release is reproducible from the journal plus
the base (`pipeline.md` §D5): a teammate, or a CI job, rebuilds release N and
compares hashes instead of storing the bytes twice. `rebuild()` is that
comparison; `camelot project verify --rebuild N` is its command line.

What a release *is* is its **range**: the lines beyond the previous release's
cut — an op *and* a drop, because a drop retires an op and the content a
release holds can change without a new op (`journal.cut()`/`pending()` were
written for exactly this). A cut whose range is empty is refused ("nothing to
release"), never a twin of the one before (§D5).

What a release *holds* is the project's state as of that cut: a zone its range
touched is shipped whole — the client's `fixtures.csv` is a file, not a diff —
with the row patches of that zone beside it, so a target that received an
earlier release converges in one push (§D12).

The two families (§D2), as the toolchain actually builds them today:

- the **server** half is the pack `camelot mod build` produces (`PACK.json`,
  the deployment the Installer reads, the members, `checksums.lock`) plus the
  row patches file when the range patched rows — the pack carries declarative
  rows, not patches, so the patches travel beside it;
- the **client** half is what the pack *would install on a client*: the
  members it carries, named with their hashes in a `patchlist.json` — the
  material the feed will publish (C). A release with no client item says
  `"client": null` rather than shipping an empty directory (§D2).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import journal as journal_mod
from . import project as project_mod
from .serve.config import ServeConfig, default_cache_dir

# `serve.world` and `serve.editor` are the daemon's, and they drag `fastapi` with
# them. Cutting a release builds that daemon (`_engine`), but *reading* one — what a
# server does with `env apply` — must not need it (`deploy/server/Dockerfile`);
# `serve.config` is a dataclass and stays, because the tests redirect the cache
# through `release.default_cache_dir`.
if TYPE_CHECKING:  # the annotations are strings, so this costs a reader nothing
    from .serve.editor import Editor
    from .serve.world import World

FORMAT = "camelot.release/1"
DIR = "releases"
FILE = "release.json"

__all__ = [
    "DIR",
    "FILE",
    "FORMAT",
    "ReleaseError",
    "cut",
    "load",
    "next_number",
    "range_of",
    "rebuild",
    "record_path",
    "releases",
]


class ReleaseError(ValueError):
    """Anything this module refuses to do, with the reason in the message."""


def digest(data: bytes) -> str:
    """`sha256:<hex>` — the one fingerprint shape this suite writes."""
    return project_mod.digest(data)


def _digest_file(path: Path) -> str:
    return digest(path.read_bytes())


def _folder(root) -> Path:
    return Path(root) / DIR


def record_path(root, number) -> Path:
    """The record of one release. Four digits, so a directory listing reads in
    release order — the number is the spine (`pipeline.md` §D1)."""
    return _folder(root) / f"{int(number):04d}" / FILE


def load(root, number) -> dict:
    """One release record, refused by name when it is not one this build reads."""
    path = record_path(root, number)
    if not path.is_file():
        raise ReleaseError(f"{path}: no such release")
    document = _read(path)
    if document.get("format") != FORMAT:
        found = document.get("format") or "(none)"
        raise ReleaseError(
            f"{path}: format {found} is not one this build reads (it reads {FORMAT})"
        )
    return document


def releases(root) -> list[dict]:
    """The history, oldest first. Every record is read (a malformed one is
    named, never skipped): the history is what "what is live" is read against."""
    found: list[dict] = []
    for path in sorted(_folder(root).glob(f"*/{FILE}")):
        document = _read(path)
        if document.get("format") != FORMAT:
            raise ReleaseError(
                f"{path}: format {document.get('format') or '(none)'} is not one this "
                f"build reads (it reads {FORMAT})"
            )
        found.append(document)
    return sorted(found, key=lambda item: int(item.get("number") or 0))


def next_number(root) -> int:
    history = releases(root)
    return int(history[-1]["number"]) + 1 if history else 1


def _read(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ReleaseError(f"{path}: expected an object")
    return document


# --- the range ---------------------------------------------------------------


def range_of(journal: journal_mod.Journal, previous: dict | None) -> list[journal_mod.Line]:
    """The lines beyond `previous`'s cut, in the journal's order: ops, drops, the
    lot. Without a previous release, everything the journal holds."""
    covered = dict((previous or {}).get("log") or {})
    return [
        line
        for line in journal.lines()
        if line.kind != "shard" and line.seq > int(covered.get(line.shard, 0) or 0)
    ]


def _zone_of(
    line: journal_mod.Line, known: dict[str, journal_mod.Line]
) -> int | None:
    """A drop names an op, not a zone: the zone is the dropped op's own — and a
    dropped op is not *live* any more, so the map holds every line."""
    if line.zone is not None:
        return line.zone
    target = line.target
    found = known.get(f"{target.get('shard', '')}:{target.get('seq', '')}")
    return found.zone if found is not None else None


def _families(lines: list[journal_mod.Line]) -> dict[str, int]:
    """What the range is made of, per change family: what the release page and
    the history read (`pipeline.md` §2's `families`)."""
    counted: dict[str, int] = {}
    for line in lines:
        if line.kind == "drop":
            family = "dropped"
        else:
            family = line.family or "unknown"
        counted[family] = counted.get(family, 0) + 1
    return dict(sorted(counted.items()))


# --- the cut -----------------------------------------------------------------


def _engine(project: project_mod.Project, client=None) -> tuple[World, Editor]:
    """A `World` and an `Editor` over one project — what the daemon builds
    with, without a daemon. The pack reads the client, so a cut needs one."""
    from .serve.editor import Editor
    from .serve.world import World

    where = project_mod.client_root(project, client)
    if where is None:
        raise ReleaseError(
            f"{project.name}: no client resolved — write {project_mod.MACHINE_FILE}, "
            "set $DAOC_CLIENT, or pass --client"
        )
    config = ServeConfig(
        root=where, project=project.root, cache_dir=default_cache_dir(), watch=False
    )
    world = World(config)
    world.editor = Editor(config)
    return world, world.editor


def cut(root, *, client=None, at=None) -> dict:
    """Cut the project's state into the next release.

    Refused when the range is empty (`nothing to release`) or the client cannot
    be read (the build is against the base, always). Writes the record, the two
    payload folders, and one `release` fact in the deploy log.
    """
    project = project_mod.load(root)
    holder = project_mod.lock_state(project.root)
    if holder is not None and holder.is_alive and holder.pid != os.getpid():
        raise ReleaseError(
            f"{project.root} is held by {holder.author} (pid {holder.pid} on {holder.host}): "
            "one writer cuts a release (`project.md` §D13) — stop it, or cut from "
            "the process that holds the checkout"
        )
    world, editor = _engine(project, client)
    journal = editor.journal()
    history = releases(project.root)
    fresh = range_of(journal, history[-1] if history else None)
    if not fresh:
        raise ReleaseError(
            "nothing to release: the journal holds no line the last release does not carry"
        )
    live = {line.key: line for line in journal.live()}
    known = {line.key: line for line in journal.lines()}
    scoped = [live[line.key] for line in fresh if line.key in live]
    zones = sorted(
        {zone for zone in (_zone_of(line, known) for line in fresh) if zone is not None}
    )
    number = next_number(project.root)
    stamp = journal_mod.now(at)
    where = _folder(project.root) / f"{number:04d}"
    server, client_half = where / "server", where / "client"
    server.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"camelot-release-{number:04d}-") as scratch:
        built = editor.build(world, lines=scoped, zones=zones, out_dir=Path(scratch))
        artifacts: dict = {"server": None, "client": None}
        if built["pack"] is None:
            # A world edit with no fixture op and no row patch: nothing to ship.
            raise ReleaseError("nothing to release: the range carries no client item")
        pack = Path(built["pack"])
        target = server / pack.name
        shutil.copy2(pack, target)
        rows = Path(scratch) / "server.patch.json"
        document = json.loads(rows.read_text(encoding="utf-8")) if rows.is_file() else {}
        patches = document.get("patches") or {}
        server_facts = {"pack": _relative(project.root, target), "sha256": _digest_file(target)}
        if patches:
            kept = server / "rows.json"
            kept.write_bytes(rows.read_bytes())
            server_facts["rows"] = _relative(project.root, kept)
            server_facts["rows_sha256"] = _digest_file(kept)
        artifacts["server"] = server_facts
        members = _members(project.root, built, Path(scratch))
        if members:
            client_half.mkdir(parents=True, exist_ok=True)
            version = sum(1 for item in history if (item.get("artifacts") or {}).get("client"))
            patchlist = client_half / "patchlist.json"
            patchlist.write_text(
                json.dumps(
                    {
                        "format": "camelot.client.patchlist/1",
                        "release": number,
                        "version": version + 1,
                        "cut_at": stamp,
                        "items": members,
                    },
                    indent=1,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            artifacts["client"] = {
                "patchlist": _relative(project.root, patchlist),
                "version": version + 1,
                "sha256": _digest_file(patchlist),
            }

    record = {
        "format": FORMAT,
        "number": number,
        "cut_at": stamp,
        "base": {
            "client": {
                "build": (project.data.get("base") or {}).get("client", {}).get("build"),
                "fingerprints": project.fingerprints,
            },
            "server": None,  # S: the baseline the Installer owns; P5 fills this
        },
        "log": journal.cut(),
        "ids": _ids_of(project, scoped),
        "families": _families(fresh),
        "artifacts": artifacts,
        "order": "client-first" if artifacts["client"] else "server-only",
        "needs": {"reload": _reloads(fresh), "restart": False, "manual": []},
        "evidence": {},
    }
    record_path(project.root, number).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    project_mod.record_deploy(
        project.root,
        {
            "kind": "release",
            "number": number,
            "families": record["families"],
            "server": (artifacts["server"] or {}).get("sha256"),
            "client": (artifacts["client"] or {}).get("version"),
        },
        at=stamp,
    )
    return record


def _relative(root, path: Path) -> str:
    return str(Path(path).relative_to(Path(root))).replace("\\", "/")


def _members(root, built: dict, scratch: Path) -> list[dict]:
    """What the client half would install: one row per member — the entry inside
    its archive, or the loose file — named with the bytes' own hash, so the feed
    (§C) and the dry run (§R3) have something exact to compare."""
    found: list[dict] = []
    for entry in built.get("entries") or ():
        path = scratch / str(entry["file"])
        found.append(
            {
                "archive": entry["archive"],
                "entry": entry["entry"],
                "sha256": _digest_file(path),
                "size": path.stat().st_size,
            }
        )
    return found


def _ids_of(project: project_mod.Project, lines: list[journal_mod.Line]) -> dict:
    """The release's own allocation record (§2): what the lines it holds asked
    for, zone by zone, read from the derived `ids.json`."""
    recorded = project_mod.ids(project.root)
    wanted = {line.key for line in lines}
    found: dict[str, dict[str, int]] = {}
    for zone, allocations in (recorded.get("zones") or {}).items():
        for key, row_id in (allocations or {}).items():
            if key in wanted:
                found.setdefault(str(zone), {})[str(key)] = int(row_id)
    return dict(sorted(found.items()))


def _reloads(lines: list[journal_mod.Line]) -> list[str]:
    """What the running server has to be told after this release lands
    (`server-install.md` §D2): a fixture move is the client's, a row patch is the
    server's own — and a moved door is both."""
    wanted: list[str] = []
    for line in lines:
        if line.kind == "drop":
            wanted.append("reload")
        elif line.rows:
            for patch in line.rows:
                table = str(patch.get("table") or "")
                if table and table not in wanted:
                    wanted.append(table)
    return wanted


# --- the rebuild -------------------------------------------------------------


def rebuild(root, number, *, client=None, at=None) -> dict:
    """Rebuild one release from the journal plus the base and compare the bytes
    (`pipeline.md` §D5) — what a CI job runs on every pull request. Returns the
    recorded hash, the rebuilt one, and whether they agree."""
    record = load(root, number)
    project = project_mod.load(root)
    world, editor = _engine(project, client)
    journal = editor.journal()
    previous = None
    for item in releases(root):
        if int(item["number"]) < int(number):
            previous = item
    fresh = range_of(journal, previous)
    live = {line.key: line for line in journal.live()}
    known = {line.key: line for line in journal.lines()}
    scoped = [live[line.key] for line in fresh if line.key in live]
    zones = sorted(
        {zone for zone in (_zone_of(line, known) for line in fresh) if zone is not None}
    )
    with tempfile.TemporaryDirectory(prefix=f"camelot-rebuild-{int(number):04d}-") as scratch:
        built = editor.build(world, lines=scoped, zones=zones, out_dir=Path(scratch))
        pack = Path(built["pack"]) if built["pack"] else None
        found = _digest_file(pack) if pack is not None else ""
    expected = str(((record.get("artifacts") or {}).get("server") or {}).get("sha256") or "")
    return {
        "number": int(number),
        "expected": expected,
        "found": found,
        "same": bool(expected) and expected == found,
    }
