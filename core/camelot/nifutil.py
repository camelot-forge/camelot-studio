"""Camelot Studio — NIF/KFA read/write helpers on top of pyffi.

Reading: tries pyffi first; meshed NetImmerse 4.x files that pyffi cannot
parse fall back to the legacy reader (`camelot.legacy`). Writing is
pyffi-only for now (legacy write is read-only).
"""

from __future__ import annotations

import math
import os

from pyffi.formats.nif import NifFormat

__all__ = [
    "AXIS_CONVERSION",
    "CamelotFormatError",
    "block_types",
    "load",
    "mat4_axis",
    "mat4_cell",
    "mat4_identity",
    "mat4_mul",
    "mat33_cell",
    "nif_rot_cell",
    "node_local_matrix",
    "sniff_version",
    "store",
    "vec3",
    "version_bits",
]


class CamelotFormatError(ValueError):
    """Raised when a DAoC-format file cannot be read or written."""


def load(path: str) -> NifFormat.Data:
    data = NifFormat.Data()
    import contextlib
    import io

    err = io.StringIO()
    try:
        with open(path, "rb") as fh, contextlib.redirect_stderr(err):
            data.read(fh)
    except Exception as e:
        detail = (err.getvalue().strip().splitlines() or [""])[-1]
        ver = sniff_version(path)
        if "NetImmerse File Format" in ver:
            # pyffi 2.2.3 cannot parse meshed 4.x files -> legacy reader
            from . import legacy

            try:
                return legacy.load_legacy(path)
            except Exception as le:  # noqa: BLE001
                raise CamelotFormatError(
                    f"{os.path.basename(path)}: pyffi failed "
                    f"({type(e).__name__}: {detail[:80]}) and legacy failed "
                    f"({type(le).__name__}: {le})"
                ) from e
        raise CamelotFormatError(
            f"{os.path.basename(path)}: pyffi parse failed "
            f"(header: {ver}) — {type(e).__name__}: {e} ; stderr: {detail[:120]}"
        ) from e
    return data


def sniff_version(path: str) -> str:
    """Format version from the text header (reads only the first bytes)."""
    with open(path, "rb") as fh:
        head = fh.read(256)
    s = head.split(b"\n", 1)[0].decode("latin1", "replace")
    return s if ("File Format" in s) else "?"


def store(data: NifFormat.Data, path: str) -> None:
    from .legacy import is_legacy

    if is_legacy(data):
        raise CamelotFormatError(
            f"{os.path.basename(path)}: legacy (4.x) write is not implemented yet"
        )
    with open(path, "wb") as fh:
        data.write(fh)


def block_types(data) -> list[str]:
    return [type(b).__name__ for b in data.blocks]


def version_bits(data) -> tuple[int, int]:
    return data.version, len(data.blocks)


# Axis conversion matrix: NIF (Z-up, every DAoC era) -> glTF (Y-up).
# -90 deg about X: (x, y, z) -> (x, z, -y).
AXIS_CONVERSION = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
)


def vec3(v) -> tuple[float, float, float]:
    """Vecteur 3 (pyffi Vector3 ou tuple) -> tuple de floats."""
    if v is None:
        return (0.0, 0.0, 0.0)
    try:
        return (float(v.x), float(v.y), float(v.z))
    except AttributeError:
        return (float(v[0]), float(v[1]), float(v[2]))


def mat33_cell(r, i: int, j: int) -> float:
    """Cell of a 3x3 matrix (pyffi Matrix33 or list of lists).

    Generic accessor, no convention change: ``(i, j)`` returns row *i*,
    column *j* as stored in the object.
    """
    for name in (f"m_{i + 1}{j + 1}", f"m{i}{j}"):
        val = getattr(r, name, None)
        if val is not None:
            return float(val)
    return float(r[i][j])


def nif_rot_cell(r, i: int, j: int) -> float:
    """True rotation cell for a NIF-sourced rotation (pyffi side).

    All DAoC exporters (NetImmerse 4.x through Gamebryo 10.1) write Matrix33
    row-major while pyffi's nif.xml describes column-major storage (``Stored
    in OpenGL column-major format``), so pyffi assigns each float to the
    transposed member (file's m12 lands in m_21). Quaternion keys (wxyz) are
    unaffected, which makes the mismatch visible: without this transpose the
    Gamebryo skinning identity ``inv(meshWorld) * boneWorld * skinToBone``
    misses by ~56 units on Boar.NIF (10.1) and ~70 on Ettin.NIF (4.2.1); with
    it the identity holds (5e-06 and 5e-03). The legacy reader converts to
    true row-major lists at the source, which pass through here unchanged.
    (pyffi 4.x matrices only occur in KFA channel nodes, whose rotations are
    never consumed — KFAs animate through quaternions.)
    """
    for name in (f"m_{j + 1}{i + 1}", f"m{j}{i}"):
        val = getattr(r, name, None)
        if val is not None:
            return float(val)
    return float(r[i][j])


def mat4_cell(m, i: int, j: int) -> float:
    """Cellule d'une matrice 4x4 (Matrix44 pyffi ou liste de listes)."""
    for name in (f"m_{i + 1}{j + 1}", f"m{i}{j}"):
        val = getattr(m, name, None)
        if val is not None:
            return float(val)
    return float(m[i][j])


def mat4_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Produit de deux matrices 4x4 (listes de lignes)."""
    return [
        [
            a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j] + a[i][3] * b[3][j]
            for j in range(4)
        ]
        for i in range(4)
    ]


def mat4_identity() -> list[list[float]]:
    return [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0], [0, 0, 0, 1.0]]


def mat4_axis() -> list[list[float]]:
    """Matrice 4x4 de la conversion d'axes (rotation pure)."""
    return _mat4_from3(AXIS_CONVERSION)


def _mat4_from3(conv) -> list[list[float]]:
    m = mat4_identity()
    for i in range(3):
        for j in range(3):
            m[i][j] = conv[i][j]
    return m


def node_local_matrix(node) -> list[list[float]] | None:
    """Local 4x4 matrix of an NiNode (T * R * S), or None if not transformable.

    NIF rotations go through :func:`nif_rot_cell` (transpose fix for DAoC
    row-major files); lists built by our own code (axis matrices, tests)
    pass through unchanged. Non-finite values (dr_wight/dr_xaphan carry NaN
    on unused shield helper nodes) are repaired to identity/zero — glTF JSON
    must never see NaN.
    """
    if type(node).__name__ not in ("NiNode", "NiTriShape", "NiTriStrips"):
        return None
    t = vec3(getattr(node, "translation", None))
    r = getattr(node, "rotation", None)
    s = float(getattr(node, "scale", 1.0) or 1.0)
    if not math.isfinite(s):
        s = 1.0
    m = mat4_identity()
    for i in range(3):
        for j in range(3):
            if r is not None:
                m[i][j] = nif_rot_cell(r, i, j) * s
            else:
                m[i][j] = s if i == j else 0.0
            if not math.isfinite(m[i][j]):
                m[i][j] = 1.0 if i == j else 0.0
        m[i][3] = t[i] if math.isfinite(t[i]) else 0.0
    return m
