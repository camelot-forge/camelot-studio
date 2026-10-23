"""This machine's own facts: `machine.json`, and what it must say.

`project.py` owns the file's shape and the precedence (`--flag`, then the file,
then the environment). This module asks the questions the desk's verbs ask
before they start: is the file here, does it name a client, does the database
its config points at match the environment's, and is that database reachable?

The rule is the same everywhere: a machine that cannot answer is refused by
name, with the command that fixes it. Never a silent fallback.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from . import project as project_mod
from . import serverrows

#: What a check found. `warn` never stops a daemon; the three others do.
OK = "ok"
WARN = "warn"
MISSING = "missing"
INVALID = "invalid"
UNREACHABLE = "unreachable"

HARD = (MISSING, INVALID, UNREACHABLE)

#: The connection timeout of a live check. A local MariaDB answers in
#: microseconds; a second is already generous.
TIMEOUT = 1.0


class MachineError(project_mod.ProjectError):
    """What this machine's file does not say, and what to run about it."""


def path(root: str | os.PathLike) -> Path:
    return Path(root) / project_mod.MACHINE_FILE


def document(root: str | os.PathLike) -> dict:
    """The file as a mapping, or an empty one when it is absent."""
    return project_mod.machine(root)


def require(root: str | os.PathLike) -> dict:
    """The file, or a refusal that names it and the command that writes it."""
    data = document(root)
    if not data:
        raise MachineError(
            f"{path(root)} is missing: name this machine's client, database and key first — "
            f"`camelot project machine --dir {Path(root)} --client <client> "
            "--db-config <serverconfig.xml>`"
        )
    return data


def entry(root: str | os.PathLike, env: str) -> dict:
    """One environment's answers from this machine, normalised to a mapping."""
    raw = (document(root).get("environments") or {}).get(str(env))
    if raw is None:
        return {}
    if isinstance(raw, str):
        return {"root": raw}
    if not isinstance(raw, dict):
        raise MachineError(
            f"{path(root)}: environments.{env} wants a folder, or an object with one"
        )
    return raw


def resolve_client(root: str | os.PathLike, override=None) -> Path:
    """Where this machine's client lives: the flag, the file, then the
    environment — and a refusal that says which of the three is missing."""
    if override:
        return Path(override).expanduser()
    named = document(root).get("client")
    if named:
        return Path(str(named)).expanduser()
    from_env = os.environ.get("DAOC_CLIENT")
    if from_env:
        return Path(from_env).expanduser()
    raise MachineError(
        f"{path(root)} names no client, and $DAOC_CLIENT is unset — "
        f"`camelot project machine --dir {Path(root)} --client <client>`"
    )


def resolve_db_config(root: str | os.PathLike, env: str = "local", override=None) -> Path | None:
    """The `serverconfig.xml` the viewer reads rows from: the flag, the
    environment's entry, then the older top-level shape. Absent is not an
    error: a client-only session is a valid session."""
    if override:
        return Path(override).expanduser()
    named = entry(root, env).get("db_config")
    if not named and env == "local":
        named = document(root).get("db_config")
    return Path(str(named)).expanduser() if named else None


def _reachable(host: str, port: int, timeout: float = TIMEOUT) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as error:
        return False, str(error) or error.__class__.__name__


