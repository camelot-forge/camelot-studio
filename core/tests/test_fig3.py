"""Unit tests for the fig3 table parsing and skeleton attachment helpers.

No game files: the CSV rows are synthetic and the skin transforms go through
a fake pyffi-shaped entry, so the bind compensation is checked on exact
matrices.
"""
import numpy as np

from camelot import fig3

PARTS_ROWS = [
    ["ID", "Text", "Part", "Expansion", "Test", "Archive", "Double"],
    ["Num", "Desc", "Filename", "Min", "Status", "", "Sided"],
    ["25", "Saracen Female Head", "sar_f_Head01", "4", "1", "15", ""],
    ["29", "Saracen Female Hair", "sar_f_Hair01", "4", "1", "7", "1"],
    ["1292", "Sar_f_Head01", "Sar_f_Head01", "4", "1", "14", ""],
    ["199", "Half Ogre Male Hair", "hal_m_Hair01", "4", "1", "15", "1"],
    ["199", "Highlander Male Hair", "hig_m_Hair03", "4", "1", "14", "1"],
    ["1400.038407", "Minotaur Gloves 1", "Gloves01_Min_m", "4", "1", "20", ""],
    ["1401.076814", "Minotaur Gloves 2", "Gloves02_Min_m", "4", "1", "20", ""],
    ["1468.993842", "Minotaur Cloak 3", "Cloak03_Min_m", "4", "1", "20", ""],
    ["", "no id", "x", "4", "1", "1", ""],
]

MAP_ROWS = [
    ["id", "", "", "", ""],
    ["#", "part", "desc", "1", "2"],
    ["4", "0", "Saracen Female Head", "1292", ""],
    ["4", "8", "Saracen Female Hair", "29", "30"],
    ["1", "0", "Briton Male Head", "1306", ""],
    ["1", "1", "Briton Male Body", "291", "292"],
    ["25", "0", "Half Ogre Male Head", "1287", ""],
    ["25", "8", "Half Ogre Male Hair", "199", "200"],
]

# Briton male's real fig3map hair row: Hair 1..6, **Bald**, Hair 8 (the
# `fig3descriptions.csv` labels). The interior empty cell must survive.
BALD_MAP_ROWS = [
    ["id", "", "", "", "", ""],
    ["#", "part", "desc", "1", "2", "3", "4"],
    ["1", "8", "Briton Male Hair", "5", "6", "", "8"],
    ["4", "8", "Saracen Female Hair", "29", "30", "", ""],
]

BALD_PARTS_ROWS = PARTS_ROWS + [
    ["5", "Briton Male Hair", "bri_m_Hair01", "4", "1", "10", "1"],
    ["6", "Briton Male Hair", "bri_m_Hair02", "4", "1", "10", "1"],
    ["8", "Briton Male Hair", "bri_m_Hair04", "4", "1", "10", "1"],
]


def test_parse_parts_skips_headers_and_bad_rows():
    parts = fig3.parse_parts(PARTS_ROWS)
    assert set(parts) == {25, 29, 199, 1292, 1400, 1401, 1468}
    assert parts[1292][0].filename == "Sar_f_Head01"
    assert parts[1292][0].archive == 14
    assert parts[25][0].archive == 15
    assert parts[29][0].double_sided is True
    assert parts[25][0].double_sided is False


def test_parse_parts_coerces_float_ids():
    """The extractor leaks numeric ids like ``1400.038407``; the engine
    reads them through an entier-style coercion (loadOnlyIntegralPart), so
    ``1400.038407`` must parse as 1400 — like an `_atol` read."""
    parts = fig3.parse_parts(PARTS_ROWS)
    assert parts[1400][0].filename == "Gloves01_Min_m"
    assert parts[1468][0].filename == "Cloak03_Min_m"


def test_parse_parts_keeps_duplicate_id_candidates():
    """fig3parts reuses ids for different files (199 = hal_m and hig_m hair)."""
    parts = fig3.parse_parts(PARTS_ROWS)
    assert [p.filename for p in parts[199]] == ["hal_m_Hair01", "hig_m_Hair03"]


def test_parse_map_groups_slots_and_strips_head_word():
    races, descs = fig3.parse_map(MAP_ROWS)
    assert races[4]["Head"] == [1292]
    assert races[4]["Hair"] == [29, 30]
    assert races[1]["Body"] == [291, 292]
    assert descs[4] == "Saracen Female"
    assert descs[1] == "Briton Male"


def test_race_id_matches_exact_then_containment():
    races, descs = fig3.parse_map(MAP_ROWS)
    tables = fig3.Tables(parts=fig3.parse_parts(PARTS_ROWS), races=races, race_desc=descs)
    assert fig3.race_id(tables, "Saracen Female") == 4
    assert fig3.race_id(tables, "Saracen Female Mummy") == 4
    # Word order differs between monnifs and fig3map ("Male Half Ogre").
    assert fig3.race_id(tables, "Male Half Ogre") == 25
    assert fig3.race_id(tables, "Frost Giant") is None
    assert fig3.race_id(tables, "") is None


