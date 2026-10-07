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
    decode_header,
    list_mpak,
    write_mpak,
)

HEADER_SIZE = 21
RECORD_SIZE = 0x11C
NAME_SLOT = 0x110


def _stream_lengths(blob: bytes) -> tuple[int, int, bytes]:
    """(s1, s2, s2 bytes) of a full archive blob, measured from the file."""
    pos = HEADER_SIZE
    d = zlib.decompressobj()
    d.decompress(blob[pos:])
    s1 = len(blob) - pos - len(d.unused_data)
    pos += s1
    d = zlib.decompressobj()
    d.decompress(blob[pos:])
    s2 = len(blob) - pos - len(d.unused_data)
    return s1, s2, blob[pos : pos + s2]


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

    header = build_header(len(s1), len(s2), zlib.crc32(s2) & 0xFFFFFFFF, 2)
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


def test_written_header_and_record_fields(tmp_path):
    out = tmp_path / "hdr.mpk"
    write_mpak(str(out), "hdr.mpk", [("x", b"data")])
    blob = out.read_bytes()
    assert blob[:4] == b"MPAK"
    h = blob[4:HEADER_SIZE]
    assert h[0] == 0x02
    assert h[7:9] == b"\x06\x07"
    assert h[10:13] == b"\x09\x0a\x0b"
    assert h[14:17] == b"\x0d\x0e\x0f"

    # the directory stream bytes, and the record they must describe
    s1 = zlib.compress(b"hdr.mpk")
    data_comp = zlib.compress(b"data")
    rec = bytearray(RECORD_SIZE)
    rec[:1] = b"x"
    struct.pack_into(
        "<IIIII", rec, NAME_SLOT - 8,
        0, 4, 0, len(data_comp), zlib.crc32(data_comp) & 0xFFFFFFFF,
    )
    s2 = zlib.compress(bytes(rec))
    assert blob[HEADER_SIZE + len(s1) : HEADER_SIZE + len(s1) + len(s2)] == s2

    decoded = decode_header(blob[:HEADER_SIZE])
    assert decoded["name_comp_len"] == len(s1)
    assert decoded["dir_comp_len"] == len(s2)
    assert decoded["dir_crc32"] == zlib.crc32(s2) & 0xFFFFFFFF
    assert decoded["num_entries"] == 1


def test_long_name_rejected(tmp_path):
    out = tmp_path / "long.mpk"
    with pytest.raises(MpakError):
        write_mpak(str(out), "x.mpk", [("n" * NAME_SLOT, b"d")])

# --- header carriers: decoded from the retail corpus ------------------------

def test_header_count_carrier_round_trips():
    """The retail corpus settles it: the count carrier is
    `(entries ^ 12) & 0xFF`; an earlier reading called it "not a plain
    count" and the writer emitted a placeholder that never matched."""
    for n in (0, 1, 3, 5, 12, 64, 151, 164, 255):
        header = build_header(10, 41, 0, n)
        assert header[17] == (n ^ 12) & 0xFF, n
        assert decode_header(header)["num_entries"] == n


