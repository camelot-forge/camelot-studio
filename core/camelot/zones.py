"""The client's world: the zone index, the terrain, the placements (Phase 6.0).

Everything here is read-only, and every rule is measured rather than assumed
(2026-10-11, `notes/` in the private workspace):

- `zones/zones.mpk` carries `zones.dat`, the client's own index: 474 enabled
  zones across 358 regions, each with its name, region, `type` (0 normal,
  1 city, 2 dungeon, 4 skycity) and its world rectangle in 8192-unit cells
  (`region_offset_x/y`, `width`, `height`). The server builds the same
  rectangle from its DB (`OffX * 8192`, `WorldMgr.cs`), so the two indexes
  agree by construction — `zone_at()` is the lookup that checks it per zone;
- a zone's data lives in archives named after it (`zones/zoneNNN/datNNN.mpk`,
  `csvNNN.mpk`, `terNNN.mpk`, `lodNNN.mpk`, `texNNN.mpk`); which exist depends
  on the type. An outdoor zone ships `terrain.pcx` + `offset.pcx` +
  `fixtures.csv` + `nifs.csv` + `bound.csv` + `sector.dat` + `zonejump.csv`;
  a dungeon ships `dungeon.chunk/.place/.prop`; a city `city.csv` +
  `adjacency.csv`. `dat` and `csv` carry the same data in the zones measured;
  which one the client reads first is the W-1 experiment, still unmeasured —
  this reader takes `dat` and falls back to `csv`;
- the ground height at a zone-local point is
  `terrain[y][x] * scalefactor + offset[y][x] * offsetfactor` (factors from
  `sector.dat`), sampled the way the client does: row-major, bilinear across
  the subtile's diagonal. The calibration against the client's own
  `fixtures.csv` is exact (median error 0 on the zones measured), which is
  what makes "place an object on the ground" trustworthy;
- a fixture with `z = 0` and `Ground = 1` is one the client drops on the
  terrain at load (Midgard's zones ship most of their fixtures that way):
  its height is `height_at(x, y)`, not an error;
- coordinates are zone-local, `0..65535`; a world point adds the zone's
  rectangle. `sector.dat`'s river bank points are in 256-unit pixels
  (`pixel * 256` is zone-local).

Nothing here writes. An edit goes through the pack pipeline
(`camelot.modbuild`), and the archives are rebuilt by `camelot.mpak`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import clientfs, gamedata, pcx
from .mpak import MpakArchive, MpakError

__all__ = [
    "ZONE_ARCHIVES",
    "ZONE_KINDS",
    "ZONE_SIZE",
    "ZONE_UNIT",
    "Bound",
    "Fixture",
    "Jump",
    "River",
    "WorldIndex",
    "Zone",
    "ZoneError",
    "ZoneInfo",
    "ZoneNif",
]

#: The world's grid: a zone spans `ZONE_UNIT * 8` = 65536 units, the same
#: rectangle the server builds from its DB (`OffX * 8192`, `WorldMgr.cs`).
ZONE_UNIT = 8192
ZONE_SIZE = ZONE_UNIT * 8

#: `zones.dat`'s `type` field, the client's own enum (BuildNav's `eZoneType`).
ZONE_KINDS = {0: "normal", 1: "city", 2: "dungeon", 4: "skycity"}

#: The archives a zone may ship, by the name this module knows them by.
ZONE_ARCHIVES = ("dat", "csv", "ter", "lod", "tex")


class ZoneError(ValueError):
    """Raised when the client's world data is missing or does not parse."""


def _parse_ini(text: str) -> dict[str, dict[str, str]]:
    """`[sections]` and `key=value`, `;` comments, values kept verbatim.

    Both `zones.dat` and `sector.dat` are this shape (with a BOM and CRLF in
    the client's files). A comment may be **attached** to a section header
    (`[zone245];Hall of the Corrupt`, measured), so a header ends at its first
    `]`; values are kept verbatim, because a value like `texture=watery.tga`
    must survive as it was written.
    """
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "#")):
            continue
        if line.startswith("["):
            end = line.find("]")
            if end > 0:
                current = line[1:end].strip()
                sections.setdefault(current, {})
                continue
        if "=" in line:
            key, _, value = line.partition("=")
            sections.setdefault(current, {})[key.strip()] = value.strip()
    return sections


