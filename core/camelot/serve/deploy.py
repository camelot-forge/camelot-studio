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

import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import clientinstall
from .. import envs as envs_mod
from .. import installer as installer_mod
from .. import machine as machine_mod
from .. import pipeline as pipeline_mod
from .. import project as project_mod
from .. import publish as publish_mod
from .. import release as release_mod
from .. import sign as sign_mod
from .config import ServeConfig
from .models import DeployAsk, DeployEnvAsk, DeployPublishAsk, DeployShardAsk

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

    #: One publish at a time: cut when the journal has edits, build the feed,
    #: sign the record, commit the deploy branch, push it. The page polls it;
    #: the stages are the operator's words, not the pipeline's.
    publish_state = {
        "running": False,
        "stage": "",
        "error": None,
        "release": None,
        "commit": None,
        "pushed": None,
        "signed": None,
    }
    publish_guard = threading.Lock()

    def cut_if_pending(root) -> int | None:
        """The release a publish carries: a new one when the journal holds
        edits, the newest one otherwise (a retry after a failed push)."""
        try:
            return int(release_mod.cut(root)["number"])
        except release_mod.ReleaseError as error:
            if "nothing to release" not in str(error):
                raise
            records = release_mod.releases(root)
            return int(records[-1]["number"]) if records else None

    def publish_in_background(root, remote: str) -> None:
        try:
            publish_state["stage"] = "preparing"
            number = cut_if_pending(root)
            if number is None:
                raise release_mod.ReleaseError(
                    "nothing to publish: the journal holds no edit and no release exists"
                )
            publish_state["release"] = number
            publish_state["stage"] = "publishing"
            report = publish_mod.publish(root, number, build=True, remote=remote)
            publish_state.update(
                {
                    "stage": "done",
                    "release": report["release"],
                    "commit": report["commit"],
                    "pushed": report["pushed"],
                    "signed": (report["signature"] or {}).get("key"),
                }
            )
        except (release_mod.ReleaseError, publish_mod.PublishError, project_mod.ProjectError) as error:
            publish_state.update({"stage": "error", "error": str(error)})
        finally:
            publish_state["running"] = False

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
                "shard": project_mod.shard(root),
                "signing": {"key": sign_mod.configured(None, machine_mod.document(root))},
                "log_dirty": publish_mod.log_dirty(root),
                "publish": dict(publish_state),
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

    @router.post("/deploy/shard")
    def deploy_shard(body: DeployShardAsk) -> dict:
        """The shard's public addresses, written into `project.json` (tracked):
        what the feed says to players, and where the game is. The page stops
        asking the operator for environment variables (`F §D4`)."""
        root = _root(config)
        _writable(config)
        try:
            return project_mod.set_shard(
                root, feed_url=body.feed_url, game_server=body.game_server
            )
        except project_mod.ProjectError as error:
            raise _refused(error) from error

    @router.post("/deploy/commit-log")
    def deploy_commit_log() -> dict:
        """Commit `deploy/log.jsonl` alone, on the working branch, on demand:
        a publish leaves that branch untouched (`remote.md` §D5)."""
        root = _root(config)
        _writable(config)
        try:
            return {"commit": publish_mod.commit_log(root), "dirty": False}
        except publish_mod.PublishError as error:
            raise _refused(error) from error

    @router.post("/deploy/publish")
    def deploy_publish(body: DeployPublishAsk | None = None) -> dict:
        """Publish the edits: cut a release when the journal has some, then build
        the feed, sign it, commit `camelot-deploy` and push. One action, because
        that is the operator's whole question ("are my changes live?")."""
        root = _root(config)
        _writable(config)
        remote = (body.remote if body else "") or "origin"
        try:
            publish_mod.ensure_commit(root)
        except publish_mod.PublishError as error:
            raise _refused(error) from error
        with publish_guard:
            if publish_state["running"]:
                raise HTTPException(status_code=409, detail="a publish is already running")
            publish_state.update(
                {
                    "running": True,
                    "stage": "preparing",
                    "error": None,
                    "release": None,
                    "commit": None,
                    "pushed": None,
                    "signed": None,
                }
            )
            threading.Thread(
                target=publish_in_background,
                args=(root, remote),
                daemon=True,
            ).start()
        return {"progress": dict(publish_state)}

    @router.get("/deploy/publish")
    def deploy_publish_status() -> dict:
        """The publish's state; the page polls it after starting one."""
        return {"progress": dict(publish_state)}

    @router.post("/deploy/test")
    def deploy_test() -> dict:
        """Put the pending edits on this machine's local environment: the same
        cut, the same deploy and the Installer's own apply. What the operator
        sees in game is the release they are about to publish."""
        root = _root(config)
        _writable(config)
        try:
            number = cut_if_pending(root)
            if number is None:
                raise release_mod.ReleaseError(
                    "nothing to test: the journal holds no edit and no release exists"
                )
            deployed = pipeline_mod.deploy(root, number, env="local")
            applied = envs_mod.apply(project_mod.load(root), "local", release=number)
            return {
                "release": number,
                "wrote": len(deployed.get("wrote") or []),
                "applied": len(applied.get("applied") or []),
                "unchanged": bool(applied.get("unchanged")),
            }
        except (
            pipeline_mod.PipelineError,
            release_mod.ReleaseError,
            project_mod.ProjectError,
            clientinstall.InstallError,
            envs_mod.EnvError,
            installer_mod.InstallerError,
        ) as error:
            raise _refused(error) from error

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
