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
from .models import AssetDetail, AssetPage, AssetSummary, Health, RootInfo
from .watcher import IndexWatcher

SHAPE_TYPES = ("NiTriShape", "NiTriStrips")


def sse_frame(event: str, data: Any) -> str:
    """Format one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


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


def _export_model(config: ServeConfig, asset: Asset, tmpdir: str) -> tuple[bytes, bytes]:
    """Export *asset* to (glb_bytes, skin_json_bytes), with auto textures."""
    from ..gltf import export_gltf

    source = _materialize(config, asset, tmpdir)
    out_prefix = str(Path(tmpdir) / "model")

    textures = None
    try:
        from ..gamedata import resolve_figure_skins

        skins = resolve_figure_skins(str(config.root), source)
        if skins:
            textures = list(skins)
    except (MpakError, OSError, ValueError, KeyError, IndexError):
        textures = None

    export_gltf(source, out_prefix, verbose=False, textures=textures)
    gltf = json.loads(Path(out_prefix + ".gltf").read_text(encoding="utf-8"))
    bin_bytes = Path(out_prefix + ".bin").read_bytes()
    skin_path = Path(out_prefix + ".skin.json")
    skin = skin_path.read_bytes() if skin_path.exists() else b"{}"
    return pack_glb(gltf, bin_bytes), skin


def _cached_model(config: ServeConfig, asset: Asset) -> tuple[bytes, bytes]:
    """Export with a disk cache keyed on the source mtime."""
    out_dir = config.cache_dir / "exports"
    glb_path = out_dir / f"{asset.id}.glb"
    skin_path = out_dir / f"{asset.id}.skin.json"
    meta_path = out_dir / f"{asset.id}.json"

    meta = read_json(meta_path)
    if (
        isinstance(meta, dict)
        and meta.get("mtime") == asset.mtime
        and glb_path.exists()
        and skin_path.exists()
    ):
        return glb_path.read_bytes(), skin_path.read_bytes()

    with tempfile.TemporaryDirectory(prefix="camelot-serve-") as tmpdir:
        glb, skin = _export_model(config, asset, tmpdir)
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path.write_bytes(glb)
    skin_path.write_bytes(skin)
    write_json(meta_path, {"mtime": asset.mtime, "size": asset.size, "version": __version__})
    return glb, skin


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
def build_router(config: ServeConfig, index: AssetIndex,
                 watcher: IndexWatcher | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/health", response_model=Health)
    def health() -> Health:
        return Health(status="ok", version=__version__, root=str(config.root))

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
    def asset_model(asset_id: str) -> Response:
        asset = index.get(asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        if asset.type != "nif":
            raise HTTPException(status_code=400, detail="model export is only available for nif assets")
        try:
            glb, _skin = _cached_model(config, asset)
        except (MpakError, OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail=f"export failed: {exc}") from exc
        return Response(content=glb, media_type="model/gltf-binary")

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
