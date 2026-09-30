"""Target projections: the server-neutral contract mapped onto one server.

A projection translates logical ids into a target's own values. It must not
reimplement client semantics -- fig3, KFA and gamedata stay in `core/`. The
target's own vocabulary (its race table, its bit-packed model word) is the
only thing a projection is allowed to know.
"""
import pytest

from camelot.targets import opendaoc


def test_open_daoc_race_ids_match_the_servers_own_table():
    """The server's `race` table numbers the 18 playable races 1..18 and the
    three Minotaurs 19..21. A projection that guesses the numbering would
    create characters on the wrong race."""
    expected = {
        "briton": 1, "avalonian": 2, "highlander": 3, "saracen": 4,
        "norseman": 5, "troll": 6, "dwarf": 7, "kobold": 8, "celt": 9,
        "firbolg": 10, "elf": 11, "lurikeen": 12, "inconnu": 13,
        "valkyn": 14, "sylvan": 15, "halforge": 16, "frostalf": 17,
        "shar": 18,
    }
    assert opendaoc.RACE_IDS == expected
    assert all(1 <= v <= 18 for v in opendaoc.RACE_IDS.values())


def test_the_two_names_the_client_spells_differently_are_normalised():
    """`fig3descriptions` says `Norse` and `Half Ogre`; the server's `race`
    table says `Norseman` and `HalfOgre`. Matching on the client's spelling
    alone would drop two of the eighteen races."""
    assert opendaoc.server_race_name("Norse") == "Norseman"
    assert opendaoc.server_race_name("Half Ogre") == "HalfOgre"
    assert opendaoc.server_race_name("Briton") == "Briton"
    # unknown names are refused, not guessed
    with pytest.raises(KeyError):
        opendaoc.server_race_name("Minotaur")


def test_the_model_word_is_a_bitfield_not_a_plain_id():
    """`CharacterCreateRequestHandler` decodes the model as
    `(CreationModel >> 11) & 3` for the body size and bans a zero one, so
    the size lives in the high bits and must survive packing."""
    word = opendaoc.pack_model(size=2, **opendaoc.DEFAULT_MODEL_FIELDS)
    assert (word >> 11) & 3 == 2
    # an unspecified field must not collide with a specified one
    assert opendaoc.unpack_model_size(
        opendaoc.pack_model(size=3, **opendaoc.DEFAULT_MODEL_FIELDS)
    ) == 3


def test_a_zero_size_is_refused_rather_than_shipped():
    """The server autobans a player whose model says size 0, so a projection
    must never emit one."""
    with pytest.raises(ValueError):
        opendaoc.pack_model(size=0, **opendaoc.DEFAULT_MODEL_FIELDS)


def test_race_gender_is_packed_as_the_client_does():
    """`Race = byte & 0x1F`, `Gender = (byte >> 7) & 1` -- the low five bits
    carry the race, the top bit the gender."""
    assert opendaoc.pack_race_gender(race=6, female=False) == 0x06
    assert opendaoc.pack_race_gender(race=6, female=True) == 0x86
    assert opendaoc.unpack_race(opendaoc.pack_race_gender(18, True)) == 18
    assert opendaoc.unpack_gender(opendaoc.pack_race_gender(18, True)) is True
    assert opendaoc.unpack_gender(opendaoc.pack_race_gender(18, False)) is False


def test_a_race_id_outside_the_five_bits_is_refused():
    with pytest.raises(ValueError):
        opendaoc.pack_race_gender(race=32, female=False)


def test_a_figure_projects_to_the_servers_vocabulary(client_dir):
    """A contract figure becomes a target row: the server's race name and
    id, the packed byte, and the model word. Nothing client-side leaks in."""
    from camelot import contract

    manifest = contract.build_player_manifest(
        client_dir, pack_id="p", version="1", with_states=False,
    )
    troll = next(f for f in manifest.figures if f.logical_id == "daoc.player.tro_m")
    row = opendaoc.project(troll)
    assert row.race == "Troll"
    assert row.race_id == 6
    assert row.gender == 0
    assert row.race_gender == 0x06
    assert row.model != 0
    assert row.logical_id == "daoc.player.tro_m", "the link back must survive"


def test_projection_covers_every_playable_race(client_dir):
    from camelot import contract

    manifest = contract.build_player_manifest(
        client_dir, pack_id="p", version="1", with_states=False,
    )
    rows = [opendaoc.project(f) for f in manifest.figures]
    assert len(rows) == 36
    assert sorted({r.race_id for r in rows}) == list(range(1, 19))


def test_projection_refuses_an_unknown_figure(client_dir):
    from camelot import contract

    manifest = contract.build_player_manifest(
        client_dir, pack_id="p", version="1", with_states=False,
    )
    manifest.figures.append(contract.Figure(
        logical_id="daoc.custom.mermaid", figure_id=0, kind="player",
        name="Mermaid Female", rig="mer_m",
    ))
    with pytest.raises(KeyError):
        opendaoc.project(manifest.figures[-1])


def test_a_projection_is_deterministic(client_dir):
    from camelot import contract

    manifest = contract.build_player_manifest(
        client_dir, pack_id="p", version="1", with_states=False,
    )
    a = [opendaoc.project(f).to_dict() for f in manifest.figures]
    b = [opendaoc.project(f).to_dict() for f in manifest.figures]
    assert a == b
