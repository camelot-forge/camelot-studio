"""Unit tests for the serve asset index (synthetic client, no game files)."""
from camelot.mpak import write_mpak
from camelot.serve.index import AssetIndex, _decode_id, make_id


def _client(tmp_path):
    root = tmp_path / "client"
    (root / "figures").mkdir(parents=True)
    (root / "anims").mkdir()
    (root / "figures" / "Boar.NIF").write_bytes(b"nif-bytes")
    (root / "anims" / "BOAR_idle.kfa").write_bytes(b"kfa-bytes")
    (root / "figures" / "notes.txt").write_bytes(b"ignored")
    write_mpak(
        str(root / "figures" / "skin001.mpk"),
        "skin001",
        [("boar.dds", b"dds"), ("readme.txt", b"text")],
    )
    return root


class TestMakeId:
    def test_roundtrip_loose(self):
        assert _decode_id(make_id("loose", "figures/Boar.NIF")) == "loose:figures/Boar.NIF"

    def test_roundtrip_archive_entry(self):
        token = make_id("mpak", "figures/skin001.mpk!boar.dds")
        assert _decode_id(token) == "mpak:figures/skin001.mpk!boar.dds"

    def test_id_is_url_safe_single_segment(self):
        token = make_id("loose", "figures/Boar.NIF")
        assert "/" not in token and "+" not in token and "=" not in token


class TestScan:
    def test_counts_types_and_archives(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache")
        n = index.scan(use_cache=False, save_cache=False)
        assert n == 4  # Boar.NIF, BOAR_idle.kfa, boar.dds, readme.txt
        counts = index.counts()
        assert counts == {"dds": 1, "entry": 1, "kfa": 1, "nif": 1}
        assert index.archives == 1
        assert index.errors == 0

    def test_archive_entries_carry_archive_and_entry(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache")
        index.scan(use_cache=False, save_cache=False)
        dds = next(a for a in index.assets if a.type == "dds")
        assert dds.origin == "mpak"
        assert dds.entry == "boar.dds"
        assert dds.archive == "figures/skin001.mpk"

    def test_skip_archives_flag(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache", include_archives=False)
        index.scan(use_cache=False, save_cache=False)
        assert index.archives == 1
        assert all(a.origin == "loose" for a in index.assets)


class TestQuery:
    def test_filter_by_type_and_search(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache")
        index.scan(use_cache=False, save_cache=False)
        items, total = index.query(type="nif")
        assert total == 1 and items[0].name == "Boar.NIF"
        items, total = index.query(q="boar")
        assert total == 3  # Boar.NIF, BOAR_idle.kfa, boar.dds
        items, total = index.query(dir="anims")
        assert total == 1 and items[0].type == "kfa"

    def test_pagination(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache")
        index.scan(use_cache=False, save_cache=False)
        page1, total = index.query(page=1, limit=2)
        page2, _ = index.query(page=2, limit=2)
        assert total == 4 and len(page1) == 2 and len(page2) == 2
        assert {a.id for a in page1}.isdisjoint({a.id for a in page2})

    def test_get_by_id(self, tmp_path):
        index = AssetIndex(_client(tmp_path), tmp_path / "cache")
        index.scan(use_cache=False, save_cache=False)
        asset = index.assets[0]
        assert index.get(asset.id) is asset
        assert index.get("does-not-exist") is None


class TestCache:
    def test_second_scan_reads_cache(self, tmp_path):
        root = _client(tmp_path)
        cache_dir = tmp_path / "cache"
        first = AssetIndex(root, cache_dir)
        first.scan(use_cache=False)
        assert (cache_dir / "index.json").exists()

        # Mutating the tree without changing the cache key: cache wins.
        (root / "figures" / "Extra.NIF").write_bytes(b"x")
        second = AssetIndex(root, cache_dir)
        second.scan(use_cache=True)
        assert len(second.assets) == len(first.assets)

    def test_cache_invalidated_by_version(self, tmp_path, monkeypatch):
        root = _client(tmp_path)
        cache_dir = tmp_path / "cache"
        AssetIndex(root, cache_dir).scan(use_cache=False)
        (root / "figures" / "Extra.NIF").write_bytes(b"x")

        import camelot.serve.index as serve_index

        monkeypatch.setattr(serve_index, "__version__", "999.0")
        index = AssetIndex(root, cache_dir)
        index.scan(use_cache=True)
        assert len(index.assets) == 5
