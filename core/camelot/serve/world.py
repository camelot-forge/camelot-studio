"""The world behind `/world`: zones as JSON, meshes as glTF, textures as PNG.

The daemon is the only process that reads the client's world — the browser
gets JSON and standard images and never opens a zone archive (the rule the
asset index already follows). Everything expensive is cached on disk keyed by
the source file's mtime, like the asset exports in `serve/api.py`.

Formats, measured 2026-10-11 on the 1.127 client:

- `GET /world/zone/{id}/terrain` is a compact binary: the magic `CAMT`, a
  format version, the grid size, the two factors from `sector.dat`, then the
  raw `terrain.pcx` and `offset.pcx` planes (one byte per subtile). The
  browser recomputes the client's own sampling (row-major, bilinear on the
  subtile diagonal) so a point on the rendered mesh and a `height_at` query
  agree by construction;
- `GET …/mesh/{name}` resolves a catalog `Filename` to the MPAK that holds it
  (`zones/nifs/<stem>.npk`, the dungeon set, the zone's own override), embeds
  the sheets the NIF itself names (`world_object`), drops the subtrees the
  client never draws (collision hulls, every `NiLODNode` level but the
  nearest), and serves one glb;
- `GET …/texture/{logical}/{entry}` and `GET /world/terraintex/{name}` serve
  DDS as PNG (the browser cannot read DXT): the zone's `tex`/`lod`/`ter`
  entries, and the shared `zones/TerrainTex` sheets the ground layers name.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import clientfs, creatures, nifutil, serverrows
from ..dxt import dds_rgba
from ..gamedata import SlotTexture
from ..glb import pack_glb
from ..gltf import export_gltf
from ..mpak import MpakArchive, MpakError
from ..png import encode_png
from ..zones import ZONE_ARCHIVES, WorldIndex, Zone, ZoneError
from .cache import read_json, write_json
from .config import ServeConfig
from .editor import Editor, EditorError, build_editor_router
from .models import (
    WorldArchive,
    WorldBound,
    WorldCatalogEntry,
    WorldFixture,
    WorldIndexPayload,
    WorldJump,
    WorldPatches,
    WorldRegionSummary,
    WorldSearch,
    WorldSearchHit,
    WorldSpawnDoor,
    WorldSpawnMob,
    WorldSpawnObject,
    WorldSpawnPath,
    WorldSpawnPoint,
    WorldSpawns,
    WorldTerrain,
    WorldTextureLayer,
    WorldWater,
    WorldZoneInfo,
    WorldZoneSummary,
)

#: Bump when an exported artefact (glb, png) changes shape: the mtime alone
#: would keep serving files built by an older exporter. Bumped to 7 when the
#: zone meshes went back to the figures' axis rotation (the reflection the
#: previous bump introduced turned every building 180 deg round — the cached
#: glbs are rebuilt).
_WORLD_EXPORT_VERSION = "8"

#: Bump when the creature export changes shape. Creature figures share the
#: world cache directory, so they carry their own exporter marker.
_CREATURE_EXPORT_VERSION = "1"

#: `/world/zone/{id}/terrain`'s payload: magic, version, grid, factors, planes.
TERRAIN_MAGIC = b"CAMT"
TERRAIN_VERSION = 1
TERRAIN_HEADER = struct.Struct("<4sHHHff")

#: A flat grid for a zone whose archive carries no `terrain.pcx` (a city or a
#: dungeon): the viewer still gets a ground, and the server rows sit on it.
FLAT_TERRAIN_SIZE = 8


class WorldError(LookupError):
    """The zone or the file asked for does not exist in this client."""


class WorldUnavailable(RuntimeError):
    """The daemon's root is not a DAoC client (or has no zone index)."""


class World:
    """The daemon's lazy view of the client's world."""

    def __init__(self, config: ServeConfig):
        self.config = config
        self._index: WorldIndex | None = None
        self._available: set[int] | None = None
        #: Set by `build_world_router`: the journal that overlays placements.
        self.editor: Editor | None = None
        #: The server's own rows (snapshot or live MySQL): the viewer's read
        #: path, and one of the caches a switch drops (`home.md` §D3.4).
        self.server_rows = ServerRows(config)
        #: model id -> client figure, read once per client.
        self._creature_models: dict[int, creatures.Creature] | None = None

    def repoint(self) -> None:
        """The daemon switched projects or clients (`home.md` §D3.4): drop
        every cache built per project — the zone index, the shipped-zone set,
        the server-rows snapshot and its live connection. The exported meshes
        and textures under `cache/` stay: keyed by path and mtime, they are
        still the right bytes for whatever client we now read."""
        self._index = None
        self._available = None
        self._creature_models = None
        self.server_rows.repoint()

    def index(self) -> WorldIndex:
        if self._index is None:
            try:
                self._index = WorldIndex(str(self.config.root))
            except ZoneError as error:
                raise WorldUnavailable(str(error)) from error
        return self._index

    def zone(self, zone_id: int) -> Zone:
        index = self.index()
        if zone_id not in index.zones:
            raise WorldError(f"the client's index has no zone {zone_id}")
        zone = index.open(zone_id)
        if not zone.available:
            raise WorldError(f"zone {zone_id} ({zone.name}) is not shipped by this client")
        return zone

    def creature(self, model_id: int) -> creatures.Creature | None:
        """The client figure a server model id resolves to, or None."""
        if self._creature_models is None:
            try:
                self._creature_models = {
                    creature.model_id: creature
                    for creature in creatures.read_creatures(str(self.config.root))
                }
            except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError) as error:
                raise WorldError(f"cannot read the creature tables: {error}") from error
        return self._creature_models.get(model_id)

    def available_ids(self) -> set[int]:
        """The zone ids whose folder exists, by listing each root once."""
        if self._available is None:
            found: set[int] = set()
            for root in ("zones", "frontiers/zones", "phousing/zones",
                         "Tutorial/zones", "tutorial/zones"):
                directory = clientfs.resolve(str(self.config.root), root)
                if directory is None:
                    continue
                try:
                    names = os.listdir(directory)
                except OSError:
                    continue
                for name in names:
                    low = name.casefold()
                    if low.startswith("zone") and low[4:].isdigit():
                        found.add(int(low[4:]))
            self._available = found
        return self._available


