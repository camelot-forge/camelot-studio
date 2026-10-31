"""The client's world: the zone index, the terrain, the placements.

Unit tests build a synthetic client (`zones.mpk` + four zone folders) so the
formats are exercised without game files; the corpus tests at the end run the
**calibration** — the client's own `fixtures.csv` rows against the height
model — which is Phase 6.0's exit criterion.
"""
import struct

import pytest

from camelot import zones
from camelot.mpak import write_mpak

#: A heightmap with one raised pixel: the subtile diagonal decides which
#: triangle a sample falls in, so (1.2, 1.2) reads 0 while (1.8, 1.8) reads
#: the raised corner — the client's own triangulation, not a guess.
TERRAIN = [
    bytes([0, 0, 0, 0]),
    bytes([0, 0, 0, 0]),
    bytes([0, 0, 5, 0]),
    bytes([0, 0, 0, 0]),
]
OFFSET = [bytes([0, 0, 0, 0])] * 4

SECTOR = (
    ";Sector Data for Test\r\n"
    "[sectorsize]\r\n"
    "sizex=8\r\n"
    "sizey=8\r\n"
    "\r\n"
    "[terrain]\r\n"
    "scalefactor=1\r\n"
    "offsetfactor=0\r\n"
    "\r\n"
    "[jumpdefs]\r\n"
    "numjumppoints=1\r\n"
    "\r\n"
    "[jump00]\r\n"
    "name=bridge\r\n"
    "x=1000\r\n"
    "y=2000\r\n"
    "z=-100\r\n"
    "a=90\r\n"
    "\r\n"
    "[waterdefs]\r\n"
    "num=1\r\n"
    "\r\n"
    "[river00]\r\n"
    "texture=watery\r\n"
    "flow=-50\r\n"
    "bankpoints=2\r\n"
    "height=1916\r\n"
    "name=faerie pool\r\n"
    "type=RIVER\r\n"
    "left00=174,256,0\r\n"
    "right00=192,256,0\r\n"
    "left01=179,249,0\r\n"
    "right01=199,250,0\r\n"
).encode("latin-1")

FIXTURES = (
    "Fixtures,, , ,,,,,NIF,Collide,,On.\r\n"
    "ID,NIF #,Textual Name,X,Y,Z,A,Scale,Collide,Radius,Animate,Ground,Flip,Cave, Unique Id\r\n"
    "1,10,Farm House,19660,19660,0,0,100,5120,0,0,1,0,0,1\r\n"
    "2,11,Pine,32768,32768,1234,2048,100,0,0,0,1,0,0,0,2\r\n"
).encode("latin-1")

NIFS = (
    "Grid Nifs,,,Ambient,Merlin Data\r\n"
    "NIF,Textual Name,Filename,Only,Shadow,Color,Animate,Collide,Ground,"
    "MinAngle,MaxAngle,MinScale,MaxScale,Radius,Unique, Local, Terrain,\r\n"
    "10,Farm House,bfarmhouse.nif,0,0,16711935,0,0,0,0,0,0,0,0,0,0,0,\r\n"
    "11,Pine,bpinea.nif,1,1,65535,0,0,1,0,360,45,100,8,0,0,0,\r\n"
).encode("latin-1")

BOUNDS = b"0, 3, 100, 200, 300, 400, 500, 600\r\n"
ZONEJUMP = b"1,CamCity,3200,28712,3053,28712,2912,3072,6,,\r\n"

INDEX = (
    "\ufeff;===============\r\n"
    ";Classic Client Zones File + Player Housing\r\n"
    ";===============\r\n"
    "\r\n"
    ";region1=albion terrain (zones 0 - 15)\r\n"
    "\r\n"
    "[region001]\r\n"
    "entry_music=2\r\n"
    "\r\n"
    "[zone000]\r\n"
    "enabled=1\r\n"
    "entry_music=2\r\n"
    "height=8\r\n"
    "name=Cotswold\r\n"
    "region=1\r\n"
    "region_offset_x=2\r\n"
    "region_offset_y=3\r\n"
    "type=0\r\n"
    "width=8\r\n"
    "\r\n"
    "[zone001]\r\n"
    "enabled=1\r\n"
    "height=8\r\n"
    "name=Nextdoor\r\n"
    "region=1\r\n"
    "region_offset_x=3\r\n"
    "region_offset_y=3\r\n"
    "width=8\r\n"
    "\r\n"
    "[zone002];Some Dungeon\r\n"
    "enabled=1\r\n"
    "height=8\r\n"
    "name=Some Dungeon\r\n"
    "region=20\r\n"
    "region_offset_x=1\r\n"
    "region_offset_y=1\r\n"
    "type=2\r\n"
    "width=8\r\n"
    "\r\n"
    "[zone003]\r\n"
    "enabled=1\r\n"
    "height=8\r\n"
    "name=Some City\r\n"
    "region=10\r\n"
    "region_offset_x=1\r\n"
    "region_offset_y=1\r\n"
    "type=1\r\n"
    "width=8\r\n"
    "\r\n"
    "[zone004]\r\n"
    "enabled=0\r\n"
    "height=8\r\n"
    "name=Housing\r\n"
    "region=2\r\n"
    "region_offset_x=1\r\n"
    "region_offset_y=1\r\n"
    "width=8\r\n"
).encode("utf-8")


