"""Serve-api character endpoints (corpus tests)."""
import json
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
