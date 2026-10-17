"""The dry run and the deploy: one release against one environment (`R`, P3b).

P3's exit criterion (`vision.md` §6): a dry run names destinations, drift and
ids, and a push is recorded and read back. §D12 adds the property that makes the
log worth reading — the second push of the same release writes nothing and says
so.

Everything runs on the synthetic client, with no daemon: the Installer's side of
the contract is P5's, and what P3 owes it is the folder it will read (§D5).
"""

import json
import shutil
from pathlib import Path

import pytest
from test_clientinstall import with_build
from test_project import base  # noqa: F401  (the synthetic client, for the chain below)
from test_release import served_client  # noqa: F401  (what `client` wraps)
from test_serve_editor import editing  # noqa: F401
from test_zones import FIXTURES, world  # noqa: F401

from camelot import clientinstall
from camelot import pipeline as pipe
from camelot import project as pj
from camelot import release as rel
from camelot.mpak import MpakArchive, write_mpak
from camelot.pipeline import _writes
from camelot.serve.config import ServeConfig
from camelot.serve.editor import Editor
from camelot.serve.world import World


@pytest.fixture()
def client(request):
    """The project's client: `test_release`'s synthetic one — the base `pj.new`
    records and the release build reads."""
    return request.getfixturevalue("served_client")


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    """The build's asset cache is this machine's, never a test's."""
    monkeypatch.setattr(rel, "default_cache_dir", lambda: tmp_path / "cache")


def _project(tmp_path, client, name="Avalon"):
    return pj.new(tmp_path / name.lower(), client=client, name=name)


def _editor(project, client) -> Editor:
    return Editor(
        ServeConfig(root=client, project=project.root, cache_dir=project.root / "cache")
    )


def _move(editor, x=30000, y=31000, row=2):
    editor.record(World(editor.config).zone(0), {"kind": "move", "id": row, "x": x, "y": y})


def _add(editor, nif_id=11, x=30000, y=31000):
    editor.record(
        World(editor.config).zone(0), {"kind": "add", "nif_id": nif_id, "x": x, "y": y}
    )


def _environment(tmp_path, client, name="env-local") -> Path:
    """An environment's folder (§D9): its own client, a copy of the base — never
    the operator's own."""
    root = tmp_path / name
    shutil.copytree(client, root / "client")
    return root


def _machine(project, **envs) -> None:
    (project.root / pj.MACHINE_FILE).write_text(
        json.dumps({"environments": {name: str(path) for name, path in envs.items()}}),
        encoding="utf-8",
    )


def _pack_name(record) -> str:
    return Path(record["artifacts"]["server"]["pack"]).name


def _tree(path: Path) -> dict:
    """Every file under *path*, with its hash and its mtime: what "nothing was
    written" is checked against (§D12)."""
    found = {}
    for item in sorted(Path(path).rglob("*")):
        if item.is_file():
            found[str(item.relative_to(path))] = (
                rel.digest(item.read_bytes()),
                item.stat().st_mtime_ns,
            )
    return found


def _entry(client_dir: Path, archive: str, entry: str) -> str:
    parsed = MpakArchive(str(client_dir / archive))
    try:
        return rel.digest(parsed.read(entry))
    finally:
        parsed.close()


