"""The launcher: a small local process, a page, and the game at the end of it.

Player-facing (`C §D14`), and the whole of what a player runs of ours. It reads a feed,
brings a client up to it, and starts the game — with `connect.exe <dll> <address>
<account> <password>`, the entry point the client supports (never `camelot.exe`, which
would let the retail patcher overwrite everything).

It imports `feed` (the protocol) and the standard library, and nothing else of ours: no
project, no release, no database, no daemon. `test_players_half_is_not_the_admins` holds
that line.

What it deliberately does not do: keep a pristine store (that is the author's install),
merge anything (the protocol replaces whole files), or know what a "release" is — a
player's machine has no business with any of those.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from . import feed

__all__ = [
    "DEFAULTS",
    "LauncherError",
    "command",
    "play",
    "read_settings",
    "settings_path",
    "shard_of",
    "state",
    "update",
    "write_settings",
]

#: What a launcher remembers between runs. The *shard's* settings are not here: they come
#: from the feed (`launcher.json`), so a player is told one URL and nothing else.
DEFAULTS: dict = {
    "feed": "",
    "client": "",
    "account": "",
    "remember": True,
    "server": "",
    "wine": "",
    "wineprefix": "",
}


class LauncherError(ValueError):
    """Anything the launcher refuses, with the reason in the message."""


# --- what it remembers ---------------------------------------------------------


def settings_path(explicit=None) -> Path:
    """Where a launcher keeps its own settings: beside the player's other settings, not
    in the client (a client is the game's, and a launcher writes nothing there it did
    not download)."""
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "camelot-launcher" / "settings.json"


def read_settings(path=None) -> dict:
    """The launcher's own settings, with the defaults filled in: a missing file is a
    fresh install, not an error. The file's own path rides along, so a page can show
    *which* launcher it is looking at."""
    where = settings_path(path)
    found = dict(DEFAULTS)
    if where.is_file():
        try:
            document = json.loads(where.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise LauncherError(f"{where}: {error}") from error
        if isinstance(document, dict):
            found.update(
                {
                    key: value
                    for key, value in document.items()
                    if key in DEFAULTS or key == "installed"
                }
            )
    found["path"] = str(where)
    return found


def write_settings(document: dict, path=None) -> Path:
    """Written whole, and only what a launcher owns: the settings file never carries a
    password, whatever the page had in a field. A document read back from an explicit
    `--settings` path is written there again, not beside the player's other settings."""
    where = settings_path(path or document.get("path") or None)
    where.parent.mkdir(parents=True, exist_ok=True)
    kept = {key: document.get(key, value) for key, value in DEFAULTS.items()}
    if "installed" in document:
        kept["installed"] = document["installed"]
    where.write_text(json.dumps(kept, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    document["path"] = str(where)
    return where


# --- what it can say without walking a client ----------------------------------


def shard_of(base_url: str, *, settings: dict | None = None) -> dict:
    """The shard the feed belongs to: its name, where the game is, and the version it
    publishes. The settings stand in when the feed carries no `launcher.json`."""
    settings = settings or {}
    try:
        announced = feed.peek(base_url)
    except feed.FeedError as error:
        raise LauncherError(f"{base_url}: {error}") from error
    carried = announced.get("launcher") or {}
    return {
        "feed": base_url,
        "version": announced["version"],
        "shard": str(carried.get("shard") or ""),
        "server": str(carried.get("server") or settings.get("server") or ""),
        "built": carried.get("built"),
    }


def state(settings: dict) -> dict:
    """What the page draws: the client, the account, the shard and the versions.

    No walk of the client happens here — a launcher that hashes 5 GB to show a page
    would be a launcher nobody opens. The walk belongs to the update.
    """
    where = str(settings.get("client") or "")
    client = Path(where).expanduser() if where else None
    found = {
        "settings": {key: value for key, value in settings.items() if key != "wineprefix"},
        "settings_path": str(settings.get("path") or settings_path()),
        "client": str(client) if client else "",
        "client_there": bool(client and client.is_dir()),
        "account": str(settings.get("account") or ""),
        "installed": settings.get("installed"),
        "shard": {},
        "problems": [],
    }
    if not where:
        found["problems"].append("no client folder yet — pick one")
    elif not (client / "connect.exe").is_file():
        found["problems"].append(f"{client} does not look like a DAoC client (no connect.exe)")
    if not settings.get("feed"):
        found["problems"].append("no feed URL yet — your shard's patch address")
        return found
    try:
        found["shard"] = shard_of(str(settings["feed"]), settings=settings)
    except LauncherError as error:
        found["problems"].append(str(error))
        return found
    found["up_to_date"] = found["installed"] is not None and int(found["installed"]) >= int(
        found["shard"]["version"]
    )
    if not found["shard"]["server"]:
        found["problems"].append(
            "the feed does not say where the game is — the launcher needs it to play"
        )
    return found


# --- what it does --------------------------------------------------------------


def update(settings: dict, *, on_progress=None) -> dict:
    """Bring the client up to what the feed publishes, then remember the version.

    The whole of the update is `feed.update`: the same walk, the same whole-file rule,
    the same read-back. The launcher adds one thing — it writes down what it installed,
    which is what the page compares against.
    """
    client = str(settings.get("client") or "")
    base_url = str(settings.get("feed") or "")
    if not client:
        raise LauncherError("no client folder yet — pick one")
    if not base_url:
        raise LauncherError("no feed URL yet — your shard's patch address")
    report = feed.update(base_url, client, on_file=on_progress)
    settings["installed"] = report["version"]
    write_settings(settings)
    return report


def _wine(settings: dict) -> str:
    named = str(settings.get("wine") or "")
    if named:
        return named
    for candidate in ("/opt/wine-staging/bin/wine", "/usr/bin/wine"):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("wine")
    if not found:
        raise LauncherError(
            "no wine found — set `wine` in the launcher's settings, or play on Windows"
        )
    return found


def command(
    settings: dict,
    *,
    account: str,
    password: str,
    system: str | None = None,
) -> list[str]:
    """The client's own entry point, exactly as the operator's scripts call it:
    `connect.exe game1127.dll <address> <account> <password>`.

    The address comes from the feed's `launcher.json` when it carries one. The password
    is on the command line because the client demands it there — it is never written to
    a log, and never saved unless the player asks for it.
    """
    client = Path(str(settings.get("client") or "")).expanduser()
    if not (client / "connect.exe").is_file():
        raise LauncherError(f"{client}: no connect.exe — that is not a DAoC client")
    address = str(settings.get("server") or "")
    if not address:
        address = shard_of(str(settings.get("feed") or ""), settings=settings)["server"]
    if not address:
        raise LauncherError("no game address — the feed does not say where the game is")
    if not account or not password:
        raise LauncherError("an account and a password are what the client asks for")
    rest = ["connect.exe", "game1127.dll", address, account, password]
    if (system or sys.platform).startswith("win"):
        return rest
    return [_wine(settings), *rest]


def play(settings: dict, *, account: str, password: str, system: str | None = None):
    """Start the game, from the client's folder. What runs keeps the launcher's own
    output: a client that cannot start says why in *its* words, not ours."""
    argv = command(settings, account=account, password=password, system=system)
    client = Path(str(settings["client"])).expanduser()
    environment = dict(os.environ)
    if not (system or sys.platform).startswith("win"):
        prefix = str(settings.get("wineprefix") or "") or str(Path.home() / ".wine")
        environment["WINEPREFIX"] = prefix
    windowless = system is None and sys.platform.startswith("win")
    return subprocess.Popen(
        argv,
        cwd=str(client),
        env=environment,
        shell=False,
        creationflags=0x08000000 if windowless else 0,
    )


# --- the local page ------------------------------------------------------------


PROGRESS = {
    "running": False,
    "stage": "",
    "what": "",
    "done": 0,
    "total": 0,
    "error": None,
    "version": None,
}


def _begin_progress() -> None:
    """Say a run has started before the thread does: the page reads the answer to
    `/api/update` immediately, and a walk that had not set its own state yet would look
    finished."""
    PROGRESS.update(
        {
            "running": True,
            "stage": "checking",
            "what": "",
            "done": 0,
            "total": 0,
            "error": None,
            "version": None,
        }
    )


def _update_in_background(settings: dict) -> None:
    """One update at a time, its progress in `PROGRESS` (a page polls it: a local
    socket, one player, and no reason to build a stream).

    Two stages, because they take different kinds of time: **checking** is the walk of
    the client (minutes on a real one, and there is nothing to count yet), **writing**
    counts the files it fetches. A page that showed "0%" through the walk would look
    stuck.
    """

    def step(name: str) -> None:
        PROGRESS["done"] += 1
        PROGRESS["what"] = name

    _begin_progress()
    try:
        report = feed.plan(str(settings["feed"]), str(settings["client"]))
        PROGRESS["total"] = len(report["wanted"])
        PROGRESS["stage"] = "writing"
        report = update(settings, on_progress=step)
        PROGRESS["version"] = report["version"]
    except (feed.FeedError, LauncherError, OSError) as error:
        PROGRESS["error"] = str(error)
    finally:
        PROGRESS["running"] = False
        PROGRESS["stage"] = ""


def handler_for(settings: dict, page):
    """The page's own handler: static files from *page*, and the three routes a browser
    cannot do itself — say the state, run an update, start the game."""
    import http.server
    from urllib.parse import urlparse as _parse

    where = Path(page).expanduser()
    if not (where / "index.html").is_file():
        raise LauncherError(
            f"{where}: no index.html — build the page (`pnpm build` in web/launcher)"
        )

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(where), **kwargs)

        def log_message(self, style, *args):  # the log is the operator's, not the page's
            return

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = _parse(self.path).path
            if path == "/api/state":
                return self._json(
                    {"state": state(settings), "progress": dict(PROGRESS), "settings": settings}
                )
            if path == "/api/progress":
                return self._json(dict(PROGRESS))
            if path.startswith("/api/"):
                return self._json({"error": f"no such route: {path}"}, status=404)
            return super().do_GET()

        def do_POST(self):
            path = _parse(self.path).path
            body = self._body()
            if path == "/api/settings":
                for key in (
                    "feed",
                    "client",
                    "account",
                    "remember",
                    "server",
                    "wine",
                    "wineprefix",
                ):
                    if key in body:
                        settings[key] = body[key]
                if not settings.get("remember"):
                    settings["account"] = ""
                write_settings(settings)
                return self._json({"state": state(settings), "settings": settings})
            if path == "/api/update":
                if PROGRESS["running"]:
                    return self._json({"progress": dict(PROGRESS)}, status=409)
                _begin_progress()
                threading.Thread(
                    target=_update_in_background, args=(settings,), daemon=True
                ).start()
                return self._json({"progress": dict(PROGRESS)})
            if path == "/api/play":
                try:
                    handle = play(
                        settings,
                        account=str(body.get("account") or settings.get("account") or ""),
                        password=str(body.get("password") or ""),
                    )
                except (LauncherError, OSError) as error:
                    return self._json({"error": str(error)}, status=400)
                return self._json({"started": handle.pid})
            return self._json({"error": f"no such route: {path}"}, status=404)

    return Handler


def server_for(settings: dict, page, *, host: str = "127.0.0.1", port: int = 8710):
    """A bound server, so a test can drive the routes without a window and the caller
    can decide which port."""
    import http.server

    return http.server.ThreadingHTTPServer((host, port), handler_for(settings, page))


def serve(settings: dict, *, page, host: str = "127.0.0.1", port: int = 8710, open_browser=True):
    """Serve the page and its routes on the loopback interface, until interrupted.

    The page is a directory built by the web side (`web/launcher`): this process hands
    files out and does the two privileged things a browser cannot — writing into the
    client's folder and starting the game.
    """
    import webbrowser

    server = server_for(settings, page, host=host, port=port)
    seen = f"http://{host}:{server.server_port}/"
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(seen)).start()
    shard = state(settings).get("shard") or {}
    print(f"camelot launcher · {shard.get('shard') or 'no feed'} · {seen}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _default_page() -> str:
    """The page a source checkout builds (`web/launcher/dist`), so running the launcher
    from the repository needs no arguments."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "web" / "launcher" / "dist"
        if candidate.is_dir():
            return str(candidate)
    return str(here.parents[3] / "web" / "launcher" / "dist")


def _default_platform() -> str:
    return platform.system().lower()
