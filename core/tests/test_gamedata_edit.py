"""The gamedata table editor: byte fidelity, resolution, row ops.

Fidelity is the point: the client reads latin-1 files with CRLF endings and
stops reading a table at a blank line (measured — a split/join round-trip
inserted one and the server had a mob no client could see,
`notes`). So an edit must leave untouched lines
byte-identical and re-encode only what it touched.
"""
import pytest

from camelot import gamedata
from camelot.gamedata import TableError
from camelot.mpak import write_mpak

MONSTERS = (
    b"Information,,NIF,Skins,\r\n"
    b"ID,name,#,Body,\r\n"
    b"99,Boar,221,109,\r\n"
    b"286,Crab,231,110,\r\n"
)
MONNIFS = (
    b"Information,,NIF,Anim,\r\n"
    b"ID,Textual Name,Name,Set,\r\n"
    b"231,Crab,crab,3,\r\n"
)


def test_parsing_keeps_the_bytes_and_the_line_endings():
    table = gamedata.parse_table("monsters.csv", MONSTERS)
    assert table.newline == b"\r\n"
    assert table.trailing is True
    assert len(table.lines) == 4
    assert table.rows[0][0] == "Information"
    assert table.names == ["ID", "name", "#", "Body", ""]


def test_a_table_round_trips_byte_for_byte():
    table = gamedata.parse_table("monsters.csv", MONSTERS)
    assert gamedata.encode_table(table) == MONSTERS


def test_a_table_without_a_trailing_newline_round_trips():
    raw = MONSTERS[:-2]
    assert gamedata.encode_table(gamedata.parse_table("monsters.csv", raw)) == raw


def test_an_empty_table_round_trips():
    assert gamedata.encode_table(gamedata.parse_table("x.csv", b"")) == b""


def test_mixed_line_endings_are_refused():
    with pytest.raises(TableError, match="mixed"):
        gamedata.parse_table("x.csv", b"a,b\r\nc,d\ne,f\r\n")


def test_column_names_resolve_case_insensitively_and_by_exact_spelling():
    table = gamedata.parse_table("monsters.csv", MONSTERS)
    assert table.column_index("name") == 1
    assert table.column_index("#") == 2
    assert table.column_index(" body ") == 3
    with pytest.raises(TableError, match="no column"):
        table.column_index("nope")


def test_an_ambiguous_column_must_be_given_by_index():
    table = gamedata.parse_table("x.csv", b"Info,,,\r\nOnly,Only,Name\r\na,b,c\r\n")
    assert table.column_index("Name") == 2
    with pytest.raises(TableError, match="ambiguous"):
        table.column_index("only")


def test_rows_are_found_by_their_id_column():
    table = gamedata.parse_table("monsters.csv", MONSTERS)
    assert table.row_index(286) == 3
    assert table.row_index(12345) is None
    assert table.row_values(3, {"name": 1}) == {"name": "Crab"}


def test_a_set_re_encodes_only_the_touched_line():
    edited = gamedata.edit_table(MONSTERS, "monsters.csv", [{
        "action": "set", "id": 286,
        "columns": {"#": 2}, "values": {"#": "999"}, "expect": {"#": "231"},
    }])
    assert b"286,Crab,999,110,\r\n" in edited
    # everything else is the original, byte for byte
    assert edited.replace(b"286,Crab,999,110,\r\n", b"286,Crab,231,110,\r\n") == MONSTERS


def test_a_set_checks_what_it_expected_to_replace():
    with pytest.raises(TableError, match="expected 'moth', found 'crab'"):
        gamedata.edit_table(MONNIFS, "monnifs.csv", [{
            "action": "set", "id": 231,
            "columns": {"Name": 2}, "values": {"Name": "ettin"},
            "expect": {"Name": "moth"},
        }])


def test_a_set_on_a_missing_row_is_refused():
    with pytest.raises(TableError, match="no row with id 4000"):
        gamedata.edit_table(MONSTERS, "monsters.csv", [{
            "action": "set", "id": 4000,
            "columns": {"name": 1}, "values": {"name": "X"}, "expect": {"name": "Y"},
        }])


def test_a_row_op_may_not_touch_the_id_column():
    with pytest.raises(TableError, match="id column"):
        gamedata.edit_table(MONSTERS, "monsters.csv", [{
            "action": "set", "id": 286,
            "columns": {"ID": 0}, "values": {"ID": "999"}, "expect": {},
        }])


def test_an_add_appends_one_line_with_the_id_in_column_zero():
    added = gamedata.edit_table(MONSTERS, "monsters.csv", [{
        "action": "add", "id": 60000,
        "columns": {"name": 1, "#": 2, "Body": 3},
        "values": {"name": "Probe Crab", "#": "231", "Body": "110"},
    }])
    assert added == MONSTERS + b"60000,Probe Crab,231,110,\r\n"
    # no blank line sneaked in before the new row -- the client stops there
    assert b"\r\n\r\n" not in added


def test_an_add_is_padded_to_the_width_of_the_table_s_rows():
    """The client stops reading a table at a short line.

    Measured in P5-F: rows added with only their non-empty fields (61 of the
    100 `monsters.csv` carries) never rendered, while a full-width copy did.
    """
    added = gamedata.edit_table(MONSTERS, "monsters.csv", [{
        "action": "add", "id": 60000,
        "columns": {"name": 1}, "values": {"name": "Probe Crab"},
    }])
    assert added == MONSTERS + b"60000,Probe Crab,,,\r\n"


