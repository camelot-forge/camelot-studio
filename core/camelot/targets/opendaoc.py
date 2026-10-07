"""OpenDAoC projection.

Everything here comes from the server's own source and tables, not from
guesswork:

- ``RACE_IDS`` is the ``race`` table read from the OpenDAoC database: the
  18 playable races numbered 1..18, the three Minotaurs 19..21 (not
  playable, so not projected).
- ``SERVER_RACE_NAMES`` normalises the two races the client spells
  differently from the server: ``fig3descriptions.csv`` says ``Norse`` and
  ``Half Ogre`` where the server's table says ``Norseman`` and ``HalfOgre``.
  Matching on the client's spelling alone would silently drop two of the
  eighteen races.
- The wire format is the one ``CharacterCreateRequestHandler`` reads:
  ``Race = byte & 0x1F``, ``Gender = (byte >> 7) & 1``, then
  ``CreationModel = packet.ReadShortLowEndian()``.
- ``CreationModel`` is a **bitfield, not a plain id**: the handler reads the
  body size as ``(CreationModel >> 11) & 3`` and autobans a player whose
  size is zero.
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_BODY_SIZE",
    "DEFAULT_LEVEL_RANGE",
    "DEFAULT_MODEL_FIELDS",
    "DEFAULT_SIZE_RANGE",
    "NPC_CLASS",
    "RACE_IDS",
    "SERVER_RACE_NAMES",
    "OpendaocFigure",
    "RaceTableReport",
    "creature_row",
    "pack_model",
    "pack_race_gender",
    "parse_race_sql",
    "project",
    "server_race_name",
    "unpack_gender",
    "unpack_model_size",
    "unpack_race",
    "verify_race_table",
]

#: The server's `race` table (read from the OpenDAoC database). 1..18 are
#: the playable races; 19..21 are Minotaurs, which no player figure maps to.
RACE_IDS = {
    "briton": 1, "avalonian": 2, "highlander": 3, "saracen": 4,
    "norseman": 5, "troll": 6, "dwarf": 7, "kobold": 8, "celt": 9,
    "firbolg": 10, "elf": 11, "lurikeen": 12, "inconnu": 13,
    "valkyn": 14, "sylvan": 15, "halforge": 16, "frostalf": 17,
    "shar": 18,
}

#: How the server's `race` table spells each race, keyed by the server name.
SERVER_RACE_NAMES = {
    "briton": "Briton", "avalonian": "Avalonian", "highlander": "Highlander",
    "saracen": "Saracen", "norseman": "Norseman", "troll": "Troll",
    "dwarf": "Dwarf", "kobold": "Kobold", "celt": "Celt",
    "firbolg": "Firbolg", "elf": "Elf", "lurikeen": "Lurikeen",
    "inconnu": "Inconnu", "valkyn": "Valkyn", "sylvan": "Sylvan",
    "halforge": "HalfOgre", "frostalf": "Frostalf", "shar": "Shar",
}

#: Client spellings that differ from the server's. `fig3descriptions.csv`
#: says "Norse" and "Half Ogre"; the server says "Norseman" and "HalfOgre".
CLIENT_SPELLING_ALIASES = {
    "norse": "Norseman",
    "norseman": "Norseman",
    "half ogre": "HalfOgre",
    "halfogre": "HalfOgre",
    "halforge": "HalfOgre",
}

#: Model sub-fields other than size. The client sends them per character;
#: the projection carries neutral defaults because the contract does not
#: describe them yet (see `next-steps.md`, extension and dye).
DEFAULT_MODEL_FIELDS = {
    "skin": -1,
    "hair": -1,
    "face": -1,
    "hair_color": -1,
    "eye_color": -1,
}

#: A player's body size, from the client's creation screen (1..3). Not to
#: be confused with `DEFAULT_SIZE_RANGE`: a player carries a number in the
#: model bitfield, a spawn carries a range in its own text column.
DEFAULT_BODY_SIZE = 1

# Bit layout of the model word, from the server's decoder.
_SIZE_SHIFT = 11
_SIZE_MASK = 0b11
_SKIN_MASK = 0b111_1111_11  # bits 2..10
_HAIR_MASK = 0b11          # bits 0..1


def _race_key(name: str) -> str:
    """Normalise a race name from either side to the server's key."""
    cleaned = (name or "").strip()
    if cleaned.lower() in SERVER_RACE_NAMES:
        return cleaned.lower()
    if cleaned.lower() in CLIENT_SPELLING_ALIASES:
        return SERVER_RACE_NAMES_INDEX[CLIENT_SPELLING_ALIASES[cleaned.lower()].lower()]
    raise KeyError(
        f"race {name!r} is not in the OpenDAoC race table; "
        f"known: {sorted(SERVER_RACE_NAMES)}"
    )