def test_race_id_ignores_single_word_containment():
    """A one-word race description ("Heavy") must not match any figure that
    happens to mention the word (e.g. "Half Orc Heavy")."""
    rows = MAP_ROWS + [["50", "0", "Heavy Head", "1", ""]]
    races, descs = fig3.parse_map(rows)
    tables = fig3.Tables(parts=fig3.parse_parts(PARTS_ROWS), races=races, race_desc=descs)
    assert fig3.race_id(tables, "Half Orc Heavy") is None
    # Multi-word containment still works ("Saracen Female Mummy").
    assert fig3.race_id(tables, "Saracen Female Mummy") == 4


def test_parts_of_disambiguates_duplicate_ids_by_race():
    races, descs = fig3.parse_map(MAP_ROWS)
    tables = fig3.Tables(parts=fig3.parse_parts(PARTS_ROWS), races=races, race_desc=descs)
    assert [p.filename for p in fig3.parts_of(tables, 25, "Hair")] == ["hal_m_Hair01"]


def test_parts_of_resolves_ids_to_parts():
    tables = fig3.Tables(
        parts=fig3.parse_parts(PARTS_ROWS),
        races=fig3.parse_map(MAP_ROWS)[0],
        race_desc=fig3.parse_map(MAP_ROWS)[1],
    )
    hairs = fig3.parts_of(tables, 4, "Hair")
    assert [p.filename for p in hairs] == ["sar_f_Hair01"]
    assert fig3.parts_of(tables, 4, "Legs") == []
    assert fig3.parts_of(tables, None, "Hair") == []


def test_parse_map_keeps_interior_empty_cells():
    """A blank fig3map cell is a variant slot with no piece (the hair
    `Bald` styles): dropping it shifts every later style. Trailing blanks
    are trimmed, interior ones are kept as 0."""
    races, _ = fig3.parse_map(BALD_MAP_ROWS)
    assert races[1]["Hair"] == [5, 6, 0, 8]  # Bald at position 3
    assert races[4]["Hair"] == [29, 30]  # trailing empties trimmed


def test_part_at_resolves_positional_variants():
    tables = fig3.Tables(
        parts=fig3.parse_parts(BALD_PARTS_ROWS),
        races=fig3.parse_map(BALD_MAP_ROWS)[0],
        race_desc=fig3.parse_map(BALD_MAP_ROWS)[1],
    )
    assert fig3.slot_ids(tables, 1, "Hair") == [5, 6, 0, 8]
    assert fig3.part_at(tables, 1, "Hair", 1).filename == "bri_m_Hair01"
    assert fig3.part_at(tables, 1, "Hair", 3) is None  # Bald
    assert fig3.part_at(tables, 1, "Hair", 4).filename == "bri_m_Hair04"
    assert fig3.part_at(tables, 1, "Hair", 5) is None  # out of range
    assert fig3.part_at(tables, 1, "Hair", 0) is None  # 1-based
    assert fig3.part_at(tables, None, "Hair", 1) is None
    assert fig3.slot_ids(tables, None, "Hair") == []


def test_resolve_bone_falls_back_to_nearest_ancestor():
    target = {"Bip01", "Bip01 Head"}
    parents = {"Bip01 Head": "Bip01 Neck", "Bip01 Neck": "Bip01", "Bip01 EyeLids": "Bip01 Head"}
    assert fig3.resolve_bone("Bip01 Head", target, parents) == "Bip01 Head"
    assert fig3.resolve_bone("Bip01 EyeLids", target, parents) == "Bip01 Head"
    assert fig3.resolve_bone("Bip01 Neck", target, parents) == "Bip01"
    assert fig3.resolve_bone("Nowhere", target, parents) is None


class _FakeRotation:
    def __init__(self):
        for i in range(1, 4):
            for j in range(1, 4):
                setattr(self, f"m_{i}{j}", 0.0)


class _FakeVector:
    def __init__(self):
        self.x = self.y = self.z = 0.0


class _FakeTransform:
    def __init__(self):
        self.rotation = _FakeRotation()
        self.translation = _FakeVector()
        self.scale = 1.0


class _FakeEntry:
    def __init__(self):
        self.skin_transform = _FakeTransform()


def test_write_skin_transform_roundtrips_through_the_reader():
    from camelot.nifutil import skin_transform_matrix

    entry = _FakeEntry()
    m = np.eye(4)
    m[:3, :3] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 2.0]]
    m[:3, 3] = [1.5, -2.0, 3.25]
    fig3.write_skin_transform(entry, m)
    back = np.asarray(skin_transform_matrix(entry), dtype=float)
    # The reader scales the rotation by `scale`, so a non-uniform column (2.0)
    # comes back uniformly: store the compensated matrix with a rigid rotation.
    assert back[0, 3] == 1.5 and back[1, 3] == -2.0 and back[2, 3] == 3.25
    rigid = np.eye(4)
    rigid[:3, :3] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    rigid[:3, 3] = [-0.25, 0.5, 7.0]
    fig3.write_skin_transform(entry, rigid)
    back = np.asarray(skin_transform_matrix(entry), dtype=float)
    assert np.allclose(back, rigid, atol=1e-6)
