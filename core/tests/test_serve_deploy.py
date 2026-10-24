"""The Deploy page's routes (`pipeline.md` §6, P4).

The page is a face on `pipeline`'s verbs: what it answers must be what the CLI
does, and the two refusals it inherits — no project, and a read-only daemon
(another process holds the checkout) — are tested here, not in the browser.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_pipeline import _editor, _environment, _machine, _move, _project
from test_project import base  # noqa: F401  (the synthetic client, for the chain)
from test_release import served_client  # noqa: F401  (what `project_client` wraps)
from test_serve_editor import editing  # noqa: F401
from test_zones import FIXTURES, world  # noqa: F401

from camelot import project as pj
from camelot import release as rel
from camelot.mpak import write_mpak
from camelot.serve.app import create_app
from camelot.serve.config import ServeConfig


@pytest.fixture()
def project_client(request):
    """The project's client: `test_release`'s synthetic one, under this
    module's own name."""
    return request.getfixturevalue("served_client")


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """The build's asset cache is this machine's, never a test's."""
    monkeypatch.setattr(rel, "default_cache_dir", lambda: tmp_path / "cache")


def _api(client_root, project_root, *, read_only=False):
    return TestClient(
        create_app(
            ServeConfig(
                root=client_root,
                watch=False,
                open_browser=False,
                project=project_root,
                read_only=read_only,
            )
        ),
        base_url="http://127.0.0.1",
    )


def _ready(tmp_path, client_root):
    """A project with one release, two environments with a scratch client each."""
    project = _project(tmp_path, client_root)
    _move(_editor(project, client_root))
    record = rel.cut(project.root)
    local = _environment(tmp_path, client_root, name="env-local")
    production = _environment(tmp_path, client_root, name="env-production")
    _machine(project, local=local, production=production)
    return project, record, local, production


def test_the_page_reads_the_project_itself(project_client, tmp_path):
    """`GET /deploy`: the environments and their folders, the releases and what
    each holds, the feed (not built yet — P6) and the newest log lines."""
    project, record, local, _production = _ready(tmp_path, project_client)

    body = _api(project_client, project.root).get("/api/v1/deploy").json()

    assert body["project"] == "Avalon" and body["read_only"] is False
    names = [item["name"] for item in body["environments"]]
    assert names == ["local", "production"]
    here = body["environments"][0]
    assert here["root"] == str(local) and here["exists"] is True
    assert here["client"]["exists"] is True and here["live"] is None
    assert body["releases"][0]["number"] == 1
    assert body["releases"][0]["client"]["version"] == 1
    assert body["releases"][0]["live_in"] == []
    assert body["feed"]["present"] is False
    assert next(line["kind"] for line in body["log"]) == "release"
    assert record["artifacts"]["server"]["sha256"]


def test_the_dry_run_writes_nothing(project_client, tmp_path):
    """`POST /deploy/plan` is the table of §3 and nothing else: the environment's
    client has no store and the server's folder does not exist after it."""
    project, _record, _local, _production = _ready(tmp_path, project_client)

    plan = _api(project_client, project.root).post(
        "/api/v1/deploy/plan", json={"release": 1, "env": "local"}
    ).json()

    assert plan["env"] == "local" and plan["order"] == "client-first"
    assert plan["counts"] == {"write": 3, "unchanged": 0, "drift": 0, "refused": 0}
    assert plan["refusals"] == []
    assert not (Path(plan["root"]) / "client" / ".camelot").exists()


def test_a_push_is_recorded_and_read_back(project_client, tmp_path):
    """`POST /deploy/deploy` runs the table, the page shows the release live
    there, and `POST /deploy/verify` reads the environment back."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)

    pushed = api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"}).json()

    assert pushed["outcome"] == "ok" and pushed["wrote"]
    body = api.get("/api/v1/deploy").json()
    assert body["environments"][0]["live"]["release"] == 1
    assert body["releases"][0]["live_in"] == ["local"]
    assert body["log"][0]["kind"] == "deploy" and body["log"][0]["outcome"] == "ok"

    read = api.post("/api/v1/deploy/verify", json={"env": "local"}).json()
    assert read == {**read, "ok": True, "release": 1, "problems": []}


def test_a_second_push_writes_nothing(project_client, tmp_path):
    """§D12, through the page: the second push answers `unchanged` with an empty
    list of writes, and the log keeps both attempts."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)
    api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"})

    again = api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"}).json()

    assert again["outcome"] == "unchanged" and again["wrote"] == []
    log = api.get("/api/v1/deploy").json()["log"]
    outcomes = [line["outcome"] for line in log if line["kind"] == "deploy"]
    assert outcomes == ["unchanged", "ok"]


def test_a_drift_is_refused_until_the_operator_adopts_it(project_client, tmp_path):
    """§3's drift: the push refuses (400, with the row's own sentence) and
    `adopt` is the operator's way through."""
    project, _record, local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)
    api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"})
    write_mpak(
        str(local / "client" / "zones" / "zone000" / "csv000.mpk"),
        "csv000.mpk",
        [("fixtures.csv", FIXTURES + b"\r\n")],
    )

    refused = api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"})

    assert refused.status_code == 400
    assert "holds neither its original nor ours" in refused.json()["detail"]
    adopted = api.post(
        "/api/v1/deploy/deploy", json={"release": 1, "env": "local", "adopt": True}
    )
    assert adopted.status_code == 200 and adopted.json()["outcome"] == "ok"


