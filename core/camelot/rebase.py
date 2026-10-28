"""Re-base: what to do when the base moved under a project (phase P8).

One view and two decisions. The view is per resource — **ours** (the fingerprint
the project recorded), **theirs** (what the client holds now), and **the log**
(the ops that touch that resource, each with a verdict against the base as it
stands). The decisions are **keep ours** (pin the fingerprints, and every deploy
warns) and **adopt theirs** (move the fingerprints and print the same list).
Nothing is written without one of them, and neither repairs an op.
"""

from __future__ import annotations

from pathlib import Path

from . import gamedata
from . import journal as journal_mod
from . import project as project_mod

__all__ = [
    "RebaseError",
    "adopt",
    "analysis",
    "keep_ours",
]


class RebaseError(ValueError):
    """Raised when a re-base cannot be read or written."""


# -- the view ------------------------------------------------------------------


def analysis(project: project_mod.Project, client=None) -> dict:
    """The per-resource view of a moved base: what changed, and what the
    journal says about it (§D17).

    Read-only. `resources` is what `verify` found, non-`ok` ones only, with the
    two fingerprints; `journal` names the ops that *write* a moved resource,
    replayed against the base as it stands, one verdict each (§D18).
    """
    directory = project_mod.client_root(project, client)
    if directory is None:
        raise RebaseError(
            f"{project.name}: no client resolved — write {project_mod.MACHINE_FILE}, "
            "set $DAOC_CLIENT, or pass --client"
        )
    findings = project_mod.verify(project, directory)
    recorded = project.fingerprints
    resources = [
        {
            "resource": finding["resource"],
            "state": finding["state"],
            "ours": recorded.get(finding["resource"], ""),
            "theirs": _found(directory, finding),
            "detail": finding["detail"],
        }
        for finding in findings
        if finding["state"] != "ok"
    ]

    journal = journal_mod.Journal(directory=project.path("journal"))
    pending = journal_mod.pending_keys(
        journal, published=_published_keys(project), cut=_newest_cut(project)
    )
    groups: dict[str, list[journal_mod.Line]] = {}
    for line in journal.live():
        if not line.op:
            continue
        written = _written(project, directory, line)
        if written is not None:
            groups.setdefault(written, []).append(line)

    moved = {item["resource"] for item in resources}
    report = []
    for resource, lines in sorted(groups.items()):
        if resource not in moved:
            continue
        report.append(_replayed(directory, resource, lines, pending))

    return {
        "name": project.name,
        "root": str(project.root),
        "client": str(directory),
        "clean": not resources,
        "resources": resources,
        "journal": report,
        #: A zone the journal writes but whose table was never fingerprinted:
        #: a client update there would be absorbed in silence, which is the one
        #: thing this phase exists to prevent (`project.md` §D16).
        "unwatched": _unwatched(project, directory, journal.live()),
        "moved": sum(1 for item in resources if item["state"] == "moved"),
        "missing": sum(1 for item in resources if item["state"] == "missing"),
        "pinned": sum(1 for item in resources if item["state"] == "pinned"),
        "conflicts": sum(
            item["conflicts"] for item in report
        ),
    }


def _unwatched(
    project: project_mod.Project, directory: Path, lines: list[journal_mod.Line]
) -> list[dict]:
    """The zones the journal writes but `project.json` never fingerprinted.

    A zone whose `fixtures.csv` is not recorded can move under the project and
    no `verify` will say so: the re-base has to name it, or the phase's promise
    (nothing is absorbed in silence) is false for that zone.
    """
    watched = set(project.fingerprints)
    found = []
    zones = sorted(
        {
            line.zone
            for line in lines
            if line.family == "world" and line.zone is not None and line.op
        }
    )
    for zone_id in zones:
        resources = [
            resource
            for resource in project_mod.zone_resources(directory, zone_id)
            if resource.endswith("#fixtures.csv")
        ]
        if not resources or any(resource in watched for resource in resources):
            continue
        found.append(
            {
                "zone": zone_id,
                "resource": resources[0],
                "ops": sum(
                    1
                    for line in lines
                    if line.family == "world" and line.zone == zone_id and line.op
                ),
            }
        )
    return found


