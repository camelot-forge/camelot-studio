"""Unit tests for the serve HTTP API (synthetic client, no game files)."""
import shutil

import pytest
from fastapi.testclient import TestClient

from camelot.serve import create_app
from camelot.serve.config import ServeConfig


def _make_app(tmp_path, nif_path, **overrides):
    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True, exist_ok=True)
    (root / "anims").mkdir(exist_ok=True)
    shutil.copy(nif_path, root / "figures" / "synth.nif")
    (root / "anims" / "idle.kfa").write_bytes(b"kfa")
    settings = {"static_dir": None, **overrides}
    cfg = ServeConfig(root=root, cache_dir=tmp_path / "cache", **settings)
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


class TestAssetStates:
    """The figure's own anim set, served for any nif.

    The creature half of `/character/…/states`: the same tables (`monnifs` →
    `anims` → `animnifs`), so a viewer can offer the clips the client itself
    would play instead of only the idle its resolver happens to pick.
    """

    def _app(self, tmp_path, nif_path):
        """`synth.nif` plus an anim set naming it (Idle 16f@3, Walk 32f@4)."""
        from camelot.mpak import write_mpak

        app = _make_app(tmp_path, nif_path)
        root = tmp_path / "client"
        (root / "anims" / "walk.kfa").write_bytes(b"kfa")

        def csv(rows):
            return ("\n".join(",".join(r) for r in rows) + "\n").encode("latin1")

        write_mpak(
            str(root / "gamedata.mpk"),
            "gamedata",
            [
                (
                    "monnifs.csv",
                    csv(
                        [
                            ["Information", "", "NIF", "Anim"],
                            ["ID", "Textual Name", "Name", "Anim Set ID"],
                            ["1", "Synth", "synth", "10"],
                        ]
                    ),
                ),
                (
                    "anims.csv",
                    csv(
                        [
                            ["Anim", "", "", ""],
                            ["Anim Set ID", "Name", "Idle", "Walk"],
                            ["10", "Synth", "1", "2"],
                        ]
                    ),
                ),
                (
                    "animnifs.csv",
                    csv(
                        [
                            ["Anim", "", "", "", "", "base", " "],
                            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
                            ["1", "a", "idle.kfa", "16", "3", "15", "loop"],
                            ["2", "b", "walk.kfa", "32", "4", "15", "clamp"],
                        ]
                    ),
                ),
            ],
        )
        return app

    def _ids(self, client):
        items = client.get("/api/v1/assets").json()["items"]
        nif = next(a["id"] for a in items if a["type"] == "nif")
        kfa = next(a["id"] for a in items if a["type"] == "kfa")
        return nif, kfa

    def test_states_list_the_sets_slots_with_the_game_timing(self, tmp_path, nif_path):
        client = _client(self._app(tmp_path, nif_path))
        nif, _kfa = self._ids(client)
        r = client.get(f"/api/v1/assets/{nif}/states")
        assert r.status_code == 200, r.text
        body = r.json()
        assert [s["slot"] for s in body] == ["Idle", "Walk"]
        idle, walk = body
        assert idle["kfa"] == "idle.kfa" and idle["loop"] is True
        # frames / fps from `animnifs`, not the placeholder file's own bytes
        assert idle["duration"] == pytest.approx(16 / 3)
        assert walk["loop"] is False
        assert walk["duration"] == pytest.approx(8.0)

    def test_unknown_state_is_404(self, tmp_path, nif_path):
        client = _client(self._app(tmp_path, nif_path))
        nif, _kfa = self._ids(client)
        r = client.get(f"/api/v1/assets/{nif}/anim", params={"state": "Nope"})
        assert r.status_code == 404
        assert "Nope" in r.json()["detail"]

    def test_states_reject_non_nif(self, tmp_path, nif_path):
        client = _client(self._app(tmp_path, nif_path))
        _nif, kfa = self._ids(client)
        assert client.get(f"/api/v1/assets/{kfa}/states").status_code == 400


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


class TestUnknownApiPathsAreJson:
    """A route this build does not have is a fact about the *daemon*, and the
    page's own fallback must not answer it: a client told `index.html` where it
    expected JSON shows the browser's parse error instead of the reason
    (measured 2026-10-17 — a page pointed at a daemon older than the front read
    "JSON.parse: unexpected character at line 1 column 1")."""

    def test_an_unknown_api_path_is_a_json_404_that_names_it(self, tmp_path, nif_path):
        built = tmp_path / "build"
        built.mkdir()
        (built / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
        client = _client(_make_app(tmp_path, nif_path, static_dir=built))

        r = client.get("/api/v1/nope/deeper")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["detail"] == "no such route: /api/v1/nope/deeper"

    def test_the_page_fallback_still_answers_a_route(self, tmp_path, nif_path):
        built = tmp_path / "build"
        built.mkdir()
        (built / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
        client = _client(_make_app(tmp_path, nif_path, static_dir=built))

        r = client.get("/home")
        assert r.status_code == 200 and r.text.startswith("<!doctype html>")
