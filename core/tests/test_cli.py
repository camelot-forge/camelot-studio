"""Unit tests for the CLI helpers (path/env resolution)."""

from camelot.cli import resolve_path


class TestResolvePath:
    def test_rewrites_client_prefix(self, monkeypatch):
        monkeypatch.setenv("DAOC_CLIENT", "/games/daoc")
        assert resolve_path("DAOC CLIENT/figures/Boar.NIF") == (
            "/games/daoc/figures/Boar.NIF")

    def test_no_env_no_rewrite(self, monkeypatch):
        monkeypatch.delenv("DAOC_CLIENT", raising=False)
        assert resolve_path("DAOC CLIENT/figures/Boar.NIF") == (
            "DAOC CLIENT/figures/Boar.NIF")

    def test_other_paths_untouched(self, monkeypatch):
        monkeypatch.setenv("DAOC_CLIENT", "/games/daoc")
        assert resolve_path("work/x.NIF") == "work/x.NIF"
        assert resolve_path("/abs/path.nif") == "/abs/path.nif"

    def test_windows_separators_handled_by_callers(self, monkeypatch):
        monkeypatch.setenv("DAOC_CLIENT", "/games/daoc")
        # resolve_path is fed pre-normalized lines; just check prefix logic
        assert resolve_path("DAOC CLIENT\\figures\\x.nif".replace("\\", "/")) == (
            "/games/daoc/figures/x.nif")

def test_cli_mpak_repack_preserves_the_header(tmp_path):
    """Repacking is the mod path, so it must be reachable from the CLI and
    carry the undecoded header field across."""
    from camelot.cli import main
    from camelot.mpak import MpakArchive, write_mpak

    src = tmp_path / "src.mpk"
    write_mpak(str(src), "gamedata.mpk", [("a.csv", b"A"), ("b.csv", b"B")])
    original = MpakArchive(str(src))
    head6 = original.header[5:11]
    original.close()

    replacement = tmp_path / "a.csv"
    replacement.write_bytes(b"REPLACED")
    out = tmp_path / "out.mpk"
    assert main(["mpak", "repack", str(src), str(out),
                 "--replace", f"a.csv={replacement}"]) == 0

    after = MpakArchive(str(out))
    try:
        assert after.header[5:11] == head6
        assert after.read("a.csv") == b"REPLACED"
        assert after.read("b.csv") == b"B"
    finally:
        after.close()
