"""Resolving a path inside a client directory: the on-disk case wins.

The retail extraction disagrees with itself on case (`zones/Zone000` beside
`zones/ZONE008`, `figures/amazon.nif` where a golden says `Amazon.nif`), and
on Linux that difference is real. The game never sees it — Wine resolves
case-insensitively — so every consumer that looks a client file up by name
must resolve it the way the game does, not the way the caller spelled it.
"""
from __future__ import annotations

import os

__all__ = ["resolve"]


def resolve(client_dir: str, rel: str) -> str | None:
    """The on-disk path *rel* names, matched case-insensitively, or None."""
    current = client_dir
    for part in rel.split("/"):
        try:
            entries = os.listdir(current)
        except OSError:
            return None
        if part in entries:
            current = os.path.join(current, part)
            continue
        matches = [name for name in entries if name.casefold() == part.casefold()]
        if len(matches) != 1:
            return None
        current = os.path.join(current, matches[0])
    return current if os.path.exists(current) else None
