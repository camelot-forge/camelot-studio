"""Where this machine keeps its own files — never a project's, never a repo's.

Two directories, two questions:

- the **config** one holds what the operator keeps *across* projects — the list
  of recent projects, and whatever else turns out to be machine-level;
- the **cache** one holds what can be thrown away and recomputed (asset
  exports, the index cache).

Both follow the OS conventions, and neither ever lives inside a repository:
the same rule `project.md` §D4 states for `machine.json`, applied to the
machine itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "camelot-studio"


def config_dir(app: str = APP) -> Path:
    """Per-user configuration: it survives a cache purge, so it is not the
    cache directory. An explicit XDG_CONFIG_HOME wins on every platform."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        base = Path(xdg)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / app


def cache_dir(app: str = APP) -> Path:
    """Per-user cache: safe to delete, recomputed on demand. An explicit
    XDG_CACHE_HOME wins on every platform."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        base = Path(xdg)
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path.home() / ".cache"
    return base / app
