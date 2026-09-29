"""Unit and corpus tests for idle-KFA resolution and track building."""

import os

import pytest

from camelot.anims import native_fps, resolve_idle_kfa


def test_native_troll_male_uses_cata_idle(client_dir):
    from camelot.anims import resolve_anim_from_gamedata

    figure = os.path.join(client_dir, "figures", "tro_m.NIF")
    linked = resolve_anim_from_gamedata(client_dir, figure)
    assert linked is not None
    assert os.path.basename(linked).lower() == "cat_troll_idle.kfa"
    assert resolve_idle_kfa(client_dir, figure) == linked


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


def test_foreign_rig_clip_drops_its_absolute_translations():
    """A vetted pair whose KFA was authored on another rig must not apply its
    absolute locals: the Cata Troll male idle on `tro_m_skeleton.nif` shifts
    the neck/head ~3.5 units and shears the face."""
    from types import SimpleNamespace

    from camelot.gltf import kfa_translation_trust
    from camelot.kfa import Animation, Channel, Key

    node = type("NiNode", (SimpleNamespace,), {})

    def rig():
        return [
            node(name=b"Bip01", translation=(0.0, 0.0, 37.0), rotation=None, children=[]),
            node(name=b"Bip01 Neck", translation=(7.5, 3.3, 0.0), rotation=None, children=[]),
            node(name=b"Bip01 Head", translation=(2.0, 0.0, 0.0), rotation=None, children=[]),
            node(name=b"Bip01 L Hand", translation=(10.0, 0.0, 0.0), rotation=None, children=[]),
        ]

    mapping = {0: 0, 1: 1, 2: 2, 3: 3}
    # authored on the target rig: every translated channel returns to the bind
    same = Animation(
        path="x", version=0, duration=1.0,
        channels={
            0: Channel(index=0, translations=(Key(0.0, (0.0, 0.0, 37.0)),)),
            1: Channel(index=1, translations=(Key(0.0, (7.5, 3.3, 0.0)),)),
            2: Channel(index=2, translations=(Key(0.0, (2.0, 0.0, 0.0)),)),
            3: Channel(index=3, translations=(Key(0.0, (10.0, 0.0, 0.0)),)),
        },
    )
    assert kfa_translation_trust(same, rig(), mapping, source="table") == {0, 1, 2, 3}
    assert (
        kfa_translation_trust(same, rig(), mapping, source="table", foreign_rig_gate=True)
        == {0, 1, 2, 3}
    )

    # authored on another rig: the vetted pair falls back to the per-channel
    # bind test, which refuses every off-rig channel
    foreign = Animation(
        path="x", version=0, duration=1.0,
        channels={
            0: Channel(index=0, translations=(Key(0.0, (0.2, 0.4, 36.9)),)),
            1: Channel(index=1, translations=(Key(0.0, (8.8, 0.0, 0.0)),)),
            2: Channel(index=2, translations=(Key(0.0, (3.8, 0.0, 0.0)),)),
            3: Channel(index=3, translations=(Key(0.0, (13.0, 0.0, 0.0)),)),
        },
    )
    assert kfa_translation_trust(foreign, rig(), mapping, source="table") == {0, 1, 2, 3}
    assert (
        kfa_translation_trust(foreign, rig(), mapping, source="table", foreign_rig_gate=True)
        == set()
    )


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


# --- animation states (animnifs slots, types and duplicates) ---------------

def _write_gamedata_rows(tmp_path, tables: dict[str, list[list[str]]]):
    from camelot.mpak import write_mpak

    def to_csv(rows: list[list[str]]) -> bytes:
        return ("\n".join(",".join(c) for c in rows) + "\n").encode("latin1")

    write_mpak(
        str(tmp_path / "gamedata.mpk"),
        "gamedata",
        [(f"{name}.csv", to_csv(rows)) for name, rows in tables.items()],
    )


def _touch_kfas(tmp_path, *names: str):
    """Placeholder KFA files: `anim_states` needs the clip to exist in
    `anims/`, but only its bytes decide `rate` (asserted in the corpus tier)."""
    anims = tmp_path / "anims"
    anims.mkdir(exist_ok=True)
    for name in names:
        (anims / name).write_bytes(b"kfa")