#: Server name -> key, so an alias can hop to it in one step.
SERVER_RACE_NAMES_INDEX = {v.lower(): k for k, v in SERVER_RACE_NAMES.items()}


def server_race_name(name: str) -> str:
    """The server's own spelling of a race, from the client's if needed."""
    return SERVER_RACE_NAMES[_race_key(name)]


def pack_race_gender(race: int, female: bool) -> int:
    """The byte the client sends: race in the low five bits, gender in bit 7.

    ``CharacterCreateRequestHandler`` reads ``Race = byte & 0x1F`` and
    ``Gender = (byte >> 7) & 1``, so a race above 31 cannot be expressed
    and is refused rather than truncated.
    """
    if not 0 <= race <= 0x1F:
        raise ValueError(f"race {race} does not fit in the five wire bits")
    return (race & 0x1F) | (0x80 if female else 0)


def unpack_race(byte: int) -> int:
    return byte & 0x1F


def unpack_gender(byte: int) -> bool:
    return bool((byte >> 7) & 0x01)


def pack_model(size: int = DEFAULT_BODY_SIZE, **fields) -> int:
    """The 16-bit model word, packed the way the client's creation packet is.

    The size lives in bits 11-12 because the server reads it as
    ``(CreationModel >> 11) & 3`` and **autobans** a size of zero, so a zero
    size is refused here rather than shipped.
    """
    if not 1 <= size <= 3:
        raise ValueError(f"body size {size} would be autobanned by the server")
    word = (size & _SIZE_MASK) << _SIZE_SHIFT
    skin = int(fields.get("skin", DEFAULT_MODEL_FIELDS["skin"]))
    hair = int(fields.get("hair", DEFAULT_MODEL_FIELDS["hair"]))
    if skin >= 0:
        word |= (skin & _SKIN_MASK) << 2
    if hair >= 0:
        word |= hair & _HAIR_MASK
    return word & 0xFFFF


def unpack_model_size(word: int) -> int:
    return (word >> _SIZE_SHIFT) & _SIZE_MASK


@dataclass
class OpendaocFigure:
    """One contract figure, expressed in the server's vocabulary."""

    logical_id: str
    race: str
    race_id: int
    gender: int          # 0 = male, 1 = female, as the server stores it
    race_gender: int     # the packed wire byte
    model: int           # the packed model word
    rig: str | None = None

    def to_dict(self) -> dict:
        return {
            "logical_id": self.logical_id,
            "race": self.race,
            "race_id": self.race_id,
            "gender": self.gender,
            "race_gender": self.race_gender,
            "model": self.model,
            "rig": self.rig,
        }


@dataclass
class RaceTableReport:
    """What a server's own `race` table says about our projection."""

    ok: bool
    missing: list[str] = field(default_factory=list)
    mismatched: list[tuple] = field(default_factory=list)
    server_names: dict = field(default_factory=dict)


def parse_race_sql(path: str) -> dict[int, str]:
    """Read `(id, 'Name')` rows out of the server's `race.sql` dump.

    Parsing the dump rather than connecting means the check runs offline
    and in CI, against the same bytes a deployment would import.
    """
    import re

    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    rows = re.findall(r"\(\s*(\d+)\s*,\s*'([^']*)'", text)
    return {int(i): name for i, name in rows}