def _pcx(rows) -> bytes:
    """An 8-bit PCX with literal-only rows (the values here stay under 0xC0)."""
    width, height = len(rows[0]), len(rows)
    header = bytearray(128)
    struct.pack_into("<BBBB", header, 0, 0x0A, 5, 1, 8)
    struct.pack_into("<HHHH", header, 4, 0, 0, width - 1, height - 1)
    header[65] = 1
    struct.pack_into("<H", header, 66, width)
    return bytes(header) + b"".join(rows)


@pytest.fixture
def world(tmp_path):
    """A synthetic client: the index, an outdoor zone, a dungeon, a city."""
    root = tmp_path / "client"
    (root / "zones" / "zone000").mkdir(parents=True)
    (root / "zones" / "zone001").mkdir()
    (root / "zones" / "zone002").mkdir()
    (root / "zones" / "zone003").mkdir()
    (root / "zones" / "zone003" / "nifs").mkdir()
    write_mpak(str(root / "zones" / "zones.mpk"), "zones.mpk",
               [("zones.dat", INDEX)])
    write_mpak(str(root / "zones" / "zone000" / "dat000.mpk"), "dat000.mpk", [
        ("terrain.pcx", _pcx(TERRAIN)),
        ("offset.pcx", _pcx(OFFSET)),
        ("sector.dat", SECTOR),
        ("fixtures.csv", FIXTURES),
        ("nifs.csv", NIFS),
        ("bound.csv", BOUNDS),
        ("zonejump.csv", ZONEJUMP),
    ])
    # zone001 ships only the csv archive, to exercise the fallback
    write_mpak(str(root / "zones" / "zone001" / "csv001.mpk"), "csv001.mpk", [
        ("terrain.pcx", _pcx(TERRAIN)),
        ("offset.pcx", _pcx(OFFSET)),
        ("sector.dat", SECTOR),
        ("fixtures.csv", FIXTURES),
    ])
    write_mpak(str(root / "zones" / "zone002" / "dat002.mpk"), "dat002.mpk", [
        ("dungeon.chunk", b"Room1.NIF\r\nHall1.NIF\r\n"),
        ("dungeon.place",
         (b"0, 0, 0, 0, 0, 1, 0, 0\r\n"
          b"1, 1536, 256, 192, 1.570796, 0, 0, 1\r\n"
          b"1, -384, 0, 0, 1.570796, 0, 0, -1\r\n"
          b"2, 512, 0, 0, 0, 1, 0, 0\r\n"
          b"1, x, 0, 0, 0, 1, 0, 0\r\n")),
        ("sector.dat", SECTOR),
    ])
    write_mpak(str(root / "zones" / "zone003" / "dat003.mpk"), "dat003.mpk", [
        ("city.csv", b"1,cam01.nif\r\n"),
        ("adjacency.csv", b"adjacency"),
    ])
    # the block header: `NHD`, a version, the name, the cell size, the cell box
    (root / "zones" / "zone003" / "nifs" / "cam01.nhd").write_bytes(
        b"NHD" + struct.pack("<HH", 1, 9) + b"cam01.npk"
        + struct.pack("<H", 64) + struct.pack("<4i", -21, 28, -21, 14)
        + b"\0" * 8)
    return str(root)


# --- the index ----------------------------------------------------------------

def test_the_index_reads_zones_and_regions(world):
    index = zones.WorldIndex(world)
    assert sorted(index.zones) == [0, 1, 2, 3, 4]
    assert sorted(index.regions) == [1]        # only sections the file defines
    cotswold = index.zone(0)
    assert cotswold.name == "Cotswold"
    assert cotswold.region == 1
    assert cotswold.kind == "normal"
    assert cotswold.enabled is True
    assert cotswold.music == 2
    assert cotswold.world_rect == (2 * 8192, 3 * 8192, 8 * 8192, 8 * 8192)
    assert index.zone(2).kind == "dungeon"
    assert index.zone(3).kind == "city"
    assert index.zone(4).kind == "unknown"
    assert index.zone(4).enabled is False
    assert index.zones_of(1) == [index.zone(0), index.zone(1)]
    assert index.regions[1]["entry_music"] == "2"


