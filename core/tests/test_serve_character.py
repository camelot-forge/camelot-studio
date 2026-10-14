"""Serve-api character endpoints (corpus tests)."""
import json
import os
import struct

import pytest

pytestmark = pytest.mark.usefixtures("client_dir")


from camelot.serve import create_app
from camelot.serve.config import ServeConfig


def _client(client_dir, tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(ServeConfig(root=str(client_dir), cache_dir=tmp_path / "c"))
    return TestClient(app, base_url="http://127.0.0.1")


def _glb_json(buf: bytes) -> dict:
    offset = 12
    total = struct.unpack_from("<I", buf, 8)[0]
    while offset < total:
        length, kind = struct.unpack_from("<II", buf, offset)
        offset += 8
        if kind == 0x4E4F534A:
            return json.loads(buf[offset:offset + length])
        offset += length
    raise AssertionError("no JSON chunk")


def test_character_options_counts_saracen_f(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    r = client.get("/api/v1/character/4/true/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["race"] == "Saracen Female"
    assert body["faces"] >= 7
    assert body["eye_colors"] == 8
    assert body["hair_colors"] == 16


def test_character_model_carries_engine_tints(client_dir, tmp_path):
    """Skin tone and hair colour travel as `fig3tints.csv` tints on the
    slot's CS_camelot_texture extension (the viewer multiplies the sheet)."""
    client = _client(client_dir, tmp_path)
    r = client.get("/api/v1/character/4/true/model?skin_tone=2&hair_color=2")
    assert r.status_code == 200, r.text
    doc = _glb_json(r.content)
    ext = {
        mesh["name"]: mesh["extensions"]["CS_camelot_texture"]
        for mesh in doc["meshes"]
    }
    assert ext["Head"]["tint"] == [223, 207, 166]  # Saracen Skin 2
    assert ext["Hair"]["tint"] == [110, 107, 59]  # Hair Tint 25 (style 1, colour 2)
    assert "tint" not in ext["Eyes"]
    assert "tint" not in ext["Body"]  # armour sheets stay untinted


def test_character_model_preserves_single_sided_hair(client_dir, tmp_path):
    from camelot import fig3

    tables = fig3.read_tables(client_dir)
    race_id = next(
        rid for rid, desc in tables.race_desc.items() if desc == "Briton Female"
    )
    client = _client(client_dir, tmp_path)
    r = client.get(f"/api/v1/character/{race_id}/true/model")
    assert r.status_code == 200, r.text
    doc = _glb_json(r.content)
    hair = next(
        mesh["extensions"]["CS_camelot_texture"]
        for mesh in doc["meshes"]
        if mesh["name"] == "Hair"
    )
    assert hair["doubleSided"] is False


def test_character_cloak_uses_the_default_cloak_sheet(client_dir, tmp_path):
    """The fig3 cloak pieces carry no texture of their own: the sheet comes
    from the equipped cloak item. The client's default cloak object
    (`objects.csv` id 57) points at Cata skin 4972 = `Cat_cloak.dds`, the same
    sheet DAoC-Tools bakes for `PART:cloak:1`. Without it the glTF slot
    fallback textured the cloak with the body sheet."""
    client = _client(client_dir, tmp_path)

    def slots(url):
        doc = _glb_json(client.get(url).content)
        return {
            mesh["name"]: mesh["extensions"]["CS_camelot_texture"]["name"]
            for mesh in doc["meshes"]
        }

    with_cloak = slots("/api/v1/character/4/true/model?cloak=1")
    assert with_cloak["Cloak"] == "Cat_cloak.dds"

    # no cloak piece -> no cloak mesh and no cloak sheet embedded
    without = slots("/api/v1/character/4/true/model")
    assert "Cloak" not in without
    assert "Cat_cloak.dds" not in without.values()


def test_character_options_keeps_positional_hair_slots(client_dir, tmp_path):
    """Briton male's client row is Hair 1..6, **Bald**, Hair 8: the builder
    must expose all eight positions and no hair at the Bald one."""
    client = _client(client_dir, tmp_path)
    r = client.get("/api/v1/character/1/false/options")
    assert r.status_code == 200, r.text
    assert r.json()["hair_styles"] == 8

    def mesh_names(url):
        doc = _glb_json(client.get(url).content)
        return {mesh["name"] for mesh in doc["meshes"]}

    assert "Hair" not in mesh_names("/api/v1/character/1/false/model?hair_style=7")
    assert "Hair" in mesh_names("/api/v1/character/1/false/model?hair_style=8")


def test_player_rows_resolve_a_figure_and_idle(client_dir):
    """Every playable race row finds its monnifs figure and idle KFA.

    The rows differ in spelling ("Male Norse", "Hibernian Elf male",
    "Cata … Stone Male"): the matcher tolerates word order and realm
    prefixes and prefers the player rows over the Cata monster ones.
    """
    from camelot import fig3, gamedata
    from camelot.anims import resolve_idle_kfa

    tables = fig3.read_tables(str(client_dir))
    for race_id in range(1, 37):
        desc = tables.race_desc.get(race_id)
        if not desc:
            continue
        name = gamedata.figure_name_for_desc(str(client_dir), desc)
        assert name, f"{desc}: no monnifs figure"
        kfa = resolve_idle_kfa(
            str(client_dir), os.path.join(str(client_dir), "figures", f"{name}.NIF")
        )
        assert kfa, f"{desc}: no idle KFA"


def test_foreign_rig_idle_keeps_translations_at_bind(client_dir, tmp_path):
    """`Cat_troll_idle.kfa` was authored on another rig: its Head/Neck locals
    sit ~3.5 units off `tro_m_skeleton.nif` and sheared the Troll male face.
    The builder must keep those bones at the figure bind while a matching
    pair (Saracen male) keeps applying its absolute translations."""
    client = _client(client_dir, tmp_path)

    troll = client.get("/api/v1/character/31/false/anim")
    assert troll.status_code == 200, troll.text
    by_node = {track["node"]: track for track in troll.json()["tracks"]}
    assert by_node["Bip01 Head"]["quats"]  # rotations still animate
    assert "positions" not in by_node["Bip01 Head"]
    assert "positions" not in by_node["Bip01 Neck"]

    saracen = client.get("/api/v1/character/3/false/anim")
    assert saracen.status_code == 200, saracen.text
    saracen_by_node = {track["node"]: track for track in saracen.json()["tracks"]}
    assert "positions" in saracen_by_node["Bip01 Head"]


@pytest.mark.parametrize(
    ("race_id", "female"),
    [(1, False), (4, True), (18, True), (29, False)],
)
def test_character_anim_serves_the_player_idle(client_dir, tmp_path, race_id, female):
    """The clip is engine-faithful: every channel the KFA carries is served
    absolute, statics included (the values form a consistent pose; dropping
    or rebasing single channels broke the chain — the skill's first rule)."""
    client = _client(client_dir, tmp_path)
    r = client.get(f"/api/v1/character/{race_id}/{female}/anim")
    assert r.status_code == 200, r.text
    clip = r.json()
    assert clip["duration"] > 0 and clip["kfa"].lower().startswith("cat_")
    nodes = {track["node"] for track in clip["tracks"]}
    assert len(clip["tracks"]) > 40, f"{len(clip['tracks'])} tracks"
    assert any(node.startswith("Bip01") for node in nodes)
    # the hands are posed by the clip (the male idles animate one hand)
    assert any("Hand" in node for node in nodes)


def test_character_model_skinned_emits_joints(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    r = client.get("/api/v1/character/4/true/model?skinned=true")
    assert r.status_code == 200, r.text
    doc = _glb_json(r.content)
    prim = doc["meshes"][0]["primitives"][0]
    assert "JOINTS_0" in prim["attributes"] and "WEIGHTS_0" in prim["attributes"]


def test_character_texture_swap_ships_the_tint(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    base = "/api/v1/character/4/true/texture"

    r = client.get(f"{base}?kind=skin_tone&skin_tone=2")
    assert r.status_code == 200 and r.content[:4] == b"DDS "
    assert r.headers["x-camelot-tint"] == "223,207,166"

    r = client.get(f"{base}?kind=hair_color&hair_style=1&hair_color=2")
    assert r.status_code == 200 and r.content[:4] == b"DDS "
    assert r.headers["x-camelot-tint"] == "110,107,59"

    r = client.get(f"{base}?kind=eye_color&eye_color=2")
    assert r.status_code == 200 and "x-camelot-tint" not in r.headers

    # the eye swap serves the baked face×eyecolor composite (per eye colour)
    r1 = client.get(f"{base}?kind=eye_color&face=1&eye_color=1")
    r2 = client.get(f"{base}?kind=eye_color&face=1&eye_color=2")
    assert r1.status_code == r2.status_code == 200
    assert r1.content[:4] == b"DDS " and r1.content != r2.content
    assert "x-camelot-tint" not in r1.headers

    # white tints are the no-op: the sheet keeps its authored colours
    r = client.get(f"{base}?kind=skin_tone&skin_tone=1")
    assert r.status_code == 200 and "x-camelot-tint" not in r.headers

    # geometry kinds stay a client-side rebuild
    r = client.get(f"{base}?kind=face&face=2")
    assert r.status_code == 501


def _slot_sheets(client, url):
    doc = _glb_json(client.get(url).content)
    return {
        mesh["name"]: mesh["extensions"]["CS_camelot_texture"]["name"]
        for mesh in doc["meshes"]
    }


def test_character_model_resolves_armour_from_the_pskin_material(
    client_dir, tmp_path
):
    """The armour sheets come from `pskins.csv` for the requested material,
    not from a frozen preview: each body slot must carry the sheet the
    client's material family declares for it."""
    client = _client(client_dir, tmp_path)
    sheets = _slot_sheets(client, "/api/v1/character/4/true/model?material=Plate")

    from camelot import appearance as app

    plate = app.pskins(str(client_dir)).by_name("Plate")
    for slot in ("Body", "Arms", "Gloves", "Legs", "Boots"):
        assert sheets[slot] == plate.sheet(slot, female=True), slot


def test_the_lbody_sheet_reaches_the_highlander(client_dir, tmp_path):
    """`Lbody` is a real slot but only Highlander ships the fig3 piece
    (`LBody01_Hig_*`), and its UVs sample the upper half of the legs atlas —
    so the material's own Lbody sheet, not the legs one, is what must land
    on that mesh."""
    client = _client(client_dir, tmp_path)
    sheets = _slot_sheets(
        client, "/api/v1/character/13/false/model?material=Plate"
    )

    from camelot import appearance as app

    plate = app.pskins(str(client_dir)).by_name("Plate")
    assert "Lbody" in sheets
    assert sheets["Lbody"] == plate.sheet("Lbody", female=False)
    assert sheets["Lbody"] != sheets["Legs"]


def test_two_materials_give_two_different_armour_sheets(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    leather = _slot_sheets(client, "/api/v1/character/4/true/model?material=Hard Leather")
    plate = _slot_sheets(client, "/api/v1/character/4/true/model?material=Plate")
    assert leather["Body"] != plate["Body"]
    assert leather["Legs"] != plate["Legs"]


def test_armour_material_is_gendered(client_dir, tmp_path):
    """`b_plt1_body1m` / `b_plt1_body1f` are different sheets: the male and
    female previews must not share the body texture."""
    client = _client(client_dir, tmp_path)
    male = _slot_sheets(client, "/api/v1/character/1/false/model?material=Plate")
    female = _slot_sheets(client, "/api/v1/character/2/true/model?material=Plate")
    assert male["Body"] != female["Body"]
    assert male["Body"].lower().endswith("m.tga")
    assert female["Body"].lower().endswith("f.tga")


def test_character_options_expose_the_armour_materials(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    body = client.get("/api/v1/character/4/true/options").json()
    materials = body["materials"]
    assert len(materials) >= 8
    assert "Plate" in materials
    # the default the builder starts on must itself be resolvable
    assert body["material"] in materials


def test_unknown_material_falls_back_instead_of_failing(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    assert client.get("/api/v1/character/4/true/model?material=nope").status_code == 200


def test_character_states_lists_the_client_slots(client_dir, tmp_path):
    """The state list is the figure's anim set, read from the client's
    tables: a server must be able to name the state it wants, not only ever
    receive the one idle the builder happened to pick."""
    client = _client(client_dir, tmp_path)
    body = client.get("/api/v1/character/4/true/states").json()
    slots = [s["slot"] for s in body]
    assert "Idle" in slots
    assert len(slots) > 1
    idle = next(s for s in body if s["slot"] == "Idle")
    assert idle["kfa"].lower().endswith(".kfa")
    assert idle["loop"] is True
    assert idle["duration"] > 0
    # the id is the animnifs row that owns the clip
    assert idle["animnifs_id"].isdigit()
    # the list is a table read: it must not open the clips to fill a rate
    assert idle.get("rate") is None


def test_character_anim_can_serve_a_named_state(client_dir, tmp_path):
    client = _client(client_dir, tmp_path)
    states = client.get("/api/v1/character/4/true/states").json()
    walk = next((s for s in states if "walk" in s["slot"].lower()), None)
    assert walk is not None, [s["slot"] for s in states]

    clip = client.get(f"/api/v1/character/4/true/anim?state={walk['slot']}").json()
    assert clip["kfa"].lower() == walk["kfa"].lower()
    assert clip["loop"] is walk["loop"]
    assert clip["duration"] == pytest.approx(walk["duration"])
    assert clip["rate"] > 0, "a played state carries the key-density rate"
    assert clip["tracks"], "a named state must carry its tracks"


def test_character_anim_transports_the_loop_flag(client_dir, tmp_path):
    """`clamp` clips are one-shots: the viewer must learn that from the
    response instead of always wrapping."""
    client = _client(client_dir, tmp_path)
    states = client.get("/api/v1/character/4/true/states").json()
    clamps = [s for s in states if not s["loop"]]
    if not clamps:
        pytest.skip("this race has no clamp state")
    clip = client.get(f"/api/v1/character/4/true/anim?state={clamps[0]['slot']}").json()
    assert clip["loop"] is False


def test_character_anim_rejects_an_unknown_state(client_dir, tmp_path):
    """The builder shows the daemon's own words beside the pick it refused."""
    client = _client(client_dir, tmp_path)
    res = client.get("/api/v1/character/4/true/anim?state=Dance")
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown state: Dance"


def test_character_anim_default_is_still_the_idle(client_dir, tmp_path):
    """No state asked for keeps the existing behaviour, so the builder and
    the asset route are untouched."""
    client = _client(client_dir, tmp_path)
    plain = client.get("/api/v1/character/4/true/anim").json()
    named = client.get("/api/v1/character/4/true/anim?state=Idle").json()
    assert plain["kfa"].lower() == named["kfa"].lower()
