"""Corpus tests for KFA posing: mapping + baked idle (no stretch)."""

import os

import pytest
from _corpus import figure_path

from camelot.gltf import export_gltf, map_kfa_to_nodes
from camelot.kfa import load_kfa
from camelot.nifutil import load


def _names_objs(nif_path):
    data = load(nif_path)
    objs, names = [], []
    for root in data.roots:
        stack = [root]
        while stack:
            obj = stack.pop()
            objs.append(obj)
            nm = ""
            if hasattr(obj, "name") and obj.name:
                nm = obj.name.decode("latin1")
            names.append(nm or type(obj).__name__)
            stack.extend(reversed(list(getattr(obj, "children", []) or [])))
    return objs, names


def _bone_table(client_dir):
    from camelot.anims import load_bone_table

    return load_bone_table(os.path.join(client_dir, "figures"))


def test_boar_kfa_mapping(client_dir):
    nif = os.path.join(client_dir, "figures", "Boar.NIF")
    kfa = os.path.join(client_dir, "anims", "BOAR_idle.kfa")
    objs, names = _names_objs(nif)
    pose = load_kfa(kfa)
    mapping = map_kfa_to_nodes(objs, names, pose)
    # 27+ channels resolve (31 with translations, minus R/L tie duplicates
    # that greedily share a node); the root must resolve exactly.
    assert len(mapping) >= 25
    rev = {v: k for k, v in mapping.items()}
    bip01 = next(i for i, n in enumerate(names) if n == "Bip01")
    assert rev[bip01] == 0
    # With the client's animnode.dat table the offset-verified pair also gains
    # the rotation-only channels (Ext helpers) offset matching cannot bind.
    from camelot.gltf import kfa_translation_trust

    full = map_kfa_to_nodes(objs, names, pose, bone_names=_bone_table(client_dir))
    assert len(full) == len(pose.channels)
    assert "Bip01 Ext01" in {names[v] for v in full.values()}
    trusted = kfa_translation_trust(pose, objs, full)
    assert len(trusted) == 31  # every translated channel belongs to this rig


def test_briton_player_figure_pairs_with_race_idle(client_dir):
    """The table unlocks player figures: BritonM1 <- britm_idle.kfa."""
    from camelot.anims import resolve_idle_kfa

    alias = resolve_idle_kfa(client_dir, os.path.join(client_dir, "figures", "BritonM1.NIF"))
    assert alias is not None and alias.endswith("britm_idle.kfa")
    nif = os.path.join(client_dir, "figures", "BritonM1.NIF")
    objs, names = _names_objs(nif)
    mapping = map_kfa_to_nodes(
        objs, names, load_kfa(alias), bone_names=_bone_table(client_dir)
    )
    assert len(mapping) >= 60
    by_name = {names[v] for v in mapping.values()}
    for want in ("Bip01 Pelvis", "Bip01 Head", "Bip01 L Thigh", "Bip01 R UpperArm"):
        assert want in by_name


def test_engine_skinning_rescues_stored_bind_rigs(client_dir):
    """AfancB/dracolich store a skin bind 300+ units away from their node tree.

    The engine-exact formula (Mb = inv(meshWorld)·boneAnim·stored S, glTF
    inverseBind = S·inv(meshWorld)) deforms them cleanly: they now animate
    instead of being refused by the stretch gate.
    """
    from camelot.anims import animation_tracks
    from camelot.gltf import STRETCH_LIMIT_MEAN, skin_stretch

    for name, kfa in (
        ("AfancB.NIF", "Afanc_idle.kfa"),
        ("dracolich.nif", "dracolich_idle.kfa"),
        ("Afanc.NIF", "Afanc_idle.kfa"),
        ("Boar.NIF", "BOAR_idle.kfa"),
        ("Zombie.NIF", "Zombie_idle.kfa"),
    ):
        nif = os.path.join(client_dir, "figures", name)
        pose = load_kfa(os.path.join(client_dir, "anims", kfa))
        stretch = skin_stretch(load(nif), pose, bone_names=_bone_table(client_dir))
        assert stretch is not None
        assert stretch[0] <= STRETCH_LIMIT_MEAN, name
        tracks = animation_tracks(nif, os.path.join(client_dir, "anims", kfa), client_dir)
        assert len(tracks["tracks"]) >= 20, name


