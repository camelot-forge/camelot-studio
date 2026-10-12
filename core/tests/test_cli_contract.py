"""The `camelot pack` command (Phase 3): an offline, reproducible build."""
import json

import pytest

from camelot.cli import main


@pytest.fixture(scope="session")
def cli_pack(client_dir, tmp_path_factory):
    """One full `camelot contract` run per session (~45 s), artifacts and all.

    Three tests assert on what a real build *wrote*; the build is the whole
    cost, so they share this one. The CLI's own behaviour reports (exit codes,
    refusals, the quick `--figures 2` run) keep their own invocations.
    """
    out = tmp_path_factory.mktemp("cli-pack") / "pack"
    assert main([
        "contract", "--client", str(client_dir), "--out", str(out),
        "--id", "test.pack", "--version", "0.1.0",
    ]) == 0
    return out


def test_pack_writes_a_manifest_and_a_report(cli_pack):
    doc = json.loads((cli_pack / "manifest" / "content.json").read_text())
    assert doc["pack"]["id"] == "test.pack"
    assert doc["schema_version"]
    assert len(doc["figures"]) == 36
    report = json.loads((cli_pack / "reports" / "validation.json").read_text())
    assert report["ok"] is True


def test_pack_is_reproducible_byte_for_byte(cli_pack, client_dir, tmp_path):
    """Two runs of the command must write the same manifest bytes; one of the
    two is the session's build."""
    out = tmp_path / "fresh"
    assert main([
        "contract", "--client", str(client_dir), "--out", str(out),
        "--id", "test.pack", "--version", "0.1.0",
    ]) == 0
    fresh = (out / "manifest" / "content.json").read_bytes()
    session = (cli_pack / "manifest" / "content.json").read_bytes()
    assert fresh == session


def test_pack_records_artifact_hashes(cli_pack):
    doc = json.loads((cli_pack / "manifest" / "content.json").read_text())
    assert doc["artifacts"]
    lock = (cli_pack / "checksums.lock").read_text()
    for name, digest in doc["artifacts"].items():
        assert name in lock
        assert digest in lock


def test_pack_refuses_to_publish_when_strict(client_dir, tmp_path, monkeypatch):
    """An unresolved reference must stop the build, not ship a guess."""
    from camelot import contract

    real = contract.build_player_manifest

    def broken(client, **kwargs):
        manifest = real(client, **kwargs)
        manifest.unresolved.append(contract.Unresolved(ref="x:y", reason="test gap"))
        return manifest

    monkeypatch.setattr(contract, "build_player_manifest", broken)
    out = tmp_path / "pack"
    code = main([
        "contract", "--client", str(client_dir), "--out", str(out),
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
        "contract", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1", "--dry-run",
    ]) == 0
    assert not (out / "manifest" / "content.json").exists()
    assert json.loads((out / "reports" / "validation.json").read_text())["ok"] is True


def test_pack_fails_without_a_client(tmp_path):
    code = main([
        "contract", "--client", str(tmp_path / "nope"), "--out", str(tmp_path / "o"),
        "--id", "p", "--version", "1",
    ])
    assert code != 0


def test_contract_accepts_a_user_supplied_figure(client_dir, tmp_path):
    """The authoring path: a user adds their own content to the contract
    with `--add-figure`, and it lands beside the built-in rows with its own
    logical id and no renumbering."""
    from camelot.cli import main

    out = tmp_path / "pack"
    code = main([
        "contract", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1", "--no-states", "--no-artifacts",
        "--add-figure", "daoc.custom.broken_sword:object:broken_sword",
    ])
    assert code == 0
    doc = json.loads((out / "manifest" / "content.json").read_text())
    ids = [f["logical_id"] for f in doc["figures"]]
    assert "daoc.custom.broken_sword" in ids
    assert len(ids) == 37
    assert "daoc.player.tro_m" in ids, "the built-in rows survived"


def test_contract_rejects_a_malformed_add_figure(client_dir, tmp_path):
    from camelot.cli import main

    code = main([
        "contract", "--client", str(client_dir), "--out", str(tmp_path / "p"),
        "--id", "p", "--version", "1", "--no-states", "--no-artifacts",
        "--add-figure", "not-namespaced",
    ])
    assert code != 0


def test_contract_can_emit_artifacts(client_dir, tmp_path):
    from camelot.cli import main

    out = tmp_path / "pack"
    code = main([
        "contract", "--client", str(client_dir), "--out", str(out),
        "--id", "p", "--version", "1", "--no-states",
        "--figures", "2",  # just Briton female, to keep the test quick
    ])
    assert code == 0
    doc = json.loads((out / "manifest" / "content.json").read_text())
    glbs = {n: d for n, d in doc["artifacts"].items() if n.endswith(".glb")}
    assert glbs, "no glTF artifact was hashed"
    lock = (out / "checksums.lock").read_text()
    for name, digest in glbs.items():
        assert name in lock and digest in lock
        assert (out / name).read_bytes()[:4] == b"glTF", name
    # the figure points at its own artifact
    figure = next(f for f in doc["figures"] if f["figure_id"] == 2)
    assert figure["artifacts"]["model"].endswith(".glb")
