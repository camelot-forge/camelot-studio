"""Unit tests for the index watcher (synthetic client, no real watching)."""
import asyncio

from camelot.serve.config import ServeConfig
from camelot.serve.index import AssetIndex
from camelot.serve.watcher import IndexWatcher


def _setup(tmp_path):
    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True)
    (root / "figures" / "Boar.NIF").write_bytes(b"a")
    config = ServeConfig(root=root, cache_dir=tmp_path / "cache")
    index = AssetIndex(root, config.cache_dir)
    index.scan(use_cache=False)
    return config, index


def _drain(queue):
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


def _run(config, index, mutate):
    watcher = IndexWatcher(index, config)

    async def scenario():
        queue: asyncio.Queue = asyncio.Queue()
        watcher.subscribe(queue)
        mutate()
        await watcher.apply_changes()
        return _drain(queue)

    return asyncio.run(scenario())


class TestApplyChanges:
    def test_added_file(self, tmp_path):
        config, index = _setup(tmp_path)
        events = _run(config, index, lambda: (config.root / "figures" / "New.NIF").write_bytes(b"b"))
        names = [e for e, _ in events]
        assert "index.changed" in names
        assert "asset.added" in names
        aggregate = next(data for e, data in events if e == "index.changed")
        assert aggregate["added"] == 1 and aggregate["total"] == 2

    def test_removed_file(self, tmp_path):
        config, index = _setup(tmp_path)
        events = _run(config, index, lambda: (config.root / "figures" / "Boar.NIF").unlink())
        names = [e for e, _ in events]
        assert "asset.removed" in names
        aggregate = next(data for e, data in events if e == "index.changed")
        assert aggregate["removed"] == 1 and aggregate["total"] == 0

    def test_changed_file(self, tmp_path):
        config, index = _setup(tmp_path)
        path = config.root / "figures" / "Boar.NIF"
        path.write_bytes(b"much-longer-content")
        events = _run(config, index, lambda: None)
        names = [e for e, _ in events]
        assert "asset.changed" in names
