"""Unit tests for the CLI helpers (path/env resolution)."""
import pytest

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