def test_stretch_gate_still_refuses_broken_rigs(client_dir):
    """The gate is not decoration: BHalfOgreF + TROLLF_IDLE over-stretches.

    (Most player races now pass once mapped through the engine table and its
    translations — BInconnuM_2 1.10, NKoboldF 1.03 — but this pairing stays
    at ~1.49 and must keep the bind pose.)
    """
    from camelot.anims import animation_tracks
    from camelot.gltf import STRETCH_LIMIT_MEAN, skin_stretch

    nif = os.path.join(client_dir, "figures", "BHalfOgreF.NIF")
    kfa = os.path.join(client_dir, "anims", "TROLLF_IDLE.kfa")
    if not os.path.exists(nif) or not os.path.exists(kfa):
        pytest.skip("fixtures missing")
    stretch = skin_stretch(
        load(nif), load_kfa(kfa), bone_names=_bone_table(client_dir), prefer_table=True
    )
    assert stretch is not None
    assert stretch[0] > STRETCH_LIMIT_MEAN
    assert animation_tracks(nif, kfa, client_dir)["tracks"] == []


def test_gamedata_linked_figures_animate_despite_offset_drift(client_dir):
    """The client's link is decisive: amazon/rat/bear/cata_lion/badger/blackcat.

    Their KFAs carry animated bones whose offsets drift from the bind (root
    bob, shoulder weights), which used to fail the offset heuristics; mapped
    through the engine's animnode.dat table they deform cleanly (skin
    stretch 1.01-1.07) and stay grounded.
    """
    from camelot.anims import animation_tracks

    expected = {
        "amazon.nif": "Hoplite_Idle.kfa",
        "Rat.NIF": "rat_idle.kfa",
        "Bear.NIF": "Bear_idle.kfa",
        "cata_lion.nif": "lynx_idle.kfa",
        "amb_blackcat.nif": "amb_blackcat_stand_idle.kfa",
        "badger.nif": "rat_idle.kfa",
    }
    for nif, kfa in expected.items():
        tracks = animation_tracks(
            os.path.join(client_dir, "figures", nif),
            os.path.join(client_dir, "anims", kfa),
            client_dir,
        )
        assert len(tracks["tracks"]) >= 15, nif


def test_wolf_idle_is_refused_on_rig_mismatch(client_dir):
    """wolf_idle.kfa's offsets belong to another bind: bind pose, no flailing."""
    nif = os.path.join(client_dir, "figures", "Wolf.NIF")
    kfa = os.path.join(client_dir, "anims", "wolf_idle.kfa")
    objs, names = _names_objs(nif)
    pose = load_kfa(kfa)
    assert map_kfa_to_nodes(objs, names, pose, bone_names=_bone_table(client_dir)) == {}


def test_cata_table_does_not_override_the_quality_gate(client_dir):
    """The CATA idle's shifted arm/tail slots stay on bind even with the table."""
    nif = os.path.join(client_dir, "figures", "Boar_CATA.NIF")
    kfa = os.path.join(client_dir, "anims", "Boar_CATA_IDLE.kfa")
    objs, names = _names_objs(nif)
    pose = load_kfa(kfa)
    mapping = map_kfa_to_nodes(objs, names, pose, bone_names=_bone_table(client_dir))
    by_name = {names[v]: k for k, v in mapping.items()}
    assert "Bip01 Pelvis" in by_name
    for kfa_idx in (22, 23, 24, 36, 37, 60):
        assert kfa_idx not in mapping


def test_zombie_kfa_mapping_never_touches_scene_root(client_dir):
    """Zombie.NIF's Scene Root sits at (0,0,0) like its Pelvis.

    The canonical table has Pelvis=1 and no Scene Root; a root mapping here
    pitched the whole figure over (it claimed channel 1 by node order).
    """
    nif = os.path.join(client_dir, "figures", "Zombie.NIF")
    kfa = os.path.join(client_dir, "anims", "Zombie_idle.kfa")
    objs, names = _names_objs(nif)
    pose = load_kfa(kfa)
    for usable in (
        map_kfa_to_nodes(objs, names, pose),
        map_kfa_to_nodes(objs, names, pose, bone_names=_bone_table(client_dir)),
    ):
        assert "Scene Root" not in {names[v] for v in usable.values()}
        rev = {v: k for k, v in usable.items()}
        pelvis = next(i for i, n in enumerate(names) if n == "Bip01 Pelvis")
        assert rev.get(pelvis) == 1  # canonical Pelvis index
    full = map_kfa_to_nodes(objs, names, pose, bone_names=_bone_table(client_dir))
    assert len(full) >= 20
    rev = {v: k for k, v in full.items()}
    bip01 = next(i for i, n in enumerate(names) if n == "Bip01")
    assert rev.get(bip01) == 0  # table also names the root channel


