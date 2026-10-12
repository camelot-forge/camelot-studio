"""The world API: zones as JSON, meshes as glTF, textures as PNG.

Unit tests run against the synthetic client `test_zones.py` builds (shared
through its `world` fixture helpers); the corpus tests at the end talk to the
real client when `DAOC_CLIENT` is set, and are skipped otherwise.
"""
import json
import os
import struct

import pytest
from fastapi.testclient import TestClient
from test_zones import (  # the synthetic client, one definition
    SECTOR,
    TERRAIN,
    _pcx,
    world,  # noqa: F401  (pytest fixture)
)

from camelot.dxt import encode_a8r8g8b8
from camelot.serve.app import create_app
from camelot.serve.config import ServeConfig
from camelot.serve.world import (
    TERRAIN_HEADER,
    TERRAIN_MAGIC,
    TERRAIN_VERSION,
    World,
    terrain_binary,
    terraintex_path,
)
from camelot.zones import WorldIndex

TERRAIN_TEXTURES = (
    "patch x,patch y,base texture filename,rotate,u translate,v translate,"
    "u scale,v scale,visible,num tiles,tileable\r\n"
    "0,0,LightGrass2,0.00, 0.00, 0.00,1.00,1.00,1,256,1\r\n"
    "0,0,CamHills-Small,0.00, 0.00, 0.00,1.00,1.00,1,1,0\r\n"
    "\r\n"
    "1,0,LightGrass2,0.00, 0.00, 0.00,1.00,1.00,1,256,1\r\n"
).encode("latin-1")

#: A zone-0-shaped texture test: only the textures.csv is added, so the
#: payload's patch model can be asserted without game data. The mask is a
#: real (tiny) DDS so the texture route has something to decode.
MASK_DDS = encode_a8r8g8b8(1, 1, bytes([10, 20, 30, 255]))


@pytest.fixture
def textured(world):  # noqa: F811  (the fixture from test_zones)
    """The synthetic client, plus a `ter` archive on zone 0."""
    from camelot.mpak import write_mpak

    write_mpak(
        os.path.join(world, "zones", "zone000", "ter000.mpk"), "ter000.mpk",
        [("textures.csv", TERRAIN_TEXTURES), ("patch0000-00.dds", MASK_DDS),
         ("bad.dds", b"not a dds at all")],
    )
    return world


def client_for(root) -> TestClient:
    return TestClient(
        create_app(ServeConfig(root=root, watch=False, open_browser=False)),
        base_url="http://127.0.0.1",
    )


# --- the index ----------------------------------------------------------------

def test_the_index_lists_zones_and_groups_them_by_region(world):  # noqa: F811
    response = client_for(world).get("/api/v1/world/index")
    assert response.status_code == 200
    payload = response.json()
    assert [zone["id"] for zone in payload["zones"]] == [0, 1, 2, 3, 4]
    cotswold = payload["zones"][0]
    assert cotswold["name"] == "Cotswold"
    assert cotswold["kind"] == "normal"
    assert cotswold["world_rect"] == [16384, 24576, 65536, 65536]
    assert cotswold["available"] is True
    assert payload["zones"][4]["available"] is False  # zone004 ships no folder
    regions = {region["id"]: region for region in payload["regions"]}
    assert regions[1]["zones"] == [0, 1]
    assert regions[1]["available"] == 2
    assert regions[1]["settings"] == {"entry_music": "2"}


def test_a_root_that_is_not_a_client_answers_503(tmp_path):
    response = client_for(tmp_path).get("/api/v1/world/index")
    assert response.status_code == 503
    assert "zones.mpk" in response.json()["detail"]


# --- one zone -----------------------------------------------------------------