def test_a_promotion_names_its_source(project_client, tmp_path):
    """`source` turns the same call into a promotion: the log line says where
    the bytes came from, and the target's package is the source's."""
    project, _record, _local, production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)
    api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"})

    promoted = api.post(
        "/api/v1/deploy/deploy",
        json={"release": 1, "env": "production", "source": "local"},
    ).json()

    assert promoted["outcome"] == "ok"
    body = api.get("/api/v1/deploy").json()
    assert body["log"][0]["kind"] == "promote" and body["log"][0]["from"] == "local"
    assert sorted(body["releases"][0]["live_in"]) == ["local", "production"]
    name = Path(_record["artifacts"]["server"]["pack"]).name
    assert (production / "server" / "camelot" / "releases" / "0001" / name).is_file()


def test_a_read_only_daemon_reads_but_never_writes(project_client, tmp_path):
    """`project.md` §D13: another process holds the checkout, so the page still
    reads the facts and the push is refused with the editor's own sentence."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root, read_only=True)

    assert api.get("/api/v1/deploy").status_code == 200
    pushed = api.post("/api/v1/deploy/deploy", json={"release": 1, "env": "local"})

    assert pushed.status_code == 409
    assert "read-only" in pushed.json()["detail"]


def test_without_a_project_the_page_is_off(project_client):
    """The front door's rule (P §D2): a daemon with no project serves the API
    and says which flag it needs."""
    api = TestClient(
        create_app(ServeConfig(root=project_client, watch=False, open_browser=False)),
        base_url="http://127.0.0.1",
    )

    answer = api.get("/api/v1/deploy")

    assert answer.status_code == 503
    assert "--project" in answer.json()["detail"]


def _published(api, deadline=10.0):
    """The progress of the publish the test just started, once it stops."""
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        progress = api.get("/api/v1/deploy/publish").json()["progress"]
        if not progress["running"]:
            return progress
        time.sleep(0.01)
    raise AssertionError("the feed build did not finish")


def _with_client(project, client) -> None:
    document = json.loads((project.root / pj.MACHINE_FILE).read_text(encoding="utf-8"))
    document["client"] = str(client)
    (project.root / pj.MACHINE_FILE).write_text(json.dumps(document), encoding="utf-8")


def test_publish_cuts_the_edits_then_pushes_the_branch(project_client, tmp_path, monkeypatch):
    """One action: the journal's new edits become a release, and that release
    is built, signed and pushed. The operator never sees "cut" and "ship"."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    _with_client(project, project_client)
    _move(_editor(project, project_client))  # one edit beyond release 1
    calls = {}

    def fake(root, number=None, *, build=True, remote=None, **kwargs):
        calls.update({"number": number, "build": build, "remote": remote})
        return {
            "release": number,
            "branch": "camelot-deploy",
            "commit": "abc123def456",
            "pushed": remote,
            "signature": None,
            "feed": {},
        }

    from camelot.serve import deploy as deploy_mod

    monkeypatch.setattr(deploy_mod.publish_mod, "publish", fake)
    monkeypatch.setattr(deploy_mod.publish_mod, "ensure_commit", lambda root: "head")
    api = _api(project_client, project.root)

    started = api.post("/api/v1/deploy/publish")
    assert started.status_code == 200
    progress = _published(api)

    assert calls == {"number": 2, "build": True, "remote": "origin"}
    assert progress["stage"] == "done" and progress["release"] == 2
    assert progress["commit"] == "abc123def456" and progress["pushed"] == "origin"


def test_publish_without_edits_publishes_the_newest_release(
    project_client, tmp_path, monkeypatch
):
    """A retry after a failed push: nothing to cut, so the newest release goes."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    _with_client(project, project_client)
    seen = {}

    def fake(root, number=None, *, build=True, remote=None, **kwargs):
        seen["number"] = number
        return {
            "release": number,
            "commit": "abc123def456",
            "pushed": remote,
            "signature": None,
            "feed": {},
        }

    from camelot.serve import deploy as deploy_mod

    monkeypatch.setattr(deploy_mod.publish_mod, "publish", fake)
    monkeypatch.setattr(deploy_mod.publish_mod, "ensure_commit", lambda root: "head")

    api = _api(project_client, project.root)
    assert api.post("/api/v1/deploy/publish").status_code == 200
    progress = _published(api)

    assert seen["number"] == 1
    assert progress["stage"] == "done" and progress["error"] is None


def test_a_second_publish_is_refused_while_one_runs(project_client, tmp_path, monkeypatch):
    """One publish at a time: two commits of the same branch would race."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    _with_client(project, project_client)
    started = threading.Event()
    release = threading.Event()

    def held(root, number=None, **kwargs):
        started.set()
        release.wait(5)
        return {
            "release": 1,
            "commit": "abc123def456",
            "pushed": "origin",
            "signature": None,
            "feed": {},
        }

    from camelot.serve import deploy as deploy_mod

    monkeypatch.setattr(deploy_mod.publish_mod, "publish", held)
    monkeypatch.setattr(deploy_mod.publish_mod, "ensure_commit", lambda root: "head")
    api = _api(project_client, project.root)
    try:
        assert api.post("/api/v1/deploy/publish").status_code == 200
        assert started.wait(5)
        again = api.post("/api/v1/deploy/publish")
        assert again.status_code == 409 and "already running" in again.json()["detail"]
    finally:
        release.set()
        _published(api)


