"""The project: the whole content of one server, in one folder (phase P1).

The design is `workspace/notes/project.md`. Three rules carry
it, and everything here follows from them:

- **what is ours lives inside** — the journal (one shard per author-session),
  the overlays, the assets, the releases, this file;
- **the base is referenced, never copied** — fingerprints of the client's own
  build and of every file we read or rewrite, so a project stays small and the
  client stays the client's;
- **what is derivable is a cache**, keyed outside the project by default.

It is a **git repository**: a team clones it, so nothing this module *tracks*
is machine-specific. The paths live in `machine.json` — untracked, written
once per machine — and the payloads are never committed: they are reproducible
from the journal plus the base (`project.md` §D9).

This module is the container and its read-only verbs. The journal's writers,
the releases and the deploy are the phases that follow (P1c, P3, P4); what is
here is what they all hang on.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import socket
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import clientfs, clientpatch
from .contract import client_build
from .mpak import MpakArchive, MpakError

__all__ = [
    "BASE_RESOURCES",
    "CACHE_POLICIES",
    "ENVIRONMENTS",
    "FORMAT",
    "GITATTRIBUTES",
    "GITIGNORE",
    "LOCK_FILE",
    "MACHINE_EXAMPLE",
    "MACHINE_FILE",
    "NOTES",
    "PROJECT_FILE",
    "Holder",
    "Project",
    "ProjectError",
    "author_name",
    "client_root",
    "fingerprint_resource",
    "fingerprints",
    "info",
    "is_git_repo",
    "load",
    "lock",
    "lock_state",
    "machine",
    "new",
    "read_resource",
    "slug",
    "unlock",
    "verify",
]

#: The schema of `project.json`. A reader refuses a format it does not know,
#: **by name** — a silent misread is the most expensive bug a project can have.
FORMAT = "camelot.project/1"

PROJECT_FILE = "project.json"
#: This machine's own facts (paths, author, environment addresses). Untracked:
#: it is what makes the project portable between the staff's machines.
MACHINE_FILE = "machine.json"
LOCK_FILE = "project.lock"
NOTES = "notes.md"
#: Every deployment fact, append-only and union-merged (P §D9): a publish, a
#: migration, a dry run. The order is read from the timestamps the lines carry,
#: never from the file.
DEPLOY_LOG = "deploy/log.jsonl"

CACHE_POLICIES = ("shared", "local")
#: The environment names a project carries. `local` and `production` are the
#: only kinds with hard meaning; `staging` is optional (operator's answer 1).
ENVIRONMENTS = ("local", "staging", "production")

#: What a *zone*'s own archives are read for: the placements an edit rewrites
#:and the catalogue it adds from. The terrain, the ground patches and the
#: textures are read by the viewer and never written by us, so they are not
#: fingerprinted — a drift check that cried wolf on every visual asset would be
#: noise, and M-2 is the measurement that decides if that changes.
ZONE_ENTRIES = ("fixtures.csv", "nifs.csv", "sector.dat")

#: The client's own data tables live inside this archive, one entry per table.
GAMEDATA_ARCHIVE = "gamedata.mpk"

#: What `new` records of the client: its own build binary, the tables and the
#: zone index every later read goes through. A resource is a path relative to
#: the client root, optionally an archive entry after `#` (the zone index
#: lives inside `zones/zones.mpk`). `populate` adds the per-zone and per-table
#: resources in the same shape.
BASE_RESOURCES = (
    clientpatch.CLIENT_BINARY,
    GAMEDATA_ARCHIVE,
    "zones/zones.mpk#zones.dat",
)

#: What git ignores, and why — the rules of `project.md` §D8/§D9/§D13.
GITIGNORE = """\
# this machine's own paths (project.md §D4) — a teammate writes their own
machine.json
# the lock of this working copy (§D13)
project.lock
# derived from the journal, recomputed by every build (§D8)
ids.json
# derivable, never committed (§D3)
cache/
# the payloads are reproducible from the journal plus the base: rebuild and
# compare, never store twice (§D9)
releases/*/server/
releases/*/client/
feed/
"""

#: Art that is a *source* (a texture we authored, an imported archive) is
#: tracked, through LFS; the deploy log merges as a union because its lines
#: are independent facts and its order is read from the timestamps (§D15).
GITATTRIBUTES = """\
# independent facts, union-merged: the order is read from the timestamps
deploy/log.jsonl merge=union