def test_header_carriers_describe_the_streams(client_dir):
    """Check the decoded carriers against the archives themselves rather
    than a recalled sample: the client reads the stream lengths from the
    header, so a carrier that disagrees with the streams breaks in-game
    with no message. A client update that changes the convention fails
    here instead of at the loading screen."""
    import glob
    import os

    paths = sorted(glob.glob(os.path.join(client_dir, "**", "*.mpk"), recursive=True))
    paths += sorted(glob.glob(os.path.join(client_dir, "**", "*.npk"), recursive=True))
    assert paths, "no archives to check"
    # a spread rather than the first N: the sample should not be one directory
    step = max(1, len(paths) // 40)
    checked = 0
    for path in paths[::step]:
        blob = open(path, "rb").read()
        s1, s2, s2b = _stream_lengths(blob)
        decoded = decode_header(blob[:HEADER_SIZE])
        assert decoded["name_comp_len"] == s1, path
        assert decoded["dir_comp_len"] == s2, path
        assert decoded["dir_crc32"] == zlib.crc32(s2b) & 0xFFFFFFFF, path
        archive = MpakArchive(path)
        try:
            assert decoded["num_entries"] == len(archive.entries), path
        finally:
            archive.close()
        checked += 1
    assert checked >= 20


def test_rewriting_recomputes_the_header_and_carries_the_record_field(client_dir, tmp_path):
    """Rewriting recompresses the streams, so the carriers must be
    recomputed — preserving them shipped archives the client refuses (the
    client trusts the header, measured in test A of the setup guide). The
    version/constant bytes and the undecoded per-entry record field are
    carried across: a mod changes a table, not the container."""
    import glob
    import os

    from camelot.mpak import MpakArchive

    source = min(glob.glob(os.path.join(client_dir, "figures", "fig3", "*.mpk")))
    archive = MpakArchive(source)
    try:
        entries = [(name, archive.read(name)) for name in archive.entries]
        unknown = archive.unknown_fields()
        out = tmp_path / "rewritten.mpk"
        write_mpak(
            str(out), archive.name, entries, template=archive.header, unknown=unknown
        )
        source_header = archive.header
    finally:
        archive.close()
    blob = out.read_bytes()
    assert blob[4] == source_header[4]  # version preserved
    s1, s2, s2b = _stream_lengths(blob)
    decoded = decode_header(blob[:HEADER_SIZE])
    assert decoded["name_comp_len"] == s1
    assert decoded["dir_comp_len"] == s2
    assert decoded["dir_crc32"] == zlib.crc32(s2b) & 0xFFFFFFFF
    assert decoded["num_entries"] == len(entries)
    after = MpakArchive(str(out))
    try:
        assert after.unknown_fields() == unknown
    finally:
        after.close()


def test_a_rewritten_archive_reports_the_right_entry_count_byte(client_dir, tmp_path):
    import glob
    import os

    from camelot.mpak import MpakArchive

    source = min(glob.glob(os.path.join(client_dir, "figures", "fig3", "*.mpk")))
    archive = MpakArchive(source)
    try:
        entries = [(name, archive.read(name)) for name in archive.entries]
        count = len(entries)
        out = tmp_path / "rewritten.mpk"
        write_mpak(str(out), archive.name, entries)
    finally:
        archive.close()
    assert out.read_bytes()[17] == (count ^ 12) & 0xFF


def test_repack_recomputes_the_header_and_replaces_one_entry(client_dir, tmp_path):
    """The mod path: rewrite an archive, swapping one entry's content.

    Used first to prove the container is accepted with content unchanged,
    then to change a table. The carriers describe the rewritten streams;
    the undecoded record field of every entry is carried across.
    """
    import glob
    import os

    from camelot.mpak import MpakArchive, repack_mpak

    source = min(glob.glob(os.path.join(client_dir, "figures", "fig3", "*.mpk")))
    original = MpakArchive(source)
    try:
        name = original.entries[0]
        out = tmp_path / "repacked.mpk"
        repack_mpak(source, str(out), {name: b"replaced"})
        unknown = original.unknown_fields()
    finally:
        original.close()

    blob = out.read_bytes()
    s1, s2, s2b = _stream_lengths(blob)
    decoded = decode_header(blob[:HEADER_SIZE])
    assert decoded["name_comp_len"] == s1
    assert decoded["dir_comp_len"] == s2
    assert decoded["dir_crc32"] == zlib.crc32(s2b) & 0xFFFFFFFF
    after = MpakArchive(str(out))
    try:
        assert decoded["num_entries"] == len(after.entries)
        assert after.unknown_fields() == unknown
        assert after.read(name) == b"replaced"
    finally:
        after.close()


def test_repack_refuses_an_entry_the_archive_does_not_have(tmp_path):
    from camelot.mpak import MpakError, repack_mpak, write_mpak

    src = tmp_path / "s.mpk"
    write_mpak(str(src), "s.mpk", [("a", b"1")])
    with pytest.raises(MpakError, match="no such entry"):
        repack_mpak(str(src), str(tmp_path / "o.mpk"), {"missing": b"x"})
