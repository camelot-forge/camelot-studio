"""Player figures: fig3 head/hair on top of the classic base body.

Classic base figures (``figures/B*.NIF``) ship two stacked head shells per
variant (``HeadA*`` face + ``HeadB*`` hair). They were authored for the
engine's decal/multitexture hair shader: a single-texture render shows the
hair sheet as an opaque blob over the face (docs/formats.md §4). The live
client and the game-accurate viewers do not use them any more — they assemble
the Catacombs fig3 pieces instead (:mod:`camelot.fig3`).

This module applies the same substitution to classic player figures: hide the
``HeadA*/HeadB*`` shells, attach ``sar_f_Head01`` and ``sar_f_Hair01`` from
the client archives to the figure's own skeleton (bind compensation in
:func:`camelot.fig3.attach_part`) and expose the default face/hair sheets as
the ``Head`` and ``Hair`` texture slots. Everything else (body, equipment
variants, textures) stays classic.
"""
from __future__ import annotations

import os

from . import fig3, gamedata
from .fig3 import SLOT_NAMES  # noqa: F401 — re-exported for callers/tests

# Experiment hook: which bone anchors the fig3 head onto the classic rig.
# "none" keeps the piece at its authored Catacombs position, "head" aligns
# Bip01 Head, "neck" aligns Bip01 Neck, "classic" keeps the classic heads.
_ANCHOR = {"none": None, "head": "Bip01 Head", "neck": "Bip01 Neck", "classic": "Bip01 Head"}[
    os.environ.get("CAMELOT_HEAD_ANCHOR", "neck")
]

# Defaults until the appearance picker lands (face 1 / hair style 1).
DEFAULT_FACE_INDEX = 1
DEFAULT_HAIR_STYLE = 1

# Hair colour variants shipped with the styles (naming varies by race:
# `_dkbrown` for most, `_darkbrown` for some Inconnu sheets).
_HAIR_FALLBACKS = ("_dkbrown", "_darkbrown", "_blonde", "_white", "_bluebase", "_brown")

_CLASSIC_HEAD_PREFIXES = ("heada", "headb")

# Classic slot meshes replaced by fig3 pieces (prefix, lowercase). The classic
# low-poly bodies are kept legible in the viewer's Character panel, but the
# default render shows the Catacombs pieces (like the live client).
_CLASSIC_BODY_PREFIXES = ("body", "lbody", "arms", "legs", "boots", "gloves")

# Fitting-order of the default fig3 parts, and the pskin sheet family each
# slot defaults to (DAoC Tools bakes one sheet per slot, shared by all
# variants: torso `cthbody01_01`, arms `ctharms01_01`, legs/gloves/boots
# `lea*01_01` — matched against their parts_library_<race>_<gender>.glb).
_BODY_SLOTS = (
    ("Body", "cthbody01_01"),
    ("Legs", "lealegs01_01"),
    ("Boots", "leaboots01_01"),
    ("Arms", "ctharms01_01"),
    ("Gloves", "leagloves01_01"),
)

# Bones whose combined weight marks a whole-body distance LOD: these meshes
# span from the legs to the arms (head shells and cloaks never do).
_LEG_CHAIN = ("Thigh", "Calf", "Foot")
_ARM_CHAIN = ("UpperArm", "Forearm", "Hand")


def face_texture_name(part: fig3.Part, index: int = DEFAULT_FACE_INDEX) -> str:
    """``Sar_f_Head01`` + 2 -> ``Sar_f_Head02.dds`` (trailing index swapped)."""
    stem = part.filename
    digits = ""
    while stem and stem[-1].isdigit():
        digits = stem[-1] + digits
        stem = stem[:-1]
    if digits:
        return f"{stem}{index:0{len(digits)}d}.dds"
    return f"{stem}{index:02d}.dds"


def hair_texture_name(part: fig3.Part) -> str:
    return f"{part.filename}.dds"


def _resolve_face(client_dir: str, part: fig3.Part) -> tuple[str, bytes] | None:
    name = face_texture_name(part, DEFAULT_FACE_INDEX)
    data = gamedata.texture_bytes(client_dir, name)
    if data is None:  # same sheet under the other case spelling
        data = gamedata.texture_bytes(client_dir, part.filename + ".dds")
    return (name, data) if data is not None else None


