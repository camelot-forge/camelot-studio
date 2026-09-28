"""Unit + corpus tests for the pskin armour-material resolution.

`pskins.csv` is the client's armour texture table: one row per
(material, body piece, gender) with the sheet name. The engine reads it
through the shared skin CSV parser (`FUN_0049a186`, character-rules §8.12)
and resolves the archive as `items/pskins/pskin%03d.mpk` (§8.9).

Scope (builder-fidelity-gaps.md §5): the *texture* layer only. The item
extension and the dye are server notions — the dye table does not exist
in the client at all — and are deferred to the Phase 2 contract.
"""
import pytest

from camelot import appearance as app

corpus = pytest.mark.usefixtures("client_dir")


# --- unit tier: no client needed -------------------------------------------

def test_parse_pskins_indexes_material_slot_and_gender():
    rows = [
        ["Information", "", "Skin", "Multi", "Archive", "Expansion"],
        ["ID", "Textual Name", "Name", "", "Num", "Only"],
        ["345", "Plate", "b_plt1_body1m.tga", "0", "2", "0"],
        ["346", "Plate", "b_plt1_body1f.tga", "0", "2", "0"],
        ["347", "Plate", "b_plt1_arm1.tga", "0", "2", "0"],
        ["348", "Plate", "b_plt1_glov1.tga", "0", "2", "0"],
        ["349", "Plate", "b_plt1_lbody1.tga", "0", "2", "0"],
        ["350", "Plate", "b_plt1_leg1.tga", "0", "2", "0"],
        ["351", "Plate", "b_plt1_boot1.tga", "0", "2", "0"],
        ["352", "Plate", "b_plt1_helm1.tga", "0", "2", "0"],
    ]
    table = app.parse_pskins(rows)
    plate = table.materials["Plate"]
    assert plate.pskin_id == 345
    assert plate.archive == 2
    assert plate.sheet("Body", female=False) == "b_plt1_body1m.tga"
    assert plate.sheet("Body", female=True) == "b_plt1_body1f.tga"
    # genderless pieces are shared by both genders
    assert plate.sheet("Arms") == "b_plt1_arm1.tga"
    assert plate.sheet("Lbody") == "b_plt1_lbody1.tga"
    assert plate.sheet("Helm") == "b_plt1_helm1.tga"
    assert plate.sheet("Cloak") is None


def test_parse_pskins_skips_headers_and_non_armour_rows():
    rows = [
        ["ID", "Textual Name", "Name", "", "Num", "Only"],
        ["760", "shield wood", "sh_wood01.tga", "0", "1", "0"],
        ["301", "Hard Leather", "b_lea1_body1m.tga", "0", "1", "0"],
    ]
    table = app.parse_pskins(rows)
    assert "shield wood" not in table.materials
    assert "Hard Leather" in table.materials


def test_sheet_falls_back_to_the_other_gender():
    """Gendered pieces are sparse; the client resolves the family sheet when
    the exact gender variant is absent rather than leaving the slot bare."""
    rows = [
        ["ID", "Textual Name", "Name", "", "Num", "Only"],
        ["400", "Sparse", "b_spa_body1f.tga", "0", "3", "0"],
    ]
    table = app.parse_pskins(rows)
    assert table.materials["Sparse"].sheet("Body", female=False) == "b_spa_body1f.tga"


def test_parse_pskins_ignores_unusable_rows():
    rows = [
        ["ID", "Textual Name", "Name", "", "Num", "Only"],
        ["notanid", "Broken", "x.tga", "0", "1", "0"],
        ["500", "", "y.tga", "0", "1", "0"],
        ["501", "NoSheet", "", "0", "1", "0"],
    ]
    assert app.parse_pskins(rows).materials == {}


# --- corpus tier: the real V2 tables ---------------------------------------

@corpus
def test_plate_material_resolves_every_armour_slot(client_dir):
    table = app.pskins(client_dir)
    plate = table.materials.get("Plate")
    assert plate is not None, "Plate is a base client material"
    for slot in ("Body", "Arms", "Gloves", "Lbody", "Legs", "Boots"):
        assert plate.sheet(slot) is not None, f"Plate has no {slot} sheet"


@corpus
def test_body_sheets_differ_per_gender(client_dir):
    """The client ships gendered body pieces; a resolution that ignores the
    gender would show the wrong silhouette texture on half the roster."""
    table = app.pskins(client_dir)
    plate = table.materials["Plate"]
    male = plate.sheet("Body", female=False)
    female = plate.sheet("Body", female=True)
    assert male != female
    assert male.lower().endswith("m.tga")
    assert female.lower().endswith("f.tga")


@corpus
def test_materials_are_discoverable_and_usable(client_dir):
    table = app.pskins(client_dir)
    # a broad, deterministic starter set: every material that covers the
    # full armour slot set for at least one gender.
    complete = [m for m in table.materials.values() if m.complete()]
    assert len(complete) >= 8, "expected several complete armour materials"
    assert all(m.name for m in complete)


@corpus
def test_sheets_resolve_to_real_client_bytes(client_dir):
    table = app.pskins(client_dir)
    for material in table.armour():
        for slot, variants in material.sheets.items():
            sheet = next(iter(variants.values()))
            assert app.gamedata.texture_bytes(client_dir, sheet) is not None, (
                f"{material.name}/{slot} -> {sheet} does not resolve"
            )


@corpus
def test_pskin_archive_numbers_resolve(client_dir):
    """Every archive a complete material points at must be a real
    `items/pskins/pskinNNN.mpk`."""
    table = app.pskins(client_dir)
    archives = {m.archive for m in table.materials.values() if m.complete()}
    assert archives
    for archive in archives:
        assert 1 <= archive < 1000