def _number(text: str, what: str) -> float:
    try:
        return float(text.strip() or 0)
    except ValueError:
        raise ZoneError(f"{what} is not a number: {text!r}") from None


def _flag(text: str) -> bool:
    return text.strip() not in ("", "0")


def _field(row: list[str], index: int) -> str:
    return row[index] if len(row) > index else ""


# --- the index ----------------------------------------------------------------

@dataclass
class ZoneInfo:
    """One zone as the client's own index describes it."""

    zone_id: int
    name: str
    region: int
    #: The world rectangle, in 8192-unit cells (the client's own units).
    offset_x: int
    offset_y: int
    width: int
    height: int
    kind: str = "unknown"
    enabled: bool = True
    music: int | None = None
    #: The section as read, so nothing the index carries is lost.
    settings: dict[str, str] = field(default_factory=dict)

    @property
    def world_rect(self) -> tuple[int, int, int, int]:
        """(x, y, width, height) in world units."""
        return (self.offset_x * ZONE_UNIT, self.offset_y * ZONE_UNIT,
                self.width * ZONE_UNIT, self.height * ZONE_UNIT)


def _zone_info(zone_id: int, values: dict[str, str]) -> ZoneInfo:
    def number(key: str, default: int = 0) -> int:
        text = values.get(key, "")
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            raise ZoneError(f"zone{zone_id:03d}: {key} is not a number: {text!r}") from None

    music = values.get("entry_music", "")
    return ZoneInfo(
        zone_id=zone_id,
        name=values.get("name", ""),
        region=number("region"),
        offset_x=number("region_offset_x"),
        offset_y=number("region_offset_y"),
        width=number("width", 8),
        height=number("height", 8),
        kind=ZONE_KINDS.get(number("type", -1), "unknown"),
        enabled=values.get("enabled", "1") == "1",
        music=int(music) if music.isdigit() else None,
        settings=dict(values),
    )


class WorldIndex:
    """`zones.dat`: the client's zones and regions, and the world lookup."""

    def __init__(self, client_dir: str):
        self.client_dir = client_dir
        path = clientfs.resolve(client_dir, "zones/zones.mpk")
        if path is None:
            raise ZoneError(f"{client_dir}: no zones/zones.mpk (not a DAoC client?)")
        archive = MpakArchive(path)
        try:
            raw = archive.read("zones.dat")
        except MpakError as error:
            raise ZoneError(f"zones.mpk: {error}") from error
        finally:
            archive.close()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        self.zones: dict[int, ZoneInfo] = {}
        self.regions: dict[int, dict[str, str]] = {}
        for name, values in _parse_ini(text).items():
            if name.startswith("zone") and name[4:].isdigit():
                self.zones[int(name[4:])] = _zone_info(int(name[4:]), values)
            elif name.startswith("region") and name[6:].isdigit():
                self.regions[int(name[6:])] = values

    def zone(self, zone_id: int) -> ZoneInfo:
        try:
            return self.zones[zone_id]
        except KeyError:
            raise ZoneError(f"the client's index has no zone {zone_id}") from None

    def zones_of(self, region: int) -> list[ZoneInfo]:
        """The index's zones in *region*, by zone id."""
        return sorted((info for info in self.zones.values() if info.region == region),
                      key=lambda info: info.zone_id)

    def zone_at(self, region: int, x: int, y: int) -> ZoneInfo | None:
        """The zone containing a *world* point, or None.

        The rectangle is the client's own (`region_offset * 8192`), which is
        also the server's (`WorldMgr.cs`), so a spawn's coordinates resolve
        here without a database.
        """
        for info in self.zones_of(region):
            left, top, width, height = info.world_rect
            if left <= x < left + width and top <= y < top + height:
                return info
        return None

    def open(self, zone_id: int) -> Zone:
        """The zone's data reader, for an id the index knows."""
        return Zone(self.client_dir, self.zone(zone_id))


