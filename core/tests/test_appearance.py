"""Corpus tests for the appearance-resolution module.

Skipped without ``$DAOC_CLIENT``; the assertions mirror the engine
semantics documented in ``notes/character-rules.md`` §8.15 and §11.
"""
import pytest

from camelot import appearance as app, gamedata

pytestmark = pytest.mark.usefixtures("client_dir")

CLIENT = pytest.fixture(lambda client_dir: client_dir)


def test_face_sheet_resolves_all_seven_faces(client_dir):
    for face in (1, 2, 3, 4, 5, 6, 7):
        found = app.face_sheet(client_dir, 4, face)
        assert found is not None, "no sheet"
        assert "Sar_f_Head" in found[0]


def test_face_sheet_matches_races(client_dir):
    assert "bri_m_head" in app.face_sheet(client_dir, 1, 1)[0].lower()
    assert "bri_f_head" in app.face_sheet(client_dir, 2, 1)[0].lower()


def test_hair_triplets_16_per_style(client_dir):
    triplets = app._hair_triplets(client_dir, 4, 1)
    assert len(triplets) >= 16
    sheet, tint, decal = triplets[0]
    assert sheet.isdigit() and tint and decal


def test_hair_style_rows_exist(client_dir):
    rows = app._haircolormap_rows(client_dir)
    assert len(rows.get(1, [])) >= 4  # Briton male has >= 4 styles
    assert rows.get(4, [])  # Saracen female too


def test_eye_sheet_per_colour(client_dir):
    sheets = {app.eye_sheet(client_dir, 4, colour)[0]
              for colour in range(1, 9)}
    assert len(sheets) == 8  # 8 distinct eye sheets per race


def test_skin_tint_varies(client_dir):
    ids = {app.skin_tint(client_dir, 4, tone) for tone in range(1, 9)}
    assert len(ids) == 8
    assert app.skin_tint(client_dir, 1, 1) != app.skin_tint(client_dir, 3, 1)


def test_decal_ids_where_shipped(client_dir):
    # decalmap rows are sparse (only the Shar/Midgard tribes + 235 bp);
    # char-engine rows 7/8/31..33 carry data (pass-8 probe).
    found = [
        int(r[0])
        for r in gamedata._read_table(client_dir, "fig3decalmap.csv")[2:]
        if r and any(cell.strip() for cell in r[2:])
    ]
    assert len(found) >= 1