# --- the server's rows --------------------------------------------------------

#: The driver's codes for the failures an operator can act on, and the sentence
#: the viewer says instead of the tuple.
_DB_DOWN = (2002, 2003)
_DB_AUTH = (1045,)
_DB_MISSING = (1049,)


def _live_problem(error, connection) -> str:
    """A database that is down is a fact about the environment, not a pymysql
    tuple: name the address and the way out."""
    args = getattr(error, "args", ())
    code = args[0] if args and isinstance(args[0], int) else None
    where = ""
    if connection:
        where = f" on {connection.get('host')}:{connection.get('port')}"
    if code in _DB_DOWN:
        return f"no MySQL{where} — the environment's database is not running"
    if code in _DB_AUTH:
        return f"MySQL{where} refused the credentials in the serverconfig.xml"
    if code in _DB_MISSING:
        return (
            f"MySQL{where} has no database {connection.get('database')!r} — "
            "run `camelot env provision`"
        )
    return f"live database: {error}"


class ServerRows:
    """The server's own rows per zone — snapshot, or live MySQL (spec §5).

    The viewer must work without a database: `--server-rows` reads an exported
    snapshot and is pure file I/O; `--db-config` reads the operator's live
    MySQL through the server's own config, TTL-cached (a spawn moved in game
    appears on the next zone open, and panning does not re-query 2,500 rows
    per frame). With neither, the payload says so instead of pretending the
    world is empty.
    """

    #: How long a live zone's rows are reused.
    LIVE_TTL = 15.0

    def __init__(self, config: ServeConfig):
        self.config = config
        self._snapshot: serverrows.Snapshot | None = None
        self._snapshot_key: tuple[str, float] | None = None
        self._connection: dict[str, Any] | None = None
        self._live: dict[int, tuple[float, dict[str, list[dict]]]] = {}

    def source(self) -> str:
        if self.config.server_rows is not None:
            return f"snapshot {self.config.server_rows}"
        if self.config.db_config is not None:
            return f"mysql {self.config.db_config}"
        return "none"

    def repoint(self) -> None:
        """A switch drops the snapshot and the live connection: both were read
        for the client we no longer serve (`home.md` §D3.4)."""
        self._snapshot = None
        self._snapshot_key = None
        self._connection = None
        self._live.clear()

    def for_zone(self, zone: Zone) -> tuple[dict[str, list[dict]], list[str]]:
        """The zone's rows and any warning that explains what is missing."""
        left, top, width, height = zone.info.world_rect
        if self.config.server_rows is not None:
            try:
                snapshot = self._load_snapshot()
            except serverrows.ServerRowsError as error:
                return {}, [str(error)]
            return snapshot.in_rect(region=zone.info.region, zone_id=zone.info.zone_id,
                                    x=left, y=top, width=width, height=height), []
        if self.config.db_config is not None:
            cached = self._live.get(zone.info.zone_id)
            now = time.monotonic()
            if cached is not None and now - cached[0] < self.LIVE_TTL:
                return cached[1], []
            try:
                rows = self._live_rows(zone)
            except serverrows.mysql_errors() as error:
                # pymysql missing, DB down, bad row: the viewer says what
                # broke and still draws the client's world.
                return {}, [_live_problem(error, self._connection)]
            self._live[zone.info.zone_id] = (now, rows)
            return rows, []
        return {}, [
            ("no server rows configured: start with --server-rows <snapshot> "
             "or --db-config <serverconfig.xml>")]

    def _load_snapshot(self) -> serverrows.Snapshot:
        """The snapshot on disk, re-read when its mtime moves (an operator
        exporting again should not have to restart the daemon)."""
        path = str(self.config.server_rows)
        key = (path, _mtime(path))
        if self._snapshot is None or self._snapshot_key != key:
            self._snapshot = serverrows.Snapshot.load(path)
            self._snapshot_key = key
        return self._snapshot

    def _live_rows(self, zone: Zone) -> dict[str, list[dict]]:
        if self._connection is None:
            self._connection = serverrows.parse_server_config(str(self.config.db_config))
        left, top, width, height = zone.info.world_rect
        return serverrows.read_rows_in_rect(
            lambda: serverrows.connect(self._connection), region=zone.info.region,
            zone_id=zone.info.zone_id, x=left, y=top, width=width, height=height)


