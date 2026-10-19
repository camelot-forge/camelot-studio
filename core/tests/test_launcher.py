"""The launcher — the players' whole surface (`C §D14`, P6).

It is small on purpose: read a feed, bring a client up to it, start the game. What it
must never do is what these tests are mostly about — it carries no project, no release,
no database (`test_players_half_is_not_the_admins` owns that line), it remembers the
player's own things beside the player's other settings, and it never logs a password.

The client is the synthetic one; the feed is a real one, built by `feedbuild` and read
back the way a player's machine reads it.
"""

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_feed import _md5, _published, _spoil, client  # noqa: F401
from test_release import served_client  # noqa: F401  (the fixture `client` asks for)
from test_serve_editor import editing  # noqa: F401  (the fixture `served_client` asks for)
from test_zones import world  # noqa: F401  (the fixture `editing` asks for)

from camelot import feed, feedbuild, launcher


@pytest.fixture(autouse=True)
def _its_own_settings(tmp_path, monkeypatch):
    """A launcher writes to the player's settings, never to a test's home."""
    real = launcher.settings_path
    monkeypatch.setattr(
        launcher,
        "settings_path",
        lambda explicit=None: tmp_path / "settings.json" if not explicit else real(explicit),
    )


def _plant(path, data: bytes) -> None:
    """A file the player's copy *has*: unlink first, because these copies share inodes
    with the base client (the staging hardlinks its files)."""
    path = Path(path)
    path.unlink(missing_ok=True)
    path.write_bytes(data)


def _ready(tmp_path, client, *, server="play.example:10300", feed_url=None):  # noqa: F811  (the fixture the import names)
    """A built feed, a player's copy of the client, and the launcher's settings.

    The feed's base URL is its own folder by default: these tests must never reach the
    network, and a folder is what the protocol allows anyway.
    """
    project = _published(tmp_path, client)
    where = project.root / feed.FEED
    base_url = feed_url or f"{where}/"
    report = feedbuild.build(project, base=client, base_url=base_url, server=server)
    player = tmp_path / "player"
    feedbuild._link_tree(client, player)
    _plant(player / "connect.exe", b"MZ fake client")
    settings = dict(launcher.DEFAULTS)
    settings.update(
        {
            "feed": base_url,
            "client": str(player),
            "account": "forge",
            # The tests must not depend on the host's Wine: the CI runner has none.
            "wine": "/opt/wine-staging/bin/wine",
        }
    )
    return settings, player, where, report


# --- the boundary --------------------------------------------------------------


def test_the_players_half_is_not_the_admins():
    """`C §D16`: the two modules a player's machine runs import the standard library and
    nothing else of ours. Run in a subprocess — what a module imports at runtime is the
    only honest answer, and an import added later must break this test rather than a
    player's machine."""
    code = (
        "import json, sys; import camelot.feed, camelot.launcher;"
        "print(json.dumps(sorted(m for m in sys.modules if m == 'camelot' or"
        " m.startswith('camelot.'))))"
    )
    found = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    imported = set(json.loads(found.stdout))

    ours = {"camelot", "camelot.feed", "camelot.launcher"}
    assert imported == ours, (
        f"the players' half now imports {sorted(imported - ours)} — nothing of the "
        "admin's may travel to a player (C §D16)"
    )


# --- what it remembers ---------------------------------------------------------


def test_settings_are_the_players_own_and_keep_what_was_installed(tmp_path):
    """A missing file is a fresh install, not an error; `installed` survives a rewrite
    because it is what the page compares against the feed. The file's own path rides
    along, so a page can say *which* launcher it is."""
    fresh = launcher.read_settings()

    assert {key: value for key, value in fresh.items() if key != "path"} == launcher.DEFAULTS
    assert fresh["path"].endswith("settings.json")

    written = launcher.write_settings({**launcher.DEFAULTS, "feed": "https://x/", "installed": 3})

    assert written == launcher.settings_path()
    read_back = launcher.read_settings()
    assert read_back["feed"] == "https://x/" and read_back["installed"] == 3
    assert launcher.settings_path("/tmp/elsewhere.json").name == "elsewhere.json"


