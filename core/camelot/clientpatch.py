"""The client's own binary, patched by recipe: the model-id ceiling.

The 1.127 client keeps its monster table in a static array (base `0xb77b98`,
464 bytes an entry) and refuses ids at or above 2500 in two places — the
`monsters.csv` loader and the getter. The array cannot grow where it sits (the
next global begins at its end), so lifting the ceiling **moves** it: a new,
zero-filled PE section holds the wider array, and six immediates in
`game1127.dll` are repointed at it (measured 2026-10-10; the report is in the
private workspace, `notes/test-client-patch-report.md`).

The patch travels as a **recipe**, never as a redistributed DLL: a map of
expected bytes (the pristine client), a map of bytes to write, and the section
to append. `model_id_recipe` generates one against a pristine client;
`apply` checks every expectation before it writes a byte — the same
refusal-on-surprise rule the rest of the installer follows, because this
client fails silently. `check_capability` reads the result back the way a
pack's `requires: client.modelids.<n>` is verified: from the bytes the client
will load, not from bookkeeping.

Two file-name traps, both paid for: the launcher loads **`game1127.dll`**, not
the `game.dll` beside it (58% of the bytes differ), and the six sites are
virtual addresses in `.text` (image base `0x400000`), which this module maps
through the PE section table rather than assuming an alignment.
"""
from __future__ import annotations

import os
import shutil
import struct

__all__ = [
    "CLIENT_BINARY",
    "NEW_CAP",
    "OLD_CAP",
    "SECTION_NAME",
    "STRIDE",
    "PatchError",
    "apply",
    "carried_cap",
    "check_capability",
    "model_id_recipe",
    "patch_file",
    "provided_capabilities",
    "restore_file",
    "verify_applied",
]

#: The binary the client actually loads (the launcher's argument); `game.dll`
#: beside it is a different, older image.
CLIENT_BINARY = "game1127.dll"

#: The model table's array: 464 bytes an entry, 2500 entries shipped.
STRIDE = 0x1D0
OLD_CAP = 2500
#: The default the ladder measured and rendered: 32768 entries, ids to 32767.
NEW_CAP = 32768

#: The two cap compares (`cmp eax,0x9c4`) and the four array references
#: (`add reg,0xb77b98`), as virtual addresses.
CAP_SITES = (0x457D7E, 0x43454A)
BASE_SITES = (0x421F7E, 0x434557, 0x457D94, 0x473AF4)
OLD_BASE = 0xB77B98

#: The appended section: the array's new home.
SECTION_NAME = ".cmod"
_SECTION_NAME = SECTION_NAME.encode("ascii")

#: A patch may not raise the ceiling past the signed 16-bit wire limit until
#: ids above 32767 are measured end to end (server `ushort` -> client).
MAX_CAP = 32768


class PatchError(ValueError):
    """Raised when a recipe cannot be generated from, or applied to, bytes."""


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


class _Pe:
    """The little of a PE32 image this module needs."""

    __slots__ = (
        "e_lfanew",
        "file_alignment",
        "image_base",
        "nsec",
        "opt",
        "section_alignment",
        "sections",
        "size_headers",
        "size_image",
    )

    def __init__(self, **fields):
        for name, value in fields.items():
            setattr(self, name, value)


def _read_pe(data: bytes) -> _Pe:
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise PatchError("not a PE image (no MZ header)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise PatchError("not a PE image (no PE signature)")
    nsec = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    opt = e_lfanew + 24
    if struct.unpack_from("<H", data, opt)[0] != 0x10B:
        raise PatchError("not a 32-bit PE image")
    section_alignment, file_alignment = struct.unpack_from("<II", data, opt + 32)
    sections = []
    for index in range(nsec):
        header = opt + 224 + index * 40
        name = data[header:header + 8].rstrip(b"\0")
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, header + 8)
        sections.append({"hdr": header, "name": name, "vsize": vsize,
                         "vaddr": vaddr, "rawsize": rawsize, "rawptr": rawptr})
    return _Pe(
        e_lfanew=e_lfanew,
        nsec=nsec,
        opt=opt,
        image_base=struct.unpack_from("<I", data, opt + 28)[0],
        section_alignment=section_alignment or 0x1000,
        file_alignment=file_alignment or 0x200,
        size_image=struct.unpack_from("<I", data, opt + 56)[0],
        size_headers=struct.unpack_from("<I", data, opt + 60)[0],
        sections=sections,
    )


