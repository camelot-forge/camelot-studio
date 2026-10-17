"""What one daemon serves, and the only way it changes (`home.md` §D3).

`create_app` builds one `Session`; every router shares the same `ServeConfig`,
so a switch **mutates that object in place** — no router is rebuilt and the
next request already sees the new project. What a switch drops is everything
built *per project or per client*: the asset index, the zone index, the
server-rows snapshot, the editor's journal and its live row connection. What
it keeps are the on-disk caches under `cache/`: they are keyed by file path
and mtime, so a switch never collides across clients and never re-exports an
artefact it already has.

The session also owns the checkout's lock (`project.md` §D13): `camelot
serve` takes it through `claim()`, a switch hands it over, and `release()`
drops it when the daemon stops.
"""
from __future__ import annotations

import os
import signal
from pathlib import Path

from .. import project as project_mod
from .. import recent
from .config import ServeConfig
from .editor import Editor
from .index import AssetIndex
from .models import EditorState, HomeDoor, HomeLeft, HomeProject
from .watcher import IndexWatcher
from .world import World


class Session:
    """What this daemon serves: the config every router shares, the indexes
    built from it, and the lock this process holds."""

    def __init__(
        self,
        config: ServeConfig,
        index: AssetIndex,
        watcher: IndexWatcher,
        world: World,
    ) -> None:
        self.config = config
        self.index = index
        self.watcher = watcher
        self.world = world
        #: The project whose lock this process holds, or None. Taken at startup
        #: and handed over on a switch (`home.md` §D3.2).
        self.claimed: str | None = None

    @property
    def editor(self) -> Editor | None:
        return self.world.editor

    # -- reading -------------------------------------------------------------

    def current(self) -> HomeProject | None:
        """The project this daemon serves (`home.md` §D5): its name, the work
        waiting, and whether another process holds the checkout. The same
        answer Home's first paint, the chip and a switch all show."""
        if self.config.project is None or self.editor is None or not self.editor.available:
            return None
        root = Path(self.editor.dir)
        return HomeProject(
            path=str(root),
            name=self.editor.name(),
            pending=self.editor.pending_counts(),
            read_only=bool(self.config.read_only),
            holder=self.editor.holder(),
        )

    def left(self) -> HomeLeft | None:
        """What the project being left still holds (`home.md` §D3.1). The
        journal *is* the save: this is a line Home shows, never a wall."""
        facts = self.current()
        if facts is None:
            return None
        return HomeLeft(
            path=facts.path,
            name=facts.name,
            pending=facts.pending.get("fixtures", 0) + facts.pending.get("rows", 0),
        )

    # -- the lock ------------------------------------------------------------

    def claim(self) -> dict:
        """Take this checkout's lock for the life of the daemon — the sequence
        `camelot serve` already ran, now owned here so that a switch can hand
        it over. `{"held": False, "by": …}` means another live process has it:
        this daemon then runs **read-only** (`project.md` §D13). The `recent`
        key carries a line when the machine's list could not be written."""
        claim = self._take(self.config.project)
        claim["recent"] = self._remember()
        return claim

    def release(self) -> None:
        """Drop the lock this process holds, if any — the daemon is stopping.
        A switch has already moved it to the project we serve now."""
        if self.claimed is None:
            return
        project_mod.release(self.claimed)
        self.claimed = None

    def release_on_signal(self, signals=(signal.SIGTERM, signal.SIGINT)) -> None:
        """Drop the lock when the process is *asked* to stop (`project.md`
        §D13) — the one moment a `finally` misses.

        `uvicorn` catches the signal, shuts down gracefully, then re-raises it
        so the process exits with the code a stop expects: the run never
        returns, so nothing after `uvicorn.run(...)` runs. It restores the
        handlers it found before re-raising, which is exactly what makes this
        work: our handler is the one that runs then — it drops the lock and
        lets the signal do its job.
        """

        def stop(signum, _frame):
            self.release()
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        for signum in signals:
            signal.signal(signum, stop)

    def _remember(self, root: Path | None = None) -> str:
        """Put a project at the front of the machine's list (`home.md` §D1).
        The daemon is what knows a project was opened: it does this when it
        takes the checkout, and after every switch. Returns a line when the
        registry could not be written — never a refusal, since the list is a
        convenience — and nothing when there was nothing to list."""
        directory = root if root is not None else self.config.project
        if directory is None or not (Path(directory) / project_mod.PROJECT_FILE).is_file():
            return ""
        try:
            recent.record(
                directory,
                name=project_mod.load(directory).name,
                directory=self.config.config_dir,
            )
        except (project_mod.ProjectError, recent.RecentError) as error:
            return str(error)
        return ""

    def _take(self, directory) -> dict:
        """Take one project's lock, and set `read_only` from what happened."""
        if directory is None:
            self.claimed = None
            self.config.read_only = False
            return {"held": True, "holder": None}
        path = Path(directory)
        if not (path / project_mod.PROJECT_FILE).is_file():
            # A legacy authoring directory has nothing to lock and is never
            # read-only (`project.md` §D13).
            self.claimed = None
            self.config.read_only = False
            return {"held": True, "holder": None}
        claim = project_mod.claim(path, author=project_mod.author_name(path))
        if claim["held"]:
            self.claimed = str(path)
            self.config.read_only = False
        else:
            self.claimed = None
            self.config.read_only = True
        return claim

    # -- the switch ----------------------------------------------------------

    def open(self, directory) -> HomeDoor:
        """D3's switch, in the order `home.md` names the steps: what we leave
        is answered first, then the lock hands over, the client is re-resolved
        from the project (never inherited), the per-project caches are dropped
        in place, and the visit is recorded."""
        project = project_mod.load(directory)  # a refusal names the file
        here = self.config.project
        same = here is not None and Path(here).resolve() == project.root.resolve()
        if not same:
            left = self.left()
            previous = self.claimed
            self._take(project.root)
            if previous is not None and previous != self.claimed:
                project_mod.release(previous)
            client, warning = self._client_for(project)
            self._adopt(project, client)
        else:
            # Opening what we already serve: the date is refreshed, nothing is
            # rebuilt, and there is nothing to warn about.
            left = None
            warning = ""
        listed = self._remember(project.root)
        if listed:
            warning = "; ".join(part for part in (warning, f"not listed: {listed}") if part)
        state = (
            self.editor.state() if self.editor is not None else EditorState(available=False)
        )
        return HomeDoor(
            path=str(project.root),
            name=project.name,
            opened=True,
            state=state,
            left=left,
            warning=warning,
        )

    def _client_for(self, project: project_mod.Project) -> tuple[Path, str]:
        """`machine.json` first, then `$DAOC_CLIENT`, then the root this daemon
        already reads — and that last fallback warns, because the base it
        points at may not be this project's (`home.md` §D3.3)."""
        found = project_mod.client_root(project)
        if found is not None:
            return found, ""
        root = self.config.root
        return root, f"{project.name} names no client — this daemon keeps reading {root}"

    def _adopt(self, project: project_mod.Project, client: Path) -> None:
        """Mutate the shared config and drop every per-project cache — in
        place, because every router holds these objects (`home.md` §D3.4)."""
        self.config.project = project.root
        self.config.root = Path(client).expanduser().resolve()
        if self.editor is not None:
            self.editor.repoint()
        self.world.repoint()
        self.index.repoint(self.config.root)
        self.watcher.follow_new_root()
