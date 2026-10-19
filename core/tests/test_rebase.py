"""The re-base: a base that moved under a project, and the two decisions
(`project.md` §D16-§D18, phase P8).

The exit criterion (`vision.md` §6): a real client update is absorbed without a
silent overwrite. The tests walk the whole path — a client whose `fixtures.csv`
changed, the journal replayed against it, the two decisions, and the warning a
deploy carries afterwards.

Everything runs on the synthetic client (`test_zones`' zones plus the two files
`project.new` insists on), with no daemon and no real client.
"""

import json
import shutil
from pathlib import Path

import pytest
from test_pipeline import _editor, _machine, _move, client  # noqa: F401
from test_release import served_client  # noqa: F401
from test_serve_editor import editing  # noqa: F401
from test_zones import FIXTURES, world  # noqa: F401

from camelot import pipeline as pipe
from camelot import project as pj
from camelot import rebase
from camelot import release as rel
from camelot.mpak import write_mpak

FIXTURES_RESOURCE = "zones/zone000/csv000.mpk#fixtures.csv"

HEADER = (
    "Fixtures,, , ,,,,,NIF,Collide,,On.\r\n"
    "ID,NIF #,Textual Name,X,Y,Z,A,Scale,Collide,Radius,Animate,Ground,Flip,Cave, Unique Id\r\n"
)


def _base(*rows: str) -> bytes:
    """A `fixtures.csv` as a client update would leave it."""
    return (HEADER + "".join(row + "\r\n" for row in rows)).encode("latin-1")


HOUSE = "1,10,Farm House,19660,19660,0,0,100,5120,0,0,1,0,0,1"
PINE = "2,11,Pine,32768,32768,1234,2048,100,0,0,0,1,0,0,0,2"


@pytest.fixture()
def project(tmp_path, client):  # noqa: F811
    """A project whose base records zone 0's own resources — what `populate`
    adds on top of `new`'s three, and what a client update moves."""
    project = pj.new(tmp_path / "avalon", client=client, name="Avalon")
    pj.add_fingerprints(project.root, pj.populate(client, zones=[0]))
    return pj.load(project.root)


@pytest.fixture()
def daemon(project, client):  # noqa: F811
    return _editor(project, client)


def _update(client_dir, fixtures: bytes) -> None:
    """A game update, as the client's own archive: `fixtures.csv` changes."""
    write_mpak(
        f"{client_dir}/zones/zone000/csv000.mpk",
        "csv000.mpk",
        [("fixtures.csv", fixtures)],
    )


def _state(view, resource=FIXTURES_RESOURCE) -> dict:
    return next(item for item in view["resources"] if item["resource"] == resource)


def _entry(view, resource=FIXTURES_RESOURCE) -> dict:
    return next(item for item in view["journal"] if item["resource"] == resource)


def _verdicts(view, resource=FIXTURES_RESOURCE) -> list[str]:
    return [op["verdict"] for op in _entry(view, resource)["ops"]]


def _finding(findings, resource) -> dict:
    return next(item for item in findings if item["resource"] == resource)


def _all_ok(project, client) -> bool:  # noqa: F811
    return all(item["state"] == "ok" for item in pj.verify(project, client))


# -- the view (D16/D17) --------------------------------------------------------


def test_a_base_that_has_not_moved_has_nothing_to_decide(project, client):  # noqa: F811
    view = rebase.analysis(project, client=client)
    assert view["clean"] is True
    assert view["resources"] == []
    assert view["journal"] == []
    assert view["conflicts"] == 0


def test_a_resource_that_moved_is_named_with_both_fingerprints(project, client):  # noqa: F811
    before = pj.fingerprint_resource(client, FIXTURES_RESOURCE)
    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))

    view = rebase.analysis(project, client=client)

    assert view["clean"] is False and view["moved"] == 1
    item = _state(view)
    assert item["state"] == "moved"
    assert item["ours"] == before
    assert item["theirs"] == pj.fingerprint_resource(client, FIXTURES_RESOURCE)
    assert "recorded" in item["detail"]