def test_a_settings_file_keeps_its_own_path_across_writes(tmp_path):
    """`--settings` is the player's file, wherever they put it: a write must land
    beside it, not in the default config folder."""
    custom = tmp_path / "elsewhere" / "settings.json"
    document = launcher.read_settings(custom)
    document.update({"feed": "https://x/", "installed": 4})

    launcher.write_settings(document)

    assert custom.is_file()
    assert launcher.read_settings(custom)["installed"] == 4


def test_the_shard_file_is_what_the_player_does_not_type(tmp_path):
    """F §D1: the distributed file names the feed and the shard. A player never
    sees the URL field; the file is beside the launcher."""
    path = tmp_path / "shard.json"
    path.write_text(
        json.dumps(
            {
                "name": "Avalon",
                "feed": "https://patch.example",
                "launcher": "https://patch.example/launcher",
                "server": "fallback:1",
            }
        ),
        encoding="utf-8",
    )

    shard = launcher.read_shard(path)

    assert shard["name"] == "Avalon"
    assert shard["feed"] == "https://patch.example/"
    assert shard["launcher"] == "https://patch.example/launcher/"
    assert shard["server"] == "fallback:1"

    settings = launcher.configure(dict(launcher.DEFAULTS, feed="https://stale/"), shard)
    assert settings["feed"] == "https://patch.example/"
    assert settings["launcher_url"] == "https://patch.example/launcher/"
    assert settings["shard_name"] == "Avalon"
    assert settings["shard_server"] == "fallback:1"


def test_the_environment_and_the_command_line_override_the_shard(tmp_path):
    """F §D2: a development run points at another feed without editing the
    distributed file."""
    shard = {"name": "Avalon", "feed": "https://patch.example/", "server": "fallback:1"}

    from_env = launcher.configure(
        dict(launcher.DEFAULTS),
        shard,
        environ={
            "CAMELOT_FEED_URL": "http://env.example/",
            "CAMELOT_GAME_SERVER": "env:2",
        },
    )
    assert from_env["feed"] == "http://env.example/"
    assert from_env["server_override"] == "env:2"

    from_cli = launcher.configure(
        from_env,
        shard,
        feed="http://cli.example",
        server="cli:3",
        environ={
            "CAMELOT_FEED_URL": "http://env.example/",
            "CAMELOT_GAME_SERVER": "env:2",
        },
    )
    assert from_cli["feed"] == "http://cli.example/"
    assert from_cli["server_override"] == "cli:3"


def test_a_broken_shard_file_is_an_error_not_a_default(tmp_path):
    path = tmp_path / "shard.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(launcher.LauncherError, match="shard.json"):
        launcher.read_shard(path)