def test_boar_anim_endpoint(client_dir, tmp_path):
    """Serve resolves BOAR_idle for Boar.NIF and returns joint rotation tracks."""
    import math
    import shutil

    from fastapi.testclient import TestClient

    from camelot.serve import create_app
    from camelot.serve.config import ServeConfig

    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True)
    (root / "anims").mkdir()
    shutil.copy(os.path.join(client_dir, "figures", "Boar.NIF"), root / "figures" / "Boar.NIF")
    shutil.copy(
        os.path.join(client_dir, "anims", "BOAR_idle.kfa"), root / "anims" / "BOAR_idle.kfa"
    )
    app = create_app(ServeConfig(root=root, cache_dir=tmp_path / "cache", static_dir=None))
    client = TestClient(app, base_url="http://localhost")
    nif_id = client.get("/api/v1/assets", params={"type": "nif"}).json()["items"][0]["id"]
    r = client.get(f"/api/v1/assets/{nif_id}/anim")
    assert r.status_code == 200
    body = r.json()
    assert body["kfa"] == "BOAR_idle.kfa"
    # no gamedata.mpk in this synthetic root: stamped file timing fallback
    assert body["duration"] == 1.0666667222976685
    assert body["rate"] == 1.0
    assert len(body["tracks"]) >= 25
    by_node = {t["node"]: t for t in body["tracks"]}
    assert "Bip01" in by_node
    for t in body["tracks"]:
        if "times" in t:  # translation-only tracks (e.g. Beetle's root) have none
            assert len(t["times"]) == len(t["quats"]) >= 1
        for q in t["quats"]:
            assert math.isclose(sum(c * c for c in q), 1.0, abs_tol=1e-3)
    r = client.get(f"/api/v1/assets/{nif_id}/model", params={"skinned": True})
    assert r.status_code == 200
    assert r.content[:4] == b"glTF"


def test_boar_anim_csv_timing(client_dir, tmp_path):
    import shutil

    from fastapi.testclient import TestClient

    from camelot.mpak import write_mpak
    from camelot.serve import create_app
    from camelot.serve.config import ServeConfig

    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True)
    (root / "anims").mkdir()
    shutil.copy(os.path.join(client_dir, "figures", "Boar.NIF"), root / "figures" / "Boar.NIF")
    shutil.copy(os.path.join(client_dir, "anims", "BOAR_idle.kfa"), root / "anims" / "BOAR_idle.kfa")
    write_mpak(
        str(root / "gamedata.mpk"),
        "gamedata",
        [("animnifs.csv", b"NIFS,name,NIF Name,frames,fps, fps,type\n320,Boar Idle,boar_idle.kfa,16,3,15,loop\n")],
    )
    app = create_app(ServeConfig(root=root, cache_dir=tmp_path / "cache", static_dir=None))
    client = TestClient(app, base_url="http://localhost")
    nif_id = client.get("/api/v1/assets", params={"type": "nif"}).json()["items"][0]["id"]
    body = client.get(f"/api/v1/assets/{nif_id}/anim").json()
    assert body["duration"] == 16 / 3
    assert body["rate"] == 0.2


