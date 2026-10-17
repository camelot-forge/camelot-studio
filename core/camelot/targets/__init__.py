"""Target projections: the neutral contract mapped onto one server.

A projection is a thin translation layer. It knows the target's vocabulary
-- its race numbering, its bit-packed model word -- and nothing else. fig3,
KFA and gamedata semantics stay in `camelot`, so a second target never
reimplements client behaviour.

OpenDAoC is the target. A projection knows that server's vocabulary -- its
race numbering, its bit-packed model word -- and nothing else. The layout
keeps one module per target so a future server would be an addition rather
than a branch inside this one, but OpenDAoC is the only one there is.
"""
from __future__ import annotations

from . import opendaoc
from .opendaoc import OWNED_TABLES, owned, owned_key

__all__ = ["OWNED_TABLES", "TARGETS", "opendaoc", "owned", "owned_key"]

#: Registered projections, by the name a pack writes under `targets/`.
TARGETS = {"opendaoc": opendaoc}