def test_the_zone_payload_carries_what_the_editor_needs(world):  # noqa: F811
    response = client_for(world).get("/api/v1/world/zone/0")
    assert response.status_code == 200
    zone = response.json()
    assert zone["info"]["name"] == "Cotswold"
    assert zone["terrain"]["width"] == 4
    assert zone["terrain"]["terrain_factor"] == 1
    assert zone["terrain"]["url"].endswith("/world/zone/0/terrain")
    assert [fixture["name"] for fixture in zone["fixtures"]] == ["Farm House", "Pine"]
    assert zone["fixtures"][1]["ground"] is True
    assert [entry["filename"] for entry in zone["catalog"]] == [
        "bfarmhouse.nif", "bpinea.nif"]
    assert [entry["mesh"] for entry in zone["catalog"]] == [False, False]
    assert zone["bounds"][0]["points"] == [[100, 200], [300, 400], [500, 600]]
    assert zone["water"][0]["name"] == "faerie pool"
    assert zone["water"][0]["left"] == [[174 * 256, 256 * 256], [179 * 256, 249 * 256]]
    assert zone["jumps"][0]["name"] == "bridge"
    assert zone["zonejumps"][0][1] == "CamCity"
    assert sorted(zone["archives"]) == ["dat"]
    assert zone["archives"]["dat"]["entries"][0] == "terrain.pcx"
    assert zone["warnings"] == []


def test_the_zone_payload_exposes_the_ground_layer_model(textured):
    zone = client_for(textured).get("/api/v1/world/zone/0").json()
    patches = zone["patches"]
    assert patches["masks"] == {"0,0": ["patch0000-00.dds"]}
    assert patches["textures"] == ["CamHills-Small", "LightGrass2"]
    assert patches["available"] == []  # no zones/TerrainTex in the synthetic client
    first = patches["layers"][0]
    assert first["patch_x"] == 0 and first["patch_y"] == 0
    assert first["texture"] == "LightGrass2"
    assert first["tiles"] == 256 and first["tileable"] is True
    assert patches["layers"][1]["texture"] == "CamHills-Small"
    assert patches["layers"][1]["tileable"] is False
    assert len(patches["layers"]) == 3


def test_a_dungeon_zone_without_a_csv_part_still_answers(world):  # noqa: F811
    """zone002 is a dungeon: no fixtures/bounds — the payload warns, no 500."""
    response = client_for(world).get("/api/v1/world/zone/2")
    assert response.status_code == 200
    zone = response.json()
    assert zone["info"]["kind"] == "dungeon"
    assert zone["fixtures"] == []
    assert any(warning.startswith("fixtures:") for warning in zone["warnings"])


def test_an_unshipped_zone_is_a_404(world):  # noqa: F811
    response = client_for(world).get("/api/v1/world/zone/4")
    assert response.status_code == 404
    assert "not shipped" in response.json()["detail"]
    assert client_for(world).get("/api/v1/world/zone/999").status_code == 404


def test_a_placeholder_zone_reports_a_warning_not_an_error(tmp_path):
    """A zone whose fixtures.csv is empty (the retail 'Dummy Zone') must
    answer with empty lists and a warning, never a 500."""
    from camelot.mpak import write_mpak

    root = tmp_path / "client"
    (root / "zones" / "zone000").mkdir(parents=True)
    index = (b"[zone000]\r\nname=Dummy Zone\r\nregion=1\r\n"
             b"region_offset_x=0\r\nregion_offset_y=0\r\nwidth=8\r\nheight=8\r\n"
             b"type=0\r\nenabled=1\r\n")
    write_mpak(str(root / "zones" / "zones.mpk"), "zones.mpk",
               [("zones.dat", index)])
    write_mpak(str(root / "zones" / "zone000" / "dat000.mpk"), "dat000.mpk", [
        ("terrain.pcx", _pcx(TERRAIN)),
        ("offset.pcx", _pcx(TERRAIN)),
        ("sector.dat", SECTOR),
        ("fixtures.csv", b"\r\n"),
    ])
    response = client_for(root).get("/api/v1/world/zone/0")
    assert response.status_code == 200
    zone = response.json()
    assert zone["fixtures"] == []
    assert any("fixtures" in warning for warning in zone["warnings"])


# --- the server's rows (read-only, spec §11 B1) -------------------------------

def spawns_client(root, **config_kwargs) -> TestClient:
    return TestClient(
        create_app(ServeConfig(root=root, watch=False, open_browser=False, **config_kwargs)),
        base_url="http://127.0.0.1",
    )