def test_the_dry_run_names_what_it_would_write_and_why(client, tmp_path):
    """§3: per item — what, ours, theirs, verdict, why — plus the release's own
    lines (order, the reload it needs, the evidence the target carries). A dry
    run writes nothing at all."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    record = rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)

    table = pipe.plan(project.root, 1, env="local")

    assert table["format"] == pipe.FORMAT
    assert (table["project"], table["env"]) == ("Avalon", "local")
    assert table["order"] == "client-first" and table["refusals"] == []
    rows = {item["what"]: item for item in table["items"]}

    entry = rows["zones/zone000/csv000.mpk#fixtures.csv"]
    assert entry["family"] == "client" and entry["verdict"] == "write"
    assert entry["ours"].startswith("sha256:") and entry["theirs"].startswith("sha256:")
    assert entry["theirs"] != entry["ours"]
    assert entry["why"] == "the target's bytes differ; it records no original"

    assert rows[pipe.STATE]["verdict"] == "write"
    assert rows[pipe.STATE]["why"] == "the target records no install of this pack"

    server = rows[f"server/camelot/releases/0001/{_pack_name(record)}"]
    assert server["family"] == "server" and server["verdict"] == "write"
    assert server["ours"] == record["artifacts"]["server"]["sha256"]
    assert server["why"] == "the target does not have it"

    assert table["counts"] == {"write": 3, "unchanged": 0, "drift": 0, "refused": 0}
    assert table["evidence"]["present"] is False
    assert not (env / "server").exists() and not (env / "client" / ".camelot").exists()


def test_a_deploy_writes_the_client_first_then_the_server_and_records_one_fact(
    client, tmp_path
):
    """§D2/§D6/§D8: the client half lands first, the server's folder after, and
    one line in the deploy log says what happened — which is what the Console
    and the Deploy page read back."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    record = rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)

    report = pipe.deploy(project.root, 1, env="local")

    assert report["outcome"] == "ok"
    assert report["wrote"] == [
        "client/zones/zone000/csv000.mpk",
        f"client/{pipe.STATE}",
        f"server/camelot/releases/0001/{_pack_name(record)}",
    ]
    # the client holds our bytes, and its own record of the pack
    assert _entry(env / "client", "zones/zone000/csv000.mpk", "fixtures.csv") == (
        report["plan"]["items"][0]["ours"]
    )
    installed = clientinstall.status(str(env / "client"))["installs"]
    assert [item["mod"] for item in installed] == ["avalon"]
    # the server's folder holds the release's own bytes, ready for the Installer
    target = env / "server" / "camelot" / "releases" / "0001" / _pack_name(record)
    assert rel.digest(target.read_bytes()) == record["artifacts"]["server"]["sha256"]

    facts = [line for line in pj.deploy_log(project.root) if line["kind"] == "deploy"]
    assert len(facts) == 1
    assert (facts[0]["env"], facts[0]["release"], facts[0]["outcome"]) == ("local", 1, "ok")
    assert facts[0]["wrote"] == report["wrote"] and facts[0]["order"] == "client-first"
    assert pipe.live(project.root)["local"]["release"] == 1


def test_the_second_deploy_of_a_release_writes_nothing(client, tmp_path):
    """§D12: the same release twice is a no-op with a line — no file touched
    (mtime included), and the log says `unchanged`."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)
    pipe.deploy(project.root, 1, env="local")
    before = _tree(env)

    table = pipe.plan(project.root, 1, env="local")
    assert table["counts"]["write"] == 0 and table["counts"]["unchanged"] == 3

    report = pipe.deploy(project.root, 1, env="local")

    assert report["outcome"] == "unchanged" and report["wrote"] == []
    assert _tree(env) == before
    facts = [line for line in pj.deploy_log(project.root) if line["kind"] == "deploy"]
    assert [line["outcome"] for line in facts] == ["ok", "unchanged"]


def test_a_target_that_drifted_refuses_until_the_operator_adopts(client, tmp_path):
    """§3's `drift`: the target holds bytes that are neither its own original
    nor ours — something else edited the archive. That is an `expect` mismatch,
    so the deploy refuses it and nothing is written; `--adopt` is the
    operator's own word for accepting what is there now as the target's."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)
    pipe.deploy(project.root, 1, env="local")
    write_mpak(
        str(env / "client" / "zones" / "zone000" / "csv000.mpk"),
        "csv000.mpk",
        [("fixtures.csv", FIXTURES + b"\r\n")],
    )

    table = pipe.plan(project.root, 1, env="local")
    row = next(
        item
        for item in table["items"]
        if item["family"] == "client" and item["what"].endswith("fixtures.csv")
    )
    assert row["verdict"] == "drift"
    assert row["why"] == "the target holds neither its original nor ours"
    assert table["refusals"] == [f"{row['what']}: {row['why']}"]
    with pytest.raises(pipe.PipelineError, match="holds neither its original nor ours"):
        pipe.deploy(project.root, 1, env="local")

    # the operator's own decision: what is there now is the target's original
    table = pipe.plan(project.root, 1, env="local", adopt=True)
    assert not table["refusals"]
    assert row["what"] in {item["what"] for item in _writes(table)}

    report = pipe.deploy(project.root, 1, env="local", adopt=True)

    assert report["outcome"] == "ok"
    assert report["wrote"] == ["client/zones/zone000/csv000.mpk"]
    assert _entry(env / "client", "zones/zone000/csv000.mpk", "fixtures.csv") == row["ours"]


