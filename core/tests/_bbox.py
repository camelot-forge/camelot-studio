"""Render-truth helpers for tests: world bbox exactly as three.js would show.

For non-skinned meshes: each mesh POSITION transformed by its node world
matrix. For skinned meshes: three.js computes
``p = sum_k w_k * (boneWorld_k * invBind_k) * (meshWorld * v)`` — this module
reproduces that, so tests catch orientation/placement regressions without a
browser.
"""
from __future__ import annotations

import json
import struct

import numpy as np

_COMP = {"VEC3": 3, "VEC2": 2, "SCALAR": 1, "VEC4": 4, "MAT4": 16}
_FMT = {5126: "f", 5123: "H", 5125: "I"}


def _read(gltf_path: str, bin_path: str | None):
    with open(gltf_path) as fh:
        gltf = json.load(fh)
    with open(bin_path or gltf_path.replace(".gltf", ".bin"), "rb") as fh:
        blob = fh.read()
    return gltf, blob


def _accessor(g, blob, index, default_comp=None):
    acc = g["accessors"][index]
    view = g["bufferViews"][acc["bufferView"]]
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    n = acc["count"]
    comps = _COMP[acc["type"]]
    comp = default_comp or acc["componentType"]
    fmt = _FMT[comp]
    size = struct.calcsize(fmt)
    stride = view.get("byteStride") or comps * size
    out = np.zeros((n, comps))
    for i in range(n):
        out[i] = struct.unpack_from("<" + str(comps) + fmt, blob, offset + i * stride)
    return out


def _world_fn(g):
    parent = {}
    for i, node in enumerate(g["nodes"]):
        for child in node.get("children", []):
            parent[child] = i

    def cm(matrix):
        return np.array(matrix).reshape(4, 4).T

    def world(i):
        chain = []
        cur = i
        while cur is not None:
            chain.append(cur)
            cur = parent.get(cur)
        m = np.eye(4)
        for a in reversed(chain):
            node = g["nodes"][a]
            m = m @ (cm(node["matrix"]) if "matrix" in node else np.eye(4))
        return m

    return world


def world_bbox(gltf_path: str, bin_path: str | None = None):
    """Non-skinned world bbox -> (dims, center) as float lists."""
    g, blob = _read(gltf_path, bin_path)
    world = _world_fn(g)
    mn = np.array([1e18] * 3)
    mx = np.array([-1e18] * 3)
    for i, node in enumerate(g["nodes"]):
        if "mesh" not in node:
            continue
        for prim in g["meshes"][node["mesh"]]["primitives"]:
            pos = _accessor(g, blob, prim["attributes"]["POSITION"], 5126)
            points = (world(i) @ np.hstack([pos, np.ones((len(pos), 1))]).T).T[:, :3]
            mn = np.minimum(mn, points.min(0))
            mx = np.maximum(mx, points.max(0))
    return np.round(mx - mn, 3).tolist(), np.round((mx + mn) / 2, 3).tolist()


def skinned_world_bbox(gltf_path: str, bin_path: str | None = None):
    """three.js-exact skinned world bbox -> (dims, center)."""
    g, blob = _read(gltf_path, bin_path)
    world = _world_fn(g)
    mn = np.array([1e18] * 3)
    mx = np.array([-1e18] * 3)
    for i, node in enumerate(g["nodes"]):
        if "mesh" not in node or "skin" not in node:
            continue
        skin = g["skins"][node["skin"]]
        joints = skin["joints"]
        ibm = _accessor(g, blob, skin["inverseBindMatrices"], 5126).reshape(-1, 4, 4)
        ibm = np.transpose(ibm, (0, 2, 1))
        mesh_world = world(i)
        for prim in g["meshes"][node["mesh"]]["primitives"]:
            pos = _accessor(g, blob, prim["attributes"]["POSITION"], 5126)
            jnt = _accessor(g, blob, prim["attributes"]["JOINTS_0"], 5123).astype(int)
            wgt = _accessor(g, blob, prim["attributes"]["WEIGHTS_0"], 5126)
            out = np.zeros((len(pos), 3))
            for k in range(4):
                for vi in range(len(pos)):
                    j = joints[jnt[vi, k]]
                    skin_m = world(j) @ ibm[jnt[vi, k]]
                    out[vi] += wgt[vi, k] * (skin_m @ (mesh_world @ np.append(pos[vi], 1.0)))[:3]
            mn = np.minimum(mn, out.min(0))
            mx = np.maximum(mx, out.max(0))
    return np.round(mx - mn, 3).tolist(), np.round((mx + mn) / 2, 3).tolist()
