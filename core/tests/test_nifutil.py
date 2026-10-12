"""Unit tests for nifutil: vectors, matrices, axis conversion, node LTM."""
import math
from types import SimpleNamespace

import pytest

from camelot import nifutil


class TestVec3:
    def test_tuple(self):
        assert nifutil.vec3((1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)

    def test_named_attributes(self):
        assert nifutil.vec3(SimpleNamespace(x=1, y=2, z=3)) == (1.0, 2.0, 3.0)

    def test_none(self):
        assert nifutil.vec3(None) == (0.0, 0.0, 0.0)


class TestMatrixCells:
    def test_mat33_from_lists(self):
        m = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
        assert nifutil.mat33_cell(m, 0, 0) == 1
        assert nifutil.mat33_cell(m, 0, 2) == 3
        assert nifutil.mat33_cell(m, 2, 1) == 8

    def test_mat33_from_named(self):
        m = SimpleNamespace(m_11=1, m_12=2, m_23=6)
        assert nifutil.mat33_cell(m, 0, 0) == 1
        assert nifutil.mat33_cell(m, 0, 1) == 2
        assert nifutil.mat33_cell(m, 1, 2) == 6

    def test_mat33_short_names(self):
        m = SimpleNamespace(m00=4, m21=9)
        assert nifutil.mat33_cell(m, 0, 0) == 4
        assert nifutil.mat33_cell(m, 2, 1) == 9

    def test_mat44_from_lists(self):
        m = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]
        assert nifutil.mat4_cell(m, 1, 2) == 7
        assert nifutil.mat4_cell(m, 3, 0) == 13

    def test_mat44_from_named(self):
        m = SimpleNamespace(m_23=7)
        assert nifutil.mat4_cell(m, 1, 2) == 7


class TestMatrixMath:
    def test_identity(self):
        assert nifutil.mat4_identity() == [
            [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
        ]

    def test_mul_identity(self):
        m = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12], [13, 14, 15, 16]]
        assert nifutil.mat4_mul(m, nifutil.mat4_identity()) == m
        assert nifutil.mat4_mul(nifutil.mat4_identity(), m) == m

    def test_mul_reference(self):
        a = [[1, 2, 3, 4], [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
        # row-major translation matrix: translation lives in column 3
        b = [[1, 0, 0, 2], [0, 1, 0, 3], [0, 0, 1, 4], [0, 0, 0, 1]]
        c = nifutil.mat4_mul(a, b)
        assert c[0] == [1, 2, 3, 24]
        assert c[3] == [0, 0, 1, 4]

    def test_axis_conversion(self):
        ax = nifutil.AXIS_CONVERSION
        assert ax == ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
        # M is a rotation: determinant +1, orthogonal rows
        det = (
            ax[0][0] * (ax[1][1] * ax[2][2] - ax[1][2] * ax[2][1])
            - ax[0][1] * (ax[1][0] * ax[2][2] - ax[1][2] * ax[2][0])
            + ax[0][2] * (ax[1][0] * ax[2][1] - ax[1][1] * ax[2][0])
        )
        assert det == pytest.approx(1.0)

    def test_world_axis_conversion_is_the_client_rotation(self):
        """The world and the figures share one conversion — the rotation —
        because the client draws a NIF into its left-handed world with it.

        Measured 2026-10-12, the other way round being what `/world` drew: the
        keep's door nodes land within 15 units of the server's `door` rows
        under this rotation and 357 under the reflection, and the gate the
        server's banners flank (1315 units north) is where the mesh's
        gatehouse lands under it.
        """
        ax = nifutil.WORLD_AXIS_CONVERSION
        assert ax == nifutil.AXIS_CONVERSION
        assert ax == ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))
        det = (
            ax[0][0] * (ax[1][1] * ax[2][2] - ax[1][2] * ax[2][1])
            - ax[0][1] * (ax[1][0] * ax[2][2] - ax[1][2] * ax[2][0])
            + ax[0][2] * (ax[1][0] * ax[2][1] - ax[1][1] * ax[2][0])
        )
        assert det == pytest.approx(1.0)

    def test_axis_matrix_applies(self):
        m = nifutil.mat4_axis()
        for vec, want in [
            ((1, 0, 0, 1), (1, 0, 0, 1)),
            ((0, 1, 0, 1), (0, 0, -1, 1)),   # +Y_up NIF -> -Y glTF
            ((0, 0, 1, 1), (0, 1, 0, 1)),    # +Z NIF -> +Y glTF
        ]:
            out = [
                sum(m[i][j] * vec[j] for j in range(4)) for i in range(4)
            ]
            assert out == list(want)

    def test_world_axis_matrix_applies(self):
        m = nifutil.mat4_world_axis()
        for vec, want in [
            ((1, 0, 0, 1), (1, 0, 0, 1)),    # +x stays east
            ((0, 1, 0, 1), (0, 0, -1, 1)),   # +y (the client's north) -> scene -Z
            ((0, 0, 1, 1), (0, 1, 0, 1)),    # +z stays up
        ]:
            out = [
                sum(m[i][j] * vec[j] for j in range(4)) for i in range(4)
            ]
            assert out == list(want)