def _file_offset(pe: _Pe, vma: int) -> int:
    """Where a virtual address lives in the file; refuses unmapped addresses.

    Mapping through the section table (rather than a fixed delta) is what
    keeps a recipe from writing into a header or into the virtual-only tail
    of `.data` — and what refuses writes into the appended section before it
    exists.
    """
    rva = vma - pe.image_base
    for section in pe.sections:
        if section["vaddr"] <= rva < section["vaddr"] + section["vsize"]:
            offset = section["rawptr"] + (rva - section["vaddr"])
            if offset + 1 > section["rawptr"] + section["rawsize"]:
                break
            return offset
    raise PatchError(f"0x{vma:x} is not inside a file-backed section")


def _parse_vma(key) -> int:
    try:
        value = int(str(key), 16)
    except (TypeError, ValueError):
        raise PatchError(f"{key!r} is not a hex offset") from None
    if value < 0:
        raise PatchError(f"{key!r} is not a hex offset")
    return value


def _sites(data: bytes, pe: _Pe, vmas) -> list[int]:
    return [struct.unpack_from("<I", data, _file_offset(pe, vma))[0] for vma in vmas]


def _pristine_checks(data: bytes, pe: _Pe) -> None:
    """Refuse anything but the unpatched, expected build."""
    for vma in CAP_SITES:
        have = struct.unpack_from("<I", data, _file_offset(pe, vma))[0]
        if have != OLD_CAP:
            raise PatchError(
                f"0x{vma:x} holds {have}, expected {OLD_CAP} — wrong client "
                "build, or already patched"
            )
    for vma in BASE_SITES:
        have = struct.unpack_from("<I", data, _file_offset(pe, vma))[0]
        if have != OLD_BASE:
            raise PatchError(
                f"0x{vma:x} holds 0x{have:x}, expected 0x{OLD_BASE:x} — wrong "
                "client build, or already patched"
            )


def _placement(pe: _Pe, data: bytes) -> dict:
    """Where an appended section lands on this image (the natural placement)."""
    last = max(pe.sections, key=lambda section: section["vaddr"] + section["vsize"])
    return {
        "vaddr": _align(max(last["vaddr"] + last["vsize"], pe.size_image),
                        pe.section_alignment),
        "rawptr": _align(len(data), pe.file_alignment),
    }


def model_id_recipe(dll_path: str, *, cap: int = NEW_CAP) -> dict:
    """The recipe lifting the model-id ceiling of the client at *dll_path*.

    The file must be the pristine one (the six sites at their shipped values):
    a recipe is a statement about bytes, and a client that is not the one it
    describes is refused, not guessed at. The result is applied once in memory
    and read back, so a recipe that does not actually provide its capability
    never leaves the build.
    """
    if not OLD_CAP < cap <= MAX_CAP:
        raise PatchError(
            f"cap {cap} is outside 2501..{MAX_CAP} (ids above 32767 wait for "
            "the signed-16-bit wire measurement)"
        )
    with open(dll_path, "rb") as fh:
        data = fh.read()
    pe = _read_pe(data)
    _pristine_checks(data, pe)
    placement = _placement(pe, data)
    new_base = pe.image_base + placement["vaddr"]
    expect = {}
    write = {}
    for vma in CAP_SITES:
        expect[f"0x{vma:x}"] = struct.pack("<I", OLD_CAP).hex()
        write[f"0x{vma:x}"] = struct.pack("<I", cap).hex()
    for vma in BASE_SITES:
        expect[f"0x{vma:x}"] = struct.pack("<I", OLD_BASE).hex()
        write[f"0x{vma:x}"] = struct.pack("<I", new_base).hex()

    from . import mod

    item = mod.client_patch(
        CLIENT_BINARY,
        provides=[f"client.modelids.{cap}"],
        expect=expect,
        write=write,
        append_section={"name": SECTION_NAME,
                        "size": _align(cap * STRIDE, 0x1000),
                        "vaddr": placement["vaddr"]},
    )
    applied = apply(data, item)
    missing = [name for name in item["provides"] if name not in provided_capabilities(applied)]
    if missing:
        raise PatchError(f"the generated recipe does not provide {missing} (studio bug)")
    return item