# --- payloads -----------------------------------------------------------------

def zone_summary(info, available: bool) -> WorldZoneSummary:
    left, top, width, height = info.world_rect
    return WorldZoneSummary(
        id=info.zone_id,
        name=info.name,
        region=info.region,
        kind=info.kind,
        enabled=info.enabled,
        music=info.music,
        world_rect=(left, top, width, height),
        cells=(info.offset_x, info.offset_y, info.width, info.height),
        available=available,
    )


def index_payload(world: World) -> WorldIndexPayload:
    index = world.index()
    available = world.available_ids()
    zones = [zone_summary(info, info.zone_id in available)
             for info in sorted(index.zones.values(), key=lambda i: i.zone_id)]
    by_region: dict[int, list[int]] = {}
    for info in zones:
        by_region.setdefault(info.region, []).append(info.id)
    regions = [
        WorldRegionSummary(
            id=region,
            zones=sorted(ids),
            available=sum(1 for z in ids if z in available),
            settings=index.regions.get(region, {}),
        )
        for region, ids in sorted(by_region.items())
    ]
    return WorldIndexPayload(zones=zones, regions=regions)


def search_payload(world: World, query: str, region: int | None, limit: int) -> WorldSearch:
    """The fixtures of a region (or of every available zone) whose name, file
    or id matches — C1's question ("every Bindstone") answered without opening
    zones by hand.

    One zone costs a `csvNNN.mpk` read (measured: 19 ms on the operator's own
    client, and the zone objects are cached after), so a whole region is about
    a second's work. The journal's view is what is searched: what the editor
    shows is what a search finds.
    """
    needle = query.strip().lower()
    if not needle:
        raise WorldError("a search needs a name, a file or an id")
    index = world.index()
    available = world.available_ids()
    wanted: set[int] | None = None
    if region is not None:
        wanted = {info.zone_id for info in index.zones_of(region)}
        if not wanted:
            raise WorldError(f"no zone in region {region}")
    matches: list[WorldSearchHit] = []
    scanned = 0
    for info in sorted(index.zones.values(), key=lambda i: i.zone_id):
        if info.zone_id not in available:
            continue
        if wanted is not None and info.zone_id not in wanted:
            continue
        zone = world.zone(info.zone_id)
        try:
            rows, _warnings = _zone_fixtures(world, zone)
            filenames = {entry.id: entry.filename for entry in _catalog(zone)}
        except (WorldError, ZoneError):
            # a zone the client lists but does not ship: nothing to search, and
            # it does not count as read
            continue
        scanned += 1
        for row in rows:
            filename = filenames.get(row.nif_id, "")
            if not (
                needle in (row.name or "").lower()
                or str(row.id) == needle
                or needle in filename.lower()
            ):
                continue
            matches.append(
                WorldSearchHit(
                    zone=info.zone_id,
                    zone_name=info.name,
                    id=row.id,
                    name=row.name or "",
                    nif_id=row.nif_id,
                    filename=filename,
                    x=row.x,
                    y=row.y,
                    z=row.z,
                    ground=row.ground,
                )
            )
            if len(matches) >= limit:
                break
        if len(matches) >= limit:
            break
    return WorldSearch(
        query=query, region=region, scanned=scanned, limit=limit, matches=matches
    )


def _fixture(row) -> WorldFixture:
    return WorldFixture.from_row(row)


def _dungeon_architecture(
        zone: Zone) -> tuple[list[WorldCatalogEntry], list[WorldFixture]]:
    """The chunks `dungeon.place` places, as a non-editable render set.

    `dungeon.place` carries the client's own local layout. The viewer mirrors
    and centres it on the server rows (measured on the shipped dungeons); the
    editable list stays `fixtures.csv`, which a dungeon does not have.
    """
    chunks = zone.dungeon_chunks()
    catalog: list[WorldCatalogEntry] = []
    for index, name in enumerate(chunks):
        stem = os.path.splitext(os.path.basename(name))[0]
        catalog.append(WorldCatalogEntry(
            id=index, name=stem, filename=name, ground=False, radius=0,
            min_scale=100, max_scale=100, min_angle=0, max_angle=0,
            mesh=zone.object_mesh(name) is not None,
        ))
    fixtures: list[WorldFixture] = []
    for index, block in enumerate(zone.dungeon_blocks()):
        stem = os.path.splitext(os.path.basename(block.name))[0]
        fixtures.append(WorldFixture(
            id=1_000_000 + index, nif_id=block.index, name=stem,
            x=block.x, y=block.y, z=block.z, angle=block.angle, scale=100,
            radius=0, collide=0, animate=False, ground=False, flip=False,
            cave=False, unique_id=index,
        ))
    return catalog, fixtures