def _resolve_hair(client_dir: str, part: fig3.Part) -> tuple[str, bytes] | None:
    """A hair sheet shipped under the style's own name (base, else colour)."""
    base = hair_texture_name(part)
    data = gamedata.texture_bytes(client_dir, base)
    if data is not None:
        return base, data
    for suffix in _HAIR_FALLBACKS:
        name = f"{base.rsplit('.', 1)[0]}{suffix}.dds"
        data = gamedata.texture_bytes(client_dir, name)
        if data is not None:
            return name, data
    return None


def _hair_from_colour_map(client_dir: str, rid: int) -> tuple[str, bytes] | None:
    """The race's hair sheet from ``fig3haircolormap.csv`` (style 1 row).

    Authoritative when the styles ship no sheets under their own names: Celt
    female points at Briton sheets, Highlander female at Norseman ones, and
    every style of a race shares the same UV layout (verified by overlay), so
    any of the listed sheets fits the first style's mesh. The dark-brown
    variant wins as the default colour.
    """
    for row in gamedata._read_table(client_dir, "fig3haircolormap.csv"):
        if len(row) < 5 or row[0].strip() != str(rid) or row[1].strip() != "1":
            continue
        sheets: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        cells = row[3:]
        for index in range(0, len(cells), 3):
            skin_id = cells[index].strip()
            if not skin_id or skin_id == "0":
                continue
            found = gamedata.skin_texture(client_dir, skin_id)
            if found is not None and found[0].lower() not in seen:
                seen.add(found[0].lower())
                sheets.append(found)
        if not sheets:
            return None
        dark = [
            sheet
            for sheet in sheets
            if "dkbrown" in sheet[0].lower() or "darkbrown" in sheet[0].lower()
        ]
        return (dark or sheets)[0]
    return None


def _pick_hair(
    client_dir: str, rid: int, parts: list[fig3.Part]
) -> tuple[fig3.Part, tuple[str, bytes]] | None:
    """The first hair style's mesh with a sheet that ships in the client.

    The colour map wins when it lists sheets (the client's own mapping);
    otherwise each style's own sheets are tried in engine order.
    """
    if not parts:
        return None
    sheet = _hair_from_colour_map(client_dir, rid)
    if sheet is not None:
        return parts[0], sheet
    for part in parts:
        found = _resolve_hair(client_dir, part)
        if found is not None:
            return part, found
    return None


def eye_texture(client_dir: str, rid: int) -> tuple[str, bytes] | None:
    """The race's default eye sheet (``fig3eyecolormap.csv``, colour 1).

    Fig3 heads carry the eyes as separate geometry that the face sheet does
    not cover — the engine draws them with an ``eyecolor_*`` sheet.
    """
    for row in gamedata._read_table(client_dir, "fig3eyecolormap.csv"):
        if len(row) < 3 or row[0].strip() != str(rid):
            continue
        for skin_id in row[2:]:
            sid = skin_id.strip()
            if not sid or sid == "0":
                continue
            found = gamedata.skin_texture(client_dir, sid)
            if found is not None:
                return found
    return None


def eye_vertices(shape) -> set[int]:
    """Vertex indices driven by the eyeball bones of a fig3 head piece.

    Must be read on the *source* piece: attaching rewrites the skin instance
    onto the target skeleton, where the missing eye bones are already folded
    into ``Bip01 Head``.
    """
    names = fig3.skin_bone_names(shape)
    weights = fig3.shape_weights(shape)
    if not names or not weights:
        return set()
    out: set[int] = set()
    for vertex, vertex_weights in weights.items():
        eye_weight = sum(
            weight
            for skin_index, weight in vertex_weights
            if "Eyeball" in (names[skin_index] if skin_index < len(names) else "")
        )
        total = sum(weight for _k, weight in vertex_weights)
        if total > 0 and eye_weight / total >= 0.5:
            out.add(vertex)
    return out


