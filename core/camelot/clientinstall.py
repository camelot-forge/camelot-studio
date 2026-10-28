"""The client half of a mod pack: plan, materialize, verify.

The server loader applies a pack's rows at boot; this module applies its
client payload to a client directory. The shape:

- **replace, from originals.** Every touched file is rebuilt from a pristine
  copy kept under `.camelot/pristine/`, plus the winning payloads, so removing
  a mod restores the exact bytes and an interrupted run self-heals: there is
  one writer (`_recompute`), it is deterministic, and state is saved last.
- **claims and priority.** A destination claimed twice is resolved by
  priority; a tie at the top refuses the whole operation before anything is
  written. The state records who owns what. (Written for independent mods;
  since 2026-10-14 the product is one content set, so a destination normally
  has a single owner — the state is kept because it is what makes a removal
  exact.)
- **read it back.** The client refuses a bad archive silently (measured: it
  exits during the loading screen), so every materialization is verified by
  reading the result back.

The store lives inside the client directory; the game ignores it.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
from dataclasses import dataclass, field

from . import clientpatch, gamedata, mod
from .mpak import MpakArchive, MpakError, write_mpak

__all__ = [
    "ConflictError",
    "DriftError",
    "InstallError",
    "ModPack",
    "PackError",
    "blocks_install",
    "client_running",
    "install_pack",
    "original",
    "original_entry",
    "resolve_existing",
    "running_client_dirs",
    "status",
    "uninstall",
    "verify",
]

STATE_DIR = ".camelot"
STATE_FILE = "state.json"
STATE_SCHEMA = 1
MEMBERS_DIR = "members"
PRISTINE_DIR = "pristine"
TMP_DIR = "tmp"

#: Mirrors the C# loader's cap: a pack larger than this is refused rather
#: than buffered, so a hostile archive cannot exhaust memory.
MAX_PACK_BYTES = 512 * 1024 * 1024


class InstallError(ValueError):
    """Raised when a client payload cannot be planned or applied."""


class PackError(InstallError):
    """Raised when a pack is malformed, tampered with, or unsafe."""


class ConflictError(InstallError):
    """Raised when two mods claim one destination at the same priority."""

    def __init__(self, conflicts):
        self.conflicts = list(conflicts)
        super().__init__(
            "client destinations are claimed twice at one priority:\n  "
            + "\n  ".join(self.conflicts)
        )


class DriftError(InstallError):
    """Raised when a client file changed outside the tool."""

    def __init__(self, paths):
        self.paths = list(paths)
        super().__init__(
            "these client files changed outside the tool:\n  "
            + "\n  ".join(self.paths)
            + "\nrestore them from .camelot/pristine/, or re-run with adopt=True "
            "to make the current bytes the new baseline"
        )


# --- small helpers -----------------------------------------------------------

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _write_bytes(path: str, data: bytes) -> None:
    """Write *data* atomically (temp + rename, same directory)."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.camelot-tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _store_dir(client_dir: str) -> str:
    return os.path.join(client_dir, STATE_DIR)


def _pristine_path(client_dir: str, rel: str) -> str:
    return os.path.join(_store_dir(client_dir), PRISTINE_DIR, rel)