def _city_architecture(
        zone: Zone) -> tuple[list[WorldCatalogEntry], list[WorldFixture]]:
    """The blocks `city.csv` names, with the `.nhd` box as the anchor.

    A city has no `fixtures.csv`: `city.csv` names the block meshes and each
    `camNN.nhd` carries the box the client walks on. The payload exposes that
    box's centre; the viewer anchors the mesh's own plane centre there
    (`architecture_anchor == "center"`), because the NIF geometry is centred on
    its own origin while the block is not.
    """
    catalog: list[WorldCatalogEntry] = []
    fixtures: list[WorldFixture] = []
    for index, block in enumerate(zone.city_blocks()):
        stem = os.path.splitext(os.path.basename(block.name))[0]
        catalog.append(WorldCatalogEntry(
            id=index, name=stem, filename=block.name, ground=False, radius=0,
            min_scale=100, max_scale=100, min_angle=0, max_angle=0,
            mesh=zone.object_mesh(block.name) is not None,
        ))
        fixtures.append(WorldFixture(
            id=1_000_000 + index, nif_id=index, name=stem,
            x=block.x + block.width / 2, y=block.y + block.height / 2, z=0,
            angle=0, scale=100, radius=0, collide=0, animate=False,
            ground=False, flip=False, cave=False, unique_id=index,
        ))
    return catalog, fixtures


def _catalog(zone: Zone) -> list[WorldCatalogEntry]:
    out: list[WorldCatalogEntry] = []
    for row in zone.catalog():
        out.append(WorldCatalogEntry(
            id=row.id, name=row.name, filename=row.filename, ground=row.ground,
            radius=row.radius, min_scale=row.min_scale, max_scale=row.max_scale,
            min_angle=row.min_angle, max_angle=row.max_angle,
            mesh=zone.object_mesh(row.filename) is not None,
        ))
    return out


def _patches(world: World, zone: Zone) -> WorldPatches | None:
    try:
        layers = zone.texture_patches()
    except ZoneError:
        return None
    masks = {f"{x},{y}": names for (x, y), names in zone.patch_masks().items()}
    textures = sorted({layer.texture for layer in layers})
    return WorldPatches(
        layers=[WorldTextureLayer(**{k: v for k, v in vars(layer).items() if k != "row"})
                for layer in layers],
        masks=masks,
        textures=textures,
        available=[name for name in textures
                   if terraintex_path(world, f"{name}.dds") is not None],
    )


def _zone_fixtures(world: World, zone: Zone) -> tuple[list[Any], list[str]]:
    """The placements to show: the editor's journal applied when it has ops.

    The journal is the daemon's own state and the payload is what the
    *build* would write — one view of the zone, edited or not. A journal
    that no longer applies (the client changed under it) falls back to the
    client's own rows and says so, rather than showing half an edit.
    """
    editor = world.editor
    if editor is None or not editor.live_ops(zone.info.zone_id):
        return zone.fixtures(), []
    try:
        return editor.fixtures(zone), []
    except (EditorError, ZoneError) as error:
        return zone.fixtures(), [
            (f"fixtures: the journal did not apply ({error}); "
             "showing the client's own rows")]


