"""The client patch: recipes, application, and capability checks.

Everything runs on a synthetic PE (`_pe.client_dll`), so no game file is
needed; the real binary is covered by the corpus test at the end (skipped
without `DAOC_CLIENT`) and by the in-client ladder.
"""
import os
import struct

import pytest
from _pe import BASE_SITES, CAP_SITES, OLD_BASE, OLD_CAP, client_dll

from camelot import clientinstall, clientpatch, mod
from camelot.cli import main
from camelot.mpak import write_mpak

MONSTERS = (
    b"Information,,NIF,Skins,\r\n"
    b"ID,name,#,Body,\r\n"
    b"99,Boar,221,109,\r\n"
)

#: A small patched table: the tests do not need 32768 entries to make the
#: point, and the file stays a couple of megabytes. 3072 is a multiple of
#: 256, which keeps the section size file-aligned (464 = 16 * 29).
CAP = 3072
CEILING = CAP - 1
NEW_VADDR = 0x84000
NEW_BASE = 0x400000 + NEW_VADDR


def _recipe(tmp_path, data=None, cap=CAP):
    """The recipe for a synthetic binary on disk (the generator reads a file)."""
    path = tmp_path / f"game1127-{cap}.dll"
    path.write_bytes(data if data is not None else client_dll())
    return clientpatch.model_id_recipe(str(path), cap=cap)


def _sites(data, vmas):
    return [struct.unpack_from("<I", data, vma - 0x400000)[0] for vma in vmas]


def test_a_pristine_binary_generates_the_recipe(tmp_path):
    recipe = _recipe(tmp_path)
    assert recipe["kind"] == "patch"
    assert recipe["file"] == "game1127.dll"
    assert recipe["provides"] == [f"client.modelids.{CAP}"]
    for vma in CAP_SITES:
        assert recipe["expect"][f"0x{vma:x}"] == struct.pack("<I", OLD_CAP).hex()
        assert recipe["write"][f"0x{vma:x}"] == struct.pack("<I", CAP).hex()
    for vma in BASE_SITES:
        assert recipe["expect"][f"0x{vma:x}"] == struct.pack("<I", OLD_BASE).hex()
        assert recipe["write"][f"0x{vma:x}"] == struct.pack("<I", NEW_BASE).hex()
    assert recipe["append_section"] == {
        "name": ".cmod", "size": CAP * clientpatch.STRIDE, "vaddr": NEW_VADDR,
    }


def test_applying_moves_the_table_and_appends_the_section(tmp_path):
    original = client_dll()
    recipe = _recipe(tmp_path)
    applied = clientpatch.apply(original, recipe)

    assert _sites(applied, CAP_SITES) == [CAP, CAP]
    assert _sites(applied, BASE_SITES) == [NEW_BASE] * 4
    assert len(applied) == NEW_VADDR + CAP * clientpatch.STRIDE
    assert set(applied[NEW_VADDR:]) == {0}
    assert struct.unpack_from("<H", applied, 0x158 + 6)[0] == 4      # one more section
    assert applied[0x2C8:0x2CD] == b".cmod"
    assert clientpatch.provided_capabilities(applied) == [f"client.modelids.{CAP}"]
    clientpatch.verify_applied(applied, recipe)

    # Nothing else moved: the only differing bytes are the six sites, the two
    # header fields and the new section header.
    allowed = {vma - 0x400000 + i for vma in CAP_SITES + BASE_SITES for i in range(4)}
    allowed |= set(range(0x158 + 6, 0x158 + 8))     # the section count
    allowed |= set(range(0x1A8, 0x1AC))             # SizeOfImage
    allowed |= set(range(0x2C8, 0x2C8 + 40))        # the new section header
    allowed |= set(range(NEW_VADDR, len(applied)))
    differing = {i for i in range(len(applied))
                 if i >= len(original) or applied[i] != original[i]}
    assert differing <= allowed


def test_a_recipe_refuses_a_binary_that_is_not_the_one_it_describes(tmp_path):
    recipe = _recipe(tmp_path)
    with pytest.raises(clientpatch.PatchError, match="wrong client build"):
        clientpatch.apply(client_dll(cap=1234), recipe)


