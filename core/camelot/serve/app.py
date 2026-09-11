"""FastAPI application factory for ``camelot serve``."""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from .api import build_router
from .config import ServeConfig
from .index import AssetIndex
from .security import host_allowed, origin_allowed
from .watcher import IndexWatcher


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
        return await call_next(request)

    app.include_router(build_router(config, index, watcher))

    static_dir = config.static_dir
    if static_dir and Path(static_dir).exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
