"""Unit tests for the serve HTTP API (synthetic client, no game files)."""
import shutil

from fastapi.testclient import TestClient

from camelot.serve import create_app
from camelot.serve.config import ServeConfig


def _make_app(tmp_path, nif_path, **overrides):
    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True, exist_ok=True)
    (root / "anims").mkdir(exist_ok=True)
    shutil.copy(nif_path, root / "figures" / "synth.nif")
    (root / "anims" / "idle.kfa").write_bytes(b"kfa")
    cfg = ServeConfig(root=root, cache_dir=tmp_path / "cache", static_dir=None, **overrides)
    return create_app(cfg)


def _client(app):
    return TestClient(app, base_url="http://localhost")


class TestBasics:
    def test_health(self, tmp_path, nif_path):
        r = _client(_make_app(tmp_path, nif_path)).get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_root_info(self, tmp_path, nif_path):
        r = _client(_make_app(tmp_path, nif_path)).get("/api/v1/root")
        body = r.json()
        assert r.status_code == 200
        assert body["counts"]["nif"] == 1
        assert body["counts"]["kfa"] == 1
        assert body["total"] == 2

    def test_assets_page(self, tmp_path, nif_path):
        r = _client(_make_app(tmp_path, nif_path)).get("/api/v1/assets")
        body = r.json()
        assert body["total"] == 2
        assert {a["type"] for a in body["items"]} == {"nif", "kfa"}


class TestAssetDetail:
    def _nif_id(self, client):
        items = client.get("/api/v1/assets", params={"type": "nif"}).json()["items"]
        return items[0]["id"]

    def test_detail_has_metadata(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get(f"/api/v1/assets/{self._nif_id(client)}")
        assert r.status_code == 200
        meta = r.json()["metadata"]
        assert meta["version"] == "0x4020200"
        assert any(n["name"] == "Mesh" for n in meta["nodes"])

    def test_unknown_asset_is_404(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        assert client.get("/api/v1/assets/nope").status_code == 404

    def test_model_is_glb(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get(f"/api/v1/assets/{self._nif_id(client)}/model")
        assert r.status_code == 200
        assert r.headers["content-type"] == "model/gltf-binary"
        assert r.content[:4] == b"glTF"

    def test_model_skinned_flag_returns_glb(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get(f"/api/v1/assets/{self._nif_id(client)}/model", params={"skinned": True})
        assert r.status_code == 200
        assert r.headers["content-type"] == "model/gltf-binary"
        assert r.content[:4] == b"glTF"

    def test_model_rejects_non_nif(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        kfa = client.get("/api/v1/assets", params={"type": "kfa"}).json()["items"][0]
        assert client.get(f"/api/v1/assets/{kfa['id']}/model").status_code == 400

    def test_anim_is_404_without_matching_kfa(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get(f"/api/v1/assets/{self._nif_id(client)}/anim")
        assert r.status_code == 404

    def test_anim_rejects_non_nif(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        kfa = client.get("/api/v1/assets", params={"type": "kfa"}).json()["items"][0]
        assert client.get(f"/api/v1/assets/{kfa['id']}/anim").status_code == 400


class TestSecurity:
    def test_foreign_host_is_rejected(self, tmp_path, nif_path):
        app = _make_app(tmp_path, nif_path)
        client = TestClient(app, base_url="http://evil.example")
        assert client.get("/api/v1/health").status_code == 403

    def test_foreign_origin_is_rejected(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get("/api/v1/health", headers={"origin": "http://evil.example"})
        assert r.status_code == 403

    def test_local_origin_is_allowed(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        r = client.get("/api/v1/health", headers={"origin": "http://localhost"})
        assert r.status_code == 200


class TestEvents:
    def test_events_route_is_registered(self, tmp_path, nif_path):
        client = _client(_make_app(tmp_path, nif_path))
        paths = client.get("/api/openapi.json").json()["paths"]
        assert "/api/v1/events" in paths

    def test_sse_frame_format(self):
        from camelot.serve.api import sse_frame

        assert sse_frame("asset.added", {"id": "x"}) == (
            'event: asset.added\ndata: {"id":"x"}\n\n'
        )
