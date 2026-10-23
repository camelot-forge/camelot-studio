"""This machine's own facts (`machine.json`): resolution, check, writing.

The first server walk lost an evening to a file that was *present and wrong*:
`db_config` opened a database the environment did not name, the daemon started,
and the viewer silently had no mobs. These tests keep the refusal in front of
that silence.
"""

import json
import socket

import pytest
from test_project import synthetic_base

from camelot import machine
from camelot import project as pj
from camelot.cli import main


@pytest.fixture()
def base(tmp_path):
    """The synthetic client, built locally so the name is not shadowed by a
    test's parameter (ruff reads imported fixtures as redefinitions)."""
    return synthetic_base(tmp_path)


def _project(tmp_path, client=None):
    return pj.new(tmp_path / "avalon", name="Avalon", client=client)


def _config(path, *, database="forge_project_local", port=3307):
    """`<root>/server/config/serverconfig.xml`, the shape `--db-config` names."""
    (path / "server" / "config").mkdir(parents=True, exist_ok=True)
    file = path / "server" / "config" / "serverconfig.xml"
    file.write_text(
        "<root><DBConnectionString>"
        f"Server=localhost;Port={port};Database={database};UserId=root;Password=secret;"
        "TreatTinyAsBoolean=false"
        "</DBConnectionString></root>",
        encoding="utf-8",
    )
    return file


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _machine(project, document) -> None:
    (project.root / pj.MACHINE_FILE).write_text(json.dumps(document), encoding="utf-8")


def _ready(project, client, tmp_path):
    """A machine that answers: a client, an environment, a matching config."""
    env_root = tmp_path / "env-local"
    env_root.mkdir()
    config = _config(env_root)
    _machine(
        project,
        {
            "author": "tester",
            "client": str(client),
            "environments": {
                "local": {
                    "root": str(env_root),
                    "database": "forge_project_local",
                    "db": {"host": "localhost", "port": 3307, "user": "root"},
                    "db_config": str(config),
                }
            },
        },
    )
    return env_root, config


# -- resolution ---------------------------------------------------------------


def test_the_file_wins_over_the_environment(tmp_path, base, monkeypatch):
    project = _project(tmp_path, client=base)
    monkeypatch.setenv("DAOC_CLIENT", str(tmp_path / "elsewhere"))

    assert machine.resolve_client(project.root) == base


def test_the_flag_wins_over_the_file(tmp_path, base):
    project = _project(tmp_path, client=base)
    other = tmp_path / "other-client"
    other.mkdir()

    assert machine.resolve_client(project.root, other) == other


def test_the_environment_is_the_last_resort(tmp_path, base, monkeypatch):
    project = pj.new(tmp_path / "avalon", name="Avalon")  # no client: no file
    monkeypatch.setenv("DAOC_CLIENT", str(base))

    assert machine.resolve_client(project.root) == base


def test_a_missing_answer_names_the_file_and_the_command(tmp_path, base, monkeypatch):
    project = pj.new(tmp_path / "avalon", name="Avalon")
    monkeypatch.delenv("DAOC_CLIENT", raising=False)

    with pytest.raises(machine.MachineError) as error:
        machine.resolve_client(project.root)

    assert "machine.json" in str(error.value)
    assert "project machine" in str(error.value)


def test_require_refuses_a_project_that_never_named_this_machine(tmp_path):
    project = pj.new(tmp_path / "avalon", name="Avalon")

    with pytest.raises(machine.MachineError):
        machine.require(project.root)


def test_dB_config_is_read_from_the_named_environment(tmp_path, base):
    project = _project(tmp_path, client=base)
    _env, config = _ready(project, base, tmp_path)

    assert machine.resolve_db_config(project.root, "local") == config
    assert machine.resolve_db_config(project.root, "production") is None


# -- the check ----------------------------------------------------------------


def test_a_machine_that_answers_has_no_hard_finding(tmp_path, base):
    project = _project(tmp_path, client=base)
    _env, _config_file = _ready(project, base, tmp_path)

    findings = machine.check(project.root, live=False)

    assert machine.failures(findings) == []
    states = {item["field"]: item["state"] for item in findings}
    assert states["client"] == machine.OK
    assert states["environments.local.db_config"] == machine.OK


def test_a_config_that_opens_another_database_is_refused(tmp_path, base):
    project = _project(tmp_path, client=base)
    env_root, _config_file = _ready(project, base, tmp_path)
    _config(env_root, database="opendaoc")

    findings = machine.check(project.root, live=False)

    mismatch = [item for item in findings if item["field"].endswith("#database")]
    assert mismatch and mismatch[0]["state"] == machine.INVALID
    assert "opendaoc" in mismatch[0]["detail"]
    assert "forge_project_local" in mismatch[0]["detail"]


def test_a_missing_file_is_one_actionable_line(tmp_path):
    project = pj.new(tmp_path / "avalon", name="Avalon")

    findings = machine.check(project.root, live=False)

    assert len(findings) == 1
    assert findings[0]["state"] == machine.MISSING
    assert "project machine" in findings[0]["detail"]


def test_a_database_that_does_not_answer_is_unreachable(tmp_path, base):
    project = _project(tmp_path, client=base)
    env_root, _config_file = _ready(project, base, tmp_path)
    _config(env_root, port=_closed_port())

    findings = machine.check(project.root, live=True)

    unreachable = [item for item in findings if item["state"] == machine.UNREACHABLE]
    assert unreachable and unreachable[0]["field"] == "environments.local.db"


