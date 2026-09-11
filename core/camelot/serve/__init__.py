"""``camelot serve`` — local daemon exposing the DAoC client as a library.

The daemon is a thin facade over the format layer (``mpak``, ``gamedata``,
``gltf``): it indexes the client directory and serves the glTF/JSON artifacts
on a loopback-only HTTP API, together with the built SvelteKit viewer. All
binary format knowledge stays in ``core``; this package only orchestrates.
"""
from __future__ import annotations

from .config import ServeConfig, default_cache_dir

__all__ = ["ServeConfig", "create_app", "default_cache_dir"]


def create_app(*args, **kwargs):
    """Lazy re-export of :func:`camelot.serve.app.create_app`."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
