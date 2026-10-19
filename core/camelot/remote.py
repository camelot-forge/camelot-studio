"""The pull: what a server does with the `camelot-deploy` branch (`remote.md` §D4).

The Console — or a job — fetches the branch, verifies the release record's
hashes, hands the server half over to the environment and writes the **wanted**
marker the Installer reads. It never applies rows and never opens the database:
the Installer is the only writer on the server side (S §D1), and the Console
asks it (S §D10).

The checkout is a mirror: the pull only ever fast-forwards it, and the feed
mirror only adds or replaces files (the feed accumulates by design, `F §D3`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import feed as feed_mod
from . import installer
from . import journal as journal_mod
from . import pipeline as pipeline_mod
from . import project as project_mod
from . import publish as publish_mod
from . import release as release_mod
from . import sign as sign_mod

__all__ = ["FORMAT", "PullError", "pull", "read_wanted", "wanted_path", "write_wanted"]

FORMAT = "camelot.wanted/1"
WANTED = "wanted.json"


class PullError(ValueError):
    """Anything a pull refuses, with the reason in the message."""


def _git(root, *args, check=True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise PullError("git is not installed: a pull needs it") from error
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise PullError(f"git {' '.join(args)}: {detail[-1] if detail else result.returncode}")
    return result.stdout


# --- the wanted marker ---------------------------------------------------------


def wanted_path(env_root) -> Path:
    """Beside the Installer's state, in `<env>/server/camelot/`."""
    return installer.folder(env_root) / WANTED


def read_wanted(env_root) -> dict:
    path = wanted_path(env_root)
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PullError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise PullError(f"{path}: expected an object")
    return document


