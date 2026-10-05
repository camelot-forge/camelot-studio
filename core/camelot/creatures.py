"""The client's creature models, read from its own `monnifs` table.

This is the same table the animation links already resolve through, read
for a different column: the row's id IS the model id the server sends in
`npctemplate.Model`, so the client resolves a creature without any new
mapping. Nothing here is invented -- a row we cannot read is skipped, not
guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .anims import _cached_table

__all__ = ["Creature", "read_creatures"]

# Column indices of `monnifs.csv` (two header rows, as every client CSV).
_ID = 0
_NAME = 1
_NIF = 2
_SET = 3


@dataclass(frozen=True)
class Creature:
    """One creature model: the id the server sends, its rig and anim set."""

    model_id: int
    name: str
    nif: str
    anim_set: int | None

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "name": self.name,
            "nif": self.nif,
            "anim_set": self.anim_set,
        }


def read_creatures(client_dir: str) -> list[Creature]:
    """Every usable `monnifs` row, ordered by model id.

    Ordered by id, and not by file order, because the caller assigns
    template ids from this list: a pack whose ids shift between builds
    would redeploy content onto different creatures.
    """
    out: list[Creature] = []
    for row in _cached_table(client_dir, "monnifs.csv")[2:]:
        if len(row) <= _SET:
            continue
        raw_id, name, nif = (cell.strip() for cell in row[:_SET])
        if not raw_id.isdigit() or not name or not nif:
            continue
        raw_set = row[_SET].strip()
        out.append(Creature(
            model_id=int(raw_id),
            name=name,
            nif=nif.lower(),
            anim_set=int(raw_set) if raw_set.isdigit() else None,
        ))
    out.sort(key=lambda c: c.model_id)
    return out
