"""Native fig3 player figures — the "character builder" assembly (v2).

Doctrine (notes/character-rules.md §8.15/§9/§11, all evidence-backed):

- **The rig is the client's runtime skeleton**,
  ``<code>_<gender>_skeleton.nif`` from ``figures/fig3/sfig%03d.mpk``
  (game.dll ``FUN_0047529c``). Pieces are attached onto that tree with a
  bind-identical compensation (they were authored on the same rig).
- The head slot's sheet comes from ``fig3facemap.csv`` (faces 1..7), the
  hair pieces from the per-race ``Hair`` list (``fig3map.csv`` slot 8),
  the hair look from ``fig3haircolormap.csv`` (16 S/T/M triplets per
  race+style), the eyes from ``fig3eyecolormap.csv`` and the skin tone
  from ``fig3skincolormap.csv`` (tints).
- Armor sheets default to the client pskin families the reference viewer
  bakes (``cthbody01_01_m`` & co), all of which exist in the client
  (validation group 7).
"""
from __future__ import annotations

import io
import os

from . import appearance as app
from . import fig3, figure, gamedata
from .mpak import MpakArchive

# Default pskin sheet family per body slot — matches the DAoC-Tools-baked
# default look (all proven to exist in the client, validation group 7).
# Lbody (Highlander/Norse sets) shares the legs atlas: its UVs cover the
# upper half of the same sheet the legs piece samples.
_SLOT_SHEETS = {
    "Body": "cthbody01_01",
    "Lbody": "lealegs01_01",
    "Legs": "lealegs01_01",
    "Boots": "leaboots01_01",
    "Arms": "ctharms01_01",
    "Gloves": "leagloves01_01",
}

_RIG_PATH_CACHE: dict[tuple[str, str], str | None] = {}


def rig_for(client_dir: str, race_id: int, female: bool):
    """The runtime rig of (race, gender) — a *fresh* NIF each call.

    The archives are cached as paths (each export must re-load the tree:
    attaching mutates it, two previews sharing one object would stack
    meshes on top of each other)."""
    code = fig3.race_short(fig3.read_tables(client_dir), race_id, female)
    if code is None:
        return None
    key = (client_dir, code)
    if key not in _RIG_PATH_CACHE:
        _RIG_PATH_CACHE[key] = _find_rig_path(client_dir, code)
    path = _RIG_PATH_CACHE[key]
    if path is None:
        return None
    entry = f"{code}_skeleton.nif"
    from pyffi.formats.nif import NifFormat

    arch = MpakArchive(path)
    try:
        raw = arch.read(entry)
    finally:
        arch.close()
    data = NifFormat.Data()
    data.read(io.BytesIO(raw))
    return data


def _find_rig_path(client_dir: str, code: str) -> str | None:
    fig_dir = os.path.join(client_dir, "figures", "fig3")
    entry = f"{code}_skeleton.nif"
    for fname in sorted(os.listdir(fig_dir)):
        if not fname.startswith("sfig"):
            continue
        path = os.path.join(fig_dir, fname)
        arch = MpakArchive(path)
        try:
            if entry in arch.entries:
                return path
        finally:
            arch.close()
    return None


def pieces_for(client_dir: str, tables, race_id: int, *, face: int = 1,
               hair_style: int = 1, cloak: int = 0):
    """Slot → part of this appearance (cloak variant only when asked)."""
    out: dict[str, fig3.Part] = {}
    # fig3map slot spelling -> exported slot name (`Lbody` matches the
    # classic mesh names and the glTF slot vocabulary).
    for slot, name in (("Body", "Body"), ("LBody", "Lbody"), ("Legs", "Legs"),
                       ("Boots", "Boots"), ("Arms", "Arms"), ("Gloves", "Gloves")):
        parts = fig3.parts_of(tables, race_id, slot)
        if parts:
            out[name] = parts[0]
    hair = fig3.parts_of(tables, race_id, "Hair")
    if hair:
        out["Hair"] = hair[max(0, min(hair_style - 1, len(hair) - 1))]
    head = fig3.parts_of(tables, race_id, "Head")
    if head:
        out["Head"] = head[0]
    cloaks = fig3.parts_of(tables, race_id, "Cloak")
    if cloaks and cloak > 0:
        out["Cloak"] = cloaks[min(cloak - 1, len(cloaks) - 1)]
    return out


