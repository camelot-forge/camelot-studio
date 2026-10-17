"""Home: the daemon's own facts, the machine's projects, one health check —
and the four doors (`home.md` §D3/§D4).

Slice 2a was the read half: what this daemon serves, which projects this
machine has opened, one project's health. This is the writing half: opening
another project (the switch, §D3), creating one from a clean client, importing
an edits folder, cloning a repository. What no door ever writes is a client —
a project is a folder, and the create modal says so (`home.md` §1).

Everything that changes *what the daemon serves* goes through `Session`
(`serve/session.py`), which owns the shared config and the checkout's lock.
What the doors write into a project folder is the same scaffolding
`camelot project new/import` writes — never a parallel implementation.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import __version__, recent
from .. import journal as journal_mod
from .. import project as project_mod
from .api import build_id
from .config import ServeConfig
from .editor import Editor
from .models import (
    HomeBrowse,
    HomeBrowseEntry,
    HomeClient,
    HomeCloneBody,
    HomeDaemon,
    HomeDoor,
    HomeForgotten,
    HomeHealth,
    HomeImportBody,
    HomeNewBody,
    HomeOpenBody,
    HomePayload,
    HomeRecent,
)
from .session import Session

#: One `browse` answer is a field's completion, not a file manager.
BROWSE_LIMIT = 200

#: The client's own tables a project's *edits* lean on (`home.md` §D4): the
#: model a mob or a world object points at, an item's own row. Recorded whole,
#: like every table `populate` reads — a partial record would make a row's
#: `client ok` mean "the part we recorded is unchanged", a lie by omission.
EDIT_TABLES = ("items.csv", "monsters.csv", "objects.csv")


def shipped_tables(client: Path) -> list[str]:
    """The `EDIT_TABLES` this client actually carries. A client that never
    shipped one is not refused over it: the table is simply not part of what
    `client ok` can be checked against."""
    found: list[str] = []
    for table in EDIT_TABLES:
        try:
            project_mod.read_resource(client, f"{project_mod.GAMEDATA_ARCHIVE}#{table}")
        except (project_mod.ProjectError, OSError):
            continue
        found.append(table)
    return found


def pending_of(config: ServeConfig, root: Path) -> dict[str, int]:
    """What one recent project still holds (`home.md` §D1: the row's own
    count, read from the project when the page asks). The editor's own method,
    on a copy of this daemon's config — one definition, so a row and the panel
    cannot disagree. A folder whose journal is still the legacy single file is
    left alone: reading a row must never migrate a project."""
    if (root / journal_mod.LEGACY_FILE).is_file():
        return {}
    try:
        return Editor(dataclasses.replace(config, project=root)).pending_counts()
    except (project_mod.ProjectError, OSError):
        return {}


def _total(counts: dict[str, int]) -> int:
    return int(counts.get("fixtures", 0)) + int(counts.get("rows", 0))


def _client_of(config: ServeConfig, wanted: str) -> Path:
    """The DAoC client a door records: the one the operator named, else the
    client this daemon already serves. A folder that is not there is refused by
    name; whether it *is* a client is the reader's business (`project.new`
    fingerprints the base and refuses a missing resource by name)."""
    directory = Path(wanted).expanduser() if wanted else config.root
    if not directory.is_dir():
        raise project_mod.ProjectError(f"no client directory at {directory}")
    return directory


def _outcome(
    config: ServeConfig,
    session: Session,
    project: project_mod.Project,
    *,
    open_it: bool,
    resources: int = 0,
    zones: int = 0,
) -> HomeDoor:
    """One answer for the three doors that write (`home.md` §D4): the project's
    facts, opened through D3's switch when the operator asked for it — and
    listed either way, because a project this machine created belongs in the
    list whether or not it is the one being served."""
    if open_it:
        door = session.open(project.root)  # the switch records the visit itself
    else:
        door = HomeDoor(path=str(project.root), name=project.name)
        try:
            recent.record(project.root, name=project.name, directory=config.config_dir)
        except recent.RecentError as error:
            door = door.model_copy(update={"warning": f"not listed: {error}"})
    return door.model_copy(update={"resources": resources, "zones": zones})


def _create(config: ServeConfig, session: Session, body: HomeNewBody) -> HomeDoor:
    """D4: create, then record **the whole client** — every zone folder plus
    the tables an edit reads. A partial record would make `client ok` mean
    "the part we recorded is unchanged"."""
    root = Path(body.dir).expanduser()
    if (root / project_mod.PROJECT_FILE).is_file():
        raise project_mod.ProjectError(f"a project already lives here: {root}")
    client = _client_of(config, body.client)
    environments = ("local", "staging", "production") if body.staging else ("local", "production")
    project = project_mod.new(
        root,
        name=body.name or None,
        client=client,
        environments=environments,
        git=body.git,
    )
    zones = project_mod.zones_on_disk(client)
    found = project_mod.populate(client, zones=zones, tables=shipped_tables(client))
    project, _moved = project_mod.add_fingerprints(project.root, found)
    return _outcome(
        config, session, project,
        open_it=body.open, resources=len(project.fingerprints), zones=len(zones),
    )


def _import(config: ServeConfig, session: Session, body: HomeImportBody) -> HomeDoor:
    """The import door (`project.md` §D14): an existing edits folder becomes a
    project, **in place** — its own notes, gitignore and machine file stay."""
    client = _client_of(config, body.client)
    report = project_mod.import_authoring(
        Path(body.dir).expanduser(),
        name=body.name or None,
        client=client,
        git=body.git,
    )
    project = report["project"]
    return _outcome(
        config, session, project,
        open_it=body.open, resources=len(project.fingerprints),
    )


def _clone(config: ServeConfig, session: Session, body: HomeCloneBody) -> HomeDoor:
    """The clone door: `git clone`, run by the daemon, then opened like any
    other project. A repository that is not a project yet is said as such —
    importing it is a different door."""
    directory = Path(body.dir).expanduser()
    if directory.exists():
        raise project_mod.ProjectError(
            f"{directory}: the folder already exists — `git clone` writes into a new one"
        )
    _git_clone(body.url, directory)
    if not (directory / project_mod.PROJECT_FILE).is_file():
        return HomeDoor(
            path=str(directory),
            name=directory.name,
            warning=(
                f"cloned, but it is not a project: no {project_mod.PROJECT_FILE} "
                "in it — use Import a folder instead"
            ),
        )
    client = _client_of(config, body.client)
    _write_machine(directory, client)
    project = project_mod.load(directory)
    return _outcome(
        config, session, project,
        open_it=body.open, resources=len(project.fingerprints),
    )


def _git_clone(url: str, directory: Path) -> None:
    """`git clone`, run by the daemon (`home.md` §D4): the operator's own
    process, with the operator's own credentials — so a private repository
    works, and no key ever reaches the browser."""
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "clone", "--quiet", url, str(directory)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise project_mod.ProjectError("git is not installed") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise project_mod.ProjectError(
            f"git clone failed: {detail[-1] if detail else result.returncode}"
        )


def _write_machine(root: Path, client: Path) -> None:
    """This machine's own answers *about that project* (`project.md` §D4),
    written into a fresh clone only when it carries none: untracked, never
    overwritten, and what makes D3.3 resolve the client without its fallback."""
    path = root / project_mod.MACHINE_FILE
    if path.is_file():
        return
    document = {"client": str(client), "author": project_mod.author_name(root)}
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_home_router(config: ServeConfig, world, session: Session) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def daemon_facts() -> HomeDaemon:
        return HomeDaemon(
            version=__version__,
            build=build_id(),
            host=config.host,
            port=config.port,
            client=str(config.root),
            zones=len(project_mod.zones_on_disk(config.root)),
        )

    def recent_rows() -> tuple[list[HomeRecent], str]:
        """The machine's list, as the disk stands now: `exists` and `project`
        read from the folders, and the work waiting read from each project
        (§D1 — the registry carries no copies). Five journals at most, and a
        journal read is a file read."""
        try:
            found = recent.entries(config.config_dir)
        except recent.RecentError as error:
            return [], str(error)
        rows: list[HomeRecent] = []
        for entry in found:
            folder = Path(entry["path"])
            is_project = (folder / project_mod.PROJECT_FILE).is_file()
            rows.append(
                HomeRecent(
                    **entry,
                    exists=folder.is_dir(),
                    project=is_project,
                    pending=_total(pending_of(config, folder)) if is_project else 0,
                )
            )
        return rows, ""

    @router.get("/home", response_model=HomePayload)
    def home() -> HomePayload:
        """Home's first paint. No `verify` runs here: the rows ask for their
        own health, so the page is never held hostage by a slow disk."""
        rows, error = recent_rows()
        return HomePayload(
            daemon=daemon_facts(),
            project=session.current(),
            recents=rows,
            recent_error=error,
        )

    @router.get("/home/health", response_model=HomeHealth)
    def home_health(path: str) -> HomeHealth:
        """One project's `verify`, in the operator's words (`home.md` §D2) —
        plus the work waiting, so a row can say both in one answer."""
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise HTTPException(status_code=400, detail=f"{folder}: no such directory")
        try:
            project = project_mod.load(folder)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        pending = pending_of(config, project.root)
        client = project_mod.client_root(project)
        if client is None:
            return HomeHealth(
                path=str(project.root),
                name=project.name,
                state="no-client",
                detail=(
                    f"no client resolved — write {project_mod.MACHINE_FILE}, "
                    "set $DAOC_CLIENT, or open it with --client"
                ),
                pending=pending,
            )
        findings = project_mod.verify(project, client)
        changed = [finding for finding in findings if finding["state"] != "ok"]
        if not changed:
            state, detail = "ok", f"{len(findings)} resources unchanged"
        else:
            missing = any(finding["state"] == "missing" for finding in changed)
            state = "missing" if missing else "changed"
            detail = f"{len(changed)} of {len(findings)} resources no longer match"
        return HomeHealth(
            path=str(project.root),
            name=project.name,
            state=state,
            detail=detail,
            findings=changed[:50],
            pending=pending,
        )

    @router.get("/home/client", response_model=HomeClient)
    def home_client(path: str) -> HomeClient:
        """Is this folder a DAoC client? The create modal's own check, run
        *before* anything is written (`home.md` §D4) — cheap on purpose: the
        zone folders and the build, never a `populate`."""
        folder = Path(path).expanduser()
        if not folder.is_dir():
            return HomeClient(
                path=str(folder), state="missing", detail=f"{folder}: no such directory"
            )
        zones = project_mod.zones_on_disk(folder)
        if not zones:
            return HomeClient(
                path=str(folder),
                state="not-a-client",
                detail=f"{folder}: no zones folder — this is not a DAoC client",
            )
        return HomeClient(
            path=str(folder),
            state="ok",
            detail=f"{len(zones)} zones",
            build=project_mod.client_build(str(folder)) or "",
            zones=len(zones),
        )

    @router.get("/home/browse", response_model=HomeBrowse)
    def home_browse(path: str = "") -> HomeBrowse:
        """The sub-folders of one directory, for a path field's completion."""
        folder = Path(path).expanduser() if path else Path.home()
        if not folder.is_dir():
            raise HTTPException(status_code=400, detail=f"{folder}: no such directory")
        folder = folder.resolve()
        found: list[HomeBrowseEntry] = []
        try:
            for child in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
                if child.name.startswith(".") or not child.is_dir():
                    continue
                found.append(
                    HomeBrowseEntry(
                        name=child.name,
                        path=str(child),
                        project=(child / project_mod.PROJECT_FILE).is_file(),
                    )
                )
        except OSError as error:
            raise HTTPException(status_code=400, detail=f"{folder}: {error}") from error
        return HomeBrowse(
            path=str(folder),
            parent=str(folder.parent) if folder.parent != folder else None,
            dirs=found[:BROWSE_LIMIT],
        )

    @router.post("/home/forget", response_model=HomeForgotten)
    def home_forget(body: HomeOpenBody) -> HomeForgotten:
        """Drop one entry from the machine's list — the folder itself is never
        touched (`home.md` §D1: a list, not a record). The answer carries the
        list as it stands, so the page needs one round trip."""
        try:
            forgotten = recent.forget(body.path, config.config_dir)
        except recent.RecentError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        rows, _error = recent_rows()
        return HomeForgotten(path=str(Path(body.path).expanduser()), forgotten=forgotten,
                             recents=rows)

    @router.post("/home/open", response_model=HomeDoor)
    def home_open(body: HomeOpenBody) -> HomeDoor:
        """D3's switch: the daemon serves another project — and says what the
        one it leaves still holds. It warns; only an unreadable project is
        refused."""
        try:
            return session.open(body.path)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/home/new", response_model=HomeDoor)
    def home_new(body: HomeNewBody) -> HomeDoor:
        """D4's create: `project.new` + `populate` over every zone the client
        ships, then the switch when the modal asked for it."""
        try:
            return _create(config, session, body)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/home/import", response_model=HomeDoor)
    def home_import(body: HomeImportBody) -> HomeDoor:
        """The import door (`project.md` §D14): a 6.3 edits folder becomes a
        project where it stands."""
        try:
            return _import(config, session, body)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/home/clone", response_model=HomeDoor)
    def home_clone(body: HomeCloneBody) -> HomeDoor:
        """The clone door: `git clone` by the daemon, then opened like any
        other project."""
        try:
            return _clone(config, session, body)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    return router