def _copy_node_transform(source, target) -> None:
    """Copy the authoring node transform onto a shape split from *source*.

    Fig3 head pieces author their raw vertices off-centre and carry the
    recentring in the *node* transform (``fro_m_head01`` translates x by
    -11.2; every race carries ~-3.5 in depth). The engine renders the whole
    head — eyeballs included — under that frame, so a split shape created
    with an identity node sinks the eyes into the skull.
    """
    translation = getattr(source, "translation", None)
    if translation is not None:
        target.translation.x = float(translation.x)
        target.translation.y = float(translation.y)
        target.translation.z = float(translation.z)
    rotation = getattr(source, "rotation", None)
    target_rotation = getattr(target, "rotation", None)
    if rotation is not None and target_rotation is not None:
        for a in range(1, 4):
            for b in range(1, 4):
                setattr(target_rotation, f"m_{a}{b}", float(getattr(rotation, f"m_{a}{b}")))
    scale = getattr(source, "scale", None)
    if scale is not None:
        target.scale = float(scale)


def _split_eyes(data, head_shape, eye_vertex_set: set[int],
                name: str = "Eyes") -> bool:
    """Move the eyeball triangles of *head_shape* into their own shape.

    The eyes sample the eye sheet, the face samples the face sheet: with one
    mesh and one texture they rendered as flat skin. The new shape shares the
    vertex data and the skin instance; only the triangle lists differ. The
    authored UVs are kept as-is: measured per race, the eyeball's front-cap UV
    centroid lands within 1-3 px of the eye sheet's artwork (a ~7 px iris
    drawing on a 128 px sheet), so the engine mapping is 1:1 — remapping the
    island onto the artwork magnifies it ~5x and renders faceted.
    """
    from pyffi.formats.nif import NifFormat

    skin_instance = getattr(head_shape, "skin_instance", None)
    skin_data = fig3._skin_data(skin_instance) if skin_instance is not None else None
    if skin_instance is None or skin_data is None or not eye_vertex_set:
        return False
    source = head_shape.data
    triangles = list(source.get_triangles())
    eye_indices = [
        index for index, tri in enumerate(triangles) if all(v in eye_vertex_set for v in tri)
    ]
    if not eye_indices:
        return False
    eye_index_set = set(eye_indices)
    # pyffi has no setters for arrays (the element hooks are read-only for
    # struct arrays): append the raw elements instead. The eyes mesh shares
    # the vertex/normal/UV entries with the face mesh, only the triangle
    # lists differ, and neither NIF is written back.
    eyes_data = NifFormat.NiTriShapeData()
    for vertex in source.vertices:
        list.append(eyes_data.vertices, vertex)
    eyes_data.num_vertices = source.num_vertices
    has_normals = bool(getattr(source, "has_normals", False)) and bool(source.normals)
    if has_normals:
        eyes_data.has_normals = True
        for normal in source.normals:
            list.append(eyes_data.normals, normal)
    uv_sets = list(getattr(source, "uv_sets", None) or [])
    if uv_sets:
        eyes_data.num_uv_sets = len(uv_sets)
        for uv_set in uv_sets:
            layer: list = []
            for texcoord in uv_set:
                copied = NifFormat.TexCoord()
                copied.u = float(texcoord.u)
                copied.v = float(texcoord.v)
                list.append(layer, copied)
            list.append(eyes_data.uv_sets, layer)
    for index in eye_indices:
        # pyffi's Triangle constructor takes no field kwargs: reuse the
        # source blocks (they are never written back).
        list.append(eyes_data.triangles, source.triangles[index])
    eyes_data.num_triangles = len(eye_indices)

    eyes_shape = NifFormat.NiTriShape()
    eyes_shape.name = name.encode("latin1")
    eyes_shape.flags = 0
    eyes_shape.data = eyes_data
    eyes_shape.skin_instance = skin_instance
    _copy_node_transform(head_shape, eyes_shape)
    fig3._append_child(data.roots[0], eyes_shape)

    for index in range(len(source.triangles) - 1, -1, -1):
        if index in eye_index_set:
            del source.triangles[index]
    return True


def _shape_name(shape) -> str:
    return shape.name.decode("latin1") if getattr(shape, "name", None) else ""


