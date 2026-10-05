"""Deployment helpers for the OpenDAoC target.

The server loads XML packages at startup through
``AutoXMLDatabaseUpdate`` and records one ``AutoXMLUpdate`` row per file:
the package's path relative to ``dbupdater``, its hash, and ``LoadResult``
(SUCCESS or FAILURE). Reading that row is the only way to tell a package
that loaded from one that was skipped -- the server logs nothing obvious
either way, and an empty runtime directory looks perfectly healthy.

Everything here is the arithmetic of that mechanism, kept testable and away
from the shell that orchestrates it. Paths and column spellings come from
the server's source, not from convention.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "LoadReport",
    "check_loaded",
    "insert_dir",
    "package_record_name",
    "server_config_xml",
    "sha256_file",
]

#: The server's `xml_load_insert_directory` default, relative to the
#: scripts directory.
INSERT_DIR_SUFFIX = "scripts/dbupdater/insert"


def insert_dir(release_root: str) -> str:
    """Where the server looks for insert packages.

    `AutoXMLDatabaseUpdate` resolves the configured directory against the
    server's root plus `scripts`; a package copied anywhere else is a no-op
    that is reported nowhere.
    """
    return f"{release_root.rstrip('/')}/{INSERT_DIR_SUFFIX}"


def package_record_name(xml_path: str) -> str:
    """The `FilePackage` value the server records for a package.

    `GetRelativePath(xml, parent-of-insert-dir)` -- so the recorded name
    starts at `insert/` or `replace/`, whichever directory holds it. A check
    against another spelling would silently never match.
    """
    normalised = xml_path.replace("\\", "/")
    for marker in ("dbupdater/",):
        index = normalised.rfind(marker)
        if index >= 0:
            return normalised[index + len(marker):]
    if normalised.startswith(("insert/", "replace/")):
        return normalised
    raise ValueError(
        f"{xml_path!r} is not under a dbupdater directory; "
        "the server records packages relative to it"
    )


@dataclass
class LoadReport:
    """What the server's own record says about our package."""

    ok: bool
    result: str | None
    reason: str = ""


def check_loaded(records, *, expected: str, sha256: str | None = None) -> LoadReport:
    """Check the server's `AutoXMLUpdate` rows for a deployed package.

    Three failures, all silent on the server side: a package it never saw
    (the directory was not read), one it tried and could not apply, and one
    whose hash no longer matches the deployed file -- which means the
    running server holds the *previous* version while the new file sits on
    disk.
    """
    row = next((r for r in records if r.get("FilePackage") == expected), None)
    if row is None:
        return LoadReport(
            ok=False, result=None,
            reason=(
                f"{expected} was never loaded: the server has no record of it. "
                "Check that it sits in the package directory the server reads."
            ),
        )
    result = row.get("LoadResult")
    if result != "SUCCESS":
        return LoadReport(
            ok=False, result=result,
            reason=f"{expected} loaded with result {result!r}",
        )
    recorded = row.get("FileHash")
    if sha256 is not None and recorded and recorded != sha256:
        return LoadReport(
            ok=False, result=result,
            reason=(
                f"{expected} is stale: the server applied hash {recorded!r} "
                f"but the deployed file hashes {sha256!r}"
            ),
        )
    return LoadReport(ok=True, result=result)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def server_config_xml(
    *, db_host: str, db_port: int, db_name: str,
    db_user: str, db_password: str, server_port: int = 10300,
    region_port: int = 10400,
) -> bytes:
    """A `serverconfig.xml` for this deployment's own database values.

    The shipped example points at `localhost:3306`, which the repository's
    own compose file does not publish: a host-run server then fails *every*
    query with "unable to connect to any of the specified MySQL hosts"
    while the configuration looks correct.
    """
    connection = (
        f"Server={db_host};Port={db_port};Database={db_name};"
        f"UserId={db_user};Password={db_password};TreatTinyAsBoolean=false;"
        "Pooling=true;MinimumPoolSize=30;MaximumPoolSize=120;"
        "ConnectionReset=false"
    )
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<root>
    <Server>
        <Port>{server_port}</Port>
        <IP>0.0.0.0</IP>
        <RegionIP>0.0.0.0</RegionIP>
        <RegionPort>{region_port}</RegionPort>
        <UdpIP>0.0.0.0</UdpIP>
        <UdpPort>{region_port}</UdpPort>
        <EnableUPnP>False</EnableUPnP>
        <DetectRegionIP>False</DetectRegionIP>
        <ServerName>OpenDAoC</ServerName>
        <ServerNameShort>CoreServer</ServerNameShort>
        <LogConfigFile>./config/logconfig.xml</LogConfigFile>
        <ScriptCompilationTarget>./lib/GameServerScripts.dll</ScriptCompilationTarget>
        <ScriptAssemblies></ScriptAssemblies>
        <EnableCompilation>True</EnableCompilation>
        <AutoAccountCreation>True</AutoAccountCreation>
        <GameType>Normal</GameType>
        <CheatLoggerName>cheats</CheatLoggerName>
        <GMActionLoggerName>gmactions</GMActionLoggerName>
        <InvalidNamesFile>./config/invalidnames.txt</InvalidNamesFile>
        <DBType>MYSQL</DBType>
        <DBConnectionString>{connection}</DBConnectionString>
        <DBAutosave>True</DBAutosave>
        <DBAutosaveInterval>10</DBAutosaveInterval>
        <MetricsEnabled>false</MetricsEnabled>
    </Server>
</root>
"""
    return xml.encode("utf-8")