def test_a_resource_that_is_gone_is_missing_and_carries_no_value(project, client):  # noqa: F811
    (Path(client) / "gamedata.mpk").unlink()
    view = rebase.analysis(project, client=client)
    item = _state(view, "gamedata.mpk")
    assert item["state"] == "missing"
    assert item["theirs"] is None
    assert view["missing"] == 1


def test_a_moved_resource_the_journal_never_wrote_is_listed_without_ops(project, client):  # noqa: F811
    (Path(client) / "gamedata.mpk").write_bytes(b"the tables, updated")
    view = rebase.analysis(project, client=client)
    assert _state(view, "gamedata.mpk")["state"] == "moved"
    assert view["journal"] == []
    assert view["conflicts"] == 0


# -- what the log says about it (D18) ------------------------------------------


def test_an_op_whose_expect_still_holds_is_holds(project, client, daemon):  # noqa: F811
    _move(daemon, x=30000, y=31000, row=2)
    # the update touches *another* row: the op still fits the base
    _update(client, _base("1,10,Farm House,19999,19999,0,0,100,5120,0,0,1,0,0,1", PINE))

    view = rebase.analysis(project, client=client)

    assert _state(view)["state"] == "moved"
    assert _verdicts(view) == ["holds"]
    assert view["conflicts"] == 0


def test_an_op_the_new_base_contradicts_is_a_conflict_with_both_values(
    project, client, daemon  # noqa: F811
):
    _move(daemon, x=30000, y=31000, row=2)
    _update(client, _base(HOUSE, "2,11,Pine,11111,11111,1234,2048,100,0,0,0,1,0,0,0,2"))

    view = rebase.analysis(project, client=client)

    assert _verdicts(view) == ["conflict"]
    assert view["conflicts"] == 1
    detail = _entry(view)["ops"][0]["detail"]
    assert "32768" in detail and "11111" in detail


def test_an_op_the_base_already_shows_is_shows(project, client, daemon):  # noqa: F811
    _move(daemon, x=30000, y=31000, row=2)
    # the base carries the edit: a publish wrote it, or the client ships it
    _update(client, _base(HOUSE, "2,11,Pine,30000,31000,1234,2048,100,0,0,0,1,0,0,0,2"))

    view = rebase.analysis(project, client=client)

    assert _verdicts(view) == ["shows"]
    assert view["conflicts"] == 0


def test_the_walk_stops_at_the_first_conflict_and_counts_the_rest(
    project, client, daemon  # noqa: F811
):
    _move(daemon, x=30000, y=31000, row=2)
    _move(daemon, x=30512, y=31512, row=1)
    _update(client, _base(HOUSE, "2,11,Pine,11111,11111,1234,2048,100,0,0,0,1,0,0,0,2"))

    view = rebase.analysis(project, client=client)

    entry = _entry(view)
    assert [op["verdict"] for op in entry["ops"]] == ["conflict"]
    assert entry["unwalked"] == 1


# -- the two decisions (D17) ---------------------------------------------------


def test_keep_ours_pins_the_value_and_verify_stops_shouting(project, client):  # noqa: F811
    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))

    view = rebase.keep_ours(project, client=client)

    assert view["pinned_now"] == 1
    written = json.loads((project.root / pj.PROJECT_FILE).read_text(encoding="utf-8"))
    pinned = written["base"]["client"]["pinned"]
    assert list(pinned) == [FIXTURES_RESOURCE]
    assert pinned[FIXTURES_RESOURCE] == pj.fingerprint_resource(client, FIXTURES_RESOURCE)

    reloaded = pj.load(project.root)
    assert reloaded.fingerprints[FIXTURES_RESOURCE] != pinned[FIXTURES_RESOURCE]
    assert _finding(pj.verify(reloaded, client), FIXTURES_RESOURCE)["state"] == "pinned"


def test_keep_ours_leaves_a_missing_resource_a_problem(project, client):  # noqa: F811
    (Path(client) / "gamedata.mpk").unlink()

    view = rebase.keep_ours(project, client=client)

    assert view["pinned_now"] == 0
    assert pj.load(project.root).pinned == {}
    assert _finding(pj.verify(pj.load(project.root), client), "gamedata.mpk")["state"] == "missing"


