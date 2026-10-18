"""The Console's read-only face: what is live on a running server (P7 7a).

The Console is the only surface that faces a server **while it runs**
(`console.md` §D1). This module is phase 7a and writes nothing: it reads the
Installer's own state (`console.md` §D3), the feed's announcement, the deploy
log when the operator publishes it beside the server, and the server's ports.

The channel was measured first (`console.md` §D5, M-3, 2026-10-18):

- OpenDAoC exposes a read-only HTTP API on `:5000` when the server property
  `atlas_api` is on (default false): stats, players, guilds, realms, news;
- the only action it exposes is `GET /utils/shutdown/{api_password}`; there is
  no reload, message, kick or ban endpoint;
- `/reload` and `/refresh` are `Cmd` handlers reached by the game's command
  parser from a GM character. The server has no console stdin and no command
  hook, so 7b's reloads and messages stay manual until a channel is added.

The page says that boundary instead of hiding it. One operator credential
guards the Console when it listens beyond loopback; accounts, roles and the
audit line arrive with 7b's actions.
"""

from __future__ import annotations

import base64
import hmac
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import feed as feed_mod
from . import installer
from . import project as project_mod
from . import publish as publish_mod
from . import remote as remote_mod

__all__ = [
    "AUDIT_FORMAT",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "FORMAT",
    "LOG_LINES",
    "ConsoleError",
    "handler_for",
    "live",
    "main",
    "page",
    "run",
    "serve",
    "server_for",
]

FORMAT = "camelot.console.live/1"
AUDIT_FORMAT = "camelot.console.audit/1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
#: How many deploy-log lines the page shows. The log is never truncated.
LOG_LINES = 20
#: How many audit lines the page shows; the file keeps them all.
AUDIT_LINES = 10
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

#: What M-3 measured, stated once and shown on the page (`console.md` §D5).
CHANNEL = {
    "read": (
        "OpenDAoC's API on :5000 when atlas_api is on: /stats, /stats/uptime, "
        "/player, /guild, /realm, /news"
    ),
    "act": (
        "GET /utils/shutdown/{api_password}: stops the server; a supervisor starts it "
        "again"
    ),
    "manual": [
        "/reload mob|object|specs|spells|teleports|npctemplates|doors",
        "messages (/broadcast)",
        "kick, mute, ban",
        "account and GM actions",
    ],
    "note": (
        "No console stdin, no command hook, no reload endpoint: the rest is an in-game "
        "GM command (M-3, measured 2026-10-18)."
    ),
}

#: The outcome codes the actions redirect with, and what the page says.
OUTCOMES = {
    "restart": {
        "requested": (
            "requested",
            "The API accepted the stop. The supervisor must start the server again.",
            "ok",
        ),
        "error": (
            "failed",
            "The API did not accept the stop; the audit line has the reason.",
            "warn",
        ),
        "disabled": ("disabled", "Restart is disabled on this Console.", "warn"),
        "api": ("no API", "The API URL or password is missing.", "warn"),
        "token": (
            "expired form",
            "The form token is no longer valid; reload the page and try again.",
            "warn",
        ),
        "confirm": ("not confirmed", "The confirmation was not ticked.", "warn"),
    },
    "pull": {
        "pulled": (
            "pulled",
            "The release is handed over; the Installer applies it (`camelot env apply`).",
            "ok",
        ),
        "error": ("failed", "The pull refused; the audit line has the reason.", "warn"),
        "disabled": ("disabled", "Pull is disabled on this Console.", "warn"),
        "deploy": (
            "no checkout",
            "No deploy checkout is configured (--deploy).",
            "warn",
        ),
        "token": (
            "expired form",
            "The form token is no longer valid; reload the page and try again.",
            "warn",
        ),
        "confirm": ("not confirmed", "The confirmation was not ticked.", "warn"),
    },
    "reverse": {
        "asked": (
            "asked",
            "The ask is written; the Installer's next run puts the rows back (camelot env apply).",
            "ok",
        ),
        "nothing": (
            "nothing applied",
            "No release is applied to this environment; there is nothing to put back.",
            "warn",
        ),
        "disabled": ("disabled", "Reverse is disabled on this Console.", "warn"),
        "token": (
            "expired form",
            "The form token is no longer valid; reload the page and try again.",
            "warn",
        ),
        "confirm": ("not confirmed", "The confirmation was not ticked.", "warn"),
    },
}


class ConsoleError(ValueError):
    """Anything the Console refuses, with the reason in the message."""


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConsoleError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise ConsoleError(f"{path}: expected an object")
    return document


def default_audit_path(env_root) -> Path:
    """The Console's own record, beside the Installer's state but never inside it."""
    return Path(env_root).expanduser() / "server" / "camelot" / "console" / "audit.jsonl"


def ensure_audit(path) -> Path:
    """An action Console without a writable audit line is not worth having
    (`console.md` §D6), so this is checked before the first request."""
    where = Path(path).expanduser()
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        with where.open("a", encoding="utf-8"):
            pass
    except OSError as error:
        raise ConsoleError(
            f"{where}: the audit log is not writable ({error}) — mount a writable "
            "directory and pass --audit"
        ) from error
    return where


def append_audit(path, entry: dict) -> dict:
    """One line per action, append-only, the same discipline as the deploy log."""
    written = {**entry, "at": str(entry.get("at") or _now())}
    try:
        with Path(path).expanduser().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(written, ensure_ascii=False) + "\n")
    except OSError as error:
        raise ConsoleError(f"{path}: {error}") from error
    return written