def apply(data: bytes, item: dict) -> bytes:
    """*data* with the recipe applied; any failed expectation refuses it.

    Writes are applied only after every expectation held, so a wrong client is
    never half-patched. The result is read back (`verify_applied`) before it
    is returned.
    """
    pe = _read_pe(data)
    for key, want_hex in item["expect"].items():
        vma = _parse_vma(key)
        want = bytes.fromhex(want_hex)
        offset = _file_offset(pe, vma)
        have = data[offset:offset + len(want)]
        if have != want:
            raise PatchError(
                f"0x{vma:x} holds {have.hex()}, expected {want_hex} — wrong "
                "client build, or already patched"
            )

    out = bytearray(data)
    section = item.get("append_section")
    if section is not None:
        name = section["name"].encode("ascii")
        size = int(section["size"])
        placement = _placement(pe, data)
        if int(section["vaddr"]) != placement["vaddr"]:
            raise PatchError(
                f"the appended section's address 0x{int(section['vaddr']):x} "
                f"does not match this file's layout (0x{placement['vaddr']:x}) "
                "— the recipe was built against a different client"
            )
        header = pe.opt + 224 + pe.nsec * 40
        if header + 40 > min(pe.size_headers, pe.sections[0]["rawptr"] if pe.sections else 0):
            raise PatchError("no room for another section header")
        if any(data[header:header + 40]):
            raise PatchError("the section header slot is not empty; refusing to guess")
        out.extend(b"\0" * (placement["rawptr"] - len(out)))
        out.extend(b"\0" * size)
        struct.pack_into("<8sIIII", out, header, name, size, placement["vaddr"],
                         size, placement["rawptr"])
        struct.pack_into("<I", out, header + 36, 0xC0000040)  # READ|WRITE|DATA
        struct.pack_into("<H", out, pe.e_lfanew + 6, pe.nsec + 1)
        struct.pack_into("<I", out, pe.opt + 56,
                         _align(placement["vaddr"] + size, pe.section_alignment))

    writes = sorted(
        ((_parse_vma(key), _file_offset(pe, _parse_vma(key)), bytes.fromhex(value))
         for key, value in item["write"].items()),
        key=lambda write: write[1],
    )
    previous = -1
    for vma, offset, payload in writes:
        if offset < previous:
            raise PatchError("two writes overlap; refusing an order-dependent patch")
        if offset + len(payload) > len(data):
            raise PatchError(
                f"a write at 0x{vma:x} would extend the file; "
                "only append_section grows it"
            )
        out[offset:offset + len(payload)] = payload
        previous = offset + len(payload)

    result = bytes(out)
    verify_applied(result, item)
    return result


def verify_applied(data: bytes, item: dict) -> None:
    """Read back what `apply` should have produced; raises on any mismatch."""
    pe = _read_pe(data)
    for key, want_hex in item["write"].items():
        vma = _parse_vma(key)
        want = bytes.fromhex(want_hex)
        offset = _file_offset(pe, vma)
        have = data[offset:offset + len(want)]
        if have != want:
            raise PatchError(f"0x{vma:x} holds {have.hex()} after the write, expected {want_hex}")
    section = item.get("append_section")
    if section is not None:
        name = section["name"].encode("ascii")
        found = next((s for s in pe.sections if s["name"] == name), None)
        if found is None:
            raise PatchError(f"the appended section {section['name']!r} is missing")
        if (found["vaddr"], found["vsize"]) != (int(section["vaddr"]), int(section["size"])):
            raise PatchError(f"the appended section {section['name']!r} does not match the recipe")
        if found["rawptr"] + found["vsize"] > len(data):
            raise PatchError(f"the appended section {section['name']!r} is not fully in the file")