def test_a_zone_without_server_rows_says_so_and_still_answers(world):  # noqa: F811
    """The DB layer is optional by design (spec §5): with no snapshot and no
    live config the payload names the flag to start with, and the client's
    world still renders as before."""
    response = spawns_client(world).get("/api/v1/world/zone/0/spawns")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "none"
    assert payload["counts"] == {"mob": 0, "worldobject": 0, "door": 0,
                                 "path": 0, "pathpoints": 0}
    assert any("--server-rows" in warning for warning in payload["warnings"])


def test_the_spawns_route_reads_a_snapshot_in_zone_local_coordinates(tmp_path, world):  # noqa: F811
    """Cotswold's rectangle is (16384, 24576) + 65536: a row at world
    (16500, 24800) is local (116, 224) — the same space as the fixtures."""
    snapshot = tmp_path / "rows.json"
    snapshot.write_text(json.dumps({
        "schema": "camelot.server-rows/1",
        "source": {"kind": "mysql", "host": "localhost", "database": "opendaoc"},
        "rows": {
            "mob": [
                {"Mob_ID": "m1", "Name": "a boar", "X": 16500, "Y": 24800,
                 "Z": 100, "Region": 1, "Level": 3, "Model": 111, "Size": 40,
                 "Heading": 2048, "PathID": "FarmPatrol"},
                {"Mob_ID": "m2", "Name": "elsewhere", "X": 1, "Y": 1, "Z": 0},
                # another region's row, in the same rectangle (its world
                # coordinates are per region): never this zone's
                {"Mob_ID": "m3", "Name": "region 165 boar", "X": 16600,
                 "Y": 24900, "Z": 5000, "Region": 165},
            ],
            "worldobject": [
                {"WorldObject_ID": "o1", "Name": "banner", "X": 16400,
                 "Y": 24576, "Z": 0, "Region": 1, "Model": 1315, "Heading": 0},
                {"WorldObject_ID": "o2", "Name": "region 165 camp", "X": 16600,
                 "Y": 24900, "Z": 4900, "Region": 165},
            ],
            "door": [
                {"Door_ID": "d1", "Name": "gate", "Type": 0, "X": 17000,
                 "Y": 25000, "Z": 50, "Heading": 1024, "InternalID": 40123},
                {"Door_ID": "d2", "Name": "Midgard Mile Gate", "Type": 1,
                 "X": 16500, "Y": 24900, "Z": 4256, "Heading": 1013,
                 "InternalID": 165102401},
            ],
            "path": [{"Path_ID": "p1", "PathID": "FarmPatrol", "PathType": 3}],
            "pathpoints": [
                {"PathPoints_ID": "pp1", "PathID": "FarmPatrol", "Step": 1,
                 "X": 16000, "Y": 24000, "Z": 10, "MaxSpeed": 150, "WaitTime": 0},
                {"PathPoints_ID": "pp2", "PathID": "FarmPatrol", "Step": 2,
                 "X": 17000, "Y": 25000, "Z": 10, "MaxSpeed": 150, "WaitTime": 300},
            ],
        },
    }))
    payload = spawns_client(world, server_rows=snapshot).get(
        "/api/v1/world/zone/0/spawns").json()
    assert payload["source"].startswith("snapshot ")
    assert payload["counts"] == {"mob": 1, "worldobject": 1, "door": 1,
                                 "path": 1, "pathpoints": 2}
    mob = payload["mobs"][0]
    assert (mob["name"], mob["x"], mob["y"], mob["z"]) == ("a boar", 116, 224, 100)
    assert mob["path"] == "FarmPatrol"
    assert [row["name"] for row in payload["mobs"]] == ["a boar"]  # no region 165 row
    assert payload["objects"][0]["y"] == 0
    assert [row["name"] for row in payload["objects"]] == ["banner"]
    assert [door["name"] for door in payload["doors"]] == ["gate"]  # not the mile gate
    assert payload["doors"][0]["x"] == 616  # 17000 - 16384
    path = payload["paths"][0]
    assert path["name"] == "FarmPatrol" and path["path_type"] == 3
    # step 1 lies outside the rectangle (16000 < 16384): the patrol is kept
    # whole rather than clipped at a guessed border
    assert [point["step"] for point in path["points"]] == [1, 2]
    assert path["points"][0]["x"] == -384
    assert path["points"][1]["wait"] == 300