def test_zone_at_resolves_a_world_point(world):
    index = zones.WorldIndex(world)
    # zone000 covers x 16384..81919, zone001 the next block east
    assert index.zone_at(1, 16384, 24576).zone_id == 0
    assert index.zone_at(1, 81919, 90000).zone_id == 0
    assert index.zone_at(1, 81920, 24576).zone_id == 1
    assert index.zone_at(1, 0, 0) is None
    assert index.zone_at(99, 16384, 24576) is None


def test_an_unknown_zone_id_is_refused(world):
    with pytest.raises(zones.ZoneError, match="no zone 99"):
        zones.WorldIndex(world).zone(99)


def test_a_directory_that_is_not_a_client_is_refused(tmp_path):
    with pytest.raises(zones.ZoneError, match="zones/zones.mpk"):
        zones.WorldIndex(str(tmp_path))


# --- one zone -----------------------------------------------------------------

def test_a_listed_zone_the_client_does_not_ship_is_not_openable(world):
    zone = zones.WorldIndex(world).open(4)
    assert zone.available is False
    with pytest.raises(zones.ZoneError, match="no dat archive"):
        zone.entry("dat", "terrain.pcx")


def test_the_kind_falls_back_to_the_archives_when_the_index_has_none(world):
    # zone001 has no `type` in the index but ships terrain.pcx
    assert zones.WorldIndex(world).open(1).kind == "normal"
    assert zones.WorldIndex(world).open(2).kind == "dungeon"
    assert zones.WorldIndex(world).open(3).kind == "city"


def test_the_data_entry_falls_back_to_the_csv_archive(world):
    zone = zones.WorldIndex(world).open(1)
    assert zone.archive("dat") is None
    assert zone.data_entry("fixtures.csv") == FIXTURES


def test_the_data_entry_prefers_the_csv_archive(world):
    """The client reads `csv` (W-1), so a reader must: taking `dat` first
    served the pristine bytes and left an installed map mod's rows invisible
    (measured 2026-10-12, the W-3 probe rows installed into a real client)."""
    patched = FIXTURES + b"3,11,Pine,30000,30000,0,0,100,0,0,0,1,0,0,3\r\n"
    write_mpak(world + "/zones/zone000/csv000.mpk", "csv000.mpk", [
        ("fixtures.csv", patched),
    ])
    zone = zones.WorldIndex(world).open(0)
    assert zone.data_entry("fixtures.csv") == patched
    assert [fixture.id for fixture in zone.fixtures()] == [1, 2, 3]
    assert zone.data_entry("terrain.pcx") == _pcx(TERRAIN)   # `dat`-only entry


def test_fixtures_are_typed_and_keep_their_row(world):
    fixtures = zones.WorldIndex(world).open(0).fixtures()
    assert [f.id for f in fixtures] == [1, 2]
    house, pine = fixtures
    assert (house.nif_id, house.name) == (10, "Farm House")
    assert (house.x, house.y, house.z) == (19660.0, 19660.0, 0.0)
    assert (house.angle, house.scale) == (0.0, 100.0)
    assert (house.ground, house.animate, house.flip, house.cave) == (True, False, False, False)
    assert house.unique_id == 1
    assert pine.ground is True
    assert pine.row[2] == "Pine"
    assert len(house.row) == 15


def test_the_catalog_is_typed(world):
    catalog = zones.WorldIndex(world).open(0).catalog()
    assert [n.id for n in catalog] == [10, 11]
    pine = catalog[1]
    assert pine.filename == "bpinea.nif"
    assert (pine.min_scale, pine.max_scale) == (45.0, 100.0)
    assert (pine.min_angle, pine.max_angle) == (0.0, 360.0)
    assert pine.ground is True


def test_bounds_parse_polygons(world):
    bounds = zones.WorldIndex(world).open(0).bounds()
    assert len(bounds) == 1
    assert bounds[0].id == 0
    assert bounds[0].points == [(100, 200), (300, 400), (500, 600)]


def test_jumps_come_from_sector_dat(world):
    jumps = zones.WorldIndex(world).open(0).jumps()
    assert len(jumps) == 1
    assert jumps[0].name == "bridge"
    assert (jumps[0].x, jumps[0].y, jumps[0].z, jumps[0].angle) == (1000.0, 2000.0, -100.0, 90.0)


