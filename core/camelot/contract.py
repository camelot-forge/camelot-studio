"""The server-facing content contract (Phase 2).

A *manifest* is the neutral description a server consumes: namespaced logical
ids, appearance and animation slots, the gamedata keys each one resolves
through, and the provenance needed to know which client build produced it.
It carries no client bytes — the server never parses NIF, KFA, MPAK or fig3,
it reads this contract and the glTF/JSON artifacts beside it.

Three rules shape the module:

- **Deterministic.** The same input serialises to the same bytes, so a pack
  can be checksummed, diffed and rebuilt reproducibly.
- **Server-neutral.** No DOLSharp or OpenDAoC type appears here; the target
  projections map these logical ids onto their own values.
- **Honest.** A reference the client tables did not resolve is recorded as
  :class:`Unresolved` and refuses publication rather than being guessed.

See ``docs/next-steps.md`` Phases 2-4.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field

__all__ = [
    "SCHEMA_VERSION",
    "AnimStateRef",
    "AppearanceSlot",
    "Figure",
    "Manifest",
    "Unresolved",
    "UnresolvedReferenceError",
    "ValidationReport",
    "artifact_hashes",
    "validate_logical_id",
]

# Bump on any change to the emitted shape; a server adapter pins it.
SCHEMA_VERSION = "1.0.0"

#: Logical ids are namespaced so two packs cannot collide and so a server can
#: tell a Studio id from one of its own.
_LOGICAL_ID = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9_-]*)+$")


class UnresolvedReferenceError(ValueError):
    """A manifest references something the client tables did not resolve."""


def validate_logical_id(logical_id: str) -> str:
    """Check a logical id is a namespaced lowercase path (`daoc.player.troll.male`)."""
    if not _LOGICAL_ID.match(logical_id):
        raise ValueError(
            f"logical id must be a namespaced lowercase path, got {logical_id!r}"
        )
    return logical_id


def artifact_hashes(files: dict[str, bytes]) -> dict[str, str]:
    """SHA-256 per artifact, keyed by sorted path so the map is stable."""
    return {name: hashlib.sha256(files[name]).hexdigest() for name in sorted(files)}


@dataclass
class Unresolved:
    """A reference the client could not resolve, kept instead of guessed."""

    ref: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AppearanceSlot:
    """One worn slot: the fig3 piece, its sheet and the engine's tint."""

    slot: str
    piece: str | None = None
    sheet: str | None = None
    #: Multiplied over the sheet by the viewer, as the client does.
    tint: tuple[int, int, int] | None = None
    #: Where this came from in the client tables, e.g. `pskins:301:Body:f`.
    gamedata_key: str | None = None
    #: `fig3parts` Double Sided flag, per piece.
    double_sided: bool | None = None

    def to_dict(self) -> dict:
        doc = asdict(self)
        if self.tint is not None:
            doc["tint"] = list(self.tint)
        return {k: v for k, v in doc.items() if v is not None}


@dataclass
class AnimStateRef:
    """One playable state, keyed by animnifs id rather than filename.

    A KFA basename is not unique (92 duplicated basenames on V2, 58 of them
    disagreeing on timing), so the id is the stable handle and the filename
    is only informational.
    """

    slot: str
    kfa: str
    animnifs_id: str
    loop: bool
    duration: float
    #: The `anims.csv` set that contributed the slot. A set is not a clean
    #: per-figure namespace: the Cata Troll male (set 263) borrows cat clips
    #: for slots like `Back`, and a target adapter needs to know which set a
    #: state came from to reason about the rig it was authored on.
    anim_set: str | None = None
    #: Game-fps over the clip's key density. 1.0 means "at the file's own
    #: key seconds", which is what a table-only build can honestly claim.
    rate: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Figure:
    """A figure the pack describes: a player row, a creature or an object."""

    logical_id: str
    #: The client's own row id (fig3 description id, monster id, object id).
    figure_id: int
    kind: str  # "player" | "creature" | "object"
    name: str | None = None
    female: bool | None = None
    rig: str | None = None
    #: The `anims.csv` set ids this figure's states were read from, in the
    #: order the client resolves them.
    anim_sets: list[str] = field(default_factory=list)
    appearance: list[AppearanceSlot] = field(default_factory=list)
    anim_states: list[AnimStateRef] = field(default_factory=list)
    #: Provenance: the packed archive the bytes came from and their hash.
    archive: str | None = None
    archive_sha256: str | None = None

    def to_dict(self) -> dict:
        return {
            "logical_id": validate_logical_id(self.logical_id),
            "figure_id": self.figure_id,
            "kind": self.kind,
            "name": self.name,
            "female": self.female,
            "rig": self.rig,
            "anim_sets": list(self.anim_sets),
            "appearance": [s.to_dict() for s in self.appearance],
            "anim_states": [s.to_dict() for s in self.anim_states],
            "archive": self.archive,
            "archive_sha256": self.archive_sha256,
        }