# art is a source, not a diff
*.dds filter=lfs diff=lfs merge=lfs -text
*.bmp filter=lfs diff=lfs merge=lfs -text
*.jpg filter=lfs diff=lfs merge=lfs -text
*.nif filter=lfs diff=lfs merge=lfs -text
*.kfa filter=lfs diff=lfs merge=lfs -text
*.npk filter=lfs diff=lfs merge=lfs -text
*.mpk filter=lfs diff=lfs merge=lfs -text
*.png filter=lfs diff=lfs merge=lfs -text
"""

MACHINE_EXAMPLE = """\
{
  "client": "/path/to/your/DAoC client",
  "author": "your-name",
  "environments": {
    "local": "/path/to/a/scratch/client for the local environment"
  }
}
"""

#: The directories `new` creates, so a fresh clone has the project's shape
#: (git does not track empty directories: each gets a `.gitkeep`).
_DIRECTORIES = (
    "journal",
    "overlays/world",
    "overlays/tables",
    "overlays/rows/opendaoc",
    "assets/imported",
    "assets/authored",
    "releases",
    "deploy",
)


class ProjectError(Exception):
    """Anything this module refuses to do, with the reason in the message."""


@dataclass(frozen=True)
class Holder:
    """Who holds a working copy's lock."""

    pid: int
    host: str
    author: str
    at: str

    def as_dict(self) -> dict:
        return {"pid": self.pid, "host": self.host, "author": self.author, "at": self.at}

    @property
    def is_local(self) -> bool:
        return self.host == socket.gethostname()

    @property
    def is_alive(self) -> bool:
        """Whether the process still runs — only meaningful on this host."""
        if not self.is_local:
            return True
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


@dataclass
class Project:
    """One project, as `project.json` describes it."""

    root: Path
    data: dict

    @property
    def name(self) -> str:
        return str(self.data.get("name") or self.root.name)

    @property
    def format(self) -> str:
        return str(self.data.get("format") or "")

    @property
    def created(self) -> str | None:
        value = self.data.get("created")
        return str(value) if value else None

    @property
    def cache(self) -> str:
        return str(self.data.get("cache") or "shared")

    @property
    def environments(self) -> list[str]:
        return list(self.data.get("environments") or ())

    @property
    def base(self) -> dict:
        return dict(self.data.get("base") or {})

    @property
    def client(self) -> dict:
        return dict(self.base.get("client") or {})

    @property
    def fingerprints(self) -> dict[str, str]:
        return dict(self.client.get("fingerprints") or {})

    @property
    def build(self) -> str | None:
        value = self.client.get("build")
        return str(value) if value else None

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)


# -- resources ---------------------------------------------------------------


def read_resource(client_root: str | os.PathLike, resource: str) -> bytes:
    """One base resource's bytes: a file, or an archive entry after `#`.

    The path is resolved the way the game resolves it — **the on-disk case
    wins** (`clientfs`) — because the retail extraction disagrees with itself
    (`zones/zone000` beside `zones/ZONE008`) and a resource name is written
    once, canonically, then read on whatever machine the operator has.
    """
    root = Path(client_root)
    path, _, entry = resource.partition("#")
    resolved = clientfs.resolve(str(root), path)
    if resolved is None:
        raise ProjectError(f"{resource}: no {path} under {root}")
    target = Path(resolved)
    if not entry:
        try:
            return target.read_bytes()
        except OSError as error:
            raise ProjectError(f"{resource}: {error}") from error
    try:
        archive = MpakArchive(str(target))
    except MpakError as error:
        raise ProjectError(f"{resource}: {error}") from error
    try:
        try:
            return archive.read(entry)
        except MpakError as error:
            raise ProjectError(f"{resource}: {error}") from error
    finally:
        archive.close()


