"""The feed service: one directory, two environments (`F §D3`).

The feed on disk must not know whether it is served locally or online; the
service owns the URL and the game address, and rewrites them per request.
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from camelot import feed, feedserve


def _feed(root):
    root.mkdir()
    (root / feed.VERSION_FILE).write_text("7\n", encoding="utf-8")
    (root / feed.PATCHLIST_FILE).write_text(
        "http://old.example/feed/\n"
        "figures/Boar.NIF\tabcdef0123456789abcdef0123456789\t12\n",
        encoding="utf-8",
    )
    (root / feed.LAUNCHER_FILE).write_text(
        json.dumps(
            {"shard": "Avalon", "feed": "http://old.example/feed/", "server": "old:1"}
        ),
        encoding="utf-8",
    )
    (root / "figures").mkdir()
    (root / "figures" / "Boar.NIF").write_bytes(b"a boar")
    return root


def _serving(where, feed_url="https://patch.example", server="play.example:10300"):
    bound = feedserve.server_for(where, feed_url, server, host="127.0.0.1", port=0)
    thread = threading.Thread(target=bound.serve_forever, daemon=True)
    thread.start()
    return bound


def _get(bound, path):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{bound.server_port}{path}", timeout=10
    ) as answer:
        return answer.status, answer.read()


def test_the_service_rewrites_the_url_and_the_game_address(tmp_path):
    where = _feed(tmp_path / "feed")
    bound = _serving(where)
    try:
        status, body = _get(bound, f"/{feed.PATCHLIST_FILE}")
        lines = body.decode("utf-8").splitlines()
        assert status == 200
        assert lines[0] == "https://patch.example/"
        assert lines[1].startswith("figures/Boar.NIF\t")

        status, body = _get(bound, f"/{feed.LAUNCHER_FILE}")
        document = json.loads(body)
        assert status == 200
        assert document["feed"] == "https://patch.example/"
        assert document["server"] == "play.example:10300"
        assert document["shard"] == "Avalon"

        status, body = _get(bound, "/figures/Boar.NIF")
        assert status == 200 and body == b"a boar"

        status, body = _get(bound, "/healthz")
        assert status == 200 and json.loads(body)["server"] == "play.example:10300"
    finally:
        bound.shutdown()
        bound.server_close()


def test_a_feed_without_launcher_json_is_a_404_not_a_broken_feed(tmp_path):
    """`launcher.json` is optional (`C §D15`): the launcher can still read the
    version and the patchlist, and says it has no game address."""
    where = _feed(tmp_path / "feed")
    (where / feed.LAUNCHER_FILE).unlink()
    bound = _serving(where)
    try:
        status, _body = _get(bound, f"/{feed.VERSION_FILE}")
        assert status == 200
        with pytest.raises(urllib.error.HTTPError) as missing:
            _get(bound, f"/{feed.LAUNCHER_FILE}")
        assert missing.value.code == 404
    finally:
        bound.shutdown()
        bound.server_close()


def test_a_directory_is_not_listed(tmp_path):
    where = _feed(tmp_path / "feed")
    bound = _serving(where)
    try:
        with pytest.raises(urllib.error.HTTPError) as refused:
            _get(bound, "/figures/")
        assert refused.value.code == 404
    finally:
        bound.shutdown()
        bound.server_close()


def test_main_refuses_without_the_public_values(monkeypatch, capsys):
    monkeypatch.delenv("CAMELOT_FEED_URL", raising=False)
    monkeypatch.delenv("CAMELOT_GAME_SERVER", raising=False)

    assert feedserve.main([]) == 2
    assert "CAMELOT_FEED_URL" in capsys.readouterr().err
