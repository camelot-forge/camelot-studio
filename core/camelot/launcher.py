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

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

from . import feed

__all__ = [
    "DEFAULTS",
    "LauncherError",
    "begin_update",
    "command",
    "configure",
    "install_directory",
    "installed_version",
    "launcher_state",
    "packaged_page",
    "play",
    "read_settings",
    "read_shard",
    "settings_path",
    "shard_config_path",
    "shard_of",
    "stage_update",
    "start_swap",
    "state",
    "swap_script",
    "update",
    "update_of",
    "write_settings",
    "write_swap_script",
]

#: The distributed file that names the shard: beside the launcher binary, so a player
#: never types the feed's URL. `launcher.json` is the *feed's* own file, a different one.
SHARD_FILE = "shard.json"

#: The version this build carries, beside the binary: what its own update compares
#: against (`deployment.md` §D7). A source checkout has none and reads as 0.
VERSION_FILE = "version.txt"

#: What the shard's `launcher` URL publishes: the package to fetch and its version.
MANIFEST_FILE = "launcher-manifest.json"

#: What a launcher remembers between runs. The *shard's* settings are not here: they come
#: from the feed (`launcher.json`) or the distributed `shard.json`.
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


def beside_the_launcher(name: str) -> Path | None:
    """A file distributed beside the binary: `shard.json`, `version.txt`."""
    executable = Path(sys.argv[0]).expanduser()
    if executable.is_file():
        return executable.resolve().parent / name
    return None


def shard_config_path(explicit=None) -> Path | None:
    """Where the distributed shard file is: the one a run names, then
    `$CAMELOT_SHARD`, then `shard.json` beside the launcher binary. A source
    checkout run from `python -m` may find none, which is why the CLI can name
    one."""
    if explicit:
        return Path(explicit).expanduser()
    named = os.environ.get("CAMELOT_SHARD")
    if named:
        return Path(named).expanduser()
    return beside_the_launcher(SHARD_FILE)


def install_directory() -> Path | None:
    """The folder a packaged launcher runs from. A source checkout has none, and
    therefore never replaces itself."""
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).expanduser()
    return executable.resolve().parent if executable.is_file() else None


def installed_version(directory=None) -> int:
    """The version of this build (`version.txt` beside the binary), 0 when a
    source checkout has none — any published package beats it."""
    where = Path(directory) if directory is not None else install_directory()
    if where is None or not (where / VERSION_FILE).is_file():
        return 0
    try:
        return int((where / VERSION_FILE).read_text(encoding="utf-8").strip() or 0)
    except ValueError:
        return 0