def test_a_snapshot_from_a_future_schema_warns_instead_of_failing(tmp_path, world):  # noqa: F811
    snapshot = tmp_path / "rows.json"
    snapshot.write_text(json.dumps({"schema": "camelot.server-rows/99", "rows": {}}))
    response = spawns_client(world, server_rows=snapshot).get("/api/v1/world/zone/0/spawns")
    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["mob"] == 0
    assert any("camelot.server-rows/99" in warning for warning in payload["warnings"])


def test_the_live_path_asks_for_the_zone_rectangle(monkeypatch, tmp_path, world):  # noqa: F811
    """`--db-config` reads the operator's MySQL through the server's own
    config; the reader is injected, so the test runs without a database and
    checks the rectangle it hands over is the zone's."""
    from camelot import serverrows

    config = tmp_path / "serverconfig.xml"
    config.write_text(
        "<DBConnectionString>Server=localhost;Port=3307;Database=opendaoc;"
        "UserId=root;Password=x</DBConnectionString>")
    seen = {}

    def fake(connect_, *, region, zone_id, x, y, width, height):
        seen["rect"] = (x, y, width, height)
        seen["region"] = region
        seen["zone"] = zone_id
        return {"mob": [{"Mob_ID": "m1", "Name": "a boar", "X": x + 10,
                         "Y": y + 20, "Z": 0}],
                "worldobject": [], "door": [], "path": [], "pathpoints": []}

    monkeypatch.setattr(serverrows, "read_rows_in_rect", fake)
    payload = spawns_client(world, db_config=config).get(
        "/api/v1/world/zone/0/spawns").json()
    assert seen["rect"] == (16384, 24576, 65536, 65536)
    assert (seen["region"], seen["zone"]) == (1, 0)
    assert payload["source"].startswith("mysql ")
    assert payload["mobs"][0]["x"] == 10


# --- the terrain binary -------------------------------------------------------

def test_the_terrain_binary_round_trips(world):  # noqa: F811
    config = ServeConfig(root=world, watch=False, open_browser=False)
    zone = World(config).zone(0)
    payload = terrain_binary(zone)
    magic, version, width, height, terrain_factor, offset_factor = (
        TERRAIN_HEADER.unpack_from(payload)
    )
    assert magic == TERRAIN_MAGIC
    assert version == TERRAIN_VERSION
    assert (width, height) == (4, 4)
    assert (terrain_factor, offset_factor) == (1, 0)
    planes = payload[TERRAIN_HEADER.size:]
    assert len(planes) == width * height * 2
    assert planes[: width * height] == b"".join(TERRAIN)
    assert planes[width * height :] == bytes(width * height)

    response = client_for(world).get("/api/v1/world/zone/0/terrain")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.content == payload


# --- textures as PNG ----------------------------------------------------------

def test_a_zone_texture_that_is_not_dds_is_refused(textured):
    client = client_for(textured)
    assert client.get(
        "/api/v1/world/zone/0/texture/dat/terrain.pcx").status_code == 404
    bad = client.get("/api/v1/world/zone/0/texture/ter/bad.dds")
    assert bad.status_code == 404
    assert "not a DDS" in bad.json()["detail"]
    assert client.get(
        "/api/v1/world/zone/0/texture/nope/x.dds").status_code == 404
    assert client.get("/api/v1/world/terraintex/LightGrass2.dds").status_code == 404


def test_the_patch_mask_converts_like_any_zone_texture(textured):
    response = client_for(textured).get(
        "/api/v1/world/zone/0/texture/ter/patch0000-00.dds")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert struct.unpack(">II", response.content[16:24]) == (1, 1)


