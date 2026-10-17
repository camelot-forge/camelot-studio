"""The environment's server half: a database, a folder, and its own record.

`camelot env provision local` is the verb (`server-install.md` §D6), and what it
does on a machine without containers is what the project's own image does at
boot: **drop the environment's own database, create it empty, import OpenDAoC's
baseline into it, and record what was imported** — the identity every later
answer is read against (§D8; M-10 asked what identifies a baseline).

Three rules make that safe on a machine that also runs a *live* server:

- the database it touches is the one the environment's own record names
  (`<env>/server/camelot/env.json`) or, on a first provision, the one
  `machine.json` names for that environment — never a name that merely looks
  like a database's;
- a database that already exists and is *not* ours is refused by name, and so is
  the database the authoring server edits (the `serverconfig.xml` this project
  reads its rows from): they hold somebody's content, and this is the verb that
  deletes;
- the baseline directory is this machine's answer (`machine.json`), never a path
  guessed in the code.

Nothing here talks to a *running* server process: provisioning is files and
tables. Applying a release's rows is the Installer's step (`installer.py`);
`camelot env apply` runs it for an environment this machine can reach.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from . import installer, serverrows, targets
from . import journal as journal_mod
from . import project as project_mod
from . import release as release_mod

FORMAT = "camelot.env/1"
RECORD = "env.json"

__all__ = [
    "FORMAT",
    "RECORD",
    "EnvError",
    "apply",
    "baseline_dir",
    "baseline_files",
    "baseline_identity",
    "connection_for",
    "database_for",
    "describe",
    "provision",
    "record_path",
    "reload",
    "verify",
]


class EnvError(ValueError):
    """Anything this module refuses to do, with the reason in the message."""


# --- the environment's own record --------------------------------------------


def record_path(env_root) -> Path:
    """`<env>/server/camelot/env.json` — what this environment was built on."""
    return installer.folder(env_root) / RECORD


def read_record(env_root) -> dict:
    path = record_path(env_root)
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnvError(f"{path}: {error}") from error
    if not isinstance(document, dict):
        raise EnvError(f"{path}: expected an object")
    return document


def write_record(env_root, document: dict) -> Path:
    path = record_path(env_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


# --- this machine's answers ---------------------------------------------------


def _mapped(project: project_mod.Project, env: str) -> dict:
    """The environment's entry in `machine.json`, as a mapping.

    A string is the folder alone (the first shape the format had); an object
    carries this machine's own answers — the folder, the database, the baseline,
    the connection — and none of it is tracked (`project.md` §D4).
    """
    mapped = (project_mod.machine(project.root).get("environments") or {}).get(str(env))
    if mapped is None:
        return {}
    if isinstance(mapped, str):
        return {"root": mapped}
    if not isinstance(mapped, dict):
        raise EnvError(
            f"{project_mod.MACHINE_FILE}: environments.{env} wants a folder, or an object with one"
        )
    return mapped


def folder(project: project_mod.Project, env: str) -> Path:
    """Where the environment lives on this machine — the one definition
    `pipeline` and this module share."""
    from . import pipeline as pipeline_mod

    return pipeline_mod.folder(project, env)


def _authoring(project: project_mod.Project) -> tuple[str, dict]:
    """The database this project *edits*: the connection its server rows come
    from. Empty when this machine names no `serverconfig.xml`."""
    named = _mapped(project, "local").get("db_config") or project_mod.machine(project.root).get(
        "db_config"
    )
    if not named:
        return "", {}
    path = Path(str(named)).expanduser()
    if not path.is_file():
        return "", {}
    try:
        connection = serverrows.parse_server_config(str(path))
    except (OSError, serverrows.ServerRowsError):
        return "", {}
    return str(connection.get("database") or ""), connection


def database_for(project: project_mod.Project, env: str) -> str:
    """The name of the environment's own database: what `machine.json` names for
    it, or what its record says, or `<project>_<env>` — a slug, so MySQL accepts
    it.

    This machine's own answer wins over the record: the record is what the last
    provision wrote, and the file the operator edits is the later word — naming
    another database there is how a *new* one gets provisioned. `verify` names
    the two when they disagree.
    """
    named = _mapped(project, env).get("database")
    if named:
        return str(named)
    recorded = read_record(folder(project, env)).get("database")
    if recorded:
        return str(recorded)
    return f"{project_mod.slug(project.name).replace('-', '_')}_{str(env).replace('-', '_')}"


def connection_for(project: project_mod.Project, env: str) -> dict:
    """How to reach the database an environment's server would use.

    This machine's own answers, in order: the environment's `db` block, then the
    authoring server's `serverconfig.xml` (the same instance, another database),
    then `$CAMELOT_DB_HOST`/`_PORT`/`_USER`/`_PASSWORD`. The **database name**
    comes from the environment itself, never from the authoring server.
    """
    mapped = _mapped(project, env)
    block = mapped.get("db") or {}
    if not isinstance(block, dict):
        raise EnvError(f"{project_mod.MACHINE_FILE}: environments.{env}.db wants an object")
    found = _authoring(project)[1]
    return {
        "host": str(
            block.get("host")
            or found.get("host")
            or os.environ.get("CAMELOT_DB_HOST")
            or "127.0.0.1"
        ),
        "port": int(
            block.get("port") or found.get("port") or os.environ.get("CAMELOT_DB_PORT") or 3306
        ),
        "user": str(
            block.get("user") or found.get("user") or os.environ.get("CAMELOT_DB_USER") or "root"
        ),
        "password": str(
            block["password"]
            if block.get("password") is not None
            else (found.get("password") or os.environ.get("CAMELOT_DB_PASSWORD") or "")
        ),
        "database": database_for(project, env),
        "charset": "utf8mb3",
    }


# --- talking to the server (never to one database only) ------------------------


def _name(row) -> str:
    """The one column of a `SHOW …` row. The driver is opened with a dict
    cursor (`serverrows.connect`), while a test's fake hands back tuples —
    both say the same thing."""
    if isinstance(row, dict):
        return str(next(iter(row.values())))
    return str(row[0])


def _rows(connect, connection: dict, statement: str) -> list[str]:
    """One query, on the connection it is given — `SHOW DATABASES` wants the
    server itself (no database selected), `SHOW TABLES` wants one."""
    handle = connect(connection)
    try:
        with handle.cursor() as cursor:
            cursor.execute(statement)
            return sorted(_name(row) for row in cursor.fetchall())
    finally:
        handle.close()


def _tables(connect, connection: dict) -> list[str]:
    return _rows(connect, connection, "SHOW TABLES")


def _databases(connect, connection: dict) -> list[str]:
    found = {key: value for key, value in connection.items() if key != "database"}
    found["database"] = None
    return _rows(connect, found, "SHOW DATABASES")


def _execute(connect, connection: dict, statements: list[str]) -> None:
    handle = connect(connection)
    try:
        with handle.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        handle.commit()
    except BaseException:
        handle.rollback()
        raise
    finally:
        handle.close()


# --- the baseline -------------------------------------------------------------


def baseline_dir(project: project_mod.Project, env: str, override=None) -> Path:
    """Where OpenDAoC's own database checkout is on this machine: `--baseline`,
    then `machine.json`'s `environments.<env>.baseline`."""
    if override:
        found = Path(str(override)).expanduser()
    else:
        named = _mapped(project, env).get("baseline")
        if not named:
            raise EnvError(
                f"{project.name}: no baseline named for {env!r} — pass --baseline <dir> "
                f'or add "baseline": "…" to {project_mod.MACHINE_FILE}'
            )
        found = Path(str(named)).expanduser()
    if not found.is_dir():
        raise EnvError(f"{found}: no such directory")
    return found