def _audit_doc(path, problems: list[str], lines: int = AUDIT_LINES) -> dict | None:
    if not path:
        return None
    where = Path(path).expanduser()
    found = {"path": str(where), "present": where.is_file(), "entries": []}
    if not found["present"]:
        return found
    try:
        raw_lines = where.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        problems.append(str(error))
        return found
    entries: list[dict] = []
    for number, raw in enumerate(raw_lines, start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as error:
            problems.append(f"{where} line {number}: {error}")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{where} line {number}: expected an object")
            continue
        entries.append(entry)
    found["entries"] = list(reversed(entries[-int(lines) :])) if lines else []
    return found


def _evidence(env_root: Path, number: int, problems: list[str]) -> dict:
    """What `applied/NNNN/applied.json` says the release did, row by row."""
    try:
        applied = installer.read_applied(env_root, number)
    except installer.InstallerError as error:
        problems.append(str(error))
        applied = {}
    return {
        "applied": bool(applied),
        "at": applied.get("at"),
        "written": _int(applied.get("written")),
        "removed": _int(applied.get("removed")),
        "skipped": _int(applied.get("skipped")),
        "refused": _int(applied.get("refused")),
        "rows": applied.get("rows") or [],
    }


def _releases(state: dict, env_root: Path, problems: list[str]) -> list[dict]:
    """The recorded releases, oldest first — the Installer's answer, never inferred
    from which folders happen to be there."""
    found: list[dict] = []
    for key in sorted((state.get("releases") or {}), key=_int):
        entry = (state.get("releases") or {}).get(key) or {}
        number = _int(key)
        found.append(
            {
                "number": number,
                "at": entry.get("at"),
                "needs": [str(name) for name in entry.get("needs") or ()],
                "tables": entry.get("tables") or {},
                "originals": sum(
                    len(rows or {}) for rows in (entry.get("originals") or {}).values()
                ),
                **_evidence(env_root, number, problems),
            }
        )
    return found


def _feed_doc(feed_dir, latest: int | None, problems: list[str]) -> dict | None:
    """What the players' feed announces, and whether it names the same release as
    the server's state (`console.md` §1, R §D6's convergence)."""
    if not feed_dir:
        return None
    where = Path(feed_dir).expanduser()
    found = {
        "path": str(where),
        "present": where.is_dir(),
        "version": None,
        "release": None,
        "built": None,
        "base_url": "",
        "launcher": {},
        "matches": None,
    }
    if not found["present"]:
        return found
    try:
        peeked = feed_mod.peek(where)
        record = _read_json(where / feed_mod.RECORD_FILE)
    except (feed_mod.FeedError, ConsoleError) as error:
        found["problem"] = str(error)
        problems.append(str(error))
        return found
    release = _int(record.get("release")) or None
    found.update(
        {
            "version": peeked.get("version"),
            "release": release,
            "built": record.get("built"),
            "base_url": record.get("base_url")
            or (peeked.get("launcher") or {}).get("feed")
            or "",
            "launcher": peeked.get("launcher") or {},
            "matches": (release == latest) if release and latest else None,
        }
    )
    return found


def _deploy_log(project, env, problems: list[str]) -> tuple[list[dict], str | None]:
    """The project's append-only deploy log, read the way `project.deploy_log`
    reads it: a malformed line is named, never skipped."""
    if not project:
        return [], None
    path = Path(project).expanduser() / "deploy" / "log.jsonl"
    if not path.is_file():
        return [], str(path)
    entries: list[dict] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError as error:
            problems.append(f"{path} line {number}: {error}")
            continue
        if not isinstance(entry, dict):
            problems.append(f"{path} line {number}: expected an object")
            continue
        if env and str(entry.get("env") or "") != str(env):
            continue
        entries.append(entry)
    return entries, str(path)


def _reload_doc(
    entries: list[dict],
    log_path: str | None,
    env,
    latest: dict | None,
) -> dict:
    """The reload ritual, as a recorded fact (`server-install.md` §D2).

    `unknown` is a real answer: the desktop records the reload in the project's
    log, and a server that has no copy of that log cannot know whether it
    happened.
    """
    needs = list((latest or {}).get("needs") or ())
    found = {
        "state": "unknown",
        "needs": needs,
        "applied_at": (latest or {}).get("at"),
        "reloaded_at": None,
        "log": log_path,
        "why": "",
    }
    if not needs:
        found.update({"state": "not-needed", "why": "the newest release names no reload"})
        return found
    if not env:
        found["why"] = "no environment name: pass --env to read this project's log"
        return found
    if not log_path:
        found["why"] = "no deploy log on this host"
        return found
    if not entries:
        found["why"] = "the deploy log has no line for this environment"
        return found
    release_number = _int((latest or {}).get("number"))
    relevant = [
        entry
        for entry in entries
        if _int(entry.get("release")) == release_number
        and entry.get("kind") in ("apply", "reload")
    ]
    applies = [entry for entry in relevant if entry.get("kind") == "apply"]
    reloads = [entry for entry in relevant if entry.get("kind") == "reload"]
    if not applies:
        found["why"] = "the log records no apply for this release"
        return found
    applied_at = str(applies[-1].get("at") or "")
    later = [entry for entry in reloads if str(entry.get("at") or "") >= applied_at]
    if later:
        found.update(
            {
                "state": "done",
                "applied_at": applies[-1].get("at"),
                "reloaded_at": later[-1].get("at"),
            }
        )
        return found
    found.update(
        {
            "state": "pending",
            "applied_at": applies[-1].get("at"),
            "reloaded_at": reloads[-1].get("at") if reloads else None,
        }
    )
    return found


def _tcp(address: str | None) -> dict | None:
    """Whether the game's port accepts a connection. A connection is not a login:
    it says the listener is there, which is what the Console can know."""
    if not address:
        return None
    host, _, port = str(address).rpartition(":")
    checked = {
        "address": str(address),
        "open": False,
        "checked_at": _now(),
        "error": None,
    }
    if not host or not port.isdigit():
        checked["error"] = "expected host:port"
        return checked
    if host in ("0.0.0.0", "*"):
        host = "127.0.0.1"
    try:
        with socket.create_connection((host, int(port)), timeout=0.75):
            checked["open"] = True
    except OSError as error:
        checked["error"] = str(error)
    return checked


def _api_get(base: str, path: str) -> tuple[object, str | None]:
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=2.0) as answer:
            body = answer.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        return None, f"{url}: {error}"
    try:
        return json.loads(body), None
    except json.JSONDecodeError:
        return body.strip(), None


def _api_doc(api_url: str | None, api_password: str | None) -> dict | None:
    """The read half of OpenDAoC's API, when it is on. `/utils/query_clients`
    carries the API password; the two stats routes do not."""
    if not api_url:
        return None
    found: dict = {"url": api_url, "stats": None, "uptime": None, "clients": None, "problems": []}
    for key, path in (("stats", "/stats"), ("uptime", "/stats/uptime")):
        value, error = _api_get(api_url, path)
        if error:
            found["problems"].append(error)
        else:
            found[key] = value
    if api_password:
        quoted = urllib.parse.quote(api_password, safe="")
        value, error = _api_get(api_url, f"/utils/query_clients/{quoted}")
        if error:
            found["problems"].append(error)
        else:
            found["clients"] = value
    return found


