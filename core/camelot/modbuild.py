"""Building a mod pack from an authoring directory (design §4.3).

An authoring directory is a file-manager-friendly description of a mod:

    mymod/
      mod.json            identity: id, version, priority, requires
      rows.json           optional server rows (the loader allocates their ids)
      client/…            loose replacements, paths mirroring the client
      client.patch.json   archive entries and gamedata row ops

`build_mod_pack` resolves the payload against a client corpus -- column
names, the id band, that the replacements exist -- assembles the deployment
document, and writes the reproducible `.tar.gz` both consumers read: the
server loader takes the rows, `camelot client install` the client half.
Refusals happen here, at build time, where the author sees them.
"""
from __future__ import annotations

import json
import os

from . import assemble, gamedata, mod, modelusage
from .contract import Manifest, client_build, client_profile

__all__ = ["build_mod_pack"]


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except ValueError as error:
        raise ValueError(f"{path}: not JSON: {error}") from error


def _loose_members(mod_dir: str) -> tuple[list[dict], dict[str, bytes]]:
    """Everything under `client/`, as mirror-path replacements, sorted."""
    root = os.path.join(mod_dir, "client")
    items: list[dict] = []
    members: dict[str, bytes] = {}
    if not os.path.isdir(root):
        return items, members
    for dirpath, _dirs, names in os.walk(root):
        for name in sorted(names):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            item = mod.client_loose(rel)
            with open(full, "rb") as fh:
                members[item["member"]] = fh.read()
            items.append(item)
    items.sort(key=lambda item: item["path"].lower())
    return items, members


def _payload_file(mod_dir: str, name) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError(f"{mod_dir}: a patch payload needs a 'file'")
    normalised = name.replace("\\", "/")
    if os.path.isabs(normalised) or ".." in normalised.split("/"):
        raise ValueError(f"{mod_dir}: payload file must stay inside the mod: {name!r}")
    return os.path.join(mod_dir, *normalised.split("/"))


def _patch_items(mod_dir: str, patch: dict, client_dir: str):
    """(items, members) for `client.patch.json`; the bytes come from `file`."""
    tables = gamedata.ClientTables(client_dir)
    items = mod.client_patch_items(patch, tables)
    members: dict[str, bytes] = {}
    specs = list(patch.get("archive") or [])
    entries = [item for item in items if item["kind"] == "archive_entry"]
    for spec, item in zip(specs, entries):
        try:
            with open(_payload_file(mod_dir, spec.get("file")), "rb") as fh:
                members[item["member"]] = fh.read()
        except FileNotFoundError:
            raise ValueError(
                f"{mod_dir}: archive payload {spec.get('file')!r} is missing"
            ) from None
    return items, members


def _rows(mod_dir: str, client_dir: str, creatures) -> list[dict]:
    rows = _read_json(os.path.join(mod_dir, "rows.json"))
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise TypeError(f"{mod_dir}/rows.json: expected a list of rows")
    if creatures is not None:
        from . import creatures as creatures_mod
        from .targets import opendaoc

        found = creatures_mod.read_creatures(client_dir)
        if creatures:
            found = found[:creatures]
        rows = rows + [
            opendaoc.creature_row(model_id=c.model_id, name=c.name,
                                  nif=c.nif, anim_set=c.anim_set)
            for c in found
        ]
    return rows


def _check_added_ids(mod_id, items, used_models: str | None) -> None:
    """Refuse a `monsters.csv` add at an id the server content references.

    The client-side rule ("the id must be free") is checked against the
    reference client elsewhere; this is the *server* half: taking an id the
    shipped content already uses would change live creatures instead of
    adding new ones. The measured list is `camelot.modelusage`; a server can
    replace it with its own export (`--used-models`).
    """
    used = modelusage.used_model_ids(used_models)
    for item in items:
        if (item.get("kind") == "gamedata_row" and item.get("action") == "add"
                and item.get("table") == "monsters.csv"
                and int(item["id"]) in used):
            raise ValueError(
                f"{mod_id}: monsters.csv id {item['id']} is referenced by the "
                "shipped server content; pick a free one (`camelot client "
                "free-ids` lists the pool) or pass --used-models for this server"
            )


def build_mod_pack(mod_dir: str, client_dir: str, out_path: str, *,
                   creatures: int | None = None,
                   used_models: str | None = None) -> dict:
    """Write the pack for *mod_dir* and report what went in."""
    if not os.path.isdir(mod_dir):
        raise ValueError(f"{mod_dir}: no such directory")
    meta = _read_json(os.path.join(mod_dir, "mod.json"))
    if meta is None:
        raise ValueError(f"{mod_dir}/mod.json: missing")
    if not isinstance(meta, dict):
        raise TypeError(f"{mod_dir}/mod.json: expected an object")
    mod_id = meta.get("id")
    version = meta.get("version")
    if not mod_id or not version:
        raise ValueError(f"{mod_dir}/mod.json: 'id' and 'version' are required")
    patch = _read_json(os.path.join(mod_dir, "client.patch.json"))
    if patch is None:
        patch = {}
    if not isinstance(patch, dict):
        raise TypeError(f"{mod_dir}/client.patch.json: expected an object")

    loose, loose_members = _loose_members(mod_dir)
    patched, patch_members = _patch_items(mod_dir, patch, client_dir)
    _check_added_ids(mod_id, patched, used_models)
    rows = _rows(mod_dir, client_dir, creatures)

    doc = mod.deployment(
        str(mod_id), str(version), rows,
        priority=int(meta.get("priority", 100)),
        client=loose + patched,
    )
    doc["requires"] = [str(name) for name in (meta.get("requires") or [])]

    files: dict[str, bytes] = {"targets/opendaoc/mod.json": mod.deployment_bytes(doc)}
    files.update(loose_members)
    files.update(patch_members)
    files["checksums.lock"] = assemble.lock_bytes(files)

    manifest = Manifest(
        pack_id=str(mod_id), version=str(version),
        client_build=client_build(client_dir),
        client_profile=client_profile(client_dir),
    )
    assemble.archive(manifest, out_path, files=files)
    return {
        "out": out_path,
        "mod": str(mod_id),
        "version": str(version),
        "items": len(loose) + len(patched),
        "rows": len(rows),
        "members": len(loose_members) + len(patch_members),
    }