# --- one zone's data ----------------------------------------------------------

@dataclass
class Fixture:
    """One row of a zone's `fixtures.csv`: an object placed in the world.

    Coordinates are zone-local (`0..ZONE_SIZE`), `angle` is the client's
    12-bit heading, `scale` a percentage. `ground` is the client's own "drop
    it on the terrain" flag: with `z == 0` the object stands at
    `Zone.height_at(x, y)`. `row` keeps the fields exactly as read, so an
    editor can write back what it did not understand.
    """

    id: int
    nif_id: int
    name: str
    x: float
    y: float
    z: float
    angle: float
    scale: float
    collide: float
    radius: float
    animate: bool
    ground: bool
    flip: bool
    cave: bool
    unique_id: int
    row: list[str] = field(default_factory=list)


@dataclass
class ZoneNif:
    """One row of a zone's `nifs.csv`: the catalog its fixtures pick from."""

    id: int
    name: str
    filename: str
    min_scale: float
    max_scale: float
    min_angle: float
    max_angle: float
    radius: float
    ground: bool
    row: list[str] = field(default_factory=list)


@dataclass
class Bound:
    """One walkable polygon of `bound.csv`, in zone-local units."""

    id: int
    points: list[tuple[int, int]]


@dataclass
class Jump:
    """One named jump point from `sector.dat`'s `[jumpNN]`."""

    name: str
    x: float
    y: float
    z: float
    angle: float


@dataclass
class River:
    """One water body from `sector.dat`'s `[riverNN]`.

    `left`/`right` are the bank points in zone-local units (the file stores
    256-unit pixels), `height` the water's Z, `kind` the file's own type
    (`RIVER`, `SWAMP`, …).
    """

    name: str
    kind: str
    texture: str
    height: float
    flow: float
    left: list[tuple[int, int]]
    right: list[tuple[int, int]]


