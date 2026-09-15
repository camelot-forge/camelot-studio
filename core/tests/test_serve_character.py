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


def test_player_rows_resolve_a_figure_and_idle(client_dir):
    """Every playable race row finds its monnifs figure and idle KFA.

    The rows differ in spelling ("Male Norse", "Hibernian Elf male",
    "Cata … Stone Male"): the matcher tolerates word order and realm
    prefixes and prefers the player rows over the Cata monster ones.
    """
    from camelot import fig3, gamedata
    from camelot.anims import resolve_idle_kfa

    tables = fig3.read_tables(str(client_dir))
    for race_id in range(1, 31):
        desc = tables.race_desc.get(race_id)
        if not desc:
            continue
        name = gamedata.figure_name_for_desc(str(client_dir), desc)
        assert name, f"{desc}: no monnifs figure"
        kfa = resolve_idle_kfa(
            str(client_dir), os.path.join(str(client_dir), "figures", f"{name}.NIF")
        )
        assert kfa, f"{desc}: no idle KFA"


@pytest.mark.parametrize(
    ("race_id", "female"),
    [(1, False), (4, True), (18, True), (29, False)],
)
def test_character_anim_serves_the_player_idle(client_dir, tmp_path, race_id, female):
    client = _client(client_dir, tmp_path)
    r = client.get(f"/api/v1/character/{race_id}/{female}/anim")
    assert r.status_code == 200, r.text
    clip = r.json()
    assert clip["duration"] > 0 and clip["kfa"]
    nodes = {track["node"] for track in clip["tracks"]}
    assert clip["tracks"] and any(node.startswith("Bip01") for node in nodes)
    # static channels are dropped: they hold the authoring rig's rest pose
    # (the hands sheared on the 15-37 degree finger/hand rests)
    for track in clip["tracks"]:
        if "times" in track:
            assert len({tuple(q) for q in track["quats"]}) > 1, track["node"]


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
