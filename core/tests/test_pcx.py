"""PCX decoding: the zone heightmaps (8-bit, one plane, RLE).

The format facts are the client's: `terrain.pcx` in `zones/Zone000/dat000.mpk`
measured 256×256, 8 bits, RLE (2026-10-11). The corpus test at the end reads
it through the same path `camelot.zones` will.
"""
import struct

import pytest

from camelot import pcx


def _rle(data: bytes) -> bytes:
    """RLE-encode one scanline: runs of 3+, literals otherwise (the PCX rule)."""
    out = bytearray()
    index = 0
    while index < len(data):
        run = 1
        while (index + run < len(data) and data[index + run] == data[index]
               and run < 63):
            run += 1
        if run >= 3 or data[index] & 0xC0 == 0xC0:
            out.append(0xC0 | run)
            out.append(data[index])
            index += run
        else:
            out.append(data[index])
            index += 1
    return bytes(out)


def _pcx(rows, *, width=None, bytes_per_line=None, manufacturer=0x0A, version=5,
         encoding=1, bpp=8, nplanes=1, body=None) -> bytes:
    width = width if width is not None else len(rows[0])
    height = len(rows)
    header = bytearray(128)
    struct.pack_into("<BBBB", header, 0, manufacturer, version, encoding, bpp)
    struct.pack_into("<HHHH", header, 4, 0, 0, width - 1, height - 1)
    struct.pack_into("<HH", header, 12, 72, 72)
    header[65] = nplanes
    struct.pack_into("<H", header, 66,
                     bytes_per_line if bytes_per_line is not None else width)
    if body is None:
        body = b"".join(_rle(row) for row in rows)
    return bytes(header) + body


def test_a_literal_scanline_decodes():
    width, height, pixels = pcx.read(_pcx([bytes([1, 2, 3, 4])]))
    assert (width, height) == (4, 1)
    assert pixels == bytes([1, 2, 3, 4])


def test_runs_expand():
    width, height, pixels = pcx.read(_pcx([bytes([7] * 5 + [9])]))
    assert (width, height) == (6, 1)
    assert pixels == bytes([7] * 5 + [9])


def test_rows_padded_to_bytes_per_line_are_trimmed():
    # three pixels, a line padded to four: the pad byte is not a pixel
    body = _rle(bytes([1, 2, 3, 0]))
    width, height, pixels = pcx.read(
        _pcx([bytes([1, 2, 3])], bytes_per_line=4, body=body))
    assert (width, height) == (3, 1)
    assert pixels == bytes([1, 2, 3])


def test_a_literal_byte_with_high_bits_is_escaped():
    # 0xC1 as a literal must travel as 0xC1 0xC1 (a run of one), never bare
    _width, _height, pixels = pcx.read(_pcx([bytes([0xC1, 0x00, 0xFF])]))
    assert pixels == bytes([0xC1, 0x00, 0xFF])


def test_the_heightmap_shape_the_client_ships():
    rows = [bytes([i % 256 for i in range(256)]) for _ in range(3)]
    width, height, pixels = pcx.read(_pcx(rows, width=256))
    assert (width, height) == (256, 3)
    assert len(pixels) == 256 * 3


@pytest.mark.parametrize("kwargs, match", [
    ({"manufacturer": 0x00}, "manufacturer"),
    ({"bpp": 4}, "8-bit"),
    ({"nplanes": 3}, "one-plane"),
    ({"encoding": 0}, "RLE"),
])
def test_a_shape_the_client_never_ships_is_refused(kwargs, match):
    with pytest.raises(pcx.PcxError, match=match):
        pcx.read(_pcx([bytes([1, 2])], **kwargs))


def test_a_truncated_body_is_refused():
    body = _rle(bytes([1, 2, 3, 4]))
    with pytest.raises(pcx.PcxError, match="truncated"):
        pcx.read(_pcx([bytes([1, 2, 3, 4])], body=body[:-2]))


def test_a_short_header_is_refused():
    with pytest.raises(pcx.PcxError, match="128-byte header"):
        pcx.read(b"MZ" + b"\0" * 10)


def test_a_zero_run_is_refused():
    with pytest.raises(pcx.PcxError, match="run of zero"):
        pcx.read(_pcx([bytes([1, 2])], body=bytes([0xC0, 0x01])))


def test_a_line_shorter_than_the_width_is_refused():
    with pytest.raises(pcx.PcxError, match="bytes_per_line"):
        pcx.read(_pcx([bytes([1, 2, 3])], bytes_per_line=2, body=b""))


@pytest.mark.usefixtures("client_dir")
def test_the_real_zone_heightmap_decodes(client_dir):
    """The measured shape: 256×256, 8-bit, and a heightmap that varies."""
    import os

    from camelot.mpak import MpakArchive

    archive_path = os.path.join(client_dir, "zones", "Zone000", "dat000.mpk")
    if not os.path.isfile(archive_path):
        pytest.skip("no zone000 in this corpus")
    archive = MpakArchive(archive_path)
    try:
        raw = archive.read("terrain.pcx")
    finally:
        archive.close()
    width, height, pixels = pcx.read(raw)
    assert (width, height) == (256, 256)
    assert len(set(pixels)) > 8, "a flat map would be a decoding bug"
