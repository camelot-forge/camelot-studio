"""Unit tests for idle-KFA resolution and track building (no game files)."""


from camelot.anims import native_fps, resolve_idle_kfa


def test_resolve_prefers_plain_idle(tmp_path):
    anims = tmp_path / "anims"
    anims.mkdir()
    for name in ("BOAR_cidle.kfa", "BOAR_idle2.kfa", "BOAR_idle.kfa"):
        (anims / name).write_bytes(b"kfa")
    got = resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "Boar.NIF"))
    assert got is not None and got.endswith("BOAR_idle.kfa")


def test_resolve_skips_cata_variants(tmp_path):
    anims = tmp_path / "anims"
    anims.mkdir()
    (anims / "Boar_CATA-CIDLE.kfa").write_bytes(b"kfa")
    assert resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "Boar.NIF")) is None


def test_resolve_missing_anim_dir(tmp_path):
    assert resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "Boar.NIF")) is None


def test_resolve_player_figure_alias(tmp_path):
    anims = tmp_path / "anims"
    anims.mkdir()
    for name in ("britm_walk.kfa", "britm_idle.kfa", "britm_cidle.kfa"):
        (anims / name).write_bytes(b"kfa")
    got = resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "BritonM1.NIF"))
    assert got is not None and got.endswith("britm_idle.kfa")


def test_resolve_direct_name_beats_alias(tmp_path):
    anims = tmp_path / "anims"
    anims.mkdir()
    for name in ("britm1_idle.kfa", "britm_idle.kfa"):
        (anims / name).write_bytes(b"kfa")
    got = resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "Britm1.NIF"))
    assert got is not None and got.endswith("britm1_idle.kfa")


def _write_gamedata(tmp_path, monnifs: str, anims: str, animnifs: str):
    from camelot.mpak import write_mpak

    write_mpak(
        str(tmp_path / "gamedata.mpk"),
        "gamedata",
        [
            ("monnifs.csv", monnifs.encode("latin1")),
            ("anims.csv", anims.encode("latin1")),
            ("animnifs.csv", animnifs.encode("latin1")),
        ],
    )


def test_gamedata_chain_links_creature_not_named_after_its_kfa(tmp_path):
    """monnifs -> anims -> animnifs: Arachite02 -> sect_idle.kfa."""
    from camelot.anims import resolve_anim_from_gamedata, resolve_idle_kfa

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim,Stride\n"
        "ID,Textual Name,Name,Set,Stride\n"
        "260,arachite 1,arachite01,38,225\n"
        "299,Afanc,afanc,61,300\n",
        ",,0,1,2\n"
        "ID,name,Walk,Idle,Death\n"
        "38,Arachites,20,21,14\n"
        "61,Afanc,643,640,638\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "21,arachite idle,sect_idle.kfa,20,15,15,loop\n"
        "640,afanc idle,afanc_idle.kfa,16,5,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    (tmp_path / "anims" / "sect_idle.kfa").write_bytes(b"kfa")
    (tmp_path / "anims" / "afanc_idle.kfa").write_bytes(b"kfa")

    got = resolve_anim_from_gamedata(str(tmp_path), str(tmp_path / "figures" / "Arachite01.NIF"))
    assert got is not None and got.endswith("sect_idle.kfa")
    # the public resolver prefers the game's link over filename conventions
    assert resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "afanc.NIF")).endswith(
        "afanc_idle.kfa"
    )


def test_gamedata_chain_picks_case_insensitively_and_falls_back(tmp_path):
    from camelot.anims import resolve_idle_kfa

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim\n"
        "ID,Textual Name,Name,Set\n"
        "970,amb_bat,amb_bat,413\n",
        ",,0,1,2\n"
        "ID,name,Fly,Idle,Death\n"
        "413,amb_bat,4058,4058,4059\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "4058,amb_bat fly,amb_bat_fly.kfa,7,15,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    (tmp_path / "anims" / "amb_bat_fly.kfa").write_bytes(b"kfa")
    (tmp_path / "anims" / "Wolf_idle.kfa").write_bytes(b"kfa")
    assert resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "amb_bat.nif")).endswith(
        "amb_bat_fly.kfa"
    )
    # no gamedata row for the wolf: filename convention still applies
    assert resolve_idle_kfa(str(tmp_path), str(tmp_path / "figures" / "Wolf.NIF")).endswith(
        "Wolf_idle.kfa"
    )


