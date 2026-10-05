"""Deployment helpers: what the server records, and how to check it.

The server loads XML packages at startup and writes one `AutoXMLUpdate` row
per file: the package's path relative to `dbupdater`, its hash, and
`LoadResult` SUCCESS or FAILURE. A harness that does not read that row
cannot tell a loaded package from an ignored one -- and the server logs
nothing obvious either way.
"""
import pytest

from camelot import deploy


def test_the_recorded_package_name_is_relative_to_dbupdater():
    """`GetRelativePath(xml, parent-of-insert-dir)` is what the server
    stores, so a check against another spelling would never match."""
    assert deploy.package_record_name("insert/npctemplate.xml") == "insert/npctemplate.xml"
    assert deploy.package_record_name("dbupdater/insert/x.xml") == "insert/x.xml"
    assert deploy.package_record_name("/abs/scripts/dbupdater/insert/y.xml") == "insert/y.xml"
    assert deploy.package_record_name("dbupdater/replace/y.xml") == "replace/y.xml"


def test_an_unknown_shape_is_refused_not_guessed():
    with pytest.raises(ValueError, match="dbupdater"):
        deploy.package_record_name("somewhere/else.xml")


def _record(package, result, hash_="ab"):
    return {"FilePackage": package, "LoadResult": result, "FileHash": hash_}


def test_a_loaded_package_is_reported_ok():
    report = deploy.check_loaded(
        [_record("insert/npctemplate.xml", "SUCCESS")],
        expected="insert/npctemplate.xml",
    )
    assert report.ok
    assert report.result == "SUCCESS"


def test_a_package_the_server_rejected_is_reported_with_its_result():
    """FAILURE means the server tried and could not -- usually a malformed
    package or an unknown DataObject type."""
    report = deploy.check_loaded(
        [_record("insert/npctemplate.xml", "FAILURE")],
        expected="insert/npctemplate.xml",
    )
    assert report.ok is False
    assert report.result == "FAILURE"


def test_a_package_the_server_never_saw_is_reported_as_absent():
    """The dangerous case: the file was copied but the loader skipped the
    directory, so there is no row at all and nothing looks wrong."""
    report = deploy.check_loaded([], expected="insert/npctemplate.xml")
    assert report.ok is False
    assert report.result is None
    assert "never loaded" in report.reason


def test_a_stale_hash_means_the_server_is_running_older_content():
    """The loader skips a file whose hash it already applied, so a row whose
    hash does not match the deployed file means the running server has the
    previous version and nothing said so."""
    report = deploy.check_loaded(
        [_record("insert/npctemplate.xml", "SUCCESS", hash_="old")],
        expected="insert/npctemplate.xml", sha256="new",
    )
    assert report.ok is False
    assert "stale" in report.reason


def test_the_server_config_is_generated_with_the_deployments_own_values():
    """The example points at localhost:3306, which the repo's compose does
    not publish: a host-run server then fails every query while the config
    looks right."""
    xml = deploy.server_config_xml(
        db_host="127.0.0.1", db_port=3307, db_name="opendaoc",
        db_user="root", db_password="secret",
    ).decode("utf-8")
    assert "127.0.0.1" in xml and "3307" in xml
    assert "Database=opendaoc" in xml
    assert "Password=secret" in xml
    assert "<Port>10300</Port>" in xml
    assert "<RegionPort>10400</RegionPort>" in xml


def test_the_config_never_carries_a_placeholder():
    """A leftover DBNAME/USERNAME/PASSWORD string produces a server that
    starts and then fails on every query."""
    xml = deploy.server_config_xml(
        db_host="db", db_port=3306, db_name="opendaoc",
        db_user="root", db_password="pw",
    ).decode("utf-8")
    for placeholder in ("DBNAME", "USERNAME", "PASSWORD"):
        assert placeholder not in xml


def test_the_insert_path_is_where_the_server_actually_looks():
    """`xml_load_insert_directory` defaults to `dbupdater/insert` relative to
    the scripts directory; copying the package anywhere else is a no-op the
    server reports nowhere."""
    assert deploy.insert_dir("Release") == "Release/scripts/dbupdater/insert"
