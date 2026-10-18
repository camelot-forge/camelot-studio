"""The Console's read-only face (P7 7a).

The state file is the contract (`console.md` §D3): these tests build one, then
check that what the Console answers is what the file says — including the
reload it cannot know without the deploy log, and the measured channel.
"""

import base64
import http.client
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from camelot import console, feed, installer


def _environment(tmp_path, *, needs=("reload",)):
    root = tmp_path / "env"
    state = {
        "format": installer.STATE_FORMAT,
        "project": "Forge Project",
        "baseline": {"files": 110, "bytes": 72052584, "sha256": "sha256:abc"},
        "releases": {
            "0001": {
                "at": "2026-10-18T10:27:40Z",
                "needs": list(needs),
                "tables": {"worldobject": {"written": 1}},
                "originals": {"worldobject": {"keep-1": {"X": "1"}}},
            }
        },
    }
    installer.written_state(root, state)
    installer.write_applied(
        root,
        1,
        {
            "format": installer.APPLIED_FORMAT,
            "release": 1,
            "at": "2026-10-18T10:27:40Z",
            "rows": [
                {
                    "table": "worldobject",
                    "key": "keep-1",
                    "action": "write",
                    "why": "1 value(s): X",
                }
            ],
            "written": 1,
            "removed": 0,
            "skipped": 0,
            "refused": 0,
        },
    )
    installer.releases_folder(root, 1).mkdir(parents=True)
    (installer.folder(root) / "env.json").write_text(
        json.dumps(
            {
                "format": "camelot.env/1",
                "database": "forge_project_staging",
                "server": {"host": "127.0.0.1", "port": 3307, "user": "root"},
            }
        ),
        encoding="utf-8",
    )
    return root


def _project(tmp_path, entries):
    root = tmp_path / "project"
    (root / "deploy").mkdir(parents=True)
    (root / "deploy" / "log.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )
    return root


def _feed(tmp_path, *, version=1, release=1):
    root = tmp_path / "feed"
    root.mkdir()
    (root / feed.VERSION_FILE).write_text(f"{version}\n", encoding="utf-8")
    (root / feed.LAUNCHER_FILE).write_text(
        json.dumps(
            {"feed": "http://feed.example/", "server": "127.0.0.1:10302", "shard": "Test"}
        ),
        encoding="utf-8",
    )
    (root / feed.RECORD_FILE).write_text(
        json.dumps(
            {
                "release": release,
                "version": version,
                "built": "2026-10-18T11:00:00Z",
                "base_url": "http://feed.example/",
            }
        ),
        encoding="utf-8",
    )
    return root


def _listening():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener, f"127.0.0.1:{listener.getsockname()[1]}"


def _apply(at="2026-10-18T10:27:40Z"):
    return {
        "kind": "apply",
        "env": "staging",
        "release": 1,
        "outcome": "ok",
        "rows": {"written": 1, "removed": 0, "skipped": 0},
        "at": at,
    }


def _reload(at="2026-10-18T10:28:00Z"):
    return {"kind": "reload", "env": "staging", "release": 1, "needs": ["reload"], "at": at}


def test_the_console_reads_the_installer_state_and_the_feed(tmp_path):
    root = _environment(tmp_path)
    project = _project(tmp_path, [_apply(), _reload()])
    feed_dir = _feed(tmp_path)
    listener, address = _listening()
    try:
        document = console.live(
            root,
            env="staging",
            project=project,
            feed=feed_dir,
            game_server=address,
        )
    finally:
        listener.close()

    assert document["format"] == console.FORMAT
    assert document["env"]["project"] == "Forge Project"
    assert document["env"]["database"] == "forge_project_staging"
    assert document["release"]["number"] == 1
    assert document["release"]["written"] == 1
    assert document["release"]["needs"] == ["reload"]
    assert document["pending"] == []
    assert document["log"]["reload"]["state"] == "done"
    assert document["feed"]["version"] == 1
    assert document["feed"]["matches"] is True
    assert document["health"]["game_server"]["open"] is True
    assert document["health"]["api"] is None
    assert document["channel"]["note"].startswith("No console stdin")
    assert document["problems"] == []
    assert document["ok"] is True


def test_a_release_applied_but_not_reloaded_is_pending(tmp_path):
    root = _environment(tmp_path)
    project = _project(tmp_path, [_apply()])
    document = console.live(root, env="staging", project=project)
    assert document["log"]["reload"]["state"] == "pending"
    assert document["log"]["reload"]["applied_at"] == "2026-10-18T10:27:40Z"


def test_a_server_without_the_log_cannot_know_the_reload(tmp_path):
    root = _environment(tmp_path)
    document = console.live(root, env="staging")
    assert document["log"]["reload"]["state"] == "unknown"
    assert "no deploy log" in document["log"]["reload"]["why"]


def test_a_reload_before_the_apply_does_not_count(tmp_path):
    root = _environment(tmp_path)
    project = _project(tmp_path, [_reload("2026-10-18T10:00:00Z"), _apply()])
    document = console.live(root, env="staging", project=project)
    assert document["log"]["reload"]["state"] == "pending"


def test_a_feed_on_another_release_is_named_not_hidden(tmp_path):
    root = _environment(tmp_path)
    feed_dir = _feed(tmp_path, version=2, release=2)
    document = console.live(root, env="staging", feed=feed_dir)
    assert document["feed"]["matches"] is False
    assert document["feed"]["release"] == 2


def test_a_closed_game_port_is_reported(tmp_path):
    root = _environment(tmp_path)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    address = f"127.0.0.1:{probe.getsockname()[1]}"
    probe.close()
    document = console.live(root, game_server=address)
    assert document["health"]["game_server"]["open"] is False
    assert document["health"]["game_server"]["error"]


def test_the_page_renders_the_same_document(tmp_path):
    root = _environment(tmp_path)
    document = console.live(root, env="staging")
    rendered = console.page(document)
    assert "Camelot Console — Forge Project" in rendered
    assert "release 0001" in rendered
    assert "keep-1" in rendered
    assert "No game address was given." in rendered
    assert "What this console can do" in rendered
    assert "No console stdin" in rendered


def _serving(document, **kwargs):
    bound = console.server_for(document, host="127.0.0.1", port=0, **kwargs)
    thread = threading.Thread(target=bound.serve_forever, daemon=True)
    thread.start()
    return bound


def _api(status=200, body=b"true"):
    """A stand-in for OpenDAoC's API: records the paths it is asked for."""
    calls: list[str] = []

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if not self.path.startswith("/utils/shutdown/"):
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    bound = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=bound.serve_forever, daemon=True)
    thread.start()
    return bound, calls


