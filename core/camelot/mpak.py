"""Camelot Studio — MPAK archive reader and writer.

MPAK (magic ``MPAK``, extensions .mpk/.npk) is the archive format of the
DAoC client (zones, fig3 pieces, gamedata tables). Layout, verified on the
retail corpus:

    [21-byte header]   magic "MPAK" + 17 bytes of metadata
    [zlib stream]      archive name (the client's internal, case-insensitive
                       lower-case name)
    [zlib stream]      directory: N records of 0x11C bytes
    [zlib streams]     one per entry, addressed from ``base + offset`` where
                       ``base`` = position right after the directory stream

Directory record (0x11C bytes) — all seven u32 fields located and verified:

    0x000..0x100   entry name (NUL-terminated/padded)
    0x100..0x104   f0: undecoded (a build-tool artefact; carried across a
                   rewrite, never regenerated)
    0x104..0x108   0 (always zero in the corpus)
    0x108..0x10C   uncompressed offset (cumulative over the archive)
    0x10C..0x110   uncompressed size
    0x110..0x114   compressed offset (base-relative; an earlier reading
                   stopped here)
    0x114..0x118   compressed size
    0x118..0x11C   crc32 of the entry's compressed stream

Directory offsets are **base-relative** (base = end of the directory
stream), not absolute — confirmed on the retail corpus.

Header (17 bytes after the magic) — decoded on 4 729 corpus archives; each
carrier byte is position-keyed (``byte[i] = key[i] ^ data``):

    offset 0        0x02                        (version, constant)
    offsets 1..4    crc32(directory stream)     u32 LE ^ 00 01 02 03
    offsets 5..6    len(directory stream)       u16 LE ^ 0x0504
    offsets 7..8    0x06 0x07                   (constants)
    offset 9        len(name stream)            u8 ^ 0x08
    offsets 10..12  0x09 0x0a 0x0b              (constants)
    offset 13       entry count                 u8 ^ 0x0C
    offsets 14..16  0x0d 0x0e 0x0f              (constants)

**The game reads the stream lengths from this header instead of measuring
the streams** — traced in the client: it opens the archive, reads exactly
the declared ``s1``/``s2`` bytes, and when the directory cannot be parsed
from that short read it retries the whole archive and then exits during
the loading screen, with no message. Rewriting an archive therefore
requires recomputing the four carriers; a version of this module that
*preserved* them shipped archives the client refused (measured, test A of
`notes`; the corpus fit and the client trace are in the
workspace note `notes/test-a-report.md`). The 16-bit
directory-length carrier and the 8-bit name-length carrier hold every
retail archive (max measured: 21 551 and 49 bytes).
"""
from __future__ import annotations

import os
import struct
import zlib

MAGIC = b"MPAK"
HEADER_SIZE = 21
RECORD_SIZE = 0x11C
NAME_SLOT = 0x110
UNKNOWN_FIELD_SIZE = 8

# position keys of the header carriers (see module docstring)
_CRC_KEY_BYTES = (0, 1, 2, 3)
_DIRLEN_KEY_BYTES = (0x04, 0x05)
_NAMELEN_KEY = 0x08
_COUNT_KEY = 0x0C

# 17 post-magic bytes with every carrier zeroed: version, constants, slots
_STANDARD_HEADER17 = (
    bytes([0x02]) + bytes(6) + b"\x06\x07" + bytes(1) + b"\x09\x0a\x0b" + bytes(1) + b"\x0d\x0e\x0f"
)


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
        self._unknown: dict[str, bytes] = {}
        off = 0
        while off + RECORD_SIZE <= len(dirdata):
            end = dirdata.find(b"\0", off, off + NAME_SLOT)
            if end < 0:
                break
            fname = dirdata[off:end].decode("latin1")
            (rel,) = struct.unpack("<I", dirdata[off + NAME_SLOT : off + NAME_SLOT + 4])
            key = fname.lower()
            self._directory[key] = rel
            self._unknown[key] = bytes(
                dirdata[off + NAME_SLOT - 16 : off + NAME_SLOT - 8]
            )
            off += RECORD_SIZE

    @property
    def entries(self) -> list[str]:
        return list(self._directory)

    def unknown_fields(self) -> dict[str, bytes]:
        """Per-entry undecoded record bytes (0x100..0x108), for carry-over."""
        return dict(self._unknown)

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


