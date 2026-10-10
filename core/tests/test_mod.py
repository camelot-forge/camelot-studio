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


def test_the_client_section_is_empty_when_the_mod_has_no_client_half():
    """The section travels in every pack; a server-only mod leaves it empty."""
    doc = mod.deployment("p", "1", [])
    assert doc["client"] == []
    assert doc["requires"] == []


# --- the client half ---------------------------------------------------------


class FakeTables:
    """The duck `client_patch_items` talks to, without a client directory."""

    def __init__(self, columns: dict, rows: dict | None = None,
                 names: dict | None = None):
        self._columns = columns
        self._rows = rows or {}
        #: table -> the header row, when a test needs sparse indices
        self._names = names or {}

    def column_index(self, table, name):
        try:
            return self._columns[table][name]
        except KeyError:
            raise ValueError(f"{table}: no column named {name!r}") from None

    def column_name(self, table, index):
        header = self._names.get(table) or list(self._columns[table])
        if not 0 <= index < len(header):
            raise ValueError(f"{table}: no column at index {index}")
        return header[index]

    def has_row(self, table, id_):
        return id_ in self._rows.get(table, {})

    def row_values(self, table, id_, names):
        row = self._rows.get(table, {}).get(id_)
        if row is None:
            raise ValueError(f"{table}: no row with id {id_}")
        return {name: row[name] for name in names}


def test_a_loose_item_names_its_member_by_convention():
    item = mod.client_loose("figures/Boar.NIF")
    assert item == {
        "kind": "loose",
        "path": "figures/Boar.NIF",
        "member": "client/figures/Boar.NIF",
    }


def test_an_archive_entry_names_its_place_in_the_pack():
    item = mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")
    assert item["member"] == "client/entries/figures/skins/skin003.mpk/boar.dds"


def test_a_member_must_live_under_client():
    with pytest.raises(ValueError, match="client/"):
        mod.client_loose("figures/Boar.NIF", member="targets/opendaoc/mod.json")


def test_paths_may_not_escape_the_client():
    with pytest.raises(ValueError, match="inside the client"):
        mod.client_loose("../evil.nif")
    with pytest.raises(ValueError, match="inside the client"):
        mod.client_archive_entry("figures/skins/skin003.mpk", "../../evil.dds")


def test_a_row_op_needs_the_column_indices_it_resolved_to():
    with pytest.raises(ValueError, match="column index"):
        mod.client_gamedata_set(
            "monnifs.csv", 286, {"Name": "ettin"}, expect={"Name": "crab"},
            columns={"NIF": 2},
        )


def test_a_row_op_may_not_write_the_id():
    with pytest.raises(ValueError, match="id column"):
        mod.client_gamedata_set(
            "monnifs.csv", 286, {"ID": "999"}, expect={}, columns={"ID": 0},
        )


def test_an_added_id_lives_in_the_reserved_band():
    ok = mod.client_gamedata_add(
        "monsters.csv", 2069, {"name": "Thing"}, columns={"name": 1},
    )
    assert ok["id"] == 2069
    for outside in (0, 2500):
        with pytest.raises(ValueError, match="reserved band"):
            mod.client_gamedata_add(
                "monsters.csv", outside, {"name": "Thing"}, columns={"name": 1},
            )
    # a pack carrying one is refused on load, not on install
    with pytest.raises(ValueError, match="reserved band"):
        mod.check_client_item(dict(ok, id=40000))


def test_claims_group_by_destination_not_spelling():
    items = [
        mod.client_archive_entry("figures/Skins/skin003.mpk", "BOAR.dds"),
        mod.client_gamedata_set(
            "monnifs.csv", 286, {"Name": "ettin"}, expect={"Name": "crab"},
            columns={"Name": 2},
        ),
        mod.client_gamedata_add(
            "monsters.csv", 2495, {"name": "Thing"}, columns={"name": 1},
        ),
    ]
    assert mod.claims(items) == [
        "entry:figures/skins/skin003.mpk#boar.dds",
        "row:monnifs.csv#286#2",
        "row:monsters.csv#2495",
    ]


def test_one_mod_may_not_claim_a_destination_twice():
    items = [mod.client_loose("figures/Boar.NIF"), mod.client_loose("figures/boar.nif")]
    with pytest.raises(ValueError, match="same destination"):
        mod.deployment("p", "1", [], client=items)


def test_the_deployment_carries_the_client_items():
    items = [mod.client_loose("figures/Boar.NIF")]
    doc = mod.deployment("p", "1", [], client=items)
    assert doc["client"] == items


def test_a_patch_resolves_columns_and_captures_expect():
    tables = FakeTables(
        columns={"monnifs.csv": {"Name": 2, "Anim Set": 3}},
        rows={"monnifs.csv": {286: {"Name": "crab", "Anim Set": "3"}}},
    )
    items = mod.client_patch_items(
        {
            "gamedata": [
                {"table": "monnifs.csv", "set": {"286": {"Name": "ettin"}}},
                {"table": "monnifs.csv",
                 "add": [{"id": 2496, "values": {"Name": "Thing"}}]},
            ],
        },
        tables,
    )
    assert items[0]["columns"] == {"Name": 2}
    assert items[0]["expect"] == {"Name": "crab"}
    assert items[1]["action"] == "add"
    assert items[1]["columns"] == {"Name": 2}


def test_an_ambiguous_column_is_written_name_at_index():
    tables = FakeTables(
        columns={"monsters.csv": {"name": 1}},
        names={"monsters.csv": [""] * 68 + ["Body"]},
    )
    items = mod.client_patch_items(
        {"gamedata": [{"table": "monsters.csv",
                       "add": [{"id": 2495, "values": {"Body@68": "5"}}]}]},
        tables,
    )
    assert items[0]["columns"] == {"Body@68": 68}
    with pytest.raises(ValueError, match="@"):
        mod.client_patch_items(
            {"gamedata": [{"table": "monsters.csv",
                           "add": [{"id": 2496, "values": {"Body@nope": "5"}}]}]},
            tables,
        )


def test_a_patch_refuses_an_add_on_an_existing_id():
    tables = FakeTables(
        columns={"monnifs.csv": {"Name": 2}},
        rows={"monnifs.csv": {2495: {"Name": "crab"}}},
    )
    with pytest.raises(ValueError, match="already exists"):
        mod.client_patch_items(
            {"gamedata": [{"table": "monnifs.csv",
                           "add": [{"id": 2495, "values": {"Name": "Thing"}}]}]},
            tables,
        )


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
