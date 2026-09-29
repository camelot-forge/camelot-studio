"""Contract tests for the server-facing content manifest (Phase 2).

The manifest is the server-neutral contract: logical ids, appearance and
animation slots, gamedata keys, and the provenance needed to know which
client build produced it. It must describe the content without embedding a
single proprietary byte, and it must refuse to publish an unresolved
reference rather than guessing one.
"""

import pytest

from camelot import pack


def test_manifest_declares_its_schema_and_provenance():
    m = pack.Manifest(pack_id="daoc.studio", version="0.1.0")
    doc = m.to_dict()
    assert doc["schema_version"] == pack.SCHEMA_VERSION
    assert doc["pack"]["id"] == "daoc.studio"
    assert doc["pack"]["version"] == "0.1.0"
    # a manifest without provenance is not publishable
    assert "validation" in doc


def test_manifest_is_deterministic():
    """Same input, same bytes: a pack that reorders keys on every run cannot
    be checksummed or diffed."""
    a = pack.Manifest(pack_id="x", version="1").to_bytes()
    b = pack.Manifest(pack_id="x", version="1").to_bytes()
    assert a == b


def test_logical_ids_are_namespaced():
    fig = pack.Figure(
        logical_id="daoc.player.briton.female",
        figure_id=2,
        kind="player",
        rig="sar_f",
    )
    assert fig.logical_id.startswith("daoc.")
    pack.validate_logical_id(fig.logical_id)
    with pytest.raises(ValueError):
        pack.validate_logical_id("BritonFemale")


def test_appearance_slot_records_carry_engine_tints_and_sheets():
    slot = pack.AppearanceSlot(
        slot="Body",
        piece="Body01_SFS_f",
        sheet="b_lea1_body1f.tga",
        tint=(255, 255, 255),
        gamedata_key="pskins:301:Body:f",
    )
    doc = slot.to_dict()
    assert doc["slot"] == "Body"
    assert doc["gamedata_key"] == "pskins:301:Body:f"
    assert doc["tint"] == [255, 255, 255]


def test_anim_state_records_the_loop_flag_and_game_timing():
    state = pack.AnimStateRef(
        slot="Walk",
        kfa="b_plt1_walk.kfa",
        animnifs_id="1234",
        loop=True,
        duration=1.07,
        rate=1.0,
    )
    doc = state.to_dict()
    assert doc["loop"] is True
    assert doc["duration"] == pytest.approx(1.07)
    # the id is the stable handle; the filename is not
    assert doc["animnifs_id"] == "1234"


def test_validation_refuses_an_unresolved_reference():
    m = pack.Manifest(pack_id="x", version="1")
    m.figures.append(
        pack.Figure(logical_id="daoc.player.troll.male", figure_id=31, kind="player")
    )
    m.unresolved.append(pack.Unresolved(
        ref="daoc.player.troll.male:rig",
        reason="no fig3 skeleton resolved for tro_m",
    ))
    with pytest.raises(pack.UnresolvedReferenceError) as exc:
        m.validate()
    assert "tro_m" in str(exc.value)


def test_validation_reports_unresolved_without_raising_for_dry_runs():
    m = pack.Manifest(pack_id="x", version="1")
    m.unresolved.append(pack.Unresolved(ref="a:b", reason="missing"))
    report = m.validate(strict=False)
    assert report.ok is False
    assert report.unresolved[0].ref == "a:b"


def test_validation_passes_on_a_consistent_manifest():
    m = pack.Manifest(pack_id="x", version="1")
    m.figures.append(pack.Figure(
        logical_id="daoc.player.briton.female", figure_id=2, kind="player", rig="sar_f",
    ))
    report = m.validate()
    assert report.ok
    assert report.unresolved == []


def test_manifest_never_carries_asset_bytes():
    """The contract describes content; it does not ship proprietary data."""
    m = pack.Manifest(pack_id="x", version="1")
    m.figures.append(pack.Figure(
        logical_id="daoc.player.briton.female", figure_id=2, kind="player",
        rig="sar_f", archive_sha256="a" * 64,
    ))
    raw = m.to_bytes().decode("utf-8")
    assert "base64" not in raw
    assert len(raw) < 4096


def test_pack_hashes_its_artifacts_deterministically():
    files = {"a.json": b"{}", "b.json": b"[]"}
    first = pack.artifact_hashes(files)
    second = pack.artifact_hashes(dict(reversed(list(files.items()))))
    assert first == second
    assert set(first) == {"a.json", "b.json"}
    assert len(first["a.json"]) == 64


# --- building a pack from the real client ----------------------------------

@pytest.mark.usefixtures("client_dir")
def test_build_pack_describes_every_playable_race(client_dir):
    """The canonical pack must cover the 18 playable races x 2 genders, each
    with a rig, appearance slots and its anim set — that is the surface a
    server needs to create a character."""
    from camelot import pack

    manifest = pack.build_player_pack(client_dir, pack_id="daoc.studio", version="0.1.0")
    assert len(manifest.figures) == 36
    for figure in manifest.figures:
        assert figure.kind == "player"
        assert figure.rig, figure.logical_id
        assert figure.appearance, figure.logical_id
        assert {s.slot for s in figure.appearance} >= {
            "Head", "Eyes", "Hair", "Body", "Arms", "Gloves", "Legs", "Boots",
        }
        assert figure.anim_states, figure.logical_id
        assert any(s.slot == "Idle" for s in figure.anim_states), figure.logical_id


