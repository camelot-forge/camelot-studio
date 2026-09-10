"""Camelot Studio — glTF 2.0 export of a NIF figure (bind pose, skinned).

- Axis conversion: NIF Z-up -> glTF Y-up through a synthetic root node (A).
- Skin: bones = NiSkinInstance.bones (order = NiSkinData.bone_list);
  glTF inverse bind = invBind_nif * A^T (see docs/formats.md).
- JOINTS_0/WEIGHTS_0: max 4 influences, normalized.
- Textures: not embedded in M0 (skins folder to come).
"""
from __future__ import annotations

import json
import os
from typing import Any

import numpy as np

from .nifutil import (
    mat4_axis,
    mat4_cell,
    mat4_identity,
    mat4_mul,
    node_local_matrix,
    vec3,
)

__all__ = ["export_gltf"]

I32 = 5125
F32 = 5126
U16 = 5123

A_AXIS = mat4_axis()


def _vec2(v) -> tuple[float, float]:
    try:
        return (float(v.u), float(v.v))
    except AttributeError:
        return (float(v[0]), float(v[1]))


def _apply_axis(points3) -> np.ndarray:
    arr = np.asarray(points3, dtype=np.float32)
    x, y, z = arr[:, 0], arr[:, 1], arr[:, 2]
    return np.stack([x, z, -y], axis=1)


def _m44_from_rows(rows) -> list[list[float]]:
    return [[mat4_cell(rows, i, j) for j in range(4)] for i in range(4)]


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

    def add(self, data: bytes, comp: int, count: int, kind: str,
            mn=None, mx=None) -> int:
        self._pad()
        self.views.append({"buffer": 0, "byteOffset": self.size, "byteLength": len(data)})
        acc = {"bufferView": len(self.views) - 1, "byteOffset": 0,
               "componentType": comp, "count": count, "type": kind}
        if mn is not None:
            acc["min"] = mn
            acc["max"] = mx
        self.accessors.append(acc)
        self.chunks.append(data)
        self.size += len(data)
        return len(self.accessors) - 1

    def tobytes(self) -> bytes:
        return b"".join(self.chunks)


