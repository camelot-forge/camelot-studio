"""Unit tests for the GLB packer (no game files needed)."""
from camelot.glb import pack_glb, unpack_glb


class TestPackGlb:
    def test_header_and_bin_roundtrip(self):
        gltf = {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 4, "uri": "model.bin"}],
            "bufferViews": [],
        }
        blob = pack_glb(gltf, b"abcd")
        assert blob[:4] == b"glTF"
        doc, binary = unpack_glb(blob)
        assert binary == b"abcd"
        # the buffer uri is replaced by the BIN chunk
        assert "uri" not in doc["buffers"][0]

    def test_total_length_matches_and_is_aligned(self):
        blob = pack_glb({"asset": {"version": "2.0"}}, None)
        assert len(blob) % 4 == 0

    def test_json_only_has_no_bin_chunk(self):
        doc, binary = unpack_glb(pack_glb({"a": 1}, None))
        assert doc == {"a": 1}
        assert binary is None

    def test_unaligned_bin_is_padded(self):
        _doc, binary = unpack_glb(pack_glb({"buffers": [{"uri": "x"}]}, b"abc"))
        assert len(binary) == 4
        assert binary[:3] == b"abc"