def decode_header(header: bytes) -> dict[str, int]:
    """Decode the four header carriers into ready-to-compare values.

    Returns ``version``, ``dir_crc32`` (crc32 of the compressed directory
    stream), ``dir_comp_len``, ``name_comp_len`` and ``num_entries``.
    """
    if len(header) != HEADER_SIZE or header[:4] != MAGIC:
        raise MpakError("not an MPAK header")
    h = header[4:HEADER_SIZE]
    (crc,) = struct.unpack("<I", bytes(b ^ k for b, k in zip(h[1:5], _CRC_KEY_BYTES)))
    (dirlen,) = struct.unpack("<H", bytes(b ^ k for b, k in zip(h[5:7], _DIRLEN_KEY_BYTES)))
    return {
        "version": h[0],
        "dir_crc32": crc,
        "dir_comp_len": dirlen,
        "name_comp_len": h[9] ^ _NAMELEN_KEY,
        "num_entries": h[13] ^ _COUNT_KEY,
    }


def build_header(
    name_comp_len: int,
    dir_comp_len: int,
    dir_crc32: int,
    num_entries: int,
    *,
    template: bytes | None = None,
) -> bytes:
    """The 21-byte MPAK header for streams of the given sizes.

    Encodes the four carriers (directory crc32 and length, name length,
    entry count) — see the module docstring for the layout. Pass
    *template* — the header read from the archive being rewritten — to
    keep its version and constant bytes; without one the standard
    constants are written.
    """
    if name_comp_len > 0xFF:
        raise MpakError(f"name stream too long for its header carrier: {name_comp_len}")
    if dir_comp_len > 0xFFFF:
        raise MpakError(f"directory stream too long for its header carrier: {dir_comp_len}")
    if template is not None:
        if len(template) != HEADER_SIZE or template[:4] != MAGIC:
            raise MpakError("header template is not an MPAK header")
        h = bytearray(template[4:HEADER_SIZE])
    else:
        h = bytearray(_STANDARD_HEADER17)
    crc_bytes = (dir_crc32 & 0xFFFFFFFF).to_bytes(4, "little")
    h[1:5] = bytes(b ^ k for b, k in zip(crc_bytes, _CRC_KEY_BYTES))
    dirlen_bytes = (dir_comp_len & 0xFFFF).to_bytes(2, "little")
    h[5:7] = bytes(b ^ k for b, k in zip(dirlen_bytes, _DIRLEN_KEY_BYTES))
    h[9] = (name_comp_len ^ _NAMELEN_KEY) & 0xFF
    h[13] = (num_entries ^ _COUNT_KEY) & 0xFF
    return MAGIC + bytes(h)


def repack_mpak(source: str, out: str, replacements: dict[str, bytes]) -> None:
    """Rewrite *source* to *out*, replacing the entries named in
    *replacements* by new content.

    The four header carriers are recomputed from the streams actually
    written; the version/constant header bytes and each entry's undecoded
    record field are carried across from the source — a mod changes a
    table, not the container.
    """
    archive = MpakArchive(source)
    try:
        entries = []
        for name in archive.entries:
            if name in replacements:
                entries.append((name, replacements[name]))
            else:
                entries.append((name, archive.read(name)))
        missing = set(replacements) - set(archive.entries)
        if missing:
            raise MpakError(f"{os.path.basename(source)}: no such entry: {sorted(missing)}")
        write_mpak(
            out,
            archive.name,
            entries,
            template=archive.header,
            unknown=archive.unknown_fields(),
        )
    finally:
        archive.close()


def write_mpak(
    path: str,
    name: str,
    entries,
    *,
    template: bytes | None = None,
    unknown: dict[str, bytes] | None = None,
) -> None:
    """Write an MPAK archive with *entries* (iterable of (entry_name, bytes)).

    Offsets are computed base-relative (relative to the start of the data
    streams), matching the retail reader's expectation. Entry order is
    preserved. *template* is a 21-byte source header whose version and
    constant bytes are kept; *unknown* carries per-entry undecoded record
    bytes (see :meth:`MpakArchive.unknown_fields`) across a rewrite.
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
    unc_acc = 0
    for (n, raw), rel, comp in zip(pairs, offsets, datas):
        nb = n.encode("latin1")
        if len(nb) >= NAME_SLOT:
            raise MpakError(f"entry name too long for a record: {n!r}")
        rec = bytearray(RECORD_SIZE)
        rec[: len(nb)] = nb
        carried = (unknown or {}).get(n.lower())
        if carried is not None:
            if len(carried) != UNKNOWN_FIELD_SIZE:
                raise MpakError(f"undecoded record field must be 8 bytes: {n!r}")
            rec[NAME_SLOT - 16 : NAME_SLOT - 8] = carried
        struct.pack_into(
            "<IIIII",
            rec,
            NAME_SLOT - 8,
            unc_acc,
            len(raw),
            rel,
            len(comp),
            zlib.crc32(comp) & 0xFFFFFFFF,
        )
        unc_acc += len(raw)
        dir_bytes += rec
    s2 = zlib.compress(bytes(dir_bytes))
    header = build_header(
        len(s1), len(s2), zlib.crc32(s2) & 0xFFFFFFFF, len(pairs), template=template
    )
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(s1)
        fh.write(s2)
        for d in datas:
            fh.write(d)
