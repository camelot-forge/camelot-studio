"""The environment's server half (`server-install.md`, P5).

Provisioning is a *database* operation, so the tests simulate the server: what
databases exist, which tables one of them holds, one row to read, and every
statement that ran. The Installer's step is the other half — it proves the
contract Deploy hands over (`§2`, `§D5`) and the criterion P5 is closed on: a
release lands twice, same result.

Nothing here opens MySQL: the real machine's server is never a test's fixture.
"""

import json
from pathlib import Path

import pytest
from test_pipeline import _environment, _project
from test_release import served_client  # noqa: F401  (what `client` wraps)
from test_serve_editor import editing  # noqa: F401  (the fixture `served_client` asks for)
from test_zones import world  # noqa: F401  (the fixture `editing` asks for)

from camelot import envs, installer
from camelot import pipeline as pipe
from camelot import project as pj
from camelot import release as rel
from camelot.serve.config import ServeConfig
from camelot.serve.editor import Editor
from camelot.serve.world import World


class FakeServer:
    """One MariaDB, as far as these verbs can tell: which databases exist, one
    database's tables, one row to read, and every statement that ran."""

    def __init__(
        self, *, databases=(), tables=("ability", "door", "mob", "path", "worldobject"), row=None
    ):
        self.databases = list(databases)
        self.tables = list(tables)
        self.row = row
        self.statements: list[str] = []
        self.transactions: list[str] = []

    def connect(self, connection=None, **kwargs):
        # `serverrows.connect` takes the whole dict positionally; a closure that
        # wants no argument at all is what `serverrows.apply_patches` asks for
        found = connection if isinstance(connection, dict) else kwargs
        return _Handle(self, found.get("database"))


class _Handle:
    def __init__(self, server: FakeServer, database):
        self.server = server
        self.database = database

    def cursor(self):
        return _Cursor(self)

    def begin(self):
        self.server.transactions.append("begin")

    def commit(self):
        self.server.transactions.append("commit")

    def rollback(self):
        self.server.transactions.append("rollback")

    def close(self):
        pass


class _Cursor:
    def __init__(self, handle: _Handle):
        self.handle = handle
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *_problem):
        return False

    def execute(self, sql, params=None):
        text = str(sql).strip()
        self.handle.server.statements.append(text)
        lowered = text.lower()
        if lowered.startswith("show databases"):
            self._rows = [(name,) for name in self.handle.server.databases]
        elif lowered.startswith("show tables"):
            # MariaDB's own answer when no database is selected: the fake says
            # the same, so a verb that forgets to select one fails here too
            if self.handle.database is None:
                raise RuntimeError("no database selected")
            self._rows = [(name,) for name in self.handle.server.tables]
        elif lowered.startswith("drop database if exists"):
            name = text.split("`")[1]
            if name in self.handle.server.databases:
                self.handle.server.databases.remove(name)
        elif lowered.startswith("create database"):
            self.handle.server.databases.append(text.split("`")[1])
        elif lowered.startswith("select "):
            self._rows = [self.handle.server.row] if self.handle.server.row else []
        elif lowered.startswith("update"):
            self._rows = []
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


@pytest.fixture()
def client(request):
    """`test_release`'s synthetic project client, under this module's own name."""
    return request.getfixturevalue("served_client")


@pytest.fixture(autouse=True)
def _private_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rel, "default_cache_dir", lambda: tmp_path / "cache")


def _baseline(tmp_path):
    """OpenDAoC's own checkout, as the two shapes that matter: a table first, a
    row after it, and a semicolon inside a string literal (`mysqldump` output
    quotes everything, so a naive split would cut a row in half)."""
    where = tmp_path / "opendaoc-db-core"
    where.mkdir()
    (where / "ability.sql").write_text(
        "/*!40101 SET NAMES utf8 */;\n"
        "CREATE TABLE IF NOT EXISTS `ability` (`AbilityID` int NOT NULL);\n",
        encoding="utf-8",
    )
    (where / "door.sql").write_text(
        "-- Dumping data for table `door`\n"
        "CREATE TABLE IF NOT EXISTS `door` (`Door_ID` varchar(255) NOT NULL);\n"
        "INSERT INTO `door` VALUES ('a;b'),('plain');\n",
        encoding="utf-8",
    )
    return where


