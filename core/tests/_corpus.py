"""Corpus path resolution: the on-disk case wins.

The corpus the goldens were captured on was assembled on a case-insensitive
filesystem; a Linux extraction is not (`figures/Amazon.nif` vs `amazon.nif`,
`BCommonF.NIF` vs `bcommonf.NIF`). The game itself never sees the difference —
Wine resolves case-insensitively — and every consumer in `core/` resolves a
client path the same way (`clientinstall.resolve_existing`). Corpus tests must
too, or they fail on a case difference that is not a content difference.

`resolve` returns the path the caller asked for when nothing matches, so the
caller's own FileNotFoundError still names the file it wanted.
"""
from __future__ import annotations

import os


def resolve(client_dir: str, rel: str) -> str:
    """The on-disk path *rel* names, matched case-insensitively.

    The rule itself lives in `camelot.clientfs` (the same one the installer
    uses); the fallback here still names the path the caller asked for.
    """
    from camelot import clientfs

    return clientfs.resolve(client_dir, rel) or os.path.join(client_dir, *rel.split("/"))


def figure_path(client_dir: str, name: str) -> str:
    """`figures/<name>` with the case the corpus actually uses."""
    return resolve(client_dir, f"figures/{name}")