def _post(bound, path, data, credential=None):
    body = urllib.parse.urlencode(data).encode()
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if credential:
        token = base64.b64encode(credential.encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    connection = http.client.HTTPConnection("127.0.0.1", bound.server_port, timeout=10)
    try:
        connection.request("POST", path, body=body, headers=headers)
        answer = connection.getresponse()
        return answer.status, answer.getheader("Location") or "", answer.read()
    finally:
        connection.close()


def _get(bound, path, credential=None):
    request = urllib.request.Request(f"http://127.0.0.1:{bound.server_port}{path}")
    if credential:
        token = base64.b64encode(credential.encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=10) as answer:
        return answer.status, answer.read()


def test_the_page_and_the_json_are_served_without_a_credential_on_loopback(tmp_path):
    root = _environment(tmp_path)
    bound = _serving(lambda: console.live(root, env="staging"))
    try:
        status, body = _get(bound, "/")
        assert status == 200 and b"release 0001" in body
        status, body = _get(bound, "/api/live")
        assert status == 200 and json.loads(body)["release"]["number"] == 1
        status, body = _get(bound, "/healthz")
        assert status == 200 and json.loads(body) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as missing:
            _get(bound, "/nope")
        assert missing.value.code == 404
    finally:
        bound.shutdown()
        bound.server_close()


def test_a_password_guards_every_route_but_healthz(tmp_path):
    root = _environment(tmp_path)
    bound = _serving(
        lambda: console.live(root, env="staging"), user="operator", password="s3cret"
    )
    try:
        status, _body = _get(bound, "/healthz")
        assert status == 200
        with pytest.raises(urllib.error.HTTPError) as refused:
            _get(bound, "/api/live")
        assert refused.value.code == 401
        status, body = _get(bound, "/api/live", credential="operator:s3cret")
        assert status == 200 and json.loads(body)["release"]["number"] == 1
        status, _body = _get(bound, "/", credential="operator:s3cret")
        assert status == 200
    finally:
        bound.shutdown()
        bound.server_close()


def test_the_api_half_is_read_with_the_operator_password(tmp_path):
    root = _environment(tmp_path)

    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/stats":
                body = json.dumps({"Total": 3}).encode()
            elif self.path == "/stats/uptime":
                body = json.dumps({"Uptime": "0d 0h 1m 00s"}).encode()
            elif self.path == "/utils/query_clients/secret":
                body = json.dumps({"forge": {"PrivLevel": 3}}).encode()
            else:
                self.send_error(401)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    bound = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=bound.serve_forever, daemon=True)
    thread.start()
    try:
        document = console.live(
            root,
            api_url=f"http://127.0.0.1:{bound.server_port}",
            api_password="secret",
        )
    finally:
        bound.shutdown()
        bound.server_close()
    api = document["health"]["api"]
    assert api["stats"]["Total"] == 3
    assert api["uptime"]["Uptime"] == "0d 0h 1m 00s"
    assert api["clients"] == {"forge": {"PrivLevel": 3}}
    assert api["problems"] == []