def write_wanted(env_root, releases, *, by="pull", at=None) -> Path:
    """What the Console asks the Installer to apply (`S §D10`): the releases it
    just handed over, oldest first, and who asked."""
    path = wanted_path(env_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "format": FORMAT,
        "releases": [int(number) for number in releases],
        "by": str(by),
        "at": journal_mod.now(at),
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- the pull ------------------------------------------------------------------


def _verify(deploy: Path, record: dict) -> list[tuple[str, str]]:
    """The recorded artifacts, as `(relative path, digest)` pairs, each read
    back from the checkout (`remote.md` §D7). A mismatch is refused by name."""
    artifacts = record.get("artifacts") or {}
    server = artifacts.get("server") or {}
    client = artifacts.get("client") or {}
    wanted = [
        (str(server.get("pack") or ""), str(server.get("sha256") or "")),
        (str(server.get("rows") or ""), str(server.get("rows_sha256") or "")),
        (str(client.get("patchlist") or ""), str(client.get("sha256") or "")),
    ]
    found: list[tuple[str, str]] = []
    for relative, digest in wanted:
        if not relative:
            continue
        path = deploy / relative
        if not path.is_file():
            raise PullError(f"release {int(record.get('number') or 0):04d}: {relative} is not in the checkout")
        if digest and release_mod.digest(path.read_bytes()) != digest:
            raise PullError(
                f"{relative}: the checkout's bytes do not hash what the release record says "
                "— refusing to hand it over"
            )
        found.append((relative, digest))
    return found


def _check_signature(deploy: Path, number: int, key) -> dict:
    """Who signed the release record, checked **before** its hashes are believed
    (`remote.md` §D7).

    The hashes travel in the same commit as the artifacts they describe, so on
    their own they prove nothing about a branch somebody else wrote. This is the
    step that makes them mean something, and it runs first for that reason.

    No key configured is not a refusal: it is the state every server was in
    before there was a key, and it is reported rather than hidden, so a Console
    can say that this server does not check who signed a release.
    """
    record = release_mod.record_path(deploy, number)
    if key is None:
        return {
            "checked": False,
            "key": None,
            "why": "no release key is configured: this server does not check who signed "
            "a release",
        }
    if not record.is_file():
        raise PullError(f"{record}: no release record to check a signature against")
    signature = record.with_name(record.name + sign_mod.SIGNATURE)
    if not signature.is_file():
        raise PullError(
            f"release {int(number):04d}: the branch carries no {signature.name} — this "
            "server checks who signed a release, so an unsigned one is refused "
            "(`remote.md` §D7)"
        )
    try:
        found = sign_mod.verify(key, record.read_bytes(), signature.read_bytes())
    except sign_mod.SignError as error:
        raise PullError(str(error)) from error
    if not found["ok"]:
        raise PullError(
            f"release {int(number):04d}: the signature does not check out against "
            f"{Path(key).expanduser()} ({found['why']}) — refusing to hand it over"
        )
    return {"checked": True, "key": found["key"], "why": ""}


def _handover(deploy: Path, env_root: Path, record: dict, files: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """The server half into `<env>/server/camelot/releases/NNNN/` — the same
    destination Deploy writes on a local environment (`R §D5`). A file the
    environment holds with other bytes is a drift: refused, nothing written."""
    number = int(record.get("number") or 0)
    folder = installer.releases_folder(env_root, number)
    server_prefix = ("releases", f"{number:04d}", "server")
    written: list[str] = []
    unchanged: list[str] = []
    for relative, digest in files:
        source = deploy / relative
        if tuple(Path(relative).parts[:3]) != server_prefix:
            continue  # the client half stays in the feed (`remote.md` §2)
        target = folder / source.name
        if target.is_file():
            if digest and release_mod.digest(target.read_bytes()) != digest:
                raise PullError(
                    f"{target}: the environment holds bytes this release does not record "
                    "— refusing to overwrite"
                )
            unchanged.append(str(target))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        written.append(str(target))
    return written, unchanged


def _mirror_feed(source: Path, target: Path) -> int:
    """The pulled feed into the directory the service serves: files are added or
    replaced, never removed — the feed accumulates, and a straggler still needs
    what earlier publishes put there (`F §D3`)."""
    if not (source / feed_mod.VERSION_FILE).is_file():
        raise PullError(f"{source}: not a feed (no {feed_mod.VERSION_FILE})")
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if destination.is_file() and destination.read_bytes() == item.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1
    return copied


def pull(
    deploy,
    env=None,
    *,
    remote=None,
    branch=publish_mod.BRANCH,
    feed=None,
    release_key=None,
    by="pull",
    at=None,
) -> dict:
    """Fetch the deploy branch, check who signed the newest publish, verify its
    hashes, hand its server half over and mark it wanted. Nothing is applied here
    (`S §D1`).

    *deploy* is the checkout of the deploy branch — a project, with this
    machine's `machine.json` naming the environment. *remote* fetches and
    fast-forwards it first; without one, the checkout is used as it stands.
    *feed* mirrors the pulled feed into the directory the service serves.
    *release_key* is the operator's public key; without one, `machine.json`'s
    `release_key` is used, and without either the release is handed over
    unsigned — reported, never hidden (`remote.md` §D7).
    """
    deploy = Path(deploy).expanduser()
    project = project_mod.load(deploy)
    if env is None:
        declared = pipeline_mod.names(project)
        if len(declared) != 1:
            raise PullError(
                f"{project.name}: name the environment to pull to ({', '.join(declared)})"
            )
        env = declared[0]

    fetched = False
    if remote:
        _git(deploy, "fetch", "--prune", str(remote), branch)
        _git(deploy, "reset", "--hard", "FETCH_HEAD")
        fetched = True

    # Read before anything is written: a checkout without git cannot name its head,
    # and a pull that cannot finish must fail before it hands anything over.
    head = _git(deploy, "rev-parse", "HEAD").strip()

    marker_path = deploy / publish_mod.MARKER
    if not marker_path.is_file():
        raise PullError(
            f"{marker_path}: no marker — this checkout carries no publish "
            "(`camelot project publish` writes one)"
        )
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PullError(f"{marker_path}: {error}") from error
    if not isinstance(marker, dict) or marker.get("format") != publish_mod.FORMAT:
        raise PullError(f"{marker_path}: not a {publish_mod.FORMAT} document")
    number = int(marker.get("release") or 0)
    if not number:
        raise PullError(f"{marker_path}: names no release")
    record = release_mod.load(deploy, number)

    # Who signed it, then whether the bytes are the ones it names: the hashes
    # only mean something once the record they are written in is trusted.
    signature = _check_signature(
        deploy, number, sign_mod.configured(release_key, project_mod.machine(deploy))
    )
    files = _verify(deploy, record)
    env_root = pipeline_mod.folder(project, env)
    written, unchanged = _handover(deploy, env_root, record, files)
    feed_report = None
    if feed:
        source = deploy / feed_mod.FEED
        copied = _mirror_feed(source, Path(feed).expanduser())
        feed_report = {
            "path": str(Path(feed).expanduser()),
            "version": (marker.get("feed") or {}).get("version"),
            "files": copied,
        }
    wanted = write_wanted(env_root, [number], by=by, at=at)

    state = installer.read_state(env_root, project.name)
    applied = installer.released(state, number) is not None
    return {
        "env": str(env),
        "root": str(env_root),
        "release": number,
        "branch": branch,
        "head": head,
        "fetched": fetched,
        "signature": signature,
        "wrote": written,
        "unchanged": unchanged,
        "wanted": str(wanted),
        "applied": applied,
        "feed": feed_report,
    }
