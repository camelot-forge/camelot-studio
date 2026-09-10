"""Unit tests for the glTF exporter (synthetic NIF, no game files)."""
import json
from types import SimpleNamespace

import numpy as np
from camelot.gltf import (
    _apply_axis,
    _fill_partition,
    _flatten_colmajor,
    _vec2,
    _weights_to_list,
    export_gltf,
)
from numpy.testing import assert_array_equal


class TestAxis:
    def test_apply_axis_maps_y_to_neg_z(self):
        pts = np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.float32)
        out = _apply_axis(pts)
        assert_array_equal(out, np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]],
                                         dtype=np.float32))


class TestVec2:
    def test_tuple(self):
        assert _vec2((1.0, 2.0)) == (1.0, 2.0)

    def test_uv_attrs(self):
        assert _vec2(SimpleNamespace(u=3, v=4)) == (3.0, 4.0)


class TestFlatten:
    def test_column_major_order(self):
        m = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]
        assert _flatten_colmajor(m) == [1, 5, 9, 13, 2, 6, 10, 14,
                                        3, 7, 11, 15, 4, 8, 12, 16]


class TestFillPartition:
    def test_legacy_dict(self):
        part = {
            "num_vertices": 2,
            "vertex_map": [3, 4],
            "bones": [7, 8],
            "vertex_weights": [[0.5], [0.6]],
            "bone_indices": [[0], [1]],
        }
        w = {}
        _fill_partition(part, w)
        assert w == {3: [(7, 0.5)], 4: [(8, 0.6)]}

    def test_pyffi_like(self):
        part = SimpleNamespace(
            num_vertices=2,
            has_vertex_map=True,
            vertex_map=[3, 4],
            bones=[7, 8],
            has_bone_indices=True,
            bone_indices=[[0], [1]],
            has_vertex_weights=True,
            vertex_weights=[[0.5], [0.6]],
        )
        w = {}
        _fill_partition(part, w)
        assert w == {3: [(7, 0.5)], 4: [(8, 0.6)]}


class TestWeightsToList:
    def test_shape(self):
        out = _weights_to_list({0: [(1, 0.5)], 2: [(3, 1.0)]}, 3)
        assert out == [[[1, 0.5]], [], [[3, 1.0]]]


class TestExportSynthetic:
    def test_export(self, nif_path, tmp_path):
        prefix = str(tmp_path / "synth")
        stats = export_gltf(nif_path, prefix, verbose=False)
        assert stats["meshes"] == 1
        assert stats["vertices"] == 3
        assert stats["triangles"] == 1

        with open(prefix + ".gltf") as fh:
            gltf = json.load(fh)
        assert gltf["asset"]["version"] == "2.0"
        names = [n["name"] for n in gltf["nodes"]]
        assert "__axes__" in names
        assert "Mesh" in names
        assert len(gltf["meshes"]) == 1
        prim = gltf["meshes"][0]["primitives"][0]
        assert "POSITION" in prim["attributes"]
        with open(prefix + ".bin", "rb") as fh:
            bin_len = len(fh.read())
        assert gltf["buffers"][0]["byteLength"] == bin_len

        with open(prefix + ".skin.json") as fh:
            sidecar = json.load(fh)
        assert sidecar["meshes"][0]["name"] == "Mesh"

    def test_export_skinned_flag_without_bones(self, nif_path, tmp_path):
        # synthetic mesh is not skinned: --skinned must not crash and must
        # not emit JOINTS_0
        prefix = str(tmp_path / "synth2")
        export_gltf(nif_path, prefix, verbose=False, emit_skinned=True)
        with open(prefix + ".gltf") as fh:
            gltf = json.load(fh)
        prim = gltf["meshes"][0]["primitives"][0]
        assert "JOINTS_0" not in prim["attributes"]
        assert "skins" not in gltf or gltf["skins"] == []