def test_river_bank_points_are_zone_local(world):
    rivers = zones.WorldIndex(world).open(0).rivers()
    assert len(rivers) == 1
    river = rivers[0]
    assert (river.name, river.kind, river.height, river.flow) == ("faerie pool", "RIVER", 1916.0, -50.0)
    assert river.left == [(174 * 256, 256 * 256), (179 * 256, 249 * 256)]
    assert river.right == [(192 * 256, 256 * 256), (199 * 256, 250 * 256)]


def test_zonejump_rows_are_read(world):
    rows = zones.WorldIndex(world).open(0).zonejumps()
    assert rows == [["1", "CamCity", "3200", "28712", "3053", "28712",
                     "2912", "3072", "6", "", ""]]


def test_dungeon_chunks_and_placements_are_read(world):
    zone = zones.WorldIndex(world).open(2)
    assert zone.dungeon_chunks() == ["Room1.NIF", "Hall1.NIF"]
    blocks = zone.dungeon_blocks()
    # row 3 names a chunk the list does not have, row 5 is not a number:
    # both are skipped rather than shifting the placements that do parse
    assert [(b.index, b.name) for b in blocks] == [
        (0, "Room1.NIF"), (1, "Hall1.NIF"), (1, "Hall1.NIF")]
    assert [(b.x, b.y, b.z) for b in blocks] == [
        (0.0, 0.0, 0.0), (1536.0, 256.0, 192.0), (-384.0, 0.0, 0.0)]
    # the axis settles the sign: +z at 90° is a quarter turn, -z the other way
    assert [b.angle for b in blocks] == pytest.approx([0.0, 90.0, 270.0])


def test_city_blocks_take_their_box_from_the_nhd_header(world):
    blocks = zones.WorldIndex(world).open(3).city_blocks()
    assert [(b.index, b.name) for b in blocks] == [(1, "cam01.nif")]
    block = blocks[0]
    # cells × grid: (-21..28, -21..14) × 64
    assert (block.x, block.y) == (-1344.0, -1344.0)
    assert (block.width, block.height) == (3136.0, 2240.0)


# --- the height model ---------------------------------------------------------

def test_height_at_samples_the_client_way(world):
    zone = zones.WorldIndex(world).open(0)
    # pixel centres: (ix + 0.5) * 16384
    assert zone.height_at(0.5 * 16384, 0.5 * 16384) == 0
    # a sample exactly on the raised pixel (2, 2) reads the pixel itself
    assert zone.height_at(2.0 * 16384, 2.0 * 16384) == 5
    # the centre of that cell is the average of its four corners
    assert zone.height_at(2.5 * 16384, 2.5 * 16384) == pytest.approx(1.25)
    # inside the cell (1, 1), the subtile diagonal decides:
    # (1.2, 1.2) is in the near triangle and cannot see the far corner
    assert zone.height_at(1.2 * 16384, 1.2 * 16384) == 0
    # (1.8, 1.8) is in the far triangle, whose far corner is the raised pixel
    assert zone.height_at(1.8 * 16384, 1.8 * 16384) == pytest.approx(3.2)


def test_height_at_clamps_to_the_map(world):
    zone = zones.WorldIndex(world).open(0)
    assert zone.height_at(-100, -100) == zone.height_at(0, 0)
    assert zone.height_at(70000, 70000) == zone.height_at(65535, 65535)


def test_maps_that_disagree_are_refused(world, tmp_path):
    zone = zones.WorldIndex(world).open(0)
    zone._cache["offset"] = (8, 8, bytes(64))          # deliberately wrong
    with pytest.raises(zones.ZoneError, match="offset.pcx is 8x8"):
        zone.height_at(0, 0)


def test_a_zone_without_height_factors_is_refused(world):
    zone = zones.WorldIndex(world).open(0)
    zone._cache["sector"] = {"terrain": {}}
    with pytest.raises(zones.ZoneError, match="scalefactor"):
        zone.height_at(0, 0)


# --- the corpus ---------------------------------------------------------------

