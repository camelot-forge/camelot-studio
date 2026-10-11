"""PCX images: the zone maps (`terrain.pcx`, `offset.pcx`, `water.pcx`, …).

The client stores each zone's heightmap as an 8-bit, one-plane, RLE-compressed
PCX — a 1990 format it never outgrew (measured 2026-10-11: `terrain.pcx` in
`zones/Zone000/dat000.mpk` is 256×256, 8 bits, RLE). Only that shape is
decoded: another depth or plane count is refused with its header named,
because a guessed heightmap would misplace every object an editor puts down.
"""
from __future__ import annotations

import struct

__all__ = ["PcxError", "read"]

#: What the client ships for the zone maps; anything else is not this format.
_MANUFACTURER = 0x0A
_ENCODING_RLE = 1


class PcxError(ValueError):
    """Raised when the bytes are not a decodable 8-bit PCX."""


def read(data: bytes) -> tuple[int, int, bytes]:
    """(width, height, pixels) — row-major, one byte a pixel, padding removed."""
    if len(data) < 128:
        raise PcxError("not a PCX image: shorter than the 128-byte header")
    manufacturer, _version, encoding, bpp = struct.unpack_from("<BBBB", data, 0)
    if manufacturer != _MANUFACTURER:
        raise PcxError(f"not a PCX image: manufacturer 0x{manufacturer:02x}")
    if bpp != 8:
        raise PcxError(f"only 8-bit PCX images are read here, this one is {bpp}-bit")
    if encoding != _ENCODING_RLE:
        raise PcxError(f"only RLE-compressed PCX images are read here (encoding {encoding})")
    xmin, ymin, xmax, ymax = struct.unpack_from("<HHHH", data, 4)
    nplanes = data[65]
    bytes_per_line = struct.unpack_from("<H", data, 66)[0]
    if nplanes != 1:
        raise PcxError(f"only one-plane PCX images are read here, this one has {nplanes}")
    width = xmax - xmin + 1
    height = ymax - ymin + 1
    if width <= 0 or height <= 0:
        raise PcxError(f"empty PCX image ({width}x{height})")
    if bytes_per_line < width:
        raise PcxError(f"bytes_per_line {bytes_per_line} is smaller than the width {width}")

    out = bytearray(width * height)
    offset = 128
    for row in range(height):
        decoded = bytearray()
        while len(decoded) < bytes_per_line:
            if offset >= len(data):
                raise PcxError(f"truncated: row {row} of {height} is incomplete")
            value = data[offset]
            offset += 1
            if (value & 0xC0) == 0xC0:
                count = value & 0x3F
                if count == 0:
                    raise PcxError(f"row {row}: a run of zero bytes")
                if offset >= len(data):
                    raise PcxError(f"truncated: a run at row {row} has no value")
                decoded.extend([data[offset]] * count)
                offset += 1
            else:
                decoded.append(value)
        out[row * width:(row + 1) * width] = decoded[:width]
    return width, height, bytes(out)
