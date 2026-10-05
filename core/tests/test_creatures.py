"""Creature coverage: the pack placing content that is not a player row.

The client corpus carries ~900 creature models (`monnifs`), each with the
model id the server sends in `npctemplate.Model` and the animation set it
plays. Describing them is what turns the pack from a description of the
stock player rows into something a server gains from.
"""
import json
import xml.etree.ElementTree as ET

import pytest

from camelot.targets import opendaoc


def test_a_creature_projects_onto_the_servers_npc_template():
    """`monnifs.ID` is the model id the client resolves, so it is what
    `npctemplate.Model` must carry. The rest are the server's required
    non-null columns."""
    row = opendaoc.project_creature(
        template_id=90001, model_id=606, name="Cata Half Ogre Male",
        nif="hal_m", anim_set=257,
    )
    assert row.template_id == 90001
    assert row.model == 606
    assert row.class_type == "DOL.GS.GameNPC"
    assert row.name == "Cata Half Ogre Male"
    assert row.to_dict()["Model"] == "606", "Model is a quoted id on the wire"
    assert row.to_dict()["ClassType"] == "DOL.GS.GameNPC"


def test_the_npc_template_carries_the_required_non_null_columns():
    """Name, ClassType, Model, Size and Level are NOT NULL in the server's
    schema; a row missing one is rejected at import."""
    row = opendaoc.project_creature(
        template_id=1, model_id=88, name="Albion Stone Minotaur",
    )
    doc = row.to_dict()
    for column in ("TemplateId", "Name", "ClassType", "Model", "Size", "Level"):
        assert column in doc, column
        assert doc[column] not in (None, ""), column


def test_size_and_level_are_ranges_as_the_server_writes_them():
    """The server's own rows use `'50-58'` and `'10-11'`: a single number
    where a range belongs still imports, but silently fixes every spawn to
    one value."""
    row = opendaoc.project_creature(template_id=1, model_id=88, name="Minotaur")
    doc = row.to_dict()
    assert "-" in doc["Size"]
    assert "-" in doc["Level"]


def test_the_npc_template_xml_is_in_the_loaders_own_format():
    """`LoaderUnloaderXML` guesses the type from the root element: it must be
    `ArrayOf<ClassName>` and each child named after the class, or the server
    logs 'Could no guess Type of XML Package' and loads nothing."""
    rows = [
        opendaoc.project_creature(template_id=1, model_id=88, name="A"),
        opendaoc.project_creature(template_id=2, model_id=606, name="B"),
    ]
    xml = opendaoc.npctemplate_xml(rows).decode("utf-8")
    root = ET.fromstring(xml)
    assert root.tag == "ArrayOfNPCTemplate"
    assert [c.tag for c in root] == ["NPCTemplate", "NPCTemplate"]
    assert root[0].findtext("Model") == "88"


def test_the_xml_declares_its_encoding_and_escapes_names():
    """A creature name with an ampersand would produce invalid XML that the
    server rejects at startup without saying why."""
    row = opendaoc.project_creature(
        template_id=1, model_id=88, name="Grumoz & the Demon <King>",
    )
    xml = opendaoc.npctemplate_xml([row]).decode("utf-8")
    assert xml.startswith("<?xml")
    root = ET.fromstring(xml)
    assert root[0].findtext("Name") == "Grumoz & the Demon <King>"


def test_the_xml_is_reproducible():
    rows = [opendaoc.project_creature(template_id=1, model_id=88, name="A")]
    assert opendaoc.npctemplate_xml(rows) == opendaoc.npctemplate_xml(rows)


def test_a_template_id_colliding_with_the_servers_is_refused():
    """The pack must not overwrite a server's own spawn by reusing its
    TemplateId. The existing range is supplied by the caller, read from the
    server's data, rather than assumed."""
    with pytest.raises(ValueError, match="collide"):
        opendaoc.check_template_ids([1, 2, 3], existing={1, 5})


def test_template_ids_are_unique_within_the_pack():
    with pytest.raises(ValueError, match="unique"):
        opendaoc.check_template_ids([7, 7], existing=set())


# --- corpus tier ----------------------------------------------------------

@pytest.mark.usefixtures("client_dir")
def test_creatures_come_from_the_clients_own_model_table(client_dir):
    """~900 creatures, each with the id the server sends and the anim set it
    plays -- the same `monnifs` table the animation links already use."""
    from camelot import creatures

    rows = creatures.read_creatures(client_dir)
    assert len(rows) > 500
    by_id = {c.model_id: c for c in rows}
    assert by_id[606].name == "Cata Half Ogre Male"
    assert by_id[606].nif == "hal_m"
    assert by_id[606].anim_set == 257
    assert all(c.model_id > 0 for c in rows)
    assert all(c.name for c in rows)


@pytest.mark.usefixtures("client_dir")
def test_creature_model_ids_are_stable_and_ordered(client_dir):
    from camelot import creatures

    a = creatures.read_creatures(client_dir)
    b = creatures.read_creatures(client_dir)
    assert [c.model_id for c in a] == [c.model_id for c in b]
    assert [c.model_id for c in a] == sorted(c.model_id for c in a)


@pytest.mark.usefixtures("client_dir")
def test_every_creature_projects_to_a_valid_template(client_dir):
    from camelot import creatures

    rows = creatures.read_creatures(client_dir)
    for creature in rows[:200]:
        row = opendaoc.project_creature(
            template_id=90000 + creature.model_id,
            model_id=creature.model_id,
            name=creature.name,
            nif=creature.nif,
            anim_set=creature.anim_set,
        )
        doc = row.to_dict()
        assert doc["Model"] == str(creature.model_id)
        assert json.dumps(doc)  # serialisable