def zone_payload(world: World, zone_id: int) -> WorldZoneInfo:
    zone = world.zone(zone_id)
    warnings: list[str] = []
    info = zone_summary(zone.info, True)
    base = f"/api/v1/world/zone/{zone_id}"

    terrain: WorldTerrain | None = None
    flat = False
    try:
        planes = terrain_planes(zone)
        terrain = WorldTerrain(
            width=planes.width, height=planes.height,
            terrain_factor=planes.terrain_factor,
            offset_factor=planes.offset_factor, url=f"{base}/terrain",
            flat=planes.flat,
        )
        flat = planes.flat
    except ZoneError as error:
        warnings.append(f"terrain: {error}")

    def part(name: str, loader: Callable[[], Any], default: Any) -> Any:
        try:
            return loader()
        except ZoneError as error:
            warnings.append(f"{name}: {error}")
            return default

    def placements() -> list[WorldFixture]:
        rows, notes = _zone_fixtures(world, zone)
        warnings.extend(notes)
        return [_fixture(row) for row in rows]

    architecture: list[WorldFixture] = []
    architecture_catalog: list[WorldCatalogEntry] = []
    architecture_anchor = "origin"
    if zone.kind in ("city", "dungeon"):
        # Their client geometry lives in a layout the archive does not carry
        # (city blocks, dungeon chunks). The server rows are still the zone's
        # content: show them on the flat ground, not four archive errors.
        fixtures: list[WorldFixture] = []
        catalog: list[WorldCatalogEntry] = []
        bounds: list[WorldBound] = []
        water: list[WorldWater] = []
        jumps: list[WorldJump] = []
        patches = None
        if zone.kind == "dungeon":
            architecture_catalog, architecture = _dungeon_architecture(zone)
        else:
            architecture_catalog, architecture = _city_architecture(zone)
            architecture_anchor = "center"
        if flat:
            warnings.append(_flat_zone_warning(zone, bool(architecture)))
    else:
        fixtures = part("fixtures", placements, [])
        catalog = part("catalog", lambda: _catalog(zone), [])
        bounds = part("bounds", lambda: [WorldBound(id=b.id, points=b.points)
                                         for b in zone.bounds()], [])
        water = part("water", lambda: [
            WorldWater(name=r.name, kind=r.kind, texture=r.texture, height=r.height,
                       flow=r.flow, left=r.left, right=r.right)
            for r in zone.rivers()], [])
        jumps = part("jumps", lambda: [WorldJump(name=j.name, x=j.x, y=j.y, z=j.z, angle=j.angle)
                                       for j in zone.jumps()], [])
        patches = _patches(world, zone)
    zonejumps = part("zonejumps", zone.zonejumps, [])

    archives: dict[str, WorldArchive] = {}
    for logical in ZONE_ARCHIVES:
        path = zone.archive(logical)
        if path is None:
            continue
        entries = part(f"archives.{logical}", lambda logical=logical: zone.entries(logical), [])
        archives[logical] = WorldArchive(
            path=os.path.relpath(path, str(world.config.root)).replace(os.sep, "/"),
            entries=entries,
        )

    return WorldZoneInfo(
        info=info,
        urls={
            "terrain": f"{base}/terrain",
            "mesh": f"{base}/mesh/{{name}}",
            "texture": f"{base}/texture/{{logical}}/{{entry}}",
            "terraintex": "/api/v1/world/terraintex/{name}",
        },
        terrain=terrain,
        fixtures=fixtures,
        catalog=catalog,
        architecture=architecture,
        architecture_catalog=architecture_catalog,
        architecture_anchor=architecture_anchor,
        bounds=bounds,
        water=water,
        jumps=jumps,
        zonejumps=zonejumps,
        patches=patches,
        archives=archives,
        warnings=warnings,
    )


def spawns_payload(server_rows: ServerRows, world: World, zone_id: int) -> WorldSpawns:
    """The zone's server rows, in zone-local coordinates like the fixtures."""
    zone = world.zone(zone_id)
    left, top, _width, _height = zone.info.world_rect
    rows, warnings = server_rows.for_zone(zone)
    if world.editor is not None:
        # The journal's patches are part of what this zone *is* now: an edited
        # door must appear where it was recorded, before it is applied.
        rows, notes = world.editor.patch_rows(zone.info.zone_id, rows)
        warnings.extend(notes)

    def local(value: float, origin: float) -> float:
        return float(value) - float(origin)

    mobs = [
        WorldSpawnMob(
            id=str(row.get("Mob_ID", "")), class_type=row.get("ClassType") or "",
            name=row.get("Name") or "", suffix=row.get("Suffix") or "",
            guild=row.get("Guild") or "",
            x=local(row["X"], left), y=local(row["Y"], top), z=row["Z"],
            heading=row.get("Heading", 0), level=row.get("Level", 0),
            realm=row.get("Realm", 0), model=row.get("Model", 0),
            size=row.get("Size", 0), gender=row.get("Gender", 0),
            flags=row.get("Flags", 0), aggro_level=row.get("AggroLevel", 0),
            aggro_range=row.get("AggroRange", 0),
            path=row.get("PathID") or None, npc_template=row.get("NPCTemplateID", 0),
        )
        for row in rows.get("mob", [])
    ]
    objects = [
        WorldSpawnObject(
            id=str(row.get("WorldObject_ID", "")), class_type=row.get("ClassType") or "",
            name=row.get("Name") or "",
            x=local(row["X"], left), y=local(row["Y"], top), z=row["Z"],
            heading=row.get("Heading", 0), model=row.get("Model", 0),
            emblem=row.get("Emblem", 0), realm=row.get("Realm", 0),
        )
        for row in rows.get("worldobject", [])
    ]
    doors = [
        WorldSpawnDoor(
            id=str(row.get("Door_ID", "")), name=row.get("Name") or "",
            type=row.get("Type", 0),
            x=local(row["X"], left), y=local(row["Y"], top), z=row["Z"],
            heading=row.get("Heading", 0), level=row.get("Level", 0),
            realm=row.get("Realm", 0), is_postern=bool(row.get("IsPostern", 0)),
            locked=row.get("Locked", 0),
        )
        for row in rows.get("door", [])
    ]

    points_by_path: dict[str, list[WorldSpawnPoint]] = {}
    for row in rows.get("pathpoints", []):
        points_by_path.setdefault(row["PathID"], []).append(WorldSpawnPoint(
            step=row.get("Step", 0), x=local(row["X"], left), y=local(row["Y"], top),
            z=row["Z"], wait=row.get("WaitTime", 0), speed=row.get("MaxSpeed", 0)))
    types = {row["PathID"]: row.get("PathType", 0) for row in rows.get("path", [])}
    paths = [
        WorldSpawnPath(name=name, path_type=types.get(name, 0),
                       points=sorted(points, key=lambda point: point.step))
        for name, points in sorted(points_by_path.items())
    ]

    return WorldSpawns(
        source=server_rows.source(),
        counts={"mob": len(mobs), "worldobject": len(objects), "door": len(doors),
                "path": len(paths),
                "pathpoints": sum(len(path.points) for path in paths)},
        mobs=mobs, objects=objects, doors=doors, paths=paths, warnings=warnings,
    )


