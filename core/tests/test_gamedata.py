"""Unit tests for gamedata figure->texture resolution (synthetic archives).

Builds a minimal gamedata.mpk and a figures/skins/skinNNN.mpk with the MPAK
writer, so the resolution runs without any game file.
"""
import os

import pytest

from camelot.gamedata import resolve_figure_skins
from camelot.mpak import write_mpak


def _row(cols):
    return ",".join(cols)


@pytest.fixture
def fake_client(tmp_path):
    """A minimal DAoC-client-shaped tree with one monster and its texture."""
    gamedata = [
        ("monsters.csv", [
            "Information,,,,,,,,,,,,",
            "ID,name,#,Body,Head,Arms,Gloves,Lbody,Legs,Boots,Cloak,Helm",
            "99,Boar,221,109,0,0,0,0,0,0,0,0",
        ]),
        ("monnifs.csv", [
            "Information,,NIF,Anim",
            "ID,Textual Name,Name,Set",
            "221,Boar,boar,12",
        ]),
        ("skins.csv", [
            "Information,,Skin,,Archive",
            "ID,Textual Name,Name,MULTI,Num",
            "109,Boar,boar.tga,0,3",
        ]),
    ]
    entries = [(name, ("\n".join(rows) + "\n").encode("latin-1")) for name, rows in gamedata]
    write_mpak(str(tmp_path / "gamedata.mpk"), "gamedata.mpk", entries)

    skin_dir = tmp_path / "figures" / "skins"
    skin_dir.mkdir(parents=True)
    dds = b"DDS " + b"\x00" * 124 + b"fake-pixels"
    write_mpak(str(skin_dir / "skin003.mpk"), "skin003.mpk", [("boar.dds", dds)])
    return str(tmp_path), dds


def test_resolves_monster_texture(fake_client):
    client_dir, dds = fake_client
    skins = resolve_figure_skins(client_dir, os.path.join(client_dir, "figures", "Boar.NIF"))
    assert len(skins) == 1
    slot, name, data = skins[0][:3]
    assert slot == "Body"
    assert name == "boar.tga"
    assert data == dds


def test_unknown_figure_returns_empty(fake_client):
    client_dir, _ = fake_client
    assert resolve_figure_skins(client_dir, os.path.join(client_dir, "figures", "Nope.NIF")) == []


def test_matches_monnifs_name_without_separators(tmp_path):
    """The tables spell names differently from the files (FrostGiant)."""
    write_mpak(str(tmp_path / "gamedata.mpk"), "gamedata.mpk", [
        ("monsters.csv", b"Information,,,,,,\nID,name,#,Body,Head\n1,Frost Giant,10,5,0\n"),
        ("monnifs.csv", b"Information,,NIF,Anim\nID,Textual Name,Name,Set\n10,Frost Giant,FrostGiant,1\n"),
        ("skins.csv", b"Information,,Skin,,Archive\nID,Textual Name,Name,MULTI,Num\n5,Frost Giant,frost_giant.tga,0,3\n"),
    ])
    skin_dir = tmp_path / "figures" / "skins"
    skin_dir.mkdir(parents=True)
    dds = b"DDS " + b"\x00" * 124 + b"pixels"
    write_mpak(str(skin_dir / "skin003.mpk"), "skin003.mpk", [("frost_giant.dds", dds)])
    skins = resolve_figure_skins(str(tmp_path), str(tmp_path / "figures" / "Frost_Giant.NIF"))
    assert [(s.slot, s.name) for s in skins] == [("Body", "frost_giant.tga")]
    assert skins[0][2] == dds


def test_missing_skin_archive_returns_empty(tmp_path):
    write_mpak(str(tmp_path / "gamedata.mpk"), "gamedata.mpk", [
        ("monsters.csv", b"Information,,NIF\nID,name,#,Body\n99,Thing,1,5\n"),
        ("monnifs.csv", b"Information,,NIF\nID,Textual Name,Name\n1,Thing,thing\n"),
        ("skins.csv", b"Information,,Skin,,Archive\nID,Textual Name,Name,MULTI,Num\n5,Thing,thing.tga,0,9\n"),
    ])
    (tmp_path / "figures").mkdir()
    assert resolve_figure_skins(str(tmp_path), os.path.join(str(tmp_path), "Thing.NIF")) == []

def test_figure_desc_reads_the_monnifs_description(fake_client):
    from camelot.gamedata import figure_desc

    client_dir, _ = fake_client
    assert figure_desc(client_dir, os.path.join(client_dir, "figures", "Boar.NIF")) == "Boar"
    assert figure_desc(client_dir, os.path.join(client_dir, "figures", "Nope.NIF")) is None