def digest(data: bytes) -> str:
    """`sha256:<hex>` — the only fingerprint shape this module writes."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def fingerprint_resource(client_root: str | os.PathLike, resource: str) -> str:
    """The fingerprint of one base resource, refusing a missing one by name."""
    return digest(read_resource(client_root, resource))


def fingerprints(client_root: str | os.PathLike, resources=BASE_RESOURCES) -> dict[str, str]:
    """Every resource's fingerprint, or a refusal naming the first that is
    missing: a project built on half a base would lie about what it read."""
    return {resource: fingerprint_resource(client_root, resource) for resource in resources}


# -- this machine's own facts ------------------------------------------------


def machine(root: str | os.PathLike) -> dict:
    """`machine.json`, or an empty mapping — a missing file is normal (a fresh
    clone has none until its owner writes one)."""
    path = Path(root) / MACHINE_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ProjectError(f"{path}: expected an object")
    return data


def slug(value: str) -> str:
    """A name a filename can carry: the journal shards are named after it."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return cleaned or "unknown"


def author_name(root: str | os.PathLike) -> str:
    """Who signs this working copy's journal shards: `machine.json` first (a
    teammate's own name), then the operating system's."""
    from_machine = machine(root).get("author")
    if from_machine:
        return slug(str(from_machine))
    try:
        return slug(getpass.getuser())
    except (KeyError, OSError):  # pragma: no cover - no user database (a slim container)
        return "unknown"


def client_root(project: Project, override=None) -> Path | None:
    """Where this machine's client lives: `--client`, then `machine.json`,
    then `$DAOC_CLIENT`. Nothing tracked is consulted (that is the point)."""
    if override:
        return Path(override).expanduser()
    from_machine = machine(project.root).get("client")
    if from_machine:
        return Path(str(from_machine)).expanduser()
    from_env = os.environ.get("DAOC_CLIENT")
    return Path(from_env).expanduser() if from_env else None


def is_git_repo(root: str | os.PathLike) -> bool:
    return (Path(root) / ".git").exists()


# -- the deploy log ----------------------------------------------------------


def deploy_log(root: str | os.PathLike) -> list[dict]:
    """Every deployment fact this project recorded, in the order it was
    written. A malformed line is refused by name: this file is the answer to
    "what is live", and a half-read answer is worse than none."""
    path = Path(root) / DEPLOY_LOG
    if not path.is_file():
        return []
    entries: list[dict] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ProjectError(f"{path} line {number}: {error}") from error
        if not isinstance(entry, dict):
            raise ProjectError(f"{path} line {number}: expected a JSON object")
        entries.append(entry)
    return entries


def record_deploy(root: str | os.PathLike, entry: dict, *, at: str | None = None) -> dict:
    """Append one fact (a publish, a migration, a dry run). Append-only on
    purpose: two machines' lines merge as a union, and the order is read from
    the timestamps."""
    path = Path(root) / DEPLOY_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    written = {**entry, "at": str(entry.get("at") or _now(at))}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(written, ensure_ascii=False) + "\n")
    return written


def last_deploy(root: str | os.PathLike, kind: str | None = None) -> dict | None:
    """The most recent fact of a kind (`publish`, `migrated`, …), or None."""
    found = [entry for entry in deploy_log(root) if kind is None or entry.get("kind") == kind]
    return found[-1] if found else None


# -- creating ----------------------------------------------------------------