@dataclass
class ValidationReport:
    """The result of checking a manifest before it is published."""

    ok: bool
    unresolved: list[Unresolved] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Manifest:
    """The pack's contract: what it describes, and where it came from."""

    pack_id: str
    version: str
    #: The DAoC build the client corpus was read from; a pack is only valid
    #: for the build it was generated against.
    client_build: str | None = None
    client_profile: str | None = None
    figures: list[Figure] = field(default_factory=list)
    unresolved: list[Unresolved] = field(default_factory=list)
    #: SHA-256 of each generated artifact, filled by the pack builder.
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "pack": {"id": self.pack_id, "version": self.version},
            "client": {"build": self.client_build, "profile": self.client_profile},
            "figures": [f.to_dict() for f in self.figures],
            "artifacts": dict(sorted(self.artifacts.items())),
            # Publication status travels with the manifest: a server reading
            # the file can tell a validated pack from a dry run without
            # re-running the builder.
            "validation": self.validate(strict=False).to_dict(),
            "notes": list(self.notes),
        }

    def to_bytes(self) -> bytes:
        """Canonical JSON: sorted keys, stable separators, trailing newline."""
        return (
            json.dumps(self.to_dict(), sort_keys=True, indent=2, ensure_ascii=False)
            + "\n"
        ).encode("utf-8")

    def validate(self, strict: bool = True) -> ValidationReport:
        """Check the manifest is publishable.

        Every unresolved reference is a hard error under ``strict`` (the
        default): the pack builder must reject missing, ambiguous or
        unverified references rather than ship a guess. ``strict=False``
        reports without raising, for a dry run or a partial pack.
        """
        errors: list[str] = []
        for figure in self.figures:
            try:
                validate_logical_id(figure.logical_id)
            except ValueError as exc:
                errors.append(str(exc))
            if figure.rig is None:
                errors.append(f"{figure.logical_id}: no rig resolved")
        report = ValidationReport(
            ok=not errors and not self.unresolved,
            unresolved=list(self.unresolved),
            errors=errors,
        )
        if strict and not report.ok:
            detail = "; ".join(
                [f"{u.ref}: {u.reason}" for u in self.unresolved] + errors
            )
            raise UnresolvedReferenceError(f"manifest not publishable: {detail}")
        return report


# --- building a pack from a client corpus ----------------------------------

# The slots a wearable player figure must carry, and the appearance pickers
# the creation screen exposes (`fig3*colormap`).
_PLAYER_SLOTS = ("Head", "Eyes", "Hair", "Body", "Arms", "Gloves", "Lbody", "Legs", "Boots")


def client_build(client_dir: str) -> str | None:
    """The retail version of the corpus, read from `game.dll`.

    `game.dll` carries a `VS_VERSION_INFO` resource whose `FileVersion` is
    the client's own build number. A pack is only valid for the build it was
    generated against, so this travels in the manifest rather than being
    assumed by the consumer — and it is what a server adapter compares
    against its supported client.
    """
    path = os.path.join(client_dir, "game.dll")
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    marker = blob.find(b"V\x00S\x00_\x00V\x00E\x00R\x00S\x00I\x00O\x00N\x00_\x00I\x00N\x00F\x00O")
    if marker < 0:
        return None
    # The value strings follow their names, UTF-16LE, in declaration order.
    tail = blob[marker:marker + 900].decode("utf-16-le", "ignore")
    name = tail.find("FileVersion")
    if name < 0:
        return None
    rest = tail[name + len("FileVersion"):]
    digits = re.search(r"\d+(?:\.\d+)+", rest)
    return digits.group() if digits else None


def client_profile(client_dir: str) -> str:
    """Identify the client corpus a pack was built from."""
    version = client_build(client_dir)
    return f"{os.path.basename(os.path.normpath(client_dir))}@{version or 'unknown'}"