def check(root: str | os.PathLike, *, env: str = "local", live: bool = True) -> list[dict]:
    """What this machine says, read back. One finding per field:

    `ok` / `warn` / `missing` / `invalid` / `unreachable`. A missing file stops
    the walk: nothing else can be read, and one actionable line is better than
    a cascade.
    """
    found: list[dict] = []

    def add(field: str, state: str, detail: str = "") -> None:
        found.append({"field": field, "state": state, "detail": detail})

    where = path(root)
    if not where.is_file():
        add(
            project_mod.MACHINE_FILE,
            MISSING,
            f"{where}: run `camelot project machine --dir {Path(root)} --client <client> "
            "--db-config <serverconfig.xml>`",
        )
        return found
    data = document(root)
    if not data:
        add(project_mod.MACHINE_FILE, INVALID, f"{where}: expected an object")
        return found
    add(project_mod.MACHINE_FILE, OK, str(where))

    named = data.get("client")
    if not named:
        add("client", MISSING, "no client: name it here, or pass --client")
    else:
        client = Path(str(named)).expanduser()
        if client.is_dir():
            add("client", OK, str(client))
        else:
            add("client", INVALID, f"{client}: not a directory")

    key = data.get("release_key")
    if key:
        release_key = Path(str(key)).expanduser()
        if release_key.is_file():
            add("release_key", OK, str(release_key))
        else:
            add("release_key", INVALID, f"{release_key}: not a file")
    else:
        add(
            "release_key",
            WARN,
            "no release_key: a publish from this machine would be unsigned",
        )

    project = project_mod.load(root)
    known = set(project.environments)
    for name in sorted(data.get("environments") or {}):
        if name not in known:
            add(
                f"environments.{name}",
                INVALID,
                f"not one of the project's environments: {', '.join(project.environments)}",
            )

    item = entry(root, env)
    if not item:
        add(
            f"environments.{env}",
            WARN,
            "no entry for this environment: the viewer renders the client only",
        )
        return found

    base = f"environments.{env}"
    folder = item.get("root")
    if not folder:
        add(f"{base}.root", WARN, "no folder: `env` and the Console cannot reach it")
    else:
        root_dir = Path(str(folder)).expanduser()
        if root_dir.is_dir():
            add(f"{base}.root", OK, str(root_dir))
        else:
            add(f"{base}.root", INVALID, f"{root_dir}: not a directory")

    database = item.get("database")
    if not database:
        add(f"{base}.database", WARN, "no database name: `env provision` cannot name one")

    config = item.get("db_config")
    connection = None
    if not config:
        add(
            f"{base}.db_config",
            WARN,
            "no serverconfig.xml: the viewer renders the client only, without mobs",
        )
    else:
        config_path = Path(str(config)).expanduser()
        if not config_path.is_file():
            add(f"{base}.db_config", INVALID, f"{config_path}: not a file")
        else:
            try:
                connection = serverrows.parse_server_config(str(config_path))
            except serverrows.ServerRowsError as error:
                add(f"{base}.db_config", INVALID, str(error))
            else:
                named_db = connection.get("database")
                if database and named_db != database:
                    add(
                        f"{base}.db_config#database",
                        INVALID,
                        f"{config_path} connects to {named_db!r}, but "
                        f"{project_mod.MACHINE_FILE} names {database!r}",
                    )
                else:
                    add(f"{base}.db_config", OK, str(config_path))

    db = item.get("db") or {}
    if connection is not None:
        # The viewer connects with the config, so the probe follows it. `db` is
        # what `env` uses; a disagreement is named, not silently resolved.
        port = int(connection["port"])
        host = str(connection["host"])
        named = db.get("port")
        if named is None:
            add(f"{base}.db.port", OK, f"{port} (from db_config)")
        else:
            try:
                named = int(named)
            except (TypeError, ValueError):
                add(f"{base}.db.port", INVALID, f"{named!r}: not a number")
            else:
                if named != port:
                    add(
                        f"{base}.db.port",
                        WARN,
                        f"{named} here, {port} in db_config: `env` and the viewer "
                        "would not read the same server",
                    )
                else:
                    add(f"{base}.db.port", OK, str(port))
    else:
        host = str(db.get("host") or "localhost")
        port = db.get("port")
        if port is None:
            add(
                f"{base}.db.port",
                WARN,
                "no port: name db.port, or a db_config that carries one",
            )
        else:
            try:
                port = int(port)
                add(f"{base}.db.port", OK, str(port))
            except (TypeError, ValueError):
                port = None
                add(f"{base}.db.port", INVALID, f"{port!r}: not a number")

    if live and port is not None:
        target = f"{host}:{port}"
        reachable, why = _reachable(host, port)
        add(
            f"{base}.db",
            OK if reachable else UNREACHABLE,
            target if reachable else f"{target}: {why}",
        )

    return found


def failures(findings) -> list[dict]:
    return [item for item in findings if item["state"] in HARD]


def _environment_root(config_path: Path) -> Path:
    """`<root>/server/config/serverconfig.xml` → `<root>`. The documented shape
    is the only one that is guessed; the operator passes `--root-dir` otherwise."""
    if config_path.parent.name == "config" and config_path.parent.parent.name == "server":
        return config_path.parent.parent.parent
    return config_path.parent


def validate_config(path) -> tuple[Path, dict]:
    """A `serverconfig.xml` this machine can read, or a refusal that names it.
    Called before a project is created, so a bad path creates nothing."""
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise MachineError(f"no serverconfig.xml at {config_path}")
    try:
        return config_path, serverrows.parse_server_config(str(config_path))
    except serverrows.ServerRowsError as error:
        raise MachineError(str(error)) from error


def write(
    root: str | os.PathLike,
    *,
    env: str = "local",
    client=None,
    root_dir=None,
    db_config=None,
    database=None,
    release_key=None,
    author=None,
) -> tuple[Path, dict]:
    """Write or update this machine's file. Given values win; everything else
    is preserved. The file is validated as it is written, not after."""
    where = path(root)
    data = document(root)
    environments = data.get("environments")
    if not isinstance(environments, dict):
        environments = {}
    data["environments"] = environments

    if author:
        data["author"] = project_mod.slug(author)
    elif not data.get("author"):
        data["author"] = project_mod.author_name(root)

    if client is not None:
        client_dir = Path(client).expanduser()
        if not client_dir.is_dir():
            raise MachineError(f"no client directory at {client_dir}")
        data["client"] = str(client_dir)

    if release_key is not None:
        key = Path(release_key).expanduser()
        if not key.is_file():
            raise MachineError(f"no release key at {key}")
        data["release_key"] = str(key)

    if db_config is not None:
        config_path, connection = validate_config(db_config)
        current = environments.get(env)
        if isinstance(current, str):
            current = {"root": current}
        item = dict(current or {})
        item["root"] = str(Path(root_dir).expanduser() if root_dir else _environment_root(config_path))
        item["database"] = str(database or connection["database"])
        item["db"] = {
            "host": str(connection["host"]),
            "port": int(connection["port"]),
            "user": str(connection["user"]),
        }
        item["db_config"] = str(config_path)
        environments[env] = item
    elif root_dir is not None or database is not None:
        current = environments.get(env)
        if isinstance(current, str):
            current = {"root": current}
        item = dict(current or {})
        if root_dir is not None:
            item["root"] = str(Path(root_dir).expanduser())
        if database is not None:
            item["database"] = str(database)
        environments[env] = item

    where.parent.mkdir(parents=True, exist_ok=True)
    where.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return where, data
