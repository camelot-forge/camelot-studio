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