def _shutdown(api_url: str, api_password: str) -> tuple[bool, str]:
    """The one order OpenDAoC exposes (M-3): stop the server. A supervisor is
    what turns that into a restart; the Console cannot start a process."""
    url = (
        str(api_url).rstrip("/")
        + "/utils/shutdown/"
        + urllib.parse.quote(str(api_password), safe="")
    )
    try:
        with urllib.request.urlopen(url, timeout=5.0) as answer:
            body = answer.read().decode("utf-8", "replace").strip()
            return answer.status == 200, f"HTTP {answer.status}: {body[:200]}"
    except urllib.error.HTTPError as error:
        try:
            body = error.read().decode("utf-8", "replace").strip()
        except OSError:
            body = ""
        return False, f"HTTP {error.code}: {body[:200]}"
    except (urllib.error.URLError, OSError) as error:
        return False, str(error)


def _git_head(where: Path) -> tuple[str | None, str | None]:
    """The checkout's short head, or the reason it has none."""
    try:
        result = subprocess.run(
            ["git", "-C", str(where), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        return None, f"git is not installed ({error})"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, detail[-1] if detail else "not a git checkout"
    return result.stdout.strip(), None


def _remote_doc(deploy, env_root, *, branch=publish_mod.BRANCH, problems=None) -> dict | None:
    """What the deploy checkout holds, and whether the environment has taken it
    (`remote.md` §D4). Read-only; the pull itself is the action."""
    if not deploy:
        return None
    where = Path(deploy).expanduser()
    found = {
        "dir": str(where),
        "branch": branch,
        "present": where.is_dir(),
        "head": None,
        "publish": None,
        "wanted": None,
        "applied": [],
        "pending": [],
        "problem": None,
    }
    if not found["present"]:
        return found
    head, problem = _git_head(where)
    found["head"], found["problem"] = head, problem
    marker = {}
    marker_path = where / publish_mod.MARKER
    if marker_path.is_file():
        try:
            marker = _read_json(marker_path)
        except ConsoleError as error:
            if problems is not None:
                problems.append(str(error))
    found["publish"] = {
        "release": marker.get("release"),
        "feed": marker.get("feed"),
        "at": marker.get("at"),
        "author": marker.get("author"),
    }
    if env_root is None:
        return found
    try:
        wanted = remote_mod.read_wanted(env_root)
    except remote_mod.PullError as error:
        if problems is not None:
            problems.append(str(error))
        wanted = {}
    numbers = [int(number) for number in wanted.get("releases") or ()]
    found["wanted"] = {
        "releases": numbers,
        "at": wanted.get("at"),
        "by": wanted.get("by"),
    }
    if numbers:
        try:
            state = installer.read_state(env_root)
        except installer.InstallerError as error:
            if problems is not None:
                problems.append(str(error))
            state = {}
        found["applied"] = [n for n in numbers if installer.released(state, n) is not None]
        found["pending"] = [n for n in numbers if n not in found["applied"]]
    return found


def live(
    env_root,
    *,
    env=None,
    project=None,
    feed=None,
    game_server=None,
    api_url=None,
    api_password=None,
    allow_restart: bool = False,
    audit=None,
    deploy=None,
    log_lines: int = LOG_LINES,
) -> dict:
    """What is live on one environment, read from the Installer's state.

    Nothing here writes. `env_root` is the environment folder (`client/` and
    `server/`); `project` is optional and only gives the deploy log; `feed`
    is optional and gives the players' side. The action section says whether a
    restart is possible and why not; the audit is read, never invented.
    """
    root = Path(env_root).expanduser()
    problems: list[str] = []
    try:
        state = installer.read_state(root)
    except installer.InstallerError as error:
        raise ConsoleError(str(error)) from error

    releases = _releases(state, root, problems)
    latest = releases[-1] if releases else None
    handed = installer.handed_over(root)
    recorded = {release["number"] for release in releases}
    pending = [number for number in handed if number not in recorded]

    environment = {}
    try:
        environment = _read_json(installer.folder(root) / "env.json")
    except ConsoleError as error:
        problems.append(str(error))
    feed_doc = _feed_doc(feed, latest["number"] if latest else None, problems)
    if not game_server and feed_doc:
        game_server = (feed_doc.get("launcher") or {}).get("server") or None

    entries, log_path = _deploy_log(project, env, problems)
    shown = list(reversed(entries[-int(log_lines) :])) if log_lines else []
    reload_doc = _reload_doc(entries, log_path, env, latest)

    restart_enabled = bool(allow_restart and api_url and api_password)
    restart_why = ""
    if not allow_restart:
        restart_why = "start the Console with --allow-restart"
    elif not api_url:
        restart_why = "no API URL (--api-url or CAMELOT_CONSOLE_API_URL)"
    elif not api_password:
        restart_why = "no API password (--api-password or CAMELOT_CONSOLE_API_PASSWORD)"

    deploy_dir = Path(str(deploy)).expanduser() if deploy else None
    pull_enabled = bool(deploy_dir and env and deploy_dir.is_dir())
    pull_why = ""
    if not deploy_dir:
        pull_why = "start the Console with --deploy (the checkout of the deploy branch)"
    elif not deploy_dir.is_dir():
        pull_why = f"{deploy_dir} is not a directory"
    elif not env:
        pull_why = "no environment name (--env)"

    ask = {}
    try:
        ask = installer.read_reverse_ask(root)
    except installer.InstallerError as error:
        problems.append(str(error))
    asked = [int(number) for number in (ask.get("releases") or ())]
    reverse_enabled = bool(latest and audit)
    reverse_why = ""
    if not latest:
        reverse_why = "nothing is applied to this environment"
    elif not audit:
        reverse_why = "no audit path (--audit or CAMELOT_CONSOLE_AUDIT)"

    return {
        "format": FORMAT,
        "at": _now(),
        "env": {
            "name": env,
            "root": str(root),
            "project": state.get("project") or "",
            "record": environment,
            "database": environment.get("database"),
            "baseline": state.get("baseline") or {},
            "updated": state.get("updated"),
        },
        "release": latest,
        "releases": releases,
        "handed_over": handed,
        "pending": pending,
        "health": {
            "game_server": _tcp(game_server),
            "api": _api_doc(api_url, api_password),
        },
        "feed": feed_doc,
        "log": {
            "path": log_path,
            "present": bool(log_path) and Path(log_path).is_file(),
            "entries": shown,
            "reload": reload_doc,
        },
        "actions": {
            "restart": {
                "enabled": restart_enabled,
                "target": api_url or None,
                "why": restart_why,
            },
            "pull": {
                "enabled": pull_enabled,
                "dir": str(deploy_dir) if deploy_dir else None,
                "why": pull_why,
            },
            "reverse": {
                "enabled": reverse_enabled,
                "release": latest["number"] if latest else None,
                "why": reverse_why,
            },
        },
        "reverse": {
            "path": str(installer.reverse_ask_path(root)),
            "releases": asked,
            "at": ask.get("at"),
            "by": ask.get("by"),
            # asked, and still applied: the Installer's step has not run yet
            "pending": [number for number in asked if number in recorded],
        },
        "remote": _remote_doc(deploy_dir, root, problems=problems),
        "audit": _audit_doc(audit, problems),
        "channel": CHANNEL,
        "problems": problems,
        "ok": not problems,
    }


# --- the page ------------------------------------------------------------------


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _chip(text, kind: str = "") -> str:
    return f'<span class="chip {kind}">{_e(text)}</span>'


def _rows(values) -> str:
    return ", ".join(_e(value) for value in values) if values else "—"


def _page_release(document: dict) -> str:
    latest = document.get("release")
    if not latest:
        return (
            '<p class="empty">No release is recorded in the Installer\'s state. '
            "The environment exists, but nothing has been applied to it yet.</p>"
        )
    return f"""
      <p class="big">{latest["number"]:04d}</p>
      <p class="muted">applied {_e(latest.get("at"))} · {latest.get("written", 0)} written,
      {latest.get("removed", 0)} removed, {latest.get("skipped", 0)} skipped</p>
      <p>Needs: {_rows(latest.get("needs"))}</p>
    """


def _page_health(document: dict) -> str:
    health = document.get("health") or {}
    game = health.get("game_server")
    api = health.get("api")
    found = []
    if game:
        kind = "ok" if game.get("open") else "bad"
        found.append(
            f'<p>{_chip("accepting", kind) if game.get("open") else _chip("closed", kind)} '
            f'<code>{_e(game.get("address"))}</code></p>'
        )
        if game.get("error"):
            found.append(f'<p class="muted">{_e(game["error"])}</p>')
    else:
        found.append('<p class="empty">No game address was given.</p>')
    if api:
        stats = api.get("stats") or {}
        uptime = api.get("uptime") or {}
        if isinstance(stats, dict):
            found.append(
                f'<p>{_chip("API", "ok")} <code>{_e(api.get("url"))}</code> · '
                f'{_e(stats.get("Total", "—"))} client(s) connected</p>'
            )
        else:
            found.append(f'<p>{_chip("API", "bad")} <code>{_e(api.get("url"))}</code></p>')
        if isinstance(uptime, dict) and uptime.get("Uptime"):
            found.append(f'<p class="muted">up {_e(uptime["Uptime"])}</p>')
        clients = api.get("clients")
        if isinstance(clients, dict) and clients:
            names = ", ".join(sorted(clients))
            found.append(f'<p class="muted">online: {_e(names)}</p>')
        for problem in api.get("problems") or ():
            found.append(f'<p class="warn">{_e(problem)}</p>')
    else:
        found.append(
            '<p class="empty">No API URL was given. The game port is the health check; '
            "player counts need <code>atlas_api</code> on <code>:5000</code>.</p>"
        )
    return "\n".join(found)


def _page_feed(document: dict) -> str:
    feed = document.get("feed")
    if feed is None:
        return (
            '<p class="empty">No feed folder was given. The Console shows the server '
            "side only.</p>"
        )
    if not feed.get("present"):
        return f'<p class="empty">Not on this host: <code>{_e(feed.get("path"))}</code></p>'
    latest = (document.get("release") or {}).get("number")
    found = [
        f'<p class="big">v{_e(feed.get("version"))}</p>',
        (
            f'<p class="muted">release {_e(feed.get("release") or "—")} · built '
            f'{_e(feed.get("built") or "—")}</p>'
        ),
        '<p class="muted">recorded base URL</p>',
        f'<p><code>{_e(feed.get("base_url") or "no base URL")}</code></p>',
    ]
    if feed.get("matches") is True:
        found.append(f'<p>{_chip("same release", "ok")} the feed and the server agree</p>')
    elif feed.get("matches") is False:
        found.append(
            f'<p>{_chip("differs", "warn")} the server is on '
            f'{latest if latest is not None else "—"} and the feed on '
            f'{_e(feed.get("release"))}</p>'
        )
    if feed.get("problem"):
        found.append(f'<p class="warn">{_e(feed["problem"])}</p>')
    return "\n".join(found)


def _page_reload(document: dict) -> str:
    reload = (document.get("log") or {}).get("reload") or {}
    state = reload.get("state")
    kinds = {"done": "ok", "pending": "warn", "unknown": "warn", "not-needed": "ok"}
    found = [
        f'<p>{_chip(state or "unknown", kinds.get(state, ""))}</p>',
        f'<p>Needs: {_rows(reload.get("needs"))}</p>',
    ]
    if reload.get("reloaded_at"):
        found.append(f'<p class="muted">recorded {_e(reload["reloaded_at"])}</p>')
    if reload.get("why"):
        found.append(f'<p class="muted">{_e(reload["why"])}</p>')
    return "\n".join(found)


def _page_releases(document: dict) -> str:
    releases = list(reversed(document.get("releases") or []))
    if not releases:
        return '<p class="empty">No release has been applied.</p>'
    body = []
    for release in releases:
        body.append(
            "<tr>"
            f'<td><code>{release["number"]:04d}</code></td>'
            f'<td>{_e(release.get("at") or "—")}</td>'
            f'<td>{release.get("written", 0)} / {release.get("removed", 0)} / '
            f'{release.get("skipped", 0)}</td>'
            f'<td>{_rows(release.get("needs"))}</td>'
            f'<td>{"yes" if release.get("applied") else "no"}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>release</th><th>applied</th>"
        "<th>written / removed / skipped</th><th>needs</th><th>evidence</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _log_detail(entry: dict) -> str:
    kind = entry.get("kind")
    if kind == "deploy":
        items = entry.get("items") or {}
        return f"{entry.get('order') or ''} · {json.dumps(items, sort_keys=True)}"
    if kind == "apply":
        return f"rows {json.dumps(entry.get('rows') or {}, sort_keys=True)}"
    if kind == "reload":
        return "needs " + ", ".join(str(name) for name in entry.get("needs") or ())
    if kind == "promote":
        return f"from {entry.get('from') or '—'}"
    return ""


def _page_rows(document: dict) -> str:
    latest = document.get("release")
    if not latest:
        return '<p class="empty">No release is recorded.</p>'
    rows = latest.get("rows") or []
    if not rows:
        return (
            '<p class="empty">The newest release records no row patch: its changes are '
            "the client's, or the Installer found nothing to write.</p>"
        )
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f'<td>{_e(row.get("table") or "—")}</td>'
            f'<td><code>{_e(row.get("key") or "—")}</code></td>'
            f'<td>{_e(row.get("action") or "—")}</td>'
            f'<td>{_e(row.get("why") or "—")}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>table</th><th>key</th><th>action</th>"
        f"<th>why</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _page_log(document: dict) -> str:
    log = document.get("log") or {}
    entries = log.get("entries") or []
    if not entries:
        return (
            '<p class="empty">No deploy line is published on this host. Pass '
            "<code>--project</code> (and <code>--env</code>) to read the project's "
            "<code>deploy/log.jsonl</code>.</p>"
        )
    body = []
    for entry in entries:
        body.append(
            "<tr>"
            f'<td>{_e(entry.get("at") or "—")}</td>'
            f'<td>{_e(entry.get("kind") or "—")}</td>'
            f'<td>{_e(entry.get("release") if entry.get("release") is not None else "—")}</td>'
            f'<td>{_e(entry.get("outcome") or "—")}</td>'
            f'<td>{_e(_log_detail(entry))}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>at</th><th>kind</th><th>release</th>"
        "<th>outcome</th><th>detail</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _page_channel(document: dict) -> str:
    channel = document.get("channel") or {}
    manual = "".join(f"<li><code>{_e(item)}</code></li>" for item in channel.get("manual") or ())
    return f"""
      <p><strong>Read.</strong> {_e(channel.get("read"))}</p>
      <p><strong>Act.</strong> {_e(channel.get("act"))}</p>
      <p><strong>Still manual.</strong></p>
      <ul>{manual}</ul>
      <p class="muted">{_e(channel.get("note"))}</p>
    """


def _action_form(kind: str, action: dict, *, token: str | None = None) -> str:
    if not action.get("enabled"):
        return (
            f'<p class="empty">{_e(kind.capitalize())} is disabled: '
            f'{_e(action.get("why") or "not configured")}.</p>'
        )
    if kind == "restart":
        confirm = (
            "I understand this stops the server, and that the supervisor must start it again."
        )
        button = "Restart (stop; supervisor restarts)"
        note = (
            "Sends <code>GET /utils/shutdown</code> to "
            f'<code>{_e(action.get("target"))}</code>. Without a supervisor this is a stop, '
            "not a restart."
        )
    elif kind == "reverse":
        confirm = "I understand this asks the server to put back what one release replaced."
        button = "Ask to reverse the newest release"
        note = (
            "Writes <code>reverse.json</code> for release "
            f'<code>{_e(action.get("release"))}</code>: the Installer\'s next run puts back the '
            "rows that release changed, and the release leaves this server. It is a repair, "
            "not a rollback — the players' clients keep what they downloaded."
        )
    else:
        confirm = "I understand this hands the published release over to this server."
        button = "Pull &amp; hand over"
        note = (
            "Fetches the deploy branch, verifies the release's hashes and copies the server "
            f'half from <code>{_e(action.get("dir"))}</code>. The Installer applies it '
            "afterwards."
        )
    return f"""
      <form method="post" action="/action/{kind}">
        <input type="hidden" name="token" value="{_e(token or '')}">
        <label class="check"><input type="checkbox" name="confirm" value="yes" required>
          {confirm}</label>
        <button type="submit">{button}</button>
      </form>
      <p class="muted">{note}</p>
    """


def _page_actions(
    document: dict,
    *,
    token: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
) -> str:
    actions = document.get("actions") or {}
    found = []
    chosen = OUTCOMES.get(str(action) or "", {})
    if outcome in chosen:
        label, text, kind = chosen[outcome]
        found.append(f'<p>{_chip(label, kind)} {_e(text)}</p>')
    found.append(_action_form("restart", actions.get("restart") or {}, token=token))
    found.append(_action_form("pull", actions.get("pull") or {}, token=token))
    found.append(_action_form("reverse", actions.get("reverse") or {}, token=token))
    asked = (document.get("reverse") or {}).get("pending") or []
    if asked:
        numbers = ", ".join(f"{int(number):04d}" for number in asked)
        found.append(
            f'<p>{_chip("asked", "warn")} release {numbers} — the Installer\'s step has not '
            "run yet.</p>"
        )
    return "\n".join(found)


def _page_audit(document: dict) -> str:
    audit = document.get("audit")
    if audit is None:
        return '<p class="empty">No audit path is configured.</p>'
    where = audit.get("path")
    if not audit.get("present"):
        return f'<p class="empty">No action has been recorded yet (<code>{_e(where)}</code>).</p>'
    entries = audit.get("entries") or []
    if not entries:
        return f'<p class="empty">The audit log is empty (<code>{_e(where)}</code>).</p>'
    body = []
    for entry in entries:
        body.append(
            "<tr>"
            f'<td>{_e(entry.get("at") or "—")}</td>'
            f'<td>{_e(entry.get("user") or "—")}</td>'
            f'<td>{_e(entry.get("action") or "—")}</td>'
            f'<td>{_e(entry.get("result") or "—")}</td>'
            f'<td><code>{_e(entry.get("target") or "—")}</code></td>'
            f'<td>{_e(entry.get("detail") or "—")}</td>'
            "</tr>"
        )
    return (
        "<table><thead><tr><th>at</th><th>user</th><th>action</th><th>result</th>"
        f"<th>target</th><th>detail</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _page_remote(document: dict) -> str:
    remote = document.get("remote")
    if remote is None:
        return (
            '<p class="empty">No deploy checkout is configured. Start the Console with '
            "<code>--deploy</code> to let it pull the deploy branch.</p>"
        )
    if not remote.get("present"):
        return f'<p class="empty">Not on this host: <code>{_e(remote.get("dir"))}</code></p>'
    found = []
    if remote.get("problem"):
        found.append(f'<p class="warn">{_e(remote["problem"])}</p>')
    found.append(
        f'<p><code>{_e(remote.get("dir"))}</code> · branch '
        f'<code>{_e(remote.get("branch"))}</code> · head '
        f'<code>{_e(remote.get("head") or "—")}</code></p>'
    )
    publish = remote.get("publish") or {}
    if publish.get("release"):
        feed = publish.get("feed") or {}
        found.append(
            f'<p>published release <strong>{int(publish["release"]):04d}</strong> · '
            f'feed v{_e(feed.get("version"))} · {_e(publish.get("at"))}</p>'
        )
    else:
        found.append('<p class="empty">No publish.json in the checkout.</p>')
    wanted = (remote.get("wanted") or {}).get("releases") or []
    if wanted:
        applied = remote.get("applied") or []
        pending = remote.get("pending") or []
        found.append(
            f'<p>wanted: {_rows(f"{int(n):04d}" for n in wanted)} · '
            f'applied: {_rows(f"{int(n):04d}" for n in applied)} · '
            f'pending: {_rows(f"{int(n):04d}" for n in pending)}</p>'
        )
        if pending:
            found.append(
                '<p class="muted">The Installer applies it on the server: '
                "<code>camelot env apply &lt;env&gt;</code>.</p>"
            )
    else:
        found.append('<p class="empty">Nothing has been handed over yet.</p>')
    return "\n".join(found)


def _page_problems(document: dict) -> str:
    problems = document.get("problems") or []
    pending = document.get("pending") or []
    found = []
    if problems:
        found.append("<ul>" + "".join(f"<li>{_e(p)}</li>" for p in problems) + "</ul>")
    else:
        found.append('<p class="empty">None.</p>')
    if pending:
        found.append(
            '<p class="warn">Handed over but not applied: '
            + _rows(f"{number:04d}" for number in pending)
            + "</p>"
        )
    return "\n".join(found)


def page(
    document: dict,
    *,
    action_token: str | None = None,
    outcome: str | None = None,
    outcome_action: str | None = None,
) -> str:
    """The Console's page, rendered from the same document `/api/live` returns."""
    env = document.get("env") or {}
    latest = document.get("release") or {}
    status = "not installed"
    if latest:
        status = f'release {latest["number"]:04d}'
    if document.get("problems"):
        status = "attention"
    project = env.get("project") or "Server"
    name = env.get("name") or "—"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>Camelot Console — {_e(project)}</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f6f5f1;
    --panel: #fffdf8;
    --ink: #1d1b17;
    --muted: #6d675c;
    --line: #ddd7cb;
    --accent: #7a4b1f;
    --ok: #24633a;
    --warn: #8a5a00;
    --bad: #922b21;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #17150f;
      --panel: #201d16;
      --ink: #eee8dc;
      --muted: #a89f8d;
      --line: #3b352a;
      --accent: #d7a86e;
      --ok: #76c893;
      --warn: #e0b34a;
      --bad: #ef7a6d;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  header, main, footer {{ width: min(1100px, calc(100% - 2rem)); margin: 0 auto; }}
  header {{ padding: 2.5rem 0 1rem; border-bottom: 1px solid var(--line); }}
  .eyebrow {{ margin: 0; color: var(--accent); font-size: .78rem;
    letter-spacing: .12em; text-transform: uppercase; }}
  h1 {{ margin: .35rem 0 .25rem; font-size: clamp(1.6rem, 3vw, 2.3rem); }}
  h2 {{ margin: 0 0 .6rem; font-size: 1rem; letter-spacing: .04em; }}
  .muted {{ color: var(--muted); }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .warn {{ color: var(--warn); }}
  .chip {{ display: inline-block; padding: .15rem .5rem; border-radius: 999px;
    border: 1px solid var(--line); font-size: .78rem; }}
  .chip.ok {{ color: var(--ok); border-color: currentColor; }}
  .chip.warn {{ color: var(--warn); border-color: currentColor; }}
  .chip.bad {{ color: var(--bad); border-color: currentColor; }}
  .big {{ margin: .2rem 0; font-size: 2rem; font-weight: 650; font-variant-numeric: tabular-nums; }}
  .grid {{ display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }}
  article {{ background: var(--panel); border: 1px solid var(--line);
    border-radius: 14px; padding: 1rem 1.1rem; }}
  section {{ margin: 2rem 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
  th, td {{ text-align: left; padding: .45rem .5rem; border-bottom: 1px solid var(--line); }}
  th {{ color: var(--muted); font-weight: 600; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .88em; }}
  form {{ display: grid; gap: .7rem; max-width: 42rem; }}
  .check {{ display: flex; gap: .5rem; align-items: flex-start; }}
  button {{ justify-self: start; padding: .5rem .9rem; border-radius: 9px;
    border: 1px solid var(--line); background: var(--accent); color: var(--panel);
    font: inherit; cursor: pointer; }}
  ul {{ margin: .3rem 0; padding-left: 1.2rem; }}
  footer {{ padding: 1rem 0 3rem; color: var(--muted); font-size: .85rem; }}
  a {{ color: var(--accent); }}
</style>
</head>
<body>
<header>
  <p class="eyebrow">Camelot Forge · Console</p>
  <h1>{_e(project)} {_chip(status, "warn" if document.get("problems") else "ok")}</h1>
  <p class="muted">environment {_e(name)} · <code>{_e(env.get("root"))}</code>
    · read {_e(document.get("at"))}</p>
</header>
<main>
  <section class="grid">
    <article><h2>Live release</h2>{_page_release(document)}</article>
    <article><h2>Game server</h2>{_page_health(document)}</article>
    <article><h2>Reload</h2>{_page_reload(document)}</article>
    <article><h2>Feed</h2>{_page_feed(document)}</article>
  </section>
  <section><h2>Releases</h2>{_page_releases(document)}</section>
  <section><h2>Rows in the newest release</h2>{_page_rows(document)}</section>
  <section><h2>Deploy log</h2>{_page_log(document)}</section>
  <section><h2>Remote (deploy branch)</h2>{_page_remote(document)}</section>
  <section><h2>Actions</h2>{_page_actions(document, token=action_token, action=outcome_action, outcome=outcome)}</section>
  <section><h2>Audit</h2>{_page_audit(document)}</section>
  <section><h2>What this console can do</h2>{_page_channel(document)}</section>
  <section><h2>Problems</h2>{_page_problems(document)}</section>
</main>
<footer>Read-only unless Restart is enabled · phase 7a/7b ·
  <a href="/api/live">live JSON</a></footer>
</body>
</html>
"""


# --- the server ----------------------------------------------------------------


class _ConsoleState:
    """What the action routes need; the read routes never touch the API password."""

    def __init__(
        self,
        *,
        allow_restart: bool,
        api_url: str | None,
        api_password: str | None,
        audit,
        env: str | None = None,
        root=None,
        deploy=None,
        remote: str | None = None,
        feed=None,
        token: str | None = None,
    ) -> None:
        self.allow_restart = bool(allow_restart)
        self.api_url = api_url
        self.api_password = api_password
        self.audit = Path(audit).expanduser() if audit else None
        self.env = env
        self.root = Path(str(root)).expanduser() if root else None
        self.deploy = Path(str(deploy)).expanduser() if deploy else None
        self.remote = remote
        self.feed = Path(str(feed)).expanduser() if feed else None
        self.token = token or secrets.token_urlsafe(32)
        self.lock = threading.Lock()

    def pull_enabled(self) -> bool:
        """The pull needs a checkout and an environment to hand over to."""
        return bool(self.deploy and self.env and self.deploy.is_dir())

    def reverse_enabled(self) -> bool:
        """The reversal needs the environment folder (the ask is a file inside it),
        an audit to write to, and something applied to put back."""
        if not self.root or not self.audit:
            return False
        try:
            state = installer.read_state(self.root)
        except installer.InstallerError:
            return False
        return bool(state.get("releases"))


class ConsoleServer(ThreadingHTTPServer):
    """The bound Console; the handler reads `console` for the action state."""

    def __init__(self, address, document, *, user=None, password=None, state: _ConsoleState):
        self.console = state
        super().__init__(address, handler_for(document, user=user, password=password))


def handler_for(document, *, user: str | None = None, password: str | None = None):
    """A handler that renders *document* on every request, behind one operator
    credential when one is configured. `/healthz` stays open for the container's
    probe and says nothing else. The action route writes one audit line before it
    answers, and never runs without the explicit flag."""

    audit_user = (user or "operator") if password else "local"

    class Handler(BaseHTTPRequestHandler):
        server_version = "camelot-console/1"

        def _send(self, status: int, body: bytes, content_type: str, *, head=False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _json(self, value, *, status: int = 200, head: bool = False) -> None:
            body = (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", head=head)

        def _redirect(self, action: str, outcome: str) -> None:
            location = (
                f"/?action={urllib.parse.quote(action, safe='')}"
                f"&outcome={urllib.parse.quote(outcome, safe='')}"
            )
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _authorized(self) -> bool:
            if not password:
                return True
            given = self.headers.get("Authorization") or ""
            expected = "Basic " + base64.b64encode(
                f"{user or 'operator'}:{password}".encode()
            ).decode()
            return hmac.compare_digest(given.encode("utf-8", "replace"), expected.encode())

        def _unauthorized(self, *, head: bool = False) -> None:
            body = b"authentication required\n"
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="camelot console", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _audit(self, *, action: str, result: str, detail: str) -> str | None:
            state = self.server.console
            if state.audit is None:
                return "no audit path"
            target = {
                "restart": state.api_url,
                "pull": str(state.deploy) if state.deploy else "",
                "reverse": str(state.root) if state.root else "",
            }.get(action)
            try:
                append_audit(
                    state.audit,
                    {
                        "format": AUDIT_FORMAT,
                        "user": audit_user,
                        "action": action,
                        "result": result,
                        "target": target or "",
                        "detail": detail,
                        "remote": self.client_address[0] if self.client_address else "",
                    },
                )
            except ConsoleError as error:
                return str(error)
            return None

        def _reverse(self, state, refuse):
            """Write the ask (`console.md` §D10): which release to put back.

            The Console never opens the database (S §D1) — the Installer's step
            reads this file and does the reversing.
            """
            try:
                applied = sorted(
                    int(name) for name in (installer.read_state(state.root).get("releases") or {})
                )
            except installer.InstallerError as error:
                audit_error = self._audit(action="reverse", result="error", detail=str(error))
                if audit_error:
                    return self._json({"ok": False, "error": audit_error}, status=500)
                return self._redirect("reverse", "error")
            if not applied:
                return refuse("nothing", "nothing is applied")
            number = applied[-1]
            try:
                installer.write_reverse_ask(state.root, [number], by=audit_user)
            except (OSError, installer.InstallerError) as error:
                audit_error = self._audit(action="reverse", result="error", detail=str(error))
                if audit_error:
                    return self._json({"ok": False, "error": audit_error}, status=500)
                return self._redirect("reverse", "error")
            error = self._audit(
                action="reverse", result="asked", detail=f"release {number:04d}"
            )
            if error:
                return self._json({"ok": False, "error": error}, status=500)
            return self._redirect("reverse", "asked")

        def _get(self, *, head: bool = False) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/healthz":
                return self._json({"ok": True}, head=head)
            if not self._authorized():
                return self._unauthorized(head=head)
            try:
                document_value = document()
            except ConsoleError as error:
                return self._json({"ok": False, "error": str(error)}, status=500, head=head)
            if path in ("/", "/index.html"):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                outcome = (query.get("outcome") or [None])[0]
                outcome_action = (query.get("action") or [None])[0]
                rendered = page(
                    document_value,
                    action_token=self.server.console.token,
                    outcome=outcome,
                    outcome_action=outcome_action,
                )
                return self._send(
                    200,
                    rendered.encode("utf-8"),
                    "text/html; charset=utf-8",
                    head=head,
                )
            if path in ("/api/live", "/api/live.json"):
                return self._json(document_value, head=head)
            return self._json({"detail": f"no such route: {path}"}, status=404, head=head)

        def _post(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            routes = {
                "restart": "/action/restart",
                "pull": "/action/pull",
                "reverse": "/action/reverse",
            }
            kind = next((name for name, route in routes.items() if route == path), None)
            if kind is None:
                return self._json({"detail": f"no such route: {path}"}, status=404)
            if not self._authorized():
                return self._unauthorized()
            state = self.server.console
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 4096:
                return self._json({"detail": "request body too large"}, status=413)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            form = urllib.parse.parse_qs(raw)
            token = (form.get("token") or [""])[0]
            confirm = (form.get("confirm") or [""])[0]

            def refuse(outcome: str, detail: str):
                error = self._audit(action=kind, result="refused", detail=detail)
                if error:
                    return self._json({"ok": False, "error": error}, status=500)
                return self._redirect(kind, outcome)

            with state.lock:
                enabled = {
                    "restart": state.allow_restart,
                    "pull": state.pull_enabled(),
                    "reverse": state.reverse_enabled(),
                }[kind]
                if not enabled:
                    if state.audit is None:
                        return self._redirect(kind, "disabled")
                    return refuse("disabled", f"{kind} is disabled")
                if not hmac.compare_digest(
                    token.encode("utf-8", "replace"), state.token.encode()
                ):
                    return refuse("token", "form token")
                if confirm != "yes":
                    return refuse("confirm", "confirmation not ticked")
                if kind == "reverse":
                    return self._reverse(state, refuse)
                if kind == "restart":
                    if not state.api_url or not state.api_password:
                        return refuse("api", "API URL or password is missing")
                    ok, detail = _shutdown(state.api_url, state.api_password)
                    error = self._audit(
                        action="restart",
                        result="requested" if ok else "error",
                        detail=detail,
                    )
                    if error:
                        return self._json({"ok": False, "error": error}, status=500)
                    return self._redirect("restart", "requested" if ok else "error")
                if not state.deploy:
                    return refuse("deploy", "no deploy checkout")
                try:
                    report = remote_mod.pull(
                        state.deploy,
                        state.env,
                        remote=state.remote,
                        feed=state.feed,
                        by=audit_user,
                    )
                except (remote_mod.PullError, project_mod.ProjectError) as error:
                    audit_error = self._audit(action="pull", result="error", detail=str(error))
                    if audit_error:
                        return self._json({"ok": False, "error": audit_error}, status=500)
                    return self._redirect("pull", "error")
                feed_files = report["feed"]["files"] if report["feed"] else 0
                detail = (
                    f"release {report['release']:04d} · wrote {len(report['wrote'])} · "
                    f"feed {feed_files}"
                )
                error = self._audit(action="pull", result="pulled", detail=detail)
                if error:
                    return self._json({"ok": False, "error": error}, status=500)
                return self._redirect("pull", "pulled")

        def do_GET(self) -> None:
            self._get()

        def do_HEAD(self) -> None:
            self._get(head=True)

        def do_POST(self) -> None:
            self._post()

        def log_message(self, format, *args) -> None:
            print(f"{self.address_string()} {format % args}", file=sys.stderr)

    return Handler


def server_for(
    document,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user: str | None = None,
    password: str | None = None,
    allow_restart: bool = False,
    api_url: str | None = None,
    api_password: str | None = None,
    audit=None,
    env: str | None = None,
    root=None,
    deploy=None,
    remote: str | None = None,
    feed=None,
    token: str | None = None,
) -> ConsoleServer:
    """A bound server, so a test can drive the routes without a container."""
    if str(host) not in LOOPBACK and not password:
        raise ConsoleError(
            f"refusing to listen on {host} without a password: "
            "set CAMELOT_CONSOLE_PASSWORD or pass --password"
        )
    if allow_restart:
        if not api_url:
            raise ConsoleError(
                "restart needs the server's API: pass --api-url or CAMELOT_CONSOLE_API_URL"
            )
        if not api_password:
            raise ConsoleError(
                "restart needs the API password: pass --api-password or "
                "CAMELOT_CONSOLE_API_PASSWORD"
            )
    if (allow_restart or deploy) and not audit:
        raise ConsoleError(
            "actions need an audit path: pass --audit or CAMELOT_CONSOLE_AUDIT"
        )
    if allow_restart or deploy:
        ensure_audit(audit)
    state = _ConsoleState(
        allow_restart=allow_restart,
        api_url=api_url,
        api_password=api_password,
        audit=audit,
        env=env,
        root=root,
        deploy=deploy,
        remote=remote,
        feed=feed,
        token=token,
    )
    return ConsoleServer(
        (host, int(port)),
        document,
        user=user,
        password=password,
        state=state,
    )


def serve(
    document,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user: str | None = None,
    password: str | None = None,
    allow_restart: bool = False,
    api_url: str | None = None,
    api_password: str | None = None,
    audit=None,
    env: str | None = None,
    root=None,
    deploy=None,
    remote: str | None = None,
    feed=None,
    token: str | None = None,
) -> int:
    bound = server_for(
        document,
        host=host,
        port=port,
        user=user,
        password=password,
        allow_restart=allow_restart,
        api_url=api_url,
        api_password=api_password,
        audit=audit,
        env=env,
        root=root,
        deploy=deploy,
        remote=remote,
        feed=feed,
        token=token,
    )
    shown = f"http://{host}:{bound.server_port}/"
    print(f"camelot console · {shown}", flush=True)
    try:
        bound.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bound.server_close()
    return 0


def run(
    root,
    *,
    env=None,
    project=None,
    feed=None,
    game_server=None,
    api_url=None,
    api_password=None,
    allow_restart: bool = False,
    audit=None,
    deploy=None,
    deploy_remote=None,
    feed_out=None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    user: str | None = None,
    password: str | None = None,
    token: str | None = None,
) -> int:
    """Serve one environment's Console. *root* is the folder holding `client/`
    and `server/`; everything else is optional. Actions are off until
    *allow_restart* is set, and then need an API and a writable audit path.
    *deploy* is the checkout of the deploy branch: with it (and *env*), the
    Console can pull (`remote.md` §D4); *feed_out* is the directory the feed
    service serves, mirrored by the pull."""
    if not root:
        raise ConsoleError("--root is required: the environment folder (client/ + server/)")
    audit_path = Path(audit).expanduser() if audit else default_audit_path(root)

    def document() -> dict:
        return live(
            root,
            env=env,
            project=project,
            feed=feed,
            game_server=game_server,
            api_url=api_url,
            api_password=api_password,
            allow_restart=allow_restart,
            audit=audit_path,
            deploy=deploy,
        )

    return serve(
        document,
        host=host,
        port=port,
        user=user,
        password=password,
        allow_restart=allow_restart,
        api_url=api_url,
        api_password=api_password,
        audit=audit_path,
        env=env,
        root=root,
        deploy=deploy,
        remote=deploy_remote,
        feed=feed_out,
        token=token,
    )


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    """The container's entry point: the environment is the whole configuration."""
    root = os.environ.get("CAMELOT_CONSOLE_ROOT") or ""
    try:
        port = int(os.environ.get("CAMELOT_CONSOLE_PORT") or DEFAULT_PORT)
    except ValueError:
        print("error: CAMELOT_CONSOLE_PORT is not an integer", file=sys.stderr)
        return 2
    try:
        return run(
            root,
            env=os.environ.get("CAMELOT_CONSOLE_ENV") or None,
            project=os.environ.get("CAMELOT_CONSOLE_PROJECT") or None,
            feed=os.environ.get("CAMELOT_CONSOLE_FEED") or None,
            game_server=os.environ.get("CAMELOT_CONSOLE_GAME_SERVER") or None,
            api_url=os.environ.get("CAMELOT_CONSOLE_API_URL") or None,
            api_password=os.environ.get("CAMELOT_CONSOLE_API_PASSWORD") or None,
            allow_restart=_truthy(os.environ.get("CAMELOT_CONSOLE_ALLOW_RESTART")),
            audit=os.environ.get("CAMELOT_CONSOLE_AUDIT") or None,
            deploy=os.environ.get("CAMELOT_CONSOLE_DEPLOY") or None,
            deploy_remote=os.environ.get("CAMELOT_CONSOLE_DEPLOY_REMOTE") or None,
            feed_out=os.environ.get("CAMELOT_CONSOLE_FEED_OUT") or None,
            host=os.environ.get("CAMELOT_CONSOLE_HOST") or DEFAULT_HOST,
            port=port,
            user=os.environ.get("CAMELOT_CONSOLE_USER") or None,
            password=os.environ.get("CAMELOT_CONSOLE_PASSWORD") or None,
        )
    except ConsoleError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