def test_gamedata_prefers_standing_idle_over_sitting(tmp_path):
    """The black cat's Idle slot is a sit-tailswipe; Idle3 is the stand idle."""
    from camelot.anims import resolve_anim_from_gamedata

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim\n"
        "ID,Textual Name,Name,Set\n"
        "962,amb_blackcat,amb_blackcat,409\n",
        ",,0,1,2,3\n"
        "ID,name,Walk,Idle,Idle2,Idle3\n"
        "409,Black Cat,4043,4039,4044,4041\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "4039,cat sit tailswipe,amb_blackcat_SIT_TAILSWIPE.kfa,40,15,15,loop\n"
        "4041,cat stand idle,amb_blackcat_STAND_IDLE.kfa,40,15,15,loop\n"
        "4044,cat sit idle,amb_blackcat_SIT_IDLE.kfa,40,15,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    for f in ("amb_blackcat_SIT_TAILSWIPE.kfa", "amb_blackcat_STAND_IDLE.kfa", "amb_blackcat_SIT_IDLE.kfa"):
        (tmp_path / "anims" / f).write_bytes(b"kfa")
    got = resolve_anim_from_gamedata(str(tmp_path), str(tmp_path / "figures" / "amb_blackcat.nif"))
    assert got is not None and got.endswith("amb_blackcat_STAND_IDLE.kfa")


def test_gamedata_keeps_sit_when_nothing_stands(tmp_path):
    from camelot.anims import resolve_anim_from_gamedata

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim\n"
        "ID,Textual Name,Name,Set\n"
        "1,cat,cat,9\n",
        ",,0,1\n"
        "ID,name,Idle,Sit Idle\n"
        "9,Cat,4039,4044\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "4039,cat sit tailswipe,cat_SIT_TAILSWIPE.kfa,40,15,15,loop\n"
        "4044,cat sit idle,cat_SIT_IDLE.kfa,40,15,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    for f in ("cat_SIT_TAILSWIPE.kfa", "cat_SIT_IDLE.kfa"):
        (tmp_path / "anims" / f).write_bytes(b"kfa")
    got = resolve_anim_from_gamedata(str(tmp_path), str(tmp_path / "figures" / "cat.nif"))
    assert got is not None and got.endswith("cat_SIT_TAILSWIPE.kfa")


def test_rotation_keys_support_type4_xyz_channels():
    """Beetle/shark/snake idles store rotations as 3 Euler float tracks."""
    from camelot.anims import _channel_rotation_keys
    from camelot.kfa import Channel, Key

    ch = Channel(
        index=1,
        xyz_rotations=(
            (Key(0.0, 0.5), Key(1.0, 0.5)),  # X
            (Key(0.0, 0.0), Key(1.0, 0.5)),  # Y
            (Key(0.0, 0.0), Key(1.0, 0.0)),  # Z
        ),
    )
    keys = _channel_rotation_keys(ch)
    times = [t for t, _ in keys]
    assert times[0] == 0.0 and times[-1] == 1.0
    # densely resampled (>= 30 Hz) so the viewer's slerp follows the
    # engine's per-axis interpolation instead of cutting straight between keys
    assert len(times) > 5 and times == sorted(times)
    for _t, q in keys:
        assert len(q) == 4 and abs(sum(c * c for c in q) - 1.0) < 1e-9
    assert _channel_rotation_keys(Channel(index=2)) == []


def test_native_fps_counts_type4_axes():
    from types import SimpleNamespace

    from camelot.anims import native_fps

    xyz = SimpleNamespace(
        rotations=(),
        xyz_rotations=(
            tuple(SimpleNamespace(time=i / 30) for i in range(47)),
            (SimpleNamespace(time=0.0),),
            (SimpleNamespace(time=0.0),),
        ),
    )
    assert native_fps(SimpleNamespace(channels={0: xyz})) == 30.0


def test_native_fps_snaps_to_authoring_rates():
    from types import SimpleNamespace

    def channel(times):
        return SimpleNamespace(rotations=[SimpleNamespace(time=t) for t in times])

    anim15 = SimpleNamespace(channels={0: channel([i / 15 for i in range(17)])})
    assert native_fps(anim15) == 15.0
    anim30 = SimpleNamespace(channels={0: channel([i / 30 for i in range(47)])})
    assert native_fps(anim30) == 30.0
    # sparse easing keys resolve to the base rate, not the mean density
    anim_sparse = SimpleNamespace(
        channels={0: channel([0.0, 0.0667, 0.3333, 0.6, 0.8, 0.8667, 0.9333, 1.0])}
    )
    assert native_fps(anim_sparse) == 15.0
    assert native_fps(SimpleNamespace(channels={})) == 15.0


