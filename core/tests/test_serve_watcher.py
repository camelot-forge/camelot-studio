"""Unit tests for the index watcher (synthetic client, no real watching)."""
import asyncio
import contextlib
import time

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


class TestTheWatchFollowsTheRoot:
    """The two lifecycle calls a project switch needs (`home.md` §D3.4): the
    watch in progress must end, `run()` must start the next one on the root the
    config names *now*, and a shutdown must still stop everything."""

    def test_a_switch_ends_the_watch_and_run_keeps_going(self, tmp_path):
        config, index = _setup(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        (other / "Fresh.NIF").write_bytes(b"f")

        async def scenario():
            watcher = IndexWatcher(index, config)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.3)  # the first watch is up
            config.root = other
            watcher.follow_new_root()
            # the new root replaces the old one: the scan follows the config
            index.repoint(other)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if {a.name for a in index.assets} == {"Fresh.NIF"}:
                    break
                await asyncio.sleep(0.2)
            alive_after_switch = not task.done()
            watcher.request_stop()
            await asyncio.wait_for(task, timeout=10)
            return alive_after_switch, {a.name for a in index.assets}

        alive, names = asyncio.run(scenario())
        assert alive, "run() returned when the switch ended the watch"
        assert names == {"Fresh.NIF"}

    def test_stop_ends_a_watch_in_progress(self, tmp_path):
        config, index = _setup(tmp_path)

        async def scenario():
            watcher = IndexWatcher(index, config)
            task = asyncio.create_task(watcher.run())
            await asyncio.sleep(0.3)
            watcher.request_stop()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(task, timeout=10)
            return task.done()

        assert asyncio.run(scenario()) is True


def test_the_index_is_only_about_one_root_at_a_time(tmp_path):
    """`AssetIndex.repoint` is what a switch calls (`home.md` §D3.4): the rows
    are re-read from the new root, in place, and nothing of the old one is left
    behind."""
    _config, index = _setup(tmp_path)
    assert [a.name for a in index.assets] == ["Boar.NIF"]
    other = tmp_path / "other"
    (other / "figures").mkdir(parents=True)
    (other / "figures" / "Fresh.NIF").write_bytes(b"f")

    assert index.repoint(other) == 1
    assert index.root == other.resolve()
    assert [a.name for a in index.assets] == ["Fresh.NIF"]
    assert index.get(index.assets[0].id) is not None
