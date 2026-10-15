"""Runtime configuration for the ``camelot serve`` daemon."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8760

CLIENT_ENV = "DAOC_CLIENT"


def default_cache_dir() -> Path:
    """Per-user cache directory (never inside the repo, never versioned)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "camelot-studio"


def client_root_from_env() -> str | None:
    """``$DAOC_CLIENT`` if set and non-empty, else ``None``."""
    value = os.environ.get(CLIENT_ENV)
    return value or None


@dataclass
class ServeConfig:
    """Everything the daemon needs to run, resolved up front."""

    root: Path
    cache_dir: Path = field(default_factory=default_cache_dir)
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    open_browser: bool = True
    watch: bool = True
    token: str | None = None
    static_dir: Path | None = None

    #: The server's world rows (spec §5): a snapshot exported from the
    #: operator's MySQL (`camelot server export`), or the server's own
    #: `serverconfig.xml` for a live read. Neither set: the viewer says so,
    #: and the client's world still renders.
    server_rows: Path | None = None
    db_config: Path | None = None
    #: May the editor *write* the server's own rows (patches and removals)?
    #: Never implied by `db_config` alone: reading is a convenience, writing
    #: is a decision the operator states.
    db_write: bool = False
    #: The project's directory (P1): the whole content of one server — the
    #: journal's shards, the build outputs and the deploy log live there.
    #: Without it the editor routes answer 503 and nothing can be written
    #: anywhere. The flag is `--project`; `--edits` is the older name of the
    #: same directory (the 6.3 authoring directory) and is kept as an alias.
    project: Path | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.cache_dir = Path(self.cache_dir).expanduser()
        if self.server_rows is not None:
            self.server_rows = Path(self.server_rows).expanduser()
        if self.db_config is not None:
            self.db_config = Path(self.db_config).expanduser()
        if self.project is not None:
            self.project = Path(self.project).expanduser()

    @property
    def edits(self) -> Path | None:
        """The older name of `project` (the 6.3 authoring directory). Read-only
        on purpose: the field is the project now, and one name writes it."""
        return self.project

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")
