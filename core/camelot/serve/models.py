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


class WorldSearchHit(BaseModel):
    """One fixture a region-wide search found: where it stands, and which zone
    holds it — the panel opens that zone and selects it."""

    zone: int
    zone_name: str
    id: int
    name: str
    nif_id: int
    filename: str = ""
    x: float
    y: float
    z: float
    ground: bool


class WorldSearch(BaseModel):
    """A region's fixtures matching a query (C1: "all the Bindstones").

    `scanned` is how many zones were actually read: the answer says what it
    looked at, not only what it found.
    """

    query: str
    region: int | None = None
    scanned: int = 0
    limit: int = 200
    matches: list[WorldSearchHit] = []


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

    @classmethod
    def from_row(cls, row) -> WorldFixture:
        """A `zones.Fixture` as the payload's fixture (one mapping, one home)."""
        return cls(id=row.id, nif_id=row.nif_id, name=row.name, x=row.x, y=row.y,
                   z=row.z, angle=row.angle, scale=row.scale, radius=row.radius,
                   collide=row.collide, animate=row.animate, ground=row.ground,
                   flip=row.flip, cave=row.cave, unique_id=row.unique_id)


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


class WorldSpawnMob(BaseModel):
    """One `mob` row inside the zone, in zone-local x/y/z.

    The field names are the server's own columns, lowercased — `id` is
    `Mob_ID`, `path` is `PathID` (the patrol a mob walks, when it does). No
    vocabulary is invented, and `z` is the row's own: a flying mob stays in
    the air rather than being snapped to a terrain the row never saw.
    """

    id: str
    class_type: str = ""
    name: str = ""
    suffix: str = ""
    guild: str = ""
    x: float
    y: float
    z: float
    heading: int = 0
    level: int = 0
    realm: int = 0
    model: int = 0
    size: int = 0
    gender: int = 0
    flags: int = 0
    aggro_level: int = 0
    aggro_range: int = 0
    path: str | None = None
    npc_template: int = 0


class WorldSpawnObject(BaseModel):
    """One `worldobject` row: a server static, `model` a client `objects.csv` id."""

    id: str
    class_type: str = ""
    name: str = ""
    x: float
    y: float
    z: float
    heading: int = 0
    model: int = 0
    emblem: int = 0
    realm: int = 0


class WorldSpawnDoor(BaseModel):
    """One `door` row — a gate is a *server* object, not a client fixture."""

    id: str
    name: str = ""
    type: int = 0
    x: float
    y: float
    z: float
    heading: int = 0
    level: int = 0
    realm: int = 0
    is_postern: bool = False
    locked: int = 0


class WorldSpawnPoint(BaseModel):
    """One `pathpoints` row of a patrol, in zone-local x/y/z."""

    step: int
    x: float
    y: float
    z: float
    wait: int = 0
    speed: int = 0


class WorldSpawnPath(BaseModel):
    """A patrol, kept whole once one of its points is inside the zone."""

    name: str
    path_type: int = 0
    points: list[WorldSpawnPoint] = []


class WorldSpawns(BaseModel):
    """The server's rows for one zone — the read half of 6.4 (spec §11 B1)."""

    source: str
    counts: dict[str, int] = {}
    mobs: list[WorldSpawnMob] = []
    objects: list[WorldSpawnObject] = []
    doors: list[WorldSpawnDoor] = []
    paths: list[WorldSpawnPath] = []
    warnings: list[str] = []


# --- the editor (6.3) ---------------------------------------------------------

class EditorZone(BaseModel):
    id: int
    ops: list[dict[str, Any]] = []


class EditorRowOp(BaseModel):
    """One zone's server-row patches: moves and removals, journal order."""

    zone: int
    patches: list[dict[str, Any]] = []


class EditorPack(BaseModel):
    pack: str
    modified: float
    size: int


class EditorClient(BaseModel):
    """What the *client* says about this session's own half.

    `revert` cannot touch it — the installed state belongs to the client, its
    own authority (D6) — so the panel shows it and `restore` takes it back.
    """

    mod: str = ""
    installed: bool = False
    version: str | None = None
    claims: list[str] = []


class EditorState(BaseModel):
    """The editing session: the mod, the journal, the built pack — and the
    client's own half, which the client records for itself."""

    available: bool
    dir: str | None = None
    #: The project's own name (`project.json`), else the folder's: what Home's
    #: row, the app bar's chip and the journal all call it.
    name: str = ""
    #: Set when this daemon does **not** hold the checkout's lock (`project.md`
    #: §D13): the viewer serves, every write refuses, and `holder` says who to
    #: close or stop.
    read_only: bool = False
    holder: str = ""
    mod: dict[str, Any] = {}
    zones: list[EditorZone] = []
    rows: list[EditorRowOp] = []
    #: What a save would still write, counted the way the panel counts it
    #: (`fixtures` = distinct fixture ids, `rows` = distinct `table:key` the
    #: database has not been given). One definition, so the chip, the panel and
    #: Home's rows cannot disagree.
    pending: dict[str, int] = {}
    pack: EditorPack | None = None
    client: EditorClient | None = None
    #: What was last published: the op count at that moment, so the panel can
    #: tell a session ahead of its client half from a published one.
    published: dict[str, Any] = {}