def test_terraintex_resolves_the_shared_sheets_by_case(tmp_path):
    """The client's `zones/TerrainTex` holds the sheets the ground layers
    name; resolution must match the on-disk case (Linux)."""
    from camelot import clientfs

    root = tmp_path / "client"
    sheet_dir = root / "zones" / "TerrainTex"
    sheet_dir.mkdir(parents=True)
    (sheet_dir / "LightGrass2.dds").write_bytes(b"DDS ")
    config = ServeConfig(root=root, watch=False, open_browser=False)
    world_service = World(config)
    assert terraintex_path(world_service, "LightGrass2.dds") == str(
        clientfs.resolve(str(root), "zones/TerrainTex/LightGrass2.dds"))
    assert terraintex_path(world_service, "lightgrass2.dds") is not None
    assert terraintex_path(world_service, "../zones/zones.mpk") is None
    assert terraintex_path(world_service, "LightGrass2.tga") is None


def test_a_real_dds_entry_is_served_as_png(textured):
    """A decodable DDS converts and caches; the second call hits the cache."""
    from camelot.mpak import write_mpak

    dds = encode_a8r8g8b8(2, 1, bytes([255, 0, 0, 255, 0, 255, 0, 128]))
    write_mpak(
        os.path.join(textured, "zones", "zone000", "tex000.mpk"), "tex000.mpk",
        [("tex00-00.dds", dds)],
    )
    client = client_for(textured)
    first = client.get("/api/v1/world/zone/0/texture/tex/tex00-00.dds")
    assert first.status_code == 200
    assert first.headers["content-type"] == "image/png"
    assert first.content.startswith(b"\x89PNG\r\n\x1a\n")
    # RGBA, 2x1: the scanline holds the two pixels (red opaque, green half).
    assert struct.unpack(">II", first.content[16:24]) == (2, 1)
    second = client.get("/api/v1/world/zone/0/texture/tex/tex00-00.dds")
    assert second.content == first.content


# --- the corpus -----------------------------------------------------------------

CORPUS = os.environ.get("DAOC_CLIENT")
corpus = pytest.mark.skipif(not CORPUS, reason="set DAOC_CLIENT to run")


@corpus
def test_the_corpus_index_and_a_zone_read_the_real_client():
    client = client_for(CORPUS)
    index = client.get("/api/v1/world/index").json()
    assert len(index["zones"]) > 400
    assert len(index["regions"]) > 300
    zones = {zone["id"]: zone for zone in index["zones"]}
    assert zones[0]["name"] == "Camelot Hills"
    assert zones[0]["available"] is True
    # A frontiers zone (the same `zoneNNN` layout under another root).
    assert zones[163]["available"] is True

    zone = client.get("/api/v1/world/zone/0").json()
    assert len(zone["fixtures"]) == 1536
    assert len(zone["catalog"]) == 54
    assert zone["warnings"] == []
    keep = next(fixture for fixture in zone["fixtures"] if fixture["name"] == "Keep")
    assert (keep["x"], keep["y"], keep["z"]) == (25344.0, 47104.0, 2880.0)
    assert zone["bounds"] and zone["water"] and zone["jumps"]
    assert len(zone["patches"]["layers"]) > 300
    assert len(zone["patches"]["masks"]) == 64
    assert "LightGrass2" in zone["patches"]["available"]
    with_mesh = [entry for entry in zone["catalog"] if entry["mesh"]]
    assert len(with_mesh) >= 45


@corpus
def test_the_corpus_terrain_and_a_mesh_and_a_texture():
    client = client_for(CORPUS)
    terrain = client.get("/api/v1/world/zone/0/terrain")
    assert terrain.status_code == 200
    magic, version, width, height, terrain_factor, offset_factor = TERRAIN_HEADER.unpack_from(
        terrain.content)
    assert (magic, version) == (TERRAIN_MAGIC, TERRAIN_VERSION)
    assert (width, height) == (256, 256)
    assert (terrain_factor, offset_factor) == (8, 32)
    assert len(terrain.content) == TERRAIN_HEADER.size + width * height * 2

    mesh = client.get("/api/v1/world/zone/0/mesh/bpinea.nif")
    assert mesh.status_code == 200
    assert mesh.headers["content-type"] == "model/gltf-binary"
    assert mesh.content[:4] == b"glTF"
    # A name the client does not ship: a 404, not a 500.
    assert client.get("/api/v1/world/zone/0/mesh/nope.nif").status_code == 404

    tex = client.get("/api/v1/world/zone/0/texture/tex/tex00-00.dds")
    assert tex.status_code == 200
    assert tex.content.startswith(b"\x89PNG")
    sheet = client.get("/api/v1/world/terraintex/LightGrass2.dds")
    assert sheet.status_code == 200
    assert sheet.content.startswith(b"\x89PNG")


