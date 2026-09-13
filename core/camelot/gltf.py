"""Camelot Studio — glTF 2.0 export of a NIF figure (bind pose, skinned).

- Axis conversion: NIF Z-up -> glTF Y-up through a synthetic root node (A).
- Skin: bones = NiSkinInstance.bones (order = NiSkinData.bone_list);
  glTF inverse bind = invBind_nif * A^T (see docs/formats.md).
- JOINTS_0/WEIGHTS_0: max 4 influences, normalized.
- Textures: not embedded in M0 (skins folder to come).
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import numpy as np

from .nifutil import (
    mat4_axis,
    mat4_identity,
    mat4_mul,
    nif_rot_cell,
    node_local_matrix,
    vec3,
)

__all__ = ["export_gltf"]

I32 = 5125
F32 = 5126
U16 = 5123


def _vec2(v) -> tuple[float, float]:
    try:
        return (float(v.u), float(v.v))
    except AttributeError:
        return (float(v[0]), float(v[1]))


def _apply_axis(points3) -> np.ndarray:
    arr = np.asarray(points3, dtype=np.float32)
    x, y, z = arr[:, 0], arr[:, 1], arr[:, 2]
    return np.stack([x, z, -y], axis=1)


def _quat_to_mat3(q) -> list[list[float]]:
    """Quaternion (w, x, y, z) -> row-major 3x3 (standard, no transpose).

    Quaternions are unambiguous (unlike NIF Matrix33 storage), so the standard
    conversion matches the transposed-corrected bind matrices (Bip01: 0.0 deg).
    """
    w, x, y, z = (float(c) for c in q)
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


# KFA channels address Biped bones only (the canonical table has no
# Footsteps: Pelvis=1, and no Scene Root). Those nodes are never animated
# targets, yet their offsets collide with real bones (Zombie.NIF's Scene
# Root sits at (0,0,0) exactly like its Pelvis, so a wrong mapping pitched
# the whole figure over).
_KFA_NON_BONES = frozenset({"scene root", "bip01 footsteps"})


def _rot3(r) -> list[list[float]]:
    """Row-major 3x3 of a node's bind rotation (identity when absent)."""
    if r is None:
        return [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    return [[nif_rot_cell(r, i, j) for j in range(3)] for i in range(3)]


def _rot_deviation_deg(a: list[list[float]], b: list[list[float]]) -> float:
    """Angle between two rotations in degrees (acos((tr(A·Bᵀ)−1)/2))."""
    cos = (sum(a[i][k] * b[i][k] for i in range(3) for k in range(3)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _channel_rot(ch):
    """First-key rotation matrix of a channel, or None without rotation keys."""
    if not ch.rotations:
        return None
    return _quat_to_mat3(ch.rotations[0].value)


def _pick_candidate(scored):
    """Choose among (distance, rotation-deviation, node) candidates.

    Translation leads; the rotation deviation (KFA quats are absolute local
    rotations, so at idle they sit within a few degrees of the bind) only
    breaks translation near-ties — the Zombie's Pelvis and Scene Root both
    sat at (0,0,0), and only the rotation tells them apart.
    """
    if not scored:
        return None
    best_d = min(c[0] for c in scored)
    if not math.isfinite(best_d):
        return None
    tie = [c for c in scored if c[0] <= best_d + max(0.05, 0.05 * best_d)]
    if not tie:
        return None
    tie.sort(key=lambda c: (c[1] if c[1] is not None else 0.0, c[0], c[2]))
    return tie[0]


def _norm_bone_name(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def map_kfa_to_nodes(
    objs,
    names,
    pose,
    trans_tol: float = 0.3,
    bone_names: list[str] | None = None,
    with_source: bool = False,
    prefer_table: bool = False,
):
    """Map KFA channel index -> node index.

    A corresponding (figure, KFA) pair (same creature, e.g. Boar.NIF with
    BOAR_idle.kfa) stores identical bone offsets: 27/31 Boar channels match
    to 1e-6. Anchors (unambiguous translations) fix their nodes first;
    contiguous index runs then inherit their side from their anchors (limb
    runs are same-side chains: 22-26 L, 35-39 R) and remaining members take
    the nearest unused node on that side. Translation near-ties (mirrored
    bones, a root at the origin) are broken by the first-key rotation, which
    must agree with the node's bind rotation for a bone.

    Pair quality (fraction of translated channels the offsets resolve) gates
    reuse: clips authored on a different rig (wolves, giants) or with a
    shifted channel table (the CATA boar's arms/tail) score low — those map
    nothing and keep the bind pose rather than flail the wrong bones. When
    *bone_names* (the client's ``figures/animnode.dat``, channel index ->
    bone name) is given and the offsets vouch for the pair, the table fills
    the remaining channels by name — including rotation-only channels
    (Ext/cloth helpers) that offset matching cannot bind at all. Returns {}
    when *pose* is None.
    """
    if pose is None:
        return ({}, "none") if with_source else {}

    # A gamedata-linked clip (prefer_table): the client itself binds this KFA
    # to this figure, so the animnode.dat name table is the engine's own
    # mapping — use it directly (physics gates validate the result) instead
    # of the offset heuristics, which mistrust animated bones (root bob,
    # clavicle breathing) and would refuse amazon/rat/bear….
    if prefer_table and bone_names:
        by_name: dict[str, int] = {}
        for i, obj in enumerate(objs):
            if obj is None or type(obj).__name__ != "NiNode":
                continue
            if names[i].lower() in _KFA_NON_BONES:
                continue
            by_name.setdefault(_norm_bone_name(names[i]), i)
        table_hits: dict[int, int] = {}
        for idx in sorted(pose.channels):
            if idx >= len(bone_names):
                continue
            ni = by_name.get(_norm_bone_name(bone_names[idx]))
            if ni is not None:
                table_hits[idx] = ni
        if len(table_hits) >= min(3, len(pose.channels)) and len(table_hits) >= 0.5 * len(pose.channels):
            if with_source:
                return table_hits, "table"
            return table_hits

    node_trans: dict[int, tuple[float, float, float]] = {}
    node_rot: dict[int, list[list[float]]] = {}
    for i, obj in enumerate(objs):
        if obj is None or type(obj).__name__ != "NiNode":
            continue
        if names[i].lower() in _KFA_NON_BONES:
            continue
        t = vec3(getattr(obj, "translation", None))
        if not all(math.isfinite(v) for v in t):
            continue  # dr_wight/dr_xaphan carry NaN shield helpers
        node_trans[i] = t
        node_rot[i] = _rot3(getattr(obj, "rotation", None))
    mapping: dict[int, int] = {}
    used: set[int] = set()

    def side_of(name: str) -> str | None:
        if " L " in name:
            return "L"
        if " R " in name:
            return "R"
        return None

    def score(idx: int, side: str | None = None) -> list:
        ch = pose.channels[idx]
        kt = ch.translations[0].value  # type: ignore[union-attr]
        kt_t = (float(kt[0]), float(kt[1]), float(kt[2]))
        if not all(math.isfinite(v) for v in kt_t):
            return []
        rc = _channel_rot(ch)
        out = []
        for ni, bt in node_trans.items():
            if ni in used:
                continue
            if side is not None:
                ns = side_of(names[ni])
                if ns is not None and ns != side:
                    continue
            d = sum((kt_t[k] - bt[k]) ** 2 for k in range(3)) ** 0.5
            if d > trans_tol:
                continue
            a = _rot_deviation_deg(rc, node_rot[ni]) if rc is not None else None
            out.append((d, a, ni))
        return out

    # Pass 1 — anchors: channels whose translation identifies a bone beyond
    # any lookalike (authoring-identical offsets beat Tail1-vs-Forearm style
    # confusion, 1e-7 vs 0.09). Dust-level R/L twins never anchor.
    anchors: dict[int, int] = {}
    for idx in sorted(pose.channels):
        if not pose.channels[idx].translations:
            continue
        scored = score(idx)
        pick = _pick_candidate(scored)
        if pick is None:
            continue
        by_d = sorted(scored, key=lambda c: c[0])
        if len(by_d) == 1 or by_d[0][0] < 0.1 * by_d[1][0]:
            anchors[idx] = pick[2]
            mapping[idx] = pick[2]
            used.add(pick[2])

    # Pass 2 — contiguous KFA index runs take their side from their anchors
    # (limb runs are same-side chains: 22-26 L, 35-39 R, 48-52 L, 54-58 R).
    with_trans = sorted(idx for idx, ch in pose.channels.items() if ch.translations)
    runs: list[list[int]] = []
    for idx in with_trans:
        if runs and idx == runs[-1][-1] + 1:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    run_side: dict[int, str | None] = {}
    for ri, run in enumerate(runs):
        votes: dict[str, int] = {}
        for idx in run:
            if idx in anchors:
                s = side_of(names[anchors[idx]])
                if s is not None:
                    votes[s] = votes.get(s, 0) + 1
        run_side[ri] = max(votes, key=lambda s: votes[s]) if votes else None

    # Pass 3 — remaining members map to the nearest unused node on their
    # run's side (sideless runs stay unconstrained). Runs WITHOUT any anchor
    # (e.g. a remodel's reshuffled limb table) map nothing: their offsets
    # only coincide by accident and posing them would flail the wrong bones.
    # Anchored sideless runs (spine, tail) still resolve — their anchors are
    # translation-decisive.
    anchored = {ri for ri, run in enumerate(runs) if any(idx in anchors for idx in run)}
    sided_runs = sorted(
        (ri for ri in anchored if run_side[ri] is not None),
        key=lambda ri: runs[ri][0],
    )
    sideless_runs = sorted(
        (ri for ri in anchored if run_side[ri] is None),
        key=lambda ri: runs[ri][0],
    )
    for ri in sided_runs + sideless_runs:
        side = run_side[ri]
        for idx in runs[ri]:
            if idx in mapping:
                continue
            pick = _pick_candidate(score(idx, side))
            if pick is not None:
                mapping[idx] = pick[2]
                used.add(pick[2])

    # Pair quality: fraction of translated channels the offsets resolve. Some
    # clips are reused on rig variants whose binds differ (wolves, giants) or
    # whose channel table is shifted (the CATA boar's arms/tail): there the few
    # offset hits are coincidences. Refuse those (bind pose) instead of
    # flailing, and only use the canonical table to fill the gaps of pairs the
    # offsets already vouch for.
    translatable = {i for i, ch in pose.channels.items() if ch.translations}
    if not translatable:
        quality = len(mapping) / max(1, len(pose.channels))
    else:
        quality = len(set(mapping) & translatable) / len(translatable)

    if quality < 0.3 and translatable:
        return ({}, "none") if with_source else {}

    source = "offsets"
    if bone_names:
        by_name = {}
        for i, obj in enumerate(objs):
            if obj is None or type(obj).__name__ != "NiNode":
                continue
            if names[i].lower() in _KFA_NON_BONES:
                continue
            by_name.setdefault(_norm_bone_name(names[i]), i)
        table_hits = {}
        for idx in sorted(pose.channels):
            if idx >= len(bone_names):
                continue
            ni = by_name.get(_norm_bone_name(bone_names[idx]))
            if ni is not None:
                table_hits[idx] = ni
        if not translatable:
            # rotation-only clip: the table is the only binding available
            if len(table_hits) >= max(3, 0.5 * len(pose.channels)):
                mapping = table_hits
                source = "table"
        elif quality >= 0.7:
            # Vetted pair: the client's own index->name table wins over the
            # offsets, which can mis-bind lookalikes (Ettin's Spine1/Spine2
            # share a bind offset; BritonM1's R UpperArm offset also matches
            # Ponytail2). Drop offset hits whose node the table claims.
            claimed = set(table_hits.values())
            for idx in [i for i in mapping if i not in table_hits and mapping[i] in claimed]:
                del mapping[idx]
            mapping.update(table_hits)
            source = "table"
    if with_source:
        return mapping, ("table" if mapping and source == "table" else ("offsets" if mapping else "none"))
    return mapping


def mapping_is_vetted(pose, mapping: dict[int, int], source: str | None = None) -> bool:
    """True when the mapping is trustworthy enough to animate the figure.

    ``source == "table"`` means the engine's own ``animnode.dat`` binding was
    used for an offset-vetted pair: vetted even when the figure lacks some of
    the clip's bones (the remaining channels are semantically right).
    ``source == "offsets"`` is the fallback subset — only vetted when it
    resolved (almost) every translated channel, since a partial subset mixes
    another rig's limb motions (amazon 17/43, Bear 13/32, CATA boar 19/40:
    refused — they walk on air). When *source* is None the mapping is
    inspected: a channel bound without translation keys can only come from
    the table.
    """
    channels = pose.channels
    if not channels or not mapping:
        return False
    if source == "table":
        return True
    translatable = {i for i, ch in channels.items() if ch.translations}
    if translatable:
        covered = len(set(mapping) & translatable) >= 0.9 * len(translatable)
    else:
        covered = len(mapping) >= 0.9 * len(channels)
    if covered:
        return True
    if source is None:
        return any(not channels[idx].translations for idx in mapping)
    return False


def kfa_translation_trust(
    pose, objs, mapping: dict[int, int], trans_tol: float = 0.3, source: str | None = None
) -> set[int]:
    """KFA channels whose translation track belongs to the mapped rig.

    KFA translations are absolute locals of the rig the clip was authored on,
    and the engine applies all of them. For a **vetted** pair (same rig, see
    :func:`mapping_is_vetted`) every translated channel is trusted — the root
    track is the grounding (2HWolf/3head/Zombie feet float 1-8 units without
    it). For partial mappings the tracks may come from another rig, so a
    track is trusted only when at least one key returns to the mapped node's
    bind translation; otherwise the caller keeps the figure's bind
    translation. Rotations are unaffected — they are local per bone.
    """
    trusted: set[int] = set()
    vetted = mapping_is_vetted(pose, mapping, source)
    for idx, ni in mapping.items():
        ch = pose.channels.get(idx)
        if ch is None or not ch.translations:
            continue
        if ni < 0 or ni >= len(objs) or objs[ni] is None:
            continue
        if vetted:
            trusted.add(idx)
            continue
        bt = vec3(getattr(objs[ni], "translation", None))
        if not all(math.isfinite(v) for v in bt):
            continue
        for key in ch.translations:
            v = key.value
            if not all(math.isfinite(float(v[k])) for k in range(3)):
                continue
            d = sum((float(v[k]) - bt[k]) ** 2 for k in range(3)) ** 0.5
            if d <= trans_tol:
                trusted.add(idx)
                break
    return trusted


# A clip whose skinned pose stretches the mesh beyond this is refused (the
# viewer keeps the bind pose instead of showing exploded limbs). The mean
# ratio is the real gate: engine-exact skinning keeps sane clips at
# 1.00-1.07 while genuinely broken pairings sit at 1.14+ (player-race rigs
# with I_HM/I_HF reach 1.2-1.6). The max ratio only exists as a catastrophic
# net; single degenerate edges spike it on valid geometry (Ettin idles at 6,
# Dragon01 at 10), so it must stay loose.
STRETCH_LIMIT_MEAN = 1.12
STRETCH_LIMIT_MAX = 25.0


def skin_stretch(
    data, pose, bone_names=None, samples: int = 4, prefer_table: bool = False
) -> tuple[float, float] | None:
    """Worst skinned edge stretch of *data* posed by *pose* vs its bind.

    Reproduces the viewer's CPU skinning
    (``inv(meshWorld)·boneAnim·inv(boneBind)·meshWorld``) at up to *samples*
    clip times and returns ``(mean_ratio, max_ratio)`` over all mesh edges, or
    ``None`` when there is no mapping to test. Some rigs store skin binds that
    disagree with their node tree (AfancB by 302 units, dracolich by 392):
    the bind pose looks fine but that skinning model explodes their limbs.
    Callers refuse such clips and keep the bind pose (see STRETCH_LIMIT_*).
    """
    if pose is None or not pose.channels:
        return None
    objs: list = []
    names: list[str] = []
    children: list[list[int]] = []
    index_of: dict[int, int] = {}

    def add(obj) -> int:
        oid = id(obj)
        if oid in index_of:
            return index_of[oid]
        idx = len(objs)
        index_of[oid] = idx
        objs.append(obj)
        nm = ""
        if hasattr(obj, "name") and obj.name:
            nm = obj.name.decode("latin1")
        names.append(nm or type(obj).__name__)
        children.append([])
        return idx

    for root in data.roots:
        rid = add(root)
        todo = [(root, rid)]
        while todo:
            obj, pid = todo.pop()
            for ch in reversed(list(getattr(obj, "children", []) or [])):
                cid = add(ch)
                children[pid].append(cid)
                todo.append((ch, cid))

    mapping, source = map_kfa_to_nodes(
        objs, names, pose, bone_names=bone_names, with_source=True, prefer_table=prefer_table
    )
    if not mapping:
        return None
    trusted = kfa_translation_trust(pose, objs, mapping, source=source)

    bind: dict[int, list[list[float]]] = {}

    def walk_bind(idx: int, pw):
        m = node_local_matrix(objs[idx])
        w = mat4_mul(pw, m) if m else pw
        bind[idx] = w
        for c in children[idx]:
            walk_bind(c, w)

    walk_bind(0, mat4_identity()) if objs else None

    def posed_world(t: float) -> dict[int, list[list[float]]]:
        rot: dict[int, list[list[float]]] = {}
        trans: dict[int, tuple[float, float, float]] = {}
        for kfa_idx, node_idx in mapping.items():
            r, tr, _s = pose.sample(kfa_idx, t)
            if r is not None:
                rot[node_idx] = _quat_to_mat3(r)
            if tr is not None and kfa_idx in trusted:
                trans[node_idx] = (float(tr[0]), float(tr[1]), float(tr[2]))
        out: dict[int, list[list[float]]] = {}

        def walk(idx: int, pw):
            base = None if objs[idx] is None else node_local_matrix(objs[idx])
            if base is not None and (idx in rot or idx in trans):
                m = [row[:] for row in base]
                scale = float(getattr(objs[idx], "scale", 1.0) or 1.0)
                if idx in rot:
                    for a in range(3):
                        for b in range(3):
                            m[a][b] = rot[idx][a][b] * scale
                if idx in trans:
                    for a in range(3):
                        m[a][3] = trans[idx][a]
            else:
                m = base
            w = mat4_mul(pw, m) if m else pw
            out[idx] = w
            for c in children[idx]:
                walk(c, w)

        walk(0, mat4_identity())
        return out

    # meshes: weights, bind edges, then skinned edges per sampled time
    meshes = []
    for i, obj in enumerate(objs):
        if obj is None or type(obj).__name__ not in ("NiTriShape", "NiTriStrips"):
            continue
        if _is_skipped_mesh(names[i], getattr(obj, "flags", None)):
            continue
        shp = getattr(obj, "data", None)
        if shp is None or not getattr(shp, "vertices", None):
            continue
        si = getattr(obj, "skin_instance", None)
        if si is None:
            continue
        skin_data = getattr(si, "skin_data", None) or getattr(si, "data", None)
        bones = list(getattr(si, "bones", []) or [])
        if skin_data is None or not bones:
            continue
        partition = getattr(si, "skin_partition", None) or getattr(skin_data, "skin_partition", None)
        weights: dict[int, list[tuple[int, float]]] = {}
        if partition is not None:
            for part in getattr(partition, "skin_partition_blocks", None) or getattr(
                partition, "partitions", []
            ):
                _fill_partition(part, weights)
        else:
            _fill_weights_from_bonelist(
                skin_data, [index_of.get(id(b), -1) for b in bones], weights
            )
        joints = [index_of.get(id(b), -1) for b in bones]
        tris = list(shp.get_triangles()) or None
        if not tris:
            continue
        verts = [vec3(v) for v in shp.vertices]
        Ws = np.asarray(bind.get(i, mat4_identity()), dtype=float)
        invWs = np.linalg.inv(Ws)
        S_mats = [_skin_transform(skin_data.bone_list[k]) for k in range(len(joints))]
        meshes.append((i, joints, weights, tris, verts, Ws, invWs, S_mats))

    if not meshes:
        return None

    def edge_ratios(positions, tris, base) -> tuple[float, float]:
        p = np.asarray(positions)
        t = np.asarray(tris, dtype=int).reshape(-1, 3)
        lens = np.linalg.norm(p[t[:, 0]] - p[t[:, 1]], axis=1)
        valid = base > 1e-6
        if not valid.any():
            return 1.0, 1.0
        r = lens[valid] / base[valid]
        return float(r.mean()), float(r.max())

    def skin_all(world) -> list[list[np.ndarray]]:
        per_mesh = []
        for i, joints, weights, _tris, verts, Ws, invWs, S_mats in meshes:
            cache: dict[int, np.ndarray] = {}
            pts = []
            for vi, v in enumerate(verts):
                acc = np.zeros(3)
                tot = 0.0
                for sk, w in weights.get(vi, []):
                    if sk < 0 or sk >= len(joints):
                        continue
                    m = cache.get(sk)
                    if m is None:
                        jn = joints[sk]
                        Wb = np.asarray(bind.get(jn, mat4_identity()), dtype=float)
                        Wa = np.asarray(world.get(jn, Wb.tolist()), dtype=float)
                        # engine-exact: Mb = inv(meshWorld) * boneAnim * S
                        m = invWs @ Wa @ np.asarray(S_mats[sk], dtype=float)
                        cache[sk] = m
                    mh = m @ np.array((v[0], v[1], v[2], 1.0))
                    acc = acc + float(w) * mh[:3]
                    tot += float(w)
                pts.append(acc / tot if tot > 0 else np.array(v))
            per_mesh.append(pts)
        return per_mesh

    # Reference shape = the engine bind (Wa = boneBind), not the raw vertices:
    # for rigs whose stored bind disagrees with the node tree the two differ
    # by a rigid shift, and the gate must measure *animation* deformation.
    base_lens = []
    for mi, (i, _j, _w, tris, _v, _Ws, _invWs, _S) in enumerate(meshes):
        t = np.asarray(tris, dtype=int).reshape(-1, 3)
        pts = np.array(skin_all(bind)[mi])
        base_lens.append(np.linalg.norm(pts[t[:, 0]] - pts[t[:, 1]], axis=1))

    duration = max(pose.duration, 1e-3)
    worst_mean, worst_max = 1.0, 1.0
    times = [duration * k / max(1, samples - 1) for k in range(max(1, samples))]
    for t in times:
        positions = skin_all(posed_world(t))
        for mi, (i, _j, _w, tris, _v, _Ws, _invWs, _S) in enumerate(meshes):
            mean_r, max_r = edge_ratios(positions[mi], tris, base_lens[mi])
            worst_mean = max(worst_mean, mean_r)
            worst_max = max(worst_max, max_r)
    return worst_mean, worst_max


def _flatten_colmajor(m: list[list[float]]) -> list[float]:
    return [m[i][j] for j in range(4) for i in range(4)]


class Buffer:
    """Monolithic glTF buffer/accessors (4-byte aligned)."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self.views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []

    def _pad(self) -> None:
        while self.size % 4:
            self.chunks.append(b"\x00")
            self.size += 1

    def add(self, data: bytes, comp: int, count: int, kind: str, mn=None, mx=None) -> int:
        self._pad()
        self.views.append({"buffer": 0, "byteOffset": self.size, "byteLength": len(data)})
        acc = {
            "bufferView": len(self.views) - 1,
            "byteOffset": 0,
            "componentType": comp,
            "count": count,
            "type": kind,
        }
        if mn is not None:
            acc["min"] = mn
            acc["max"] = mx
        self.accessors.append(acc)
        self.chunks.append(data)
        self.size += len(data)
        return len(self.accessors) - 1

    def tobytes(self) -> bytes:
        return b"".join(self.chunks)


def _slot_for_name(name: str) -> str | None:
    """Map a NIF node name (e.g. 'body', 'head', 'arms') to a skin slot."""
    n = name.lower()
    for slot, keys in (
        ("Head", ("head", "helm")),
        ("Arms", ("arm", "wing", "hand", "glove")),
        ("Legs", ("leg",)),
        ("Boots", ("boot", "foot", "feet", "shoe")),
        ("Cloak", ("cloak",)),
        ("Lbody", ("lbody",)),
        ("Body", ("body", "torso", "chest")),
    ):
        if any(k in n for k in keys):
            return slot
    return None


# Meshes that never render, checked in this order. First, the NiAVObject
# hidden flag (bit 0 — same rule as DAOC_Navigator): 3ds Max biped-rig
# helpers (dozens of "Biped Object"/"Bone" boxes around the joints, e.g. 37
# of them in amb_rabbit.nif) and 6-vert export scraps all carry it, while
# real meshes do not (180/22 vs 181/133). Second, the same non-drawable name
# list DAOC_Navigator uses (collision/LOD/climb/shadow stand-ins), matched
# case-insensitively on prefix, for the rare unflagged leftovers.
SKIP_MESH_PREFIXES = (
    "biped",
    "bone",
    "collidee",
    "bounding",
    "climb",
    "!lod_cullme",
    "!visible_damaged",
    "shadowcaster",
    "far",
)


def _is_skipped_mesh(name: str, flags=None) -> bool:
    if flags is not None:
        try:
            if int(flags) & 1:
                return True
        except (TypeError, ValueError):
            pass
    return any(name.lower().startswith(p) for p in SKIP_MESH_PREFIXES)


# Per-asset orientation corrections (basename lower-case -> XYZ euler degrees),
# applied on top of the version axis. The client's asset frames are genuinely
# inconsistent and not derivable from the data alone (verified with rendered
# screenshots), so this curated, tested table is the source of truth.
ORIENTATION_OVERRIDES: dict[str, tuple[float, float, float]] = {}

# Corrections by *family*: (version, mesh-container rotation signature) ->
# XYZ euler degrees. Assets share authoring frames, so ~20 signatures cover the
# whole figures corpus; this scales far better than per-asset exceptions.
SIGNATURE_OVERRIDES: dict[tuple[int, tuple[int, ...]], tuple[float, float, float]] = {
    # horizontal canines facing +Z in NIF space; turn them to face +X like the
    # goldens (90 deg about Y). Signatures are row-major post transpose-fix.
    (0x0A010000, (0, 0, 1, -1, 0, 0, 0, -1, 0)): (0.0, 90.0, 0.0),
    (0x0A010000, (0, 0, 1, 1, 0, 0, 0, 1, 0)): (0.0, 90.0, 0.0),
}


def _container_signature(objs, children) -> tuple[int, ...] | None:
    """Rounded 3x3 rotation of the main mesh's ancestor chain (family key)."""
    parent: dict[int, int] = {}
    for i, kids in enumerate(children):
        for c in kids:
            parent[c] = i
    best_i, best_v = -1, -1
    for i, obj in enumerate(objs):
        if obj is None or type(obj).__name__ not in ("NiTriShape", "NiTriStrips"):
            continue
        data = getattr(obj, "data", None)
        n = len(getattr(data, "vertices", []) or []) if data is not None else 0
        if n > best_v:
            best_i, best_v = i, n
    if best_i < 0:
        return None
    chain = []
    cur = best_i
    while cur is not None:
        chain.append(cur)
        cur = parent.get(cur)
    r = mat4_identity()
    for a in reversed(chain[:-1]):
        m = node_local_matrix(objs[a]) if objs[a] is not None else None
        if m is not None:
            r = mat4_mul(r, m)
    return tuple(round(r[i][j]) for i in range(3) for j in range(3))


def _euler_xyz_matrix(degrees: tuple[float, float, float]) -> list[list[float]]:
    import math

    rx, ry, rz = (math.radians(v) for v in degrees)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    rot_x = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    rot_y = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rot_z = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]

    r = mul(mul(rot_x, rot_y), rot_z)
    out = mat4_identity()
    for i in range(3):
        for j in range(3):
            out[i][j] = r[i][j]
    return out


def export_gltf(
    nif_path: str,
    out_prefix: str,
    verbose: bool = True,
    emit_skinned: bool = False,
    texture: str | None = None,
    texture_data: tuple[str, bytes] | None = None,
    textures: list[tuple[str, str, bytes]] | None = None,
    pose=None,
    pose_time: float = 0.0,
    bake_pose: bool = False,
    bone_names: list[str] | None = None,
) -> dict:
    """
    emit_skinned=False (default): bind-pose geometry + skeleton, plus a
    <prefix>.skin.json sidecar (bone names + per-vertex weights) for later.
    emit_skinned=True (experimental): emits JOINTS_0/WEIGHTS_0 with
    inverseBind = inverse(exported skeleton world) — standard glTF, correct at
    the bind pose; will use the kfa index table later.
    pose + bake_pose: sample a KFA :class:`Animation` at *pose_time* and bake
    the linear-blend-skinning pose into the vertex positions/normals
    (``p = sum w * inv(meshWorld) * boneAnim * inv(boneBind) * meshWorld * v``,
    the Gamebryo ``Mb = rootParentToSkin * boneToRootParent * skinToBone``
    identity). Rotations and translations replace the bind locals; the KFA
    translations carry grounding (root bob with planted feet). The KFA->node
    map comes from the client's ``animnode.dat`` table when available, else
    from bind-translation matching (:func:`map_kfa_to_nodes`), so proportions
    survive. The viewer receives a plain posed mesh.
    """
    from .nifutil import load

    data = load(nif_path)
    # Every DAoC exporter (NetImmerse 4.x through Gamebryo 10.1) composes the
    # node tree Z-up in NIF world space: the skeleton's feet are lowest along
    # +Z and the pelvis->neck direction is +Z for bipeds, verified across the
    # figures corpus with the row-major reading (skinning identity <= 5e-03).
    # The former "early 4.x is X-down" conversion was compensating for the
    # pre-row-major transposed reading and is gone (see docs/formats.md §2).
    axis_matrix = mat4_axis()

    # ---------- node tree (DFS) ----------
    objs: list = []
    names: list[str] = []
    children: list[list[int]] = []
    index_of: dict[int, int] = {}

    def add(obj) -> int:
        oid = id(obj)
        if oid in index_of:
            return index_of[oid]
        idx = len(objs)
        index_of[oid] = idx
        objs.append(obj)
        nm = ""
        if hasattr(obj, "name") and obj.name:
            nm = obj.name.decode("latin1")
        names.append(nm or type(obj).__name__)
        children.append([])
        return idx

    root_ids: list[int] = []
    for root in data.roots:
        rid = add(root)
        root_ids.append(rid)
        todo = [(root, rid)]
        while todo:
            obj, pid = todo.pop()
            for ch in reversed(list(getattr(obj, "children", []) or [])):
                cid = add(ch)
                children[pid].append(cid)
                todo.append((ch, cid))

    # synthetic root node (axis conversion)
    synth = len(objs)
    objs.append(None)
    names.append("__axes__")
    children.append(list(root_ids))

    # Orientation overrides: by family signature first, then per-asset.
    signature = _container_signature(objs, children)
    for extra in (
        SIGNATURE_OVERRIDES.get((data.version, signature)) if signature else None,
        ORIENTATION_OVERRIDES.get(os.path.basename(nif_path).lower()),
    ):
        if extra is not None:
            axis_matrix = mat4_mul(_euler_xyz_matrix(extra), axis_matrix)

    # Bind world matrices (diagnostic + inverse bind). The synthetic axis node
    # must be included: the glTF bone nodes carry it, so the inverse bind must
    # be its inverse, else the skin applies the axis twice.
    world: dict[int, list[list[float]]] = {}

    def walk(idx: int, pw: list[list[float]]) -> None:
        m = axis_matrix if idx == synth else node_local_matrix(objs[idx])
        w = mat4_mul(pw, m) if m else pw
        world[idx] = w
        for c in children[idx]:
            walk(c, w)

    walk(synth, mat4_identity())

    # Optional KFA pose: map channels to nodes, sample TRS at pose_time.
    # Both rotations and translations replace the bind locals (absolute,
    # engine-faithful: the controller overwrites the node transform).
    # Translations apply only when the clip's own bind matches the mapped
    # node (kfa_translation_trust): clips reused across rig variants would
    # otherwise detach bones or lift the figure. Scales stay at the figure
    # bind (never animated in the corpus). Channels without keys keep bind.
    if pose is not None and bone_names is None:
        from .anims import load_bone_table

        bone_names = load_bone_table(os.path.dirname(os.path.abspath(nif_path)))
    if pose is not None:
        stretch = skin_stretch(data, pose, bone_names=bone_names)
        if stretch is not None and (
            stretch[0] > STRETCH_LIMIT_MEAN or stretch[1] > STRETCH_LIMIT_MAX
        ):
            if verbose:
                print(
                    f"  pose ignored: skin stretch {stretch[0]:.2f} (max {stretch[1]:.2f})"
                    " — rig bind mismatch, bind pose kept"
                )
            pose = None
    prefer_table = False
    if pose is not None and bone_names:
        from .anims import kfa_comes_from_gamedata

        pose_path = getattr(pose, "path", "") or ""
        client_dir = os.path.dirname(os.path.dirname(os.path.abspath(nif_path)))
        if pose_path and os.path.exists(os.path.join(client_dir, "gamedata.mpk")):
            prefer_table = kfa_comes_from_gamedata(client_dir, nif_path, pose_path)
    kfa_to_node, kfa_source = map_kfa_to_nodes(
        objs, names, pose, bone_names=bone_names, with_source=True, prefer_table=prefer_table
    )
    if pose is not None and not mapping_is_vetted(pose, kfa_to_node, kfa_source):
        if verbose:
            print("  pose ignored: partial mapping (subset of another rig) — bind pose kept")
        pose = None
        kfa_to_node = {}
    trusted_trans = (
        kfa_translation_trust(pose, objs, kfa_to_node, source=kfa_source)
        if pose is not None
        else set()
    )
    posed_rot: dict[int, list[list[float]]] = {}
    posed_trans: dict[int, tuple[float, float, float]] = {}
    if pose is not None:
        for kfa_idx, node_idx in kfa_to_node.items():
            rot, trans, _scale = pose.sample(kfa_idx, pose_time)
            if rot is not None:
                posed_rot[node_idx] = _quat_to_mat3(rot)
            if trans is not None and kfa_idx in trusted_trans:
                posed_trans[node_idx] = (float(trans[0]), float(trans[1]), float(trans[2]))

    def posed_local(idx: int) -> list[list[float]] | None:
        """Bind local matrix with the KFA rotation/translation applied."""
        base = None if objs[idx] is None else node_local_matrix(objs[idx])
        if base is None or idx not in posed_rot and idx not in posed_trans:
            return base
        m = [row[:] for row in base]
        if idx in posed_rot:
            for a in range(3):
                for b in range(3):
                    m[a][b] = posed_rot[idx][a][b] * (
                        float(getattr(objs[idx], "scale", 1.0) or 1.0)
                    )
        if idx in posed_trans:
            for a in range(3):
                m[a][3] = posed_trans[idx][a]
        return m

    posed_world: dict[int, list[list[float]]] = {}
    if bake_pose and pose is not None:

        def walk_posed(idx: int, pw: list[list[float]]) -> None:
            if idx == synth:
                w = mat4_mul(pw, axis_matrix)
            else:
                m = posed_local(idx)
                w = mat4_mul(pw, m) if m else pw
            posed_world[idx] = w
            for c in children[idx]:
                walk_posed(c, w)

        walk_posed(synth, mat4_identity())
        # NIF-space bind worlds (synthetic axis excluded) for the skinning
        # identity; the glTF `world` above includes the axis node.
        nif_bind: dict[int, list[list[float]]] = {}

        def walk_nif(idx: int, pw: list[list[float]]) -> None:
            m = None if idx == synth else node_local_matrix(objs[idx])
            w = mat4_mul(pw, m) if m else pw
            nif_bind[idx] = w
            for c in children[idx]:
                walk_nif(c, w)

        walk_nif(synth, mat4_identity())
        nif_posed: dict[int, list[list[float]]] = {}

        def walk_nif_posed(idx: int, pw: list[list[float]]) -> None:
            if idx == synth:
                w = pw
            else:
                m = posed_local(idx)
                w = mat4_mul(pw, m) if m else pw
            nif_posed[idx] = w
            for c in children[idx]:
                walk_nif_posed(c, w)

        walk_nif_posed(synth, mat4_identity())

    # ---------- meshes ----------
    buf = Buffer()
    meshes: list[dict[str, Any]] = []  # node_index, name, accessors
    skins: list[dict[str, Any]] = []  # joints, invbind (acc), node_index
    stats = {"meshes": 0, "skinned": 0, "vertices": 0, "triangles": 0, "joints": 0, "skipped": 0}
    sidecar: dict[str, Any] = {"meshes": []}

    for i, obj in enumerate(objs):
        if obj is None or type(obj).__name__ not in ("NiTriShape", "NiTriStrips"):
            continue
        if _is_skipped_mesh(names[i], getattr(obj, "flags", None)):
            stats["skipped"] += 1
            continue
        shp = getattr(obj, "data", None)
        if shp is None:
            continue
        verts = [vec3(v) for v in shp.vertices]
        norms = [vec3(v) for v in (shp.normals or [])] or None
        uvs = [_vec2(v) for v in (shp.uv_sets[0] if getattr(shp, "uv_sets", None) else [])] or None
        tris = list(shp.get_triangles()) or None

        si = getattr(obj, "skin_instance", None)
        # In these NIFs the partition lives under skin_data; si.bones = refs of the bone nodes
        skin_data = None
        if si is not None:
            skin_data = getattr(si, "skin_data", None)
            if skin_data is None:
                skin_data = getattr(si, "data", None)
        partition = None
        if si is not None:
            partition = getattr(si, "skin_partition", None)
            if partition is None and skin_data is not None:
                partition = getattr(skin_data, "skin_partition", None)
        skinned = si is not None and bool(getattr(si, "bones", []) or [])

        joints: list[int] = []
        joint_names: list[str] = []
        weights_global: dict[int, list[tuple[int, float]]] = {}

        if skinned:
            for bn in list(getattr(si, "bones", []) or []):
                joints.append(index_of.get(id(bn), -1))
                joint_names.append(bn.name.decode("latin1") if bn.name else "?")
            if partition is not None:
                for part in getattr(partition, "skin_partition_blocks", None) or getattr(
                    partition, "partitions", []
                ):
                    _fill_partition(part, weights_global)
            elif skin_data is not None:
                # legacy 4.1.0.12: weights live in bone_list, no partition
                _fill_weights_from_bonelist(skin_data, joints, weights_global)
            stats["joints"] = max(stats["joints"], len(joints))

        if bake_pose and pose is not None and skinned and weights_global:
            # CPU linear-blend skinning into NIF-local space, engine-exact:
            # Mb = inv(meshWorld) * boneAnim * skinToBone, using the *stored*
            # NiSkinData transform (S). Identical to the old inv(boneBind)
            # formula for consistent rigs, and faithful for the ones whose
            # stored bind disagrees with the node tree (AfancB: S differs by
            # 302 units; the old math stretched those limbs by 45%).
            Ws = np.asarray(nif_bind.get(i, mat4_identity()), dtype=float)
            invWs = np.linalg.inv(Ws)
            S_mats = [_skin_transform(skin_data.bone_list[k]) for k in range(len(joints))]
            Mb_cache: dict[int, np.ndarray] = {}
            Rb_cache: dict[int, np.ndarray] = {}

            def _mb(
                skin_k: int,
                _joints=joints,
                _nif_bind=nif_bind,
                _nif_posed=nif_posed,
                _S=S_mats,
                _Ws=Ws,
                _invWs=invWs,
                _Mb_cache=Mb_cache,
                _Rb_cache=Rb_cache,
            ) -> np.ndarray:
                m = _Mb_cache.get(skin_k)
                if m is None:
                    jn = _joints[skin_k] if skin_k < len(_joints) else -1
                    Wb = np.asarray(_nif_bind.get(jn, mat4_identity()), dtype=float)
                    Wa = np.asarray(_nif_posed.get(jn, Wb.tolist()), dtype=float)
                    m = _invWs @ Wa @ np.asarray(_S[skin_k], dtype=float)
                    _Mb_cache[skin_k] = m
                    _Rb_cache[skin_k] = m[:3, :3]
                return m

            baked_v: list[tuple[float, float, float]] = []
            for vi, v in enumerate(verts):
                acc = np.zeros(3)
                tot = 0.0
                for sk, w in weights_global.get(vi, []):
                    if sk < 0 or sk >= len(joints):
                        continue
                    mh = _mb(sk) @ np.array((v[0], v[1], v[2], 1.0))
                    acc = acc + float(w) * mh[:3]
                    tot += float(w)
                baked_v.append(
                    (float(acc[0] / tot), float(acc[1] / tot), float(acc[2] / tot))
                    if tot > 0
                    else v
                )
            verts = baked_v
            if norms is not None:
                baked_n = []
                for vi, n in enumerate(norms):
                    acc = np.zeros(3)
                    tot = 0.0
                    for sk, w in weights_global.get(vi, []):
                        if sk < 0 or sk >= len(joints):
                            continue
                        if sk not in Rb_cache:
                            _mb(sk)
                        acc = acc + float(w) * (Rb_cache[sk] @ np.array(n[:3]))
                        tot += float(w)
                    if tot > 0:
                        acc = acc / tot
                        l = float(np.linalg.norm(acc)) or 1.0
                        baked_n.append((float(acc[0] / l), float(acc[1] / l), float(acc[2] / l)))
                    else:
                        baked_n.append(n)
                norms = baked_n

        num_v = len(verts)
        joint_arr = np.zeros((num_v, 4), dtype=np.uint16)
        weight_arr = np.zeros((num_v, 4), dtype=np.float32)
        if skinned:
            for gv, wl in weights_global.items():
                wl = sorted(wl, key=lambda x: -x[1])[:4]
                tot = sum(w for _, w in wl) or 1.0
                for k, (j, w) in enumerate(wl):
                    joint_arr[gv, k] = max(j, 0)
                    weight_arr[gv, k] = w / tot

        # Vertices/normals stay in the NIF (Z-up) space; the axis conversion
        # (Z-up -> glTF Y-up) is applied once by the synthetic root node's
        # matrix below, so the whole model (mesh + skeleton) rotates together.
        verts_np = np.array(verts, dtype=np.float32)
        norms_np = np.array(norms, dtype=np.float32) if norms is not None else None
        acc_pos = buf.add(
            verts_np.tobytes(),
            F32,
            num_v,
            "VEC3",
            mn=verts_np.min(axis=0).round(6).tolist(),
            mx=verts_np.max(axis=0).round(6).tolist(),
        )
        acc_nrm = buf.add(norms_np.tobytes(), F32, num_v, "VEC3") if norms_np is not None else None
        acc_uv = (
            buf.add(np.array(uvs, dtype=np.float32).reshape(-1).tobytes(), F32, num_v, "VEC2")
            if uvs
            else None
        )
        acc_j = (
            buf.add(joint_arr.tobytes(), U16, num_v, "VEC4") if (skinned and emit_skinned) else None
        )
        acc_w = (
            buf.add(weight_arr.tobytes(), F32, num_v, "VEC4")
            if (skinned and emit_skinned)
            else None
        )
        acc_i = (
            buf.add(
                np.array(tris, dtype=np.uint32).reshape(-1).tobytes(), I32, len(tris) * 3, "SCALAR"
            )
            if tris
            else None
        )

        prim = {"attributes": {"POSITION": acc_pos}, "indices": acc_i}
        if acc_nrm is not None:
            prim["attributes"]["NORMAL"] = acc_nrm
        if acc_uv is not None:
            prim["attributes"]["TEXCOORD_0"] = acc_uv
        if skinned and emit_skinned:
            prim["attributes"]["JOINTS_0"] = acc_j
            prim["attributes"]["WEIGHTS_0"] = acc_w

        meshes.append({"node": i, "name": names[i], "prim": prim})
        if skinned and emit_skinned:
            # inverseBind = S * inv(meshWorld), the engine-exact form:
            # three.js computes p = boneWorld * IB * meshWorld * v and the
            # engine computes boneAnim * S * v; they match when
            # IB = S * inv(meshWorld). For consistent rigs S = inv(boneBind) *
            # meshWorld_nif, so this equals the previous inv(boneWorld).
            mesh_inv = np.linalg.inv(np.asarray(world.get(i, mat4_identity()), dtype=float))
            ib = [
                (np.asarray(_skin_transform(skin_data.bone_list[k]), dtype=float) @ mesh_inv).tolist()
                for k in range(len(joints))
            ]
            ib_arr = np.array([_flatten_colmajor(m) for m in ib], dtype=np.float32)
            acc_ib = buf.add(ib_arr.tobytes(), F32, len(ib), "MAT4")
            skins.append(
                {"node": i, "joints": joints, "invbind": acc_ib, "joint_names": joint_names}
            )
            stats["skinned"] += 1
        # sidecar: keep the raw material for later (weights, bones) even without a glTF skin
        sidecar["meshes"].append(
            {
                "name": names[i],
                "joints": joint_names,
                "vertices": _weights_to_list(weights_global, num_v),
            }
        )
        stats["meshes"] += 1
        stats["vertices"] += num_v
        stats["triangles"] += len(tris) if tris else 0

    # ---------- JSON ----------
    with open(out_prefix + ".skin.json", "w") as fh:
        json.dump(sidecar, fh, indent=1, allow_nan=False)
    if verbose and sidecar["meshes"]:
        print(f"  sidecar : {out_prefix}.skin.json")

    gltf_nodes: list[dict[str, Any]] = []
    for i in range(len(objs)):
        n: dict[str, Any] = {"name": names[i]}
        if children[i]:
            n["children"] = children[i]
        if i == synth:
            n["matrix"] = _flatten_colmajor(axis_matrix)
        else:
            m = posed_local(i) if pose is not None else node_local_matrix(objs[i])
            if m is not None and any(
                abs(m[r][c] - (1.0 if r == c else 0.0)) > 1e-6 for r in range(4) for c in range(4)
            ):
                n["matrix"] = _flatten_colmajor(m)
        gltf_nodes.append(n)

    mesh_index_by_node: dict[int, int] = {}
    for k, mesh in enumerate(meshes):
        mesh_index_by_node[mesh["node"]] = k

    # ---- textures (CS_camelot_texture extension) ----
    # DAoC figure textures are DDS (DXT) resolved from gamedata archives, not
    # standard glTF images. Embed each skin-slot DDS as a bufferView and map
    # every mesh node to its slot (by node name: body/head/arms/...); the
    # viewer loads it with three.js DDSLoader. GLTFLoader ignores the views.
    tex_list = list(textures or [])
    if texture:
        with open(texture, "rb") as fh:
            tex_list.append(("Body", os.path.basename(texture), fh.read()))
    elif texture_data is not None:
        tex_list.append(("Body", texture_data[0], texture_data[1]))

    slot_views: dict[str, tuple[int, str]] = {}
    for slot, name, data in tex_list:
        if slot in slot_views:
            continue
        buf._pad()
        vi = len(buf.views)
        buf.views.append({"buffer": 0, "byteOffset": buf.size, "byteLength": len(data)})
        buf.chunks.append(data)
        buf.size += len(data)
        slot_views[slot] = (vi, name)

    parent: dict[int, int] = {}
    for i, ch in enumerate(children):
        for c in ch:
            parent[c] = i

    def _mesh_slot(node_idx: int) -> str:
        j = node_idx
        while True:
            s = _slot_for_name(names[j])
            if s and s in slot_views:
                return s
            if j not in parent:
                break
            j = parent[j]
        return "Body" if "Body" in slot_views else next(iter(slot_views))

    mesh_ext: dict[int, dict] = {}
    for k, mesh in enumerate(meshes):
        node = mesh["node"]
        gltf_nodes[node]["mesh"] = k
        if slot_views:
            vi, nm = slot_views[_mesh_slot(node)]
            mesh_ext[k] = {"CS_camelot_texture": {"bufferView": vi, "name": nm}}

    for k, skin in enumerate(skins):
        gltf_nodes[skin["node"]]["skin"] = k

    b = buf.tobytes()
    gltf = {
        "asset": {"version": "2.0", "generator": "camelot-studio-core/0.1"},
        "scene": 0,
        "scenes": [{"nodes": [synth]}],
        "nodes": gltf_nodes,
        "meshes": [
            {
                "primitives": [m["prim"]],
                "name": m["name"],
                **({"extensions": mesh_ext[k]} if k in mesh_ext else {}),
            }
            for k, m in enumerate(meshes)
        ],
        "skins": [
            {"joints": s["joints"], "inverseBindMatrices": s["invbind"], "skeleton": synth}
            for s in skins
        ],
        "buffers": [{"byteLength": len(b), "uri": os.path.basename(out_prefix) + ".bin"}],
        "bufferViews": buf.views,
        "accessors": buf.accessors,
    }
    if slot_views:
        gltf["extensionsUsed"] = ["CS_camelot_texture"]
    with open(out_prefix + ".bin", "wb") as fh:
        fh.write(b)
    with open(out_prefix + ".gltf", "w") as fh:
        json.dump(gltf, fh, indent=1, allow_nan=False)

    # ---------- diagnostics ----------
    if verbose:
        print(f"glTF written: {out_prefix}.gltf + .bin ({len(b)} bytes)")
        print(
            f"  nodes={len(gltf_nodes)} meshes={stats['meshes']} skinned={stats['skinned']} "
            f"vertices={stats['vertices']} triangles={stats['triangles']} bones={stats['joints']}"
            + (f" skipped={stats['skipped']}" if stats["skipped"] else "")
        )
        for s in skins:
            if s["joint_names"]:
                print(
                    "  bones: "
                    + ", ".join(s["joint_names"][:24])
                    + (" …" if len(s["joint_names"]) > 24 else "")
                )
        worst, checked = 0.0, 0
        for s in skins:
            for jn in s["joints"]:
                if jn < 0 or jn not in world:
                    continue
                w = np.asarray(world[jn], dtype=float)
                prod = w @ np.linalg.inv(w)
                checked += 1
                worst = max(worst, float(np.abs(prod - np.eye(4)).max()))
        if checked:
            print(f"  invBind diagnostic: max identity deviation = {worst:.2e} ({checked} bones)")
    return {**stats, "bin_bytes": len(b)}


def _weights_to_list(weights_global: dict[int, list[tuple[int, float]]], num_v: int) -> list:
    """Per-vertex weights [(joint, weight), ...] for the JSON sidecar."""
    out = []
    for gv in range(num_v):
        out.append([[j, round(w, 6)] for j, w in weights_global.get(gv, [])])
    return out


def _fill_partition(part, weights_global: dict[int, list[tuple[int, float]]]) -> None:
    if isinstance(part, dict):
        num = part["num_vertices"]
        vmap = part["vertex_map"]
        part_bones = part["bones"]
        wm = part["vertex_weights"]
        has_bi = "bone_indices" in part
        for i in range(num):
            gv = int(vmap[i])
            bi = part["bone_indices"][i] if has_bi else list(range(len(wm[i])))
            wts = [float(w) for w in wm[i]]
            wl = weights_global.setdefault(gv, [])
            for bj, w in zip(bi, wts):
                jg = part_bones[bj] if bj < len(part_bones) else bj
                wl.append((jg, w))
        return
    # pyffi SkinPartitionBlock
    num = part.num_vertices
    vmap = list(part.vertex_map) if getattr(part, "has_vertex_map", False) else list(range(num))
    part_bones = list(part.bones or [])
    has_bi = bool(getattr(part, "has_bone_indices", False))
    for i in range(num):
        gv = int(vmap[i])
        bi = list(part.bone_indices[i]) if has_bi else [0]
        if getattr(part, "has_vertex_weights", False):
            wts = [float(w) for w in part.vertex_weights[i]]
        else:
            wts = [1.0 / len(bi)] * len(bi)
        wl = weights_global.setdefault(gv, [])
        for bj, w in zip(bi, wts):
            jg = part_bones[bj] if bj < len(part_bones) else bj
            wl.append((jg, w))


def _skin_transform(entry) -> list[list[float]]:
    """Stored NiSkinData bone transform S (skin space -> bone space).

    Row-major lists from our legacy reader, or a pyffi ``SkinTransform``
    whose Matrix33 goes through :func:`nif_rot_cell`. This is the transform
    the engine actually uses; for most files it equals
    ``inv(boneBind) * meshWorld``, but some rigs (AfancB by 302 units) store
    a different bind — see docs/formats.md §3.
    """
    t = entry["transform"] if isinstance(entry, dict) else getattr(entry, "skin_transform", None)
    m = mat4_identity()
    if isinstance(t, (tuple, list)):  # legacy reader: (row-major mat33, vec3, scale)
        r, tv, s = t
        for i in range(3):
            for j in range(3):
                m[i][j] = float(r[i][j]) * float(s)
            m[i][3] = float(tv[i])
        return m
    if t is None or not hasattr(t, "rotation"):
        return m
    rot, tv, s = t.rotation, vec3(t.translation), float(t.scale)
    for i in range(3):
        for j in range(3):
            m[i][j] = nif_rot_cell(rot, i, j) * s
        m[i][3] = tv[i]
    return m


def _fill_weights_from_bonelist(skin_data, joints, weights_global) -> None:
    """Legacy 4.1.0.12 skins store per-vertex weights in bone_list (no partition).

    Each bone_list entry's weights are (vertex_index, weight) for that bone;
    the key stored is the skin-bone index *k* (position in the bone list),
    consistent with :func:`_fill_partition`, so ``joints[k]`` resolves it to
    a node. (Previously stored the node index directly, which broke JOINTS
    indexing whenever ``emit_skinned`` was used on legacy skins.)
    """
    bl = getattr(skin_data, "bone_list", []) or []
    for k, entry in enumerate(bl):
        wts = entry["weights"] if isinstance(entry, dict) else getattr(entry, "weights", None)
        for vi, w in wts or []:
            weights_global.setdefault(int(vi), []).append((k, w))