def test_an_already_patched_binary_is_refused(tmp_path):
    path = tmp_path / "game1127.dll"
    path.write_bytes(client_dll())
    recipe = clientpatch.model_id_recipe(str(path), cap=CAP)
    path.write_bytes(clientpatch.apply(path.read_bytes(), recipe))
    with pytest.raises(clientpatch.PatchError, match="already patched"):
        clientpatch.model_id_recipe(str(path), cap=CAP)


def test_a_recipe_built_for_another_layout_is_refused(tmp_path):
    recipe = _recipe(tmp_path)
    other = client_dll(sections=((".text", 0x1000, 0x80000), (".data", 0x81000, 0x2000),
                                 (".rsrc", 0x83000, 0x2000)))
    with pytest.raises(clientpatch.PatchError, match="different client"):
        clientpatch.apply(other, recipe)


def test_a_cap_outside_the_measured_range_is_refused(tmp_path):
    path = tmp_path / "game1127.dll"
    path.write_bytes(client_dll())
    with pytest.raises(clientpatch.PatchError, match="2501"):
        clientpatch.model_id_recipe(str(path), cap=2500)
    with pytest.raises(clientpatch.PatchError, match="2501"):
        clientpatch.model_id_recipe(str(path), cap=65536)


def test_the_capability_check_reads_the_bytes(tmp_path):
    recipe = _recipe(tmp_path)
    pristine = client_dll()
    applied = clientpatch.apply(pristine, recipe)
    name = f"client.modelids.{CAP}"
    assert clientpatch.check_capability(name, applied) is None
    assert clientpatch.carried_cap(applied) == CAP
    assert clientpatch.carried_cap(pristine) is None
    assert "no .cmod section" in clientpatch.check_capability(name, pristine)

    # a section that is there, but a table that is not the promised width
    narrowed = bytearray(applied)
    for vma in CAP_SITES:
        struct.pack_into("<I", narrowed, vma - 0x400000, OLD_CAP)
    assert "holds 2500 entries, not 3072" in clientpatch.check_capability(name, bytes(narrowed))

    assert "no game1127.dll" in clientpatch.check_capability(name, None)
    assert "cannot verify" in clientpatch.check_capability("client.objects.10000", applied)
    assert "not a PE image" in clientpatch.check_capability(name, b"not a pe at all")


def test_a_patch_must_declare_what_it_provides():
    with pytest.raises(ValueError, match="capability it provides"):
        mod.client_patch("game1127.dll", provides=[],
                         expect={"0x457d7e": "c4090000"}, write={"0x457d7e": "b80b0000"})
    with pytest.raises(ValueError, match="must start with 'client.'"):
        mod.client_patch("game1127.dll", provides=["modelids"],
                         expect={"0x457d7e": "c4090000"}, write={"0x457d7e": "b80b0000"})


def test_a_recipe_is_validated_where_the_author_sees_it():
    provides = [f"client.modelids.{CAP}"]
    good = {"0x457d7e": "c4090000"}
    with pytest.raises(ValueError, match="0x prefix"):
        mod.client_patch("game1127.dll", provides=provides,
                         expect={"457d7e": "c4090000"}, write=good)
    with pytest.raises(ValueError, match="even-length hex string"):
        mod.client_patch("game1127.dll", provides=provides,
                         expect={"0x1": "abc"}, write=good)
    with pytest.raises(ValueError, match="not hex"):
        mod.client_patch("game1127.dll", provides=provides,
                         expect={"0x1": "zz"}, write=good)
    with pytest.raises(ValueError, match="non-empty 'write'"):
        mod.client_patch("game1127.dll", provides=provides, expect=good, write={})
    with pytest.raises(ValueError, match="1..8 printable"):
        mod.client_patch("game1127.dll", provides=provides, expect=good, write=good,
                         append_section={"name": "toolongname", "size": 0x1000,
                                         "vaddr": 0x1000})
    with pytest.raises(ValueError, match="multiple of 0x1000"):
        mod.client_patch("game1127.dll", provides=provides, expect=good, write=good,
                         append_section={"name": ".x", "size": 0x900, "vaddr": 0x1000})
    with pytest.raises(ValueError, match="inside the client"):
        mod.client_patch("../game1127.dll", provides=provides, expect=good, write=good)


