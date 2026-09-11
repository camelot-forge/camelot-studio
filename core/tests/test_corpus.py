"""Corpus tests — validated against the real DAoC client.

Every test here is skipped automatically unless the DAOC_CLIENT environment
variable points at the installed client folder. Game assets are read at
runtime only and never committed.
"""
import json
import os
from collections import Counter

import pytest

from camelot import nifutil
from camelot.kfa import kfa_summary
from camelot.legacy import is_legacy, load_legacy

pytestmark = pytest.mark.usefixtures("client_dir")

# (relative path, expected version, expected block count)
LEGACY_EXPECTATIONS = [
    ("figures/Ettin.NIF", 0x04020100, 299),
    ("figures/fox_pet01.NIF", 0x04020200, 175),
    ("figures/ghost.nif", 0x04020200, 302),
    ("figures/Zombie.NIF", 0x0401000C, 276),
    ("items/jackolantern-GROUND.NIF", 0x04020200, 13),
    ("items/epic_wpn_savage_axe_1h.nif", 0x04020200, 17),
    ("items/Catapult_W_attack.NIF", 0x04020100, 96),
    ("items/N_2h_sword01.nif", 0x0401000C, 17),
    ("effects/AlgaeSlimeBlob.NIF", 0x04020100, None),
    ("effects/CorrosiveCloud.NIF", 0x04020100, None),
    ("effects/VFX_Crows01.nif", None, None),
]

PYFFI_EXPECTATIONS = [
    ("figures/Boar.NIF", 0x0A010000, 111),
    ("figures/Boar_CATA.NIF", 0x0A010000, 133),
    ("figures/BritonM1.NIF", 0x0A010000, None),
    ("figures/skeleton.nif", None, 143),
    ("figures/2HDragon.NIF", 0x0A010000, None),
    ("items/hceltmerwepn.nif", 0x04020100, 115),  # NiLODNode, via legacy
]


def _path(client_dir, rel):
    return os.path.join(client_dir, rel)


@pytest.mark.parametrize("rel,version,blocks", LEGACY_EXPECTATIONS)
def test_legacy_parse(client_dir, rel, version, blocks):
    p = _path(client_dir, rel)
    d = load_legacy(p)
    assert is_legacy(d)
    if version is not None:
        assert d.version == version
    if blocks is not None:
        assert len(d.blocks) == blocks
    assert len(d.roots) >= 1


@pytest.mark.parametrize("rel,version,blocks", PYFFI_EXPECTATIONS)
def test_general_parse(client_dir, rel, version, blocks):
    d = nifutil.load(_path(client_dir, rel))
    if version is not None:
        assert d.version == version
    if blocks is not None:
        assert len(d.blocks) == blocks


def test_legacy_skin_resolution(client_dir):
    """fox_pet01 is a skinned 4.2.2.0 classic: skin refs must resolve."""
    d = load_legacy(_path(client_dir, "figures/fox_pet01.NIF"))
    skinned = [b for b in d.blocks if type(b).__name__ == "NiSkinInstance"]
    assert skinned, "expected at least one NiSkinInstance"
    si = skinned[0]
    assert si.skin_data is not None
    assert si.bones, "expected resolved bone nodes"
    names = [b.name.decode("latin1") for b in si.bones if b.name]
    assert names, "bones should carry names"


def test_roundtrip_pyffi_file(client_dir, tmp_path):
    """Read→write→read equals (version, block count, type multiset, roots)."""
    d = nifutil.load(_path(client_dir, "figures/Boar.NIF"))
    before = (d.version, len(d.blocks), Counter(nifutil.block_types(d)))
    roots_b = sorted(str(getattr(r, "name", None)) for r in d.roots)
    tmp = tmp_path / "rt.nif"
    nifutil.store(d, str(tmp))
    d2 = nifutil.load(str(tmp))
    after = (d2.version, len(d2.blocks), Counter(nifutil.block_types(d2)))
    roots_a = sorted(str(getattr(r, "name", None)) for r in d2.roots)
    assert before == after
    assert roots_b == roots_a


def test_kfa_summary(client_dir):
    s = kfa_summary(_path(client_dir, "anims/BOAR_idle.kfa"))
    assert s["version"] == "0x4020100"
    assert len(s["bone_indices"]) >= 20
    assert s["index_order"] == sorted(s["bone_indices"])
    assert s["max_keys"] > 0


def test_kfa_10x(client_dir):
    s = kfa_summary(_path(client_dir, "anims/wolf_dragon_mount_nod.kfa"))
    assert s["version"] == "0xa010000"
    assert len(s["bone_indices"]) >= 20