def test_an_add_to_a_file_without_a_trailing_newline_does_not_merge_rows():
    raw = MONSTERS[:-2]
    added = gamedata.edit_table(raw, "monsters.csv", [{
        "action": "add", "id": 60000,
        "columns": {"name": 1}, "values": {"name": "Probe Crab"},
    }])
    assert added == raw + b"\r\n" + b"60000,Probe Crab,,,\r\n"


def test_an_add_on_an_existing_id_is_refused():
    with pytest.raises(TableError, match="already exists"):
        gamedata.edit_table(MONSTERS, "monsters.csv", [{
            "action": "add", "id": 286,
            "columns": {"name": 1}, "values": {"name": "X"},
        }])


def test_a_remove_drops_the_row_and_its_line():
    """The editor's journal removes placements (6.3). The pack carries whole
    assembled files, so the op never reaches an installer — but it runs
    through the same path, and obeys the same `expect` rule."""
    edited = gamedata.edit_table(MONSTERS, "monsters.csv", [{
        "action": "remove", "id": 99,
        "columns": {"Body": 3}, "expect": {"Body": "109"},
    }])
    assert edited == (
        b"Information,,NIF,Skins,\r\n"
        b"ID,name,#,Body,\r\n"
        b"286,Crab,231,110,\r\n"
    )
    with pytest.raises(TableError, match="expected '110', found '109'"):
        gamedata.edit_table(MONSTERS, "monsters.csv", [{
            "action": "remove", "id": 99,
            "columns": {"Body": 3}, "expect": {"Body": "110"},
        }])
    with pytest.raises(TableError, match="no row with id 4000"):
        gamedata.edit_table(MONSTERS, "monsters.csv", [{"action": "remove", "id": 4000}])


def test_values_round_trip_in_latin_1_and_anything_else_is_refused():
    edited = gamedata.edit_table(MONNIFS, "monnifs.csv", [{
        "action": "set", "id": 231,
        "columns": {"Name": 2}, "values": {"Name": "cr\u00e9ature"},
        "expect": {"Name": "crab"},
    }])
    assert b"cr\xe9ature" in edited
    with pytest.raises(TableError, match="latin-1"):
        gamedata.edit_table(MONNIFS, "monnifs.csv", [{
            "action": "set", "id": 231,
            "columns": {"Name": 2}, "values": {"Name": "\u20ac"},
            "expect": {"Name": "crab"},
        }])


def test_ops_apply_in_order_against_the_running_image():
    edited = gamedata.edit_table(MONSTERS, "monsters.csv", [
        {"action": "set", "id": 286, "columns": {"#": 2},
         "values": {"#": "999"}, "expect": {"#": "231"}},
        {"action": "set", "id": 286, "columns": {"name": 1},
         "values": {"name": "Replaced"}, "expect": {"name": "Crab"}},
    ])
    assert b"286,Replaced,999,110,\r\n" in edited


def test_client_tables_reads_a_client_directory(tmp_path):
    write_mpak(str(tmp_path / "gamedata.mpk"), "gamedata.mpk", [
        ("monsters.csv", MONSTERS),
        ("monnifs.csv", MONNIFS),
    ])
    tables = gamedata.ClientTables(str(tmp_path))
    assert tables.column_index("monsters.csv", "#") == 2
    assert tables.has_row("monnifs.csv", 231) is True
    assert tables.has_row("monnifs.csv", 999) is False
    assert tables.row_values("monnifs.csv", 231, ["Name", "Set"]) == {
        "Name": "crab", "Set": "3",
    }
    assert gamedata.read_table_bytes(str(tmp_path), "monnifs.csv") == MONNIFS
    with pytest.raises(TableError, match="no table"):
        gamedata.read_table_bytes(str(tmp_path), "fig3parts.csv")


# --- corpus tier -------------------------------------------------------------

def test_a_real_table_round_trips_and_an_edit_moves_one_line(client_dir):
    """The real monsters.csv: 2 249 lines, ragged rows, CRLF, latin-1.

    A no-op edit may re-encode its line, but nothing else may move, and the
    table must parse back identically -- the client stops at a blank line,
    so a stray byte is a table the game stops reading.
    """
    raw = gamedata.read_table_bytes(client_dir, "monsters.csv")
    table = gamedata.parse_table("monsters.csv", raw)
    assert gamedata.encode_table(table) == raw
    assert len(table.rows) > 2000

    index = gamedata.HEADER_ROWS
    row = table.rows[index]
    column = len(row) - 1
    edited = gamedata.edit_table(raw, "monsters.csv", [{
        "action": "set", "id": int(row[gamedata.ID_COLUMN]),
        "columns": {"col": column}, "values": {"col": row[column]},
        "expect": {"col": row[column]},
    }])
    before = raw.split(table.newline)
    after = edited.split(table.newline)
    differing = {i for i, (a, b) in enumerate(zip(before, after)) if a != b}
    assert differing <= {index}, f"lines moved: {sorted(differing)}"
    assert gamedata.parse_table("monsters.csv", edited).rows == table.rows


def test_a_row_added_to_the_real_monsters_table_is_full_width(client_dir):
    """P5-F: a short added row is a row no client renders.

    Every shipped row carries the same field count; the added one must too.
    """
    raw = gamedata.read_table_bytes(client_dir, "monsters.csv")
    table = gamedata.parse_table("monsters.csv", raw)
    edited = gamedata.edit_table(raw, "monsters.csv", [{
        "action": "add", "id": 4095,
        "columns": {"name": 1, "#": 2, "Body": 3},
        "values": {"name": "Probe 4095", "#": "286", "Body": "1172"},
    }])
    parsed = gamedata.parse_table("monsters.csv", edited)
    assert len(parsed.rows[-1]) == table.width
    assert parsed.rows[-1][1] == "Probe 4095"
    assert b"\r\n\r\n" not in edited
