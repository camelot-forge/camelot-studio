"""Home: the daemon's own facts, the machine's projects, one health check.

The read half of the front door (`workspace/notes/home.md`, P2
slice 2a): what this daemon serves, which projects this machine has opened,
and — one at a time, when a page asks — whether a project's client still
matches what was recorded.

Nothing here writes. Opening, creating and cloning are slice 2b; the registry
grows on its own when a daemon serves a project (`cli.cmd_serve`), because the
daemon is the only thing that knows a project was opened.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import __version__, recent
from .. import project as project_mod
from .api import build_id
from .config import ServeConfig
from .models import (
    HomeBrowse,
    HomeBrowseEntry,
    HomeDaemon,
    HomeHealth,
    HomePayload,
    HomeProject,
    HomeRecent,
)

#: One `browse` answer is a field's completion, not a file manager.
BROWSE_LIMIT = 200


def build_home_router(config: ServeConfig, world=None) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    editor = getattr(world, "editor", None)

    def daemon_facts() -> HomeDaemon:
        return HomeDaemon(
            version=__version__,
            build=build_id(),
            host=config.host,
            port=config.port,
            client=str(config.root),
            zones=len(project_mod.zones_on_disk(config.root)),
        )

    def current_project() -> HomeProject | None:
        if config.project is None or editor is None or not editor.available:
            return None
        root = Path(editor.dir)
        try:
            name = project_mod.load(root).name
        except project_mod.ProjectError:
            # A 6.3 authoring directory that is not a project: it serves, it is
            # simply not one — the folder's own name is what the chip shows.
            name = root.name
        holder = ""
        try:
            state = project_mod.lock_state(root)
        except project_mod.ProjectError:
            state = None
        if config.read_only and state is not None:
            holder = f"{state.author} (pid {state.pid} on {state.host})"
        return HomeProject(
            path=str(root),
            name=name,
            pending=editor.pending_counts(),
            read_only=bool(config.read_only),
            holder=holder,
        )

    def recent_rows() -> tuple[list[HomeRecent], str]:
        try:
            found = recent.entries(config.config_dir)
        except recent.RecentError as error:
            return [], str(error)
        rows: list[HomeRecent] = []
        for entry in found:
            folder = Path(entry["path"])
            rows.append(
                HomeRecent(
                    **entry,
                    exists=folder.is_dir(),
                    project=(folder / project_mod.PROJECT_FILE).is_file(),
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
            project=current_project(),
            recents=rows,
            recent_error=error,
        )

    @router.get("/home/health", response_model=HomeHealth)
    def home_health(path: str) -> HomeHealth:
        """One project's `verify`, in the operator's words (`home.md` §D2)."""
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise HTTPException(status_code=400, detail=f"{folder}: no such directory")
        try:
            project = project_mod.load(folder)
        except project_mod.ProjectError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
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

    return router
