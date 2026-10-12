"""Shared fixtures for the camelot test suite."""
import os
import struct

import pytest

# Synthetic minimal meshed NetImmerse 4.2.2.0 NIF (NiNode + NiTriShape +
# NiTriShapeData, 3 vertices / 1 triangle). Lets unit tests exercise the
# legacy reader and the glTF export without any game file.


@pytest.fixture
def nif_bytes() -> bytes:
    b = bytearray()

    def u16(v):
        b.extend(struct.pack("<H", v))

    def u32(v):
        b.extend(struct.pack("<I", v))

    def f32(v):
        b.extend(struct.pack("<f", v))

    def string(s):
        u32(len(s))
        b.extend(s)

    def vec3(x, y, z):
        f32(x)
        f32(y)
        f32(z)

    def mat33_identity():
        for col in range(3):
            for row in range(3):
                f32(1.0 if col == row else 0.0)

    def transform():
        u16(0)                 # flags
        vec3(0.0, 0.0, 0.0)    # translation
        mat33_identity()       # rotation (column-major)
        f32(1.0)               # scale
        vec3(0.0, 0.0, 0.0)    # velocity
        u32(0)                 # num props
        b.append(0)            # has bounding box (u8, false)

    def objectnet(name: bytes):
        string(name)
        u32(0xFFFFFFFF)        # extra data ref
        u32(0xFFFFFFFF)        # controller ref

    def block(tname: bytes, body):
        u32(len(tname))
        b.extend(tname)
        body()

    b += b"NetImmerse File Format, Version 4.2.2.0\n"
    u32(0x04020200)
    u32(3)                     # num blocks

    def body_node():
        objectnet(b"")
        transform()
        u32(1)                 # num children
        u32(1)                 # child = block 1
        u32(0)                 # num effects

    def body_shape():
        objectnet(b"Mesh")
        transform()
        u32(2)                 # data ref = block 2
        u32(0xFFFFFFFF)        # skin instance ref

    def body_shape_data():
        u16(3)                 # num vertices
        b.append(1)            # has vertices
        vec3(0.0, 0.0, 0.0)
        vec3(1.0, 0.0, 0.0)
        vec3(0.0, 1.0, 0.0)
        b.append(1)            # has normals
        for _ in range(3):
            vec3(0.0, 0.0, 1.0)
        vec3(0.5, 0.5, 0.0)    # center
        f32(1.0)               # radius
        b.append(0)            # has vertex colors
        b.append(0)            # num uv sets
        b.append(0)            # t-space flag
        u16(1)                 # num triangles
        u32(3)                 # num triangle points
        u16(0)
        u16(1)
        u16(2)
        u16(0)                 # num match groups

    block(b"NiNode", body_node)
    block(b"NiTriShape", body_shape)
    block(b"NiTriShapeData", body_shape_data)
    u32(1)                     # num roots
    u32(0)                     # root = block 0
    return bytes(b)


@pytest.fixture
def nif_path(tmp_path, nif_bytes):
    p = tmp_path / "synth.nif"
    p.write_bytes(nif_bytes)
    return str(p)


@pytest.fixture(scope="session")
def client_dir():
    """Path to the DAoC client; skips the test when DAOC_CLIENT is unset."""
    d = os.environ.get("DAOC_CLIENT")
    if not d or not os.path.isdir(d):
        pytest.skip("DAOC_CLIENT not set (corpus tests need the game client)")
    return d


def pytest_collection_modifyitems(config, items):
    """Tag every test that asks for the real client as `corpus`.

    The suite has two tiers: unit tests (synthetic corpora, milliseconds) and
    the corpus tests, which walk the ~5 GB client — the contract builds are the
    extreme at ~45 s each. The tag is derived from fixture use rather than
    sprinkled per file, so a test joins the slow tier by asking for
    `client_dir` and nothing else. Inner loop: `pytest -q -m "not corpus"`.
    """
    for item in items:
        if "client_dir" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.corpus)


@pytest.fixture(scope="session")
def player_manifest(client_dir):
    """The canonical player contract, built once per session (~6 s).

    `build_player_manifest` walks the corpus: 36 figures with their rigs,
    appearance slots and anim sets. About a dozen tests need nothing but a
    built manifest, and building it per test was most of their runtime. Tests
    that change the manifest's *inputs* (a custom figure, another pack id)
    still build their own.
    """
    from camelot import contract

    return contract.build_player_manifest(client_dir, pack_id="p", version="1")


@pytest.fixture(scope="session")
def emitted_pack(client_dir, tmp_path_factory):
    """The manifest *and* its GLBs, emitted once per session (~45 s).

    The artifacts are the expensive half — one glTF per figure — and they are
    a pure function of the corpus and the manifest, so every test that
    inspects a built pack reads this one. The reproducibility tests build
    again on purpose: that comparison is what they assert.
    Returns `(manifest, output_dir)`.
    """
    from camelot import contract

    out = tmp_path_factory.mktemp("emitted-pack")
    manifest = contract.build_player_manifest(
        client_dir, pack_id="p", version="1", with_states=False,
    )
    contract.emit_artifacts(client_dir, manifest, out)
    return manifest, out