def _found(directory: Path, finding: dict) -> str | None:
    """What the client holds for a resource now — nothing when it is gone."""
    if finding["state"] == "missing":
        return None
    try:
        return project_mod.fingerprint_resource(directory, finding["resource"])
    except project_mod.ProjectError:
        return None


def _written(
    project: project_mod.Project, directory: Path, line: journal_mod.Line
) -> str | None:
    """The resource an op writes, or nothing when it writes none of ours.

    A `world` op writes its zone's `fixtures.csv` — the entry the client reads
    and the editor publishes. A `tables` op writes the `gamedata` entry it
    names. A `rows` op writes the environment's database, which a client
    re-base does not touch, so it has no resource here.
    """
    if line.family == "world" and line.zone is not None:
        for resource in project_mod.zone_resources(directory, line.zone):
            if resource.endswith("#fixtures.csv"):
                return resource
        return None
    if line.family == "tables":
        table = str(line.op.get("table") or "")
        return f"{project_mod.GAMEDATA_ARCHIVE}#{table}" if table else None
    return None


def _replayed(
    directory: Path,
    resource: str,
    lines: list[journal_mod.Line],
    pending: set[str],
) -> dict:
    """One resource's ops, replayed against the base as the client holds it."""
    ops = [line.op for line in lines]
    covered = {index for index, line in enumerate(lines) if line.key not in pending}
    entry = {
        "resource": resource,
        "ops": [],
        "conflicts": 0,
        "unwalked": 0,
        "note": "",
    }
    try:
        raw = project_mod.read_resource(directory, resource)
        table = gamedata.parse_table(resource.rpartition("#")[2] or resource, raw)
    except (project_mod.ProjectError, gamedata.TableError) as error:
        entry["note"] = f"the base cannot be read back: {error}"
        entry["unwalked"] = len(ops)
        return entry
    _, findings = gamedata.apply_ops(table, ops, covered=covered)
    for finding in findings:
        line = lines[finding["index"]]
        entry["ops"].append(
            {
                "key": line.key,
                "where": line.where,
                "row": finding["row"],
                "verdict": finding["verdict"],
                "detail": finding["detail"],
            }
        )
    conflict = next((item for item in findings if item["verdict"] == "conflict"), None)
    if conflict is not None:
        entry["conflicts"] = 1
        entry["unwalked"] = len(ops) - conflict["index"] - 1
    return entry


# -- the two decisions ---------------------------------------------------------


def keep_ours(project: project_mod.Project, *, client=None) -> dict:
    """**Keep ours** (§D17): pin the values the client holds now.

    The recorded fingerprints stay the project's base, and the values the
    operator refused are written beside them, so the next `verify` says
    `pinned` instead of `moved`. A resource that is *gone* is not pinned:
    there is no value to keep, and a missing base file stays a problem.
    """
    view = analysis(project, client)
    values = {
        item["resource"]: item["theirs"]
        for item in view["resources"]
        if item["theirs"] is not None
    }
    project_mod.pin_resources(project.root, values)
    return {**view, "pinned_now": len(values)}


def adopt(project: project_mod.Project, *, client=None) -> dict:
    """**Adopt theirs** (§D17): move the fingerprints to what the client holds.

    Returns the view it was decided on: the ops that no longer fit the new base
    are in it, with both values. Adopting records a base — it repairs no op.
    """
    view = analysis(project, client)
    found = {
        item["resource"]: item["theirs"]
        for item in view["resources"]
        if item["theirs"] is not None
    }
    if found:
        project_mod.add_fingerprints(project.root, found, update=True)
    return {**view, "adopted": len(found)}


# -- what a publish and a release already wrote --------------------------------


def _published_keys(project: project_mod.Project) -> set[str]:
    """The journal lines a publish covered — the same read the editor makes."""
    keys: set[str] = set()
    for entry in project_mod.deploy_log(project.root):
        if entry.get("kind") == "publish":
            keys.update(str(key) for key in entry.get("keys") or ())
    return keys


def _newest_cut(project: project_mod.Project) -> dict[str, int] | None:
    """The newest release's cut, when the project has one. A read of the
    records, never the engine: a re-base builds nothing."""
    from . import release as release_mod

    try:
        history = release_mod.releases(project.root)
    except release_mod.ReleaseError:
        return None
    return dict(history[-1].get("log") or {}) if history else None