def test_an_unknown_environment_is_refused(tmp_path, base):
    project = _project(tmp_path, client=base)
    _env, _config_file = _ready(project, base, tmp_path)
    document = json.loads((project.root / pj.MACHINE_FILE).read_text(encoding="utf-8"))
    document["environments"]["prod"] = {"root": str(tmp_path)}
    _machine(project, document)

    findings = machine.check(project.root, live=False)

    unknown = [item for item in findings if item["field"] == "environments.prod"]
    assert unknown and unknown[0]["state"] == machine.INVALID


# -- writing ------------------------------------------------------------------


def test_write_derives_the_environment_from_the_config(tmp_path, base):
    project = pj.new(tmp_path / "avalon", name="Avalon")
    env_root = tmp_path / "env-local"
    env_root.mkdir()
    config = _config(env_root)
    key = tmp_path / "camelot-release"
    key.write_text("private", encoding="utf-8")

    where, _data = machine.write(
        project.root, client=base, db_config=config, release_key=key
    )

    written = json.loads(where.read_text(encoding="utf-8"))
    local = written["environments"]["local"]
    assert written["client"] == str(base)
    assert written["release_key"] == str(key)
    assert local["root"] == str(env_root)
    assert local["database"] == "forge_project_local"
    assert local["db"] == {"host": "localhost", "port": 3307, "user": "root"}
    assert local["db_config"] == str(config)
    assert machine.failures(machine.check(project.root, live=False)) == []


def test_write_keeps_the_environments_it_does_not_touch(tmp_path, base):
    project = pj.new(tmp_path / "avalon", name="Avalon")
    _machine(
        project,
        {"environments": {"production": {"root": "/srv/forge/env", "client": False}}},
    )
    env_root = tmp_path / "env-local"
    env_root.mkdir()

    machine.write(project.root, db_config=_config(env_root))

    written = json.loads((project.root / pj.MACHINE_FILE).read_text(encoding="utf-8"))
    assert written["environments"]["production"] == {
        "root": "/srv/forge/env",
        "client": False,
    }
    assert "local" in written["environments"]


def test_write_refuses_a_client_that_is_not_there(tmp_path):
    project = pj.new(tmp_path / "avalon", name="Avalon")

    with pytest.raises(machine.MachineError):
        machine.write(project.root, client=tmp_path / "nope")


# -- the verbs ----------------------------------------------------------------


def test_project_machine_refuses_to_write_nothing(tmp_path, capsys):
    project = pj.new(tmp_path / "avalon", name="Avalon")

    assert main(["project", "machine", "--dir", str(project.root)]) == 1
    assert "nothing to write" in capsys.readouterr().err
    assert not (project.root / pj.MACHINE_FILE).exists()


def test_project_machine_writes_the_file_and_check_reads_it_back(tmp_path, base, capsys):
    project = pj.new(tmp_path / "avalon", name="Avalon")
    env_root = tmp_path / "env-local"
    env_root.mkdir()
    config = _config(env_root)

    assert main(
        [
            "project",
            "machine",
            "--dir",
            str(project.root),
            "--client",
            str(base),
            "--db-config",
            str(config),
        ]
    ) == 0
    assert (project.root / pj.MACHINE_FILE).is_file()

    assert main(["project", "check", "--dir", str(project.root), "--no-live"]) == 0
    assert "answers for local" in capsys.readouterr().out


def test_project_check_fails_on_a_config_that_opens_another_database(
    tmp_path, base, capsys
):
    project = _project(tmp_path, client=base)
    env_root, _config_file = _ready(project, base, tmp_path)
    _config(env_root, database="opendaoc")

    assert main(["project", "check", "--dir", str(project.root), "--no-live"]) == 1
    assert "opendaoc" in capsys.readouterr().out


def test_studio_refuses_before_starting_without_a_machine_file(tmp_path, capsys):
    project = pj.new(tmp_path / "avalon", name="Avalon")

    assert main(["studio", str(project.root), "--no-live"]) == 2
    captured = capsys.readouterr()
    assert "machine.json" in captured.err
    assert "not ready" in captured.err


def test_studio_without_arguments_opens_the_newest_recent_project(
    tmp_path, base, monkeypatch, capsys
):
    """One verb, no path: this folder, then the machine's list."""
    import uvicorn

    from camelot import paths, recent

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)
    project = pj.new(tmp_path / "avalon", name="Avalon", client=base)
    recent.record(project.root, name=project.name, directory=paths.config_dir())
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    monkeypatch.chdir(empty)

    assert main(["studio", "--no-live", "--no-open"]) == 0
    assert f"root={base}" in capsys.readouterr().out


def test_studio_without_a_project_opens_home(tmp_path, monkeypatch, capsys):
    """No folder, no project here, no recents: the front door, not an error."""
    import uvicorn

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(uvicorn, "run", lambda *arguments, **keywords: None)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    assert main(["studio", "--no-live", "--no-open"]) == 0
    captured = capsys.readouterr()
    assert "no project here" in captured.err
    assert "no client yet" in captured.out


def test_studio_edit_without_a_project_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)

    assert main(["studio", "--edit", "--no-open"]) == 2
    assert "needs a project" in capsys.readouterr().err