def textures_for(client_dir: str, tables, race_id: int, female: bool,
                 *, face: int = 1, hair_style_key: int = 1,
                 hair_color: int = 1, eye_color: int = 1, skin_tone: int = 1):
    """`SlotTexture`s for the picked appearance (sheets + engine tints).

    The skin tone and most hair colours are tints (`fig3tints.csv`): the
    viewer multiplies the sheet by the RGB the engines packs.
    """
    out: list[gamedata.SlotTexture] = []

    def add(slot: str, target: tuple[str, bytes] | None, tint=None):
        if target is None:
            return
        name, raw = target
        if raw is not None:
            out.append(gamedata.SlotTexture(slot, name, raw, tint))

    add("Head", app.face_sheet(client_dir, race_id, face),
        tint=app.skin_tint_rgb(client_dir, race_id, skin_tone))
    add("Eyes", app.eye_sheet(client_dir, race_id, eye_color))
    triplets = app._hair_triplets(client_dir, race_id, style=hair_style_key)
    if triplets:
        # fig3 haircolormap row = 16 colour slots; empty S cells carry no
        # sheet for that colour (engine picks tint-only blacks). Our visible
        # default stays the nearest non-empty cell (like DT's fallback).
        chosen = triplets[min(max(hair_color, 1), len(triplets)) - 1]
        cell = chosen if chosen[0] else next(
            (t for t in triplets if t[0]), None)

        add("Hair", gamedata.skin_texture(client_dir, cell[0]),
            tint=app.hair_tint_rgb(client_dir, race_id, hair_style_key, hair_color))
    for slot, stem in _SLOT_SHEETS.items():
        gendered = client_sheet(client_dir, f"{stem}_f.dds" if female
                                else f"{stem}_m.dds")
        plain = client_sheet(client_dir, f"{stem}.dds")
        found = gendered or plain
        if found:
            out.append(gamedata.SlotTexture(slot, found[0], found[1]))
    return out


def client_sheet(client_dir, name):
    raw = gamedata.texture_bytes(client_dir, name)
    return (name, raw) if raw else None


def build(client_dir: str, desc: str, *, face: int = 1, hair_style: int = 1,
          hair_color: int = 1, eye_color: int = 1, skin_tone: int = 1,
          cloak: int = 0):
    """Assemble a whole player figure for (race, gender, appearance).

    Returns ``(data, [(slot, name, dds), ...])`` or None when the race has
    no fig3 coverage."""
    tables = fig3.read_tables(client_dir)
    rid = fig3.race_id(tables, desc)
    if rid is None:
        return None
    female = "female" in desc.lower()
    data = rig_for(client_dir, rid, female)
    if data is None:
        return None
    pieces = pieces_for(client_dir, tables, rid, face=face,
                        hair_style=hair_style, cloak=cloak)
    textures = textures_for(client_dir, tables, rid, female,
                            face=face, hair_style_key=hair_style,
                            hair_color=hair_color, eye_color=eye_color,
                            skin_tone=skin_tone)
    attached = 0
    for slot, part in pieces.items():
        piece = fig3.load_part(client_dir, part)
        if piece is None:
            continue
        if slot == "Head":
            # Export under the head slot with its own sheets; the eyes come
            # from the same mesh (split like engine runtime splits their
            # decal pass); bone-viz boxes never survive.
            head_shape = next(
                (s for s in fig3.shapes_of(piece) if getattr(s, 'skin_instance', None)), None)
            if head_shape is None:
                continue
            head_shape.name = b"Head"
            figure._hide_shapes(data, [s for s in fig3.shapes_of(piece) if (s.name or b"").lower().startswith(b"box")])
            eye_set = figure.eye_vertices(head_shape)
            if not fig3.attach_part(data, piece, slot, reference=data):
                continue
            figure._split_eyes(data, head_shape, eye_set)
            attached += 1
            continue
        if fig3.attach_part(data, piece, slot, reference=data):
            attached += 1
    if attached == 0:
        return None
    return data, textures
