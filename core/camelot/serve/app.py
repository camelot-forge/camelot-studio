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
from .editor import Editor
from .home import build_home_router
from .index import AssetIndex
from .security import host_allowed, origin_allowed
from .session import Session
from .watcher import IndexWatcher
from .world import World, build_world_router


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

    # One world and one editor for every router: Home reads the same session
    # the world editor writes (`home.md` §1), and the switch re-points them
    # here and nowhere else. The session owns the shared config and the lock.
    world = World(config)
    world.editor = Editor(config)
    session = Session(config, index, watcher, world)
    app.state.session = session

    app.include_router(build_router(config, index, watcher))
    app.include_router(build_world_router(config, world))
    app.include_router(build_home_router(config, world, session))

    static_dir = config.static_dir
    if static_dir and Path(static_dir).exists():
        root = Path(static_dir)
        index = root / "index.html"

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            """Serve static files, falling back to index.html (SPA routes).

            The *page* is never cached: `index.html` names hashed bundles, and
            a browser that keeps it runs an old build for as long as it likes
            (the operator's Firefox did exactly that, 2026-10-12 — the panel
            now stamps the build, and this is the fix). The hashed files
            themselves are content-addressed and keep their caching.

            An **unknown API path** is answered as JSON, never by the page: a
            route this build does not have is a fact about the daemon, and a
            client that gets `index.html` where it expected JSON shows the
            browser's own parse error instead of the reason (measured
            2026-10-17: a page pointed at an older daemon read "JSON.parse:
            unexpected character at line 1 column 1").
            """
            if full_path.startswith("api/"):
                return JSONResponse({"detail": f"no such route: /{full_path}"}, status_code=404)
            candidate = root / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index, headers={"cache-control": "no-store"})

    return app
