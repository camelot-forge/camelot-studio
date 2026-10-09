"""Packaging: the contract, its artifacts and its projection as one unit.

A *pack* is the thing someone actually deploys, as opposed to the contract
inside it. Assembly is deliberately boring and deliberately strict:

- **Self-describing.** `PACK.json` travels inside the archive, so a reader
  can tell what it is, which schema it speaks and which client build it was
  generated against without external notes.
- **Reproducible.** Same input, same bytes. Every member carries mtime 0 and
  a fixed uid/gid, because a timestamp would make two builds of one corpus
  differ and render the lock meaningless.
- **Complete.** The returned lock covers every member including the
  metadata, so nothing inside is unverifiable.
- **Byte-free.** It ships Studio-authored content and descriptions, never
  the retail client's files.

Target projections are written beside the contract by the caller; this
module does not know what a projection contains.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile

from .contract import SCHEMA_VERSION, Manifest

__all__ = ["archive", "lock_bytes", "pack_metadata", "pack_name"]

#: Fixed ownership/timestamps: a pack is content, not a filesystem snapshot.
_MEMBER_ATTRS = {"mtime": 0, "uid": 0, "gid": 0, "uname": "", "gname": "", "mode": 0o644}


def pack_name(manifest: Manifest) -> str:
    """`<id>-<version>-<client build>`, safe for a filename.

    A pack is only valid for the build it was generated against, so the
    build is part of the name rather than something a deployer has to
    remember.
    """
    build = manifest.client_build or "unknown"
    return f"{manifest.pack_id}-{manifest.version}-{build}"


def pack_metadata(manifest: Manifest) -> bytes:
    """The `PACK.json` that travels inside the archive.

    It repeats the identifying fields on purpose: a deployer who extracts
    only this file can still tell what they hold.
    """
    doc = {
        "pack": {
            "id": manifest.pack_id,
            "version": manifest.version,
            "name": pack_name(manifest),
        },
        "schema_version": SCHEMA_VERSION,
        "client": {
            "build": manifest.client_build,
            "profile": manifest.client_profile,
        },
        "figures": len(manifest.figures),
        "unresolved": [u.to_dict() for u in manifest.unresolved],
        "artifact_count": len(manifest.artifacts),
    }
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def lock_bytes(files: dict[str, bytes]) -> bytes:
    """The `checksums.lock` a pack carries: one `<sha256>  <path>` line each.

    Written inside the archive over every member but itself -- which is what
    the loader verifies: each line it finds must match a member, and a member
    the lock does not list (like `PACK.json`, generated after this point) is
    still allowed.
    """
    return "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    ).encode("utf-8")


def _add(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    for key, value in _MEMBER_ATTRS.items():
        setattr(info, key, value)
    tar.addfile(info, io.BytesIO(data))


def archive(
    manifest: Manifest, path: str, *, files: dict[str, bytes]
) -> dict[str, str]:
    """Write a reproducible `.tar.gz` and return the lock over its members.

    `files` maps archive-relative paths to bytes. `PACK.json` is generated
    here and is included in the lock, so the metadata is verified along with
    everything else.
    """
    members = dict(files)
    members["PACK.json"] = pack_metadata(manifest)

    # gzip would otherwise staple the wall clock into the header, which is
    # exactly the kind of drift that makes two builds of one corpus differ.
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar,
    ):
        for name in sorted(members):
            _add(tar, name, members[name])

    blob = buffer.getvalue()
    with open(path, "wb") as fh:
        fh.write(blob)
    return {name: hashlib.sha256(members[name]).hexdigest() for name in sorted(members)}
