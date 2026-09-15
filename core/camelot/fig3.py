"""Catacombs fig3 pieces: tables, extraction and skeleton attachment.

The live client no longer composes player bodies from the classic base
figures (``figures/B*.NIF``, whose ``HeadA*/HeadB*`` pair needed the engine's
decal shader): every visible part is a separate file under
``figures/fig3/figNNN.mpk``, listed by ``fig3parts.csv`` and grouped per
race/gender by ``fig3map.csv``.

Those pieces ship *without* their own skeleton: the skin instance binds
placeholder nodes and the real bones are named in a ``NiStringsExtraData``
``bonelist`` (hair, body…), or reference the skeleton embedded in the fig3
head piece. This module reads the tables, extracts a piece and rebinds its
skin onto a target skeleton **by bone name**, compensating the stored bind
transforms so the piece lands exactly where its own file authored it::

    S' = inv(boneWorld_target) @ boneWorld_part @ S

With matching bind poses (the fig3 assembly) ``S' == S``; on a classic
figure, whose bones sit at different heights, the correction keeps the piece
in place at the bind pose and lets it follow the target skeleton under
animation.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import numpy as np

from .gamedata import _read_table, norm_name
from .mpak import MpakArchive
from .nifutil import mat4_mul, node_local_matrix, skin_transform_matrix

# fig3map.csv part column (0..9) -> slot name.
SLOT_NAMES = {
    0: "Head",
    1: "Body",
    2: "LBody",
    3: "Legs",
    4: "Boots",
    5: "Arms",
    6: "Gloves",
    7: "Cloak",
    8: "Hair",
    9: "FullHelm",
}

# Shapes that never carry the visible piece (bone-visualisation boxes in the
# fig3 head file mark Helmet/Held/Shield bones).
_SKIP_SHAPE_PREFIXES = ("box",)


@dataclass(frozen=True)
class Part:
    """One fig3 piece: ``fig<archive>.mpk`` -> ``<filename>.nif``."""

    id: int
    desc: str
    filename: str  # e.g. "sar_f_Hair01" (no extension)
    archive: int  # figNNN archive number
    double_sided: bool = False


@dataclass
class Tables:
    # fig3parts reuses some ids for different files (id 199 = `hal_m_Hair01`
    # *and* `hig_m_Hair03`), so an id maps to a list of candidates.
    parts: dict[int, list[Part]] = field(default_factory=dict)
    races: dict[int, dict[str, list[int]]] = field(default_factory=dict)
    race_desc: dict[int, str] = field(default_factory=dict)


def _strip_slot_word(desc: str, word: str) -> str:
    if desc.lower().endswith(word.lower()):
        return desc[: -len(word)].strip()
    return desc


def parse_parts(rows: list[list[str]]) -> dict[int, list[Part]]:
    """``fig3parts.csv`` rows -> {id: [Part, ...]}. Two header rows skipped.

    Some extractor rows leak float ids (``1400.038407``); the engine
    reads the field with an integral-part coercion (``_atol`` semantics),
    so we do the same and key the piece by the truncated id.
    """
    parts: dict[int, list[Part]] = {}
    for row in rows:
        if len(row) < 6:
            continue
        raw_id = row[0].strip()
        filename = row[2].strip()
        if not _integral_id(raw_id) or not filename:
            continue
        try:
            archive = int(row[5].strip())
        except ValueError:
            continue
        part = Part(
            id=_integral_id(raw_id),
            desc=row[1].strip(),
            filename=filename,
            archive=archive,
            double_sided=bool(row[6].strip()) if len(row) > 6 else False,
        )
        parts.setdefault(part.id, []).append(part)
    return parts


def _integral_id(raw: str) -> int | None:
    """Parse an id the way the engine's field loader does: only the leading
    integral digits count (``1400.038407`` -> 1400)."""
    digits = ""
    for char in raw.strip():
        if not char.isdigit():
            break
        digits += char
    return int(digits) if digits else None


def parse_map(rows: list[list[str]]) -> tuple[dict[int, dict[str, list[int]]], dict[int, str]]:
    """``fig3map.csv`` rows -> (parts per race id, head-row description)."""
    races: dict[int, dict[str, list[int]]] = {}
    descs: dict[int, str] = {}
    for row in rows:
        if len(row) < 4:
            continue
        raw_id, raw_slot = row[0].strip(), row[1].strip()
        if not raw_id.isdigit() or not raw_slot.isdigit():
            continue
        slot = SLOT_NAMES.get(int(raw_slot))
        if slot is None:
            continue
        ids = [int(cell) for cell in row[3:] if cell.strip().isdigit()]
        races.setdefault(int(raw_id), {})[slot] = ids
        if slot == "Head" and int(raw_id) not in descs:
            descs[int(raw_id)] = _strip_slot_word(row[2].strip(), "Head")
    return races, descs


def read_tables(client_dir: str) -> Tables:
    parts = parse_parts(_read_table(client_dir, "fig3parts.csv"))
    races, descs = parse_map(_read_table(client_dir, "fig3map.csv"))
    return Tables(parts=parts, races=races, race_desc=descs)


def race_id(tables: Tables, desc: str) -> int | None:
    """fig3 race/gender id for a monnifs/monsters description.

    Exact normalized match first, then word-set equality (the tables write
    ``Male Half Ogre`` where fig3map has ``Half Ogre Male``), then the longest
    race description contained in the target (``Saracen Female Mummy``).
    """
    target = norm_name(desc)
    if not target:
        return None
    target_words = _word_key(desc)
    best: int | None = None
    best_len = 0
    for rid, race_desc in tables.race_desc.items():
        key = norm_name(race_desc)
        if not key:
            continue
        words = _word_key(race_desc)
        if key == target or words == target_words:
            return rid
        # Containment needs a specific (multi-word) race name: single-word
        # descriptions like "Heavy" or "Undead" would otherwise match any
        # figure mentioning that word.
        if len(words) >= 2 and words <= target_words and len(words) > best_len:
            best, best_len = rid, len(words)
    return best


_RACE_WORD_SYNONYMS = {"nor": "norse", "norseman": "norse",
                       "half ogre": "half-ogre", "half-ogre": "half ogre"}


def _word_key(desc: str) -> frozenset[str]:
    words = {
        _RACE_WORD_SYNONYMS.get(norm_name(word), norm_name(word))
        for word in desc.split() if word
    }
    return frozenset(words)


def parts_of(tables: Tables, rid: int | None, slot: str) -> list[Part]:
    """The slot's pieces in engine order, disambiguated by race description.

    Ids shared by several rows keep only the candidates whose description
    starts with the race's (e.g. `Half Ogre Male Hair` for the Half Ogre);
    when none match (multi-race pieces like `Saracen/Frostalf/Shar Female
    Body`) every candidate is kept.
    """
    if rid is None:
        return []
    out: list[Part] = []
    for pid in tables.races.get(rid, {}).get(slot, []):
        out.extend(tables.parts.get(pid, []))
    desc = tables.race_desc.get(rid, "").lower()
    if desc:
        matching = [part for part in out if part.desc.lower().startswith(desc)]
        if matching:
            out = matching
    return out


def read_part_bytes(client_dir: str, part: Part) -> bytes | None:
    """The raw NIF bytes of a piece, or None when the archive lacks it."""
    path = os.path.join(client_dir, "figures", "fig3", f"fig{part.archive:03d}.mpk")
    if not os.path.isfile(path):
        return None
    archive = MpakArchive(path)
    try:
        by_lower = {entry.lower(): entry for entry in archive.entries}
        entry = by_lower.get(f"{part.filename.lower()}.nif")
        return archive.read(entry) if entry else None
    finally:
        archive.close()


def load_part(client_dir: str, part: Part):
    """Load a piece NIF; None when absent or unreadable."""
    raw = read_part_bytes(client_dir, part)
    if raw is None:
        return None
    from pyffi.formats.nif import NifFormat

    data = NifFormat.Data()
    try:
        data.read(io.BytesIO(raw))
    except Exception:  # noqa: BLE001 — corrupt piece must not break the export
        return None
    return data


# Some races' head pieces embed the canonical fig3 skeleton (`Sar_f_Head01`,
# 153 nodes); others ship skeleton-less, bonelist-driven like the hair and
# body pieces. The embedded rig is the bind reference for all of them.
_REFERENCE_CACHE: dict[str, object] = {}


def load_reference(client_dir: str, tables: Tables):
    """A fig3 piece that embeds the canonical skeleton the pieces were bound
    against, or None when the client ships none (then the caller must get its
    reference from the piece being attached)."""
    if client_dir in _REFERENCE_CACHE:
        return _REFERENCE_CACHE[client_dir]
    reference = None
    for rid in sorted(tables.races):
        for part in parts_of(tables, rid, "Head"):
            data = load_part(client_dir, part)
            if data is not None and len(_bone_nodes(data)) > 10:
                reference = data
                break
        if reference is not None:
            break
    _REFERENCE_CACHE[client_dir] = reference
    return reference


# --------------------------------------------------------------------------- #
# Skeleton attachment
# --------------------------------------------------------------------------- #


def _bone_nodes(data) -> dict[str, tuple[object, np.ndarray]]:
    """name -> (node, bind world) for every named node of a NIF tree."""
    out: dict[str, tuple[object, np.ndarray]] = {}

    def walk(obj, parent_world):
        m = node_local_matrix(obj)
        world = np.asarray(mat4_mul(parent_world, m), dtype=float) if m is not None else parent_world
        name = obj.name.decode("latin1") if getattr(obj, "name", None) else ""
        if name:
            out.setdefault(name, (obj, world))
        for child in getattr(obj, "children", None) or []:
            walk(child, world)

    for root in data.roots:
        walk(root, np.eye(4))
    return out


def _node_parents(data) -> dict[str, str]:
    parents: dict[str, str] = {}

    def walk(obj, parent_name):
        name = obj.name.decode("latin1") if getattr(obj, "name", None) else ""
        if name:
            parents[name] = parent_name
        for child in getattr(obj, "children", None) or []:
            walk(child, name)

    for root in data.roots:
        walk(root, "")
    return parents


def shapes_of(data) -> list:
    shapes: list = []

    def walk(obj):
        if type(obj).__name__ in ("NiTriShape", "NiTriStrips"):
            shapes.append(obj)
        for child in getattr(obj, "children", None) or []:
            walk(child)

    for root in data.roots:
        walk(root)
    return shapes


def _bonelist(shape) -> list[str] | None:
    for extra in getattr(shape, "extra_data_list", None) or []:
        if getattr(extra, "name", None) == b"bonelist":
            return [
                s.decode("latin1") if isinstance(s, bytes) else str(s) for s in extra.data
            ]
    return None


def _skin_data(skin_instance):
    return getattr(skin_instance, "skin_data", None) or getattr(skin_instance, "data", None)


def skin_bone_names(shape) -> list[str]:
    """Bone names a fig3 shape binds to: the ``bonelist`` when present, else
    the skin instance's own bone refs (the fig3 head piece)."""
    bonelist = _bonelist(shape)
    if bonelist:
        return bonelist
    skin_instance = getattr(shape, "skin_instance", None)
    if skin_instance is None:
        return []
    return [
        bone.name.decode("latin1") if getattr(bone, "name", None) else ""
        for bone in skin_instance.bones or []
    ]