def _db_config(tmp_path) -> Path:
    """A `serverconfig.xml` naming the *authoring* database: the connection the
    editor reads a row's originals from, and the one provisioning refuses to
    touch (§D6)."""
    path = tmp_path / "serverconfig.xml"
    path.write_text(
        "<root><Server><DBConnectionString>"
        "Server=127.0.0.1;Port=3307;Database=opendaoc;UserId=root;Password=secret;"
        "</DBConnectionString></Server></root>",
        encoding="utf-8",
    )
    return path


def _ready(tmp_path, client, *, database="avalon_local", row=None, extra=None, server=None):
    """A project with one environment whose server side this machine names."""
    project = _project(tmp_path, client)
    env_root = _environment(tmp_path, client, name="env-local")
    baseline = _baseline(tmp_path)
    entry = {
        "root": str(env_root),
        "database": database,
        "baseline": str(baseline),
        "db": {"host": "127.0.0.1", "port": 3307, "user": "root", "password": "secret"},
        "db_config": str(_db_config(tmp_path)),
    }
    entry.update(extra or {})
    # an *object* per environment: a string is the folder alone, and this
    # machine's other answers (database, baseline, connection) live beside it.
    # The rest of the file stays: it carries the client this machine resolved.
    document = pj.machine(project.root)
    document["environments"] = {"local": entry}
    (project.root / pj.MACHINE_FILE).write_text(
        json.dumps(document, indent=1) + "\n", encoding="utf-8"
    )
    return project, env_root, baseline, server or FakeServer(row=row)


def test_provision_refuses_without_the_word_that_says_drop_it(tmp_path, client):
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)

    with pytest.raises(envs.EnvError, match="pass --yes"):
        envs.provision(project, "local", connect=server.connect)

    assert server.databases == []  # nothing was dropped, nothing was created


def test_provision_refuses_the_database_the_authoring_server_edits(tmp_path, client):
    """The guard that matters on a machine that also runs a live server: the
    environment never gets `opendaoc` — that is somebody's content."""
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client, database="opendaoc")

    with pytest.raises(envs.EnvError, match="reads its rows from"):
        envs.provision(project, "local", yes=True, connect=server.connect)


def test_provision_refuses_a_database_this_project_did_not_create(tmp_path, client):
    project, _env_root, _baseline_dir, server = _ready(
        tmp_path, client, database="someone_elses", server=FakeServer(databases=["someone_elses"])
    )

    with pytest.raises(envs.EnvError, match="did not create"):
        envs.provision(project, "local", yes=True, connect=server.connect)


def test_provision_imports_the_baseline_and_records_what_it_was(tmp_path, client):
    """§D6/§D8: the database is dropped, created, imported in the project's own
    file order, and its identity recorded — what every later answer is read
    against."""
    project, env_root, _baseline_dir, server = _ready(tmp_path, client)

    record = envs.provision(project, "local", yes=True, connect=server.connect)

    assert record["format"] == envs.FORMAT
    assert record["database"] == "avalon_local"
    assert record["baseline"]["files"] == 2
    assert record["baseline"]["sha256"].startswith("sha256:")
    assert record["statements"] >= 4  # two CREATE TABLE, one INSERT, the SETs
    assert "avalon_local" in server.databases
    assert (env_root / "server" / "camelot" / "env.json").is_file()
    # the file order is the Dockerfile's: `cat *.sql`, by name
    created = [s for s in server.statements if s.lower().startswith("create table")]
    assert "ability" in created[0] and "door" in created[1]


def test_import_replays_a_dump_whole(tmp_path, client):
    """A semicolon inside a string literal is real: the row that carries one is
    one statement, not two (a naive split would hand MariaDB half a row)."""
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)

    _record = envs.provision(project, "local", yes=True, connect=server.connect)

    inserts = [s for s in server.statements if s.lower().startswith("insert into")]
    assert inserts and inserts[0].count(";") == 1
    assert "'a;b'" in inserts[0]