def test_a_publish_error_is_shown_in_the_progress(project_client, tmp_path, monkeypatch):
    from camelot import publish as publish_mod
    from camelot.serve import deploy as deploy_mod

    project, _record, _local, _production = _ready(tmp_path, project_client)
    _with_client(project, project_client)

    def refused(root, number=None, **kwargs):
        raise publish_mod.PublishError("no feed URL: pass --feed-url or set CAMELOT_FEED_URL")

    monkeypatch.setattr(deploy_mod.publish_mod, "publish", refused)
    monkeypatch.setattr(deploy_mod.publish_mod, "ensure_commit", lambda root: "head")
    api = _api(project_client, project.root)

    assert api.post("/api/v1/deploy/publish").status_code == 200
    progress = _published(api)

    assert progress["stage"] == "error" and "CAMELOT_FEED_URL" in progress["error"]


def test_the_local_test_deploys_and_applies(project_client, tmp_path, monkeypatch):
    """Test locally is the same release, written to `local` and applied by the
    Installer, before the operator publishes it."""
    from camelot.serve import deploy as deploy_mod

    project, _record, _local, _production = _ready(tmp_path, project_client)
    _with_client(project, project_client)
    monkeypatch.setattr(
        deploy_mod.pipeline_mod, "deploy", lambda root, number, env: {"wrote": ["a", "b"]}
    )
    monkeypatch.setattr(
        deploy_mod.envs_mod,
        "apply",
        lambda project, env, release: {"applied": [{}, {}], "unchanged": False},
    )

    answer = _api(project_client, project.root).post("/api/v1/deploy/test").json()

    assert answer == {"release": 1, "wrote": 2, "applied": 2, "unchanged": False}


def test_the_shard_addresses_are_written_to_the_project(project_client, tmp_path):
    """The two values every player reads are facts of the project, not of the
    daemon's shell (`F §D4`): the page writes them into `project.json`."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)

    saved = api.post(
        "/api/v1/deploy/shard",
        json={"feed_url": "https://feed.example", "game_server": "play.example:10300"},
    ).json()

    assert saved == {
        "feed_url": "https://feed.example/",
        "game_server": "play.example:10300",
    }
    assert pj.shard(project.root) == saved
    assert api.get("/api/v1/deploy").json()["shard"] == saved


def test_a_shard_address_that_is_not_host_port_is_refused(project_client, tmp_path):
    project, _record, _local, _production = _ready(tmp_path, project_client)

    refused = _api(project_client, project.root).post(
        "/api/v1/deploy/shard", json={"game_server": "play.example"}
    )

    assert refused.status_code == 400 and "host:port" in refused.json()["detail"]


def test_the_page_says_which_key_signs(project_client, tmp_path):
    project, _record, _local, _production = _ready(tmp_path, project_client)
    api = _api(project_client, project.root)

    assert api.get("/api/v1/deploy").json()["signing"]["key"] is None

    document = json.loads((project.root / pj.MACHINE_FILE).read_text(encoding="utf-8"))
    document["release_key"] = "/home/tester/keys/camelot-release"
    (project.root / pj.MACHINE_FILE).write_text(json.dumps(document), encoding="utf-8")

    assert (
        api.get("/api/v1/deploy").json()["signing"]["key"]
        == "/home/tester/keys/camelot-release"
    )


def test_the_feed_panel_reads_the_launcher_files(project_client, tmp_path):
    """C §D4: `feed/` holds the launcher's two text files and our record beside
    them; the panel counts what the launcher would fetch."""
    project, _record, _local, _production = _ready(tmp_path, project_client)
    feed = project.root / "feed"
    feed.mkdir()
    (feed / "version-new.txt").write_text("4\n", encoding="utf-8")
    (feed / "patchlist-new.txt").write_text(
        "https://example.invalid/feed/\n"
        "zones/zone000/csv000.mpk\t0123456789abcdef0123456789abcdef\t120\n"
        "game1127.dll\t0123456789abcdef0123456789abcdef\t2048\n",
        encoding="utf-8",
    )
    (feed / "release.json").write_text(json.dumps({"release": 1}), encoding="utf-8")

    body = _api(project_client, project.root).get("/api/v1/deploy").json()

    assert body["feed"]["present"] is True
    assert body["feed"]["version"] == 4
    assert body["feed"]["files"] == 2
    assert body["feed"]["bytes"] == 2168
    assert body["feed"]["release"] == 1