def update_of(base: str, *, current=None) -> dict | None:
    """The launcher's own update, read from the shard's `launcher` URL
    (`deployment.md` §D6-§D7), or None when there is none to take.

    *current* is the version to compare against, defaulting to this build's. A
    manifest that does not parse, names a path instead of a file, or carries a
    hash of the wrong shape is refused by name — never read as "no update".
    """
    if not base:
        return None
    raw = feed._fetch(base, MANIFEST_FILE, timeout=10)
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LauncherError(f"{base}: the launcher manifest does not parse: {error}") from error
    if not isinstance(manifest, dict):
        raise LauncherError(f"{base}: the launcher manifest is not an object")
    try:
        version = int(manifest["version"])
    except (KeyError, TypeError, ValueError) as error:
        raise LauncherError(f"{base}: the launcher manifest has no version") from error
    name = str(manifest.get("file") or "")
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise LauncherError(f"{base}: the launcher manifest names no usable file: {name!r}")
    digest = str(manifest.get("sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise LauncherError(f"{base}: the launcher manifest's sha256 is not sha256:<64 hex>")
    held = int(current if current is not None else installed_version())
    if version <= held:
        return None
    return {"version": version, "file": name, "sha256": digest, "base": base}


def stage_update(directory, manifest: dict) -> dict:
    """Download the package the manifest names, check its hash, and unpack it
    beside the install (`deployment.md` §D8).

    Nothing is swapped here: the caller hands over to the swap script once this
    folder is *complete*, so a failed download or a short hash leaves the
    running launcher untouched.
    """
    base = str(manifest.get("base") or "")
    name = str(manifest.get("file") or "")
    if not base or not name:
        raise LauncherError("stage_update needs the manifest `update_of` returned")
    payload = feed._fetch(base, name)
    found = "sha256:" + hashlib.sha256(payload).hexdigest()
    if found != manifest.get("sha256"):
        raise LauncherError(
            f"{name}: the download does not hash what the manifest says "
            f"({found} != {manifest.get('sha256')})"
        )
    where = Path(directory) / "update"
    where.mkdir(parents=True, exist_ok=True)
    archive = where / name
    archive.write_bytes(payload)
    target = where / str(manifest["version"])
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.namelist():
            parts = Path(member).parts
            if Path(member).is_absolute() or ".." in parts:
                raise LauncherError(f"{archive}: {member!r} escapes the package")
        bundle.extractall(target)
    roots = [entry for entry in target.iterdir() if entry.is_dir()]
    loose = [entry for entry in target.iterdir() if entry.is_file()]
    if len(roots) != 1 or loose:
        raise LauncherError(
            f"{archive}: the package must hold one top-level folder, "
            f"found {len(roots)} folder(s) and {len(loose)} file(s)"
        )
    root = roots[0]
    if os.name == "posix":
        # A zip does not carry the executable bit through `extractall`, and a
        # staged binary that cannot run would leave the swap with no process.
        executable = root / "camelot-launcher"
        if executable.is_file():
            executable.chmod(executable.stat().st_mode | 0o111)
    return {"version": int(manifest["version"]), "archive": str(archive), "root": str(root)}


def launcher_state(settings: dict) -> dict:
    """The launcher's own version and the update its shard publishes.

    A source checkout has no package to replace, so it never reports one. A
    manifest that cannot be read is an error on this card only: the client
    update and Play do not depend on it.
    """
    found: dict = {"installed": 0, "published": None, "available": False, "error": None}
    where = install_directory()
    if where is None:
        return found
    held = installed_version(where)
    found["installed"] = held
    base = str(settings.get("launcher_url") or "")
    if not base:
        return found
    try:
        update = update_of(base, current=held)
    except (LauncherError, feed.FeedError, OSError) as error:
        found["error"] = str(error)
        return found
    if update is not None:
        found["published"] = update["version"]
        found["available"] = True
    return found


def _windows_arg(arg) -> str:
    """One argument for `start`, quoted so spaces and cmd separators stay part
    of the value."""
    return '"' + str(arg).replace('"', '\\"') + '"'


def swap_script(
    install, staged, *, pid: int, version: int, system: str | None = None, args=()
) -> str:
    """The script that replaces *install* with *staged* after *pid* exits
    (`deployment.md` §D8). It is written beside the install and refers to it
    through `%~dp0` (Windows) or the script's own folder (POSIX), so the paths
    that may carry an accent or a space never enter the file.

    *version* names the folder the old install is parked under. A failed move
    puts it back, so a half-replaced install is never what starts. *args* is
    the command line the new process starts with.
    """
    where = Path(install).expanduser().resolve()
    target = Path(staged).expanduser().resolve()
    try:
        relative = target.relative_to(where.parent)
    except ValueError as error:
        raise LauncherError(
            f"{target}: the staged package is not beside the install"
        ) from error
    windows = (system or sys.platform).startswith("win")
    old = f"{where.name}.old-{version}"
    if windows:
        staged_path = str(relative).replace("/", "\\")
        launch = " ".join(_windows_arg(arg) for arg in args)
        return f"""@echo off
setlocal
set "HERE=%~dp0"
cd /d "%HERE%"
set "INSTALL=%HERE%{where.name}"
set "OLD=%HERE%{old}"
set "UPDATE=%HERE%update"
set "STAGED=%HERE%{staged_path}"
set "EXE=camelot-launcher.exe"
set "PID={int(pid)}"
set "TRIES=0"
:wait
tasklist /FI "PID eq %PID%" /NH 2>NUL | findstr /R /C:"[ ]%PID%[ ]" >NUL
if errorlevel 1 goto swap
ping -n 2 127.0.0.1 >NUL
set /a TRIES+=1
if %TRIES% GEQ 60 exit /b 1
goto wait
:swap
if exist "%OLD%" rmdir /s /q "%OLD%"
move "%INSTALL%" "%OLD%" >NUL 2>&1
if errorlevel 1 exit /b 1
move "%STAGED%" "%INSTALL%" >NUL 2>&1
if errorlevel 1 goto restore
start "" "%INSTALL%\\%EXE%" {launch}
rmdir /s /q "%UPDATE%" >NUL 2>&1
del "%~f0" >NUL 2>&1
exit /b 0
:restore
move "%OLD%" "%INSTALL%" >NUL 2>&1
exit /b 1
"""
    staged_path = relative.as_posix()
    launch = " ".join(shlex.quote(str(arg)) for arg in args)
    return f"""#!/bin/sh
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL="$HERE/{where.name}"
OLD="$HERE/{old}"
UPDATE="$HERE/update"
STAGED="$HERE/{staged_path}"
EXE="$INSTALL/camelot-launcher"
PID={int(pid)}
TRIES=0
while kill -0 "$PID" 2>/dev/null; do
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge 300 ]; then exit 1; fi
  sleep 0.2
done
cd /
rm -rf "$OLD"
mv "$INSTALL" "$OLD" || exit 1
if mv "$STAGED" "$INSTALL"; then
  nohup "$EXE" {launch} >/dev/null 2>&1 &
  rm -rf "$OLD" "$UPDATE"
  rm -f -- "$0"
else
  mv "$OLD" "$INSTALL"
  exit 1
fi
"""


def write_swap_script(
    install, staged, *, pid: int, version: int, system: str | None = None, args=()
) -> Path:
    """Write the swap script beside the install and make it executable on POSIX.

    The script is never inside the folder it renames.
    """
    windows = (system or sys.platform).startswith("win")
    script = swap_script(
        install, staged, pid=pid, version=version, system=system, args=args
    )
    where = Path(install).expanduser().resolve().parent / (
        f"swap-launcher-{int(pid)}.bat" if windows else f"swap-launcher-{int(pid)}.sh"
    )
    where.write_text(script, encoding="utf-8", newline="\r\n" if windows else "\n")
    if not windows:
        where.chmod(0o755)
    return where


def start_swap(script, *, system: str | None = None) -> None:
    """Start the swap script detached, so it outlives the launcher it waits on."""
    if (system or sys.platform).startswith("win"):
        subprocess.Popen(
            ["cmd", "/c", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=0x00000008,  # DETACHED_PROCESS
        )
        return
    subprocess.Popen(
        ["/bin/sh", str(script)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def begin_update(settings: dict, *, system: str | None = None, argv=None) -> dict:
    """Stage the launcher's next version and hand over to the swap script.

    The route calls this after the page asks; the process must then stop, which
    is the caller's job. Nothing here touches the running install. *argv* is
    what the new process starts with, defaulting to this run's own options plus
    `--no-open`: the page that asked is already open.
    """
    install = install_directory()
    if install is None:
        raise LauncherError("a source checkout has no package to replace")
    base = str(settings.get("launcher_url") or "")
    if not base:
        raise LauncherError("this launcher has no launcher URL to update from")
    manifest = update_of(base)
    if manifest is None:
        raise LauncherError("this launcher is already at the published version")
    staged = stage_update(install.parent, manifest)
    system = system or sys.platform
    executable = "camelot-launcher.exe" if system.startswith("win") else "camelot-launcher"
    if not (Path(staged["root"]) / executable).is_file():
        raise LauncherError(f"{staged['root']}: the package has no {executable}")
    args = list(sys.argv[1:] if argv is None else argv)
    if "--no-open" not in args:
        args.append("--no-open")
    script = write_swap_script(
        install,
        staged["root"],
        pid=os.getpid(),
        version=installed_version(install),
        system=system,
        args=args,
    )
    start_swap(script, system=system)
    return {"version": manifest["version"], "script": str(script), "root": staged["root"]}


def _with_slash(value: str) -> str:
    """A feed base URL ends with `/`: the protocol composes `base + path` and
    `write_patchlist` refuses anything else."""
    value = str(value)
    return value if not value or value.endswith("/") else value + "/"


def read_shard(path=None) -> dict:
    """The shard this launcher was built for (`shard.json`): its name, its feed,
    and an optional game address used only when the feed does not carry one.

    Missing is a fact — a development run can name a feed directly — but a file
    that exists and does not parse is an error, never a silent default."""
    where = shard_config_path(path)
    if where is None or not where.exists():
        return {}
    if not where.is_file():
        raise LauncherError(f"{where}: not a file")
    try:
        document = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LauncherError(f"{where}: {error}") from error
    if not isinstance(document, dict):
        raise LauncherError(f"{where}: the shard file must be an object")
    return {
        "name": str(document.get("name") or ""),
        "feed": _with_slash(str(document.get("feed") or "")),
        "launcher": _with_slash(str(document.get("launcher") or "")),
        "server": str(document.get("server") or ""),
        "path": str(where),
    }


def configure(settings: dict, shard: dict | None = None, *, feed=None, server=None, environ=None) -> dict:
    """The settings one run uses: the distributed shard first, then the
    environment, then the command line. The shard's name and fallback server
    ride along; the explicit server override and neither of those is written to
    the player's settings file."""
    environ = os.environ if environ is None else environ
    shard = shard or {}
    found = dict(settings)
    found["shard_name"] = str(shard.get("name") or "")
    found["shard_server"] = str(shard.get("server") or "")
    found["launcher_url"] = str(shard.get("launcher") or "")
    found["server_override"] = ""
    if shard.get("feed"):
        found["feed"] = _with_slash(str(shard["feed"]))
    if environ.get("CAMELOT_FEED_URL"):
        found["feed"] = _with_slash(str(environ["CAMELOT_FEED_URL"]))
    if environ.get("CAMELOT_GAME_SERVER"):
        found["server_override"] = str(environ["CAMELOT_GAME_SERVER"])
    if feed:
        found["feed"] = _with_slash(str(feed))
    if server:
        found["server_override"] = str(server)
    return found


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
        "shard": str(carried.get("shard") or settings.get("shard_name") or ""),
        "server": str(
            settings.get("server_override")
            or carried.get("server")
            or settings.get("shard_server")
            or settings.get("server")
            or ""
        ),
        "built": carried.get("built"),
    }


def state(settings: dict) -> dict:
    """What the page draws: the client, the account, the shard and the versions.

    No walk of the client happens here — a launcher that hashes 5 GB to show a page
    would be a launcher nobody opens. The walk belongs to the update.
    """
    where = str(settings.get("client") or "")
    client = Path(where).expanduser() if where else None
    internal = ("launcher_url", "wineprefix", "server_override", "shard_name", "shard_server")
    found = {
        "settings": {key: value for key, value in settings.items() if key not in internal},
        "settings_path": str(settings.get("path") or settings_path()),
        "client": str(client) if client else "",
        "client_there": bool(client and client.is_dir()),
        "account": str(settings.get("account") or ""),
        "installed": settings.get("installed"),
        "launcher": launcher_state(settings),
        "shard": {},
        "problems": [],
    }
    if not where:
        found["problems"].append("no client folder yet — pick one")
    elif not (client / "connect.exe").is_file():
        found["problems"].append(f"{client} does not look like a DAoC client (no connect.exe)")
    name = str(settings.get("shard_name") or "")
    fallback = str(
        settings.get("server_override")
        or settings.get("shard_server")
        or settings.get("server")
        or ""
    )
    if not settings.get("feed"):
        found["problems"].append("no feed URL yet — your shard's patch address")
        if name:
            found["shard"] = {"shard": name, "server": fallback}
        return found
    try:
        found["shard"] = shard_of(str(settings["feed"]), settings=settings)
    except LauncherError as error:
        found["problems"].append(str(error))
        if name or fallback:
            found["shard"] = {"shard": name, "server": fallback}
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
    address = str(settings.get("server_override") or "")
    if not address and settings.get("feed"):
        try:
            address = shard_of(str(settings["feed"]), settings=settings)["server"]
        except LauncherError:
            address = ""
    if not address:
        address = str(settings.get("shard_server") or settings.get("server") or "")
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
    output: a client that cannot start says why in *its* words, not ours.

    Refuses while the client is behind the feed (`C §D17`): the page disables Play,
    and this check is the gate — a page in a browser is not one."""
    argv = command(settings, account=account, password=password, system=system)
    found = state(settings)
    if not found.get("up_to_date"):
        if found["problems"]:
            raise LauncherError("; ".join(found["problems"]))
        installed = found.get("installed")
        published = (found.get("shard") or {}).get("version")
        raise LauncherError(
            "an update is waiting — the client is at "
            f"{installed if installed is not None else 'nothing installed'}, the shard "
            f"publishes {published}: Update first"
        )
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
                return self._json({"progress": dict(PROGRESS)})
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
            if path == "/api/launcher-update":
                try:
                    started = begin_update(settings)
                except (LauncherError, feed.FeedError, OSError) as error:
                    return self._json({"error": str(error)}, status=400)
                # The script waits for this process to be gone before it renames
                # the install. The answer reaches the page first (`deployment.md`
                # §D8).
                threading.Timer(0.5, self.server.shutdown).start()
                return self._json({"launcher": started, "restarting": True})
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


def packaged_page() -> str:
    """The page a player package carries: PyInstaller's `_MEIPASS/page`, then
    `page/` beside the executable, then the source checkout's build. A packaged
    launcher must never depend on the repository being present."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        candidate = Path(bundled) / "page"
        if (candidate / "index.html").is_file():
            return str(candidate)
    beside = Path(sys.argv[0]).resolve().parent / "page"
    if (beside / "index.html").is_file():
        return str(beside)
    return _default_page()


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