def test_a_package_finds_its_page_beside_the_executable(tmp_path, monkeypatch):
    """The player build has no checkout: the page rides beside the binary (or in
    PyInstaller's `_MEIPASS`)."""
    page = tmp_path / "page"
    page.mkdir()
    (page / "index.html").write_text("<!doctype html>", encoding="utf-8")
    executable = tmp_path / "camelot-launcher"
    executable.write_text("", encoding="utf-8")
    monkeypatch.delattr(launcher.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(launcher.sys, "argv", [str(executable)])

    assert launcher.packaged_page() == str(page)


def test_the_shard_file_is_looked_up_beside_the_executable(tmp_path, monkeypatch):
    executable = tmp_path / "camelot-launcher"
    executable.write_text("", encoding="utf-8")
    monkeypatch.delenv("CAMELOT_SHARD", raising=False)
    monkeypatch.setattr(launcher.sys, "argv", [str(executable)])

    assert launcher.shard_config_path() == tmp_path / "shard.json"


# --- what it can say without walking a client ----------------------------------


def test_state_says_what_is_missing_before_anything_else(tmp_path):
    """A page with no client and no feed says so, rather than showing a spinner."""
    bare = launcher.state(dict(launcher.DEFAULTS))

    assert bare["shard"] == {}
    assert any("no client folder" in problem for problem in bare["problems"])
    assert any("no feed URL" in problem for problem in bare["problems"])


def test_state_reads_the_shard_from_the_feed(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """The version, the name and where the game is come from the feed — a player typed
    one URL and nothing else (`C §D15`)."""
    settings, player, _where, report = _ready(tmp_path, client)

    found = launcher.state(settings)

    assert found["shard"]["shard"] == "Avalon"
    assert found["shard"]["server"] == "play.example:10300"
    assert found["shard"]["version"] == report["version"] == 1
    assert found["up_to_date"] is False
    assert found["client_there"] is True and found["problems"] == []

    settings.update({"feed": f"{report['base_url']}nowhere/"})
    without = launcher.state(settings)

    assert without["shard"] == {} and without["problems"][0].endswith("that is not a feed")
    # and the folder the player pointed at is named in the state
    assert found["client"] == str(player)


def test_a_feed_that_does_not_say_where_the_game_is_is_a_problem(tmp_path, client):  # noqa: F811  (the fixture the import names)
    settings, _player, _where, _report = _ready(tmp_path, client, server="")

    found = launcher.state(settings)

    assert any("does not say where the game is" in problem for problem in found["problems"])
    with pytest.raises(launcher.LauncherError, match="no game address"):
        launcher.command(settings, account="forge", password="pw", system="linux")


def test_the_shard_name_and_fallback_server_show_before_the_feed(tmp_path):
    settings = dict(launcher.DEFAULTS, shard_name="Avalon", shard_server="fallback:1")

    found = launcher.state(settings)

    assert found["shard"] == {"shard": "Avalon", "server": "fallback:1"}
    assert any("no feed URL" in problem for problem in found["problems"])


def test_the_command_uses_the_shard_fallback_when_the_feed_has_no_address(tmp_path, client):  # noqa: F811  (the fixture the import names)
    settings, _player, _where, _report = _ready(tmp_path, client, server="")
    settings["shard_server"] = "fallback:1"

    argv = launcher.command(settings, account="forge", password="pw", system="linux")

    assert argv[-3] == "fallback:1"


def test_a_local_server_override_is_what_the_page_and_the_command_use(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """`--server` is for a machine pointing at another address than the feed's: the
    page must show the address Play will use, not the one it overrides."""
    settings, _player, _where, _report = _ready(tmp_path, client)
    settings["server_override"] = "127.0.0.1:10300"

    found = launcher.state(settings)

    assert found["shard"]["server"] == "127.0.0.1:10300"
    argv = launcher.command(settings, account="forge", password="pw", system="linux")
    assert argv[-3] == "127.0.0.1:10300"


# --- what it does --------------------------------------------------------------


def test_update_brings_the_client_up_and_remembers_the_version(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """The whole update is `feed.update`; what the launcher adds is the version it wrote
    down, which is what the page shows next time."""
    settings, player, where, report = _ready(tmp_path, client)
    for name in report["changed"]:
        _spoil(player / name, b"stale")

    seen: list[str] = []
    done = launcher.update(settings, on_progress=seen.append)

    assert done["version"] == report["version"]
    for name in report["changed"]:
        assert _md5(player / name) == _md5(where / name)
    assert seen == report["changed"]  # one call per file, in order
    assert launcher.read_settings()["installed"] == report["version"]
    assert launcher.state({**settings, "installed": report["version"]})["up_to_date"] is True


def test_the_command_is_what_the_operators_scripts_call(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """`connect.exe game1127.dll <address> <account> <password>` — never `camelot.exe`
    (the retail patcher would overwrite everything). Under Wine on Linux, directly on
    Windows."""
    settings, player, _where, _report = _ready(tmp_path, client)
    settings["wine"] = "/opt/wine-staging/bin/wine"

    linux = launcher.command(settings, account="forge", password="pw", system="linux")
    windows = launcher.command(settings, account="forge", password="pw", system="win32")

    assert linux == [
        "/opt/wine-staging/bin/wine",
        "connect.exe",
        "game1127.dll",
        "play.example:10300",
        "forge",
        "pw",
    ]
    assert windows == ["connect.exe", "game1127.dll", "play.example:10300", "forge", "pw"]

    with pytest.raises(launcher.LauncherError, match="password"):
        launcher.command(settings, account="forge", password="", system="linux")
    (player / "connect.exe").unlink()
    with pytest.raises(launcher.LauncherError, match="not a DAoC client"):
        launcher.command(settings, account="forge", password="pw", system="linux")


def test_play_starts_the_client_from_the_clients_own_folder(tmp_path, client, monkeypatch):  # noqa: F811  (the fixture the import names)
    """The game runs *in* the client's folder (its data files are relative to it), and a
    Linux run carries the player's Wine prefix."""
    settings, player, _where, report = _ready(tmp_path, client)
    settings["installed"] = report["version"]
    settings["wineprefix"] = "/tmp/prefix"
    seen: dict = {}

    def fake_popen(argv, **kwargs):
        seen.update({"argv": argv, **kwargs})
        return type("Handle", (), {"pid": 4242})()

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    handle = launcher.play(settings, account="forge", password="pw", system="linux")

    assert handle.pid == 4242
    assert seen["cwd"] == str(player) and seen["shell"] is False
    assert seen["env"]["WINEPREFIX"] == "/tmp/prefix"
    assert seen["argv"][-2:] == ["forge", "pw"]


def test_play_refuses_while_the_client_is_behind_the_feed(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """`C §D17`: a player must not enter the shard with content it no longer publishes.
    The page disables Play; the process is the gate behind it."""
    settings, _player, _where, report = _ready(tmp_path, client)

    with pytest.raises(launcher.LauncherError, match="update is waiting"):
        launcher.play(settings, account="forge", password="pw", system="linux")

    settings["installed"] = report["version"] - 1 if report["version"] > 1 else 0
    with pytest.raises(launcher.LauncherError, match="update is waiting"):
        launcher.play(settings, account="forge", password="pw", system="linux")


# --- the page's own routes -----------------------------------------------------


def _page(tmp_path):
    where = tmp_path / "page"
    where.mkdir(exist_ok=True)
    (where / "index.html").write_text("<!doctype html><title>launcher</title>", encoding="utf-8")
    return where


def _call(server, path, *, body=None, method="GET"):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=10) as answer:
        return json.loads(answer.read().decode("utf-8"))


def test_the_routes_say_the_state_save_the_settings_and_refuse_what_they_should(tmp_path, client):  # noqa: F811  (the fixture the import names)
    """A browser can neither write in the client's folder nor start the game — these
    three routes are the whole reason the process exists."""
    settings, _player, _where, report = _ready(tmp_path, client)
    server = launcher.server_for(settings, _page(tmp_path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        state = _call(server, "/api/state")["state"]
        assert state["shard"]["server"] == "play.example:10300"
        assert state["shard"]["version"] == report["version"]

        saved = _call(
            server,
            "/api/settings",
            method="POST",
            body={"account": "michael", "remember": True, "feed": settings["feed"]},
        )["state"]
        assert saved["account"] == "michael"
        assert launcher.read_settings()["account"] == "michael"

        forgotten = _call(server, "/api/settings", method="POST", body={"remember": False})["state"]
        assert forgotten["account"] == "" and launcher.read_settings()["account"] == ""

        with pytest.raises(urllib.error.HTTPError) as refused:
            _call(server, "/api/play", method="POST", body={"password": ""})
        assert refused.value.code == 400
        assert "account" in json.loads(refused.value.read().decode("utf-8"))["error"]

        with pytest.raises(urllib.error.HTTPError) as stale:
            _call(
                server,
                "/api/play",
                method="POST",
                body={"account": "forge", "password": "pw"},
            )
        assert stale.value.code == 400
        assert "update is waiting" in json.loads(stale.value.read().decode("utf-8"))["error"]

        with pytest.raises(urllib.error.HTTPError) as missing:
            _call(server, "/api/nothing")
        assert missing.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as no_package:
            _call(server, "/api/launcher-update", method="POST", body={})
        assert no_package.value.code == 400
        assert "source checkout" in json.loads(no_package.value.read().decode("utf-8"))["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_the_update_route_refuses_a_second_run_at_the_same_time(tmp_path, client, monkeypatch):  # noqa: F811  (the fixture the import names)
    """One update at a time: two walks of the same client would fight over the same
    files. The route answers 409 while one is running."""
    settings, _player, _where, _report = _ready(tmp_path, client)
    server = launcher.server_for(settings, _page(tmp_path), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    started = threading.Event()

    def slow(settings):  # what a real update does, with the door held open
        started.set()
        started.wait(5)

    monkeypatch.setattr(launcher, "_update_in_background", slow)
    try:
        first = _call(server, "/api/update", method="POST", body={})
        assert first["progress"]["running"] is True
        assert started.wait(5)
        # the page reads the polling route through the same `progress` key
        assert _call(server, "/api/progress")["progress"]["running"] is True
        with pytest.raises(urllib.error.HTTPError) as busy:
            _call(server, "/api/update", method="POST", body={})
        assert busy.value.code == 409
    finally:
        launcher.PROGRESS["running"] = False
        server.shutdown()
        server.server_close()


# --- its own update (F §D6-§D8) ------------------------------------------------


def _package(directory, *, version: int = 2, digest: str | None = None, binary=True) -> Path:
    """One shard's launcher URL: the zip and the manifest that names it."""
    import hashlib
    import zipfile

    where = Path(directory)
    where.mkdir(parents=True, exist_ok=True)
    archive = where / "camelot-launcher-win32.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("camelot-launcher/version.txt", f"{version}\n")
        if binary:
            bundle.writestr("camelot-launcher/camelot-launcher", b"")
    found = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    (where / "launcher-manifest.json").write_text(
        json.dumps(
            {"version": version, "file": archive.name, "sha256": digest or found}
        ),
        encoding="utf-8",
    )
    return where


def test_the_launcher_reads_its_own_version_beside_the_binary(tmp_path):
    """`version.txt` is what the package carries and what its own update reads; a
    source checkout has none, and any published package beats it."""
    (tmp_path / "version.txt").write_text("7\n", encoding="utf-8")

    assert launcher.installed_version(tmp_path) == 7
    assert launcher.installed_version(tmp_path / "not-there") == 0


def test_an_update_is_taken_only_when_the_remote_version_is_greater(tmp_path):
    """The rule the client's protocol already uses (`C §3`): a re-published
    package with the same version is invisible to a player who has it."""
    base = _package(tmp_path / "shard", version=3)

    found = launcher.update_of(str(base), current=2)
    assert found["version"] == 3 and found["file"].endswith(".zip")
    assert launcher.update_of(str(base), current=3) is None
    assert launcher.update_of("", current=0) is None


def test_a_manifest_that_names_a_path_instead_of_a_file_is_refused(tmp_path):
    base = _package(tmp_path / "shard")
    (base / launcher.MANIFEST_FILE).write_text(
        json.dumps({"version": 9, "file": "../evil.zip", "sha256": "sha256:" + "0" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(launcher.LauncherError, match="usable file"):
        launcher.update_of(str(base), current=1)


def test_staging_checks_the_hash_and_unpacks_beside_the_install(tmp_path):
    base = _package(tmp_path / "shard", version=4)
    manifest = launcher.update_of(str(base), current=1)

    staged = launcher.stage_update(tmp_path / "install", manifest)

    assert Path(staged["root"]).name == "camelot-launcher"
    assert (Path(staged["root"]) / "version.txt").read_text(encoding="utf-8") == "4\n"
    assert not (tmp_path / "install" / "camelot-launcher").exists()


def test_a_download_that_does_not_hash_is_refused_before_anything_is_staged(tmp_path):
    base = _package(tmp_path / "shard", version=4, digest="sha256:" + "0" * 64)
    manifest = launcher.update_of(str(base), current=1)

    with pytest.raises(launcher.LauncherError, match="does not hash"):
        launcher.stage_update(tmp_path / "install", manifest)

    assert not (tmp_path / "install" / "update").exists()


def test_staging_refuses_a_package_without_one_top_level_folder(tmp_path):
    """The swap moves one folder into the install's place; a loose file or two
    roots would leave the install half-replaced."""
    import hashlib
    import zipfile

    base = tmp_path / "shard"
    base.mkdir()
    archive = base / "camelot-launcher-win32.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("version.txt", "5\n")
    (base / launcher.MANIFEST_FILE).write_text(
        json.dumps(
            {
                "version": 5,
                "file": archive.name,
                "sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    manifest = launcher.update_of(str(base), current=1)

    with pytest.raises(launcher.LauncherError, match="one top-level folder"):
        launcher.stage_update(tmp_path / "install", manifest)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bit")
def test_staging_makes_the_launcher_binary_executable(tmp_path):
    """A zip does not carry the executable bit through `extractall`; a staged
    binary that cannot run would be swapped in and never answer."""
    import hashlib
    import zipfile

    base = tmp_path / "shard"
    base.mkdir()
    archive = base / "camelot-launcher-linux.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        info = zipfile.ZipInfo("camelot-launcher/camelot-launcher")
        info.external_attr = 0o644 << 16
        bundle.writestr(info, b"not a real executable")
    (base / launcher.MANIFEST_FILE).write_text(
        json.dumps(
            {
                "version": 5,
                "file": archive.name,
                "sha256": "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    manifest = launcher.update_of(str(base), current=1)

    staged = launcher.stage_update(tmp_path / "install", manifest)

    binary = Path(staged["root"]) / "camelot-launcher"
    assert binary.stat().st_mode & 0o111


def test_launcher_state_reads_the_manifest_of_the_package_it_runs(tmp_path, monkeypatch):
    """The page shows the version beside the binary and the one the shard
    publishes; a source checkout reports no update at all."""
    install = tmp_path / "dist" / "camelot-launcher"
    install.mkdir(parents=True)
    (install / launcher.VERSION_FILE).write_text("2\n", encoding="utf-8")
    base = _package(tmp_path / "shard", version=5)
    monkeypatch.setattr(launcher, "install_directory", lambda: install)

    found = launcher.launcher_state({"launcher_url": str(base)})

    assert found == {"installed": 2, "published": 5, "available": True, "error": None}
    _package(tmp_path / "shard", version=2)
    assert launcher.launcher_state({"launcher_url": str(base)})["available"] is False

    monkeypatch.setattr(launcher, "install_directory", lambda: None)
    assert launcher.launcher_state({"launcher_url": str(base)})["installed"] == 0


def test_state_says_the_launcher_it_runs_and_what_waits(tmp_path, client, monkeypatch):  # noqa: F811  (the fixture the import names)
    settings, _player, _where, _report = _ready(tmp_path, client)
    install = tmp_path / "dist" / "camelot-launcher"
    install.mkdir(parents=True)
    (install / launcher.VERSION_FILE).write_text("1\n", encoding="utf-8")
    settings["launcher_url"] = str(_package(tmp_path / "shard", version=4))
    monkeypatch.setattr(launcher, "install_directory", lambda: install)

    found = launcher.state(settings)

    assert found["launcher"] == {
        "installed": 1,
        "published": 4,
        "available": True,
        "error": None,
    }
    assert "launcher_url" not in found["settings"]


def test_the_swap_script_waits_for_the_pid_then_swaps_and_can_restore(tmp_path):
    """Windows cannot overwrite a running executable, so the script waits for the
    process to be gone, parks the old folder, moves the staged one in, and puts
    the old one back when the move fails."""
    install = tmp_path / "dist" / "camelot-launcher"
    staged = install.parent / "update" / "5" / "camelot-launcher"

    windows = launcher.swap_script(
        install, staged, pid=4321, version=2, system="win32", args=["--no-open", "--port", "8799"]
    )
    assert 'set "PID=4321"' in windows
    assert 'PID eq %PID%' in windows
    assert 'set "STAGED=%HERE%update\\5\\camelot-launcher"' in windows
    assert '"--port" "8799"' in windows
    assert 'move "%INSTALL%" "%OLD%"' in windows
    assert 'move "%OLD%" "%INSTALL%"' in windows
    assert "camelot-launcher.old-2" in windows
    assert "--no-open" in windows

    posix = launcher.swap_script(
        install, staged, pid=4321, version=2, system="linux", args=["--no-open", "--port", "8799"]
    )
    assert 'kill -0 "$PID"' in posix
    assert 'STAGED="$HERE/update/5/camelot-launcher"' in posix
    assert "--port 8799" in posix
    assert 'mv "$INSTALL" "$OLD"' in posix
    assert 'mv "$OLD" "$INSTALL"' in posix
    assert "camelot-launcher.old-2" in posix
    assert "--no-open" in posix


def test_begin_update_stages_beside_the_install_and_hands_over(tmp_path, monkeypatch):
    install = tmp_path / "dist" / "camelot-launcher"
    install.mkdir(parents=True)
    (install / launcher.VERSION_FILE).write_text("2\n", encoding="utf-8")
    base = _package(tmp_path / "shard", version=5)
    monkeypatch.setattr(launcher, "install_directory", lambda: install)
    seen: dict = {}
    monkeypatch.setattr(
        launcher, "start_swap", lambda script, **kwargs: seen.update(script=script, **kwargs)
    )

    started = launcher.begin_update(
        {"launcher_url": str(base)}, system="linux", argv=["--port", "8799"]
    )

    root = install.parent / "update" / "5" / "camelot-launcher"
    assert started["version"] == 5
    assert (root / launcher.VERSION_FILE).read_text(encoding="utf-8") == "5\n"
    script = Path(started["script"])
    assert script.parent == install.parent and script.is_file()
    assert seen["script"] == script
    text = script.read_text(encoding="utf-8")
    assert 'mv "$INSTALL" "$OLD"' in text
    assert "--port 8799" in text and "--no-open" in text


def test_begin_update_refuses_a_source_checkout(tmp_path):
    base = _package(tmp_path / "shard", version=5)

    with pytest.raises(launcher.LauncherError, match="source checkout"):
        launcher.begin_update({"launcher_url": str(base)})


def test_begin_update_refuses_a_package_without_the_launchers_binary(tmp_path, monkeypatch):
    """The swap deletes the old install after the move: a package that carries
    no executable would leave nothing to start."""
    install = tmp_path / "dist" / "camelot-launcher"
    install.mkdir(parents=True)
    (install / launcher.VERSION_FILE).write_text("2\n", encoding="utf-8")
    base = _package(tmp_path / "shard", version=5, binary=False)
    monkeypatch.setattr(launcher, "install_directory", lambda: install)

    with pytest.raises(launcher.LauncherError, match="has no camelot-launcher"):
        launcher.begin_update({"launcher_url": str(base)}, system="linux", argv=[])
