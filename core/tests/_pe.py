"""A synthetic PE32 image carrying the model-id sites, for the patch tests.

The layout mirrors the real client's convention (FileAlignment 0x1000, each
section's raw pointer equal to its virtual address, so `file offset = RVA`)
but nothing here is game data: the six sites are the addresses the studio
patches and the sections around them are empty. The real binary is covered
separately, by the corpus tests and the in-client ladder.
"""
from __future__ import annotations

import struct

IMAGE_BASE = 0x400000
CAP_SITES = (0x457D7E, 0x43454A)
BASE_SITES = (0x421F7E, 0x434557, 0x457D94, 0x473AF4)
OLD_CAP = 2500
OLD_BASE = 0xB77B98

_E_LFANEW = 0x158
_OPT = _E_LFANEW + 24
_FILE_ALIGN = 0x1000
_SECTIONS = ((".text", 0x1000, 0x80000), (".data", 0x81000, 0x2000),
             (".rsrc", 0x83000, 0x1000))


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def client_dll(*, cap: int = OLD_CAP, base: int = OLD_BASE,
               sections=_SECTIONS, image_base: int = IMAGE_BASE) -> bytes:
    """The synthetic client binary: three sections, the six sites filled in."""
    nsec = len(sections)
    size = _align(max(vaddr + vsize for _name, vaddr, vsize in sections), _FILE_ALIGN)
    data = bytearray(size)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, _E_LFANEW)
    data[_E_LFANEW:_E_LFANEW + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, _E_LFANEW + 4,
                     0x14C, nsec, 0, 0, 0, 0xE0, 0x2102)
    struct.pack_into("<H", data, _OPT, 0x10B)
    struct.pack_into("<I", data, _OPT + 28, image_base)
    struct.pack_into("<II", data, _OPT + 32, 0x1000, _FILE_ALIGN)
    struct.pack_into("<I", data, _OPT + 56, size)
    struct.pack_into("<I", data, _OPT + 60, _align(_OPT + 224 + nsec * 40, _FILE_ALIGN))
    for index, (name, vaddr, vsize) in enumerate(sections):
        struct.pack_into("<8sIIII", data, _OPT + 224 + index * 40,
                         name.encode(), vsize, vaddr, vsize, vaddr)
    for vma in CAP_SITES:
        struct.pack_into("<I", data, vma - image_base, cap)
    for vma in BASE_SITES:
        struct.pack_into("<I", data, vma - image_base, base)
    return bytes(data)
