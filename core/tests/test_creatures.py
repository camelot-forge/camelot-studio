"""The client's creature models, and how they become mod rows.

Two things live here: resolving `monsters.csv` through `monnifs.csv` (the
server's model-id chain), and the rules a creature row obeys when it is
expressed declaratively for the mod loader.
"""
import pytest

from camelot.targets import opendaoc


def test_a_creature_row_names_itself_and_carries_no_key():
    """`monsters.ID` is the model id the server sends and the client
    resolves, so `Model` stays literal -- it is existing game data, not
    something the mod introduces. The row's own key column is absent: the
    manager allocates it."""
    row = opendaoc.creature_row(
        model_id=88, name="Albion Stone Minotaur", nif="mino", anim_set=357,
    )
    assert row["table"] == "npctemplate"
    assert row["id"] == "spawn:88"
    assert row["values"]["Model"] == 88
    assert row["values"]["ClassType"] == "DOL.GS.GameNPC"
    assert "TemplateId" not in row["values"]


def test_the_row_carries_the_required_non_null_columns():
    """Name, ClassType, Model, Size and Level are NOT NULL in the server's
    schema; a row missing one is rejected at import."""
    values = opendaoc.creature_row(model_id=88, name="Minotaur")["values"]
    for column in ("Name", "ClassType", "Model", "Size", "Level"):
        assert column in values, column
        assert values[column] not in (None, ""), column


def test_size_and_level_are_ranges_as_the_server_writes_them():
    """The server's own rows use `'50-58'` and `'10-1'`: a single number
    where a range belongs still imports, but silently pins every spawn to
    one value."""
    values = opendaoc.creature_row(model_id=88, name="Minotaur")["values"]
    assert "-" in values["Size"]
    assert "-" in values["Level"]


def test_a_creature_row_rejects_a_nonsense_model_id():
    with pytest.raises(ValueError, match="model id"):
        opendaoc.creature_row(model_id=0, name="Nowhere")


# --- corpus tier ----------------------------------------------------------

@pytest.mark.usefixtures("client_dir")
def test_creature_models_resolve_through_monsters_then_monnifs(client_dir):
    """~2 200 models: the server's id is `monsters`', and the figure and
    anim set come from the `monnifs` row its `NIF` field names. Resolving one
    level off gives the wrong creature (monnifs 606 is a Cata half-ogre; the
    model the server calls 606 is an imp)."""
    from camelot import creatures

    rows = creatures.read_creatures(client_dir)
    assert len(rows) > 2000
    by_id = {c.model_id: c for c in rows}
    assert by_id[592].name == "sand crab"
    assert by_id[592].nif == "crab"
    assert by_id[592].anim_set == 51
    assert by_id[88].nif == "bavalonf"
    assert by_id[606].name == "Imp"
    assert by_id[606].nif == "imp"
    assert all(c.model_id > 0 for c in rows)
    assert all(c.name for c in rows)
    assert all(c.nif for c in rows)


@pytest.mark.usefixtures("client_dir")
def test_creature_model_ids_are_stable_unique_and_ordered(client_dir):
    """The table ships one repeated id (2375); the first row wins, so a
    caller's logical ids stay unique."""
    from camelot import creatures

    a = creatures.read_creatures(client_dir)
    b = creatures.read_creatures(client_dir)
    ids = [c.model_id for c in a]
    assert ids == sorted(ids)
    assert ids == [c.model_id for c in b]
    assert len(set(ids)) == len(ids)


@pytest.mark.usefixtures("client_dir")
def test_every_creature_becomes_a_row(client_dir):
    from camelot import creatures, mod

    rows = [opendaoc.creature_row(
        model_id=c.model_id, name=c.name, nif=c.nif, anim_set=c.anim_set,
    ) for c in creatures.read_creatures(client_dir)]
    doc = mod.deployment("daoc.studio.spawns", "0.1.0", rows)
    assert doc["allocations"] == [{"table": "npctemplate", "count": len(rows)}]
    assert len({r["id"] for r in rows}) == len(rows), "logical ids must be unique"
    assert all(r["values"]["Model"] > 0 for r in rows)
    assert all("TemplateId" not in r["values"] for r in rows)
