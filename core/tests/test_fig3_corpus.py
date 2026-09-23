"""Corpus tests for the fig3 player-head assembly — skipped without a client.

Validates the piece extraction and the bind-compensated attachment against
the real figures: the attached fig3 head/hair must land exactly where their
own (fig3) skeleton authored them, even though they are rebound onto the
classic figure's skeleton.
"""
import json
import os
import struct

import numpy as np
import pytest

from camelot import fig3, figure, gamedata
from camelot.gltf import _fill_partition
from camelot.nifutil import load, skin_transform_matrix

pytestmark = pytest.mark.usefixtures("client_dir")

SARACEN = "figures/BSaracenF.NIF"


def _resolved_view(reference, target) -> dict:
    """Source bone name -> target bone, as :func:`fig3.attach_part` resolves
    it (bonelist names against the reference hierarchy)."""
    target_bones = fig3._bone_nodes(target)
    parents = fig3._node_parents(reference)
    view = {}
    for name in fig3._bone_nodes(reference):
        resolved = fig3.resolve_bone(name, target_bones, parents)
        if resolved is not None:
            view[name] = target_bones[resolved]
    return view


def _positions(shape, bones: dict) -> np.ndarray:
    """Skinned bind positions of a shape: sum(w * boneWorld @ S @ v)."""
    skin_instance = shape.skin_instance
    names = fig3.skin_bone_names(shape)
    skin_data = fig3._skin_data(skin_instance)
    partition = skin_instance.skin_partition or skin_data.skin_partition
    weights: dict[int, list[tuple[int, float]]] = {}
    for block in getattr(partition, "skin_partition_blocks", None) or getattr(
        partition, "partitions", []
    ):
        _fill_partition(block, weights)
    verts = np.array([[float(v.x), float(v.y), float(v.z)] for v in shape.data.vertices])
    out = np.zeros_like(verts)
    for index in range(len(verts)):
        acc = np.zeros(3)
        total = 0.0
        for skin_index, weight in weights.get(index, []):
            joint = names[skin_index]
            if joint not in bones:
                continue
            stored = np.asarray(
                skin_transform_matrix(skin_data.bone_list[skin_index]), dtype=float
            )
            point = (bones[joint][1] @ stored @ np.append(verts[index], 1.0))[:3]
            acc += weight * point
            total += weight
        out[index] = acc / total if total > 0 else verts[index]
    return out


def _shapes_named(data, name: str) -> list:
    return [
        shape
        for shape in fig3.shapes_of(data)
        if shape.name and shape.name.decode("latin1") == name
    ]


def test_tables_map_saracen_female(client_dir):
    tables = fig3.read_tables(client_dir)
    rid = fig3.race_id(tables, "Saracen Female")
    assert rid == 4
    head = fig3.parts_of(tables, rid, "Head")[0]
    assert head.filename.lower() == "sar_f_head01"
    hair = fig3.parts_of(tables, rid, "Hair")[0]
    assert hair.filename.lower() == "sar_f_hair01"
    assert hair.double_sided is True


def test_loaded_pieces_match_the_reference_geometry(client_dir):
    """The fig3 pieces are the same 541/800 (head) and 269/310 (hair) meshes
    the reference viewer ships."""
    tables = fig3.read_tables(client_dir)
    head = fig3.load_part(client_dir, fig3.parts_of(tables, 4, "Head")[0])
    hair = fig3.load_part(client_dir, fig3.parts_of(tables, 4, "Hair")[0])
    assert head is not None and hair is not None
    head_shape = fig3.shapes_of(head)[-1]
    hair_shape = fig3.shapes_of(hair)[-1]
    assert len(head_shape.data.vertices) == 541
    assert len(list(head_shape.data.get_triangles())) == 800
    assert len(hair_shape.data.vertices) == 269
    assert len(list(hair_shape.data.get_triangles())) == 310
    assert fig3.skin_bone_names(hair_shape) == ["Bip01 Head", "Bip01 Spine2"]


