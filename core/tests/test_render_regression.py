"""Golden render regression: the exported glTF's world bbox as three.js shows it.

These tests lock the approved orientation/placement of a fixture set, so a
change that moves or orients a figure differently fails here instead of
relying on eyeballing the viewer. Ettin/Zombie values were recaptured after
the Z-up axis fix (the old "early 4.x is X-down" goldens locked them lying
down); the rest date from the original harness.
"""

import os

import pytest
from _bbox import feet_below_pelvis, skinned_world_bbox, world_bbox

from camelot.gltf import export_gltf

GOLDEN: dict[str, dict] = {
    "7legspider.nif": {"dims": [125.41, 37.217, 118.199], "center": [-6.863, 18.591, 4.397]},
    "Afanc.NIF": {"dims": [270.81, 187.456, 1007.105], "center": [0.0, 93.722, 0.0]},
    "Boar.NIF": {"dims": [20.796, 39.489, 81.941], "center": [-0.0, 19.798, 7.791]},
    "Amazon.nif": {"dims": [85.47, 120.339, 23.422], "center": [-0.005, 60.068, 1.335]},
    "2HWolf.NIF": {"dims": [113.75, 53.476, 30.153], "center": [-3.273, 26.712, -0.017]},
    "2headed_Wolf.NIF": {"dims": [97.548, 49.627, 24.556], "center": [-4.766, 24.596, 0.011]},
    "Ettin.NIF": {"dims": [102.107, 72.547, 20.275], "center": [0.009, 36.274, 1.459]},
    "Zombie.NIF": {"dims": [76.818, 71.82, 10.186], "center": [-0.0, 35.92, -0.48]},
    "skeleton.nif": {"dims": [70.126, 68.892, 9.429], "center": [0.001, 34.446, 0.285]},
    "3head.NIF": {"dims": [127.961, 83.577, 32.415], "center": [-0.0, 41.952, -1.261]},
}


def _export(client_dir, name, tmp_path, **kwargs):
    prefix = str(tmp_path / "model")
    export_gltf(os.path.join(client_dir, "figures", name), prefix, verbose=False, **kwargs)
    return prefix + ".gltf", prefix + ".bin"


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_world_bbox_matches_golden(client_dir, tmp_path, name):
    gltf, binary = _export(client_dir, name, tmp_path)
    dims, center = world_bbox(gltf, binary)
    assert dims == pytest.approx(GOLDEN[name]["dims"], abs=0.05)
    assert center == pytest.approx(GOLDEN[name]["center"], abs=0.05)


@pytest.mark.parametrize("name", ["Zombie.NIF", "Ettin.NIF", "Amazon.nif", "3head.NIF"])
def test_biped_stands_feet_below_pelvis(client_dir, tmp_path, name):
    # Up-axis regression: every era composes Z-up in NIF world space, so the
    # exported (Y-up) figure must have its feet below its pelvis. This is the
    # guard that would have caught the stale "early 4.x is X-down" rule after
    # the row-major fix (those figures exported lying down).
    gltf, binary = _export(client_dir, name, tmp_path)
    assert feet_below_pelvis(gltf, binary) is True


@pytest.mark.parametrize("name", ["Boar.NIF", "Afanc.NIF", "7legspider.nif"])
def test_skinned_bind_matches_non_skinned(client_dir, tmp_path, name):
    # The skinned bind pose must render exactly like the static export
    # (otherwise the skin double-applies the axis and the mesh drifts).
    g0, b0 = _export(client_dir, name, tmp_path)
    dims0, center0 = world_bbox(g0, b0)
    g1, b1 = _export(client_dir, name, tmp_path, emit_skinned=True)
    dims1, center1 = skinned_world_bbox(g1, b1)
    assert dims1 == pytest.approx(dims0, abs=0.05)
    assert center1 == pytest.approx(center0, abs=0.05)