def classic_head_shapes(data) -> list:
    """The classic head shells to hide, by name or by bone binding.

    Standard player figures name them ``HeadA*/HeadB*``; older exports
    (`HFirbolgF`, `BHalfOgreM`) call every mesh ``Figure:N`` (renamed by
    :func:`_rename_unnamed_shapes` first) and are matched by their skin
    weight: a head shell carries most of its weight on the head/eye bones.
    """
    shapes = fig3.shapes_of(data)
    named = [
        shape
        for shape in shapes
        if _shape_name(shape).lower().startswith(_CLASSIC_HEAD_PREFIXES)
    ]
    if named:
        return named
    # No usable names (or renamed fallbacks failed): bind-driven fallback.
    # Only the ≥50% head-weight rule — a lower threshold (or a dominant-bone
    # test) catches hooded cloak variants and, on single-mesh figures, the
    # whole body (which must stay: fused "Editable Mesh" figures carry their
    # own baked head).
    return [shape for shape in shapes if fig3.head_dominated(shape)]


def whole_body_shapes(data) -> list:
    """Whole-body distance LODs to hide: their weight spans a leg chain and an
    arm chain. Classic figures name them ``Lod1a..d`` (the web panel handles
    those); unnamed exports (``BHalfOgreM``'s four ``Editable Mesh`` levels)
    are caught here so they never render on top of the assembled body."""
    out = []
    for shape in fig3.shapes_of(data):
        if _shape_name(shape).lower().startswith("lod"):
            continue
        names = fig3.skin_bone_names(shape)
        if not names:
            continue
        weights = fig3.shape_weights(shape)
        leg = arm = False
        for vertex_weights in weights.values():
            for skin_index, weight in vertex_weights:
                if weight <= 0:
                    continue
                name = names[skin_index] if skin_index < len(names) else ""
                if any(k in name for k in _LEG_CHAIN):
                    leg = True
                elif any(k in name for k in _ARM_CHAIN):
                    arm = True
            if leg and arm:
                break
        if leg and arm:
            out.append(shape)
    return out


# Node-index layout shared by every classic player export: unnamed figures
# ("Figure:N" / "Editable Mesh") still follow BSaracenF's shape order, so the
# index maps to the slot name the named figures use.
_CLASSIC_NODE_TEMPLATE = {
    0: "Body1", 1: "Body1b", 2: "Body2", 3: "Body2b", 4: "Body3", 5: "Body3b",
    6: "Lbody1", 7: "Lbody2", 8: "Lbody3", 9: "Arms1", 10: "Arms2", 11: "Arms3",
    12: "Arms4", 13: "Gloves1", 14: "Legs1", 15: "Boots1", 16: "Boots2",
    17: "Cloak1", 18: "Cloak2", 19: "HeadA1", 20: "HeadA2", 21: "HeadA3",
    22: "HeadA4", 23: "HeadA5", 24: "HeadB1", 25: "HeadB2", 26: "HeadB3",
    27: "HeadB4", 28: "HeadB5", 29: "Cloak3", 30: "Body4", 31: "Body4b",
    32: "Arms5", 33: "Arms6", 34: "Gloves2", 35: "Gloves3", 36: "Legs2",
    37: "Boots3", 38: "Legs3", 39: "LBody4", 40: "Lod1a", 41: "Lod1b",
    42: "Lod1c", 43: "Lod1d", 44: "Body5", 45: "Body5b", 46: "Legs4",
    47: "Boots4", 48: "Boots5",
}


def _rename_unnamed_shapes(data) -> int:
    """Give the classic template names to ``Figure:N``/``Editable Mesh`` shapes.

    The Hibernian/older exporters dropped the slot names but kept the shape
    order; renaming restores the Character panel's variant grouping. Returns
    the number of renamed shapes.
    """
    import re

    renamed = 0
    for shape in fig3.shapes_of(data):
        name = _shape_name(shape)
        if not name:
            continue
        match = re.fullmatch(r"Figure:?(\d+)", name)
        if not match:
            continue
        new_name = _CLASSIC_NODE_TEMPLATE.get(int(match.group(1)))
        if new_name:
            shape.name = new_name.encode("latin1")
            renamed += 1
    return renamed


def has_classic_heads(data) -> bool:
    """Named ``HeadA*``/``HeadB*`` shells (the standard classic layout).

    The bone-weight fallback is only for `classic_head_shapes` (Figure:N
    figures): after assembly the attached fig3 head is head-dominated too.
    """
    return any(
        _shape_name(shape).lower().startswith(_CLASSIC_HEAD_PREFIXES)
        for shape in fig3.shapes_of(data)
    )