def baseline_files(where: Path) -> list[Path]:
    """The order the project's own Dockerfile uses: every `*.sql`, by name.
    `combined.sql` is skipped — it *is* the concatenation, and importing both
    would import everything twice."""
    found = sorted(path for path in where.glob("*.sql") if path.name != "combined.sql")
    if not found:
        raise EnvError(f"{where}: no *.sql to import (OpenDAoC's database checkout?)")
    return found


def baseline_identity(files: list[Path]) -> dict:
    """What identifies a baseline (§D8, the question M-10 asked): the digest of
    every file it is made of, in order, and how big it is."""
    digest = hashlib.sha256()
    total = 0
    for path in files:
        data = path.read_bytes()
        total += len(data)
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return {
        "source": str(files[0].parent) if files else "",
        "files": len(files),
        "bytes": total,
        "sha256": f"sha256:{digest.hexdigest()}",
    }


def statements_of(path: Path):
    """One SQL statement at a time, in the file's order.

    These files are `mysqldump` output: semicolons inside string literals are
    real, so the reader knows about quotes and escapes. Lines that are only a
    comment are dropped (`mysqldump` writes one above every table); the
    `/*!…*/` directives are kept — MariaDB reads them itself, and dropping them
    would change what the dump asks for.
    """
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if quote is None and (not stripped or stripped.startswith("--")):
            continue
        for char in line:
            if quote:
                current.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
                current.append(char)
                continue
            if char == ";":
                statement = "".join(current).strip()
                current = []
                if statement:
                    yield statement
                continue
            current.append(char)
        current.append("\n")
    tail = "".join(current).strip()
    if tail:
        yield tail


