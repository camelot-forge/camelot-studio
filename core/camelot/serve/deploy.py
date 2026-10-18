"""The Deploy page's facts (`pipeline.md` §6, P4).

The page is a face on the verbs `pipeline` already implements, never a second
implementation: it reads the project's releases, the environments `machine.json`
names and the deploy log; it asks for the dry run of one release against one
environment; and it runs exactly that table. A daemon that does not hold the
checkout's lock refuses the writes, with the same sentence the editor uses
(`project.md` §D13).

The feed panel reads the project's own `feed/` (C §D4). A feed that is not
there yet is a fact the page says out loud — building it is P6.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import clientinstall, feedbuild
from .. import feed as feed_mod
from .. import pipeline as pipeline_mod
from .. import project as project_mod
from .. import release as release_mod
from .config import ServeConfig
from .models import DeployAsk, DeployEnvAsk

#: How many log lines the page shows. The log itself is never truncated: this
#: is a window on the newest attempts, not the record.
LOG_LINES = 20


def _root(config: ServeConfig) -> Path:
    if config.project is None:
        raise HTTPException(
            status_code=503,
            detail="the deploy page needs a project: start the daemon with --project <dir>",
        )
    return Path(config.project)


def _writable(config: ServeConfig) -> None:
    if config.read_only:
        raise HTTPException(
            status_code=409,
            detail="this project is held by another daemon: this one is read-only — "
            "close the other one, or stop it and reload",
        )


def _refused(error: Exception) -> HTTPException:
    """The pipeline's own refusals are the operator's business (`pipeline.md`
    §D7): a 400 with the sentence, never a stack trace."""
    return HTTPException(status_code=400, detail=str(error))


def _environments(project: project_mod.Project, live: dict[str, dict]) -> list[dict]:
    """One row per declared environment: where its folder is on this machine,
    what it holds, and whether its client is there. A name with no folder is a
    row that says so, not an error that hides the others."""
    found: list[dict] = []
    for name in pipeline_mod.names(project):
        try:
            folder = pipeline_mod.folder(project, name)
        except pipeline_mod.PipelineError as error:
            found.append(
                {
                    "name": name,
                    "root": None,
                    "exists": False,
                    "live": None,
                    "client": None,
                    "problem": str(error),
                }
            )
            continue
        client = pipeline_mod.client_of(folder)
        found.append(
            {
                "name": name,
                "root": str(folder),
                "exists": folder.is_dir(),
                "live": live.get(name),
                "client": {"path": str(client), "exists": client.is_dir()},
                "problem": None,
            }
        )
    return found


def _releases(root, live: dict[str, dict]) -> list[dict]:
    """The history, newest first — what each cut holds, where it is live, and
    the reload it names. Read from the records themselves: the page never
    summarises a release from the log."""
    found: list[dict] = []
    for record in release_mod.releases(root):
        number = int(record["number"])
        server = (record.get("artifacts") or {}).get("server") or {}
        client_half = (record.get("artifacts") or {}).get("client") or {}
        found.append(
            {
                "number": number,
                "cut_at": record.get("cut_at"),
                "families": record.get("families") or {},
                "order": record.get("order"),
                "needs": record.get("needs") or {},
                "server": {"sha256": server.get("sha256"), "pack": server.get("pack")},
                "client": {
                    "version": client_half.get("version"),
                    "patchlist": client_half.get("patchlist"),
                },
                "live_in": sorted(
                    name
                    for name, fact in live.items()
                    if fact and int(fact.get("release") or 0) == number
                ),
            }
        )
    return list(reversed(found))


def build_deploy_router(config: ServeConfig, world=None) -> APIRouter:
    """The Deploy page's routes: its facts, the dry run, the push, the read-back
    and the feed build. `world` is only read, for the work the chip shows."""
    router = APIRouter(prefix="/api/v1")
    progress = {
        "running": False,
        "stage": "",
        "done": 0,
        "total": 0,
        "error": None,
        "version": None,
        "path": None,
    }
    guard = threading.Lock()

    def publish_progress() -> dict:
        return {"progress": dict(progress)}

    def publish_target() -> tuple[str, str]:
        """The two values the feed server will serve; the daemon reads the same
        environment, so the page never asks for a URL (`F §D4`)."""
        feed_url = (os.environ.get("CAMELOT_FEED_URL") or "").strip()
        server = (os.environ.get("CAMELOT_GAME_SERVER") or "").strip()
        if not feed_url:
            raise HTTPException(
                status_code=400,
                detail="CAMELOT_FEED_URL is not set: the feed's public base URL",
            )
        feed_url = feed_url if feed_url.endswith("/") else feed_url + "/"
        if not server:
            raise HTTPException(
                status_code=400,
                detail="CAMELOT_GAME_SERVER is not set: the game's host:port",
            )
        if ":" not in server:
            raise HTTPException(
                status_code=400,
                detail=f"CAMELOT_GAME_SERVER must be host:port, got {server!r}",
            )
        return feed_url, server

    def publish_in_background(project, base, feed_url: str, server: str) -> None:
        def step(stage: str, done: int = 0, total: int = 0) -> None:
            progress.update({"stage": stage, "done": done, "total": total})

        try:
            report = feedbuild.build(
                project,
                base=base,
                base_url=feed_url,
                server=server,
                on_step=step,
            )
            progress["version"] = report["version"]
            progress["path"] = report["path"]
        except (feedbuild.FeedError, feed_mod.FeedError, clientinstall.InstallError, OSError) as error:
            progress["error"] = str(error)
            progress["stage"] = "error"
        finally:
            progress["running"] = False

    def work_waiting() -> dict[str, int]:
        editor = getattr(world, "editor", None)
        if editor is None or not editor.available:
            return {"fixtures": 0, "rows": 0}
        return editor.pending_counts()

    @router.get("/deploy")
    def deploy_page() -> dict:
        """What the page draws: the environments, the releases, the feed and the
        newest log lines. Read-only, so it answers on a read-only daemon."""
        root = _root(config)
        try:
            project = project_mod.load(root)
            live = pipeline_mod.live(root)
            return {
                "project": project.name,
                "root": str(root),
                "read_only": bool(config.read_only),
                "pending": work_waiting(),
                "environments": _environments(project, live),
                "releases": _releases(root, live),
                "feed": pipeline_mod.feed(root),
                "publish": dict(progress),
                "log": list(reversed(project_mod.deploy_log(root)[-LOG_LINES:])),
            }
        except (release_mod.ReleaseError, project_mod.ProjectError) as error:
            raise _refused(error) from error

    @router.post("/deploy/plan")
    def deploy_plan(body: DeployAsk) -> dict:
        """The dry run: the table of `pipeline.md` §3, writes nothing."""
        root = _root(config)
        try:
            return pipeline_mod.plan(root, body.release, env=body.env, adopt=body.adopt)
        except (
            pipeline_mod.PipelineError,
            release_mod.ReleaseError,
            project_mod.ProjectError,
            clientinstall.InstallError,
        ) as error:
            raise _refused(error) from error

    @router.post("/deploy/deploy")
    def deploy_push(body: DeployAsk) -> dict:
        """The dry run, then the writes it describes — the same call the CLI's
        `camelot project deploy` makes. `source` turns it into a promotion."""
        root = _root(config)
        _writable(config)
        try:
            if body.source:
                return pipeline_mod.promote(
                    root, body.release, frm=body.source, to=body.env, adopt=body.adopt
                )
            return pipeline_mod.deploy(root, body.release, env=body.env, adopt=body.adopt)
        except (
            pipeline_mod.PipelineError,
            release_mod.ReleaseError,
            project_mod.ProjectError,
            clientinstall.InstallError,
        ) as error:
            raise _refused(error) from error

    @router.post("/deploy/cut")
    def deploy_cut() -> dict:
        """Cut the next release — the pipeline's first verb, from the page. The
        daemon holds the checkout's lock, so the cut runs in the process that
        owns it (`project.md` §D13), and a range with nothing in it is refused
        with the kernel's own sentence (`nothing to release`)."""
        root = _root(config)
        _writable(config)
        try:
            return release_mod.cut(root)
        except (
            pipeline_mod.PipelineError,
            release_mod.ReleaseError,
            project_mod.ProjectError,
            clientinstall.InstallError,
        ) as error:
            raise _refused(error) from error

    @router.get("/deploy/publish")
    def deploy_publish_progress() -> dict:
        """The feed build's state; the page polls it after starting one."""
        return publish_progress()

    @router.post("/deploy/publish")
    def deploy_publish() -> dict:
        """Build the players' feed in the background: the same `feedbuild.build`
        as `camelot project feed`, with the public URL and game address read from
        the daemon's environment (`F §D4`)."""
        root = _root(config)
        _writable(config)
        feed_url, server = publish_target()
        with guard:
            if progress["running"]:
                raise HTTPException(status_code=409, detail="a feed build is already running")
            try:
                project = project_mod.load(root)
                # The feed's base is the project's own client (`machine.json.client`),
                # not the daemon's root: a local daemon edits an environment's install,
                # and a feed built against that would compare a tree with itself. The
                # daemon's root is only a fallback for a project that names none.
                base = project_mod.client_root(project) or config.root
            except project_mod.ProjectError as error:
                raise _refused(error) from error
            progress.update(
                {
                    "running": True,
                    "stage": "staging",
                    "done": 0,
                    "total": 0,
                    "error": None,
                    "version": None,
                    "path": None,
                }
            )
            threading.Thread(
                target=publish_in_background,
                args=(project, base, feed_url, server),
                daemon=True,
            ).start()
        return publish_progress()

    @router.post("/deploy/verify")
    def deploy_verify(body: DeployEnvAsk) -> dict:
        """Read one environment back: the package's hashes and the client
        install's own verification (§D11)."""
        root = _root(config)
        try:
            return pipeline_mod.verify(root, env=body.env)
        except (
            pipeline_mod.PipelineError,
            release_mod.ReleaseError,
            project_mod.ProjectError,
        ) as error:
            raise _refused(error) from error

    return router