# --- the binary terrain -------------------------------------------------------

@dataclass
class TerrainPlanes:
    """The two byte planes and the factors that turn them into heights."""

    width: int
    height: int
    terrain: bytes
    offset: bytes
    terrain_factor: int
    offset_factor: int
    flat: bool = False


def terrain_planes(zone: Zone) -> TerrainPlanes:
    """A zone's height planes, or a flat one for a city/dungeon.

    Those two kinds carry their architecture in a client layout, not in
    `terrain.pcx`/`offset.pcx`. A flat ground keeps the viewer usable for the
    server rows; the missing architecture is reported by the caller.
    """
    try:
        width, height, terrain = zone.terrain_map()
        other_width, other_height, offset = zone.offset_map()
    except ZoneError:
        if zone.kind not in ("city", "dungeon", "skycity"):
            raise
        size = FLAT_TERRAIN_SIZE
        height = _flat_height(zone)
        # The flat plane is synthetic: one byte per sample, so pick a factor
        # that reaches the zone's floor (a dungeon can sit above 10 200).
        terrain_factor = max(1, math.ceil(height / 255))
        height_byte = min(255, max(0, round(height / terrain_factor)))
        planes = bytes([height_byte]) * (size * size)
        return TerrainPlanes(size, size, planes, bytes(size * size),
                             terrain_factor, 0, True)
    if (width, height) != (other_width, other_height):
        raise WorldError(f"{zone.name}: terrain.pcx is {width}x{height} but "
                         f"offset.pcx is {other_width}x{other_height}")
    if width > 0xFFFF or height > 0xFFFF:
        raise WorldError(f"{zone.name}: {width}x{height} does not fit the header")
    terrain_factor, offset_factor = zone.factors()
    return TerrainPlanes(width, height, terrain, offset, terrain_factor,
                         offset_factor)


