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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import clientpatch
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

CACHE_POLICIES = ("shared", "local")
#: The environment names a project carries. `local` and `production` are the
#: only kinds with hard meaning; `staging` is optional (operator's answer 1).
ENVIRONMENTS = ("local", "staging", "production")

#: What `new` records of the client: its own build binary, the tables and the
#: zone index every later read goes through. A resource is a path relative to
#: the client root, optionally an archive entry after `#` (the zone index
#: lives inside `zones/zones.mpk`). The per-zone and per-table resources are
#: added by `populate` (P1b) in the same shape.
BASE_RESOURCES = (
    clientpatch.CLIENT_BINARY,
    "gamedata.mpk",
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

    `zones/zones.mpk#zones.dat` is the shape the zone index needs; plain files
    (`gamedata.mpk`, the client binary) are the common case.
    """
    root = Path(client_root)
    path, _, entry = resource.partition("#")
    target = root / path
    if not target.is_file():
        raise ProjectError(f"{resource}: no {path} under {root}")
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
    (directory / NOTES).write_text(
        f"# {data['name']}\n\nWhat this server changes, and why. Free form.\n", encoding="utf-8"
    )
    (directory / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (directory / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    (directory / f"{MACHINE_FILE}.example").write_text(MACHINE_EXAMPLE, encoding="utf-8")
    machine_data = {}
    if client is not None:
        machine_data["client"] = str(Path(client).expanduser())
    if author:
        machine_data["author"] = slug(author)
    elif client is not None:
        machine_data["author"] = author_name(directory)
    if machine_data:
        (directory / MACHINE_FILE).write_text(
            json.dumps(machine_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    project = load(directory)
    if git:
        _git_init(directory)
    return project


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
