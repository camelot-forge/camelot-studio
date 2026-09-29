"""The `camelot pack` command (Phase 3): an offline, reproducible build."""
import json

from camelot.cli import main


def test_pack_writes_a_manifest_and_a_report(client_dir, tmp_path):
    out = tmp_path / "pack"
    code = main([
        "pack", "--client", str(client_dir),
        "--out", str(out), "--id", "test.pack", "--version", "0.1.0",
    ])
    assert code == 0
    doc = json.loads((out / "manifest" / "content.json").read_text())
    assert doc["pack"]["id"] == "test.pack"
    assert doc["schema_version"]
    assert len(doc["figures"]) == 36
    report = json.loads((out / "reports" / "validation.json").read_text())
    assert report["ok"] is True


def test_pack_is_reproducible_byte_for_byte(client_dir, tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        assert main([
            "pack", "--client", str(client_dir), "--out", str(out),
            "--id", "p", "--version", "1",
        ]) == 0
    a = (first / "manifest" / "content.json").read_bytes()
    b = (second / "manifest" / "content.json").read_bytes()
    assert a == b


def test_pack_records_artifact_hashes(client_dir, tmp_path):
    out = tmp_path / "pack"
    assert main([
        "pack", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1",
    ]) == 0
    doc = json.loads((out / "manifest" / "content.json").read_text())
    assert doc["artifacts"]
    lock = (out / "checksums.lock").read_text()
    for name, digest in doc["artifacts"].items():
        assert name in lock
        assert digest in lock


def test_pack_refuses_to_publish_when_strict(client_dir, tmp_path, monkeypatch):
    """An unresolved reference must stop the build, not ship a guess."""
    from camelot import pack

    real = pack.build_player_pack

    def broken(client, **kwargs):
        manifest = real(client, **kwargs)
        manifest.unresolved.append(pack.Unresolved(ref="x:y", reason="test gap"))
        return manifest

    monkeypatch.setattr(pack, "build_player_pack", broken)
    out = tmp_path / "pack"
    code = main([
        "pack", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1",
    ])
    assert code != 0
    # the report is still written so the failure is inspectable
    report = json.loads((out / "reports" / "validation.json").read_text())
    assert report["ok"] is False


def test_pack_can_report_without_writing_a_manifest(client_dir, tmp_path):
    """`--dry-run` reports and writes no contract, for a partial corpus."""
    out = tmp_path / "pack"
    assert main([
        "pack", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1", "--dry-run",
    ]) == 0
    assert not (out / "manifest" / "content.json").exists()
    assert json.loads((out / "reports" / "validation.json").read_text())["ok"] is True


def test_pack_fails_without_a_client(tmp_path):
    code = main([
        "pack", "--client", str(tmp_path / "nope"), "--out", str(tmp_path / "o"),
        "--id", "p", "--version", "1",
    ])
    assert code != 0
