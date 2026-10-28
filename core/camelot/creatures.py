"""The client's creature models, resolved through the server's two-level chain.

The server sends `npctemplate.Model`; the client resolves it in two steps
(measured in game):

    monsters.csv[Model]   its `NIF` field is a `monnifs` id
    monnifs.csv[that id]  its `NIF` field is the figure, `Anim` the anim set

An earlier version of this module read `monnifs` directly and treated its row
id as the model id the server sends. That is one level off: `/mob fastcreate
88` renders the Avalonian female `monsters[88]` resolves to, not the minotaur
`monnifs[88]` names.

Nothing here is invented -- a row we cannot resolve is skipped, not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

from .anims import _cached_table

__all__ = ["Creature", "read_creatures"]

# Column indices, two header rows per client CSV.
# monsters.csv: ID, name, NIF (a monnifs id)
_MODEL_ID = 0
_MODEL_NAME = 1
_MODEL_NIF = 2
# monnifs.csv: ID, name, NIF (the figure name), Anim (the set)
_MONNIFS_ID = 0
_MONNIFS_NIF = 2
_MONNIFS_SET = 3


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
    """Every resolvable `monsters` row, ordered by model id.

    Ordered by id, and not by file order, because the caller assigns
    template ids from this list: a pack whose ids shift between builds
    would redeploy content onto different creatures.

    A repeated model id keeps its first row (the table ships one, id 2375),
    so a caller's logical ids stay unique.
    """
    monnifs_by_id: dict[int, list[str]] = {}
    for row in _cached_table(client_dir, "monnifs.csv")[2:]:
        if len(row) <= _MONNIFS_SET:
            continue
        raw_id = row[_MONNIFS_ID].strip()
        if raw_id.isdigit() and int(raw_id) not in monnifs_by_id:
            monnifs_by_id[int(raw_id)] = row

    out: list[Creature] = []
    seen: set[int] = set()
    for row in _cached_table(client_dir, "monsters.csv")[2:]:
        if len(row) <= _MODEL_NIF:
            continue
        raw_id, name, raw_nif = (cell.strip() for cell in row[: _MODEL_NIF + 1])
        if not raw_id.isdigit() or not name or not raw_nif.isdigit():
            continue
        model_id = int(raw_id)
        source = monnifs_by_id.get(int(raw_nif))
        if source is None or model_id in seen:
            continue
        nif = source[_MONNIFS_NIF].strip()
        if not nif:
            continue
        raw_set = source[_MONNIFS_SET].strip()
        out.append(Creature(
            model_id=model_id,
            name=name,
            nif=nif.lower(),
            anim_set=int(raw_set) if raw_set.isdigit() else None,
        ))
        seen.add(model_id)
    out.sort(key=lambda c: c.model_id)
    return out
