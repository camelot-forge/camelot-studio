"""HTTP API (``/api/v1``) over the asset index and the format layer."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from .. import __version__
from ..glb import pack_glb
from ..mpak import MpakArchive, MpakError
from .cache import read_json, write_json
from .config import ServeConfig
from .index import Asset, AssetIndex
from .models import CharacterOptions

# Export-cache schema/behaviour version (bump when export output changes).
_EXPORT_VERSION = "17"
from .models import AssetDetail, AssetPage, AssetSummary, Health, RootInfo
from .watcher import IndexWatcher

SHAPE_TYPES = ("NiTriShape", "NiTriStrips")


def sse_frame(event: str, data: Any) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def build_id() -> str:
    """Short git commit (+dirty) of the checkout, best-effort for stale-build
    detection (surfaced in /health and the viewer footer)."""
    import subprocess

    try:
        repo = Path(__file__).resolve().parents[3]
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        if rev:
            return rev + ("+dirty" if dirty else "")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return "unknown"


# --------------------------------------------------------------------------- #
# Format helpers (always deferred to core)
# --------------------------------------------------------------------------- #
def _materialize(config: ServeConfig, asset: Asset, tmpdir: str) -> str:
    """Local filesystem path for *asset* (extracting an archive entry if needed)."""
    if asset.origin == "loose":
        return str(config.root / asset.path)
    archive = MpakArchive(str(config.root / (asset.archive or "")))
    try:
        data = archive.read(asset.entry or "")
    finally:
        archive.close()
    dest = Path(tmpdir) / (os.path.basename(asset.entry or "entry.bin"))
    dest.write_bytes(data)
    return str(dest)


def _nif_metadata(path: str) -> dict[str, Any]:
    from ..nifutil import block_types, load, version_bits

    data = load(path)
    version, blocks = version_bits(data)
    nodes: list[dict[str, str]] = []
    textures: set[str] = set()
    for b in data.blocks:
        kind = type(b).__name__
        if kind in ("NiNode",) + SHAPE_TYPES:
            name = b.name.decode("latin1") if getattr(b, "name", None) else ""
            if name:
                nodes.append({"type": kind, "name": name})
        if kind == "NiSourceTexture" and getattr(b, "file_name", None):
            textures.add(b.file_name.decode("latin1"))
    return {
        "version": hex(version),
        "blocks": blocks,
        "block_types": dict(Counter(block_types(data)).most_common()),
        "nodes": nodes[:200],
        "textures": sorted(textures),
    }


def _kfa_metadata(path: str) -> dict[str, Any]:
    from ..kfa import kfa_summary

    summary = kfa_summary(path)
    return {k: v for k, v in summary.items() if k != "path"}


def _metadata(config: ServeConfig, asset: Asset, tmpdir: str) -> dict[str, Any]:
    try:
        source = _materialize(config, asset, tmpdir)
        if asset.type == "nif":
            return _nif_metadata(source)
        if asset.type == "kfa":
            return _kfa_metadata(source)
    except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {}


def _export_model(
    config: ServeConfig, asset: Asset, tmpdir: str, skinned: bool = False
) -> tuple[bytes, bytes]:
    """Export *asset* to (glb_bytes, skin_json_bytes), with auto textures.

    Player base figures go through :func:`camelot.figure.export_figure`, which
    swaps the classic head shells for the Catacombs fig3 head/hair when the
    client archives carry them; every other asset exports directly.
    """
    from ..figure import export_figure

    source = _materialize(config, asset, tmpdir)
    out_prefix = str(Path(tmpdir) / "model")

    export_figure(
        str(config.root),
        source,
        out_prefix,
        verbose=False,
        emit_skinned=skinned,
    )
    gltf = json.loads(Path(out_prefix + ".gltf").read_text(encoding="utf-8"))
    bin_bytes = Path(out_prefix + ".bin").read_bytes()
    skin_path = Path(out_prefix + ".skin.json")
    skin = skin_path.read_bytes() if skin_path.exists() else b"{}"
    return pack_glb(gltf, bin_bytes), skin


def _cached_model(config: ServeConfig, asset: Asset, skinned: bool = False) -> tuple[bytes, bytes]:
    """Export with a disk cache keyed on the source mtime (and skin flag).

    ``_EXPORT_VERSION`` must be bumped whenever the exported glTF changes
    (e.g. the fig3 player-head assembly): the source mtime alone would keep
    serving stale models built by an older exporter.
    """
    out_dir = config.cache_dir / "exports"
    suffix = ".skinned" if skinned else ""
    glb_path = out_dir / f"{asset.id}{suffix}.glb"
    skin_path = out_dir / f"{asset.id}{suffix}.skin.json"
    meta_path = out_dir / f"{asset.id}{suffix}.json"

    meta = read_json(meta_path)
    if (
        isinstance(meta, dict)
        and meta.get("mtime") == asset.mtime
        and meta.get("exporter") == _EXPORT_VERSION
        and glb_path.exists()
        and skin_path.exists()
    ):
        return glb_path.read_bytes(), skin_path.read_bytes()

    with tempfile.TemporaryDirectory(prefix="camelot-serve-") as tmpdir:
        glb, skin = _export_model(config, asset, tmpdir, skinned=skinned)
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path.write_bytes(glb)
    skin_path.write_bytes(skin)
    write_json(
        meta_path,
        {
            "mtime": asset.mtime,
            "size": asset.size,
            "version": __version__,
            "exporter": _EXPORT_VERSION,
        },
    )
    return glb, skin


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def build_router(config: ServeConfig, index: AssetIndex,
                 watcher: IndexWatcher | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/character/{race_id}/{female}/options", response_model=CharacterOptions)
    def character_options(race_id: int, female: bool) -> CharacterOptions:
        """The appearance-picker shape of one (race, gender) — counts straight
        from the client's `fig3descriptions`/`fig3*colormap` tables."""
        from .. import appearance as app
        from .. import fig3

        tables = fig3.read_tables(str(config.root))
        desc = tables.race_desc.get(race_id)
        if desc is None or ("female" in desc.lower()) != female:
            raise HTTPException(status_code=404, detail="unknown race")
        hair_styles = len(fig3.slot_ids(tables, race_id, "Hair"))
        cloak_variants = len(fig3.slot_ids(tables, race_id, "Cloak"))
        body_variants = len(fig3.slot_ids(tables, race_id, "Body"))
        triplets = app._hair_triplets(str(config.root), race_id, 1)
        face_row = app._facemap(str(config.root)).get(race_id, [])
        faces = max(1, len([c for c in face_row[2:] if c.strip()])) if face_row else 1
        skin_row = app._skincolormap(str(config.root)).get(race_id, None)
        skin_tones = len([c for c in skin_row[2:] if c.strip()]) if skin_row else 0
        eye_row = app._eyecolormap(str(config.root)).get(race_id, None)
        eye_colors = (
            len([c for c in eye_row[2:] if c.strip()]) if eye_row else 0
        )
        decals = app.decal_ids(str(config.root), race_id)
        return CharacterOptions(
            race=desc,
            race_id=race_id,
            female=female,
            faces=faces,
            hair_styles=hair_styles,
            hair_colors=len(triplets) or 0,
            eye_colors=eye_colors,
            skin_tones=skin_tones,
            has_decal=bool(decals),
            cloak_variants=cloak_variants,
            body_variants=body_variants,
        )

    @router.get("/character/{race_id}/{female}/model")
    def character_model(
        race_id: int,
        female: bool,
        face: int = 1,
        hair_style: int = 1,
        hair_color: int = 1,
        eye_color: int = 1,
        skin_tone: int = 1,
        cloak: int = 0,
        skinned: bool = False,
    ) -> Response:
        """A preview glb of a player figure assembled with the appearance
        picked: rig from the client sfig archives; pieces by slot; sheets
        from the colormaps; textures through pskins (§8.15 doctrine).

        ``skinned=true`` emits joints/weights so the viewer can play the
        figure's idle (`/character/…/anim`)."""
        import tempfile
        from pathlib import Path as _Path

        from .. import fig3
        from ..gltf import export_gltf
        from ..playerfig import build as build_figure

        tables = fig3.read_tables(str(config.root))
        desc = tables.race_desc.get(race_id)
        if desc is None or ("female" in desc.lower()) != female:
            raise HTTPException(status_code=404, detail="unknown race")
        appearance = {
            "face": face,
            "hair_style": hair_style,
            "hair_color": hair_color,
            "eye_color": eye_color,
            "skin_tone": skin_tone,
            "cloak": cloak,
        }
        try:
            prepared = build_figure(str(config.root), desc, **appearance)
        except (MpakError, OSError, ValueError, AttributeError, KeyError, IndexError):
            prepared = None
        if prepared is None:
            raise HTTPException(status_code=500, detail="assembly failed")
        data, textures = prepared
        with tempfile.TemporaryDirectory() as tmpdir:
            out = str(_Path(tmpdir) / "preview")
            export_gltf(
                "character-preview.nif", out, data=data, textures=textures,
                emit_skinned=skinned,
            )
            glb_doc = json.loads(Path(out + ".gltf").read_text())
            glb_bin = Path(out + ".bin").read_bytes()
        return Response(content=pack_glb(glb_doc, glb_bin),
                            media_type="model/gltf-binary")

    @router.get("/character/{race_id}/{female}/anim")
    def character_anim(race_id: int, female: bool) -> Response:
        """The player idle of (race, gender) as viewer tracks.

        The clip is resolved through the client's tables — the Catacombs
        figure of the race (`sar_f` & co) first: those sets carry the live
        client's player poses, and it is served **engine-faithful**: every
        channel the clip carries is applied absolute (rotations and
        translations), the way the client overwrites its node transforms.
        Partial fixes (dropping "static" channels, rebasing single bones)
        broke the pose chain — see notes/character-rules.md and the skill."""
        import tempfile
        from pathlib import Path as _Path

        from .. import fig3, gamedata
        from ..anims import animation_tracks, resolve_idle_kfa
        from ..playerfig import rig_bytes

        tables = fig3.read_tables(str(config.root))
        desc = tables.race_desc.get(race_id)
        if desc is None or ("female" in desc.lower()) != female:
            raise HTTPException(status_code=404, detail="unknown race")
        name = fig3.race_short(tables, race_id, female) or gamedata.figure_name_for_desc(
            str(config.root), desc
        )
        if name is None:
            raise HTTPException(status_code=404, detail="no player figure for this race")
        virtual = os.path.join(str(config.root), "figures", name + ".NIF")
        kfa_path = resolve_idle_kfa(str(config.root), virtual)
        if kfa_path is None:
            raise HTTPException(status_code=404, detail="no idle animation for this race")
        raw = rig_bytes(str(config.root), race_id, female)
        if raw is None:
            raise HTTPException(status_code=404, detail="no runtime rig for this race")
        with tempfile.TemporaryDirectory() as tmpdir:
            rig_path = str(_Path(tmpdir) / f"{name}_skeleton.nif")
            Path(rig_path).write_bytes(raw)
            try:
                tracks = animation_tracks(
                    rig_path, kfa_path, str(config.root), prefer_table=True,
                    foreign_rig_gate=True,
                )
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=500, detail=f"anim failed: {exc}") from exc
        if not tracks["tracks"]:
            raise HTTPException(status_code=404, detail="no idle animation for this race")
        return Response(content=json.dumps(tracks), media_type="application/json")

    @router.get("/character/{race_id}/{female}/texture")
    def character_texture(
        race_id: int,
        female: bool,
        kind: str,
        face: int = 1,
        hair_style: int = 1,
        hair_color: int = 1,
        eye_color: int = 1,
        skin_tone: int = 1,
    ) -> Response:
        """Fine-grained appearance update: return the *texture bytes* of the
        one slot the change affects (with its engine tint in
        ``X-Camelot-Tint`` when the sheet is tinted), so the web builder can
        swap it live (DAoC-Tools behaviour: only the hair/face texture
        re-forms on a colour change, never the whole model). Kinds that
        change geometry — face / hair_style — fall back to a full rebuild
        client-side."""
        from .. import appearance as app
        from ..dxt import viewer_dds

        def sheet_response(name: str, raw: bytes, tint) -> Response:
            headers = {}
            if tint is not None:
                headers["X-Camelot-Tint"] = ",".join(str(channel) for channel in tint)
            return Response(content=viewer_dds(raw), media_type="image/x-dds", headers=headers)

        root = str(config.root)
        if kind == "hair_color":
            triplet = app._hair_triplets(root, race_id, hair_style)
            cell = triplet[min(max(hair_color, 1), len(triplet)) - 1]
            out = app.hair_composite(root, cell[0], cell[2])
            if out:
                tint = app.hair_tint_rgb(root, race_id, hair_style, hair_color)
                return sheet_response(out[0], out[1], tint)
        if kind == "skin_tone":
            # The tone is a pure tint: the face sheet re-serves unchanged.
            found = app.face_sheet(root, race_id, face)
            if found:
                tint = app.skin_tint_rgb(root, race_id, skin_tone)
                return sheet_response(found[0], found[1], tint)
        if kind == "eye_color":
            found = app.eye_composite(root, race_id, face, eye_color)
            if found:
                return sheet_response(found[0], found[1], None)
        raise HTTPException(
            status_code=501,
            detail="this kind triggers a full rebuild (geometry change)",
        )

    @router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=__version__, root=str(config.root), build=build_id())

    @router.get("/root", response_model=RootInfo)
    def root_info() -> RootInfo:
        return RootInfo(
            root=str(config.root),
            total=len(index.assets),
            archives=index.archives,
            errors=index.errors,
            scanned_at=index.scanned_at,
            counts=index.counts(),
            dirs=index.top_dirs(),
        )

    @router.get("/assets", response_model=AssetPage)
    def list_assets(
        type: str | None = None,
        q: str | None = None,
        dir: str | None = None,
        sort: str = "path",
        page: int = Query(1, ge=1),
        limit: int = Query(100, ge=1, le=1000),
    ) -> AssetPage:
        items, total = index.query(type=type, q=q, dir=dir, sort=sort, page=page, limit=limit)
        return AssetPage(
            items=[AssetSummary(**vars(a)) for a in items],
            total=total,
            page=page,
            limit=limit,
        )

    @router.get("/assets/{asset_id}", response_model=AssetDetail)
    def asset_detail(asset_id: str) -> AssetDetail:
        asset = index.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        with tempfile.TemporaryDirectory(prefix="camelot-serve-") as tmpdir:
            meta = _metadata(config, asset, tmpdir)
        return AssetDetail(**vars(asset), metadata=meta)

    @router.get("/assets/{asset_id}/skin")
    def asset_skin(asset_id: str) -> Response:
        asset = index.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        if asset.type != "nif":
            raise HTTPException(status_code=400, detail="skin is only available for nif assets")
        _glb, skin = _cached_model(config, asset)
        return Response(content=skin, media_type="application/json")

    @router.get("/assets/{asset_id}/model")
    def asset_model(asset_id: str, skinned: bool = False) -> Response:
        asset = index.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        if asset.type != "nif":
            raise HTTPException(status_code=400, detail="model export is only available for nif assets")
        try:
            glb, _skin = _cached_model(config, asset, skinned=skinned)
        except (MpakError, OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"export failed: {exc}") from exc
        return Response(content=glb, media_type="model/gltf-binary")

    @router.get("/assets/{asset_id}/anim")
    def asset_anim(asset_id: str) -> Response:
        """Idle animation rotation tracks for a figure (auto-resolved KFA)."""
        from ..anims import animation_tracks, resolve_idle_kfa

        asset = index.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        if asset.type != "nif":
            raise HTTPException(status_code=400, detail="anim is only available for nif assets")
        with tempfile.TemporaryDirectory(prefix="camelot-serve-") as tmpdir:
            try:
                source = _materialize(config, asset, tmpdir)
                kfa_path = resolve_idle_kfa(str(config.root), source)
            except (MpakError, OSError) as exc:
                raise HTTPException(status_code=500, detail=f"anim failed: {exc}") from exc
            if kfa_path is None:
                raise HTTPException(status_code=404, detail="no idle animation for this figure")
            try:
                tracks = animation_tracks(source, kfa_path, str(config.root))
            except (OSError, ValueError) as exc:
                raise HTTPException(status_code=500, detail=f"anim failed: {exc}") from exc
            if not tracks["tracks"]:
                raise HTTPException(status_code=404, detail="no idle animation for this figure")
        return Response(content=json.dumps(tracks), media_type="application/json")

    @router.get("/events")
    async def events(request: Request) -> StreamingResponse:
        if watcher is None:
            raise HTTPException(status_code=404, detail="event stream is disabled")

        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        watcher.subscribe(queue)

        async def stream():
            yield "event: ready\ndata: {}\n\n"
            try:
                while not await request.is_disconnected():
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield sse_frame(event, data)
            finally:
                watcher.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