def test_one_file_has_one_owner(tmp_path):
    """A loose replacement and a patch of one file are one destination."""
    with pytest.raises(ValueError, match="same destination"):
        mod.claims([
            mod.client_loose("game1127.dll", member="client/game1127.dll"),
            _recipe(tmp_path),
        ])


def test_the_ceiling_comes_from_the_declarations():
    assert mod.capability_ceiling([f"client.modelids.{CAP}"]) == CEILING
    assert mod.capability_ceiling(["probe.base"]) is None
    assert mod.effective_ceiling([], [{"kind": "patch",
                                       "provides": [f"client.modelids.{CAP}"]}]) == CEILING
    assert mod.effective_ceiling([], []) == mod.CLIENT_ID_MAX


def test_the_unmanaged_path_patches_and_restores(tmp_path):
    path = tmp_path / "game1127.dll"
    original = client_dll()
    path.write_bytes(original)
    recipe = clientpatch.patch_file(str(path), cap=CAP)
    assert path.read_bytes() == clientpatch.apply(original, recipe)
    assert (tmp_path / "game1127.dll.orig").read_bytes() == original
    with pytest.raises(clientpatch.PatchError, match="already patched"):
        clientpatch.patch_file(str(path), cap=CAP)
    clientpatch.restore_file(str(path))
    assert path.read_bytes() == original
    with pytest.raises(clientpatch.PatchError, match="no backup"):
        clientpatch.restore_file(str(tmp_path / "elsewhere.dll"))


def test_the_cli_patches_restores_and_reports(tmp_path, capsys):
    root = tmp_path / "client"
    root.mkdir()
    original = client_dll()
    (root / "game1127.dll").write_bytes(original)

    assert main(["client", "patch", "--client", str(root),
                 "--max-id", str(CEILING)]) == 0
    out = capsys.readouterr().out
    assert f"providing client.modelids.{CAP}" in out
    assert clientpatch.carried_cap((root / "game1127.dll").read_bytes()) == CAP

    assert main(["client", "patch", "--client", str(root),
                 "--max-id", str(CEILING)]) == 1         # already patched
    assert main(["client", "patch", "--client", str(root), "--restore"]) == 0
    assert (root / "game1127.dll").read_bytes() == original


def test_free_ids_reads_the_patched_ceiling(tmp_path, capsys):
    root = tmp_path / "client"
    root.mkdir()
    write_mpak(str(root / "gamedata.mpk"), "gamedata.mpk", [("monsters.csv", MONSTERS)])
    (root / "game1127.dll").write_bytes(clientpatch.apply(client_dll(), _recipe(tmp_path)))

    assert main(["client", "free-ids", "--client", str(root)]) == 0
    out = capsys.readouterr().out
    assert f"ceiling {CEILING} (client.modelids.{CAP})" in out
    assert "free to add:       3070 ids" in out


def test_the_direct_patch_refuses_while_the_game_runs(tmp_path, monkeypatch):
    root = tmp_path / "client"
    root.mkdir()
    original = client_dll()
    (root / "game1127.dll").write_bytes(original)
    monkeypatch.setattr(clientinstall, "client_running", lambda: "game1127.dll")
    assert main(["client", "patch", "--client", str(root)]) == 1
    assert (root / "game1127.dll").read_bytes() == original


@pytest.mark.usefixtures("client_dir")
def test_the_real_binary_matches_the_ladder_recipe(client_dir):
    """The measured client: two caps, four bases, and a 32768-entry section
    at 0x207c000 (the ladder's report, 2026-10-10)."""
    path = os.path.join(client_dir, clientpatch.CLIENT_BINARY)
    if not os.path.isfile(path):
        pytest.skip(f"no {clientpatch.CLIENT_BINARY} in this corpus")
    with open(path, "rb") as fh:
        data = fh.read()
    if clientpatch.carried_cap(data):
        pytest.skip("this corpus client already carries the patch")
    recipe = clientpatch.model_id_recipe(path)
    assert recipe["provides"] == ["client.modelids.32768"]
    assert recipe["append_section"]["vaddr"] == 0x207C000
    assert recipe["write"]["0x421f7e"] == struct.pack("<I", 0x247C000).hex()
    applied = clientpatch.apply(data, recipe)
    assert clientpatch.provided_capabilities(applied) == ["client.modelids.32768"]