class TestNodeLocalMatrixSanitize:
    def test_nan_translation_becomes_zero(self):
        from camelot.legacy import _block_class

        Node = _block_class("NiNode")
        nan = float("nan")
        node = Node(translation=(nan, nan, nan), rotation=None, scale=1.0)
        m = nifutil.node_local_matrix(node)
        assert m is not None
        assert m[0][3] == 0.0 and m[1][3] == 0.0 and m[2][3] == 0.0
        assert all(math.isfinite(v) for row in m for v in row)  # no NaN left


class TestNodeLocalMatrix:
    @pytest.fixture(autouse=True)
    def _legacy_classes(self):
        from camelot.legacy import _block_class
        self.Node = _block_class("NiNode")
        self.Shape = _block_class("NiTriShape")
        self.Other = _block_class("NiZBufferProperty")

    def test_translation_only(self):
        n = self.Node(translation=(1.0, 2.0, 3.0), rotation=None, scale=None)
        m = nifutil.node_local_matrix(n)
        assert m[0][3] == 1.0
        assert m[1][3] == 2.0
        assert m[2][3] == 3.0
        assert m[3] == [0, 0, 0, 1.0]
        assert m[0][0] == 1.0  # identity rotation

    def test_scale(self):
        n = self.Node(translation=None, rotation=None, scale=2.0)
        m = nifutil.node_local_matrix(n)
        assert m[0][0] == 2.0
        assert m[1][1] == 2.0

    def test_ignores_non_nodes(self):
        assert nifutil.node_local_matrix(self.Other()) is None

    def test_shape_is_transformable(self):
        s = self.Shape(translation=(0, 0, 0), rotation=None, scale=1.0)
        assert nifutil.node_local_matrix(s) is not None


class TestSniffVersion:
    def test_nif_header(self, tmp_path):
        p = tmp_path / "x.nif"
        p.write_bytes(b"Gamebryo File Format, Version 10.1.0.0\n\x00" * 8)
        assert nifutil.sniff_version(str(p)) == (
            "Gamebryo File Format, Version 10.1.0.0")

    def test_unknown(self, tmp_path):
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00" * 64)
        assert nifutil.sniff_version(str(p)) == "?"


class TestVersionBits:
    def test_trivial(self):
        data = SimpleNamespace(version=0x04020200, blocks=[1, 2, 3])
        assert nifutil.version_bits(data) == (0x04020200, 3)

    def test_block_types(self):
        from camelot.legacy import _block_class
        a, b = _block_class("NiNode"), _block_class("NiTriShape")
        data = SimpleNamespace(blocks=[a(), b(), a()])
        assert nifutil.block_types(data) == ["NiNode", "NiTriShape", "NiNode"]