def test_a_running_game_refuses_the_client_half(client, tmp_path, monkeypatch):
    """§D7/S §D11: a refusal is not a write. The plan says why, the deploy
    refuses the whole attempt, and nothing anywhere is touched."""
    monkeypatch.setattr(clientinstall, "blocks_install", lambda client_dir: "daocgame.exe")
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)

    table = pipe.plan(project.root, 1, env="local")

    assert table["refusals"] == ["the game is running (daocgame.exe)"]
    assert table["counts"]["refused"] == 2 and table["counts"]["write"] == 1
    with pytest.raises(pipe.PipelineError, match="the game is running"):
        pipe.deploy(project.root, 1, env="local")
    assert not (env / "server").exists() and not (env / "client" / ".camelot").exists()
    assert not [line for line in pj.deploy_log(project.root) if line["kind"] == "deploy"]


def test_a_target_of_another_build_is_refused(client, tmp_path):
    """C §D8: a release was built against one client build. The target's own
    build is read, and a different one is refused by name."""
    with_build(client, "1.130")  # the authoring client, before the cut
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)
    with_build(str(env / "client"), "1.131")

    table = pipe.plan(project.root, 1, env="local")

    assert table["refusals"] == [
        "the target is client build 1.131, the release was built against 1.130"
    ]


