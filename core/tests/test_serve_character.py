"""Serve-api character-options endpoint (corpus tests)."""
import pytest

pytestmark = pytest.mark.usefixtures("client_dir")


from camelot.serve import create_app
from camelot.serve.config import ServeConfig


def test_character_options_counts_saracen_f(client_dir, tmp_path):
    from fastapi.testclient import TestClient

    app = create_app(ServeConfig(root=str(client_dir), cache_dir=tmp_path / "c"))
    client = TestClient(app, base_url="http://127.0.0.1")
    r = client.get("/api/v1/character/4/true/options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["race"] == "Saracen Female"
    assert body["faces"] >= 7
    assert body["eye_colors"] == 8
    assert body["hair_colors"] == 16
