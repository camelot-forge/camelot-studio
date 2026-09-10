"""Unit tests for the legacy NetImmerse 4.x reader (synthetic buffers only)."""
import struct

import pytest
from camelot.legacy import (
    LegacyData,
    LegacyFormatError,
    _block_class,
    _read_header,
    _Reader,
    _strip_triangles,
    is_legacy,
)


class TestStripTriangles:
    def test_single_strip(self):
        assert _strip_triangles([[0, 1, 2, 3]]) == [(0, 1, 2), (3, 2, 1)]

    def test_wind_alternates_per_crank(self):
        assert _strip_triangles([[0, 1, 2, 3, 4]]) == [
            (0, 1, 2), (3, 2, 1), (2, 3, 4),
        ]

    def test_short_strip_yields_nothing(self):
        assert _strip_triangles([[0, 1]]) == []

    def test_multiple_strips(self):
        assert _strip_triangles([[0, 1, 2], [0, 1, 2]]) == [
            (0, 1, 2), (0, 1, 2),
        ]


class TestBlockClass:
    def test_cached_per_name(self):
        assert _block_class("NiNode") is _block_class("NiNode")

    def test_distinct_names(self):
        assert _block_class("NiNode") is not _block_class("NiBone")

    def test_dynamic_typename(self):
        assert _block_class("NiNode").__name__ == "NiNode"

    def test_repr(self):
        b = _block_class("NiNode")(name=b"n")
        assert "NiNode" in repr(b)


class TestReaderPrimitives:
    def make(self, buf, version=0x04020200):
        return _Reader(buf, version, "t.nif")

    def test_u8_u16_u32_f32(self):
        r = self.make(struct.pack("<BHIf", 0xFF, 0x1234, 0xDEADBEEF, 1.5))
        assert r.u8() == 0xFF
        assert r.u16() == 0x1234
        assert r.u32() == 0xDEADBEEF
        assert r.f32() == 1.5

    def test_string(self):
        r = self.make(b"\x03\x00\x00\x00abcXYZ")
        assert r.string() == b"abc"
        assert r.pos == 7

    def test_string_empty(self):
        r = self.make(b"\x00\x00\x00\x00")
        assert r.string() == b""

    def test_string_too_long(self):
        r = self.make(struct.pack("<I", 0xFFFF))
        with pytest.raises(LegacyFormatError):
            r.string()

    def test_ref(self):
        r = self.make(struct.pack("<I", 0xFFFFFFFF))
        assert r.ref() == 0xFFFFFFFF

    def test_vec3_vec4(self):
        r = self.make(struct.pack("<7f", 1, 2, 3, 4, 5, 6, 7))
        assert r.vec3() == (1.0, 2.0, 3.0)
        assert r.vec4() == (4.0, 5.0, 6.0, 7.0)

    def test_mat33_column_major_to_row_major(self):
        # column-major storage: col0=(1,2,3) col1=(4,5,6) col2=(7,8,9)
        r = self.make(struct.pack("<9f", 1, 2, 3, 4, 5, 6, 7, 8, 9))
        assert r.mat33() == [[1, 4, 7], [2, 5, 8], [3, 6, 9]]

    def test_read_past_end(self):
        r = self.make(b"\x01")
        with pytest.raises(LegacyFormatError):
            r.u32()


class TestBooleans:
    def test_u8_false_values(self):
        for v in (0x00, 0xFF):
            r = _Reader(bytes([v]), 0x04020200, "t")
            assert r.bool_() is False

    def test_u8_true_values(self):
        for v in (0x01, 0x02):
            r = _Reader(bytes([v]), 0x04020200, "t")
            assert r.bool_() is True

    def test_old_u32_bools(self):
        old = 0x04010009  # < 4.1.0.1
        assert _Reader(struct.pack("<I", 0), old, "t").bool_() is False
        assert _Reader(struct.pack("<I", 0xFFFFFFFF), old, "t").bool_() is False
        assert _Reader(struct.pack("<I", 1), old, "t").bool_() is True


