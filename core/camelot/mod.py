"""The mod deployment document a pack carries.

A pack does not ship SQL, and it does not choose ids. It declares how many
ids it needs per table, and describes its rows as columns and values with a
logical name for each row that other rows may reference.

Two consequences, and they are the reason for the shape:

- **overlap is impossible.** No mod picks an id, so two mods cannot claim
  the same one and there is no conflict policy to design;
- **a mod cannot execute arbitrary statements** against a server's database,
  which matters when the mods come from strangers.

The client section is declared empty: Phase 5 fills it, and a field declared
and unused costs nothing where adding one later would break the schema.
"""
from __future__ import annotations

import json

__all__ = ["DEPLOYMENT_FILE", "deployment", "deployment_bytes"]

#: Where the document lives inside a pack, beside the target projection.
DEPLOYMENT_FILE = "targets/opendaoc/mod.json"


def deployment(mod_id: str, version: str, rows, *, priority: int = 100) -> dict:
    """The deployment document for a set of declarative rows.

    `rows` are mappings with a `table`, a logical `id` and a `values`
    mapping. The allocations are counted from them rather than passed in, so
    a declared count can never disagree with what is actually written.
    """
    cleaned: list[dict] = []
    counts: dict[str, int] = {}
    for row in rows:
        table = row.get("table")
        logical_id = row.get("id")
        if not table:
            raise ValueError("a row must name the table it belongs to")
        if not logical_id:
            raise ValueError("a row must have a logical id")
        cleaned.append({
            "table": table,
            "id": logical_id,
            "values": dict(row.get("values") or {}),
        })
        counts[table] = counts.get(table, 0) + 1

    return {
        "mod": {"id": mod_id, "version": version, "priority": priority},
        "requires": [],
        "allocations": [
            {"table": table, "count": counts[table]} for table in sorted(counts)
        ],
        "rows": cleaned,
        "client": [],
    }


def deployment_bytes(doc: dict) -> bytes:
    """Canonical JSON, so two builds of one corpus produce the same bytes."""
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