@pytest.mark.parametrize(
    ("nif", "race"),
    [(SARACEN, 4), ("figures/BSaracenM.NIF", 3)],
)
def test_attach_preserves_the_authored_bind(client_dir, nif, race):
    """Attached pieces keep their authored bind — head pieces with and
    without an embedded skeleton, plus the skeleton-less hair."""
    tables = fig3.read_tables(client_dir)
    data = load(os.path.join(client_dir, nif))
    head = fig3.load_part(client_dir, fig3.parts_of(tables, race, "Head")[0])
    hair = fig3.load_part(client_dir, fig3.parts_of(tables, race, "Hair")[0])

    # Authored positions, on the canonical fig3 skeleton.
    source_bones = fig3._bone_nodes(fig3.load_reference(client_dir, tables))
    authored = {
        "Head": [_positions(s, source_bones) for s in fig3.shapes_of(head) if s.skin_instance],
        "Hair": [_positions(s, source_bones) for s in fig3.shapes_of(hair) if s.skin_instance],
    }

    reference = fig3.load_reference(client_dir, tables)
    assert fig3.attach_part(data, head, "Head", reference=reference) == 1
    assert fig3.attach_part(data, hair, "Hair", reference=reference) == 1

    view = _resolved_view(reference, data)
    for name in ("Head", "Hair"):
        attached = _shapes_named(data, name)
        assert len(attached) == 1
        for expected, shape in zip(authored[name], attached):
            got = _positions(shape, view)
            assert np.allclose(got, expected, atol=2e-3)


def test_prepare_swaps_the_classic_head(client_dir):
    prepared = figure.prepare(client_dir, os.path.join(client_dir, SARACEN))
    assert prepared is not None
    data, textures = prepared
    slots = {tex.slot: tex.name for tex in textures}
    assert {"Head", "Hair", "Eyes"} <= set(slots)
    assert slots["Head"].lower() == "sar_f_head01.dds"
    assert slots["Hair"].lower().startswith("sar_f_hair01")
    assert figure.has_classic_heads(data) is False
    assert _shapes_named(data, "Head") and _shapes_named(data, "Hair")