@corpus
def test_the_corpus_mesh_carries_one_sheet_per_shape():
    """A world object's meshes each carry their own sheet (the NIF's
    `NiTexturingProperty`), not one texture for the whole model: the guard
    tower names three sheets."""
    import json

    client = client_for(CORPUS)
    response = client.get("/api/v1/world/zone/0/mesh/b-rndtw2.nif")
    assert response.status_code == 200
    gltf = json.loads(response.content[20 : 20 + int.from_bytes(
        response.content[12:16], "little")])
    names = set()
    for mesh in gltf.get("meshes", []):
        ext = mesh.get("extensions", {}).get("CS_camelot_texture")
        if ext:
            names.add(ext["name"].lower())
    assert {"britlog.dds", "brtwd01.dds", "b_stone2.dds"} <= names

    # The pine's sheet is alpha-cut art: the mesh must say so (the viewer
    # alpha-tests it) and stay a single sheet.
    pine = client.get("/api/v1/world/zone/0/mesh/bpinea.nif")
    pine_gltf = json.loads(pine.content[20 : 20 + int.from_bytes(
        pine.content[12:16], "little")])
    sheets = [
        mesh["extensions"]["CS_camelot_texture"]
        for mesh in pine_gltf.get("meshes", [])
        if "extensions" in mesh
    ]
    assert sheets and all(sheet.get("alpha") for sheet in sheets)


@corpus
def test_the_corpus_mesh_drops_the_far_lod_and_collision_subtrees():
    """The client draws one `NiLODNode` level and no collision hull; a viewer
    that draws them all paints a second, coarser copy over every object.

    The guard tower's near level names its parts `roof`/`tower-exterior` and
    the far level `roof01`/`tower-exterior01` (measured); the tavern keeps
    its visible meshes and loses every `collision *`/`collidee` one."""
    import json

    client = client_for(CORPUS)

    def glb_meshes(zone_id: int, name: str) -> list[str]:
        response = client.get(f"/api/v1/world/zone/{zone_id}/mesh/{name}")
        assert response.status_code == 200
        gltf = json.loads(response.content[20 : 20 + int.from_bytes(
            response.content[12:16], "little")])
        return [mesh.get("name", "") for mesh in gltf.get("meshes", [])]

    tower = glb_meshes(0, "b-rndtw2.nif")
    assert len(tower) == 5          # the near level's five parts (the file
                                    # ships 18: 9 collision/climb + 5 far)
    tavern = glb_meshes(0, "btavern.nif")
    assert tavern and not any("far" in m.lower() for m in tavern)
    assert not any("collision" in m.lower() or "collidee" in m.lower() for m in tavern)
    assert any(m.lower().startswith("btavern2intnear") for m in tavern)


@corpus
def test_the_corpus_zone_163_reads_a_frontiers_zone():
    """Agramon lives under `frontiers/zones/` only — the folder roots must
    find it, and its payload must parse like any other zone's."""
    client = client_for(CORPUS)
    zone = client.get("/api/v1/world/zone/163").json()
    assert zone["info"]["name"] == "Agramon"
    assert zone["info"]["region"] == 163 or zone["info"]["region"] > 0
    assert zone["fixtures"]
    assert zone["terrain"]["width"] == 256


@corpus
def test_the_corpus_world_index_agrees_with_the_client_index():
    index = WorldIndex(CORPUS)
    payload = client_for(CORPUS).get("/api/v1/world/index").json()
    assert len(payload["zones"]) == len(index.zones)
    assert {zone["id"] for zone in payload["zones"]} == set(index.zones)