class EditorEdit(BaseModel):
    """One accepted edit: the resolved row op, the row as it stands, and the
    server rows it carried (B2: the rows a fixture move took with it)."""

    op: dict[str, Any]
    fixture: WorldFixture | None = None
    patches: list[dict[str, Any]] = []


class EditorBuild(BaseModel):
    """What a build (or an apply) wrote, with the toolchain's own report.

    `pack` is None for a server-only edit (a moved door has no client half).
    """

    pack: str | None = None
    entries: list[dict[str, Any]] = []
    files: list[str] = []
    report: dict[str, Any] = {}
    install: dict[str, Any] | None = None
    patches: int = 0


class EditorRestore(BaseModel):
    """What a full restore took back: the client half that *was* installed,
    the server rows put back, and the session as it stands after."""

    client: EditorClient
    rows: int = 0
    state: EditorState


class HomeDaemon(BaseModel):
    """What this daemon serves, said before anything else is asked (`home.md`
    §1): the client it reads, the port, the version and the build."""

    version: str
    build: str = "unknown"
    host: str
    port: int
    client: str
    zones: int = 0


class HomeProject(BaseModel):
    """The project this daemon serves — or nothing, and `held`/`read_only` say
    why it cannot be written (`project.md` §D13)."""

    path: str
    name: str
    #: `{"fixtures": n, "rows": m}` — what a save would still write.
    pending: dict[str, int] = {}
    read_only: bool = False
    holder: str = ""


class HomeRecent(BaseModel):
    """One row of the machine's list: the entry, plus what the disk says
    *now* (`home.md` §D1 — the registry carries no copies)."""

    path: str
    name: str
    opened: str = ""
    exists: bool
    project: bool


class HomePayload(BaseModel):
    """Home's first paint: the daemon's facts, the current project, the
    machine's list. Health is asked one row at a time (`/home/health`)."""

    daemon: HomeDaemon
    project: HomeProject | None = None
    recents: list[HomeRecent] = []
    #: Set when the registry itself is unreadable: named, and the list empty —
    #: a convenience never stands between the operator and a folder.
    recent_error: str = ""


class HomeHealth(BaseModel):
    """One project's `verify` (`home.md` §D2): `ok`, `changed`, `missing`,
    `no-client`, or `not-a-project` with the refusal's own words — plus the
    work waiting, so a row can say both things in one answer."""

    path: str
    name: str = ""
    state: str
    detail: str = ""
    findings: list[dict[str, Any]] = []
    #: `{"fixtures": n, "rows": m}` — the same count the chip shows, read from
    #: the project (`home.md` §D1). Empty when the project cannot be read.
    pending: dict[str, int] = {}


class HomeClient(BaseModel):
    """One directory, checked as a DAoC client before anything is written on
    it (`home.md` §D4's modal). Cheap on purpose: the build, the zone folders
    — never a `populate`."""

    path: str
    state: str
    detail: str = ""
    build: str = ""
    zones: int = 0


class HomeLeft(BaseModel):
    """The project a switch leaves behind, and what it still holds (`home.md`
    §D3.1). The journal *is* the save: this is a line Home shows, never a
    wall."""

    path: str
    name: str
    pending: int = 0


class HomeDoor(BaseModel):
    """What one door did (`home.md` §D4): the project's own facts, and — when
    the daemon switched to it — the editor state and what was left behind."""

    path: str
    name: str
    #: Did the daemon switch to it? False when the modal's "Open it in the
    #: World editor" was off, or when there was nothing to open.
    opened: bool = False
    #: Fingerprints recorded (create and import only).
    resources: int = 0
    zones: int = 0
    state: EditorState | None = None
    left: HomeLeft | None = None
    warning: str = ""


class HomeOpenBody(BaseModel):
    """D3's switch: one project's folder."""

    path: str


class HomeNewBody(BaseModel):
    """D4's create: the modal's fields, in the modal's order."""

    name: str
    #: The folder to create — the modal computes it from the name and lets the
    #: operator edit it.
    dir: str
    #: The DAoC client to record. Empty = the client this daemon serves.
    client: str = ""
    git: bool = False
    staging: bool = False
    open: bool = True


class HomeImportBody(BaseModel):
    """The import door: an existing edits folder becomes a project, in place."""

    dir: str
    name: str = ""
    client: str = ""
    git: bool = False
    open: bool = True


class HomeCloneBody(BaseModel):
    """The clone door: `git clone`, run by the daemon (`home.md` §D4)."""

    url: str
    dir: str
    client: str = ""
    open: bool = True


class HomeBrowseEntry(BaseModel):
    name: str
    path: str
    project: bool = False


class HomeBrowse(BaseModel):
    """A folder's sub-folders, for a path field's completion — the daemon is
    local, so the browser never needs a file picker (`home.md` §D7)."""

    path: str
    parent: str | None = None
    dirs: list[HomeBrowseEntry] = []