def import_baseline(connect, connection: dict, files: list[Path], *, on_file=None) -> int:
    """Import every file, in **one transaction**: a baseline is whole or it is
    not there, and the caller drops the database when this raises."""
    statements = 0
    handle = connect(connection)
    try:
        with handle.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            cursor.execute("SET NAMES utf8mb3")
            for path in files:
                for statement in statements_of(path):
                    cursor.execute(statement)
                    statements += 1
                if on_file is not None:
                    on_file(path)
        handle.commit()
    except BaseException:
        handle.rollback()
        raise
    finally:
        handle.close()
    return statements


# --- provisioning -------------------------------------------------------------


def provision(
    project: project_mod.Project,
    env: str,
    *,
    baseline=None,
    connect=None,
    yes: bool = False,
    on_file=None,
) -> dict:
    """Build the environment's own database from OpenDAoC's baseline.

    Refused unless *yes* is set — the verb drops a database — and refused by
    name when the database it would drop is not the one this project created for
    that environment, or is the one the authoring server edits.
    """
    if not yes:
        raise EnvError(
            "provisioning drops the environment's database and imports the baseline "
            "again: pass --yes when that is what you mean"
        )
    env_root = folder(project, env)
    connection = connection_for(project, env)
    connect = connect or serverrows.connect
    database = connection["database"]
    if not re.fullmatch(r"[A-Za-z0-9_]+", str(database)):
        raise EnvError(
            f"{database!r} is not a name this verb will create a database under "
            "(letters, digits and underscores)"
        )
    authoring = _authoring(project)[0]
    if authoring and authoring == database:
        raise EnvError(
            f"{database!r} is the database {project.name} reads its rows from — the "
            "environment needs its own: name one in "
            f"{project_mod.MACHINE_FILE} (environments.{env}.database)"
        )
    recorded = read_record(env_root)
    ours = str(recorded.get("database") or "") == str(database)
    if not ours and str(database) in _databases(connect, connection):
        raise EnvError(
            f"the database {database!r} already exists and this project did not create "
            "it — refusing to drop it: name another one in "
            f"{project_mod.MACHINE_FILE} (environments.{env}.database)"
        )
    files = baseline_files(baseline_dir(project, env, baseline))
    identity = baseline_identity(files)

    _execute(
        connect,
        {**connection, "database": None},
        [
            f"DROP DATABASE IF EXISTS `{database}`",
            f"CREATE DATABASE `{database}` CHARACTER SET utf8mb3 COLLATE utf8mb3_general_ci",
        ],
    )
    try:
        statements = import_baseline(connect, connection, files, on_file=on_file)
    except BaseException:
        _execute(
            connect, {**connection, "database": None}, [f"DROP DATABASE IF EXISTS `{database}`"]
        )
        raise
    tables = _tables(connect, connection)
    record = {
        "format": FORMAT,
        "database": database,
        "imported": journal_mod.now(),
        "baseline": identity,
        "tables": len(tables),
        "statements": statements,
        "server": {
            "host": connection["host"],
            "port": connection["port"],
            "user": connection["user"],
        },
        "created_by": project.name,
    }
    reset = installer.reset(env_root, project=project.name, baseline=identity)
    write_record(env_root, record)
    return {**record, "installer": reset}


# --- reading an environment back -----------------------------------------------


def describe(project: project_mod.Project, env: str) -> dict:
    """What the environment holds, without opening its database: the folder, the
    record, and the Installer's own state when it wrote one."""
    env_root = folder(project, env)
    recorded = read_record(env_root)
    state = installer.read_state(env_root)
    return {
        "env": str(env),
        "root": str(env_root),
        "exists": env_root.is_dir(),
        "database": recorded.get("database") or database_for(project, env),
        "record": recorded,
        "installer": {
            "folder": str(installer.folder(env_root)),
            "state": bool(state.get("releases")),
            "applied": installer.applied_releases(env_root),
            "releases": installer.handed_over(env_root),
        },
    }