def test_a_public_listener_without_a_password_is_refused():
    with pytest.raises(console.ConsoleError) as refused:
        console.server_for(dict, host="0.0.0.0", port=0)
    assert "without a password" in str(refused.value)


def test_the_pull_action_is_disabled_without_a_checkout(tmp_path):
    root = _environment(tmp_path)
    document = console.live(root, env="staging")
    assert document["actions"]["pull"]["enabled"] is False
    assert "Pull is disabled" in console.page(document)
    assert document["remote"] is None


def test_the_pull_action_hands_over_and_audits(tmp_path, monkeypatch):
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    def fake(deploy, env=None, **kwargs):
        calls.append((str(deploy), env, kwargs))
        return {"release": 1, "wrote": ["a", "b"], "feed": {"files": 3}}

    monkeypatch.setattr(console.remote_mod, "pull", fake)
    bound = _serving(
        lambda: console.live(root, env="staging", deploy=checkout, audit=audit),
        audit=audit,
        deploy=checkout,
        env="staging",
        token="t",
    )
    try:
        status, location, _body = _post(
            bound, "/action/pull", {"token": "t", "confirm": "yes"}
        )
        assert status == 303
        assert "action=pull" in location and "outcome=pulled" in location
    finally:
        bound.shutdown()
        bound.server_close()
    assert calls and calls[0][1] == "staging"
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "pull"
    assert entry["result"] == "pulled"
    assert "release 0001" in entry["detail"]


def test_the_pull_action_reports_a_refusal(tmp_path, monkeypatch):
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    def fake(deploy, env=None, **kwargs):
        raise console.remote_mod.PullError("the pack does not hash")

    monkeypatch.setattr(console.remote_mod, "pull", fake)
    bound = _serving(
        lambda: console.live(root, env="staging", deploy=checkout, audit=audit),
        audit=audit,
        deploy=checkout,
        env="staging",
        token="t",
    )
    try:
        status, location, _body = _post(
            bound, "/action/pull", {"token": "t", "confirm": "yes"}
        )
        assert status == 303 and "outcome=error" in location
    finally:
        bound.shutdown()
        bound.server_close()
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["result"] == "error"
    assert "does not hash" in entry["detail"]


def test_the_page_shows_the_checkout_and_what_is_wanted(tmp_path):
    root = _environment(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "publish.json").write_text(
        json.dumps(
            {
                "format": console.publish_mod.FORMAT,
                "release": 1,
                "feed": {"version": 1},
                "at": "2026-10-18T10:00:00Z",
                "author": "m",
            }
        ),
        encoding="utf-8",
    )
    (installer.folder(root) / "wanted.json").write_text(
        json.dumps(
            {
                "format": console.remote_mod.FORMAT,
                "releases": [2],
                "by": "local",
                "at": "2026-10-18T10:05:00Z",
            }
        ),
        encoding="utf-8",
    )

    document = console.live(root, env="staging", deploy=checkout)

    assert document["remote"]["publish"]["release"] == 1
    assert document["remote"]["wanted"]["releases"] == [2]
    assert document["remote"]["pending"] == [2]
    rendered = console.page(document, action_token="t")
    assert "published release" in rendered
    assert "pending:" in rendered


def test_a_broken_state_is_a_500_with_the_reason(tmp_path):
    root = _environment(tmp_path)
    (installer.state_path(root)).write_text("{not json", encoding="utf-8")
    bound = _serving(lambda: console.live(root))
    try:
        with pytest.raises(urllib.error.HTTPError) as broken:
            _get(bound, "/api/live")
        assert broken.value.code == 500
        assert b"state.json" in broken.value.read()
    finally:
        bound.shutdown()
        bound.server_close()