def _now(moment=None) -> str:
    if moment is not None:
        return str(moment)
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new(
    root: str | os.PathLike,
    *,
    name: str | None = None,
    client: str | os.PathLike | None = None,
    cache: str = "shared",
    environments=(),
    author: str | None = None,
    resources=BASE_RESOURCES,
    git: bool = False,
    created: str | None = None,
) -> Project:
    """Create a project: the file, the git scaffolding and the base records.

    A client is optional — a project may be created before its base is at hand
    (`verify` then says so instead of guessing). When one is given, its own
    build and every resource in *resources* are fingerprinted, and anything
    missing is refused **by name**: better no project than one that claims to
    have read a file it never saw.
    """
    directory = Path(root).expanduser()
    if (directory / PROJECT_FILE).is_file():
        raise ProjectError(f"a project already lives here: {directory}")
    if cache not in CACHE_POLICIES:
        raise ProjectError(f"cache must be one of {', '.join(CACHE_POLICIES)}, not {cache!r}")
    names = [str(item) for item in (environments or ("local", "production"))]
    for item in names:
        if slug(item) != item:
            raise ProjectError(f"environment names are slugs (a-z, 0-9, -): {item!r}")

    directory.mkdir(parents=True, exist_ok=True)
    base: dict = {}
    if client is not None:
        client_dir = Path(client).expanduser()
        if not client_dir.is_dir():
            raise ProjectError(f"no client directory at {client_dir}")
        base["client"] = {
            "build": client_build(str(client_dir)),
            "fingerprints": fingerprints(client_dir, resources),
        }
    data = {
        "format": FORMAT,
        "name": name or directory.name,
        "created": _now(created),
        "cache": cache,
        "environments": names,
        "base": base,
    }
    (directory / PROJECT_FILE).write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for relative in _DIRECTORIES:
        path = directory / relative
        path.mkdir(parents=True, exist_ok=True)
        (path / ".gitkeep").touch()
    (directory / "deploy" / "log.jsonl").touch()
    # The scaffolding is written **only when absent**: `import` runs this on a
    # directory that may already hold an operator's own notes, .gitignore or
    # machine file, and none of those is ours to overwrite.
    _write_if_absent(
        directory / NOTES,
        f"# {data['name']}\n\nWhat this server changes, and why. Free form.\n",
    )
    _write_if_absent(directory / ".gitignore", GITIGNORE)
    _write_if_absent(directory / ".gitattributes", GITATTRIBUTES)
    _write_if_absent(directory / f"{MACHINE_FILE}.example", MACHINE_EXAMPLE)
    machine_data = {}
    if client is not None:
        machine_data["client"] = str(Path(client).expanduser())
    if author:
        machine_data["author"] = slug(author)
    elif client is not None:
        machine_data["author"] = author_name(directory)
    if machine_data:
        _write_if_absent(
            directory / MACHINE_FILE,
            json.dumps(machine_data, indent=2, sort_keys=True) + "\n",
        )

    project = load(directory)
    if git:
        _git_init(directory)
    return project


def _write_if_absent(path: Path, content: str) -> None:
    """Scaffolding never overwrites: an operator's own file wins."""
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _name_from(directory: Path) -> str:
    """What a 6.3 authoring directory calls itself: its `mod.json`, else the
    directory's own name."""
    meta = directory / "mod.json"
    if meta.is_file():
        try:
            document = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = None
        if isinstance(document, dict) and document.get("id"):
            return str(document["id"])
    return directory.name