def test_load_bone_table_parses_crlf_and_skips_blanks(tmp_path):
    from camelot.anims import load_bone_table

    (tmp_path / "animnode.dat").write_bytes(b"Bip01\r\nBip01 Pelvis\r\n\r\nBip01 Spine\r\n")
    assert load_bone_table(str(tmp_path)) == ["Bip01", "Bip01 Pelvis", "Bip01 Spine"]


def test_load_bone_table_absent(tmp_path):
    from camelot.anims import load_bone_table

    assert load_bone_table(str(tmp_path)) is None


def test_table_fills_rotation_only_channels_and_refuses_bad_pairs():
    """The canonical table only fills gaps of pairs the offsets vouch for."""
    from types import SimpleNamespace

    from camelot.gltf import kfa_translation_trust, map_kfa_to_nodes
    from camelot.kfa import Animation, Channel, Key

    node = type("NiNode", (SimpleNamespace,), {})

    def make(objs, names, pose):
        return map_kfa_to_nodes(objs, names, pose, bone_names=["Bip01", "Bip01 Pelvis"])

    # pair with one exact anchor (ch0) and a rotation-only ch1 (Ext-style)
    objs = [node(name=b"Bip01", translation=(0.0, 0.0, 36.0), rotation=None, children=[]),
            node(name=b"Bip01 Pelvis", translation=(0.0, 0.0, 0.0), rotation=None, children=[])]
    names = ["Bip01", "Bip01 Pelvis"]
    pose = Animation(
        path="x", version=0, duration=1.0,
        channels={
            0: Channel(index=0, translations=(Key(0.0, (0.0, 0.0, 36.0)),)),
            1: Channel(index=1, rotations=(Key(0.0, (1.0, 0.0, 0.0, 0.0)),)),
        },
    )
    assert make(objs, names, pose) == {0: 0, 1: 1}
    assert kfa_translation_trust(pose, objs, {0: 0}) == {0}

    # pair whose translation does not match any bind: refused (bind pose)
    pose_bad = Animation(
        path="x", version=0, duration=1.0,
        channels={
            0: Channel(index=0, translations=(Key(0.0, (99.0, 0.0, 0.0)),)),
            1: Channel(index=1, rotations=(Key(0.0, (1.0, 0.0, 0.0, 0.0)),)),
        },
    )
    assert make(objs, names, pose_bad) == {}


def test_anim_timing_from_csv_rows(tmp_path):

    from camelot.anims import anim_timing
    from camelot.mpak import write_mpak

    csv_text = (
        "NIFS,name,NIF Name,frames,fps, fps,type\n"
        "320,Boar Idle,boar_idle.kfa,16,3,15,loop\n"
        "322,Boar Run,boar_run.kfa,15,30,30,loop\n"
    )
    write_mpak(
        str(tmp_path / "gamedata.mpk"), "gamedata", [("animnifs.csv", csv_text.encode("latin1"))]
    )
    assert anim_timing(str(tmp_path), "anims/BOAR_idle.kfa") == (3.0, 16.0)
    assert anim_timing(str(tmp_path), "anims/boar_run.kfa") == (30.0, 15.0)
    assert anim_timing(str(tmp_path), "anims/nope.kfa") is None


def test_boar_timing_from_client(client_dir):

    from camelot.anims import anim_timing
    from camelot.kfa import load_kfa

    kfa = anim_timing(client_dir, "anims/BOAR_idle.kfa")
    assert kfa is not None
    fps, frames = kfa
    pose = load_kfa(f"{client_dir}/anims/BOAR_idle.kfa")
    rate = fps / native_fps(pose)
    assert rate == 0.2
    assert frames / fps == 16 / 3


def test_anim_link_matches_monnifs_name_without_separators(tmp_path):
    """FrostGiant in monnifs, Frost_Giant.NIF on disk: same skeleton."""
    from camelot.anims import resolve_anim_from_gamedata

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim\n"
        "ID,Textual Name,Name,Set\n"
        "135,Frost Giant,FrostGiant,9\n",
        ",,0,1\n"
        "ID,name,Idle\n"
        "9,Frost Giant,100\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "100,frost giant idle,FrostGiant_Idle.kfa,40,15,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    (tmp_path / "anims" / "FrostGiant_idle.kfa").write_bytes(b"kfa")
    got = resolve_anim_from_gamedata(str(tmp_path), str(tmp_path / "figures" / "Frost_Giant.NIF"))
    assert got is not None and got.endswith("FrostGiant_idle.kfa")