def verify(project: project_mod.Project, env: str, *, connect=None) -> dict:
    """Read the environment back: the database answers and is the one the record
    names, its tables are the baseline's own count, and the tables the project
    owns are there. Nothing here writes."""
    env_root = folder(project, env)
    recorded = read_record(env_root)
    if not recorded:
        raise EnvError(
            f"{env_root}: no {RECORD} — provision it first (`camelot env provision {env} --yes`)"
        )
    connection = connection_for(project, env)
    connect = connect or serverrows.connect
    problems: list[str] = []
    if connection["database"] != str(recorded.get("database") or ""):
        problems.append(
            f"the database is named {connection['database']!r} now, the record says "
            f"{recorded.get('database')!r}"
        )
    tables: list[str] = []
    try:
        tables = _tables(connect, connection)
    except serverrows.mysql_errors() as error:
        problems.append(f"the database did not answer: {error}")
    expected = int(recorded.get("tables") or 0)
    if tables and expected and len(tables) != expected:
        problems.append(f"{len(tables)} table(s) in the database, the record says {expected}")
    problems += [
        f"{name}: the table the project writes is not in the database"
        for name, facts in sorted(targets.OWNED_TABLES.items())
        if facts.get("write") and tables and name not in tables
    ]
    state = installer.read_state(env_root, project.name)
    return {
        "env": str(env),
        "root": str(env_root),
        "database": connection["database"],
        "tables": len(tables),
        "baseline": recorded.get("baseline") or {},
        "installer": {
            "state": bool(state.get("releases")),
            "applied": sorted(str(key) for key in (state.get("releases") or {})),
        },
        "problems": problems,
        "ok": not problems,
    }


def apply(project: project_mod.Project, env: str, *, release=None, connect=None) -> dict:
    """Run the Installer's step for a release this environment holds (`§2`).

    It reads `<env>/server/camelot/releases/NNNN/` — what Deploy handed over,
    never the project — writes those rows into the environment's database, and
    records what it wrote. On a server the Installer does this itself (§D1); an
    environment this project provisioned is a test server, and the step can run
    from here.

    Without *release*, the newest one the environment holds that is not applied
    yet; an environment that holds nothing is refused by name, because deploying
    is what puts a release there. One whose releases are all applied gets the
    newest back as a no-op that says so (§D14).
    """
    env_root = folder(project, env)
    state = installer.read_state(env_root, project.name)
    handed = installer.handed_over(env_root)
    if release is None:
        if not handed:
            raise EnvError(
                f"{env_root}: no release is handed over to {env} — "
                f"`camelot project deploy N --env {env}` puts one there"
            )
        pending = [number for number in handed if not installer.released(state, number)]
        number = pending[-1] if pending else handed[-1]
    else:
        number = int(release)
        if number not in handed:
            raise EnvError(
                f"{env_root}: release {number:04d} is not there — "
                f"`camelot project deploy {number} --env {env}` hands it over"
            )
    try:
        needs: tuple = tuple(
            (release_mod.load(project.root, number).get("needs") or {}).get("reload") or ()
        )
    except release_mod.ReleaseError:
        needs = ()  # a release this machine does not hold: the ritual is not ours to name
    connection = connection_for(project, env)
    write = connect or serverrows.connect
    applied = installer.apply(
        env_root,
        installer.releases_folder(env_root, number),
        connect=lambda: write(connection),
        project=project.name,
        number=number,
        needs=needs,
    )
    fact = project_mod.record_deploy(
        project.root,
        {
            "kind": "apply",
            "env": str(env),
            "release": number,
            "outcome": "unchanged" if applied.get("unchanged") else "ok",
            "rows": {
                "written": int(applied.get("written") or 0),
                "skipped": int(applied.get("skipped") or 0),
            },
            "needs": [str(name) for name in needs],
        },
    )
    return {**applied, "env": str(env), "folder": str(env_root), "fact": fact}


def reload(project: project_mod.Project, env: str) -> dict:
    """Record the ritual a release named (`server-install.md` §D2).

    The reload itself is an in-game admin command (`/reload doors`) or the
    Console's business (P7); what the desktop can do is put the fact in the log,
    so "applied but not reloaded" is a state the Console can show rather than
    infer."""
    env_root = folder(project, env)
    state = installer.read_state(env_root, project.name)
    needs: list[str] = []
    newest = 0
    for name, entry in sorted((state.get("releases") or {}).items()):
        try:
            newest = max(newest, int(name))
        except ValueError:
            continue
    for entry in (state.get("releases") or {}).values():
        for name in entry.get("needs") or ():
            if str(name) not in needs:
                needs.append(str(name))
    fact = project_mod.record_deploy(
        project.root,
        {
            "kind": "reload",
            "env": str(env),
            "release": newest or None,
            "needs": needs,
        },
    )
    return {**fact, "folder": str(env_root)}