def import_authoring(
    directory: str | os.PathLike,
    *,
    name: str | None = None,
    client: str | os.PathLike | None = None,
    cache: str = "shared",
    environments=(),
    author: str | None = None,
    git: bool = False,
    at: str | None = None,
) -> dict:
    """Make a 6.3 authoring directory into a project, **in place**.

    The scaffolding `new` writes (and never over: an operator's own notes,
    `.gitignore` and machine file stay), plus the legacy single-file journal
    migrated to a shard — the conversion is the kernel's
    (`journal.migrate_legacy`), the facts it owes go to the deploy log, and the
    old file stays beside the shard as `journal.json.migrated`.
    """
    from . import journal as journal_mod

    root = Path(directory).expanduser()
    if (root / PROJECT_FILE).is_file():
        raise ProjectError(f"{root} is already a project")
    markers = (root / "mod.json", root / journal_mod.LEGACY_FILE, root / "client.patch.json")
    if not any(marker.is_file() for marker in markers):
        raise ProjectError(
            f"{root} is not an authoring directory: no mod.json, no "
            f"{journal_mod.LEGACY_FILE}, no built pack"
        )
    project = new(
        root,
        name=name or _name_from(root),
        client=client,
        cache=cache,
        environments=environments,
        author=author,
        git=git,
        created=at,
    )
    report = journal_mod.migrate_legacy(
        root, author=author_name(root), base=project.fingerprints, at=at
    )
    facts = []
    if report is not None:
        # The identity the legacy file carried survives when it was the only
        # place it lived: the new model reads `mod.json` first, and `new` does
        # not write one, so an import writes it from the journal's own block.
        if report.get("mod") and not (root / "mod.json").is_file():
            (root / "mod.json").write_text(
                json.dumps(report["mod"], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        facts = journal_mod.migration_facts(report)
        for fact in facts:
            record_deploy(root, fact)
    return {"project": load(root), "migration": report, "facts": facts}


def _git_init(directory: Path) -> None:
    """`git init`, and nothing else — no commit, no remote, no surprise: the
    operator's history is theirs to write."""
    try:
        result = subprocess.run(
            ["git", "init", "--quiet", str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise ProjectError("git is not installed: the project was created without it") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ProjectError(f"git init failed: {detail[-1] if detail else result.returncode}")


# -- reading -----------------------------------------------------------------


def load(root: str | os.PathLike) -> Project:
    """Read a project, refusing a format this build does not know — by name."""
    directory = Path(root).expanduser()
    path = directory / PROJECT_FILE
    if not path.is_file():
        raise ProjectError(f"not a project: no {PROJECT_FILE} in {directory}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise ProjectError(f"{path}: expected an object")
    found = str(data.get("format") or "")
    if found != FORMAT:
        raise ProjectError(
            f"{path}: format {found or '(none)'} is not one this build reads (it reads {FORMAT})"
        )
    return Project(root=directory, data=data)


# -- the lock ----------------------------------------------------------------


def lock_state(project: Project) -> Holder | None:
    """Who holds this working copy, or None. A stale local holder is reported
    as such (the caller decides whether to take it): a crashed session must not
    make the project unwritable forever."""
    path = project.root / LOCK_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Holder(
            pid=int(data["pid"]),
            host=str(data["host"]),
            author=str(data.get("author") or "unknown"),
            at=str(data.get("at") or ""),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        # A half-written lock is not a holder: the next writer replaces it.
        return None


def lock(
    project: Project, *, author: str, now: str | None = None, take_stale: bool = True
) -> Holder:
    """Take the working copy's lock, or refuse naming who holds it. The lock is
    about *one checkout* (a daemon and a CLI, say), never about the team: that
    is the repository's business (`project.md` §D13)."""
    current = lock_state(project)
    if current is not None and (current.is_alive or not take_stale):
        raise ProjectError(
            f"{project.root} is held by {current.author} (pid {current.pid} on {current.host}, "
            f"since {current.at or 'an unknown time'})"
        )
    holder = Holder(pid=os.getpid(), host=socket.gethostname(), author=slug(author), at=_now(now))
    (project.root / LOCK_FILE).write_text(
        json.dumps(holder.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return holder


def unlock(project: Project) -> bool:
    """Let the working copy go. Returns whether a lock was there to drop."""
    path = project.root / LOCK_FILE
    if not path.is_file():
        return False
    path.unlink()
    return True


# -- reading the base back ---------------------------------------------------


# -- populating: what an edit depends on -------------------------------------


def zones_on_disk(client: str | os.PathLike) -> list[int]:
    """The zone ids the client ships, read from the folders themselves.

    The index inside `zones/zones.mpk` is the client's own truth for *what a
    zone is*; the folders are what a populate walks, so a client whose index is
    unreadable (or a zone the index disables) is still workable.
    """
    root = clientfs.resolve(str(client), "zones")
    if root is None:
        return []
    found: list[int] = []
    for name in os.listdir(root):
        match = re.fullmatch(r"zone(\d+)", name, re.IGNORECASE)
        if match:
            found.append(int(match.group(1)))
    return sorted(found)


def zone_resources(
        client: str | os.PathLike, zone_id: int, *, entries=ZONE_ENTRIES
    ) -> list[str]:
        """The resources one zone's own data lives in, canonically named.

        `csv` answers first and `dat` answers only what it carries — the resolution
        the *reader* itself uses (`Zone.data_entry`), so one entry is one resource
        and not two fingerprints of the same value.
        """
        archives: dict[str, set[str]] = {}
        for logical in ("csv", "dat"):
            relative = f"zones/zone{zone_id:03d}/{logical}{zone_id:03d}.mpk"
            resolved = clientfs.resolve(str(client), relative)
            if resolved is None:
                continue
            try:
                archive = MpakArchive(resolved)
            except MpakError:
                continue
            try:
                archives[relative] = {name.casefold() for name in archive.entries}
            finally:
                archive.close()
        found: list[str] = []
        for entry in entries:
            for relative, names in archives.items():
                if entry.casefold() in names:
                    found.append(f"{relative}#{entry}")
                    break
        return found


def populate(
    client: str | os.PathLike,
    *,
    zones: Iterable[int] = (),
    tables: Iterable[str] = (),
) -> dict[str, str]:
    """The resources a project's *edits* depend on, fingerprinted.

    A shelf, not a copy (`project.md` §6): what a zone is read for is its
    placements and its catalogue, and a table is read *whole* out of
    `gamedata.mpk` — everything else the client ships stays the client's.
    """
    found: dict[str, str] = {}
    for zone_id in zones:
        for resource in zone_resources(client, int(zone_id)):
            found[resource] = fingerprint_resource(client, resource)
    for table in tables:
        resource = f"{GAMEDATA_ARCHIVE}#{table}"
        found[resource] = fingerprint_resource(client, resource)
    return found


def add_fingerprints(
    root: str | os.PathLike, found: dict[str, str], *, update: bool = False
) -> tuple[Project, list[str]]:
    """Record what `populate` read, **without moving the base**.

    A resource already recorded with another value is a *moved base*: it is
    refused by name unless the caller says `update=True`, because adopting it
    silently is what re-base (P8) exists for. Returns the project and the
    resources whose value changed.
    """
    project = load(root)
    recorded = dict(project.fingerprints)
    moved = sorted(
        resource
        for resource, value in found.items()
        if resource in recorded and recorded[resource] != value
    )
    if moved and not update:
        raise ProjectError(
            "the base moved under this project: "
            + ", ".join(moved)
            + " — run `camelot project verify`, or pass --update to adopt it"
        )
    recorded.update(found)
    data = dict(project.data)
    base = dict(data.get("base") or {})
    client = dict(base.get("client") or {})
    client["fingerprints"] = dict(sorted(recorded.items()))
    base["client"] = client
    data["base"] = base
    path = project.root / PROJECT_FILE
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return load(project.root), moved


def verify(project: Project, client=None) -> list[dict]:
    """Read the base back: one finding per recorded resource.

    States: `ok` (the bytes are the ones we recorded), `moved` (the client's
    file changed under us — a game update, another tool), `missing` (the file
    or the archive entry is gone). Nothing is repaired here: the report is what
    the operator and `re-base` (P8) act on.
    """
    directory = client_root(project, client)
    recorded = project.fingerprints
    if directory is None:
        raise ProjectError(
            f"{project.name}: no client resolved — write {MACHINE_FILE}, set $DAOC_CLIENT, "
            "or pass --client"
        )
    findings = []
    for resource, want in sorted(recorded.items()):
        try:
            found = fingerprint_resource(directory, resource)
        except ProjectError as error:
            findings.append({"resource": resource, "state": "missing", "detail": str(error)})
            continue
        findings.append(
            {
                "resource": resource,
                "state": "ok" if found == want else "moved",
                "detail": "" if found == want else f"recorded {want[:15]}…, found {found[:15]}…",
            }
        )
    return findings


def info(project: Project) -> dict:
    """What the CLI (and, later, Home) shows without reading a byte of the
    base: the recorded facts, the machine's own answers, the lock."""
    shards = sorted(path.name for path in project.path("journal").glob("*.jsonl"))
    releases = sorted(path.name for path in project.path("releases").glob("[0-9]*"))
    holder = lock_state(project)
    directory = client_root(project)
    return {
        "name": project.name,
        "root": str(project.root),
        "format": project.format,
        "created": project.created,
        "cache": project.cache,
        "environments": project.environments,
        "build": project.build,
        "resources": len(project.fingerprints),
        "client": str(directory) if directory is not None else None,
        "author": author_name(project.root),
        "git": is_git_repo(project.root),
        "shards": shards,
        "releases": releases,
        "lock": holder.as_dict() if holder is not None else None,
    }