def test_gltf_boar_export_is_upright(client_dir, tmp_path):
    """The exported Boar must stand on its feet (height along Y, feet low).

    Regresses the double axis-rotation bug: positions stay in NIF (Z-up)
    space and the root node applies the axis conversion once, so the model's
    height ends up along the glTF Y-up axis.
    """
    import json

    import numpy as np

    from camelot.gltf import export_gltf

    prefix = str(tmp_path / "boar")
    export_gltf(_path(client_dir, "figures/Boar.NIF"), prefix, verbose=False)
    with open(prefix + ".gltf") as fh:
        gltf = json.load(fh)
    with open(prefix + ".bin", "rb") as fh:
        binb = fh.read()

    nodes = gltf["nodes"]
    parent = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            parent[c] = i

    def mat(n):
        m = n.get("matrix")
        return np.array(m, dtype=float).reshape(4, 4).T if m else np.eye(4)

    def world(idx):
        ms = []
        while idx in parent:
            idx = parent[idx]
            ms.append(mat(nodes[idx]))
        acc = np.eye(4)
        for m in reversed(ms):
            acc = acc @ m
        return acc

    mins, maxs = None, None
    for i, n in enumerate(nodes):
        if "mesh" not in n:
            continue
        acc = gltf["accessors"][0]
        bv = gltf["bufferViews"][acc["bufferView"]]
        off = bv["byteOffset"] + acc.get("byteOffset", 0)
        pos = np.frombuffer(binb, dtype=np.float32, count=acc["count"] * 3, offset=off).reshape(-1, 3)
        w = pos @ world(i)[:3, :3].T + world(i)[:3, 3]
        mins = w.min(0) if mins is None else np.minimum(mins, w.min(0))
        maxs = w.max(0) if maxs is None else np.maximum(maxs, w.max(0))
    ext = maxs - mins
    # Upright quadruped: height (Y) above width (X); feet near the bottom of
    # Y (the double-rotation bug left the model upside down / lying flat).
    assert ext[1] > ext[0]
    assert mins[1] < ext[1] * 0.4


def test_gltf_export_boar(client_dir, tmp_path):
    from camelot.gltf import export_gltf

    prefix = str(tmp_path / "boar")
    export_gltf(_path(client_dir, "figures/Boar.NIF"), prefix, verbose=False)
    with open(prefix + ".gltf") as fh:
        gltf = json.load(fh)
    assert gltf["asset"]["version"] == "2.0"
    assert any(n["name"] == "Bip01" for n in gltf["nodes"])
    with open(prefix + ".bin", "rb") as fh:
        assert gltf["buffers"][0]["byteLength"] == len(fh.read())
    with open(prefix + ".skin.json") as fh:
        sidecar = json.load(fh)
    assert sidecar["meshes"], "expected mesh sidecar entries"


def _mpak_repack_check(client_dir, rel, tmp_path):
    """Read every entry of a retail archive, repack, and compare all bytes."""
    from camelot.mpak import MpakArchive, write_mpak

    p = os.path.join(client_dir, rel)
    if not os.path.exists(p):
        pytest.skip(f"{rel} not present")
    a = MpakArchive(p)
    try:
        original = {e: a.read(e) for e in a.entries}
        name = a.name
    finally:
        a.close()
    out = tmp_path / "repack.mpk"
    write_mpak(str(out), name, original.items())
    b = MpakArchive(str(out))
    try:
        assert b.name == name
        assert sorted(b.entries) == sorted(original)
        for e, value in original.items():
            assert b.read(e) == value
    finally:
        b.close()


def test_mpak_repack_single(client_dir, tmp_path):
    _mpak_repack_check(client_dir, "pregame/realmdesc.mpk", tmp_path)


def test_mpak_repack_multi(client_dir, tmp_path):
    _mpak_repack_check(client_dir, "figures/fig3/fig011.mpk", tmp_path)


def test_canonical_bone_table_from_skeleton(client_dir):
    """BONE_INDEX equals the DFS pre-order of skeleton.nif (minus Scene Root)."""
    from camelot.skeleton import BONE_INDEX

    d = nifutil.load(_path(client_dir, "figures/skeleton.nif"))

    def nm(b):
        return b.name.decode("latin1") if b.name else ""

    order = []

    def dfs(b):
        if b is None:
            return
        order.append(nm(b))
        for c in getattr(b, "children", None) or []:
            dfs(c)

    dfs(d.roots[0])
    assert order[0] == "Scene Root"
    order = order[1:]
    assert {i: n for i, n in enumerate(order)} == BONE_INDEX


def test_kfa_indices_land_on_bone_table(client_dir):
    """The 2HW_idle channels at known indices map to the expected bones."""
    from camelot.skeleton import bone_name

    anchors = {11: "Bip01 R Forearm", 12: "Bip01 R Hand",
               23: "Bip01 R Shield", 24: "Bip01 L Clavicle",
               42: "Bip01 R Thigh", 44: "Bip01 R Foot"}
    for idx, want in anchors.items():
        assert bone_name(idx) == want


def test_gamedata_resolves_boar_texture(client_dir):
    """The Boar's fig texture resolves from gamedata to a real DDS (DXT)."""
    from camelot.gamedata import resolve_figure_skins

    skins = resolve_figure_skins(client_dir, _path(client_dir, "figures/Boar.NIF"))
    assert skins, "expected at least one skin texture for the Boar"
    slot, _name, data = skins[0]
    assert slot == "Body"
    assert data[:4] == b"DDS "


def test_gamedata_resolves_multi_slot(client_dir):
    """The 2-headed dragon resolves Body/Head/Arms skin slots."""
    from camelot.gamedata import resolve_figure_skins

    skins = resolve_figure_skins(client_dir, _path(client_dir, "figures/2HDragon.NIF"))
    slots = {s for s, _, _ in skins}
    assert {"Body", "Head", "Arms"} <= slots
    assert all(d[:4] == b"DDS " for _, _, d in skins)


def test_batch_manifest_coverage(client_dir, tmp_path):
    """Every manifest path must resolve and parse (paths already exist)."""
    manifest = "core/tests/manifest.example.txt"
    if not os.path.exists(manifest):
        pytest.skip("manifest not present")
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rel = line.removeprefix("DAOC CLIENT/")
            p = _path(client_dir, rel)
            if not os.path.exists(p):
                continue  # sample files may be absent locally
            d = nifutil.load(p)
            assert len(d.blocks) > 0