def test_a_database_this_machine_names_wins_over_the_record(tmp_path, client):
    """The refusal message for a foreign database says to name another one in
    `machine.json`; naming one has to actually move the environment, or the
    advice is a dead end. The record follows the machine, and `verify` names the
    two when they disagree."""
    project, env_root, _baseline_dir, server = _ready(tmp_path, client)
    envs.provision(project, "local", yes=True, connect=server.connect)
    _rename(project, "local", "avalon_other")

    record = envs.provision(project, "local", yes=True, connect=server.connect)

    assert record["database"] == "avalon_other"
    assert envs.database_for(project, "local") == "avalon_other"
    assert envs.read_record(env_root)["database"] == "avalon_other"
    assert "avalon_other" in server.databases


def test_provision_starts_the_installers_record_over(tmp_path, client):
    """§D6: the state and the applied releases describe the database that was just
    replaced, so they go with it. Because what Deploy handed over stays, the
    releases are applied again — against the fresh baseline — and because
    `--clean` says the baseline alone, they are not."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    envs.provision(project, "local", yes=True, connect=server.connect)
    _handed(env_root, 7, rows={"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]})
    envs.apply(project, "local", connect=server.connect)
    written = [s for s in server.statements if s.lower().startswith("update")]
    assert installer.read_state(env_root, project.name)["releases"]

    record = envs.provision(project, "local", yes=True, connect=server.connect)

    assert record["installer"]["dropped"] == ["0007"]
    assert record["installer"]["kept"] == ["0007"]
    assert [entry["release"] for entry in record["applied"]] == [7]
    assert len([s for s in server.statements if s.lower().startswith("update")]) == len(written) + 1
    state = installer.read_state(env_root, project.name)
    assert list(state["releases"]) == ["0007"]  # true again, against the new database
    assert state["baseline"] == record["baseline"]  # what the fresh database is

    clean = envs.provision(project, "local", yes=True, clean=True, connect=server.connect)

    assert clean["applied"] == []
    assert installer.read_state(env_root, project.name)["releases"] == {}
    assert installer.handed_over(env_root) == [7]


def test_provision_leaves_the_environment_at_the_content_it_holds(tmp_path, client):
    """§D6: what the operator gets from a provision is a test server that runs
    this project — the baseline, then every release the folder holds, oldest
    first."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    patch = {"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]}
    _handed(env_root, 6, rows=patch)
    _handed(env_root, 7, rows=patch)

    record = envs.provision(project, "local", yes=True, connect=server.connect)

    assert [entry["release"] for entry in record["applied"]] == [6, 7]
    assert record["applied"][0]["written"] == 1
    assert list(installer.read_state(env_root, project.name)["releases"]) == ["0006", "0007"]
    assert "avalon_local" in server.databases