def _hide_shapes(data, hidden_shapes: list) -> int:
    """Remove *hidden_shapes* (by identity) from the export tree."""
    ids = {id(shape) for shape in hidden_shapes}
    hidden = 0

    def walk(obj) -> None:
        nonlocal hidden
        children = getattr(obj, "children", None)
        if children is None:
            return
        # Delete from the live array back-to-front: index-based deletes while
        # walking forward would shift the remaining entries (and drop the
        # wrong meshes).
        for index in range(len(children) - 1, -1, -1):
            if id(children[index]) in ids:
                del children[index]
                hidden += 1
            else:
                walk(children[index])

    for root in data.roots:
        walk(root)
    return hidden


def _body_sheets(client_dir: str, stem: str, female: bool) -> tuple[str, bytes] | None:
    """Default pskin sheet of a body slot, gendered spelling first."""
    table = gamedata._read_table(client_dir, "pskins.csv")
    candidates = []
    for suffix in ("_f", "_m") if female else ("_m", "_f"):
        candidates.append(f"{stem}{suffix}.dds")
    candidates.append(stem + ".dds")
    wanted = [c.lower() for c in candidates]
    for row in table:
        for cell in row:
            name = cell.strip()
            if name.lower() in wanted:
                found = gamedata.texture_bytes(client_dir, name)
                if found is not None:
                    return name, found
    for name in candidates:
        found = gamedata.texture_bytes(client_dir, name)
        if found is not None:
            return name, found
    return None


def _hide_classic_body(data) -> list:
    """The classic slot meshes the fig3 body assembly replaces (name-driven)."""
    out = []
    for shape in fig3.shapes_of(data):
        name = _shape_name(shape).lower()
        if name.startswith(_CLASSIC_BODY_PREFIXES) or name.startswith("lod"):
            out.append(shape)
    return out


def _attach_body(
    client_dir: str, tables: fig3.Tables, rid: int, data, reference
) -> list[tuple[str, str, bytes]]:
    """Attach the first fig3 piece of every body slot, with its pskin sheet."""
    desc = (tables.race_desc.get(rid) or "").lower()
    female = "female" in desc
    textures: list[tuple[str, str, bytes]] = []
    attached_any = False
    for slot, stem in _BODY_SLOTS:
        parts = fig3.parts_of(tables, rid, slot)
        if not parts:
            continue
        piece = fig3.load_part(client_dir, parts[0])
        if piece is None:
            continue
        sheet = _body_sheets(client_dir, stem, female)
        if not fig3.attach_part(data, piece, slot, reference=reference):
            continue
        attached_any = True
        if sheet is not None:
            textures.append((slot, sheet[0], sheet[1]))
    return textures if attached_any else []


