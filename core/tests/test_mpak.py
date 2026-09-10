"""Unit tests for the MPAK reader/writer (no game files needed).

The reader is validated against hand-crafted archive bytes (an independent
construction of the documented layout) so the writer is not tested against
its own reader only. The writer is then round-trip checked and its header
fields verified.
"""
import struct
import zlib

import pytest

from camelot.mpak import (
    MpakArchive,
    MpakError,
    build_header,
    list_mpak,
    write_mpak,
)

HEADER_SIZE = 21
RECORD_SIZE = 0x11C
NAME_SLOT = 0x110


def _hand_made_archive() -> bytes:
    """Build a small MPAK by hand (independent of ``write_mpak``).

    Two entries: ``a.txt`` ("hello") and ``b.bin`` (4 raw bytes). Directory
    offsets are base-relative to the start of the data streams.
    """
    name = b"handmade.mpk"
    s1 = zlib.compress(name)

    data_a = zlib.compress(b"hello")
    data_b = zlib.compress(bytes([0, 1, 2, 255]))
    rel_a, rel_b = 0, len(data_a)

    dir_bytes = bytearray()
    for nb, rel in ((b"a.txt", rel_a), (b"b.bin", rel_b)):
        rec = bytearray(RECORD_SIZE)
        rec[: len(nb)] = nb
        struct.pack_into("<I", rec, NAME_SLOT, rel)
        dir_bytes += rec
    s2 = zlib.compress(bytes(dir_bytes))

    header = build_header(len(s1), 2)
    return header + s1 + s2 + data_a + data_b


def test_read_hand_made_archive(tmp_path):
    p = tmp_path / "hand.mpk"
    p.write_bytes(_hand_made_archive())
    a = MpakArchive(str(p))
    try:
        assert a.name == "handmade.mpk"
        assert sorted(a.entries) == ["a.txt", "b.bin"]
        assert a.read("a.txt") == b"hello"
        assert a.read("b.bin") == bytes([0, 1, 2, 255])
        assert a.read("A.TXT") == b"hello"  # case-insensitive
    finally:
        a.close()


def test_read_unknown_entry(tmp_path):
    p = tmp_path / "hand.mpk"
    p.write_bytes(_hand_made_archive())
    a = MpakArchive(str(p))
    try:
        with pytest.raises(MpakError):
            a.read("nope.txt")
    finally:
        a.close()


def test_rejects_non_mpak(tmp_path):
    p = tmp_path / "not.mpk"
    p.write_bytes(b"NOTMPAK" + b"\0" * 64)
    with pytest.raises(MpakError):
        MpakArchive(str(p))


def test_write_roundtrip(tmp_path):
    out = tmp_path / "out.mpk"
    entries = [("one.txt", b"first entry"), ("two.bin", bytes(range(32))), ("Empty", b"")]
    write_mpak(str(out), "RoundTrip.mpk", entries)
    name, names = list_mpak(str(out))
    assert name == "RoundTrip.mpk"
    # entry names are exposed lower-cased (the client is case-insensitive)
    assert names == ["one.txt", "two.bin", "empty"]
    a = MpakArchive(str(out))
    try:
        assert a.read("one.txt") == b"first entry"
        assert a.read("two.bin") == bytes(range(32))
        assert a.read("Empty") == b""
    finally:
        a.close()


def test_written_header_field(tmp_path):
    out = tmp_path / "hdr.mpk"
    write_mpak(str(out), "hdr.mpk", [("x", b"data")])
    blob = out.read_bytes()
    assert blob[:4] == b"MPAK"
    assert len(blob[:HEADER_SIZE]) == HEADER_SIZE
    h = blob[4:HEADER_SIZE]
    assert h[0] == 0x02
    assert h[7:9] == b"\x06\x07"
    assert h[10:13] == b"\x09\x0a\x0b"
    assert h[14:17] == b"\x0d\x0e\x0f"
    # header byte 13 == compressed name-stream length + 8
    s1 = zlib.compress(b"hdr.mpk")
    assert h[9] == len(s1) + 8


def test_long_name_rejected(tmp_path):
    out = tmp_path / "long.mpk"
    with pytest.raises(MpakError):
        write_mpak(str(out), "x.mpk", [("n" * NAME_SLOT, b"d")])