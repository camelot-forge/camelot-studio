"""Corpus tests for the appearance-resolution module.

Skipped without ``$DAOC_CLIENT``; the assertions mirror the engine semantics
documented in the client's tables and the module's docstring.
"""
import pytest

from camelot import appearance as app
from camelot import gamedata

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


def test_hair_composite_blends_the_decal(client_dir):
    """The Hair texture is the base sheet with the `M` decal alpha-blended:
    the engine selects `ps_HairMaterialDecalShader1x` when the third
    haircolormap cell is set, and the decal is a 1-bit-alpha cutout on the
    base's own UV set."""
    import numpy as np

    from camelot.dxt import dds_rgba, parse_dds_header

    base, _tint, decal = app._hair_triplets(client_dir, 4, 1)[0]  # Saracen F
    assert decal  # Saracen female style 1 ships a decal
    comp = app.hair_composite(client_dir, base, decal)
    plain = gamedata.skin_texture(client_dir, base)
    assert comp is not None and plain is not None
    assert comp[1] != plain[1]

    width, height, fourcc = parse_dds_header(comp[1])
    assert fourcc == b""  # baked uncompressed
    assert (width, height) == (256, 256)
    _w, _h, pixels = dds_rgba(comp[1])
    baked = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)
    _w, _h, base_px = dds_rgba(plain[1])
    original = np.frombuffer(base_px, np.uint8).reshape(height, width, 4)
    # the base's strand alpha (cutout) survives the bake…
    assert np.array_equal(baked[:, :, 3], original[:, :, 3])
    # …and the decal region re-coloured the sheet
    assert not np.array_equal(baked[:, :, :3], original[:, :, :3])


def test_hair_composite_without_decal_returns_base(client_dir):
    base, _tint, decal = app._hair_triplets(client_dir, 1, 1)[0]  # Briton M
    assert decal == ""
    assert app.hair_composite(client_dir, base, decal) == gamedata.skin_texture(
        client_dir, base
    )


def test_eye_composite_paints_the_eye(client_dir):
    """The Eyes texture is the face sheet's painted eye multiplied by the
    `eyecolor_*` dark-map decal (per eye colour)."""
    import numpy as np

    from camelot.dxt import dds_rgba, parse_dds_header

    comp = app.eye_composite(client_dir, 4, 1, 1)
    assert comp is not None
    assert comp[0].lower().startswith("eyecolor_")
    width, height, fourcc = parse_dds_header(comp[1])
    assert fourcc == b""  # baked uncompressed
    assert (width, height) == (256, 256)  # the face sheet's grid
    _w, _h, pixels = dds_rgba(comp[1])
    rgb = np.frombuffer(pixels, np.uint8).reshape(height, width, 4)[:, :, :3].astype(int)
    # the plain eye sheet is nearly all white: the composite must carry the
    # artwork (dark pupil/iris pixels)
    assert (rgb.sum(axis=2) < 200).sum() > 0

    # the ring is the iris colour: each eye colour bakes a different sheet
    other = app.eye_composite(client_dir, 4, 1, 2)
    assert other is not None and other[1] != comp[1]


def test_skin_tint_varies(client_dir):
    ids = {app.skin_tint(client_dir, 4, tone) for tone in range(1, 9)}
    assert len(ids) == 8
    assert app.skin_tint(client_dir, 1, 1) != app.skin_tint(client_dir, 3, 1)


def test_tint_rgb_reads_fig3tints(client_dir):
    # `fig3tints.csv` rows are id + name + Red + Green + Blue (engine packs
    # them (R<<16)+(G<<8)+B); the tables also carry header rows.
    assert app.tint_rgb(client_dir, 143) == (223, 207, 166)  # Saracen Skin 2
    assert app.tint_rgb(client_dir, 121) == (110, 107, 59)  # Hair Tint 25
    assert app.tint_rgb(client_dir, 188) == (255, 255, 255)  # "No tint"
    assert app.tint_rgb(client_dir, 999999) is None


def test_skin_tone_tints(client_dir):
    # Saracen female tones are the ids 41 (white, no-op) then 143..149.
    assert app.skin_tint_rgb(client_dir, 4, 1) is None
    assert app.skin_tint_rgb(client_dir, 4, 2) == (223, 207, 166)
    assert app.skin_tint_rgb(client_dir, 4, 8) == (24, 0, 0)


def test_hair_colour_tints(client_dir):
    # Saracen varies the tint across the 16 colours of a style…
    assert app.hair_tint_rgb(client_dir, 4, 1, 1) == (145, 145, 145)
    assert app.hair_tint_rgb(client_dir, 4, 1, 2) == (110, 107, 59)
    # …while Lurikeen pre-colours the sheets and ships "No tint" (white).
    assert app.hair_tint_rgb(client_dir, 18, 1, 1) is None


def test_decal_ids_where_shipped(client_dir):
    # decalmap rows are sparse (only the Shar/Midgard tribes + 235 bp);
    # char-engine rows 7/8/31..33 carry data (pass-8 probe).
    found = [
        int(r[0])
        for r in gamedata._read_table(client_dir, "fig3decalmap.csv")[2:]
        if r and any(cell.strip() for cell in r[2:])
    ]
    assert len(found) >= 1
