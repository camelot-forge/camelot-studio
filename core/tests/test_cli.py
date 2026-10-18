"""Unit tests for the CLI helpers (path/env resolution)."""

import subprocess
import sys

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

def test_cli_mpak_repack_recomputes_the_header(tmp_path):
    """Repacking is the mod path: the header carriers must describe the
    streams actually written. Preserving them (while they were undecoded)
    shipped archives the client refused — it reads the stream lengths from
    the header, not from the streams."""
    import zlib

    from camelot.cli import main
    from camelot.mpak import MpakArchive, decode_header, write_mpak

    src = tmp_path / "src.mpk"
    write_mpak(str(src), "gamedata.mpk", [("a.csv", b"A"), ("b.csv", b"B")])

    replacement = tmp_path / "a.csv"
    replacement.write_bytes(b"REPLACED" * 100)
    out = tmp_path / "out.mpk"
    assert main(["mpak", "repack", str(src), str(out),
                 "--replace", f"a.csv={replacement}"]) == 0

    blob = out.read_bytes()
    pos = 21
    d = zlib.decompressobj()
    d.decompress(blob[pos:])
    s1 = len(blob) - pos - len(d.unused_data)
    pos += s1
    d = zlib.decompressobj()
    d.decompress(blob[pos:])
    s2 = len(blob) - pos - len(d.unused_data)
    head = decode_header(blob[:21])
    assert head["name_comp_len"] == s1
    assert head["dir_comp_len"] == s2
    assert head["dir_crc32"] == zlib.crc32(blob[pos:pos + s2]) & 0xFFFFFFFF
    assert head["num_entries"] == 2

    after = MpakArchive(str(out))
    try:
        assert after.read("a.csv") == b"REPLACED" * 100
        assert after.read("b.csv") == b"B"
    finally:
        after.close()


def test_the_server_needs_no_client_reader_and_no_daemon():
    """`deploy/server/Dockerfile` runs this module for `env apply` (S §D10), so the
    server's verbs must import neither the client's format code (`pyffi` and the
    readers built on it) nor the daemon's (`fastapi`, and the `serve.*` modules
    behind it): a server writes rows — it has no client to parse and no page to
    serve.

    In a subprocess, because what a module imports at runtime is the only honest
    answer, and because every other test in this process may have read a client or
    built a daemon already.
    """
    code = (
        "import sys; import camelot.cli, camelot.envs;"
        "heavy = ('pyffi', 'fastapi', 'uvicorn', 'watchfiles');"
        "print(','.join(sorted(m for m in sys.modules if m.split('.')[0] in heavy)))"
    )
    found = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert found.stdout.strip() == "", (
        f"the server's verbs now import {found.stdout.strip()} — the job container "
        "would need a client reader or the daemon to apply a release"
    )