class Zone:
    """One zone's data, read from the client's own archives (read-only)."""

    def __init__(self, client_dir: str, info: ZoneInfo):
        self.client_dir = client_dir
        self.info = info
        self._cache: dict[str, object] = {}

    # -- files -----------------------------------------------------------------

    @property
    def name(self) -> str:
        return self.info.name or f"zone{self.info.zone_id:03d}"

    @property
    def folder(self) -> str | None:
        """The on-disk folder (`zones/zoneNNN`), or None when not shipped."""
        return clientfs.resolve(self.client_dir, f"zones/zone{self.info.zone_id:03d}")

    @property
    def available(self) -> bool:
        """True when the client ships this zone's folder.

        The index lists zones whose data lives elsewhere (housing regions are
        built by the server), so a listed zone is not always an openable one.
        """
        return self.folder is not None

    def archive(self, logical: str) -> str | None:
        """The path of the zone's *logical* archive, or None when absent."""
        if logical not in ZONE_ARCHIVES:
            raise ZoneError(f"unknown zone archive {logical!r}")
        folder = self.folder
        if folder is None:
            return None
        return clientfs.resolve(folder, f"{logical}{self.info.zone_id:03d}.mpk")

    def entries(self, logical: str) -> list[str]:
        """The entry names inside *logical*'s archive (lower-cased), [] if absent."""
        path = self.archive(logical)
        if path is None:
            return []
        archive = MpakArchive(path)
        try:
            return list(archive.entries)
        finally:
            archive.close()

    def entry(self, logical: str, name: str) -> bytes:
        """One entry of *logical*'s archive, by name (case-insensitive)."""
        path = self.archive(logical)
        if path is None:
            raise ZoneError(f"{self.name}: the client has no {logical} archive "
                            f"(zone{self.info.zone_id:03d})")
        archive = MpakArchive(path)
        try:
            return archive.read(name)
        except MpakError as error:
            raise ZoneError(f"{self.name}: {error}") from error
        finally:
            archive.close()

    def data_entry(self, name: str) -> bytes:
        """One data entry, from `dat` and falling back to `csv`.

        The two archives carry the same bytes in the zones measured; which one
        the client prefers is the W-1 experiment. Reads do not depend on the
        answer; writes will.
        """
        if self.archive("dat") is not None:
            try:
                return self.entry("dat", name)
            except ZoneError:
                pass
        return self.entry("csv", name)

    # -- the index's own kind, or the archives' ---------------------------------

    @property
    def kind(self) -> str:
        """The zone's type: the index's `type`, else read from its archives."""
        if self.info.kind != "unknown":
            return self.info.kind
        for logical in ("dat", "csv"):
            entries = set(self.entries(logical))
            if {"dungeon.chunk", "dungeon.place"} & entries:
                return "dungeon"
            if {"city.csv", "adjacency.csv"} & entries:
                return "city"
            if "terrain.pcx" in entries:
                return "normal"
        return "unknown"

    # -- terrain ---------------------------------------------------------------

    def sector(self) -> dict[str, dict[str, str]]:
        """`sector.dat` parsed (fog, sky, terrain factors, water, jumps)."""
        if "sector" not in self._cache:
            raw = self.data_entry("sector.dat")
            try:
                text = raw.decode("latin-1")
            except UnicodeDecodeError as error:            # pragma: no cover
                raise ZoneError(f"{self.name}: sector.dat: {error}") from error
            self._cache["sector"] = _parse_ini(text)
        return self._cache["sector"]  # type: ignore[return-value]

    def terrain_map(self) -> tuple[int, int, bytes]:
        """(width, height, pixels) of the heightmap (`terrain.pcx`)."""
        if "terrain" not in self._cache:
            self._cache["terrain"] = pcx.read(self.data_entry("terrain.pcx"))
        return self._cache["terrain"]  # type: ignore[return-value]

    def offset_map(self) -> tuple[int, int, bytes]:
        """(width, height, pixels) of the offset map (`offset.pcx`)."""
        if "offset" not in self._cache:
            self._cache["offset"] = pcx.read(self.data_entry("offset.pcx"))
        return self._cache["offset"]  # type: ignore[return-value]

    def factors(self) -> tuple[int, int]:
        """(terrain, offset) height factors, from `sector.dat`'s `[terrain]`."""
        terrain = self.sector().get("terrain", {})
        try:
            return int(terrain["scalefactor"]), int(terrain["offsetfactor"])
        except (KeyError, ValueError):
            raise ZoneError(f"{self.name}: sector.dat has no [terrain] "
                            "scalefactor/offsetfactor") from None

    def height_at(self, x: float, y: float) -> float:
        """The ground height at a zone-local point, as the client samples it.

        Row-major, bilinear across each subtile's diagonal (`x + y <= 1`
        picks the first triangle) — the shape BuildNav's `HeightMap`
        documents and this zone's own fixtures calibrate (median error 0).
        """
        width, height, terrain = self.terrain_map()
        other_width, other_height, offset = self.offset_map()
        if (other_width, other_height) != (width, height):
            raise ZoneError(f"{self.name}: terrain.pcx is {width}x{height} but "
                            f"offset.pcx is {other_width}x{other_height}")
        terrain_factor, offset_factor = self.factors()
        fx = min(max(x, 0.0) * width / ZONE_SIZE, width - 1.0)
        fy = min(max(y, 0.0) * height / ZONE_SIZE, height - 1.0)

        def pixel(ix: int, iy: int) -> float:
            ix = min(max(ix, 0), width - 1)
            iy = min(max(iy, 0), height - 1)
            index = iy * width + ix
            return terrain[index] * terrain_factor + offset[index] * offset_factor

        left, top = int(fx), int(fy)
        right, bottom = left + 1, top + 1
        x_percent, y_percent = fx - left, fy - top
        if x_percent + y_percent <= 1.0:
            edge = pixel(left, top)
            first = edge + (pixel(left, bottom) - edge) * y_percent
            second = pixel(right, top) + (pixel(left, bottom) - pixel(right, top)) * y_percent
            return first + (second - first) * x_percent
        x_percent, y_percent = 1.0 - x_percent, 1.0 - y_percent
        edge = pixel(right, bottom)
        first = edge + (pixel(right, top) - edge) * y_percent
        second = pixel(left, bottom) + (pixel(right, top) - pixel(left, bottom)) * y_percent
        return first + (second - first) * x_percent

    # -- placements ------------------------------------------------------------

    def fixtures(self) -> list[Fixture]:
        """The zone's object placements (`fixtures.csv`), typed."""
        table = gamedata.parse_table("fixtures.csv", self.data_entry("fixtures.csv"))
        try:
            columns = {name: table.column_index(name) for name in (
                "ID", "NIF #", "Textual Name", "X", "Y", "Z", "A", "Scale",
                "Collide", "Radius", "Animate", "Ground", "Flip", "Cave", "Unique Id")}
        except gamedata.TableError as error:
            raise ZoneError(f"{self.name}: fixtures.csv: {error}") from error
        out: list[Fixture] = []
        for row in table.rows[gamedata.HEADER_ROWS:]:
            if not row or not _field(row, 0).strip():
                continue
            fields = {name: _field(row, index) for name, index in columns.items()}
            out.append(Fixture(
                id=int(_number(fields["ID"], f"{self.name}: fixtures.csv ID")),
                nif_id=int(_number(fields["NIF #"], f"{self.name}: fixtures.csv NIF #")),
                name=fields["Textual Name"].strip(),
                x=_number(fields["X"], f"{self.name}: fixtures.csv X"),
                y=_number(fields["Y"], f"{self.name}: fixtures.csv Y"),
                z=_number(fields["Z"], f"{self.name}: fixtures.csv Z"),
                angle=_number(fields["A"], f"{self.name}: fixtures.csv A"),
                scale=_number(fields["Scale"], f"{self.name}: fixtures.csv Scale"),
                collide=_number(fields["Collide"], f"{self.name}: fixtures.csv Collide"),
                radius=_number(fields["Radius"], f"{self.name}: fixtures.csv Radius"),
                animate=_flag(fields["Animate"]),
                ground=_flag(fields["Ground"]),
                flip=_flag(fields["Flip"]),
                cave=_flag(fields["Cave"]),
                unique_id=int(_number(fields["Unique Id"], f"{self.name}: fixtures.csv Unique Id")),
                row=list(row),
            ))
        return out

    def catalog(self) -> list[ZoneNif]:
        """The zone's object catalog (`nifs.csv`), typed."""
        table = gamedata.parse_table("nifs.csv", self.data_entry("nifs.csv"))
        try:
            columns = {name: table.column_index(name) for name in (
                "NIF", "Textual Name", "Filename", "Ground", "MinAngle",
                "MaxAngle", "MinScale", "MaxScale", "Radius")}
        except gamedata.TableError as error:
            raise ZoneError(f"{self.name}: nifs.csv: {error}") from error
        out: list[ZoneNif] = []
        for row in table.rows[gamedata.HEADER_ROWS:]:
            if not row or not _field(row, 0).strip():
                continue
            fields = {name: _field(row, index) for name, index in columns.items()}
            out.append(ZoneNif(
                id=int(_number(fields["NIF"], f"{self.name}: nifs.csv NIF")),
                name=fields["Textual Name"].strip(),
                filename=fields["Filename"].strip(),
                min_scale=_number(fields["MinScale"], f"{self.name}: nifs.csv MinScale"),
                max_scale=_number(fields["MaxScale"], f"{self.name}: nifs.csv MaxScale"),
                min_angle=_number(fields["MinAngle"], f"{self.name}: nifs.csv MinAngle"),
                max_angle=_number(fields["MaxAngle"], f"{self.name}: nifs.csv MaxAngle"),
                radius=_number(fields["Radius"], f"{self.name}: nifs.csv Radius"),
                ground=_flag(fields["Ground"]),
                row=list(row),
            ))
        return out

    def bounds(self) -> list[Bound]:
        """The zone's walkable polygons (`bound.csv`), zone-local."""
        raw = self.data_entry("bound.csv")
        out: list[Bound] = []
        for line in raw.decode("latin-1").splitlines():
            fields = [field_.strip() for field_ in line.split(",")]
            if len(fields) < 2 or not fields[0]:
                continue
            try:
                bound_id, count = int(fields[0]), int(fields[1])
            except ValueError:
                raise ZoneError(f"{self.name}: bound.csv: unreadable row {line!r}") from None
            numbers: list[int] = []
            for text in fields[2:]:
                if not text:
                    continue
                try:
                    numbers.append(int(float(text)))
                except ValueError:
                    raise ZoneError(
                        f"{self.name}: bound.csv: {text!r} is not a coordinate") from None
            points = [(numbers[i], numbers[i + 1])
                      for i in range(0, min(len(numbers), count * 2) - 1, 2)]
            out.append(Bound(id=bound_id, points=points))
        return out

    def jumps(self) -> list[Jump]:
        """The zone's named jump points (`sector.dat`'s `[jumpNN]`)."""
        sector = self.sector()
        count = int(_number(sector.get("jumpdefs", {}).get("numjumppoints", "0"),
                            f"{self.name}: jumpdefs"))
        out: list[Jump] = []
        for index in range(count):
            section = sector.get(f"jump{index:02d}")
            if not section:
                continue
            out.append(Jump(
                name=section.get("name", ""),
                x=_number(section.get("x", "0"), f"{self.name}: jump{index:02d}.x"),
                y=_number(section.get("y", "0"), f"{self.name}: jump{index:02d}.y"),
                z=_number(section.get("z", "0"), f"{self.name}: jump{index:02d}.z"),
                angle=_number(section.get("a", "0"), f"{self.name}: jump{index:02d}.a"),
            ))
        return out

    def rivers(self) -> list[River]:
        """The zone's water bodies (`sector.dat`'s `[riverNN]`), zone-local."""
        sector = self.sector()
        count = int(_number(sector.get("waterdefs", {}).get("num", "0"),
                            f"{self.name}: waterdefs"))
        out: list[River] = []
        for index in range(count):
            section = sector.get(f"river{index:02d}")
            if not section:
                continue
            banks = int(_number(section.get("bankpoints", "0"),
                                f"{self.name}: river{index:02d}.bankpoints"))
            left: list[tuple[int, int]] = []
            right: list[tuple[int, int]] = []
            for point in range(banks):
                for side, target in (("left", left), ("right", right)):
                    text = section.get(f"{side}{point:02d}")
                    if not text:
                        continue
                    parts = text.split(",")
                    if len(parts) < 2:
                        raise ZoneError(f"{self.name}: river{index:02d}.{side}{point:02d}: "
                                        f"unreadable bank point {text!r}")
                    target.append((int(_number(parts[0], "bank x")) * 256,
                                   int(_number(parts[1], "bank y")) * 256))
            out.append(River(
                name=section.get("name", ""),
                kind=section.get("type", ""),
                texture=section.get("texture", ""),
                height=_number(section.get("height", "0"), f"{self.name}: river{index:02d}.height"),
                flow=_number(section.get("flow", "0"), f"{self.name}: river{index:02d}.flow"),
                left=left,
                right=right,
            ))
        return out

    def zonejumps(self) -> list[list[str]]:
        """`zonejump.csv`'s rows (from/to points and the zone they lead to)."""
        raw = self.data_entry("zonejump.csv")
        rows: list[list[str]] = []
        for line in raw.decode("latin-1").splitlines():
            fields = [field_.strip() for field_ in line.split(",")]
            if not fields or not fields[0]:
                continue
            if fields[0].lower().startswith("id"):
                continue
            rows.append(fields)
        return rows