class TestKeygroup:
    def make(self, buf):
        return _Reader(buf, 0x04020200, "t")

    def test_empty(self):
        r = self.make(struct.pack("<I", 0))
        assert r._keygroup(3) == []

    def test_linear_with_interp(self):
        r = self.make(struct.pack("<II7f",
                                  1,            # count
                                  1,            # interpolation (LINEAR)
                                  0.5, 1, 2, 3,  # time + vec3
                                  0, 0, 0))
        keys = r._keygroup(3)
        assert keys == [(0.5, (1.0, 2.0, 3.0))]

    def test_no_interp_when_peek_invalid(self):
        r = self.make(struct.pack("<If", 1, 0.25) + struct.pack("<3f", 9, 8, 7))
        keys = r._keygroup(3)  # next u32 (0) is not a plausible KeyType
        assert keys == [(0.25, (9.0, 8.0, 7.0))]

    def test_quadratic_keys_have_tangents(self):
        r = self.make(struct.pack("<IIf", 1, 2, 0.0) + struct.pack("<fff", 1.0, 1.5, 0.5))
        keys = r._keygroup(1)  # float payload, quadratic => time+val+fwd+back
        assert keys == [(0.0, (1.0, 1.5, 0.5))]

    def test_tbc_keys(self):
        r = self.make(struct.pack("<IIf", 1, 3, 0.0)
                      + struct.pack("<ffff", 2.0, 0.5, 0.25, 0.125))
        keys = r._keygroup(1)  # TBC => time+val+3 floats
        assert keys == [(0.0, (2.0, 0.5, 0.25, 0.125))]

    def test_peek_does_not_advance(self):
        r = self.make(struct.pack("<II", 1, 2))
        assert r._valid_key_type() is True
        assert r.pos == 0
        assert r._valid_key_type() is True


class TestReadHeader:
    def test_parse(self):
        buf = (b"NetImmerse File Format, Version 4.2.2.0\n"
               + struct.pack("<II", 0x04020200, 7)
               + b"data")
        vs, ver, nb, pos = _read_header(buf, "t")
        assert vs == "NetImmerse File Format, Version 4.2.2.0"
        assert ver == 0x04020200
        assert nb == 7
        assert buf[pos:] == b"data"

    def test_not_nif(self):
        with pytest.raises(LegacyFormatError):
            _read_header(b"PNG....\n\x00", "t")

    def test_truncated(self):
        with pytest.raises(LegacyFormatError):
            _read_header(b"NetImmerse File Format, Version 4.2.2.0\n\x00", "t")


class TestLegacyData:
    def test_synthetic_load(self, nif_path):
        d = LegacyData(nif_path)
        assert d.legacy is True
        assert d.writable is False
        assert d.version == 0x04020200
        assert len(d.blocks) == 3
        assert len(d.roots) == 1

    def test_resolution(self, nif_path):
        d = LegacyData(nif_path)
        root = d.roots[0]
        assert type(root).__name__ == "NiNode"
        shape = root.children[0]
        assert type(shape).__name__ == "NiTriShape"
        assert shape.name == b"Mesh"
        assert shape.parent is root
        mesh = shape.data
        assert len(mesh.vertices) == 3
        assert mesh.triangles == [(0, 1, 2)]
        assert mesh.get_triangles() == [(0, 1, 2)]
        assert shape.skin_instance is None

    def test_is_legacy(self, nif_path):
        d = LegacyData(nif_path)
        assert is_legacy(d)

    def test_walk_whole_file(self, nif_path):
        with open(nif_path, "rb") as fh:
            buf = fh.read()
        _, ver, nb, pos = _read_header(buf, "t")
        r = _Reader(buf[pos:], ver, "t")
        r.read_blocks(nb)
        roots = r.read_footer()
        assert len(roots) == 1
        assert roots == [0]
        assert r.pos == len(buf) - pos