def test_a_pin_is_per_value_so_a_second_move_is_a_new_fact(project, client):  # noqa: F811
    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))
    rebase.keep_ours(project, client=client)
    assert _finding(pj.verify(pj.load(project.root), client), FIXTURES_RESOURCE)["state"] == "pinned"

    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3", "4,11,Pine,2048,2048,0,0,100,0,0,0,1,0,0,0,4"))

    assert _finding(pj.verify(pj.load(project.root), client), FIXTURES_RESOURCE)["state"] == "moved"


def test_adopt_moves_the_fingerprints_and_still_names_the_conflicts(
    project, client, daemon  # noqa: F811
):
    _move(daemon, x=30000, y=31000, row=2)
    _update(client, _base(HOUSE, "2,11,Pine,11111,11111,1234,2048,100,0,0,0,1,0,0,0,2"))

    view = rebase.adopt(project, client=client)

    assert view["adopted"] == 1
    assert view["conflicts"] == 1 and _verdicts(view) == ["conflict"]
    reloaded = pj.load(project.root)
    assert reloaded.fingerprints[FIXTURES_RESOURCE] == _state(view)["theirs"]
    assert _all_ok(reloaded, client)


def test_adopting_supersedes_the_pin_it_moved_past(project, client):  # noqa: F811
    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))
    rebase.keep_ours(project, client=client)
    assert pj.load(project.root).pinned

    rebase.adopt(project, client=client)

    reloaded = pj.load(project.root)
    assert reloaded.pinned == {}
    assert _all_ok(reloaded, client)


# -- the deploy warns (D17) ----------------------------------------------------


def _plan_notes(project, client, daemon, tmp_path) -> list[str]:  # noqa: F811
    """`pipeline.plan`'s notes for the project's first release."""
    _move(daemon)
    rel.cut(project.root)
    environment = tmp_path / "env-local"
    shutil.copytree(client, environment / "client")
    _machine(project, local=environment)
    return list(pipe.plan(project.root, 1, env="local").get("notes") or [])


def test_a_pinned_base_makes_every_deploy_say_so(project, client, daemon, tmp_path):  # noqa: F811
    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))
    rebase.keep_ours(project, client=client)

    notes = _plan_notes(project, client, daemon, tmp_path)

    assert any("pinned" in note for note in notes)


def test_a_clean_base_adds_no_note(project, client, daemon, tmp_path):  # noqa: F811
    assert pj.load(project.root).pinned == {}
    assert _plan_notes(project, client, daemon, tmp_path) == []


# -- the CLI -------------------------------------------------------------------


def test_the_cli_says_nothing_to_decide_and_exits_zero(project, client, capsys):  # noqa: F811
    from camelot.cli import main

    code = main(["project", "rebase", "--dir", str(project.root), "--client", str(client)])

    assert code == 0
    assert "nothing to decide" in capsys.readouterr().out


def test_the_cli_exits_one_when_something_moved(project, client, daemon, capsys):  # noqa: F811
    from camelot.cli import main

    _move(daemon, x=30000, y=31000, row=2)
    _update(client, _base(HOUSE, "2,11,Pine,11111,11111,1234,2048,100,0,0,0,1,0,0,0,2"))

    code = main(["project", "rebase", "--dir", str(project.root), "--client", str(client)])

    out = capsys.readouterr().out
    assert code == 1
    assert "conflict" in out and "nothing written" in out


def test_the_cli_refuses_both_decisions_at_once(project, client, capsys):  # noqa: F811
    from camelot.cli import main

    code = main([
        "project", "rebase", "--dir", str(project.root), "--client", str(client),
        "--keep-ours", "--adopt",
    ])

    assert code == 1
    assert "two different decisions" in capsys.readouterr().err


def test_the_cli_keeps_ours_and_says_it(project, client, capsys):  # noqa: F811
    from camelot.cli import main

    _update(client, _base(HOUSE, PINE, "3,11,Pine,1024,1024,0,0,100,0,0,0,1,0,0,0,3"))

    code = main([
        "project", "rebase", "--dir", str(project.root), "--client", str(client), "--keep-ours",
    ])

    assert code == 0
    assert "keep ours: written to project.json" in capsys.readouterr().out
    assert pj.load(project.root).pinned