def test_anim_link_matches_orphan_set_name_with_typo(tmp_path):
    """Boar_Keggers.nif has no monnifs row; the `Boar Keggars` set names it."""
    from camelot.anims import resolve_anim_from_gamedata

    _write_gamedata(
        tmp_path,
        "Information,,NIF,Anim\n"
        "ID,Textual Name,Name,Set\n",
        ",,0,1\n"
        "ID,name,Idle,Idle2\n"
        "425,Boar Keggars,100,101\n",
        "Anim,,,,\n"
        "NIFS,name,NIF Name,frames,fps,fps,type\n"
        "100,boar keggers idle,Boar_CATA_IDLE.kfa,15,15,15,loop\n"
        "101,other,Other_idle.kfa,15,15,15,loop\n",
    )
    (tmp_path / "anims").mkdir()
    (tmp_path / "anims" / "Boar_CATA_IDLE.kfa").write_bytes(b"kfa")
    got = resolve_anim_from_gamedata(str(tmp_path), str(tmp_path / "figures" / "Boar_Keggers.nif"))
    assert got is not None and got.endswith("Boar_CATA_IDLE.kfa")


def test_type4_xyz_idles_animate(client_dir):
    """Beetle (a classic mob) stores its idle as Euler tracks; it must play."""
    import os

    import pytest

    from camelot.anims import animation_tracks
    from camelot.kfa import load_kfa

    for nif, kfa, minimum in (("Beetle.NIF", "Beetle_idle.kfa", 10),):
        path = os.path.join(client_dir, "figures", nif)
        kfa_path = os.path.join(client_dir, "anims", kfa)
        if not os.path.exists(path) or not os.path.exists(kfa_path):
            pytest.skip(f"{nif} fixtures missing")
        pose = load_kfa(kfa_path)
        assert any(ch.xyz_rotations for ch in pose.channels.values()), nif
        tracks = animation_tracks(path, kfa_path, client_dir)
        assert len(tracks["tracks"]) >= minimum, nif
        if nif == "Beetle.NIF":
            # The root has translation keys only; its grounding track must be
            # served, else the body stays pinned while the legs animate (the
            # "feet floating" regression).
            root = next(t for t in tracks["tracks"] if t["node"] == "Bip01")
            assert "quats" not in root
            assert len(root["ptimes"]) == len(root["positions"]) == 3


def test_normalised_and_set_name_links_animate_from_client(client_dir):
    """Names the tables spell differently, and orphan models named by their set.

    Frost_Giant/Svartalf come from monnifs under another spelling;
    Boar_Keggers (Keggars), Stingray, wolverine and the minecarts are linked
    by their animation-set name. Boar_CATA has no such link and stays bind.
    """
    import os

    from camelot.anims import animation_tracks, resolve_anim_from_gamedata

    expected = {
        "Frost_Giant.NIF": "FrostGiant_idle.kfa",
        "Svartalf_M.NIF": "I_HM.kfa",
        "Stingray.NIF": "stingray_idle.kfa",
        "wolverine.NIF": "wolverine_IDLE.kfa",
        "Boar_Keggers.nif": "Boar_CATA_IDLE.kfa",
        "minecart_a.nif": "kobold_minecart_i.kfa",
    }
    for nif, want in expected.items():
        path = os.path.join(client_dir, "figures", nif)
        if not os.path.exists(path):
            continue
        got = resolve_anim_from_gamedata(client_dir, path)
        assert got is not None, nif
        assert os.path.basename(got).lower() == want.lower(), nif
        assert len(animation_tracks(path, got, client_dir)["tracks"]) >= 5, nif
    # no set/name link: still refused (offset subset not vetted)
    cata = os.path.join(client_dir, "figures", "Boar_CATA.NIF")
    assert resolve_anim_from_gamedata(client_dir, cata) is None


def test_gamedata_links_creature_animations_from_client(client_dir):
    """The client's own chain links figures whose KFAs are named differently."""
    import os

    expected = {
        "AfancB.NIF": "afanc_idle.kfa",
        "arachite01.nif": "sect_idle.kfa",
        "amb_bat.nif": "amb_bat_fly.kfa",
        "amb_bird.nif": "amb_bird_fly.kfa",
        "b_avmon01.nif": "undead_idle.kfa",
        "amazon.nif": "hoplite_idle.kfa",
        "2HWolf.NIF": "2hw_idle.kfa",
        "7legspider.nif": "cata_spider_idle.kfa",
    }
    for nif, want in expected.items():
        got = resolve_idle_kfa(client_dir, os.path.join(client_dir, "figures", nif))
        assert got is not None, nif
        assert os.path.basename(got).lower() == want, nif
