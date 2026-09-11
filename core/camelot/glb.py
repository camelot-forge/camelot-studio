"""Camelot Studio — glTF -> GLB (binary container) packing.

The exporter emits ``.gltf`` + a sibling ``.bin``. The local serve daemon
prefers a single self-contained ``.glb`` for the viewer (one HTTP response,
no sidecar fetch). GLB is a generic glTF container, not a DAoC format, so it
lives here next to the exporter rather than in the serve layer.
"""
from __future__ import annotations

import json
import struct

MAGIC = 0x46546C67  # "glTF" little-endian
VERSION = 2
CHUNK_JSON = 0x4E4F534A  # "JSON"
CHUNK_BIN = 0x004E4942  # "BIN\0"


def _pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def pack_glb(gltf: dict, bin_bytes: bytes | None = None) -> bytes:
    """Package a glTF JSON document and its buffer into a GLB byte string.

    The first buffer's ``uri`` is dropped (its bytes now travel in the BIN
    chunk). JSON is space-padded and binary zero-padded to 4-byte alignment,
    as required by the GLB spec.
    """
    doc = dict(gltf)
    if "buffers" in gltf:
        buffers = [dict(b) for b in gltf.get("buffers", [])]
        if bin_bytes is not None and buffers:
            buffers[0].pop("uri", None)
        doc["buffers"] = buffers

    json_chunk = _pad(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    chunks = [(CHUNK_JSON, json_chunk)]
    if bin_bytes is not None:
        chunks.append((CHUNK_BIN, _pad(bin_bytes, b"\x00")))

    total = 12 + sum(8 + len(data) for _, data in chunks)
    out = bytearray(struct.pack("<III", MAGIC, VERSION, total))
    for ctype, data in chunks:
        out += struct.pack("<II", len(data), ctype)
        out += data
    return bytes(out)


def unpack_glb(blob: bytes) -> tuple[dict, bytes | None]:
    """Inverse of :func:`pack_glb` (used by tests and diagnostics)."""
    magic, version, total = struct.unpack_from("<III", blob, 0)
    if magic != MAGIC or version != VERSION or total != len(blob):
        raise ValueError("not a valid GLB")
    off = 12
    doc: dict | None = None
    bin_bytes: bytes | None = None
    while off < len(blob):
        length, ctype = struct.unpack_from("<II", blob, off)
        off += 8
        data = blob[off : off + length]
        off += length
        if ctype == CHUNK_JSON:
            doc = json.loads(data.decode("utf-8"))
        elif ctype == CHUNK_BIN:
            bin_bytes = data
    if doc is None:
        raise ValueError("GLB has no JSON chunk")
    return doc, bin_bytes