def provided_capabilities(data: bytes) -> list[str]:
    """The capabilities a client binary itself carries (measured, not assumed)."""
    try:
        pe = _read_pe(data)
        section = next((s for s in pe.sections if s["name"] == _SECTION_NAME), None)
        if section is None:
            return []
        caps = set(_sites(data, pe, CAP_SITES))
        bases = set(_sites(data, pe, BASE_SITES))
    except PatchError:
        return []
    if len(caps) != 1 or len(bases) != 1:
        return []
    cap = caps.pop()
    base = bases.pop()
    if cap <= OLD_CAP or base != pe.image_base + section["vaddr"]:
        return []
    if section["vsize"] < cap * STRIDE:
        return []
    return [f"client.modelids.{cap}"]


def carried_cap(data: bytes | None) -> int | None:
    """The model-table width the binary carries; None for the shipped 2500."""
    names = provided_capabilities(data or b"")
    return max(int(name.rsplit(".", 1)[1]) for name in names) if names else None


def check_capability(name: str, data: bytes | None) -> str | None:
    """None when *data* carries *name*; otherwise the reason it does not.

    The check is the *effect*, not a promise: the cap compares, the array
    references and the section's size are read from the bytes the client will
    load. An unknown capability is a reason too — the studio only vouches for
    what it can verify.
    """
    prefix = "client.modelids."
    if not isinstance(name, str) or not name.startswith(prefix) or not name[len(prefix):].isdigit():
        return f"the studio cannot verify {name!r} (only client.modelids.<n> is known)"
    want = int(name[len(prefix):])
    if not data:
        return f"the client has no {CLIENT_BINARY} to read"
    try:
        pe = _read_pe(data)
    except PatchError as error:
        return f"{CLIENT_BINARY} is {error}"
    section = next((s for s in pe.sections if s["name"] == _SECTION_NAME), None)
    if section is None:
        return f"{CLIENT_BINARY} carries no {SECTION_NAME} section"
    try:
        caps = set(_sites(data, pe, CAP_SITES))
        bases = set(_sites(data, pe, BASE_SITES))
    except PatchError as error:
        return f"{CLIENT_BINARY}: {error}"
    if caps != {want}:
        held = ", ".join(str(value) for value in sorted(caps)) or "none"
        return f"its model table holds {held} entries, not {want}"
    if bases != {pe.image_base + section["vaddr"]}:
        return f"its array references do not point at {SECTION_NAME}"
    if section["vsize"] < want * STRIDE:
        return (f"{SECTION_NAME} holds {section['vsize'] // STRIDE} entries, "
                f"fewer than {want}")
    return None


def _write_bytes(path: str, data: bytes) -> None:
    tmp = f"{path}.camelot-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def patch_file(path: str, *, cap: int = NEW_CAP) -> dict:
    """Patch the client binary at *path* in place, keeping a `.orig` backup.

    The unmanaged path (`camelot client patch`): no installer state, just the
    backup and the recipe. Refuses a file that is not the pristine one, and
    reads its own write back before returning.
    """
    recipe = model_id_recipe(path, cap=cap)
    with open(path, "rb") as fh:
        data = fh.read()
    patched = apply(data, recipe)
    backup = path + ".orig"
    if not os.path.exists(backup):
        shutil.copyfile(path, backup)
    _write_bytes(path, patched)
    with open(path, "rb") as fh:
        verify_applied(fh.read(), recipe)
    return recipe


def restore_file(path: str) -> None:
    """Put the `.orig` backup back over *path* (the unmanaged path)."""
    backup = path + ".orig"
    if not os.path.isfile(backup):
        raise PatchError(f"{backup}: no backup to restore from")
    shutil.copyfile(backup, path)
