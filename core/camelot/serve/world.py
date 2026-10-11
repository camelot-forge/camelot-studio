"""The world behind `/world`: zones as JSON, meshes as glTF, textures as PNG.

The daemon is the only process that reads the client's world — the browser
gets JSON and standard images and never opens a zone archive (the rule the
asset index already follows). Everything expensive is cached on disk keyed by
the source file's mtime, like the asset exports in `serve/api.py`.

Formats, measured 2026-10-11 (`notes/` in the private workspace):

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
import os
import struct
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import clientfs, nifutil
from ..dxt import dds_rgba
from ..gamedata import SlotTexture
from ..glb import pack_glb
from ..gltf import export_gltf
from ..mpak import MpakArchive, MpakError
from ..png import encode_png
from ..zones import ZONE_ARCHIVES, WorldIndex, Zone, ZoneError
from .cache import read_json, write_json
from .config import ServeConfig
from .models import (
    WorldArchive,
    WorldBound,
    WorldCatalogEntry,
    WorldFixture,
    WorldIndexPayload,
    WorldJump,
    WorldPatches,
    WorldRegionSummary,
    WorldTerrain,
    WorldTextureLayer,
    WorldWater,
    WorldZoneInfo,
    WorldZoneSummary,
)

#: Bump when an exported artefact (glb, png) changes shape: the mtime alone
#: would keep serving files built by an older exporter. Bumped to 7 when the
#: zone meshes moved to the world axis conversion (the reflection of the
#: client's left-handed frame — the cached glbs were mirrored).
_WORLD_EXPORT_VERSION = "7"

#: `/world/zone/{id}/terrain`'s payload: magic, version, grid, factors, planes.
TERRAIN_MAGIC = b"CAMT"
TERRAIN_VERSION = 1
TERRAIN_HEADER = struct.Struct("<4sHHHff")


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


def _fixture(row) -> WorldFixture:
    return WorldFixture(
        id=row.id, nif_id=row.nif_id, name=row.name, x=row.x, y=row.y, z=row.z,
        angle=row.angle, scale=row.scale, radius=row.radius, collide=row.collide,
        animate=row.animate, ground=row.ground, flip=row.flip, cave=row.cave,
        unique_id=row.unique_id,
    )


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


def zone_payload(world: World, zone_id: int) -> WorldZoneInfo:
    zone = world.zone(zone_id)
    warnings: list[str] = []
    info = zone_summary(zone.info, True)
    base = f"/api/v1/world/zone/{zone_id}"

    terrain: WorldTerrain | None = None
    try:
        width, height, _ = zone.terrain_map()
        terrain_factor, offset_factor = zone.factors()
        terrain = WorldTerrain(
            width=width, height=height, terrain_factor=terrain_factor,
            offset_factor=offset_factor, url=f"{base}/terrain",
        )
    except ZoneError as error:
        warnings.append(f"terrain: {error}")

    def part(name: str, loader: Callable[[], Any], default: Any) -> Any:
        try:
            return loader()
        except ZoneError as error:
            warnings.append(f"{name}: {error}")
            return default

    fixtures = part("fixtures", lambda: [_fixture(row) for row in zone.fixtures()], [])
    catalog = part("catalog", lambda: _catalog(zone), [])
    bounds = part("bounds", lambda: [WorldBound(id=b.id, points=b.points)
                                     for b in zone.bounds()], [])
    water = part("water", lambda: [
        WorldWater(name=r.name, kind=r.kind, texture=r.texture, height=r.height,
                   flow=r.flow, left=r.left, right=r.right)
        for r in zone.rivers()], [])
    jumps = part("jumps", lambda: [WorldJump(name=j.name, x=j.x, y=j.y, z=j.z, angle=j.angle)
                                   for j in zone.jumps()], [])
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
        bounds=bounds,
        water=water,
        jumps=jumps,
        zonejumps=zonejumps,
        patches=_patches(world, zone),
        archives=archives,
        warnings=warnings,
    )


# --- the binary terrain -------------------------------------------------------

def terrain_binary(zone: Zone) -> bytes:
    """`CAMT` + version + grid + factors + the two byte planes, row-major."""
    width, height, terrain = zone.terrain_map()
    other_width, other_height, offset = zone.offset_map()
    if (width, height) != (other_width, other_height):
        raise WorldError(f"{zone.name}: terrain.pcx is {width}x{height} but "
                         f"offset.pcx is {other_width}x{other_height}")
    if width > 0xFFFF or height > 0xFFFF:
        raise WorldError(f"{zone.name}: {width}x{height} does not fit the header")
    terrain_factor, offset_factor = zone.factors()
    return (
        TERRAIN_HEADER.pack(TERRAIN_MAGIC, TERRAIN_VERSION, width, height,
                            float(terrain_factor), float(offset_factor))
        + terrain
        + offset
    )


# --- caching ------------------------------------------------------------------

def _cached(config: ServeConfig, kind: str, source: str, mtime: float,
            builder: Callable[[], bytes], suffix: str) -> bytes:
    digest = hashlib.sha1(f"{kind}|{source}".encode()).hexdigest()[:24]
    out_dir = config.cache_dir / "world"
    out = out_dir / f"{digest}{suffix}"
    meta = out_dir / f"{digest}.json"
    entry = read_json(meta)
    if (
        isinstance(entry, dict)
        and entry.get("exporter") == _WORLD_EXPORT_VERSION
        and entry.get("mtime") == mtime
        and out.exists()
    ):
        return out.read_bytes()
    data = builder()
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    write_json(meta, {"source": source, "mtime": mtime,
                      "exporter": _WORLD_EXPORT_VERSION})
    return data


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


# --- the meshes ---------------------------------------------------------------

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


def build_world_router(config: ServeConfig) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    world = World(config)

    @router.get("/world/index", response_model=WorldIndexPayload)
    def world_index() -> WorldIndexPayload:
        """Every zone and region the client's `zones.dat` lists, with the
        ones whose folder is shipped marked `available`."""
        try:
            return index_payload(world)
        except WorldUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

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

    return router
