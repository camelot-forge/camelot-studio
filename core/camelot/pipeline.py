"""Releases meet an environment: the dry run, the deploy, the way back (`R`).

`camelot project deploy N --env E` is the command; this module is what it does.
The dry run comes first and is mandatory (`pipeline.md` §D7): one row per item —
what we would write, what the target holds now, and why. It is the deploy
without the writes, not a summary of it, so the table the operator reads is the
table the deploy acts on.

An **environment** is a folder this machine names in `machine.json` (§D9). The
folder holds the environment's two halves: `client/` is that environment's
client — the author's install mode, with its own pristine store (C §D7) — and
`server/` is that environment's server, where the Installer reads a release from
`camelot/` (S §D2).

Two families, two destinations (§D2): the pack goes to the server's folder, and
the same pack's client half goes into the environment's client through the
machinery the operator's own install uses (`clientinstall`). The **order** is
the release's (§D6) — the client first whenever the server needs it. A release
the environment already holds writes nothing and says so (§D12).

What this module does not do: it never talks to a running server (S §D1, R
§D11 — the Installer is the only writer on that side), and it never publishes
the feed (C, phase P6). `promote()` moves the same bytes to the next
environment, never a rebuild (§D4).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import clientinstall, contract, installer
from . import project as project_mod
from . import release as release_mod
from .mpak import MpakArchive, MpakError

FORMAT = "camelot.plan/1"

#: The two halves of an environment's folder (§D9).
CLIENT = "client"
SERVER = "server"
#: The feed the players' launcher reads (C §D4): a directory in the project,
#: rebuildable and never committed.
FEED = "feed"
#: The install's own record of what a client holds (C §D7).
STATE = ".camelot/state.json"

#: The verdicts of one row (`pipeline.md` §3). `write` is the only one that
#: writes: `drift` is a target whose bytes moved under us and is refused unless
#: the operator adopts them (`--adopt`), and `refused` is a rule saying no
#: (§D7, S §D11 — nothing is written, anywhere, for either).
VERDICTS = ("write", "unchanged", "drift", "refused")

__all__ = [
    "FORMAT",
    "PipelineError",
    "client_of",
    "deploy",
    "feed",
    "folder",
    "installer_of",
    "live",
    "names",
    "plan",
    "promote",
    "verify",
]


class PipelineError(ValueError):
    """Anything this module refuses to do, with the reason in the message."""


# --- the environment ---------------------------------------------------------


def names(project: project_mod.Project) -> list[str]:
    """The environments the project declares — names, never paths: a path is
    this machine's own answer (`project.md` §D4)."""
    return list(project.environments)


def folder(project: project_mod.Project, env: str) -> Path:
    """Where an environment lives on this machine. Refused by name when the
    project does not declare it, or when this machine has no folder for it.

    `machine.json` gives either the folder alone (the first shape the format
    had) or an object with one: the object carries this machine's other answers
    too — the environment's database, its baseline, its connection — and `root`
    is the folder among them (`server-install.md` §D6).
    """
    declared = names(project)
    if str(env) not in declared:
        raise PipelineError(
            f"{project.name}: {env!r} is not one of its environments "
            f"({', '.join(declared) if declared else 'none declared'})"
        )
    mapped = (project_mod.machine(project.root).get("environments") or {}).get(str(env))
    root = mapped.get("root") if isinstance(mapped, dict) else mapped
    if not root:
        raise PipelineError(
            f"{project.name}: {env!r} has no folder on this machine — add "
            f'"{env}": "…" to {project_mod.MACHINE_FILE}'
        )
    return Path(str(root)).expanduser()


def client_of(env_root) -> Path:
    """The environment's client: what `clientinstall` writes into (C §D1)."""
    return Path(env_root) / CLIENT


def installer_of(env_root) -> Path:
    """The Installer's own folder, beside the server's files (S §D2). One
    definition, shared with the module that reads and writes it (`installer`)."""
    return installer.folder(env_root)


def _release_folder(env_root, number: int) -> Path:
    return installer.releases_folder(env_root, number)


def _relative(root, path: Path) -> str:
    return str(Path(path).relative_to(Path(root))).replace("\\", "/")


# --- reading a target --------------------------------------------------------


def _digest_file(path: Path) -> str:
    return release_mod.digest(Path(path).read_bytes())