def test_provision_clean_leaves_the_baseline_alone(tmp_path, client):
    """The other half of §D6's sentence: `--clean` is an environment that is the
    baseline and nothing else — the releases stay handed over, unapplied."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    _handed(env_root, 7, rows={"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]})

    record = envs.provision(project, "local", yes=True, clean=True, connect=server.connect)

    assert record["applied"] == []
    assert installer.read_state(env_root, project.name)["releases"] == {}
    assert installer.handed_over(env_root) == [7]


def test_verify_reads_the_environment_back(tmp_path, client):
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)
    envs.provision(project, "local", yes=True, connect=server.connect)

    report = envs.verify(project, "local", connect=server.connect)

    assert report["ok"] is True and report["problems"] == []
    assert report["database"] == "avalon_local"
    assert report["tables"] == 5
    assert report["installer"]["state"] is False


def test_verify_does_not_mind_tables_a_running_server_added(tmp_path, client):
    """The operator's core makes a table of its own at boot (`camelotmod`,
    measured 2026-10-18): more tables than the import made is a fact, not a
    fault. Fewer is a fault — that is what "the record says 110" is for."""
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)
    envs.provision(project, "local", yes=True, connect=server.connect)
    server.tables = [*server.tables, "camelotmod"]

    report = envs.verify(project, "local", connect=server.connect)

    assert report["ok"] is True and report["extra"] == 1 and report["tables"] == 6

    server.tables = ["ability", "door"]

    lost = envs.verify(project, "local", connect=server.connect)

    assert lost["ok"] is False
    assert any("tables went missing" in problem for problem in lost["problems"])


def test_verify_names_a_table_the_project_writes_that_is_gone(tmp_path, client):
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)
    envs.provision(project, "local", yes=True, connect=server.connect)
    server.tables = ["ability", "path"]  # the database lost the tables we write

    report = envs.verify(project, "local", connect=server.connect)

    assert report["ok"] is False
    assert any(
        "the table the project writes is not in the database" in problem
        for problem in report["problems"]
    )


def test_reload_records_the_ritual_a_release_named(tmp_path, client):
    """§D2: the ritual is performed in game and *recorded* here — so "applied
    but not reloaded" is a state the Console can show."""
    project, env_root, _baseline_dir, _server = _ready(tmp_path, client)
    installer.write_applied(env_root, 7, {"format": installer.APPLIED_FORMAT, "release": 7})
    installer.written_state(
        env_root,
        {
            **installer.empty_state(project.name),
            "releases": {"0007": {"at": "2026-10-17T10:00:00Z", "needs": ["doors", "mob"]}},
        },
    )

    fact = envs.reload(project, "local")

    assert fact["kind"] == "reload" and fact["env"] == "local"
    assert fact["release"] == 7 and fact["needs"] == ["doors", "mob"]
    assert [line["kind"] for line in pj.deploy_log(project.root)][-1] == "reload"


def _rename(project, env, database) -> None:
    """Name another database for the environment, the way the operator edits
    `machine.json`."""
    document = pj.machine(project.root)
    document["environments"][env]["database"] = database
    (project.root / pj.MACHINE_FILE).write_text(
        json.dumps(document, indent=1) + "\n", encoding="utf-8"
    )


def _handed(env_root, number, *, rows=None, name="world.mpk"):
    """A release folder as Deploy leaves it (`§D5`): the package, and the row
    patches beside it when the range patched rows."""
    folder = installer.releases_folder(env_root, number)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"the pack")
    if rows is not None:
        (folder / "rows.json").write_text(json.dumps({"patches": rows}), encoding="utf-8")
    return folder


def test_a_release_lands_and_is_recorded_row_by_row(tmp_path, client):
    """§2/§D5: the Installer reads the folder, skips what the environment already
    holds, writes what it does not, and leaves both documents behind."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    folder = _handed(
        env_root,
        7,
        rows={
            "0": [
                {"table": "door", "key": "d1", "values": {"X": "30000"}},
                {"table": "door", "key": "d2", "values": {"Y": "32768"}},
            ]
        },
    )
    server.row = {"Door_ID": "d1", "X": "32768", "Y": "32768"}

    applied = installer.apply(
        env_root, folder, connect=server.connect, project=project.name, number=7, needs=["doors"]
    )

    assert applied["release"] == 7
    assert applied["written"] + applied["skipped"] == 2
    state = installer.read_state(env_root, project.name)
    assert installer.released(state, 7)["needs"] == ["doors"]
    assert (env_root / "server" / "camelot" / "applied" / "0007" / "applied.json").is_file()


def test_a_release_lands_twice_the_same(tmp_path, client):
    """P5's exit criterion: the second apply is a no-op that says so, and the
    state is not touched again (§D14)."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    folder = _handed(
        env_root, 7, rows={"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]}
    )
    first = installer.apply(
        env_root, folder, connect=server.connect, project=project.name, number=7
    )
    wrote = [s for s in server.statements if s.lower().startswith("update")]

    second = installer.apply(
        env_root, folder, connect=server.connect, project=project.name, number=7
    )

    assert first["written"] == 1
    assert second["unchanged"] is True and second["written"] == 0
    assert [s for s in server.statements if s.lower().startswith("update")] == wrote