def test_the_restart_action_is_disabled_without_the_flag(tmp_path):
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    bound = _serving(
        lambda: console.live(root, env="staging", audit=audit), audit=audit, token="t"
    )
    try:
        status, location, _body = _post(
            bound, "/action/restart", {"token": "t", "confirm": "yes"}
        )
        assert status == 303
        assert "outcome=disabled" in location
    finally:
        bound.shutdown()
        bound.server_close()
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "restart"
    assert entry["result"] == "refused"
    assert "disabled" in entry["detail"]


def test_the_restart_action_stops_the_server_and_writes_the_audit(tmp_path):
    api_bound, calls = _api()
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    api_url = f"http://127.0.0.1:{api_bound.server_port}"

    def document() -> dict:
        return console.live(
            root,
            env="staging",
            api_url=api_url,
            api_password="secret",
            allow_restart=True,
            audit=audit,
        )

    bound = _serving(
        document,
        allow_restart=True,
        api_url=api_url,
        api_password="secret",
        audit=audit,
        token="t",
    )
    try:
        status, location, _body = _post(
            bound, "/action/restart", {"token": "t", "confirm": "yes"}
        )
        assert status == 303
        assert "outcome=requested" in location
    finally:
        bound.shutdown()
        bound.server_close()
        api_bound.shutdown()
        api_bound.server_close()

    assert calls == ["/utils/shutdown/secret"]
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["format"] == console.AUDIT_FORMAT
    assert entry["user"] == "local"
    assert entry["result"] == "requested"
    assert entry["target"] == api_url
    assert "HTTP 200" in entry["detail"]

    read_back = console.live(
        root,
        env="staging",
        audit=audit,
        allow_restart=True,
        api_url=api_url,
        api_password="secret",
    )
    assert read_back["audit"]["entries"][0]["result"] == "requested"
    rendered = console.page(read_back, action_token="t", outcome="requested")
    assert "Restart (stop; supervisor restarts)" in rendered
    assert "/action/restart" in rendered


def test_the_restart_form_needs_the_token_and_the_confirmation(tmp_path):
    api_bound, calls = _api()
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    api_url = f"http://127.0.0.1:{api_bound.server_port}"
    bound = _serving(
        lambda: console.live(
            root,
            env="staging",
            api_url=api_url,
            api_password="secret",
            allow_restart=True,
            audit=audit,
        ),
        allow_restart=True,
        api_url=api_url,
        api_password="secret",
        audit=audit,
        token="t",
    )
    try:
        status, location, _body = _post(bound, "/action/restart", {"token": "t"})
        assert status == 303 and "outcome=confirm" in location
        status, location, _body = _post(
            bound, "/action/restart", {"token": "wrong", "confirm": "yes"}
        )
        assert status == 303 and "outcome=token" in location
        status, location, _body = _post(
            bound, "/action/restart", {"token": "t", "confirm": "yes"}
        )
        assert status == 303 and "outcome=requested" in location
    finally:
        bound.shutdown()
        bound.server_close()
        api_bound.shutdown()
        api_bound.server_close()
    assert calls == ["/utils/shutdown/secret"]
    results = [
        json.loads(line)["result"] for line in audit.read_text(encoding="utf-8").splitlines()
    ]
    assert results == ["refused", "refused", "requested"]


def test_a_refused_shutdown_is_audited_as_an_error(tmp_path):
    api_bound, calls = _api(status=401, body=b"no bread for you")
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    api_url = f"http://127.0.0.1:{api_bound.server_port}"
    bound = _serving(
        lambda: console.live(
            root,
            env="staging",
            api_url=api_url,
            api_password="secret",
            allow_restart=True,
            audit=audit,
        ),
        allow_restart=True,
        api_url=api_url,
        api_password="secret",
        audit=audit,
        token="t",
    )
    try:
        status, location, _body = _post(
            bound, "/action/restart", {"token": "t", "confirm": "yes"}
        )
        assert status == 303 and "outcome=error" in location
    finally:
        bound.shutdown()
        bound.server_close()
        api_bound.shutdown()
        api_bound.server_close()
    assert calls == ["/utils/shutdown/secret"]
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["result"] == "error"
    assert "HTTP 401" in entry["detail"]