def resolve_bone(name: str, target: dict, parents: dict[str, str]) -> str | None:
    """The target bone that carries *name*: itself, or its nearest ancestor.

    Fig3 pieces also name bones the classic skeletons lack (``Bip01
    EyeLids``, ``Bip01 Helmet``): those follow their parent bone instead of
    detaching.
    """
    current = name
    while current:
        if current in target:
            return current
        current = parents.get(current, "")
    return None


def shape_weights(shape) -> dict[int, list[tuple[int, float]]]:
    """Per-vertex [(skin bone index, weight), ...] from the skin partition."""
    skin_instance = getattr(shape, "skin_instance", None)
    if skin_instance is None:
        return {}
    skin_data = _skin_data(skin_instance)
    partition = getattr(skin_instance, "skin_partition", None)
    if partition is None and skin_data is not None:
        partition = getattr(skin_data, "skin_partition", None)
    blocks = getattr(partition, "skin_partition_blocks", None) if partition else None
    if blocks is None and partition is not None:
        blocks = getattr(partition, "partitions", [])
    out: dict[int, list[tuple[int, float]]] = {}
    for block in blocks or []:
        num = block.num_vertices
        vmap = list(block.vertex_map) if getattr(block, "has_vertex_map", False) else list(range(num))
        bones = list(block.bones or [])
        has_bi = bool(getattr(block, "has_bone_indices", False))
        has_vw = bool(getattr(block, "has_vertex_weights", False))
        for i in range(num):
            gv = int(vmap[i])
            bi = list(block.bone_indices[i]) if has_bi else [0]
            ws = [float(w) for w in block.vertex_weights[i]] if has_vw else [1.0] * len(bi)
            for bj, w in zip(bi, ws):
                out.setdefault(gv, []).append((bones[bj] if bj < len(bones) else bj, w))
    return out