def _archive_entry(path, entry: str) -> bytes | None:
    """One entry of an archive, or None when the archive or the entry is not
    there: a target that never had it is a `write`, not a failure."""
    if not Path(path).is_file():
        return None
    archive = MpakArchive(str(path))
    try:
        return archive.read(entry)
    except MpakError:
        return None
    finally:
        archive.close()


def _patchlist(root, record: dict) -> dict:
    """The client half's own record of what it would install (§D2)."""
    half = (record.get("artifacts") or {}).get("client") or {}
    path = Path(root) / str(half.get("patchlist") or "")
    if not half or not path.is_file():
        raise PipelineError(
            f"release {record.get('number')}: its client half is recorded as "
            f"{half.get('patchlist')!r}, which is not there — rebuild it "
            "(`camelot project verify --rebuild`) or cut again"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(f"{path}: {error}") from error
    return document


def _pack(record: dict, root) -> tuple[Path, clientinstall.ModPack]:
    """The release's pack, read: what the server receives and what the client
    install applies are the same file (§D2)."""
    rel = ((record.get("artifacts") or {}).get("server") or {}).get("pack")
    path = Path(root) / str(rel or "")
    if not rel or not path.is_file():
        raise PipelineError(
            f"release {record.get('number')}: its pack is recorded as {rel!r}, "
            "which is not there — rebuild it (`camelot project verify --rebuild`) "
            "or cut again"
        )
    try:
        return path, clientinstall.ModPack.load(str(path))
    except clientinstall.InstallError as error:
        raise PipelineError(f"{path}: {error}") from error


# --- the dry run -------------------------------------------------------------


def _client_items(record: dict, root, env_client: Path, *, adopt: bool) -> list[dict]:
    """One row per client destination: the entry inside its archive. Ours comes
    from the release's patchlist, theirs is read out of the target's own
    archive, and the target's own copy of the original (`clientinstall.original`)
    is what tells a plain write from drift."""
    document = _patchlist(root, record)
    items: list[dict] = []
    for item in document.get("items") or []:
        archive = str(item.get("archive") or "")
        entry = str(item.get("entry") or "")
        ours = str(item.get("sha256") or "")
        found = clientinstall.resolve_existing(str(env_client), archive)
        theirs = _archive_entry(found, entry) if found else None
        original = clientinstall.original_entry(str(env_client), archive, entry)
        if theirs is None:
            verdict, why = "write", "the target does not have it"
        elif release_mod.digest(theirs) == ours:
            verdict, why = "unchanged", "already ours"
        elif original is None:
            verdict, why = "write", "the target's bytes differ; it records no original"
        elif original == theirs:
            verdict, why = "write", "the target holds the original bytes"
        elif adopt:
            verdict = "write"
            why = (
                "the target holds neither its original nor ours; its current bytes "
                "become its original"
            )
        else:
            verdict, why = "drift", "the target holds neither its original nor ours"
        items.append(
            {
                "family": "client",
                "what": f"{archive}#{entry}",
                "ours": ours,
                "theirs": release_mod.digest(theirs) if theirs is not None else None,
                "size": item.get("size"),
                "verdict": verdict,
                "why": why,
            }
        )
    return items


def _record_item(record: dict, root, env_client: Path) -> dict:
    """The client family's own bookkeeping, as a row: the install writes its
    state file even when every destination already holds our bytes, and §D12
    says a deploy that changes nothing touches nothing — so this row is what
    makes that decidable."""
    _path, pack = _pack(record, root)
    found = clientinstall.status(str(env_client)) if Path(env_client).is_dir() else {}
    recorded = next(
        (item for item in found.get("installs") or [] if item.get("mod") == pack.mod_id),
        None,
    )
    ours = f"{pack.mod_id} {pack.version}"
    theirs = f"{recorded['mod']} {recorded['version']}" if recorded else None
    return {
        "family": "client",
        "what": STATE,
        "ours": ours,
        "theirs": theirs,
        "size": None,
        "verdict": "unchanged" if recorded else "write",
        "why": "already recorded" if recorded else "the target records no install of this pack",
    }


def _server_items(record: dict, root, env_root: Path, *, adopt: bool) -> list[dict]:
    """One row per file of the server package: what the Installer reads (§D5),
    at the destination this release names — an immutable folder, so a file that
    is already there with our hash is `unchanged` and one that differs is
    `drift` unless the operator adopts it."""
    facts = (record.get("artifacts") or {}).get("server") or {}
    number = int(record.get("number") or 0)
    wanted = []
    if facts.get("pack"):
        wanted.append((str(facts["pack"]), str(facts.get("sha256") or "")))
    if facts.get("rows"):
        wanted.append((str(facts["rows"]), str(facts.get("rows_sha256") or "")))
    items: list[dict] = []
    for rel, ours in wanted:
        target = _release_folder(env_root, number) / Path(rel).name
        theirs = _digest_file(target) if target.is_file() else None
        if theirs is None:
            verdict, why = "write", "the target does not have it"
        elif theirs == ours:
            verdict, why = "unchanged", "already ours"
        elif adopt:
            verdict = "write"
            why = (
                "the target holds bytes this release does not record; the release's "
                "own bytes replace them"
            )
        else:
            verdict, why = "drift", "the target holds bytes this release does not record"
        items.append(
            {
                "family": "server",
                "what": _relative(env_root, target),
                "source": rel,
                "ours": ours,
                "theirs": theirs,
                "size": (Path(root) / rel).stat().st_size if (Path(root) / rel).is_file() else None,
                "verdict": verdict,
                "why": why,
            }
        )
    return items


def _held_ids(root, env: str, number: int) -> dict[str, dict[int, int]]:
    """What the releases already deployed to this environment allocated: read
    from the deploy log and each release's own record, so the check is against
    what this environment holds, not against what the project holds."""
    held: dict[str, dict[int, int]] = {}
    for entry in project_mod.deploy_log(root):
        if entry.get("kind") not in ("deploy", "promote"):
            continue
        if str(entry.get("env")) != str(env) or entry.get("outcome") != "ok":
            continue
        seen = int(entry.get("release") or 0)
        if seen in (0, int(number)):
            continue
        try:
            record = release_mod.load(root, seen)
        except release_mod.ReleaseError:
            continue
        for zone, allocations in (record.get("ids") or {}).items():
            for row_id in (allocations or {}).values():
                held.setdefault(str(zone), {}).setdefault(int(row_id), seen)
    return held


def _id_refusals(record: dict, root, env: str) -> tuple[list[str], int]:
    """The id space, at the destination (§D9's collision check, as the deploy
    can make it today): an id this release allocates that an earlier release in
    this environment already allocated is refused, naming both."""
    held = _held_ids(root, env, int(record.get("number") or 0))
    refusals: list[str] = []
    checked = 0
    for zone, allocations in (record.get("ids") or {}).items():
        for row_id in (allocations or {}).values():
            checked += 1
            other = held.get(str(zone), {}).get(int(row_id))
            if other is not None:
                refusals.append(
                    f"id {row_id} in zone {zone} is already held by release {other} in {env}"
                )
    return refusals, checked


def _client_refusal(record: dict, env_client: Path) -> str | None:
    """What makes the client family un-deployable, or None: the target has to
    be a client, the game must not be playing from it, and the release was
    built against one build (C §D8)."""
    if not env_client.is_dir():
        return f"there is no client at {env_client}"
    running = clientinstall.blocks_install(str(env_client))
    if running:
        return f"the game is running ({running})"
    wanted = ((record.get("base") or {}).get("client") or {}).get("build")
    found = contract.client_build(str(env_client))
    if wanted and found and str(wanted) != str(found):
        return f"the target is client build {found}, the release was built against {wanted}"
    return None


def plan(root, number, *, env, adopt=False) -> dict:
    """The dry run: the table of `pipeline.md` §3 for one release against one
    environment, plus the three lines that belong to the release — its order,
    the reload it needs and the evidence the target carries.

    A destination whose bytes moved under us is `drift`, and a drift refuses
    the attempt unless *adopt* says the operator accepts what is there now as
    the target's own (`--adopt`, the same word `camelot client install` uses).
    """
    project = project_mod.load(root)
    record = release_mod.load(root, number)
    env_root = folder(project, env)
    env_client = client_of(env_root)

    items = _client_items(record, root, env_client, adopt=adopt)
    if items:
        items.append(_record_item(record, root, env_client))
    items += _server_items(record, root, env_root, adopt=adopt)

    refusals: list[str] = []
    if (record.get("artifacts") or {}).get("client"):
        reason = _client_refusal(record, env_client)
        if reason:
            refusals.append(reason)
            for item in items:
                if item["family"] == "client":
                    item["verdict"], item["why"] = "refused", reason
    refusals += [f"{item['what']}: {item['why']}" for item in items if item["verdict"] == "drift"]
    id_refusals, checked = _id_refusals(record, root, str(env))
    if id_refusals:
        refusals += id_refusals
        for item in items:
            if item["family"] == "server":
                item["verdict"], item["why"] = "refused", id_refusals[0]

    applied = installer.applied_path(env_root, number)
    counts = {verdict: 0 for verdict in VERDICTS}
    for item in items:
        counts[item["verdict"]] += 1
    return {
        "format": FORMAT,
        "project": project.name,
        "release": int(number),
        "env": str(env),
        "root": str(env_root),
        "order": record.get("order") or "server-only",
        "items": items,
        "counts": counts,
        "needs": record.get("needs") or {},
        "ids": {"checked": checked, "collisions": id_refusals},
        "evidence": {"path": str(applied), "present": applied.is_file()},
        "refusals": refusals,
    }


# --- the deploy --------------------------------------------------------------


def _writes(table: dict) -> list[dict]:
    return [item for item in table["items"] if item["verdict"] == "write"]


def _deploy_client(record: dict, root, table: dict, env_root: Path, *, adopt: bool) -> list[str]:
    """The client half, through the machinery the operator's own install uses:
    the same code that produces the local environment's client and the staged
    tree the feed publishes (C §2)."""
    pack_path, _ = _pack(record, root)
    report = clientinstall.install_pack(str(client_of(env_root)), str(pack_path), adopt=adopt)
    wrote = [f"{CLIENT}/{rel}" for rel in report.get("changed") or ()]
    if any(item["what"] == STATE for item in _writes(table)):
        wrote.append(f"{CLIENT}/{STATE}")
    return wrote


def _deploy_server(record: dict, root, table: dict, env_root: Path) -> list[str]:
    """The server half: the package into the folder the Installer reads (§D5).
    Nothing here talks to a server — the Installer is the only writer on that
    side (S §D1)."""
    wrote: list[str] = []
    for item in _writes(table):
        if item["family"] != "server":
            continue
        target = Path(env_root) / str(item["what"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(root) / str(item["source"]), target)
        wrote.append(str(item["what"]))
    return wrote


def deploy(root, number, *, env, adopt=False, source=None) -> dict:
    """Run the dry run, then the deploy it describes: the writes in the
    release's own order (§D6), one fact in the deploy log, and nothing at all
    when the environment already holds the release (§D12).

    *adopt* is the operator accepting bytes that changed outside the tool as
    the target's own (the client install's own word for it).

    `source` names the environment this release came from, when it is a
    promotion: the log line then says so (§D4).
    """
    table = plan(root, number, env=env, adopt=adopt)
    if table["refusals"]:
        raise PipelineError("; ".join(table["refusals"]))
    env_root = Path(table["root"])
    writes = _writes(table)
    wrote: list[str] = []
    if writes:
        order = [CLIENT, SERVER] if table["order"] == "client-first" else [SERVER, CLIENT]
        for family in order:
            if not any(item["family"] == family for item in writes):
                continue
            record = release_mod.load(root, number)
            if family == CLIENT:
                wrote += _deploy_client(record, root, table, env_root, adopt=adopt)
            else:
                wrote += _deploy_server(record, root, table, env_root)
    fact = {
        "kind": "promote" if source else "deploy",
        "env": str(env),
        "release": int(number),
        "outcome": "ok" if writes else "unchanged",
        "order": table["order"],
        "items": dict(table["counts"]),
        "wrote": wrote,
        "needs": table["needs"],
    }
    if source:
        fact["from"] = str(source)
    return {
        "plan": table,
        "outcome": fact["outcome"],
        "wrote": wrote,
        "fact": project_mod.record_deploy(root, fact),
    }


def promote(root, number, *, frm, to, adopt=False) -> dict:
    """The same bytes to the next environment (§D4): refused unless the release
    is already deployed to `frm` — a promotion moves an artifact that is live,
    it does not replace a deploy."""
    live_now = live(root).get(str(frm))
    if not live_now or int(live_now.get("release") or 0) != int(number):
        where = (
            f"it holds release {live_now['release']}" if live_now else "nothing is deployed there"
        )
        raise PipelineError(
            f"release {number} is not deployed to {frm} ({where}): deploy it there "
            "first — a promotion moves the same bytes, it does not rebuild them"
        )
    return deploy(root, number, env=to, adopt=adopt, source=frm)


# --- reading an environment back ---------------------------------------------


def live(root, *, env=None) -> dict[str, dict]:
    """What the deploy log says each environment holds: the newest attempt that
    succeeded, per environment. The log is append-only, so the answer is read,
    never inferred (§D8)."""
    found: dict[str, dict] = {}
    for entry in project_mod.deploy_log(root):
        if entry.get("kind") not in ("deploy", "promote"):
            continue
        if entry.get("outcome") != "ok":
            continue
        name = str(entry.get("env") or "")
        if not name:
            continue
        if str(entry.get("at") or "") >= str(found.get(name, {}).get("at") or ""):
            found[name] = {
                "release": int(entry.get("release") or 0),
                "at": entry.get("at"),
                "from": entry.get("from"),
            }
    if env is not None:
        return {str(env): found[str(env)]} if str(env) in found else {}
    return dict(sorted(found.items()))


def feed(root) -> dict:
    """What the players can fetch, read from the project's own `feed/` (C §D4): the
    launcher's two files, and our record beside them. A feed that is not there yet is a
    fact, not an error — `camelot project feed` is what builds one."""
    from . import feed as feed_mod

    where = Path(root) / FEED
    found: dict = {
        "path": str(where),
        "present": where.is_dir(),
        "version": None,
        "files": 0,
        "bytes": 0,
        "published": 0,
        "published_bytes": 0,
        "release": None,
    }
    if not found["present"]:
        return found
    try:
        read_back = feed_mod.read(where)
    except feed_mod.FeedError:
        return found  # a folder without the two files is not a feed yet
    record = read_back["record"] or {}
    return {
        **found,
        "version": read_back["version"],
        "files": read_back["files"],
        "bytes": read_back["bytes"],
        "published": len(record.get("changed") or ()),
        "published_bytes": int(record.get("bytes") or 0),
        "release": record.get("release"),
        "base_url": read_back["base_url"],
    }


def verify(root, *, env) -> dict:
    """Read one environment back (§D11): the server's folder against the
    release's own hashes, and the client through the install's own
    verification. One line per environment is the CLI's job; this is its
    arithmetic."""
    project = project_mod.load(root)
    env_root = folder(project, env)
    env_client = client_of(env_root)
    problems: list[str] = []
    server: dict = {"release": None, "files": []}
    deployed = live(root, env=str(env)).get(str(env))
    if deployed and deployed.get("release"):
        number = int(deployed["release"])
        server["release"] = number
        record = release_mod.load(root, number)
        facts = (record.get("artifacts") or {}).get("server") or {}
        wanted = []
        if facts.get("pack"):
            wanted.append((str(facts["pack"]), str(facts.get("sha256") or "")))
        if facts.get("rows"):
            wanted.append((str(facts["rows"]), str(facts.get("rows_sha256") or "")))
        for rel, ours in wanted:
            target = _release_folder(env_root, number) / Path(rel).name
            found = _digest_file(target) if target.is_file() else None
            ok = found == ours
            server["files"].append({"what": _relative(env_root, target), "ok": ok})
            if not ok:
                problems.append(
                    f"{_relative(env_root, target)}: "
                    + ("is not there" if found is None else "does not match the release")
                )
    client = {"ok": None, "problems": [], "installs": []}
    if env_client.is_dir():
        client = clientinstall.verify(str(env_client))
        problems += [f"{CLIENT}: {problem}" for problem in client.get("problems") or ()]
    else:
        client = {"ok": False, "problems": [f"there is no client at {env_client}"], "installs": []}
        problems += [f"there is no client at {env_client}"]
    return {
        "env": str(env),
        "root": str(env_root),
        "release": server["release"],
        "server": server,
        "client": client,
        "problems": problems,
        "ok": not problems,
    }
