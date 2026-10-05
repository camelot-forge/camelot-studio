"""Packaging: turning the contract, artifacts and projection into one
deployable unit.

A pack is what someone actually deploys. It must be self-describing (a
reader can tell what it is and for which build), complete (the archive's
own contents are hashed), and refuse to build from an unpublishable
contract.
"""
import json
import tarfile

from camelot import assemble


def _manifest(tmp_path, **kw):
    from camelot import contract

    return contract.Manifest(pack_id="test.pack", version="0.1.0", **kw)


def test_a_pack_names_itself_and_its_client_build():
    """A pack is only valid for the client build it was generated against,
    so the identifier travels inside it rather than in someone's notes."""
    manifest = _manifest(None)
    manifest.client_build = "1.130"
    assert assemble.pack_name(manifest) == "test.pack-0.1.0-1.130"


def test_pack_name_survives_a_missing_client_build():
    assert assemble.pack_name(_manifest(None)) == "test.pack-0.1.0-unknown"


def test_assembling_writes_a_tarball_with_the_contract_inside(tmp_path):
    manifest = _manifest(None)
    manifest.client_build = "1.130"
    out = tmp_path / "pack.tar.gz"
    assemble.archive(manifest, str(out), files={"manifest/content.json": b"{}"})
    assert out.exists()
    with tarfile.open(out) as tar:
        names = tar.getnames()
    assert "manifest/content.json" in names
    assert "PACK.json" in names, "the archive must describe itself"


def test_the_archive_is_reproducible(tmp_path):
    """Same input, same bytes: an archive whose mtime or member order drifts
    cannot be checked against its own lock."""
    a, b = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    payload = {"manifest/content.json": b'{"x":1}', "artifacts/a.glb": b"glTF"}
    assemble.archive(_manifest(None), str(a), files=payload)
    assemble.archive(_manifest(None), str(b), files=payload)
    assert a.read_bytes() == b.read_bytes()


def test_the_lock_covers_every_member_including_the_metadata(tmp_path):
    out = tmp_path / "pack.tar.gz"
    payload = {"manifest/content.json": b'{"x":1}'}
    lock = assemble.archive(_manifest(None), str(out), files=payload)
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    assert set(lock) == names, "a member outside the lock is unverifiable"
    for digest in lock.values():
        assert len(digest) == 64


def test_the_archive_carries_no_client_bytes():
    """The pack ships Studio-authored content and a contract; never the
    retail client's files."""
    manifest = _manifest(None)
    doc = json.loads(assemble.pack_metadata(manifest).decode("utf-8"))
    assert doc["pack"]["id"] == "test.pack"
    assert doc["schema_version"]
    assert "base64" not in json.dumps(doc)


def test_a_pure_python_tarball_needs_no_external_tool(tmp_path):
    """gzip embeds the mtime by default, which would make every build differ
    from the last; the archive must pin it."""
    out = tmp_path / "p.tar.gz"
    assemble.archive(_manifest(None), str(out), files={"a": b"x"})
    with tarfile.open(out) as tar:
        for member in tar.getmembers():
            assert member.mtime == 0, f"{member.name} carries a timestamp"


def test_a_target_that_cannot_express_a_figure_records_it_instead_of_failing(tmp_path):
    """A pack is still valid for the figures a target understands. An object
    the OpenDAoC projection cannot place yet must appear as unprojected in
    its own file -- a silent omission is exactly what nobody notices."""
    import json

    from camelot import contract
    from camelot.cli import write_targets

    manifest = contract.Manifest(pack_id="p", version="1")
    manifest.figures.append(contract.Figure(
        logical_id="daoc.custom.sword", figure_id=0, kind="object",
        name="Broken Sword", rig="sword",
    ))
    write_targets(manifest, str(tmp_path), targets=["opendaoc"])
    doc = json.loads((tmp_path / "targets" / "opendaoc" / "figures.json").read_text())
    assert doc["figures"] == []
    assert [u["logical_id"] for u in doc["unprojected"]] == ["daoc.custom.sword"]
    assert "only player figures" in doc["unprojected"][0]["reason"]