def _player_appearance(
    client_dir: str, race_id: int, female: bool, material: str | None
) -> tuple[list[AppearanceSlot], list[Unresolved]]:
    """The figure's appearance slots, resolved through the client's tables."""
    from . import appearance as app
    from . import fig3, playerfig

    unresolved: list[Unresolved] = []
    tables = fig3.read_tables(client_dir)
    # The stem already carries the gender (`tro_m`, `bri_f`), so the id does
    # not repeat it: `daoc.player.tro_m`.
    stem = fig3.race_short(tables, race_id, female) or str(race_id)
    logical = f"daoc.player.{stem}"

    def sheet(slot: str, target, tint=None, double_sided=None, key=None) -> None:
        if target is None:
            unresolved.append(Unresolved(ref=f"{logical}:{slot}", reason="no sheet"))
            return
        name = target[0] if isinstance(target, tuple) else target
        slots.append(AppearanceSlot(
            slot=slot, sheet=name, tint=tint, gamedata_key=key,
            double_sided=double_sided,
        ))

    slots: list[AppearanceSlot] = []
    hair_part = fig3.part_at(tables, race_id, "Hair", 1)
    hair = app._hair_triplets(client_dir, race_id, 1)
    hair_sheet = None
    if hair:
        cell = hair[0] if hair[0][0] else next((t for t in hair if t[0]), None)
        if cell is not None:
            hair_sheet = app.hair_composite(client_dir, cell[0], cell[2]) or cell[0]
    sheet("Head", app.face_sheet(client_dir, race_id, 1),
          tint=app.skin_tint_rgb(client_dir, race_id, 1),
          key=f"fig3facemap:{race_id}:1")
    sheet("Eyes", app.eye_composite(client_dir, race_id, 1, 1),
          key=f"fig3eyecolormap:{race_id}:1")
    sheet("Hair", hair_sheet,
          tint=app.hair_tint_rgb(client_dir, race_id, 1, 1),
          double_sided=hair_part.double_sided if hair_part else None,
          key=f"fig3haircolormap:{race_id}:1:1")
    for slot, name in playerfig.armour_sheets(client_dir, female, material) or ():
        sheet(slot, name, key=f"pskins:{material or playerfig.DEFAULT_MATERIAL}:{slot}")
    missing = [s for s in _PLAYER_SLOTS if s not in {x.slot for x in slots}]
    for slot in missing:
        unresolved.append(Unresolved(
            ref=f"{logical}:{slot}", reason="no piece for this race",
        ))
    return slots, unresolved


def build_player_manifest(
    client_dir: str, *, pack_id: str, version: str,
    material: str | None = None, with_states: bool = True,
) -> Manifest:
    """Read the client corpus and describe every playable figure.

    Produces the canonical, server-neutral manifest: the 18 playable races x
    2 genders with their rig, appearance slots (sheets + engine tints +
    gamedata keys) and their anim set. Unresolved references are recorded,
    never guessed — :meth:`Manifest.validate` then refuses the pack.
    """
    from . import anims, fig3, playerfig

    manifest = Manifest(
        pack_id=pack_id,
        version=version,
        client_build=client_build(client_dir),
        client_profile=client_profile(client_dir),
    )
    tables = fig3.read_tables(client_dir)
    for race_id in sorted(tables.race_desc):
        desc = tables.race_desc[race_id]
        female = "female" in desc.lower()
        if not playerfig.is_playable(race_id):
            continue
        # The stem already carries the gender (`tro_m`, `bri_f`), so the id
        # does not repeat it: `daoc.player.tro_m`.
        style = fig3.race_short(tables, race_id, female) or desc
        logical = f"daoc.player.{style.lower()}"
        appearance, unresolved = _player_appearance(
            client_dir, race_id, female, material
        )
        states: list[AnimStateRef] = []
        anim_sets: list[str] = []
        if with_states:
            figure_path = os.path.join(client_dir, "figures", style + ".NIF")
            anim_sets = anims._anim_set_ids(client_dir, figure_path)
            for state in anims.anim_states(client_dir, figure_path):
                # rate is left at 1.0 (raw key seconds): computing it means
                # opening each of the set's 166 clips, which no offline
                # build needs and the viewer corrects anyway from the clip
                # it actually plays.
                states.append(AnimStateRef(
                    slot=state.slot,
                    kfa=state.kfa,
                    animnifs_id=state.animnifs_id,
                    loop=state.loop,
                    duration=round(state.duration, 4),
                    anim_set=anim_sets[0] if anim_sets else None,
                ))
            if not states:
                unresolved.append(Unresolved(
                    ref=f"{logical}:anim", reason="no anim set resolved",
                ))
        if playerfig.rig_bytes(client_dir, race_id, female) is None:
            unresolved.append(Unresolved(
                ref=f"{logical}:rig", reason="no fig3 runtime skeleton",
            ))
        manifest.figures.append(Figure(
            logical_id=logical,
            figure_id=race_id,
            kind="player",
            name=desc,
            female=female,
            rig=style,
            anim_sets=anim_sets,
            appearance=appearance,
            anim_states=states,
        ))
        manifest.unresolved.extend(unresolved)
    return manifest