def prepare(client_dir: str, nif_path: str):
    """Build the fig3-headed variant of a classic player figure.

    Returns ``(data, [("Head", name, dds), ("Hair", name, dds)])`` or None
    when the figure is not a classic player base, the fig3 tables have no
    matching race, or a piece fails to load.
    """
    from .nifutil import load

    tables = fig3.read_tables(client_dir)
    desc = gamedata.figure_desc(client_dir, nif_path)
    if not desc:
        return None
    rid = fig3.race_id(tables, desc)
    if rid is None:
        return None
    head_parts = fig3.parts_of(tables, rid, "Head")
    hair_parts = fig3.parts_of(tables, rid, "Hair")
    if not head_parts or not hair_parts:
        return None

    face = _resolve_face(client_dir, head_parts[0])
    picked = _pick_hair(client_dir, rid, hair_parts)
    if face is None or picked is None:
        return None
    hair_part, hair = picked

    data = load(nif_path)
    # Older exports dropped the slot names ("Figure:N", "Editable Mesh") but
    # kept the classic shape order: renaming restores name-based handling.
    _rename_unnamed_shapes(data)
    heads = classic_head_shapes(data)
    if not heads:
        return None  # already a fig3 figure (or not a player base)
    _hide_shapes(data, whole_body_shapes(data))
    # The Catacombs pieces replace every classic body slot; the hide must
    # happen *before* attaching (the attached pieces carry the same names).
    classic_bodies = _hide_classic_body(data)

    head_data = fig3.load_part(client_dir, head_parts[0])
    hair_data = fig3.load_part(client_dir, hair_part)
    if head_data is None or hair_data is None:
        return None
    # Eye vertices must be read on the source piece, before attaching folds
    # its eye bones into Bip01 Head on the classic skeleton.
    source_head = next(
        (shape for shape in fig3.shapes_of(head_data) if shape.skin_instance), None
    )
    source_eye_vertices = eye_vertices(source_head) if source_head is not None else set()

    # Bind reference: the skeleton embedded in the canonical fig3 head piece.
    # Skeleton-less heads/hair (bonelist-driven) take their source bone worlds
    # from it; a piece with its own skeleton overrides per bone. Attaching
    # *aligned on the target's head bone* puts the taller Catacombs head on
    # the classic neck instead of floating at its fig3 height.
    reference = fig3.load_reference(client_dir, tables)
    attached = fig3.attach_part(
        data, head_data, "Head", reference=reference, align_bone=_ANCHOR
    ) + fig3.attach_part(
        data, hair_data, "Hair", reference=reference or head_data, align_bone=_ANCHOR
    )
    if attached < 2:
        return None
    _hide_shapes(data, heads)
    textures = [("Head", face[0], face[1]), ("Hair", hair[0], hair[1])]
    # Smooth Catacombs body parts on the classic skeleton: the per-bone bind
    # compensation absorbs the small (≤2.5u) classic/fig3 rig offsets — each
    # bone maps through its own offset, as with the head, so the progressive
    # spine difference lands the piece over the classic collar.
    body_textures = _attach_body(client_dir, tables, rid, data, reference)
    if body_textures:
        # The attached piece beats the classic monsters.csv skin by slot name.
        replaced = {tex[0] for tex in body_textures}
        textures = [
            tex for tex in textures if tex[0] not in replaced
        ] + body_textures
        _hide_shapes(data, classic_bodies)
    # Fig3 heads carry the eyes as geometry the face sheet does not cover:
    # give them their own mesh + sheet so they do not render as flat skin.
    head_shapes = [shape for shape in fig3.shapes_of(data) if _shape_name(shape) == "Head"]
    eye = eye_texture(client_dir, rid)
    if (
        eye is not None
        and head_shapes
        and source_eye_vertices
        and _split_eyes(data, head_shapes[0], source_eye_vertices)
    ):
        textures.append(("Eyes", eye[0], eye[1]))
    return data, textures


def export_figure(
    client_dir: str,
    nif_path: str,
    out_prefix: str,
    *,
    verbose: bool = False,
    emit_skinned: bool = False,
    textures: list[tuple[str, str, bytes]] | None = None,
    fig3_head: bool = True,
    **kwargs,
) -> dict:
    """``export_gltf`` with the fig3 head substitution when applicable.

    Any piece-level failure (missing archive, unreadable NIF) silently falls
    back to the classic figure: the head stays hidden, the rest exports.
    """
    from .gltf import export_gltf
    from .mpak import MpakError

    data = None
    extra: list[tuple[str, str, bytes]] = []
    if fig3_head:
        # Native fig3 assembly first (the live client's way); the classic
        # hybrid is the fallback for layouts no fig3 race covers.
        try:
            desc = gamedata.figure_desc(client_dir, nif_path)
        except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError):
            desc = None
        from . import playerfig

        try:
            prepared = playerfig.build(client_dir, desc) if desc else None
        except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError):
            prepared = None
        if prepared is None:
            try:
                prepared = prepare(client_dir, nif_path)
            except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError):
                prepared = None
        if prepared is not None:
            data, extra = prepared

    if textures is None:
        try:
            textures = gamedata.resolve_figure_skins(client_dir, nif_path)
        except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError):
            textures = None
    if extra:
        # The default fig3 face replaces the classic monsters.csv head skin.
        textures = [tex for tex in textures if tex[0] != "Head"] + extra
    return export_gltf(
        nif_path,
        out_prefix,
        verbose=verbose,
        emit_skinned=emit_skinned,
        textures=textures,
        data=data,
        **kwargs,
    )