def verify_race_table(keys, race_sql_path: str) -> RaceTableReport:
    """Check our projected `race` ids against a server's own table.

    Three ways a projection can be wrong, all of them silent in game: a race
    the server does not have, a race on a different id, and a name the
    server spells differently. All three are reported rather than the first.
    """
    server = parse_race_sql(race_sql_path)
    by_name = {name.lower(): rid for rid, name in server.items()}
    missing: list[str] = []
    mismatched: list[tuple] = []
    for key in keys:
        if key not in SERVER_RACE_NAMES or key not in RACE_IDS:
            # Either we do not project it or we have no id for it: both mean
            # the claim cannot be checked, which is a failure to report, not
            # an exception to raise.
            missing.append(key)
            continue
        name = SERVER_RACE_NAMES[key].lower()
        if name not in by_name:
            missing.append(key)
        elif by_name[name] != RACE_IDS[key]:
            mismatched.append((key, RACE_IDS[key], by_name[name]))
    return RaceTableReport(
        ok=not missing and not mismatched,
        missing=missing,
        mismatched=mismatched,
        server_names={name: rid for rid, name in server.items()},
    )


def project(figure) -> OpendaocFigure:
    """Map a contract :class:`~camelot.contract.Figure` onto the server.

    The figure's name is the client's spelling ("Norse Male"), so the race
    is taken from it after normalisation. An unknown race raises rather
    than defaulting: a projection that guessed would create characters on
    the wrong race, and the failure would only show up in-game.
    """
    if figure.kind != "player":
        raise KeyError(f"{figure.logical_id}: only player figures project")
    name = figure.name or ""
    # "Troll Male" / "Half Ogre Female" -> the race part
    parts = name.split()
    race_part = " ".join(parts[:-1]) if len(parts) > 1 else parts[0]
    race = server_race_name(race_part)
    race_id = RACE_IDS[_race_key(race_part)]
    female = bool(figure.female)
    return OpendaocFigure(
        logical_id=figure.logical_id,
        race=race,
        race_id=race_id,
        gender=1 if female else 0,
        race_gender=pack_race_gender(race_id, female),
        model=pack_model(size=DEFAULT_BODY_SIZE, **DEFAULT_MODEL_FIELDS),
        rig=figure.rig,
    )

# --- creatures: how a creature is expressed --------------------------------

#: The class the server instantiates for a plain creature. The server's own
#: `npctemplate` rows use `DOL.GS.GameNPC` for anything without a special AI.
NPC_CLASS = "DOL.GS.GameNPC"

#: How the server writes a spawn's size and level. Its own rows use ranges
#: (`'50-58'`), and a single number where a range belongs still imports --
#: but silently pins every spawn to one value.
DEFAULT_SIZE_RANGE = "50-58"
DEFAULT_LEVEL_RANGE = "1-5"

# --- declarative rows for the mod deployment --------------------------------

#: How a creature's logical id is spelled. Keyed on the model id, because
#: that is the corpus's own unique key for a creature: several creatures
#: share a rig (`mino` is both the Stone and the Shade Minotaur), so the NIF
#: stem is not an identity. The manager allocates the numeric row id; this
#: only has to be unique within the mod.
_ROW_ID_PREFIX = "spawn"


def creature_row(
    *, model_id: int, name: str, nif: str | None = None,
    anim_set: int | None = None,
) -> dict:
    """One creature as a declarative row, with no id of its own.

    `Model` is a client model id from `monsters` (the table the server's
    ids index; its `NIF` field resolves through `monnifs`), existing game
    data rather than something the mod introduces, so it stays literal. The
    row's own key column is deliberately absent: the manager fills it.
    """
    if model_id <= 0:
        raise ValueError(f"model id {model_id} must be positive")
    values = {
        "Name": name,
        "ClassType": NPC_CLASS,
        "Model": model_id,
        "Size": DEFAULT_SIZE_RANGE,
        "Level": DEFAULT_LEVEL_RANGE,
    }
    return {
        "table": "npctemplate",
        "id": f"{_ROW_ID_PREFIX}:{model_id}",
        "values": values,
    }