def test_anim_states_expose_the_named_slots(tmp_path):
    """A figure's playable states are the named slots of its anim set, not one
    auto-picked idle: the client's `anims.csv` header names them (Idle,
    C-Idle, Walk, Run, Fly…)."""
    from camelot.anims import anim_states

    _write_gamedata_rows(tmp_path, {
        "anims": [
            ["Anim", "", "", "", "", "", ""],
            ["Anim Set ID", "Name", "Idle", "C-Idle", "Walk", "Run", "Fly"],
            ["10", "Saracen", "1", "2", "3", "4", "5"],
        ],
        "monnifs": [
            ["Information", "", "NIF", "Anim"],
            ["ID", "Textual Name", "Name", "Anim Set ID"],
            ["1", "Saracen Female", "sar_f", "10"],
        ],
        "animnifs": [
            ["Anim", "", "", "", "", "base", " "],
            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
            ["1", "a", "sar_f_idle.kfa", "16", "3", "15", "loop"],
            ["2", "b", "sar_f_cidle.kfa", "18", "15", "15", "loop"],
            ["3", "c", "sar_f_walk.kfa", "7", "15", "15", "loop"],
            ["4", "d", "sar_f_run.kfa", "16", "30", "30", "loop"],
            ["5", "e", "sar_f_fly.kfa", "20", "15", "15", "clamp"],
        ],
    })
    _touch_kfas(tmp_path, "sar_f_idle.kfa", "sar_f_cidle.kfa", "sar_f_walk.kfa",
                "sar_f_run.kfa", "sar_f_fly.kfa")
    states = anim_states(str(tmp_path), "figures/sar_f.NIF")
    assert [s.slot for s in states] == ["Idle", "C-Idle", "Walk", "Run", "Fly"]
    assert states[0].kfa == "sar_f_idle.kfa"
    assert states[0].loop is True
    assert states[0].duration == pytest.approx(16 / 3)
    # a clamp clip is a one-shot: the viewer must not wrap it
    assert states[4].loop is False


def test_anim_states_carry_the_game_timing_not_the_file_timing(tmp_path):
    from camelot.anims import anim_states

    _write_gamedata_rows(tmp_path, {
        "anims": [
            ["Anim", "", ""],
            ["Anim Set ID", "Name", "Idle"],
            ["10", "Boar", "1"],
        ],
        "monnifs": [
            ["Information", "", "NIF", "Anim"],
            ["ID", "Textual Name", "Name", "Anim Set ID"],
            ["1", "Boar", "boar", "10"],
        ],
        "animnifs": [
            ["Anim", "", "", "", "", "base", " "],
            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
            ["1", "a", "boar_idle.kfa", "16", "3", "15", "loop"],
        ],
    })
    _touch_kfas(tmp_path, "boar_idle.kfa")
    (idle,) = anim_states(str(tmp_path), "figures/BOAR.NIF")
    # the game's 16 frames at 3 fps, not the file's own key spacing
    assert idle.duration == pytest.approx(16 / 3)


def test_anim_states_prefer_the_exact_anims_row_over_a_near_match(tmp_path):
    """`anims.csv` reuses descriptions across anim sets; a set that only
    matches loosely must not shadow the figure's own row."""
    from camelot.anims import anim_states

    _write_gamedata_rows(tmp_path, {
        "anims": [
            ["Anim", "", ""],
            ["Anim Set ID", "Name", "Idle"],
            ["1", "some other creature", "1"],
            ["10", "boar", "2"],
        ],
        "monnifs": [
            ["Information", "", "NIF", "Anim"],
            ["ID", "Textual Name", "Name", "Anim Set ID"],
            ["1", "Boar", "boar", "10"],
        ],
        "animnifs": [
            ["Anim", "", "", "", "", "base", " "],
            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
            ["1", "a", "wrong_idle.kfa", "10", "15", "15", "loop"],
            ["2", "b", "boar_idle.kfa", "16", "3", "15", "loop"],
        ],
    })
    _touch_kfas(tmp_path, "wrong_idle.kfa", "boar_idle.kfa")
    (idle,) = anim_states(str(tmp_path), "figures/boar.NIF")
    assert idle.kfa == "boar_idle.kfa"
    assert idle.duration == pytest.approx(16 / 3)


def test_anim_timing_refuses_an_ambiguous_basename(tmp_path):
    """A KFA basename is not a unique key: 92 basenames carry several
    animnifs rows and 58 of them disagree on (type, frames, fps). Refusing
    beats silently taking the first row."""
    from camelot.anims import anim_timing

    _write_gamedata_rows(tmp_path, {
        "animnifs": [
            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
            ["1", "a", "ettin_swim.kfa", "10", "15", "15", "loop"],
            ["2", "b", "ettin_swim.kfa", "40", "30", "30", "clamp"],
            ["3", "c", "boar_idle.kfa", "16", "3", "15", "loop"],
        ],
    })
    assert anim_timing(str(tmp_path), "anims/boar_idle.kfa") == (3.0, 16.0)
    assert anim_timing(str(tmp_path), "anims/ettin_swim.kfa") is None