def test_restart_needs_the_api_and_an_audit_path(tmp_path):
    audit = tmp_path / "audit.jsonl"
    with pytest.raises(console.ConsoleError) as no_url:
        console.server_for(dict, allow_restart=True, api_password="p", audit=audit)
    assert "API" in str(no_url.value)
    with pytest.raises(console.ConsoleError) as no_password:
        console.server_for(dict, allow_restart=True, api_url="http://x", audit=audit)
    assert "API password" in str(no_password.value)
    with pytest.raises(console.ConsoleError) as no_audit:
        console.server_for(
            dict, allow_restart=True, api_url="http://x", api_password="p"
        )
    assert "audit" in str(no_audit.value).lower()


def test_the_action_page_says_when_restart_is_disabled(tmp_path):
    root = _environment(tmp_path)
    document = console.live(root, env="staging")
    assert document["actions"]["restart"]["enabled"] is False
    assert "Restart is disabled" in console.page(document)
    enabled = console.live(
        root,
        env="staging",
        api_url="http://127.0.0.1:5000",
        api_password="secret",
        allow_restart=True,
    )
    assert enabled["actions"]["restart"]["enabled"] is True
    rendered = console.page(enabled, action_token="t")
    assert "Restart (stop; supervisor restarts)" in rendered
    assert "Without a supervisor this is a stop" in rendered


# --- the other direction: the reverse ask (§D10, §D17) --------------------------


def test_the_reverse_action_is_disabled_when_nothing_is_applied(tmp_path):
    """There is nothing to put back, and the page says so instead of offering a
    button that cannot work."""
    root = tmp_path / "env"
    installer.written_state(root, installer.empty_state("Forge Project"))
    document = console.live(root, env="staging", audit=tmp_path / "audit.jsonl")

    assert document["actions"]["reverse"]["enabled"] is False
    assert "nothing is applied" in document["actions"]["reverse"]["why"]
    assert "Reverse is disabled" in console.page(document)


def test_the_reverse_action_writes_the_ask_and_audits(tmp_path):
    """`console.md` §D10: the Console never opens the database (S §D1). It writes
    the ask; the Installer's step does the reversing."""
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    bound = _serving(
        lambda: console.live(root, env="staging", audit=audit),
        audit=audit,
        root=root,
        env="staging",
        token="t",
    )
    try:
        status, location, _body = _post(
            bound, "/action/reverse", {"token": "t", "confirm": "yes"}
        )
        assert status == 303
        assert "action=reverse" in location and "outcome=asked" in location
    finally:
        bound.shutdown()
        bound.server_close()

    ask = json.loads(installer.reverse_ask_path(root).read_text(encoding="utf-8"))
    assert ask["format"] == installer.REVERSE_FORMAT
    assert ask["releases"] == [1]  # the newest applied release
    assert ask["by"] == "local"  # no password on loopback, so no operator name
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["action"] == "reverse"
    assert entry["result"] == "asked"
    assert "0001" in entry["detail"]
    assert entry["target"] == str(root)


def test_the_reverse_form_needs_the_token_and_the_confirmation(tmp_path):
    root = _environment(tmp_path)
    audit = tmp_path / "audit.jsonl"
    bound = _serving(
        lambda: console.live(root, env="staging", audit=audit),
        audit=audit,
        root=root,
        env="staging",
        token="t",
    )
    try:
        status, location, _body = _post(bound, "/action/reverse", {"token": "no", "confirm": "yes"})
        assert status == 303 and "outcome=token" in location
        status, location, _body = _post(bound, "/action/reverse", {"token": "t", "confirm": "no"})
        assert status == 303 and "outcome=confirm" in location
    finally:
        bound.shutdown()
        bound.server_close()

    assert not installer.reverse_ask_path(root).exists()
    lines = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [line["result"] for line in lines] == ["refused", "refused"]
    assert [line["detail"] for line in lines] == ["form token", "confirmation not ticked"]


def test_the_page_says_an_ask_has_not_been_honoured_yet(tmp_path):
    """Asked and still applied: the Installer's step has not run, and the page says
    that rather than showing the release as reversed."""
    root = _environment(tmp_path)
    installer.write_reverse_ask(root, [1], by="operator")

    document = console.live(root, env="staging", audit=tmp_path / "audit.jsonl")

    assert document["reverse"]["releases"] == [1]
    assert document["reverse"]["pending"] == [1]
    assert document["reverse"]["by"] == "operator"
    assert "not run yet" in console.page(document)