def _safe_version(version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", version) or "unknown"


def _member_store_path(client_dir: str, mod_id: str, version: str, member: str) -> str:
    safe = member.replace("\\", "/").lstrip("/")
    if not safe or ".." in safe.split("/"):
        raise PackError(f"unsafe member name {member!r}")
    return os.path.join(
        _store_dir(client_dir), MEMBERS_DIR, mod_id, _safe_version(version), safe
    )


def _tmp_dir(client_dir: str) -> str:
    return os.path.join(_store_dir(client_dir), TMP_DIR)


def _clean_tmp(client_dir: str) -> None:
    shutil.rmtree(_tmp_dir(client_dir), ignore_errors=True)


def _temp_file(client_dir: str, tag: str, data: bytes) -> str:
    directory = _tmp_dir(client_dir)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{_sha(tag.encode())[:8]}-{os.path.basename(tag)}")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def resolve_existing(client_dir: str, rel: str) -> str | None:
    """The on-disk file *rel* names, matched case-insensitively, or None.

    The extraction case mismatch is real (four corpus tests trip on it), so a
    pack built on one filesystem must find the file the client actually reads
    on another. The rule lives in `camelot.clientfs`.
    """
    from . import clientfs

    path = clientfs.resolve(client_dir, rel)
    return path if path is not None and os.path.isfile(path) else None


def original(client_dir: str, rel: str) -> bytes | None:
    """The store's copy of a destination's original bytes, or None.

    Only the store is consulted. The question this answers is whether the
    target *records* an original — the author's mode captures one the first
    time a destination is touched, a player's client has none — and reading
    the file on disk instead would answer a different one.
    """
    path = _pristine_path(client_dir, rel)
    if not os.path.isfile(path):
        return None
    return _read_file(path)


def original_entry(client_dir: str, archive: str, entry: str) -> bytes | None:
    """One entry of an archive destination, as the store's original holds it."""
    path = _pristine_path(client_dir, archive)
    if not os.path.isfile(path):
        return None
    parsed = MpakArchive(path)
    try:
        return parsed.read(entry)
    except MpakError:
        return None
    finally:
        parsed.close()


def _resolve_for_write(client_dir: str, rel: str) -> str:
    return resolve_existing(client_dir, rel) or os.path.join(client_dir, rel)


def _client_build(client_dir: str) -> str | None:
    from .contract import client_build

    return client_build(client_dir)


def client_running() -> str | None:
    """The name of a running game client, when one is visible (Linux).

    The client reads its files once, at startup, so installing under a
    running one changes nothing on screen — and on Windows the open files
    refuse the write anyway. Refusing early is the honest version of both.
    The process carries the name of the binary it loads (`game1127.dll`).
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        if comm in ("camelot.exe", "camtest.exe", "game1127.dll"):
            return comm
    return None


# --- the pack ----------------------------------------------------------------

def _check_mod_id(mod_id, path: str) -> None:
    """A pack's id and its mod requirements share `camelot.mod`'s rules."""
    try:
        mod.check_mod_id(mod_id, path)
    except ValueError as error:
        raise PackError(str(error)) from None


def _item_members(item: dict) -> list[str]:
    if item.get("kind") in ("loose", "archive_entry"):
        return [item["member"]]
    return []


def _read_pack_members(path: str) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(path, "r:gz") as handle:
            for entry in handle:
                if not entry.isfile():
                    continue
                name = entry.name.replace("\\", "/").lstrip("/")
                if not name or ".." in name.split("/"):
                    raise PackError(f"{path}: unsafe member name {entry.name!r}")
                data = handle.extractfile(entry).read()
                total += len(data)
                if total > MAX_PACK_BYTES:
                    raise PackError(f"{path}: archive too large")
                members[name] = data
    except (tarfile.TarError, OSError) as error:
        raise PackError(f"{path}: not a readable pack: {error}") from error
    return members


def _verify_lock(members: dict[str, bytes]) -> None:
    """Mirror the loader: every line in the lock must match a member."""
    lock = members.get("checksums.lock")
    if lock is None:
        raise PackError("no checksums.lock inside the pack")
    for line in lock.decode("utf-8").split("\n"):
        line = line.strip()
        if not line:
            continue
        split = line.find("  ")
        if split <= 0:
            continue
        expected, name = line[:split], line[split + 2:]
        data = members.get(name)
        if data is None:
            raise PackError(f"{name} is listed in checksums.lock but missing")
        if _sha(data) != expected.lower():
            raise PackError(f"{name} does not match its checksum")


@dataclass
class ModPack:
    """A pack's client half, read and verified, ready to install."""

    path: str
    mod_id: str
    version: str
    priority: int
    items: list[dict]
    requires: list[str]
    members: dict[str, bytes]
    build: str | None = None
    #: The highest id this pack's own declarations allow (`mod.effective_ceiling`).
    ceiling: int = mod.CLIENT_ID_MAX

    @classmethod
    def load(cls, path: str) -> ModPack:
        members = _read_pack_members(path)
        _verify_lock(members)
        raw = members.get("targets/opendaoc/mod.json")
        if raw is None:
            raise PackError(f"{path}: no targets/opendaoc/mod.json inside")
        try:
            doc = json.loads(raw)
        except ValueError as error:
            raise PackError(f"{path}: mod.json is not JSON: {error}") from error
        section = doc.get("mod") or {}
        _check_mod_id(section.get("id"), path)
        version = section.get("version")
        if not version:
            raise PackError(f"{path}: mod.json has no version")
        items = list(doc.get("client") or [])
        requires = [str(name) for name in (doc.get("requires") or [])]
        for name in requires:
            if not mod.is_capability(name):
                _check_mod_id(name, f"{path} requires")
        ceiling = mod.effective_ceiling(requires, items)
        try:
            mod.claims(items, ceiling=ceiling)
        except (TypeError, ValueError) as error:
            raise PackError(f"{path}: {error}") from error
        for item in items:
            for member in _item_members(item):
                if member not in members:
                    raise PackError(f"{path}: payload member {member} is missing")
        build = None
        if "PACK.json" in members:
            try:
                build = (json.loads(members["PACK.json"]).get("client") or {}).get("build")
            except ValueError:
                build = None
        return cls(
            path=path,
            mod_id=section["id"],
            version=str(version),
            priority=int(section.get("priority", 100)),
            items=items,
            requires=requires,
            members=members,
            build=build,
            ceiling=ceiling,
        )


# --- state -------------------------------------------------------------------

@dataclass
class Install:
    mod_id: str
    version: str
    priority: int
    items: list[dict]
    requires: list[str]
    claims: list[str]
    members: dict[str, str]
    build: str | None = None

    def to_dict(self) -> dict:
        return {
            "mod": self.mod_id,
            "version": self.version,
            "priority": self.priority,
            "items": self.items,
            "requires": list(self.requires),
            "claims": list(self.claims),
            "members": dict(sorted(self.members.items())),
            "build": self.build,
        }

    @classmethod
    def from_dict(cls, doc: dict) -> Install:
        return cls(
            mod_id=str(doc.get("mod")),
            version=str(doc.get("version") or ""),
            priority=int(doc.get("priority", 100)),
            items=list(doc.get("items") or []),
            requires=[str(name) for name in (doc.get("requires") or [])],
            claims=[str(claim) for claim in (doc.get("claims") or [])],
            members={str(k): str(v) for k, v in (doc.get("members") or {}).items()},
            build=doc.get("build"),
        )


@dataclass
class State:
    client: dict = field(default_factory=dict)
    installs: list[Install] = field(default_factory=list)
    #: rel path -> {"pristine": sha | None, "materialized": sha | None}
    destinations: dict[str, dict] = field(default_factory=dict)


def _state_path(client_dir: str) -> str:
    return os.path.join(_store_dir(client_dir), STATE_FILE)


def _load_state(client_dir: str) -> State:
    try:
        raw = _read_file(_state_path(client_dir))
    except FileNotFoundError:
        return State()
    try:
        doc = json.loads(raw)
    except ValueError as error:
        raise InstallError(f"{_state_path(client_dir)}: state is not JSON: {error}") from error
    if doc.get("schema") != STATE_SCHEMA:
        raise InstallError(f"{_state_path(client_dir)}: unknown state schema {doc.get('schema')!r}")
    return State(
        client=dict(doc.get("client") or {}),
        installs=[Install.from_dict(item) for item in (doc.get("installs") or [])],
        destinations={str(k): dict(v) for k, v in (doc.get("destinations") or {}).items()},
    )


def _save_state(client_dir: str, state: State) -> None:
    doc = {
        "schema": STATE_SCHEMA,
        "client": state.client,
        "installs": [install.to_dict() for install in state.installs],
        "destinations": dict(sorted(state.destinations.items())),
    }
    body = (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes(_state_path(client_dir), body)


# --- planning ----------------------------------------------------------------

def _claim_owners(installs) -> tuple[dict[str, str], list[str], list[str]]:
    """(claim -> winning mod, conflicts, warnings) for a set of installs.

    The rule is D2 of the design: the highest priority wins; a tie at the
    top is a conflict that refuses the whole operation.
    """
    groups: dict[str, list[Install]] = {}
    for install in installs:
        # ceiling=None: the band is a build-time rule, and a mod installed
        # under a wider band must stay readable after it moves.
        for claim in mod.claims(install.items, ceiling=None):
            groups.setdefault(claim, []).append(install)
    owners: dict[str, str] = {}
    conflicts: list[str] = []
    warnings: list[str] = []
    for claim, group in sorted(groups.items()):
        top = max(install.priority for install in group)
        winners = [install for install in group if install.priority == top]
        if len(winners) > 1:
            names = ", ".join(sorted(install.mod_id for install in winners))
            conflicts.append(f"{claim} is claimed at priority {top} by {names}")
            continue
        owners[claim] = winners[0].mod_id
        if len(group) > 1 and claim.startswith("row:") and claim.count("#") == 1:
            others = sorted({install.mod_id for install in group} - {owners[claim]})
            warnings.append(
                f"{claim}: also added by {', '.join(others)}; priority picked "
                f"{owners[claim]}, and their rows now point at this one"
            )
    return owners, conflicts, warnings


def _effective_item(item: dict, owners: dict[str, str], mod_id: str) -> dict | None:
    """The item as it applies under the ownership map, or None."""
    kind = item["kind"]
    if kind in ("loose", "patch"):
        rel = item["path"] if kind == "loose" else item["file"]
        return item if owners.get(f"file:{rel.lower()}") == mod_id else None
    if kind == "archive_entry":
        claim = f"entry:{item['archive'].lower()}#{item['entry'].lower()}"
        return item if owners.get(claim) == mod_id else None
    table = item["table"].lower()
    if item["action"] == "add":
        return item if owners.get(f"row:{table}#{item['id']}") == mod_id else None
    columns = item["columns"]
    keep = {
        name: value
        for name, value in item["values"].items()
        if owners.get(f"row:{table}#{item['id']}#{columns[name]}") == mod_id
    }
    if not keep:
        return None
    effective = dict(item)
    effective["values"] = keep
    effective["expect"] = {name: v for name, v in item["expect"].items() if name in keep}
    effective["columns"] = {name: idx for name, idx in columns.items() if name in keep}
    return effective


def _touched(installs) -> set[str]:
    rels: set[str] = set()
    for install in installs:
        for item in install.items:
            if item["kind"] == "loose":
                rels.add(item["path"])
            elif item["kind"] == "patch":
                rels.add(item["file"])
            elif item["kind"] == "archive_entry":
                rels.add(item["archive"])
            else:
                rels.add(gamedata.GAMEDATA_ARCHIVE)
    return rels


# --- materialization ---------------------------------------------------------

def _pristine_bytes_for(client_dir: str, rel: str, *, capture: bool) -> bytes | None:
    """The original bytes of *rel*: the store copy, or the disk on first touch.

    The store copy is authoritative when it exists: a crash between a write
    and the state save leaves exactly that situation, and the store copy is
    still the original.
    """
    store = _pristine_path(client_dir, rel)
    if os.path.isfile(store):
        return _read_file(store)
    existing = resolve_existing(client_dir, rel)
    if existing is None:
        return None
    data = _read_file(existing)
    if capture:
        _write_bytes(store, data)
    return data


def _stored_member(client_dir: str, install: Install, member: str) -> bytes:
    path = _member_store_path(client_dir, install.mod_id, install.version, member)
    try:
        data = _read_file(path)
    except OSError as error:
        raise InstallError(
            f"{install.mod_id}: payload member {member} is not in the store; "
            "reinstall the pack"
        ) from error
    if _sha(data) != install.members.get(member):
        raise InstallError(
            f"{install.mod_id}: payload member {member} does not match its checksum"
        )
    return data


def _rebuild_archive(client_dir: str, rel: str, pristine: bytes,
                     replacements: dict[str, bytes]) -> bytes:
    """The archive *rel* with *replacements* (entry -> bytes) applied.

    The writer recomputes the header carriers; the result is read back before
    it is trusted (`the client refuses a bad archive silently`).
    """
    source = _temp_file(client_dir, f"pristine-{rel}", pristine)
    out = f"{source}.out"
    wanted: dict[str, bytes] = {}
    try:
        archive = MpakArchive(source)
        try:
            known = {name.lower() for name in archive.entries}
            for entry, data in replacements.items():
                key = entry.lower()
                if key not in known:
                    raise InstallError(
                        f"{rel}: no entry {entry!r} to replace "
                        "(v1 replaces; it does not add entries)"
                    )
                wanted[key] = data
            entries = [
                (name, wanted.get(name, archive.read(name))) for name in archive.entries
            ]
            write_mpak(
                out, archive.name, entries,
                template=archive.header, unknown=archive.unknown_fields(),
            )
        finally:
            archive.close()
        written = _read_file(out)
        check = MpakArchive(out)
        try:
            for name, data in wanted.items():
                if check.read(name) != data:
                    raise InstallError(
                        f"{rel}: the writer did not reproduce entry {name!r}"
                    )
        finally:
            check.close()
        return written
    finally:
        _remove_file(source)
        _remove_file(out)


def _table_edits(client_dir: str, pristine: bytes, ops) -> dict[str, bytes]:
    """The edited table bytes for a set of (priority, mod_id, item) ops."""
    source = _temp_file(client_dir, "tables-pristine.mpk", pristine)
    try:
        archive = MpakArchive(source)
        try:
            by_table: dict[str, list[tuple[str, dict]]] = {}
            for _priority, mod_id, item in ops:
                by_table.setdefault(item["table"], []).append((mod_id, item))
            out: dict[str, bytes] = {}
            for table_name, items in by_table.items():
                key = table_name.lower()
                if key not in archive.entries:
                    raise InstallError(
                        f"{gamedata.GAMEDATA_ARCHIVE}: no table named {table_name!r}"
                    )
                edited = archive.read(key)
                for mod_id, item in items:
                    try:
                        edited = gamedata.edit_table(edited, table_name, [item])
                    except gamedata.TableError as error:
                        raise InstallError(f"{mod_id}: {error}") from error
                out[key] = edited
            return out
        finally:
            archive.close()
    finally:
        _remove_file(source)


def _compute(client_dir: str, installs, *, capture: bool) -> tuple[dict, dict, list[str]]:
    """(content by rel path, owners, warnings) for the install set.

    Content is what should be on disk: the winning payloads applied to the
    pristine bytes. Nothing is written here; `capture` only decides whether a
    first-touch pristine copy is stored.
    """
    owners, conflicts, warnings = _claim_owners(installs)
    if conflicts:
        raise ConflictError(conflicts)
    content: dict[str, bytes | None] = {}
    loose: dict[str, bytes] = {}
    archives: dict[str, dict[str, bytes]] = {}
    tables: list[tuple[int, str, dict]] = []
    patches: dict[str, bytes] = {}
    for install in installs:
        for item in install.items:
            effective = _effective_item(item, owners, install.mod_id)
            if effective is None:
                continue
            kind = effective["kind"]
            if kind == "loose":
                # Capture the original before anything writes (first touch only).
                if _pristine_bytes_for(client_dir, effective["path"], capture=capture) is None:
                    raise InstallError(
                        f"{effective['path']}: the pack replaces it, but the client "
                        "has no such file (v1 replaces; it does not add)"
                    )
                loose[effective["path"]] = _stored_member(
                    client_dir, install, effective["member"]
                )
            elif kind == "patch":
                pristine = _pristine_bytes_for(
                    client_dir, effective["file"], capture=capture
                )
                if pristine is None:
                    raise InstallError(
                        f"{effective['file']}: the pack patches it, but the client "
                        "has no such file"
                    )
                try:
                    patches[effective["file"]] = clientpatch.apply(pristine, effective)
                except clientpatch.PatchError as error:
                    raise InstallError(f"{install.mod_id}: {error}") from error
            elif kind == "archive_entry":
                if effective["archive"].lower() == gamedata.GAMEDATA_ARCHIVE.lower():
                    raise InstallError(
                        f"{install.mod_id}: {effective['archive']} carries the tables; "
                        "edit them with gamedata row ops, not a whole-entry replacement"
                    )
                archives.setdefault(effective["archive"], {})[effective["entry"]] = (
                    _stored_member(client_dir, install, effective["member"])
                )
            else:
                tables.append((install.priority, install.mod_id, effective))
    content.update(loose)
    for rel, replacements in archives.items():
        pristine = _pristine_bytes_for(client_dir, rel, capture=capture)
        if pristine is None:
            raise InstallError(f"{rel}: the pack replaces it, but the client has no such file")
        content[rel] = _rebuild_archive(client_dir, rel, pristine, replacements)
    if tables:
        rel = gamedata.GAMEDATA_ARCHIVE
        pristine = _pristine_bytes_for(client_dir, rel, capture=capture)
        if pristine is None:
            raise InstallError(f"{rel}: the client has no gamedata archive")
        tables.sort(key=lambda item: (item[0], item[1]))
        replacements = _table_edits(client_dir, pristine, tables)
        content[rel] = _rebuild_archive(client_dir, rel, pristine, replacements)
    for rel, data in patches.items():
        if rel in content:
            raise InstallError(f"{rel}: two payload items produce the same file")
        content[rel] = data
    return content, owners, warnings


def _drifted(client_dir: str, state: State, content: dict) -> list[str]:
    drifted = []
    for rel, record in sorted(state.destinations.items()):
        recorded = record.get("materialized")
        if recorded is None:
            continue
        path = resolve_existing(client_dir, rel)
        on_disk = _sha_file(path) if path else None
        if on_disk == recorded:
            continue
        expected = content.get(rel)
        if expected is not None and on_disk == _sha(expected):
            continue  # our own half-applied write: a crash before the state save
        drifted.append(rel)
    return drifted


def _write_content(client_dir: str, content: dict) -> list[str]:
    changed = []
    for rel, data in sorted(content.items()):
        path = _resolve_for_write(client_dir, rel)
        if data is None:
            if os.path.isfile(path):
                os.remove(path)
                changed.append(rel)
            continue
        if os.path.isfile(path) and _sha_file(path) == _sha(data):
            continue
        _write_bytes(path, data)
        changed.append(rel)
    return changed


def _verify_content(client_dir: str, content: dict, installs, owners) -> None:
    """Read every write back, through the same readers the game uses."""
    for rel, data in sorted(content.items()):
        path = resolve_existing(client_dir, rel)
        if data is None:
            if path is not None:
                raise InstallError(f"{rel}: could not be removed")
            continue
        if path is None or _read_file(path) != data:
            raise InstallError(f"{rel}: the written file does not match what was computed")
    for install in installs:
        for item in install.items:
            effective = _effective_item(item, owners, install.mod_id)
            # A patch is verified by the whole-file comparison above: its
            # result is the file's bytes, not an entry to decode.
            if effective is None or effective["kind"] in ("loose", "patch"):
                continue
            if effective["kind"] == "archive_entry":
                path = resolve_existing(client_dir, effective["archive"])
                archive = MpakArchive(path)
                try:
                    if archive.read(effective["entry"]) != _stored_member(
                        client_dir, install, effective["member"]
                    ):
                        raise InstallError(
                            f"{effective['archive']}: entry {effective['entry']!r} "
                            "did not survive the rewrite"
                        )
                finally:
                    archive.close()
            else:
                path = resolve_existing(client_dir, gamedata.GAMEDATA_ARCHIVE)
                archive = MpakArchive(path)
                try:
                    table = gamedata.parse_table(
                        effective["table"], archive.read(effective["table"])
                    )
                finally:
                    archive.close()
                index = table.row_index(effective["id"])
                if index is None:
                    raise InstallError(
                        f"{effective['table']}: row {effective['id']} is not in the archive"
                    )
                for name, value in effective["values"].items():
                    found = table.row_values(index, effective["columns"])[name]
                    if found != value:
                        raise InstallError(
                            f"{effective['table']}#{effective['id']}.{name}: "
                            f"wrote {value!r} but read back {found!r}"
                        )


def _check_capabilities(client_dir: str, installs, content: dict) -> None:
    """Every required capability must be carried by the client this set produces.

    Read from the bytes the plan produces — a patch in the same set counts, so
    a pack may carry the patch and use it — never from bookkeeping: a pack's
    `provides` is a promise, this is the check. An unknown capability is
    refused here for the same reason: it cannot be verified.
    """
    for install in installs:
        for name in install.requires:
            if not mod.is_capability(name):
                continue
            rel = clientpatch.CLIENT_BINARY
            if rel in content:
                data = content[rel]
            else:
                path = resolve_existing(client_dir, rel)
                data = _read_file(path) if path else None
            reason = clientpatch.check_capability(name, data)
            if reason:
                raise InstallError(f"{install.mod_id}: requires {name}, but {reason}")


def _recompute(client_dir: str, state: State, installs, *, adopt: bool = False) -> dict:
    _clean_tmp(client_dir)
    content, owners, warnings = _compute(client_dir, installs, capture=True)
    for rel in sorted(set(state.destinations) - set(content)):
        content[rel] = _pristine_bytes_for(client_dir, rel, capture=False)
    drifted = _drifted(client_dir, state, content)
    if drifted:
        if not adopt:
            raise DriftError(drifted)
        for rel in drifted:
            path = resolve_existing(client_dir, rel)
            if path is not None:
                _write_bytes(_pristine_path(client_dir, rel), _read_file(path))
                state.destinations.setdefault(rel, {})["pristine"] = _sha_file(path)
        warnings.append(
            "adopted changes made outside the tool for: " + ", ".join(drifted)
            + " (they are now part of the baseline)"
        )
        content, owners, extra = _compute(client_dir, installs, capture=True)
        warnings += extra
        for rel in sorted(set(state.destinations) - set(content)):
            content[rel] = _pristine_bytes_for(client_dir, rel, capture=False)
    _check_capabilities(client_dir, installs, content)
    changed = _write_content(client_dir, content)
    _verify_content(client_dir, content, installs, owners)
    touched = _touched(installs)
    ledger: dict[str, dict] = {}
    for rel in sorted(touched):
        store = _pristine_path(client_dir, rel)
        ledger[rel] = {
            "pristine": _sha_file(store) if os.path.isfile(store) else None,
            "materialized": _sha(content[rel]) if content.get(rel) is not None else None,
        }
    for rel in sorted(set(state.destinations) | set(content)):
        if rel not in touched:
            _remove_file(_pristine_path(client_dir, rel))
    state.destinations = ledger
    return {"owners": owners, "warnings": warnings, "changed": changed,
            "destinations": sorted(touched)}


def _store_members(client_dir: str, pack: ModPack, record: Install) -> None:
    for member in sorted(record.members):
        data = pack.members.get(member)
        if data is None:
            raise PackError(f"{pack.path}: payload member {member} is missing")
        _write_bytes(
            _member_store_path(client_dir, record.mod_id, record.version, member), data
        )


def _prune_members(client_dir: str, installs) -> None:
    root = os.path.join(_store_dir(client_dir), MEMBERS_DIR)
    if not os.path.isdir(root):
        return
    wanted = {(install.mod_id, _safe_version(install.version)) for install in installs}
    for mod_name in os.listdir(root):
        mod_root = os.path.join(root, mod_name)
        if not os.path.isdir(mod_root):
            continue
        for version_name in os.listdir(mod_root):
            if (mod_name, version_name) not in wanted:
                shutil.rmtree(os.path.join(mod_root, version_name), ignore_errors=True)
        if not os.listdir(mod_root):
            os.rmdir(mod_root)


# --- the operations ----------------------------------------------------------

#: The binary names a running DAoC client shows in `/proc/<pid>/comm`.
CLIENT_COMMS = ("camelot.exe", "camtest.exe", "game1127.dll")


def _wine_client_dir(command_line: str, comm: str) -> str | None:
    """The client directory behind a process' command line, when readable.

    On Linux the client runs under Wine, whose command lines carry Windows
    paths — measured on this machine: `connect.exe` launches
    `Z:\\home\\michael\\daoc-client\\game1127.dll`, and Wine's `Z:` drive is
    the root mapping. A relative token, another drive letter (`C:\\…`) or no
    token at all has no mapping we can *prove*, so they return None — and the
    callers treat "unknown" as "refuse the write", which is the safe side.
    """
    for token in command_line.split("\0"):
        if not token.endswith(comm):
            continue
        folder = token.replace("\\", "/").rsplit("/", 1)[0]
        drive, separator, rest = folder.partition(":")
        if len(drive) != 1 or not separator or not rest.startswith("/"):
            return None
        if drive.upper() != "Z":
            return None
        return rest
    return None


def running_client_dirs() -> list[str]:
    """The client directories of running game clients we can read."""
    out: list[str] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/comm", encoding="utf-8", errors="replace") as fh:
                comm = fh.read().strip()
        except OSError:
            continue
        if comm not in CLIENT_COMMS:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", encoding="utf-8", errors="replace") as fh:
                line = fh.read()
        except OSError:
            continue
        folder = _wine_client_dir(line, comm)
        if folder:
            out.append(folder)
    return out


def blocks_install(client_dir: str) -> str | None:
    """The running client's name when it is *this* client, else None.

    The client reads its files once, at startup, so writing under a copy it is
    playing from changes nothing on screen (and on Windows the open files
    refuse the write anyway) — but writing under a *different* copy is a
    different set of files and is safe. That is what makes the test suite's
    temporary clients installable while the operator plays.

    When the running client is visible but its directory cannot be read, or
    the target is the one it plays from, the write is refused: the unknown
    case is not worth guessing about.
    """
    running = client_running()
    if not running:
        return None
    dirs = running_client_dirs()
    if not dirs:
        return running
    target = os.path.realpath(client_dir)
    return running if any(os.path.realpath(d) == target for d in dirs) else None


def install_pack(client_dir: str, pack_path: str, *, priority: int | None = None,
                 allow_build_mismatch: bool = False, adopt: bool = False) -> dict:
    """Install (or update) a pack's client half; returns a report."""
    running = blocks_install(client_dir)
    if running:
        raise InstallError(f"the game is running ({running}); close it and retry")
    pack = ModPack.load(pack_path)
    state = _load_state(client_dir)
    warnings: list[str] = []
    target = _client_build(client_dir)
    if pack.build and target and target != pack.build and not allow_build_mismatch:
        raise InstallError(
            f"{pack.mod_id}: built against client {pack.build}, this client is {target}"
        )
    if pack.build and not target:
        warnings.append(
            f"{pack.mod_id}: could not read the client's build to check it "
            f"(the pack was built against {pack.build})"
        )
    installed = {install.mod_id: install for install in state.installs}
    missing = [name for name in pack.requires
               if not mod.is_capability(name) and name not in installed]
    if missing:
        raise InstallError(
            f"{pack.mod_id}: requires {', '.join(missing)}, which is not installed"
        )
    record = Install(
        mod_id=pack.mod_id,
        version=pack.version,
        priority=int(priority if priority is not None else pack.priority),
        items=pack.items,
        requires=list(pack.requires),
        claims=mod.claims(pack.items, ceiling=pack.ceiling),
        members={
            member: _sha(pack.members[member])
            for item in pack.items
            for member in _item_members(item)
        },
        build=pack.build,
    )
    installs = [i for i in state.installs if i.mod_id != pack.mod_id] + [record]
    installs.sort(key=lambda i: (i.priority, i.mod_id))
    _owners, conflicts, claimed = _claim_owners(installs)
    if conflicts:
        raise ConflictError(conflicts)
    warnings += claimed
    _store_members(client_dir, pack, record)
    report = _recompute(client_dir, state, installs, adopt=adopt)
    state.installs = installs
    if target:
        state.client = {"build": target}
    _save_state(client_dir, state)
    _prune_members(client_dir, installs)
    return {
        "mod": record.mod_id,
        "version": record.version,
        "changed": report["changed"],
        "owners": report["owners"],
        "warnings": warnings + report["warnings"],
    }


def uninstall(client_dir: str, mod_id: str) -> dict:
    """Remove a mod's client half; exact, from the stored originals."""
    running = blocks_install(client_dir)
    if running:
        raise InstallError(f"the game is running ({running}); close it and retry")
    state = _load_state(client_dir)
    if not any(install.mod_id == mod_id for install in state.installs):
        raise InstallError(f"{mod_id} is not installed on this client")
    removed = next(install for install in state.installs if install.mod_id == mod_id)
    provided = {
        str(capability)
        for item in removed.items
        if item.get("kind") == "patch"
        for capability in item.get("provides") or []
    }
    dependents = sorted(
        install.mod_id
        for install in state.installs
        if install.mod_id != mod_id
        and (mod_id in install.requires or provided & set(install.requires))
    )
    if dependents:
        raise InstallError(
            f"{mod_id} is required by {', '.join(dependents)}; uninstall those first"
        )
    installs = [install for install in state.installs if install.mod_id != mod_id]
    _clean_tmp(client_dir)
    report = _recompute(client_dir, state, installs)
    state.installs = installs
    _save_state(client_dir, state)
    _prune_members(client_dir, installs)
    return {
        "mod": mod_id,
        "changed": report["changed"],
        "owners": report["owners"],
        "warnings": report["warnings"],
    }


def status(client_dir: str) -> dict:
    """What is installed, who owns what, and what the state records."""
    state = _load_state(client_dir)
    owners, conflicts, warnings = _claim_owners(state.installs)
    return {
        "installs": [
            {
                "mod": install.mod_id,
                "version": install.version,
                "priority": install.priority,
                "requires": list(install.requires),
                "claims": list(install.claims),
            }
            for install in sorted(state.installs, key=lambda i: (i.priority, i.mod_id))
        ],
        "owners": dict(sorted(owners.items())),
        "conflicts": conflicts,
        "warnings": warnings,
        "destinations": {rel: dict(record) for rel, record in sorted(state.destinations.items())},
    }


def verify(client_dir: str) -> dict:
    """Read the client back: store integrity, conflicts, and drift."""
    state = _load_state(client_dir)
    problems: list[str] = []
    _owners, conflicts, warnings = _claim_owners(state.installs)
    problems += [f"conflict: {conflict}" for conflict in conflicts]
    for install in state.installs:
        for member, digest in sorted(install.members.items()):
            path = _member_store_path(client_dir, install.mod_id, install.version, member)
            if not os.path.isfile(path):
                problems.append(
                    f"{install.mod_id} {install.version}: payload member {member} is missing"
                )
            elif _sha_file(path) != digest:
                problems.append(
                    f"{install.mod_id} {install.version}: payload member {member} is corrupt"
                )
    try:
        content, owners, more = _compute(client_dir, state.installs, capture=False)
        warnings += more
        for rel in sorted(set(state.destinations) - set(content)):
            content[rel] = _pristine_bytes_for(client_dir, rel, capture=False)
        for rel, data in sorted(content.items()):
            path = resolve_existing(client_dir, rel)
            if data is None:
                if path is not None:
                    problems.append(f"{rel} should be absent")
                continue
            if path is None:
                problems.append(f"{rel} is missing")
            elif _read_file(path) != data:
                problems.append(f"{rel} was modified outside the tool")
        _verify_content(client_dir, content, state.installs, owners)
        _check_capabilities(client_dir, state.installs, content)
    except InstallError as error:
        problems.append(str(error))
    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "installs": [install.mod_id for install in state.installs],
    }