def dominant_bone(shape) -> str | None:
    """The bone with the most skin weight on a shape."""
    names = skin_bone_names(shape)
    weights = shape_weights(shape)
    if not names or not weights:
        return None
    totals: dict[str, float] = {}
    for vertex_weights in weights.values():
        for skin_index, weight in vertex_weights:
            name = names[skin_index] if skin_index < len(names) else "?"
            totals[name] = totals.get(name, 0.0) + weight
    return max(totals, key=totals.get) if totals else None


def head_dominated(shape, threshold: float = 0.5) -> bool:
    """True when most of the shape's skin weight sits on the head/eye bones.

    Older figures (`HFirbolgF`, `BHalfOgreM`) name their meshes `Figure:N`,
    so their head shells are identified by what drives them instead of by
    name (neck collars are excluded on purpose — only Head/Eye bones count).
    """
    names = skin_bone_names(shape)
    weights = shape_weights(shape)
    if not names or not weights:
        return False
    total = 0.0
    head = 0.0
    for vertex_weights in weights.values():
        for skin_index, weight in vertex_weights:
            name = names[skin_index] if skin_index < len(names) else ""
            total += weight
            if "Head" in name or "Eye" in name:
                head += weight
    return total > 0 and (head / total) >= threshold


def _set_rotation(rotation, m: np.ndarray) -> None:
    # nifutil.nif_rot_cell reads m_{j+1}{i+1} for the true cell (i, j).
    for i in range(3):
        for j in range(3):
            setattr(rotation, f"m_{j + 1}{i + 1}", float(m[i, j]))


