"""Target projections: the neutral contract mapped onto one server.

A projection is a thin translation layer. It knows the target's vocabulary
-- its race numbering, its bit-packed model word -- and nothing else. fig3,
KFA and gamedata semantics stay in `camelot`, so a second target never
reimplements client behaviour.

DOLSharp and OpenDAoC share the DOL lineage, but they are separate targets:
each ships its own tables and its own vocabulary. Add a module per target
rather than branching inside a shared one.
"""
from __future__ import annotations

__all__ = ["opendaoc"]
