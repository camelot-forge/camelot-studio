"""A minimal PNG writer (8-bit RGBA, filter 0, no dependency).

The world viewer needs PNG for the textures the client ships as DDS (DXT):
the browser cannot read those. PNG is four chunks around zlib-compressed
scanlines, so it needs nothing beyond the standard library — and a copy this
small is easier to keep exact than a dependency's behaviour.
"""
from __future__ import annotations

import struct
import zlib

__all__ = ["encode_png"]

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png(width: int, height: int, rgba: bytes, level: int = 6) -> bytes:
    """PNG bytes for 8-bit RGBA pixels (row-major, ``width * height * 4``)."""
    if width <= 0 or height <= 0:
        raise ValueError(f"encode_png: bad size {width}x{height}")
    if len(rgba) != width * height * 4:
        raise ValueError(f"encode_png: {len(rgba)} bytes for {width}x{height} RGBA")
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: none
        raw += rgba[y * stride : (y + 1) * stride]
    return (
        _SIGNATURE
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(bytes(raw), level))
        + _chunk(b"IEND", b"")
    )
