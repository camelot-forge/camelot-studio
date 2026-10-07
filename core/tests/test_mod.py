"""The mod deployment document a pack carries.

A pack does not ship SQL and does not choose ids. It declares how many ids
it needs per table and describes its rows in terms of columns and values,
naming its own rows with logical ids so the manager can allocate real ones
at install time. That is what makes overlap impossible and what keeps a
third-party mod from executing arbitrary statements.
"""
import json

import pytest

from camelot import mod
from camelot.targets import opendaoc


def test_a_row_names_itself_logically_and_carries_no_id():
    """The key column is the manager's to fill. A row that shipped one would
    be choosing its own place in the id space, which is exactly what the
    design forbids."""
    row = opendaoc.creature_row(
        model_id=88, name="Albion Stone Minotaur", nif="mino", anim_set=357,
    )
    assert row["id"] == "spawn:88"
    assert row["table"] == "npctemplate"
    assert "TemplateId" not in row["values"], "the key column is not a mod's to set"
    assert row["values"]["Model"] == 88, "existing game data stays literal"


def test_two_rows_of_one_rig_do_not_collide_on_a_logical_id():
    """A logical id names a row, so it must be unique within the mod. Two
    different creatures can share a rig -- `mino` is both the Stone and the
    Shade Minotaur -- so the NIF stem is not an identity."""
    a = opendaoc.creature_row(model_id=88, name="Albion Stone Minotaur", nif="mino")
    b = opendaoc.creature_row(model_id=89, name="Albion Shade Minotaur", nif="mino")
    assert a["id"] != b["id"]


def test_the_deployment_declares_the_ids_it_needs():
    rows = [
        opendaoc.creature_row(model_id=88, name="A", nif="mino"),
        opendaoc.creature_row(model_id=89, name="B", nif="mino"),
    ]
    doc = mod.deployment("daoc.studio.spawns", "0.1.0", rows)
    assert doc["allocations"] == [{"table": "npctemplate", "count": 2}]
    assert doc["mod"]["id"] == "daoc.studio.spawns"


def test_allocations_are_counted_per_table():
    rows = [
        opendaoc.creature_row(model_id=88, name="A", nif="mino"),
        opendaoc.creature_row(model_id=89, name="B", nif="mino"),
        {"table": "itemtemplate", "id": "item:x", "values": {"Name": "X"}},
    ]
    doc = mod.deployment("p", "1", rows)
    by_table = {a["table"]: a["count"] for a in doc["allocations"]}
    assert by_table == {"npctemplate": 2, "itemtemplate": 1}


def test_the_deployment_is_deterministic():
    rows = [opendaoc.creature_row(model_id=88, name="A", nif="mino")]
    a = mod.deployment("p", "1", rows)
    b = mod.deployment("p", "1", rows)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_the_deployment_never_contains_sql_or_a_literal_id():
    """The format's whole point: a mod cannot smuggle statements in, and it
    cannot pick its own ids."""
    rows = [opendaoc.creature_row(model_id=88, name="A", nif="mino")]
    text = json.dumps(mod.deployment("p", "1", rows)).lower()
    for forbidden in ("insert ", "delete ", "replace into", "drop "):
        assert forbidden not in text


def test_a_row_must_name_the_table_it_belongs_to():
    with pytest.raises(ValueError, match="table"):
        mod.deployment("p", "1", [{"id": "x", "values": {}}])


def test_a_row_must_have_a_logical_id():
    with pytest.raises(ValueError, match="id"):
        mod.deployment("p", "1", [{"table": "npctemplate", "values": {}}])


def test_the_client_section_is_reserved_and_empty():
    """Phase 5 will fill it. Declaring it now costs nothing; adding it later
    would break the schema."""
    doc = mod.deployment("p", "1", [])
    assert doc["client"] == []
    assert doc["requires"] == []


# --- corpus tier ----------------------------------------------------------

@pytest.mark.usefixtures("client_dir")
def test_every_creature_becomes_a_row(client_dir):
    from camelot import creatures

    rows = [opendaoc.creature_row(
        model_id=c.model_id, name=c.name, nif=c.nif, anim_set=c.anim_set,
    ) for c in creatures.read_creatures(client_dir)]
    doc = mod.deployment("daoc.studio.spawns", "0.1.0", rows)
    assert doc["allocations"] == [{"table": "npctemplate", "count": len(rows)}]
    assert len({r["id"] for r in rows}) == len(rows), "logical ids must be unique"
    assert all(r["values"]["Model"] > 0 for r in rows)
    assert all ("TemplateId" not in r["values"] for r in rows)