def write_skin_transform(entry, m: np.ndarray) -> None:
    """Store a 4x4 skin-to-bone transform in a NiSkinData bone entry."""
    if isinstance(entry, dict):  # legacy reader entry
        r = [[float(m[i, j]) for j in range(3)] for i in range(3)]
        entry["transform"] = (r, [float(m[0, 3]), float(m[1, 3]), float(m[2, 3])], 1.0)
        return
    transform = getattr(entry, "skin_transform", None)
    if transform is None or not hasattr(transform, "rotation"):
        raise ValueError("unsupported NiSkinData bone entry")
    linear = m[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0])) or 1.0
    _set_rotation(transform.rotation, linear / scale)
    translation = transform.translation
    translation.x, translation.y, translation.z = (
        float(m[0, 3]),
        float(m[1, 3]),
        float(m[2, 3]),
    )
    transform.scale = scale


def _append_child(parent, node) -> None:
    children = parent.children
    ref = children._elementType(
        template=children._elementTypeTemplate,
        argument=children._elementTypeArgument,
        parent=children,
    )
    ref.set_value(node)
    children.append(ref)


def _detach(shape, data) -> None:
    def walk(obj):
        for index, child in enumerate(list(getattr(obj, "children", None) or [])):
            if child is shape:
                del obj.children[index]
                return True
            if walk(child):
                return True
        return False

    for root in data.roots:
        if walk(root):
            return