def _flat_height(zone: Zone) -> float:
    """The floor of a city/dungeon, from its own `lights.csv`.

    Lights and server rows share the zone's coordinates (measured: Camelot's
    lights sit at z 8 109..9 346, its rows at z 8 000; the Barrows' lights at
    13 543..16 646, its rows at 13 206..16 224). The median keeps the flat
    ground at the level the rows were authored on.
    """
    try:
        raw = zone.data_entry("lights.csv").decode("latin-1")
    except ZoneError:
        return 0.0
    values: list[float] = []
    for line in raw.splitlines():
        fields = line.split(",")
        if len(fields) < 3:
            continue
        try:
            values.append(float(fields[2]))
        except ValueError:
            continue
    if not values:
        return 0.0
    values.sort()
    return values[len(values) // 2]


def _flat_zone_warning(zone: Zone, has_architecture: bool = False) -> str:
    if zone.kind == "dungeon":
        if has_architecture:
            return (f"{zone.name}: the dungeon chunks are aligned to the server "
                    "rows; the client's own floors carry the ground")
        return (f"{zone.name}: the dungeon chunks could not be placed; the "
                "ground is a flat fallback")
    if zone.kind == "city":
        if has_architecture:
            return (f"{zone.name}: the city blocks are placed from city.csv and "
                    "their .nhd headers; the client's own floors carry the ground")
        return (f"{zone.name}: the city blocks could not be placed; the ground "
                "is a flat fallback")
    what = {"skycity": "geometry"}.get(
        zone.kind, "geometry")
    return (f"{zone.name}: the client places its {what} outside the zone "
            "archive; the server rows are shown on a flat ground")


def terrain_binary(zone: Zone) -> bytes:
    """`CAMT` + version + grid + factors + the two byte planes, row-major."""
    planes = terrain_planes(zone)
    return (
        TERRAIN_HEADER.pack(TERRAIN_MAGIC, TERRAIN_VERSION, planes.width,
                            planes.height, float(planes.terrain_factor),
                            float(planes.offset_factor))
        + planes.terrain
        + planes.offset
    )


# --- caching ------------------------------------------------------------------

def _cached(config: ServeConfig, kind: str, source: str, mtime: float,
            builder: Callable[[], bytes], suffix: str,
            exporter: str = _WORLD_EXPORT_VERSION) -> bytes:
    digest = hashlib.sha1(f"{kind}|{source}".encode()).hexdigest()[:24]
    out_dir = config.cache_dir / "world"
    out = out_dir / f"{digest}{suffix}"
    meta = out_dir / f"{digest}.json"
    entry = read_json(meta)
    if (
        isinstance(entry, dict)
        and entry.get("exporter") == exporter
        and entry.get("mtime") == mtime
        and out.exists()
    ):
        return out.read_bytes()
    data = builder()
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    write_json(meta, {"source": source, "mtime": mtime,
                      "exporter": exporter})
    return data


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


# --- the meshes ---------------------------------------------------------------

def creature_glb(world: World, model_id: int) -> bytes:
    """The glb of one server model id, resolved through the client's own
    `monsters.csv` -> `monnifs.csv` chain and cached on disk."""
    creature = world.creature(model_id)
    if creature is None:
        raise WorldError(f"no creature model {model_id} in this client")
    path = clientfs.resolve(str(world.config.root), f"figures/{creature.nif}.nif")
    if path is None:
        raise WorldError(
            f"model {model_id}: figures/{creature.nif}.nif is not in this client"
        )
    return _cached(
        world.config, "creature", path, _mtime(path),
        lambda: _export_creature(str(world.config.root), path), ".glb",
        exporter=_CREATURE_EXPORT_VERSION,
    )


def _export_creature(client_dir: str, path: str) -> bytes:
    """One figure, bind pose, as the asset viewer exports it. Textures are
    embedded by `export_figure`; the skin sidecar is not needed here."""
    from ..figure import export_figure

    with tempfile.TemporaryDirectory(prefix="camelot-world-") as tmpdir:
        prefix = str(Path(tmpdir) / "creature")
        try:
            export_figure(client_dir, path, prefix, verbose=False)
            gltf = json.loads(Path(prefix + ".gltf").read_text(encoding="utf-8"))
            bin_bytes = Path(prefix + ".bin").read_bytes()
        except (MpakError, OSError, ValueError, AttributeError, KeyError,
                IndexError, NotImplementedError) as error:
            raise WorldError(f"{os.path.basename(path)}: export failed: {error}") from error
    return pack_glb(gltf, bin_bytes)


def zone_mesh(world: World, zone: Zone, name: str) -> bytes:
    """The glb of the zone object named by a catalog `Filename` (cached)."""
    path = zone.object_mesh(name)
    if path is None:
        raise WorldError(f"{zone.name}: no mesh named {name!r} in this client")
    cache_source = f"{path}@{zone.info.zone_id}"
    return _cached(world.config, "mesh", cache_source, _mtime(path),
                   lambda: _export_zone_mesh(zone, path), ".glb")


def _export_zone_mesh(zone: Zone, path: str) -> bytes:
    archive = MpakArchive(path)
    try:
        entries = list(archive.entries)
        if not entries:
            raise WorldError(f"{path}: the mesh archive is empty")
        data = archive.read(entries[0])
        nif_name = entries[0]
    except MpakError as error:
        raise WorldError(f"{path}: {error}") from error
    finally:
        archive.close()

    with tempfile.TemporaryDirectory(prefix="camelot-world-") as tmpdir:
        nif_path = Path(tmpdir) / os.path.basename(nif_name)
        nif_path.write_bytes(data)
        try:
            document = nifutil.load(str(nif_path))
        except (OSError, ValueError, AttributeError, KeyError, IndexError) as error:
            raise WorldError(f"{nif_name}: {error}") from error
        textures = _zone_textures(zone, document)
        out_prefix = str(Path(tmpdir) / "mesh")
        try:
            export_gltf(str(nif_path), out_prefix, verbose=False, data=document,
                        textures=textures, world_object=True)
        except (ValueError, AttributeError, KeyError, IndexError) as error:
            raise WorldError(f"{nif_name}: export failed: {error}") from error
        gltf = json.loads(Path(out_prefix + ".gltf").read_text(encoding="utf-8"))
        bin_bytes = Path(out_prefix + ".bin").read_bytes()
    return pack_glb(gltf, bin_bytes)


def _zone_textures(zone: Zone, document) -> list[SlotTexture]:
    """Every sheet the NIF names, resolved by stem and embedded by shape.

    A name the client ships as TGA/BMP (no DDS) is skipped: the viewer's
    texture path reads DDS, and a broken sheet is worse than none (the mesh
    renders with its material colour instead).
    """
    out: list[SlotTexture] = []
    seen: set[str] = set()
    for block in document.blocks:
        if type(block).__name__ != "NiSourceTexture":
            continue
        file_name = getattr(block, "file_name", None)
        if not file_name:
            continue
        key = file_name.decode("latin1").lower()
        if key in seen:
            continue
        seen.add(key)
        sheet = zone.object_texture(key)
        if sheet is None or not sheet.casefold().endswith(".dds"):
            continue
        out.append(SlotTexture(key, os.path.basename(sheet), Path(sheet).read_bytes()))
    return out


# --- the textures -------------------------------------------------------------

def texture_png_bytes(logical: str, entry: str, data: bytes) -> bytes:
    """The entry's DDS payload as PNG; anything else is refused by the caller."""
    got = dds_rgba(data)
    if got is None:
        raise WorldError(f"{logical}/{entry}: not a DDS the studio can decode")
    width, height, rgba = got
    return encode_png(width, height, rgba)


def zone_texture(world: World, zone: Zone, logical: str, entry: str) -> bytes:
    if logical not in ZONE_ARCHIVES:
        raise WorldError(f"unknown zone archive {logical!r}")
    path = zone.archive(logical)
    if path is None:
        raise WorldError(f"{zone.name}: the client has no {logical} archive")
    if not entry.casefold().endswith(".dds"):
        raise WorldError(f"{logical}/{entry}: only DDS entries convert to PNG")

    def build() -> bytes:
        try:
            data = zone.entry(logical, entry)
        except ZoneError as error:
            raise WorldError(str(error)) from error
        return texture_png_bytes(logical, entry, data)

    return _cached(world.config, "texture", f"{path}#{entry}", _mtime(path),
                   build, ".png")


def terraintex_path(world: World, name: str) -> str | None:
    """The client's loose ground-layer sheet (`zones/TerrainTex/<name>.dds`)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if not base.casefold().endswith(".dds") or base.startswith("."):
        return None
    for root in ("zones/TerrainTex", "frontiers/zones/TerrainTex",
                 "tutorial/zones/TerrainTex", "Tutorial/TerrainTex",
                 "phousing/zones/TerrainTex"):
        found = clientfs.resolve(str(world.config.root), f"{root}/{base}")
        if found is not None:
            return found
    return None


def terraintex_png(world: World, name: str) -> bytes:
    path = terraintex_path(world, name)
    if path is None:
        raise WorldError(f"no ground-layer sheet named {name!r} in this client")

    def build() -> bytes:
        try:
            return texture_png_bytes("TerrainTex", name, Path(path).read_bytes())
        except OSError as error:
            raise WorldError(f"{name}: {error}") from error

    return _cached(world.config, "terraintex", path, _mtime(path), build, ".png")


# --- routes -------------------------------------------------------------------

def _http(error: WorldError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def build_world_router(config: ServeConfig, world: World | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    # The world (and its editor) may be built by the caller and shared — that
    # is what lets Home read the *same* session, and what a project switch
    # (slice 2b) will re-point once instead of per router.
    world = world if world is not None else World(config)
    if world.editor is None:
        world.editor = Editor(config)
    server_rows = world.server_rows

    @router.get("/world/index", response_model=WorldIndexPayload)
    def world_index() -> WorldIndexPayload:
        """Every zone and region the client's `zones.dat` lists, with the
        ones whose folder is shipped marked `available`."""
        try:
            return index_payload(world)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @router.get("/world/search", response_model=WorldSearch)
    def world_search(q: str = "", region: int | None = None, limit: int = 200) -> WorldSearch:
        """Every fixture of a region whose name, file or id matches (C1)."""
        try:
            return search_payload(world, q, region, max(1, min(limit, 1000)))
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error

    @router.get("/world/zone/{zone_id}", response_model=WorldZoneInfo)
    def world_zone(zone_id: int) -> WorldZoneInfo:
        """One zone: info, fixtures, catalog, bounds, water, jumps, the ground
        layer model, and the archive directory. Unreadable parts are skipped
        with a `warning` rather than failing the zone."""
        try:
            return zone_payload(world, zone_id)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error

    @router.get("/world/zone/{zone_id}/spawns", response_model=WorldSpawns)
    def world_spawns(zone_id: int) -> WorldSpawns:
        """The zone's server rows — mobs, statics, doors and patrols — from
        the configured snapshot or live MySQL; read-only. Without either, the
        payload says so and the client's world still renders."""
        try:
            return spawns_payload(server_rows, world, zone_id)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error

    @router.get("/world/zone/{zone_id}/terrain")
    def world_terrain(zone_id: int) -> Response:
        try:
            zone = world.zone(zone_id)
            payload = terrain_binary(zone)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error
        except ZoneError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        return Response(content=payload, media_type="application/octet-stream")

    @router.get("/world/creature/{model_id}")
    def world_creature(model_id: int) -> Response:
        """One server model id as a bind-pose glb, for the editor's nearby
        creature previews. The model is client-wide, not zone-wide."""
        try:
            glb = creature_glb(world, model_id)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error
        return Response(content=glb, media_type="model/gltf-binary")

    @router.get("/world/zone/{zone_id}/mesh/{name}")
    def world_mesh(zone_id: int, name: str) -> Response:
        try:
            zone = world.zone(zone_id)
            glb = zone_mesh(world, zone, name)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error
        return Response(content=glb, media_type="model/gltf-binary")

    @router.get("/world/zone/{zone_id}/texture/{logical}/{entry}")
    def world_texture(zone_id: int, logical: str, entry: str) -> Response:
        try:
            zone = world.zone(zone_id)
            png = zone_texture(world, zone, logical, entry)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error
        return Response(content=png, media_type="image/png")

    @router.get("/world/terraintex/{name}")
    def world_terraintex(name: str) -> Response:
        try:
            png = terraintex_png(world, name)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except WorldError as error:
            raise _http(error) from error
        return Response(content=png, media_type="image/png")

    router.include_router(build_editor_router(world))
    return router