@pytest.mark.usefixtures("client_dir")
def test_build_pack_is_reproducible(client_dir):
    """A pack is a build artifact: rebuilding it from the same client must
    produce identical bytes, or its checksums are meaningless."""
    from camelot import pack

    a = pack.build_player_pack(client_dir, pack_id="p", version="1")
    b = pack.build_player_pack(client_dir, pack_id="p", version="1")
    assert a.to_bytes() == b.to_bytes()


@pytest.mark.usefixtures("client_dir")
def test_build_pack_records_the_client_build(client_dir):
    """A pack is only valid for the build it was generated against, so the
    manifest carries the corpus's own `game.dll` FileVersion rather than
    leaving the consumer to assume one."""
    from camelot import pack

    manifest = pack.build_player_pack(client_dir, pack_id="p", version="1")
    assert pack.client_build(client_dir)
    assert manifest.client_build == pack.client_build(client_dir)
    assert manifest.to_dict()["client"]["build"] == manifest.client_build
    assert manifest.client_profile.endswith(manifest.client_build)


def test_client_build_is_none_without_a_game_dll(tmp_path):
    from camelot import pack

    assert pack.client_build(str(tmp_path)) is None
    (tmp_path / "game.dll").write_bytes(b"not a PE")
    assert pack.client_build(str(tmp_path)) is None


@pytest.mark.usefixtures("client_dir")
def test_build_pack_validates_against_the_real_client(client_dir):
    """The real V2 corpus must produce a publishable pack: any unresolved
    reference here is a genuine gap, not a test artefact."""
    from camelot import pack

    manifest = pack.build_player_pack(client_dir, pack_id="p", version="1")
    report = manifest.validate(strict=False)
    assert report.ok, report.errors + [u.ref for u in report.unresolved]


@pytest.mark.usefixtures("client_dir")
def test_manifest_is_small_and_carries_no_client_bytes(client_dir):
    from camelot import pack

    manifest = pack.build_player_pack(client_dir, pack_id="p", version="1")
    raw = manifest.to_bytes()
    # 5958 anim states over 36 figures stay a ~1.2 MB text contract; the
    # glTF and sheets live in the artifacts beside it, not inlined.
    assert len(raw) < 2_000_000
    # sheet *names* belong in the contract, sheet *bytes* never do
    text = raw.decode("utf-8")
    assert "base64" not in text
    assert "b_lea1_body1f.tga" in text  # the default material's body sheet


@pytest.mark.usefixtures("client_dir")
def test_pack_records_which_anim_set_each_state_came_from(client_dir):
    """An anim set is not a clean per-figure namespace: the Cata Troll male
    (set 263) borrows cat clips for slots like `Back`. A target adapter needs
    the set id to reason about the rig a clip was authored on."""
    from camelot import pack

    manifest = pack.build_player_pack(client_dir, pack_id="p", version="1")
    troll = next(f for f in manifest.figures if f.logical_id == "daoc.player.tro_m")
    assert troll.anim_sets
    assert {s.anim_set for s in troll.anim_states} == {troll.anim_sets[0]}
    assert all(s.anim_set for s in troll.anim_states)
    back = next(s for s in troll.anim_states if s.slot == "Back")
    assert back.kfa.lower().startswith("cat_"), back.kfa


@pytest.mark.usefixtures("client_dir")
def test_a_null_tint_means_white_not_a_missing_one(client_dir):
    """Skin tone 1 and hair colour 1 carry no tint at all — the client
    paints them white — and the contract must say "no tint", not invent one
    or leave the field ambiguous with a dropped sheet."""
    from camelot import appearance as app
    from camelot import pack

    assert app.skin_tint_rgb(client_dir, 2, 1) is None
    manifest = pack.build_player_pack(client_dir, pack_id="p", version="1")
    briton = next(f for f in manifest.figures if f.logical_id == "daoc.player.bri_f")
    head = next(s for s in briton.appearance if s.slot == "Head")
    assert head.tint is None
    assert head.sheet, "a null tint must not cost the sheet"


@pytest.mark.usefixtures("client_dir")
def test_pack_exposes_a_tinted_variant_of_the_same_slots(client_dir):
    """A server replaying a chosen skin tone needs the RGB the client
    multiplies by, so the pack must be buildable per appearance pick rather
    than only at the defaults."""
    from camelot import pack

    manifest = pack.build_player_pack(
        client_dir, pack_id="p", version="1", material="Plate",
    )
    briton = next(f for f in manifest.figures if f.logical_id == "daoc.player.bri_f")
    body = next(s for s in briton.appearance if s.slot == "Body")
    assert body.sheet == "b_plt1_body1f.tga"
    assert body.gamedata_key == "pskins:Plate:Body"