def attach_part(
    target, part, name: str, reference=None, align_bone: str | None = None
) -> int:
    """Rebind every skinned shape of *part* onto *target* and name it *name*.

    A skeleton-less piece (hair, body…) does not carry the bones its
    ``bonelist`` names: *reference* supplies them — the skeleton embedded in
    the fig3 head piece, which is the bind the pieces were authored against.
    Bone resolution and the bind compensation use that source skeleton; bones
    missing from the target follow their nearest ancestor there.

    *align_bone* rigidly attaches the piece to the target's bone of that name
    instead of keeping its authored fig3 position: the whole piece is moved by
    ``Wb_target @ inv(Wb_ref)``, which is what puts a Catacombs head on a
    classic neck (the fig3 skeleton's head sits ~2.8 units higher).

    Returns the number of attached shapes (0 when the piece carries no
    skinned geometry).
    """
    target_bones = _bone_nodes(target)
    source = reference if reference is not None else part
    source_bones = {**_bone_nodes(source), **_bone_nodes(part)}
    source_parents = {**_node_parents(source), **_node_parents(part)}
    # Rigid attach: maps the piece's authored frame onto the target's bone.
    correction: np.ndarray | None = None
    if align_bone:
        src = source_bones.get(align_bone)
        dst = target_bones.get(align_bone)
        if src is not None and dst is not None:
            correction = dst[1] @ np.linalg.inv(src[1])
    attached = 0
    for shape in shapes_of(part):
        shape_name = shape.name.decode("latin1") if getattr(shape, "name", None) else ""
        if shape_name.lower().startswith(_SKIP_SHAPE_PREFIXES):
            continue
        skin_instance = getattr(shape, "skin_instance", None)
        if skin_instance is None:
            continue
        names = skin_bone_names(shape)
        if not names:
            continue
        skin_data = _skin_data(skin_instance)
        moved = False
        for k, source_name in enumerate(names):
            resolved = resolve_bone(source_name, target_bones, source_parents)
            if resolved is None or source_name not in source_bones:
                continue
            if skin_data is not None and k < len(skin_data.bone_list):
                stored = np.asarray(skin_transform_matrix(skin_data.bone_list[k]), dtype=float)
                source_world = source_bones[source_name][1]
                if correction is not None:
                    source_world = correction @ source_world
                compensated = np.linalg.inv(target_bones[resolved][1]) @ source_world @ stored
                write_skin_transform(skin_data.bone_list[k], compensated)
            if k < len(skin_instance.bones):
                skin_instance.bones[k] = target_bones[resolved][0]
            moved = True
        if not moved:
            continue
        if name:
            shape.name = name.encode("latin1")
        _detach(shape, part)
        _append_child(target.roots[0], shape)
        attached += 1
    return attached





def race_short(tables, race_id: int, female: bool) -> str | None:
    """The `<code>_<gender>` of a fig3 race row — the skeleton-file stem."""
    desc = tables.race_desc.get(race_id)
    if not desc:
        return None
    token = desc.split()[0].lower()
    # fig3map says "Norse Male" where monnifs says "Norseman Male".
    token = _RACE_WORD_SYNONYMS.get(token, token)
    short = {"briton": "bri", "avalonian": "ava", "highlander": "hig",
             "saracen": "sar", "inconnu": "inc", "norse": "nor",
             "troll": "tro", "dwarf": "dwa", "kobold": "kob",
             "valkyn": "val", "frostalf": "fro", "celt": "cel",
             "elf": "elf", "firbolg": "fir", "lurikeen": "lur",
             "sylvan": "syl", "shar": "sha", "half": "hal",
             "minotaur": "min", "korazh": "min", "deifrang": "min",
             "graoch": "min"}
    code = short.get(token)
    return f"{code}_{'f' if female else 'm'}" if code else None