def test_a_release_that_names_a_table_we_do_not_own_is_refused_by_name(tmp_path, client):
    """§D4: refused before anything is written, and nothing is recorded."""
    project, env_root, _baseline_dir, server = _ready(tmp_path, client)
    folder = _handed(
        env_root, 7, rows={"0": [{"table": "character", "key": "1", "values": {"Name": "Bob"}}]}
    )

    with pytest.raises(installer.InstallerError, match="character"):
        installer.apply(env_root, folder, connect=server.connect, project=project.name, number=7)

    assert not (env_root / "server" / "camelot" / "state.json").exists()
    assert installer.read_applied(env_root, 7) == {}


def test_a_release_whose_row_is_gone_writes_nothing(tmp_path, client):
    """§D11: all or nothing — a row that is not there refuses the release, and
    no state is touched."""
    project, env_root, _baseline_dir, server = _ready(tmp_path, client, row=None)
    folder = _handed(
        env_root, 7, rows={"0": [{"table": "door", "key": "d9", "values": {"X": "1"}}]}
    )

    with pytest.raises(installer.InstallerError, match="no row with Door_ID"):
        installer.apply(env_root, folder, connect=server.connect, project=project.name, number=7)

    assert installer.read_state(env_root, project.name)["releases"] == {}


def test_apply_refuses_an_environment_that_holds_nothing(tmp_path, client):
    """The verb reads what Deploy handed over; an environment that holds no
    release is refused by name, with the command that would put one there."""
    project, _env_root, _baseline_dir, server = _ready(tmp_path, client)

    with pytest.raises(envs.EnvError, match="no release is handed over"):
        envs.apply(project, "local", connect=server.connect)

    assert pj.deploy_log(project.root) == []


def test_apply_takes_every_release_the_environment_holds_in_order(tmp_path, client):
    """A release carries the rows of its own range's zones, so a later one does
    not cover an earlier one's zones: what reconstructs the content is the whole
    folder, oldest first (§D6)."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    patch = {"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]}
    _handed(env_root, 6, rows=patch)
    _handed(env_root, 7, rows=patch)

    applied = envs.apply(project, "local", connect=server.connect)

    assert [entry["release"] for entry in applied["applied"]] == [6, 7]
    assert list(installer.read_state(env_root, project.name)["releases"]) == ["0006", "0007"]
    facts = [line for line in pj.deploy_log(project.root) if line["kind"] == "apply"]
    assert [(line["release"], line["outcome"]) for line in facts] == [(6, "ok"), (7, "ok")]

    done = envs.apply(project, "local", connect=server.connect)

    # nothing is pending: the newest answers for the environment, as a no-op
    assert done["release"] == 7 and done["unchanged"] is True


def test_a_row_says_which_release_will_be_applied(tmp_path, client):
    """`--release N` is exact: it applies that one, applied or not."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768"}
    )
    patch = {"0": [{"table": "door", "key": "d1", "values": {"X": "30000"}}]}
    _handed(env_root, 6, rows=patch)
    _handed(env_root, 7, rows=patch)

    applied = envs.apply(project, "local", release=6, connect=server.connect)

    assert [entry["release"] for entry in applied["applied"]] == [6]
    assert list(installer.read_state(env_root, project.name)["releases"]) == ["0006"]


def test_apply_names_a_release_that_was_not_handed_over(tmp_path, client):
    project, env_root, _baseline_dir, server = _ready(tmp_path, client)
    _handed(env_root, 7, rows={"0": [{"table": "door", "key": "d1", "values": {"X": "1"}}]})

    with pytest.raises(envs.EnvError, match="release 0009 is not there"):
        envs.apply(project, "local", release=9, connect=server.connect)