def test_an_id_the_environment_already_holds_is_refused(client, tmp_path):
    """§D9's collision check, as the deploy can make it before the Installer
    exists: against the ids the releases deployed to this environment
    allocated, read from their own records."""
    project = _project(tmp_path, client)
    editor = _editor(project, client)
    _add(editor, nif_id=11)
    first = rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)
    pipe.deploy(project.root, 1, env="local")
    held = first["ids"]
    assert held, "an add allocates an id"
    row_id = next(iter(next(iter(held.values())).values()))

    # a second release claiming the same allocation — what a hand-edited record
    # or a re-based project looks like
    _move(editor, x=25472)
    second = rel.cut(project.root, client=client)
    second["ids"] = held
    rel.record_path(project.root, 2).write_text(
        json.dumps(second, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    table = pipe.plan(project.root, 2, env="local")

    assert table["ids"]["checked"] == 1
    assert table["ids"]["collisions"] == [
        f"id {row_id} in zone 0 is already held by release 1 in local"
    ]
    with pytest.raises(pipe.PipelineError, match="already held by release 1"):
        pipe.deploy(project.root, 2, env="local")


def test_a_promotion_moves_the_same_bytes_to_the_next_environment(client, tmp_path):
    """§D4: a promotion moves the artifact, it never rebuilds — the target's
    bytes are the source's — and it is refused for a release the source
    environment does not hold."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    record = rel.cut(project.root)
    local = _environment(tmp_path, client, name="env-local")
    production = _environment(tmp_path, client, name="env-production")
    _machine(project, local=local, production=production)

    with pytest.raises(pipe.PipelineError, match="is not deployed to local"):
        pipe.promote(project.root, 1, frm="local", to="production")

    pipe.deploy(project.root, 1, env="local")
    report = pipe.promote(project.root, 1, frm="local", to="production")

    assert report["outcome"] == "ok"
    name = _pack_name(record)
    here = local / "server" / "camelot" / "releases" / "0001" / name
    there = production / "server" / "camelot" / "releases" / "0001" / name
    assert rel.digest(there.read_bytes()) == rel.digest(here.read_bytes())
    assert rel.digest(there.read_bytes()) == record["artifacts"]["server"]["sha256"]

    facts = [line for line in pj.deploy_log(project.root) if line["kind"] == "promote"]
    assert len(facts) == 1
    assert (facts[0]["from"], facts[0]["env"], facts[0]["release"]) == ("local", "production", 1)
    assert pipe.live(project.root)["production"]["release"] == 1
    assert pipe.live(project.root)["local"]["release"] == 1


def test_verify_reads_the_environment_back(client, tmp_path):
    """§D11: the read-back is the check — the server's folder against the
    release's own hashes, and the client through the install's own
    verification."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    record = rel.cut(project.root)
    env = _environment(tmp_path, client)
    _machine(project, local=env)
    pipe.deploy(project.root, 1, env="local")

    report = pipe.verify(project.root, env="local")
    assert report["ok"] and report["release"] == 1 and report["problems"] == []
    assert [item["ok"] for item in report["server"]["files"]] == [True]

    # a client destination changed outside the tool
    write_mpak(
        str(env / "client" / "zones" / "zone000" / "csv000.mpk"),
        "csv000.mpk",
        [("fixtures.csv", FIXTURES + b"\r\n")],
    )
    report = pipe.verify(project.root, env="local")
    assert not report["ok"]
    assert any("modified outside the tool" in problem for problem in report["problems"])

    # and a file of the server package that is not the release's own
    (env / "server" / "camelot" / "releases" / "0001" / _pack_name(record)).write_bytes(
        b"not the pack"
    )
    report = pipe.verify(project.root, env="local")
    assert any("does not match the release" in problem for problem in report["problems"])


def test_verify_of_an_environment_that_holds_nothing(client, tmp_path):
    """An environment nothing was deployed to reads back as holding nothing —
    and its client, never written to, is intact."""
    project = _project(tmp_path, client)
    _environment(tmp_path, client)
    _machine(project, local=tmp_path / "env-local")

    report = pipe.verify(project.root, env="local")

    assert report["release"] is None and report["ok"] and report["problems"] == []


def test_an_environment_must_be_declared_and_mapped(client, tmp_path):
    """§D9/P §D4: the names are the project's (tracked), the folders are this
    machine's own answer (`machine.json`), and each refusal says which one is
    missing."""
    project = _project(tmp_path, client)
    _machine(project, local=tmp_path / "somewhere")

    assert pipe.folder(project, "local") == tmp_path / "somewhere"
    with pytest.raises(pipe.PipelineError, match="is not one of its environments"):
        pipe.folder(project, "staging")
    with pytest.raises(pipe.PipelineError, match="has no folder on this machine"):
        pipe.folder(project, "production")


def test_an_environment_without_a_client_is_refused(client, tmp_path):
    """A folder that is not a client yet is refused by name — the client half is
    where the tested client comes from (C §2), so a deploy without one would
    lie about what was tested."""
    project = _project(tmp_path, client)
    _move(_editor(project, client))
    rel.cut(project.root)
    env = tmp_path / "env-local"
    env.mkdir()
    _machine(project, local=env)

    table = pipe.plan(project.root, 1, env="local")

    assert table["refusals"] == [f"there is no client at {env / 'client'}"]
    with pytest.raises(pipe.PipelineError, match="there is no client"):
        pipe.deploy(project.root, 1, env="local")
