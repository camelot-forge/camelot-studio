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

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.cache_dir = Path(self.cache_dir).expanduser()

    @property
    def is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")
