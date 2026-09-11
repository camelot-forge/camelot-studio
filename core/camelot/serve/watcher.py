"""Filesystem watcher: rescans the index and broadcasts change events (SSE).

Rescans run in a worker thread so the event loop stays responsive while the
(possibly large) client directory is re-read. Granular per-asset events are
emitted for small diffs; large diffs only emit an aggregate event to avoid
flooding subscribers.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from .config import ServeConfig
from .index import AssetIndex

MAX_PER_ASSET_EVENTS = 100


class IndexWatcher:
    def __init__(self, index: AssetIndex, config: ServeConfig):
        self.index = index
        self.config = config
        self._subscribers: set[asyncio.Queue] = set()
        self._stop = threading.Event()

    # -- pub/sub ------------------------------------------------------------
    def subscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: str, data: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:  # slow consumer: drop, it will refetch
                pass

    # -- lifecycle ----------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        from watchfiles import awatch

        async for _changes in awatch(
            self.config.root, stop_event=self._stop, recursive=True, debounce=500
        ):
            await self.apply_changes()

    # -- change handling ----------------------------------------------------
    async def apply_changes(self) -> None:
        before = {a.id: (a.mtime, a.size) for a in self.index.assets}
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: self.index.scan(use_cache=False))
        after = {a.id: (a.mtime, a.size) for a in self.index.assets}

        added = [i for i in after if i not in before]
        removed = [i for i in before if i not in after]
        changed = [i for i in after if i in before and after[i] != before[i]]

        self.publish("index.changed", {
            "added": len(added), "removed": len(removed),
            "changed": len(changed), "total": len(after),
        })
        small = len(added) + len(removed) + len(changed) <= MAX_PER_ASSET_EVENTS
        if small:
            for i in added:
                self.publish("asset.added", {"id": i})
            for i in removed:
                self.publish("asset.removed", {"id": i})
            for i in changed:
                self.publish("asset.changed", {"id": i})
