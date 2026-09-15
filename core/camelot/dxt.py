"""Camelot Studio — DXT1 (BC1) / DXT3 (BC2) decoding, for sheets with alpha.

DAoC's Cata hair sheets (``sar_f_hair01_dkbrown.dds``) are DXT1 textures whose
transparent texels cut the hair silhouette. three.js `DDSLoader` maps DXT1 to
an opaque RGB format, so those texels would render as garbage or hide the
face; untangling that is easier upstream: decode DXT1 here and re-emit an
uncompressed A8R8G8B8 DDS the loader reads with a real alpha channel.
"""
from __future__ import annotations

import struct

DDS_MAGIC = b"DDS "
_HEADER_SIZE = 128


def parse_dds_header(dds: bytes) -> tuple[int, int, bytes]:
    """(width, height, fourcc) from a DDS header (``fourcc`` may be empty)."""
    if len(dds) < _HEADER_SIZE or dds[:4] != DDS_MAGIC:
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", dds, 12)
    pf_flags = struct.unpack_from("<I", dds, 80)[0]
    fourcc = dds[84:88] if pf_flags & 0x4 else b""
    return width, height, fourcc


def _rgb565(c: int) -> tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r * 255 // 31, g * 255 // 63, b * 255 // 31)


def decode_dxt1(data: bytes, width: int, height: int) -> bytes:
    """RGBA bytes (3 components + alpha 0/255) of a DXT1 block stream."""
    out = bytearray(width * height * 4)
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            c0, c1, bits = struct.unpack_from("<HHI", data, pos)
            pos += 8
            col0 = _rgb565(c0)
            col1 = _rgb565(c1)
            if c0 > c1:
                pal = [
                    (*col0, 255),
                    (*col1, 255),
                    (*((2 * col0[i] + col1[i]) // 3 for i in range(3)), 255),
                    (*((col0[i] + 2 * col1[i]) // 3 for i in range(3)), 255),
                ]
            else:
                pal = [
                    (*col0, 255),
                    (*col1, 255),
                    (*((col0[i] + col1[i]) // 2 for i in range(3)), 255),
                    (0, 0, 0, 0),  # 1-bit transparency
                ]
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                row = (bits >> (py * 8)) & 0xFF
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    idx = (row >> (px * 2)) & 0x3
                    o = (y * width + x) * 4
                    out[o:o + 4] = bytes(pal[idx])
    return bytes(out)


def decode_dxt3(data: bytes, width: int, height: int) -> bytes:
    """RGBA bytes of a DXT3 (explicit 4-bit alpha) block stream."""
    out = bytearray(width * height * 4)
    blocks_x = (width + 3) // 4
    blocks_y = (height + 3) // 4
    pos = 0
    for by in range(blocks_y):
        for bx in range(blocks_x):
            a0, a1 = struct.unpack_from("<II", data, pos)
            pos += 8
            c0, c1, bits = struct.unpack_from("<HHI", data, pos)
            pos += 8
            col0 = _rgb565(c0)
            col1 = _rgb565(c1)
            pal = [
                (*col0, 255),
                (*col1, 255),
                (*((2 * col0[i] + col1[i]) // 3 for i in range(3)), 255),
                (*((col0[i] + 2 * col1[i]) // 3 for i in range(3)), 255),
            ]
            alpha_bits = a0 | (a1 << 32)
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    continue
                row = (bits >> (py * 8)) & 0xFF
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        continue
                    idx = (row >> (px * 2)) & 0x3
                    o = (y * width + x) * 4
                    a = (alpha_bits >> ((py * 4 + px) * 4)) & 0xF
                    out[o:o + 4] = (*pal[idx][:3], a * 17)
    return bytes(out)


def decode_dxt3_to_a8r8g8b8(dds: bytes) -> bytes | None:
    """Decode a DXT3 DDS to uncompressed RGBA (alpha is real, always needed)."""
    width, height, fourcc = parse_dds_header(dds)
    if fourcc != b"DXT3":
        return None
    return encode_a8r8g8b8(width, height, decode_dxt3(dds[_HEADER_SIZE:], width, height))


def dxt1_has_alpha(data: bytes, width: int, height: int) -> bool:
    """True when any DXT1 block uses the 3-color + transparent mode."""
    pos = 0
    for _ in range(((width + 3) // 4) * ((height + 3) // 4)):
        c0, c1 = struct.unpack_from("<HH", data, pos)
        pos += 8
        if c0 <= c1:
            return True
    return False


def encode_a8r8g8b8(width: int, height: int, rgba: bytes) -> bytes:
    """Uncompressed A8R8G8B8 DDS (stored BGRA, the order DDSLoader expects)."""
    hdr = bytearray(_HEADER_SIZE)
    hdr[0:4] = DDS_MAGIC
    struct.pack_into("<I", hdr, 4, 124)
    flags = 0x1 | 0x2 | 0x4 | 0x1000  # CAPS | HEIGHT | WIDTH | PIXELFORMAT
    struct.pack_into("<I", hdr, 8, flags)
    struct.pack_into("<I", hdr, 12, height)
    struct.pack_into("<I", hdr, 16, width)
    struct.pack_into("<I", hdr, 20, width * 4)  # pitch
    struct.pack_into("<I", hdr, 76, 32)  # pf size
    struct.pack_into("<I", hdr, 80, 0x1 | 0x40)  # ALPHAPIXELS | RGB
    struct.pack_into("<I", hdr, 88, 32)  # bit count
    struct.pack_into("<I", hdr, 92, 0x00FF0000)
    struct.pack_into("<I", hdr, 96, 0x0000FF00)
    struct.pack_into("<I", hdr, 100, 0x000000FF)
    struct.pack_into("<I", hdr, 104, 0xFF000000)
    struct.pack_into("<I", hdr, 108, 0x1000)  # DDSCAPS_TEXTURE
    bgra = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        bgra[i], bgra[i + 1], bgra[i + 2], bgra[i + 3] = b, g, r, a
    return bytes(hdr) + bytes(bgra)


def dxt1_to_a8r8g8b8(dds: bytes) -> bytes | None:
    """Decode a DXT1 DDS to uncompressed RGBA; None when it has no alpha.

    A dense (all-opaque) sheet is left untouched: the compressed texture is
    smaller and DXT1 is fine when no texel needs to be cut.
    """
    width, height, fourcc = parse_dds_header(dds)
    if fourcc != b"DXT1":
        return None
    data = dds[_HEADER_SIZE:]
    if not dxt1_has_alpha(data, width, height):
        return None
    return encode_a8r8g8b8(width, height, decode_dxt1(data, width, height))


def viewer_dds(dds: bytes) -> bytes:
    """DDS bytes three.js ``DDSLoader`` renders faithfully.

    The loader maps DXT1 to an opaque RGB format, so a sheet that cuts texels
    with the 1-bit transparency mode would paint them black. Re-encode such
    sheets to uncompressed A8R8G8B8, which the loader reads with a real alpha
    channel; DXT3/DXT5/dense-DXT1 already carry their alpha. Non-DDS bytes
    (TGA/BMP sheets) pass through untouched.
    """
    try:
        converted = dxt1_to_a8r8g8b8(dds)
    except ValueError:  # not a DDS at all
        return dds
    return converted if converted is not None else dds