@pytest.mark.usefixtures("client_dir")
def test_the_real_index_matches_the_measurements(client_dir):
    """Measured 2026-10-11: 483 zones (122 normal, 296 dungeon, 18 skycity,
    4 city, 43 without a type) across 358 regions; zone 0 is Camelot Hills in
    region 1, and zone245's header carries a comment attached to it
    (`[zone245];Hall of the Corrupt`), which is why a header ends at its
    first `]`."""
    index = zones.WorldIndex(client_dir)
    assert len(index.zones) == 483
    assert len(index.regions) == 358
    kinds: dict[str, int] = {}
    for info in index.zones.values():
        kinds[info.kind] = kinds.get(info.kind, 0) + 1
    assert kinds == {"normal": 122, "dungeon": 296, "skycity": 18,
                     "city": 4, "unknown": 43}
    camelot = index.zone(0)
    assert camelot.name == "Camelot Hills"
    assert camelot.region == 1
    assert camelot.world_rect == (67 * 8192, 59 * 8192, 8 * 8192, 8 * 8192)
    assert index.zone(100).name == "Vale of Mularn"
    assert index.zone(26).kind == "city"
    assert index.zone(19).kind == "dungeon"
    assert index.zone(245).name == "Hall of the Corrupt"
    assert index.zone(245).kind == "dungeon"


@pytest.mark.usefixtures("client_dir")
def test_the_height_model_matches_the_fixtures_own_z(client_dir):
    """Phase 6.0's exit criterion: wherever the client's own fixtures are
    ground-placed, their `Z` is the height this model computes.

    Measured 2026-10-11: exact on most of the world (zones 1, 7, 10, 203, 27:
    median 0-1 and every row within 64), and **not** on Camelot Hills, whose
    rows sit ~600-1000 above the terrain with 2.5% matching — that zone's
    fixture `Z` is authored, not snapped. (The editor's rule follows the
    client's own convention instead: emit `z = 0` with `Ground = 1` and let
    the client drop the object, as its Midgard zones do.) The criterion
    therefore judges the zones the data itself identifies as ground-placed.
    """
    index = zones.WorldIndex(client_dir)
    exact = 0
    for info in sorted(index.zones.values(), key=lambda info: info.zone_id):
        zone = zones.Zone(client_dir, info)
        if not zone.available or "terrain.pcx" not in zone.entries("dat"):
            continue
        try:
            ground = [fixture for fixture in zone.fixtures()
                      if fixture.ground and fixture.z > 0]
        except zones.ZoneError:
            continue        # a placeholder zone: "Dummy Zone" ships an empty file
        errors = sorted(abs(zone.height_at(fixture.x, fixture.y) - fixture.z)
                        for fixture in ground)
        if len(errors) < 50:
            continue
        within = sum(1 for error in errors if error <= 64) / len(errors)
        if within < 0.9:
            continue                     # authored rows: not this model's claim
        median = errors[len(errors) // 2]
        assert median <= 8, f"{info.name}: median error {median}"
        exact += 1
    assert exact >= 5, f"only {exact} zones were ground-placed enough to judge"


@pytest.mark.usefixtures("client_dir")
def test_the_real_outdoor_zone_reads_its_placements(client_dir):
    zone = zones.WorldIndex(client_dir).open(0)
    fixtures = zone.fixtures()
    assert len(fixtures) > 1000
    keep = next(f for f in fixtures if f.name == "Keep")
    assert keep.nif_id in {n.id for n in zone.catalog()}
    assert 0 <= keep.x <= 65536 and 0 <= keep.y <= 65536
    assert zone.bounds() and len(zone.bounds()[0].points) > 10
    assert len(zone.jumps()) == 6
    assert [river.name for river in zone.rivers()] == ["river00", "faerie pool"]
    for river in zone.rivers():
        for x, y in river.left + river.right:
            assert 0 <= x <= 65536 and 0 <= y <= 65536


@pytest.mark.usefixtures("client_dir")
def test_the_real_dungeon_and_city_are_recognized(client_dir):
    index = zones.WorldIndex(client_dir)
    dungeon = zones.Zone(client_dir, index.zone(19))
    assert dungeon.kind == "dungeon"
    assert {"dungeon.chunk", "dungeon.place", "dungeon.prop"} <= set(dungeon.entries("dat"))
    city = zones.Zone(client_dir, index.zone(26))
    assert city.kind == "city"
    assert {"city.csv", "adjacency.csv"} <= set(city.entries("dat"))


@pytest.mark.usefixtures("client_dir")
def test_a_server_world_point_resolves_through_the_index(client_dir):
    """A spawn's coordinates (the server DB's) land in the zone the client
    draws, without a database: the sand crab row at region 100,
    (774446, 835469) resolves to zone 103, "Gotar" (measured 2026-10-11)."""
    index = zones.WorldIndex(client_dir)
    found = index.zone_at(100, 774446, 835469)
    assert found is not None
    assert found.zone_id == 103
    assert found.name == "Gotar"
    assert found.world_rect == (88 * 8192, 98 * 8192, 8 * 8192, 8 * 8192)