def _exported_uvs(gltf_path, bin_path, mesh_name):
    doc = json.loads(gltf_path.read_text())
    binary = bin_path.read_bytes()
    prim = next(m["primitives"][0] for m in doc["meshes"] if m["name"] == mesh_name)
    acc = doc["accessors"][prim["attributes"]["TEXCOORD_0"]]
    view = doc["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    values = struct.unpack_from("<" + "f" * acc["count"] * 2, binary, start)
    return np.array(values).reshape(-1, 2)


def test_player_cloak_uses_uv_set_one(client_dir, tmp_path):
    """Cloak meshes carry three UV sets; the base sheet is sampled on UV set 1
    (the reference viewer bakes every library from `uv_sets[1]`). Exporting
    `uv_sets[0]` mapped the cloth to a fragmented atlas layout and produced a
    hard seam across the hood."""
    from camelot.gltf import export_gltf
    from camelot.playerfig import build

    tables = fig3.read_tables(client_dir)
    rid = fig3.race_id(tables, "Saracen Female")
    cloak = fig3.load_part(client_dir, fig3.parts_of(tables, rid, "Cloak")[0])
    source = next(
        s for s in fig3.shapes_of(cloak)
        if (s.name or b"").lower().startswith(b"cloak")
    )
    source_uvs = np.array(
        [[uv.u, uv.v] for uv in source.data.uv_sets[1]], dtype=np.float32
    )

    data, textures = build(str(client_dir), "Saracen Female", cloak=1)
    out = tmp_path / "cloak"
    export_gltf("cloak.nif", str(out), data=data, textures=textures)
    exported = _exported_uvs(out.with_suffix(".gltf"), out.with_suffix(".bin"), "Cloak")

    assert exported.shape == source_uvs.shape
    assert np.allclose(exported, source_uvs, atol=1e-5)


def test_prepare_assembles_the_fig3_body(client_dir):
    prepared = figure.prepare(client_dir, os.path.join(client_dir, SARACEN))
    assert prepared is not None
    data, textures = prepared
    slots = {tex.slot for tex in textures}
    for slot in ("Body", "Legs", "Boots", "Arms", "Gloves"):
        assert slot in slots, f"{slot}: no fig3 piece attached"
        assert figure._shapes_named(data, slot) if hasattr(figure, "_shapes_named") else True
    assert _shapes_named(data, "Body") and _shapes_named(data, "Gloves")
    tex_by_slot = {tex.slot: tex.name for tex in textures}
    assert tex_by_slot["Body"].lower() == "cthbody01_01_f.dds"
    assert tex_by_slot["Gloves"].lower() == "leagloves01_01.dds"

    # Nothing classic of the covered slots may survive on top of the pieces.
    for shape in fig3.shapes_of(data):
        name = figure._shape_name(shape).lower()
        assert not name.startswith("lbody"), name


# The hybrid head must work for every classic player layout: the fig3 pieces
# for most races ship skeleton-less (bonelist-driven) and only some hair
# styles have colour sheets, so both paths matter.
PLAYER_FIGURES = [
    "BSaracenF.NIF",  # embedded-skeleton head, base hair sheet
    "BSaracenM.NIF",  # skeleton-less head piece
    "BAvalonM.NIF",  # hair only on style 4, `_dkbrown`
    "BInconnuF_2.NIF",  # cross-race hair piece
    "BHalfOgreF.NIF",
]


@pytest.mark.parametrize("nif", PLAYER_FIGURES)
def test_prepare_covers_player_figures(client_dir, nif):
    tables = fig3.read_tables(client_dir)
    prepared = figure.prepare(client_dir, os.path.join(client_dir, "figures", nif))
    assert prepared is not None, f"{nif}: fig3 head assembly failed"
    data, textures = prepared
    names = [tex.slot for tex in textures]
    assert names[:2] == ["Head", "Hair"]
    assert "Eyes" in names
    assert figure.has_classic_heads(data) is False
    assert _shapes_named(data, "Head") and _shapes_named(data, "Hair")
    assert _shapes_named(data, "Eyes"), f"{nif}: eye geometry was not split"

    # The hair sheet must belong to one of *its own* styles: reusing another
    # race's sheet (client tables do list cross-race ids) would not sit on
    # the mesh UVs.
    desc = gamedata.figure_desc(client_dir, os.path.join(client_dir, "figures", nif))
    styles = fig3.parts_of(tables, fig3.race_id(tables, desc), "Hair")
    stem = textures[1][1].rsplit(".", 1)[0].lower()
    assert any(
        stem == part.filename.lower() or stem.startswith(part.filename.lower() + "_")
        for part in styles
    ), f"{nif}: hair sheet {stem} matches no style"


def test_export_figure_emits_the_fig3_head_and_hair(client_dir, tmp_path):
    out = str(tmp_path / "model")
    figure.export_figure(
        client_dir, os.path.join(client_dir, SARACEN), out, emit_skinned=True
    )
    with open(out + ".gltf", encoding="utf-8") as fh:
        doc = json.loads(fh.read())
    meshes = {m.get("name"): m for m in doc["meshes"]}
    assert "Head" in meshes and "Hair" in meshes and "Eyes" in meshes
    assert not any(
        name.lower().startswith(("heada", "headb")) for name in meshes if name
    )
    head_ext = meshes["Head"]["extensions"]["CS_camelot_texture"]
    hair_ext = meshes["Hair"]["extensions"]["CS_camelot_texture"]
    assert head_ext["name"].lower() == "sar_f_head01.dds"
    assert hair_ext["name"].lower().startswith("sar_f_hair01")
    assert hair_ext.get("alpha") is True
    assert hair_ext.get("doubleSided") is True
    eyes_ext = meshes["Eyes"]["extensions"]["CS_camelot_texture"]
    assert eyes_ext["name"].lower().startswith("eyecolor_")
    # The embedded sheets must be viewer-readable: DXT1 sheets cutting texels
    # with 1-bit alpha are re-encoded to uncompressed A8R8G8B8 (DDSLoader
    # reads DXT1 as opaque and would paint the cut texels black).
    with open(out + ".bin", "rb") as fh:
        bin_bytes = fh.read()
    for ext in (hair_ext, eyes_ext):
        bv = doc["bufferViews"][ext["bufferView"]]
        raw = bin_bytes[bv.get("byteOffset", 0): bv.get("byteOffset", 0) + bv["byteLength"]]
        assert raw[:4] == b"DDS ", ext["name"]
        pf_flags = struct.unpack_from("<I", raw, 80)[0]
        assert pf_flags & 0x1, ext["name"]  # ALPHAPIXELS
        assert not pf_flags & 0x4, ext["name"]  # uncompressed (no fourCC)


# --------------------------------------------------------------------------- #
# Native fig3 assembly (playerfig) regressions
# --------------------------------------------------------------------------- #


def _node_matrix(shape) -> list:
    from camelot.nifutil import node_local_matrix

    return [[round(float(v), 9) for v in row] for row in (node_local_matrix(shape) or [])]


def test_playerfig_eyes_inherit_the_head_node_frame(client_dir):
    """The split ``Eyes`` node must keep the head node's authoring transform.

    Fig3 head pieces author their raw vertices off-centre and carry the
    recentring on the node (``fro_m_head01``: -11.2 x; every race ~-3.5 in
    depth). An identity ``Eyes`` node left the eyeballs sunk in the skull
    (Briton/Highlander) or floating 11 units aside (Frostalf Male).
    """
    from camelot import playerfig

    for desc in ("Frostalf Male", "Briton Female", "Highlander Female"):
        prepared = playerfig.build(client_dir, desc)
        assert prepared is not None, desc
        data, _textures = prepared
        head = _shapes_named(data, "Head")[0]
        eyes = _shapes_named(data, "Eyes")[0]
        assert _node_matrix(eyes) == _node_matrix(head), desc
        # the check is only meaningful on pieces with a non-trivial frame
        assert abs(_node_matrix(head)[1][3]) > 1.0, desc


def test_player_race_codes_and_rigs_resolve(client_dir):
    """All 36 playable (race, gender) rows must resolve their skeleton code.

    fig3map spells Norseman as "Norse Male/Female": the code lookup used to
    return None, so `rig_for` had no skeleton and the character endpoint
    500ed on both Norseman genders.
    """
    from camelot import playerfig

    tables = fig3.read_tables(client_dir)
    for rid in range(1, 37):
        desc = tables.race_desc[rid]
        code = fig3.race_short(tables, rid, "female" in desc.lower())
        assert code, f"rid {rid} ({desc}): no skeleton code"
    for desc in ("Norseman Male", "Norseman Female"):
        assert playerfig.build(client_dir, desc) is not None, desc


def test_playerfig_assembles_the_lbody_slot(client_dir):
    """Highlander/Norse body sets ship the fig3 ``LBody`` piece (slot 2): it
    must be attached (a pelvis hole otherwise) and textured like the legs."""
    from camelot import playerfig

    tables = fig3.read_tables(client_dir)
    assert fig3.parts_of(tables, 14, "LBody"), "corpus: no Highlander F LBody pieces"
    prepared = playerfig.build(client_dir, "Highlander Female")
    assert prepared is not None
    data, textures = prepared
    assert _shapes_named(data, "Lbody"), "LBody piece was not attached"
    assert "Lbody" in {tex.slot for tex in textures}
    # races with an empty fig3map LBody row attach no phantom mesh
    saracen, _textures = playerfig.build(client_dir, "Saracen Female")
    assert not _shapes_named(saracen, "Lbody")