def test_asset_state_endpoint_serves_the_clients_own_clip(client_dir, tmp_path):
    """A figure's anim set is served per slot, not only as the auto-resolved
    idle: the boar's walk comes back with its own tracks and its own
    `loop`/`clamp` flag, and an unnamed state still falls back to the idle.

    The set also carries player emotes the boar's rig cannot bind: those are
    refused with the state's name, not with an idle's."""
    import shutil

    from fastapi.testclient import TestClient

    from camelot.mpak import write_mpak
    from camelot.serve import create_app
    from camelot.serve.config import ServeConfig

    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True)
    (root / "anims").mkdir()
    shutil.copy(os.path.join(client_dir, "figures", "Boar.NIF"), root / "figures" / "Boar.NIF")
    for name in ("BOAR_idle.kfa", "BOAR_walk.kfa", "EM_RVRDANCE.kfa"):
        shutil.copy(os.path.join(client_dir, "anims", name), root / "anims" / name)
    write_mpak(
        str(root / "gamedata.mpk"),
        "gamedata",
        [
            (
                "monnifs.csv",
                b"Information,,NIF,Anim\nID,Textual Name,Name,Anim Set ID\n"
                b"1,Boar,BOAR,10\n",
            ),
            (
                "anims.csv",
                b"Anim,,,,,\nAnim Set ID,Name,Idle,Walk,Death,EM Dance\n"
                b"10,Boar,320,321,323,46\n",
            ),
            (
                "animnifs.csv",
                b"Anim,,,,,base, \nNIFS,name,NIF Name,frames,fps, fps,type\n"
                b"320,Boar Idle,boar_idle.kfa,16,3,15,loop\n"
                b"321,Boar Walk,boar_walk.kfa,32,3,15,clamp\n"
                b"323,Boar Death,boar_death.kfa,12,3,15,clamp\n"
                b"46,Emote Dance,em_rvrdance.kfa,40,15,15,clamp\n",
            ),
        ],
    )
    client = TestClient(
        create_app(ServeConfig(root=root, cache_dir=tmp_path / "cache", static_dir=None)),
        base_url="http://localhost",
    )
    nif_id = client.get("/api/v1/assets", params={"type": "nif"}).json()["items"][0]["id"]

    states = client.get(f"/api/v1/assets/{nif_id}/states").json()
    assert [s["slot"] for s in states] == ["Idle", "Walk", "EM Dance"]
    walk = states[1]
    assert walk["loop"] is False  # `clamp`: a one-shot, the viewer must not wrap it
    assert walk["duration"] == pytest.approx(32 / 3)

    clip = client.get(f"/api/v1/assets/{nif_id}/anim", params={"state": "Walk"}).json()
    assert clip["kfa"].lower() == "boar_walk.kfa"
    assert clip["loop"] is False
    assert clip["duration"] == pytest.approx(32 / 3)
    assert len(clip["tracks"]) >= 25, "a named state carries its own tracks"

    # The boar's rig binds none of `EM_RVRDANCE`'s channels (measured on the
    # corpus: 64 of its 96 slots are refused this way). The refusal names the
    # state, so the UI can say *which* pair was refused.
    refused = client.get(f"/api/v1/assets/{nif_id}/anim", params={"state": "EM Dance"})
    assert refused.status_code == 404
    assert refused.json()["detail"] == "no tracks for state: EM Dance"

    # no state asked for: the resolver's idle, unchanged
    plain = client.get(f"/api/v1/assets/{nif_id}/anim").json()
    assert plain["kfa"].lower() == "boar_idle.kfa"
    assert "loop" not in plain


def test_boar_baked_idle_no_stretch(client_dir, tmp_path):
    from _bbox import world_bbox

    nif = os.path.join(client_dir, "figures", "Boar.NIF")
    kfa = os.path.join(client_dir, "anims", "BOAR_idle.kfa")
    p0, p1 = str(tmp_path / "bind"), str(tmp_path / "idle")
    export_gltf(nif, p0, verbose=False, texture=None, textures=[])
    pose = load_kfa(kfa)
    export_gltf(
        nif, p1, verbose=False, texture=None, textures=[], pose=pose, pose_time=0.5, bake_pose=True
    )
    d0, _ = world_bbox(p0 + ".gltf", p0 + ".bin")
    d1, _ = world_bbox(p1 + ".gltf", p1 + ".bin")
    # Same creature, same order of magnitude: idle breathes, it must neither
    # explode (56-unit bind error) nor stretch triangles (edge blowup).
    for a, b in zip(d0, d1):
        assert b == pytest.approx(a, rel=0.25)