def test_anim_timing_still_answers_for_a_consistent_duplicate(tmp_path):
    from camelot.anims import anim_timing

    _write_gamedata_rows(tmp_path, {
        "animnifs": [
            ["NIFS", "name", "NIF Name", "frames", "fps", " fps", "type"],
            ["1", "a", "ettin_swim.kfa", "10", "15", "15", "loop"],
            ["2", "b", "ettin_swim.kfa", "10", "15", "15", "loop"],
        ],
    })
    # identical rows are not ambiguous
    assert anim_timing(str(tmp_path), "anims/ettin_swim.kfa") == (15.0, 10.0)


# --- corpus tier: the real anim sets ---------------------------------------

def test_anim_states_on_the_real_client_carry_the_game_rate(client_dir):
    """`rate` is the game's fps over the clip's own key density: 16 frames at
    3 fps over a 15 fps file is 0.2, which is why classic idles loop for
    5.33s while their keys span 1.07s."""
    from camelot.anims import anim_states
    from camelot.kfa import load_kfa

    states = {s.slot: s for s in anim_states(
        client_dir, os.path.join(client_dir, "figures", "BSaracenF.NIF")
    )}
    idle = states["Idle"]
    row = _row(client_dir, idle.animnifs_id)
    fps, frames = float(row[4]), float(row[3])
    pose = load_kfa(os.path.join(client_dir, "anims", idle.kfa))
    assert idle.rate(client_dir) == pytest.approx(fps / native_fps(pose))
    assert idle.duration == pytest.approx(frames / fps)
    assert idle.loop is (row[6].strip().lower() == "loop")


def _row(client_dir, animnifs_id):
    from camelot.anims import _cached_table

    for row in _cached_table(client_dir, "animnifs.csv")[2:]:
        if row and row[0].strip() == animnifs_id:
            return row
    raise AssertionError(f"no animnifs row {animnifs_id}")


def test_anim_states_agree_with_the_idle_resolver_on_the_client(client_dir):
    """The state list and the single-idle resolver must not disagree about
    which file is the idle: a server that picks states and a viewer that
    picks a default would otherwise show different clips."""
    from camelot.anims import anim_states, resolve_idle_kfa

    for figure in ("BSaracenF.NIF", "Boar.NIF", "Ettin.NIF"):
        path = os.path.join(client_dir, "figures", figure)
        states = anim_states(client_dir, path)
        if not states:
            continue
        idle = resolve_idle_kfa(client_dir, path)
        assert idle is not None, figure
        slots = {s.slot: s for s in states}
        assert os.path.basename(idle).lower() in {
            s.kfa.lower() for s in slots.values()
        }, f"{figure}: {os.path.basename(idle)} is not one of {sorted(s.kfa for s in slots.values())}"


def test_anim_states_cover_the_player_races_on_the_client(client_dir):
    from camelot.anims import anim_states

    total = 0
    for race in ("bri_m", "bri_f", "tro_m", "fro_f", "dwa_m", "val_f"):
        states = anim_states(client_dir, os.path.join(client_dir, "figures", race + ".NIF"))
        assert states, race
        assert any(s.slot.lower() == "idle" for s in states), race
        total += len(states)
    assert total > 6


def test_anim_states_do_not_open_every_clip(client_dir, monkeypatch):
    """Listing a figure's states must stay a table read: a player anim set
    carries 166 slots, and opening each KFA to compute `rate` turned the
    states route into a 22s request the browser gave up on."""
    from camelot import anims

    opened: list[str] = []
    real = anims.native_fps

    def spy(pose):
        opened.append(pose.path)
        return real(pose)

    monkeypatch.setattr(anims, "native_fps", spy)
    states = anims.anim_states(
        client_dir, os.path.join(client_dir, "figures", "BSaracenF.NIF")
    )
    assert len(states) > 10
    assert opened == [], f"anim_states opened {len(opened)} clips"


def test_anim_state_rate_is_computed_on_demand(client_dir):
    """`rate` needs the clip's key density, so it is resolved when a state is
    actually asked for, not while the list is built."""
    from camelot.anims import anim_states
    from camelot.kfa import load_kfa

    states = {s.slot: s for s in anim_states(
        client_dir, os.path.join(client_dir, "figures", "BSaracenF.NIF")
    )}
    idle = states["Idle"]
    row = _row(client_dir, idle.animnifs_id)
    assert idle.rate(client_dir) == pytest.approx(float(row[4]) / native_fps(
        load_kfa(os.path.join(client_dir, "anims", idle.kfa))
    ))
