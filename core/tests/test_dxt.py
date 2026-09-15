"""Unit tests for the DXT1 decoder (synthetic blocks, no game files)."""
import struct

import pytest

from camelot.dxt import (
    decode_dxt1,
    dxt1_has_alpha,
    dxt1_to_a8r8g8b8,
    encode_a8r8g8b8,
    parse_dds_header,
)


def _block(c0: int, c1: int, bits: int = 0) -> bytes:
    return struct.pack("<HHI", c0, c1, bits)


def test_four_color_mode():
    # c0 white > c1 black: 4-color mode, all texels opaque.
    bits = 0b01_01_01_01 * 0x01010101  # every texel = palette[1] (black)
    data = _block(0xFFFF, 0x0000, bits)
    rgba = decode_dxt1(data, 4, 4)
    assert rgba[0:4] == bytes([0, 0, 0, 255])
    assert not dxt1_has_alpha(data, 4, 4)
    white = decode_dxt1(_block(0xFFFF, 0x0000, 0), 4, 4)
    assert white[0:4] == bytes([255, 255, 255, 255])


def test_three_color_mode_is_transparent():
    # c0 <= c1: palette index 3 is transparent.
    data = _block(0x0000, 0xFFFF, 0b11_11_11_11)
    assert dxt1_has_alpha(data, 4, 4)
    rgba = decode_dxt1(data, 4, 4)
    assert rgba[0:4] == bytes([0, 0, 0, 0])
    assert rgba[4:8] == bytes([0, 0, 0, 0])


def test_partial_block_at_edges():
    rgba = decode_dxt1(_block(0xFFFF, 0x0000), 2, 2)
    assert len(rgba) == 2 * 2 * 4


def test_encode_a8r8g8b8_writes_bgra_and_header():
    rgba = bytes([10, 20, 30, 255])
    dds = encode_a8r8g8b8(1, 1, rgba)
    width, height, fourcc = parse_dds_header(dds)
    assert (width, height) == (1, 1)
    assert fourcc == b""  # uncompressed
    pf_flags = struct.unpack_from("<I", dds, 80)[0]
    assert pf_flags & 0x1  # ALPHAPIXELS
    assert dds[128:132] == bytes([30, 20, 10, 255])  # BGRA


def test_dxt1_to_a8r8g8b8_only_for_alpha_sheets():
    dense = b"DDS " + b"\x00" * 124 + _block(0xFFFF, 0x0000)
    # Patch the header fourCC + plain header fields the parser reads.
    dense = bytearray(dense)
    struct.pack_into("<I", dense, 80, 0x4)
    dense[84:88] = b"DXT1"
    struct.pack_into("<II", dense, 12, 4, 4)
    assert dxt1_to_a8r8g8b8(bytes(dense)) is None

    cut = bytearray(dense)
    struct.pack_into("<HHI", cut, 128, 0x0000, 0xFFFF, 0xFFFFFFFF)
    out = dxt1_to_a8r8g8b8(bytes(cut))
    assert out is not None
    assert parse_dds_header(out)[2] == b""
    assert out[128 + 3] == 0  # first texel transparent


def test_rejects_non_dds():
    with pytest.raises(ValueError):
        parse_dds_header(b"nope")


def test_viewer_dds_rewrites_only_dxt1_alpha_sheets():
    from camelot.dxt import viewer_dds

    # Non-DDS sheets (TGA/BMP) pass through untouched.
    assert viewer_dds(b"nope") == b"nope"

    dense = bytearray(b"DDS " + b"\x00" * 124 + _block(0xFFFF, 0x0000))
    struct.pack_into("<I", dense, 80, 0x4)
    dense[84:88] = b"DXT1"
    struct.pack_into("<II", dense, 12, 4, 4)
    assert viewer_dds(bytes(dense)) == bytes(dense)  # opaque: kept compressed

    cut = bytearray(dense)
    struct.pack_into("<HHI", cut, 128, 0x0000, 0xFFFF, 0xFFFFFFFF)
    out = viewer_dds(bytes(cut))
    assert out != bytes(cut)
    assert parse_dds_header(out)[2] == b""  # re-encoded uncompressed
    assert out[128 + 3] == 0  # the cut texel keeps its alpha