def _glb_min_y(prefix: str) -> float:
    import json
    from pathlib import Path

    import numpy as np

    gltf = json.loads(Path(prefix + ".gltf").read_text())
    blob = Path(prefix + ".bin").read_bytes()
    nodes = gltf["nodes"]

    def world(i, pw):
        n = nodes[i]
        m = np.eye(4)
        if "matrix" in n:
            m = np.array(n["matrix"], dtype=float).reshape(4, 4, order="F")
        w = pw @ m
        for c in n.get("children", []):
            yield from world(c, w)
        if "mesh" in n:
            yield w

    worlds = []
    for s in gltf["scenes"][0]["nodes"]:
        worlds.extend(world(s, np.eye(4)))
    prim = gltf["meshes"][0]["primitives"][0]
    acc = gltf["accessors"][prim["attributes"]["POSITION"]]
    bv = gltf["bufferViews"][acc["bufferView"]]
    pos = np.frombuffer(
        blob[bv.get("byteOffset", 0) :][: acc["count"] * 12], dtype=np.float32
    ).reshape(-1, 3)
    return min((w @ np.append(p, 1))[1] for w in worlds for p in pos)


def test_rabbit_skips_rig_helpers(client_dir, tmp_path):
    """Hidden-flagged meshes (Max biped helpers, export scraps) must not render."""
    from camelot.gltf import _is_skipped_mesh, export_gltf

    assert _is_skipped_mesh("Biped Object", 181)
    assert _is_skipped_mesh("Bone", 181)
    assert _is_skipped_mesh("collidee wall", 181)
    assert not _is_skipped_mesh("Editable Poly", 180)
    assert not _is_skipped_mesh("body", 22)
    assert not _is_skipped_mesh("body", None)
    nif = figure_path(client_dir, "amb_rabbit.NIF")
    stats = export_gltf(nif, str(tmp_path / "rabbit"), verbose=False, texture=None, textures=[])
    assert stats["meshes"] == 1
    assert stats["skipped"] == 37


def test_boar_baked_idle_stays_grounded(client_dir, tmp_path):
    """KFA translations plant the feet: min-Y matches the bind all loop long.

    (Regresses the rotations-only era, where the body floated +2.26.)
    """
    nif = os.path.join(client_dir, "figures", "Boar.NIF")
    kfa = os.path.join(client_dir, "anims", "BOAR_idle.kfa")
    p0 = str(tmp_path / "bind")
    export_gltf(nif, p0, verbose=False, texture=None, textures=[])
    bind_min = _glb_min_y(p0)
    pose = load_kfa(kfa)
    for t in (0.0, 0.267, 0.533, 0.8, 1.0):
        p = str(tmp_path / f"pose{int(t * 1000)}")
        export_gltf(
            nif, p, verbose=False, texture=None, textures=[],
            pose=pose, pose_time=t, bake_pose=True,
        )
        assert _glb_min_y(p) == pytest.approx(bind_min, abs=0.5)


def test_cata_mapping_is_clean_subset(client_dir):
    """Remodel drift must not flail: spine/legs map exactly, reshuffled arm
    slots stay on bind (runs without anchors map nothing)."""
    from camelot.gltf import map_kfa_to_nodes
    from camelot.kfa import load_kfa

    d = load(os.path.join(client_dir, "figures", "Boar_CATA.NIF"))
    objs, names = [], []
    for root in d.roots:
        stack = [root]
        while stack:
            obj = stack.pop()
            objs.append(obj)
            names.append(obj.name.decode("latin1") if getattr(obj, "name", None) else type(obj).__name__)
            stack.extend(reversed(list(getattr(obj, "children", []) or [])))
    pose = load_kfa(os.path.join(client_dir, "anims", "Boar_CATA_IDLE.kfa"))
    mapping = map_kfa_to_nodes(objs, names, pose)
    by_name = {names[v]: k for k, v in mapping.items()}
    # spine + legs resolve (same authoring as the figure)
    for want in ("Bip01 Pelvis", "Bip01 Spine", "Bip01 L Thigh", "Bip01 R Foot", "Bip01 Tail2"):
        assert want in by_name
    # reshuffled arm/finger slots must NOT map (would flail the wrong bones)
    for kfa_idx in (22, 23, 24, 26, 35, 36, 37, 39, 85):
        assert kfa_idx not in mapping