def test_a_release_lands_on_a_fresh_local_server_twice(tmp_path, client):
    """P5's exit criterion, the whole rung: a fresh baseline, one row edit, the
    cut, the deploy, and the Installer's step — then the same step again, which
    writes nothing (`vision.md` §6, P5)."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path,
        client,
        row={"Door_ID": "d1", "X": "39660", "Y": "39660", "Z": "10", "Heading": "1024"},
    )
    envs.provision(project, "local", yes=True, connect=server.connect)
    editor = Editor(
        ServeConfig(
            root=client,
            project=project.root,
            cache_dir=project.root / "cache",
            db_config=_db_config(tmp_path),
        ),
        connect_=server.connect,
    )
    editor.record(
        World(editor.config).zone(0),
        {
            "kind": "move",
            "id": 1,
            "x": 20800,
            "y": 20800,
            "rows": [{"table": "door", "key": "d1"}],
        },
    )
    record = rel.cut(project.root)

    report = pipe.deploy(project.root, record["number"], env="local")
    assert (installer.releases_folder(env_root, 1) / "rows.json").is_file()
    first = envs.apply(project, "local", connect=server.connect)
    wrote = [s for s in server.statements if s.lower().startswith("update")]
    second = envs.apply(project, "local", connect=server.connect)

    assert report["outcome"] == "ok"
    assert first["release"] == 1 and first["written"] == 1
    assert len(wrote) == 1 and "`X` = %s" in wrote[0] and "`Y` = %s" in wrote[0]
    assert second["unchanged"] is True
    assert [s for s in server.statements if s.lower().startswith("update")] == wrote
    state = installer.read_state(env_root, project.name)
    assert list(state["releases"]) == ["0001"]
    assert installer.read_applied(env_root, 1)["written"] == 1
    assert envs.verify(project, "local", connect=server.connect)["installer"]["state"] is True


def test_a_release_that_removes_a_row_keeps_the_whole_row(tmp_path, client):
    """`row-remove` is an op like any other (§D14): the row goes, and the whole
    row is recorded as its original, because putting the release back is putting
    the row back."""
    project, env_root, _baseline_dir, server = _ready(
        tmp_path, client, row={"Door_ID": "d1", "X": "32768", "Y": "32768", "Heading": "1024"}
    )
    _handed(
        env_root,
        7,
        rows={"0": [{"table": "door", "key": "d1", "values": {}, "deleted": True}]},
    )

    applied = installer.apply(
        env_root,
        installer.releases_folder(env_root, 7),
        connect=server.connect,
        project=project.name,
        number=7,
    )

    removed = [s for s in server.statements if s.lower().startswith("delete from")]
    assert len(removed) == 1 and "`door`" in removed[0]
    assert applied["removed"] == 1 and applied["written"] == 0
    state = installer.read_state(env_root, project.name)
    assert state["releases"]["0007"]["originals"]["door"]["d1"] == {
        "Door_ID": "d1",
        "Heading": "1024",
        "X": "32768",
        "Y": "32768",
    }
    assert installer.read_applied(env_root, 7)["rows"][0]["action"] == "remove"


def test_a_release_that_removes_a_row_that_is_gone_is_a_no_op(tmp_path, client):
    """The row is already gone: nothing to remove, nothing to say but that."""
    project, env_root, _baseline_dir, server = _ready(tmp_path, client, row=None)
    _handed(
        env_root,
        7,
        rows={"0": [{"table": "door", "key": "d9", "values": {}, "deleted": True}]},
    )

    applied = installer.apply(
        env_root,
        installer.releases_folder(env_root, 7),
        connect=server.connect,
        project=project.name,
        number=7,
    )

    assert applied["removed"] == 0 and applied["skipped"] == 1
    assert [s for s in server.statements if s.lower().startswith("delete")] == []
    assert installer.read_applied(env_root, 7)["rows"][0]["why"] == "the row is not there any more"


def test_the_id_space_check_names_the_row_that_holds_the_id(tmp_path):
    """§D9, as the data it is: the release's own allocation against what the
    server answers. Grown but free is not a collision."""
    allocated = {"0": {"2026-10-17T09-00-00-michael.jsonl:1": 1234}}
    held = {"mob": {1234: "Patrol_Boar"}}

    collisions = installer.id_collisions(allocated, held)

    assert collisions == [
        "id 1234 (zone 0, 2026-10-17T09-00-00-michael.jsonl:1) is already held by mob Patrol_Boar"
    ]
    assert installer.id_collisions(allocated, {"mob": {9999: "somebody_else"}}) == []
