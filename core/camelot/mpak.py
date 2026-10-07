"""Camelot Studio — MPAK archive reader and writer.

MPAK (magic ``MPAK``, extensions .mpk/.npk) is the archive format of the
DAoC client (zones, fig3 pieces, gamedata tables). Layout, verified on the
retail corpus:

    [21-byte header]   magic "MPAK" + 17 bytes of metadata
    [zlib stream]      archive name (the client's internal, case-insensitive
                       lower-case name)
    [zlib stream]      directory: N records of 0x11C bytes — the entry name
                       NUL-terminated within the first 0x110 bytes, then a
                       u32 data offset at 0x110 (bytes 0x114..0x11C unused)
    [zlib streams]     one per entry, addressed from ``base + offset`` where
                       ``base`` = position right after the directory stream

Directory offsets are **base-relative** (base = end of the directory
stream), not absolute — confirmed on the retail corpus.

Header bytes (after the magic), see docs/formats.md §5:

    byte  4        0x02                             (constant)
    bytes 5..10     6-byte checksum                 (*open*: not decoded)
    bytes 11,12     0x06 0x07                       (constant)
    byte  13        len(name_stream_compressed) + 8 (decoded)
    bytes 14..16    0x09 0x0a 0x0b                  (constant)
    byte  17        checksum byte                   (*open*: a deterministic
                                                    function of the entry
                                                    count, not a plain count)
    bytes 18..20    0x0d 0x0e 0x0f                  (constant)

The two *open* checksum fields are written with a deterministic placeholder
until their algorithm is decoded. Our reader (like mharj's dempak.py) skips
the header past the magic, so writer round-trips prove the structural bytes;
whether the game validates the checksums needs in-game confirmation.
"""
from __future__ import annotations

import os
import struct
import zlib

MAGIC = b"MPAK"
HEADER_SIZE = 21
RECORD_SIZE = 0x11C
NAME_SLOT = 0x110


class MpakError(ValueError):
    """Raised when an MPAK archive cannot be read or written."""


def _read_stream(fh) -> bytes:
    """Decompress one zlib stream from *fh*, leaving it after the stream."""
    d = zlib.decompressobj()
    out = bytearray()
    while True:
        chunk = fh.read(65536)
        if not chunk:
            out += d.decompress(b"x")
            break
        out += d.decompress(chunk)
        if d.unused_data:
            fh.seek(-len(d.unused_data), os.SEEK_CUR)
            break
    return bytes(out)


class MpakArchive:
    """A parsed MPAK archive: name, directory, lazy entry reads."""

    def __init__(self, path: str):
        self.path = path
        # keep the handle open for lazy per-entry reads (not a context manager)
        self._fh = open(path, "rb")  # noqa: SIM115
        try:
            self.header = self._fh.read(HEADER_SIZE)
            if self.header[:4] != MAGIC:
                raise MpakError(f"{os.path.basename(path)}: not an MPAK archive")
            self.name = _read_stream(self._fh).decode("latin1")
            dirdata = _read_stream(self._fh)
        except MpakError:
            self._fh.close()
            raise
        self._base = self._fh.tell()
        self._directory: dict[str, int] = {}
        off = 0
        while off + RECORD_SIZE <= len(dirdata):
            end = dirdata.find(b"\0", off, off + NAME_SLOT)
            if end < 0:
                break
            fname = dirdata[off:end].decode("latin1")
            (rel,) = struct.unpack("<I", dirdata[off + NAME_SLOT : off + NAME_SLOT + 4])
            self._directory[fname.lower()] = rel
            off += RECORD_SIZE

    @property
    def entries(self) -> list[str]:
        return list(self._directory)

    def read(self, entry: str) -> bytes:
        """Return the decompressed bytes of *entry* (case-insensitive)."""
        key = entry.lower()
        if key not in self._directory:
            raise MpakError(f"{os.path.basename(self.path)}: unknown entry: {entry}")
        self._fh.seek(self._base + self._directory[key])
        return _read_stream(self._fh)

    def close(self) -> None:
        self._fh.close()


def list_mpak(path: str) -> tuple[str, list[str]]:
    """Open an archive and return (internal name, entry names)."""
    a = MpakArchive(path)
    try:
        return a.name, a.entries
    finally:
        a.close()


def _placeholder_checksum6(num_entries: int) -> bytes:
    """*open* 6-byte header checksum — deterministic placeholder."""
    v = (num_entries * 0x01010101) ^ 0x4D50414B
    return (v & 0xFFFFFFFF).to_bytes(4, "little") + num_entries.to_bytes(2, "little")


def header_entry_checksum(num_entries: int) -> int:
    """Header byte 17: `(num_entries ^ 12) & 0xFF`.

    Decoded from the retail corpus: the formula holds for all 1306 archives
    in the client, across every archive family (figures, skins, zones,
    terrain). `formats.md` recorded this field as undecoded "not a plain
    count"; it is a plain function of the entry count.
    """
    return (num_entries ^ 12) & 0xFF


def build_header(
    name_comp_len: int, num_entries: int, *, checksum6: bytes | None = None
) -> bytes:
    """The 21-byte MPAK header for a name stream of *name_comp_len* bytes.

    Byte 13 encodes the decoded name-stream length (``+ 8``) and byte 17 the
    entry count (see :func:`header_entry_checksum`). Bytes 5..10 remain
    undecoded: a 48-bit value, uniform across the corpus, matching no CRC or
    hash truncation of the name or the directory.

    Pass *checksum6* -- the field read from the archive being rewritten --
    to preserve it. Nothing about *which entries exist* changes when a
    table's content is edited, so a preserved field stays as valid as it
    ever was; emitting a placeholder instead throws away the only value we
    know the retail archive carried.
    """
    if checksum6 is None:
        checksum6 = _placeholder_checksum6(num_entries)
    if len(checksum6) != 6:
        raise MpakError(f"checksum6 must be 6 bytes, got {len(checksum6)}")
    return (
        MAGIC
        + b"\x02"
        + bytes(checksum6)
        + b"\x06\x07"
        + bytes([(name_comp_len + 8) & 0xFF])
        + b"\x09\x0a\x0b"
        + bytes([header_entry_checksum(num_entries)])
        + b"\x0d\x0e\x0f"
    )


def write_mpak(path: str, name: str, entries, *, checksum6: bytes | None = None) -> None:
    """Write an MPAK archive with *entries* (iterable of (entry_name, bytes)).

    Offsets are computed base-relative (relative to the start of the data
    streams), matching the retail reader's expectation. Entry order is
    preserved.
    """
    pairs = [(str(n), bytes(d)) for n, d in entries]
    name_b = name.encode("latin1")
    s1 = zlib.compress(name_b)
    datas = [zlib.compress(d) for _, d in pairs]
    offsets: list[int] = []
    acc = 0
    for d in datas:
        offsets.append(acc)
        acc += len(d)
    dir_bytes = bytearray()
    for (n, _), rel in zip(pairs, offsets):
        nb = n.encode("latin1")
        if len(nb) >= NAME_SLOT:
            raise MpakError(f"entry name too long for a record: {n!r}")
        rec = bytearray(RECORD_SIZE)
        rec[: len(nb)] = nb
        struct.pack_into("<I", rec, NAME_SLOT, rel)
        dir_bytes += rec
    s2 = zlib.compress(bytes(dir_bytes))
    header = build_header(len(s1), len(pairs), checksum6=checksum6)
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(s1)
        fh.write(s2)
        for d in datas:
            fh.write(d)