def export_gltf(nif_path: str, out_prefix: str, verbose: bool = True,
                emit_skinned: bool = False) -> dict:
    """
    emit_skinned=False (default): bind-pose geometry + skeleton, plus a
    <prefix>.skin.json sidecar (bone names + per-vertex weights) for later.
    emit_skinned=True (experimental): emits JOINTS_0/WEIGHTS_0 with
    inverseBind = inverse(exported skeleton world) — standard glTF, correct at
    the bind pose; will use the kfa index table later.
    """
    from .nifutil import load

    data = load(nif_path)

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

    # NIF world matrices (diagnostic only)
    world: dict[int, list[list[float]]] = {}

    def walk(idx: int, pw: list[list[float]]) -> None:
        m = node_local_matrix(objs[idx])
        w = mat4_mul(pw, m) if m else pw
        world[idx] = w
        for c in children[idx]:
            walk(c, w)

    walk(synth, mat4_identity())

    # ---------- meshes ----------
    buf = Buffer()
    meshes: list[dict[str, Any]] = []       # node_index, name, accessors
    skins: list[dict[str, Any]] = []        # joints, invbind (acc), node_index
    stats = {"meshes": 0, "skinned": 0, "vertices": 0, "triangles": 0, "joints": 0}
    sidecar: dict[str, Any] = {"meshes": []}

    for i, obj in enumerate(objs):
        if obj is None or type(obj).__name__ not in ("NiTriShape", "NiTriStrips"):
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
                for part in (getattr(partition, "skin_partition_blocks", None)
                             or getattr(partition, "partitions", [])):
                    _fill_partition(part, weights_global)
            elif skin_data is not None:
                # legacy 4.1.0.12: weights live in bone_list, no partition
                _fill_weights_from_bonelist(skin_data, joints, weights_global)
            stats["joints"] = max(stats["joints"], len(joints))

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
        acc_pos = buf.add(verts_np.tobytes(), F32, num_v, "VEC3",
                          mn=verts_np.min(axis=0).round(6).tolist(),
                          mx=verts_np.max(axis=0).round(6).tolist())
        acc_nrm = buf.add(norms_np.tobytes(), F32, num_v, "VEC3") if norms_np is not None else None
        acc_uv = buf.add(np.array(uvs, dtype=np.float32).reshape(-1).tobytes(), F32, num_v, "VEC2") if uvs else None
        acc_j = buf.add(joint_arr.tobytes(), U16, num_v, "VEC4") if (skinned and emit_skinned) else None
        acc_w = buf.add(weight_arr.tobytes(), F32, num_v, "VEC4") if (skinned and emit_skinned) else None
        acc_i = buf.add(np.array(tris, dtype=np.uint32).reshape(-1).tobytes(), I32, len(tris) * 3, "SCALAR") if tris else None

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
            # inverseBind = inv(bone world) in the skeleton-root (Z-up) space;
            # the root node's A_AXIS rotates the whole skeleton to Y-up.
            ib = []
            for jn in joints:
                w = world.get(jn, mat4_identity())
                ib.append(np.linalg.inv(np.asarray(w, dtype=float)).tolist())
            ib_arr = np.array([_flatten_colmajor(m) for m in ib], dtype=np.float32)
            acc_ib = buf.add(ib_arr.tobytes(), F32, len(ib), "MAT4")
            skins.append({"node": i, "joints": joints, "invbind": acc_ib, "joint_names": joint_names})
            stats["skinned"] += 1
        # sidecar: keep the raw material for later (weights, bones) even without a glTF skin
        sidecar["meshes"].append({
            "name": names[i],
            "joints": joint_names,
            "vertices": _weights_to_list(weights_global, num_v),
        })
        stats["meshes"] += 1
        stats["vertices"] += num_v
        stats["triangles"] += len(tris) if tris else 0

    # ---------- JSON ----------
    with open(out_prefix + ".skin.json", "w") as fh:
        json.dump(sidecar, fh, indent=1)
    if verbose and sidecar["meshes"]:
        print(f"  sidecar : {out_prefix}.skin.json")

    gltf_nodes: list[dict[str, Any]] = []
    for i in range(len(objs)):
        n: dict[str, Any] = {"name": names[i]}
        if children[i]:
            n["children"] = children[i]
        if i == synth:
            n["matrix"] = _flatten_colmajor(A_AXIS)
        else:
            m = node_local_matrix(objs[i])
            if m is not None and any(
                abs(m[r][c] - (1.0 if r == c else 0.0)) > 1e-6
                for r in range(4) for c in range(4)
            ):
                n["matrix"] = _flatten_colmajor(m)
        gltf_nodes.append(n)

    mesh_index_by_node: dict[int, int] = {}
    for k, mesh in enumerate(meshes):
        mesh_index_by_node[mesh["node"]] = k

    for k, mesh in enumerate(meshes):
        gltf_nodes[mesh["node"]]["mesh"] = k

    for k, skin in enumerate(skins):
        gltf_nodes[skin["node"]]["skin"] = k

    b = buf.tobytes()
    gltf = {
        "asset": {"version": "2.0", "generator": "camelot-studio-core/0.1"},
        "scene": 0,
        "scenes": [{"nodes": [synth]}],
        "nodes": gltf_nodes,
        "meshes": [{"primitives": [m["prim"]], "name": m["name"]} for m in meshes],
        "skins": [
            {"joints": s["joints"], "inverseBindMatrices": s["invbind"], "skeleton": synth}
            for s in skins
        ],
        "buffers": [{"byteLength": len(b), "uri": os.path.basename(out_prefix) + ".bin"}],
        "bufferViews": buf.views,
        "accessors": buf.accessors,
    }
    with open(out_prefix + ".bin", "wb") as fh:
        fh.write(b)
    with open(out_prefix + ".gltf", "w") as fh:
        json.dump(gltf, fh, indent=1)

    # ---------- diagnostics ----------
    if verbose:
        print(f"glTF written: {out_prefix}.gltf + .bin ({len(b)} bytes)")
        print(f"  nodes={len(gltf_nodes)} meshes={stats['meshes']} skinned={stats['skinned']} "
              f"vertices={stats['vertices']} triangles={stats['triangles']} bones={stats['joints']}")
        for s in skins:
            if s["joint_names"]:
                print("  bones: " + ", ".join(s["joint_names"][:24]) + (" …" if len(s["joint_names"]) > 24 else ""))
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


def _weights_to_list(weights_global: dict[int, list[tuple[int, float]]],
                     num_v: int) -> list:
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


def _fill_weights_from_bonelist(skin_data, joints, weights_global) -> None:
    """Legacy 4.1.0.12 skins store per-vertex weights in bone_list (no partition).

    Each bone_list entry's weights are (vertex_index, weight) for that bone;
    the bone's canonical index is its position in the skin's bone list.
    """
    bl = getattr(skin_data, "bone_list", []) or []
    for k, entry in enumerate(bl):
        jg = joints[k] if k < len(joints) else k
        wts = entry["weights"] if isinstance(entry, dict) else getattr(entry, "weights", None)
        for vi, w in wts or []:
            weights_global.setdefault(int(vi), []).append((jg, w))