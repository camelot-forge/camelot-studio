"""FastAPI application factory for ``camelot serve``."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__
from .api import build_router
from .config import ServeConfig
from .index import AssetIndex
from .security import host_allowed, origin_allowed
from .watcher import IndexWatcher
from .world import build_world_router


def create_app(config: ServeConfig) -> FastAPI:
    """Build the daemon app: index, API router, guard, static viewer."""
    index = AssetIndex(config.root, config.cache_dir)
    index.scan()
    watcher = IndexWatcher(index, config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(watcher.run()) if config.watch else None
        try:
            yield
        finally:
            watcher.request_stop()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="camelot serve", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", lifespan=lifespan)

    app.state.config = config
    app.state.index = index
    app.state.watcher = watcher
    app.state.extra_hosts = (config.host,)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        extra = app.state.extra_hosts
        if not host_allowed(request.headers.get("host"), extra):
            return JSONResponse({"detail": "invalid host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and not origin_allowed(origin, extra):
            return JSONResponse({"detail": "invalid origin"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            # The daemon is a *live* view of the client: a mesh exported with a
            # different axis conversion keeps the same URL, so without this a
            # browser may serve the old one and hide the change (measured
            # 2026-10-12, the world axis fix). Revalidate every time; the
            # static viewer build keeps its normal caching.
            response.headers["cache-control"] = "no-cache"
        return response

    app.include_router(build_router(config, index, watcher))
    app.include_router(build_world_router(config))

    static_dir = config.static_dir
    if static_dir and Path(static_dir).exists():
        root = Path(static_dir)
        index = root / "index.html"

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            """Serve static files, falling back to index.html (SPA routes)."""
            candidate = root / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app
