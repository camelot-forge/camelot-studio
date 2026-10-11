"""Pydantic schemas for the serve API (source of the generated TS types)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
    root: str
    build: str = "unknown"


class RootInfo(BaseModel):
    root: str
    total: int
    archives: int
    errors: int
    scanned_at: float
    counts: dict[str, int]
    dirs: dict[str, int] = {}


class AssetSummary(BaseModel):
    id: str
    type: str
    name: str
    path: str
    origin: str
    size: int
    mtime: float
    archive: str | None = None
    entry: str | None = None


class CharacterOptions(BaseModel):
    race: str
    race_id: int
    female: bool
    faces: int
    hair_styles: int
    hair_colors: int
    eye_colors: int
    skin_tones: int
    has_decal: bool
    cloak_variants: int
    body_variants: int
    # `pskins.csv` armour materials, and the one a preview starts on.
    materials: list[str] = []
    material: str = ""


class AnimStateSummary(BaseModel):
    """One playable state of a figure, as the client's tables declare it.

    `rate` is optional: it needs the clip's own key density, so the routes
    that do not play the clip omit it rather than open every KFA.
    """

    slot: str
    kfa: str
    animnifs_id: str
    loop: bool
    duration: float
    rate: float | None = None


class AssetDetail(AssetSummary):
    metadata: dict[str, Any] = {}


class AssetPage(BaseModel):
    items: list[AssetSummary]
    total: int
    page: int
    limit: int


# --- the world (Phase 6.2) ----------------------------------------------------

class WorldZoneSummary(BaseModel):
    """One zone as `zones.dat` and the disk describe it."""

    id: int
    name: str
    region: int
    kind: str
    enabled: bool
    music: int | None = None
    world_rect: tuple[int, int, int, int]
    cells: tuple[int, int, int, int]
    available: bool


class WorldRegionSummary(BaseModel):
    id: int
    zones: list[int]
    available: int
    settings: dict[str, str] = {}


class WorldIndexPayload(BaseModel):
    zones: list[WorldZoneSummary]
    regions: list[WorldRegionSummary]


class WorldTerrain(BaseModel):
    """The heightmap's shape and factors; the bytes come from `url`."""

    width: int
    height: int
    terrain_factor: int
    offset_factor: int
    url: str


class WorldFixture(BaseModel):
    id: int
    nif_id: int
    name: str
    x: float
    y: float
    z: float
    angle: float
    scale: float
    radius: float
    collide: float
    animate: bool
    ground: bool
    flip: bool
    cave: bool
    unique_id: int


class WorldCatalogEntry(BaseModel):
    id: int
    name: str
    filename: str
    ground: bool
    radius: float
    min_scale: float
    max_scale: float
    min_angle: float
    max_angle: float
    mesh: bool


class WorldBound(BaseModel):
    id: int
    points: list[tuple[int, int]]


class WorldWater(BaseModel):
    name: str
    kind: str
    texture: str
    height: float
    flow: float
    left: list[tuple[int, int]]
    right: list[tuple[int, int]]


class WorldJump(BaseModel):
    name: str
    x: float
    y: float
    z: float
    angle: float


class WorldTextureLayer(BaseModel):
    """One ground-texture layer of one patch (`ter/textures.csv`)."""

    patch_x: int
    patch_y: int
    texture: str
    rotate: float
    u: float
    v: float
    u_scale: float
    v_scale: float
    visible: bool
    tiles: int
    tileable: bool


class WorldPatches(BaseModel):
    layers: list[WorldTextureLayer]
    masks: dict[str, list[str]]
    textures: list[str]
    available: list[str]


class WorldArchive(BaseModel):
    path: str
    entries: list[str]


class WorldZoneInfo(BaseModel):
    """One zone's data, as the editor consumes it (URLs relative to /api/v1)."""

    info: WorldZoneSummary
    urls: dict[str, str] = {}
    terrain: WorldTerrain | None = None
    fixtures: list[WorldFixture] = []
    catalog: list[WorldCatalogEntry] = []
    bounds: list[WorldBound] = []
    water: list[WorldWater] = []
    jumps: list[WorldJump] = []
    zonejumps: list[list[str]] = []
    patches: WorldPatches | None = None
    archives: dict[str, WorldArchive] = {}
    warnings: list[str] = []
