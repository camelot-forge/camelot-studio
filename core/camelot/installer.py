"""The Installer's contract (`server-install.md` §2, §D5), as data, checks and one
step.

The C# Installer is a later piece; the **contract is ours**, and this module is
it — the two documents it reads and writes, and the three checks it makes
before it writes anything:

- the folder: `<server>/camelot/releases/NNNN/` holds what Deploy handed over
  (the pack and, when the range patched rows, `rows.json`);
- `state.json`: per release and per table, the rows written, the values they had
  before, and the ids allocated — what every other surface reads;
- `applied/NNNN/applied.json`: one line per row, `written` / `skipped` /
  `refused`, which is the evidence a release carries for an environment
  (`pipeline.md` §D8).

`apply()` is the step itself, for an environment whose database this machine can
reach: a local test server this project provisioned, which has no C# Installer
to run (`camelot env apply local`). On a server the same step is the Installer's
own — nothing else writes a server's tables (§D1). It writes through
`serverrows`, the same code the editor uses, so it holds no SQL of its own.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import journal as journal_mod
from . import serverrows, targets
from .targets.opendaoc import OWNED_TABLES, owned  # noqa: F401  (the map, re-exported)

STATE_FORMAT = "camelot.installer.state/1"
APPLIED_FORMAT = "camelot.installer.applied/1"

SERVER = "server"
INSTALLER = "camelot"
STATE = "state.json"
RELEASES = "releases"
APPLIED = "applied"

__all__ = [
    "APPLIED",
    "APPLIED_FORMAT",
    "INSTALLER",
    "RELEASES",
    "SERVER",
    "STATE",
    "STATE_FORMAT",
    "InstallerError",
    "applied_path",
    "applied_releases",
    "apply",
    "empty_state",
    "folder",
    "handed_over",
    "id_collisions",
    "owned",
    "read_applied",
    "read_state",
    "refused_tables",
    "released",
    "releases_folder",
    "reset",
    "state_path",
    "write_applied",
    "written_state",
]


class InstallerError(ValueError):
    """Anything the contract refuses, with the reason in the message."""


def folder(env_root) -> Path:
    """The Installer's own folder, beside the server's files (§2)."""
    return Path(env_root) / SERVER / INSTALLER


def state_path(env_root) -> Path:
    return folder(env_root) / STATE


def releases_folder(env_root, number=None) -> Path:
    root = folder(env_root) / RELEASES
    return root / f"{int(number):04d}" if number is not None else root


def applied_path(env_root, number) -> Path:
    return folder(env_root) / APPLIED / f"{int(number):04d}" / "applied.json"


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise InstallerError(f"{path}: expected an object")
    return document


def empty_state(project: str) -> dict:
    """What the Installer writes before it has applied anything: the project it
    serves, the baseline it was built on, and an empty record of releases."""
    return {
        "format": STATE_FORMAT,
        "project": str(project),
        "baseline": {},
        "releases": {},
        "updated": journal_mod.now(),
    }


def read_state(env_root, project: str | None = None) -> dict:
    """The environment's own record of what it applied. A state file another
    project wrote is refused by name: two projects on one server would fight
    over the same rows, which is what `project.md` §D1 retired."""
    document = _read(state_path(env_root))
    if not document:
        return empty_state(project or "")
    found = document.get("format")
    if found != STATE_FORMAT:
        raise InstallerError(
            f"{state_path(env_root)}: format {found or '(none)'} is not one this build "
            f"reads (it reads {STATE_FORMAT})"
        )
    if project and document.get("project") and document["project"] != project:
        raise InstallerError(
            f"{state_path(env_root)}: this environment was provisioned for "
            f"{document['project']!r}, not {project!r}"
        )
    return {**empty_state(project or ""), **document}


def written_state(env_root, state: dict) -> Path:
    """Write the record, whole: it is read by every other surface, and a half
    document is worse than none."""
    path = state_path(env_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({**state, "updated": journal_mod.now()}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def read_applied(env_root, number) -> dict:
    """What one release did to this environment, row by row."""
    document = _read(applied_path(env_root, number))
    if document and document.get("format") != APPLIED_FORMAT:
        raise InstallerError(
            f"{applied_path(env_root, number)}: format {document.get('format')} is not "
            f"one this build reads (it reads {APPLIED_FORMAT})"
        )
    return document


def write_applied(env_root, number, document: dict) -> Path:
    """The evidence one release carries for this environment (`pipeline.md`
    §D8): written whole, beside the release it describes."""
    path = applied_path(env_root, number)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def id_collisions(allocated: dict, held: dict) -> list[str]:
    """§D9's check, as data: an id a release allocates that the server's own
    tables already hold is a collision, and it is named with the row that holds
    it. `allocated` is the release's own record (`{zone: {key: id}}`), `held`
    what the server answers for the tables it would write
    (`{table: {id: key}}`).

    Allocating an id the server *grew* into is not a collision: the release
    allocated against a snapshot, and a row above it is somebody else's content,
    not ours ("grown but no collision → apply and report").
    """
    found: list[str] = []
    for zone, rows in sorted((allocated or {}).items()):
        for key, row_id in sorted((rows or {}).items()):
            row_id = int(row_id)
            for table, taken in sorted((held or {}).items()):
                holder = (taken or {}).get(row_id)
                if holder is not None:
                    found.append(
                        f"id {row_id} (zone {zone}, {key}) is already held by {table} {holder}"
                    )
    return found


def released(state: dict, number) -> dict | None:
    """What the state says about one release, or None — never a guess from the
    files: "applied" is a recorded fact (`server-install.md` §D8)."""
    return (state.get("releases") or {}).get(f"{int(number):04d}")


def refused_tables(keys) -> list[str]:
    """The tables a release names that the project does not own
    (`server-install.md` §D4): refused by name, before anything is written."""
    return sorted({str(key) for key in keys if not owned(str(key))})


def handed_over(env_root) -> list[int]:
    """The releases Deploy has put in the environment's folder, in order —
    what the step has to read, regardless of what state.json records."""
    root = releases_folder(env_root)
    if not root.is_dir():
        return []
    return sorted(
        int(path.name) for path in root.glob("*") if path.is_dir() and path.name.isdigit()
    )


def applied_releases(env_root) -> list[int]:
    """The releases this environment carries a written record for, in order."""
    root = folder(env_root) / APPLIED
    if not root.is_dir():
        return []
    return sorted(int(path.parent.name) for path in root.glob("*/applied.json"))


def reset(env_root, *, project: str, baseline=None) -> dict:
    """Start the environment's own record over, because its database was
    replaced (`server-install.md` §D6, `camelot env provision`).

    The state and the applied releases describe **that** database — the rows and
    the values they had before — so they go with it, or the folder would claim
    releases nothing holds any more. What Deploy handed over stays: packages are
    files, and the next apply is what puts their rows back.
    """
    where = folder(env_root)
    dropped: list[str] = []
    if (where / APPLIED).is_dir():
        dropped = [f"{number:04d}" for number in applied_releases(env_root)]
        shutil.rmtree(where / APPLIED)
    state = empty_state(project)
    state["baseline"] = dict(baseline or {})
    written_state(env_root, state)
    return {
        "dropped": dropped,
        "kept": [f"{number:04d}" for number in handed_over(env_root)],
        "state": str(state_path(env_root)),
    }


# --- the step ------------------------------------------------------------------


def rows_of(release_dir) -> list[dict]:
    """The rows a handed-over release carries, flattened in the order they were
    written: `rows.json` is what the cut puts beside the pack when the range
    patched rows (`release.py`), and a release that patched none has none."""
    path = Path(release_dir) / "rows.json"
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError(f"{path}: {error}") from error
    found: list[dict] = []
    for _zone, group in sorted((document.get("patches") or {}).items()):
        for patch in group or ():
            if isinstance(patch, dict):
                found.append(patch)
    return found


def _current(connection, table: str, key: str) -> dict | None:
    """The row as the server holds it now, or None — a release never adds rows
    to the server's own tables (they are the client's, §D3)."""
    column = targets.owned_key(table)
    if column is None:
        raise InstallerError(f"{table!r} is not a table this project owns")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT * FROM `{table}` WHERE `{column}` = %s", (key,))
        return cursor.fetchone()


def apply(
    env_root,
    release_dir,
    *,
    connect,
    project: str,
    number: int,
    needs=(),
    at=None,
) -> dict:
    """Apply one release's rows through the connection factory *connect*.

    All or nothing (§D11): a row that is not there refuses the whole release,
    nothing is written, and `state.json` is not touched. What the environment
    already holds is `skipped`, not rewritten (§D14).
    """
    rows = rows_of(release_dir)
    refused = refused_tables([str(row.get("table") or "") for row in rows])
    if refused:
        raise InstallerError(
            f"release {int(number):04d} names "
            f"{', '.join(refused)}: this project does not own that table "
            "(the Installer refuses it by name, before anything is written)"
        )
    state = read_state(env_root, project)
    if released(state, number):
        return {
            "format": APPLIED_FORMAT,
            "release": int(number),
            "at": (released(state, number) or {}).get("at"),
            "rows": [],
            "written": 0,
            "skipped": len(rows),
            "refused": 0,
            "unchanged": True,
        }

    handle = connect()
    lines: list[dict] = []
    originals: dict[str, dict[str, dict]] = {}
    to_write: list[dict] = []
    try:
        for row in rows:
            table, key = str(row.get("table") or ""), str(row.get("key") or "")
            if not table or not key:
                raise InstallerError(
                    f"release {int(number):04d}: a row patch needs its table and its key"
                )
            current = _current(handle, table, key)
            if current is None:
                raise InstallerError(
                    f"{table}: no row with {targets.owned_key(table)} {key!r} — the release "
                    "cannot write it, and nothing was written"
                )
            wanted = {name: str(value) for name, value in (row.get("values") or {}).items()}
            changed = {
                name: value for name, value in wanted.items() if str(current.get(name)) != value
            }
            if changed:
                originals.setdefault(table, {})[key] = {name: current.get(name) for name in changed}
                to_write.append({"table": table, "key": key, "values": changed})
                lines.append(
                    {
                        "table": table,
                        "key": key,
                        "action": "write",
                        "why": f"{len(changed)} value(s): {', '.join(sorted(changed))}",
                    }
                )
            else:
                lines.append(
                    {
                        "table": table,
                        "key": key,
                        "action": "skip",
                        "why": "already what the release says",
                    }
                )
    finally:
        handle.close()

    if to_write:
        serverrows.apply_patches(connect, to_write)

    applied = {
        "format": APPLIED_FORMAT,
        "release": int(number),
        "at": journal_mod.now(at),
        "rows": lines,
        "written": sum(1 for line in lines if line["action"] == "write"),
        "skipped": sum(1 for line in lines if line["action"] == "skip"),
        "refused": sum(1 for line in lines if line["action"] == "refused"),
    }
    write_applied(env_root, number, applied)
    per_table: dict[str, dict[str, int]] = {}
    for line in lines:
        counted = per_table.setdefault(line["table"], {"written": 0, "skipped": 0})
        counted["written" if line["action"] == "write" else "skipped"] += 1
    state = read_state(env_root, project)
    state["releases"][f"{int(number):04d}"] = {
        "at": applied["at"],
        "tables": per_table,
        "originals": originals,
        "needs": [str(name) for name in needs],
    }
    written_state(env_root, state)
    return applied
