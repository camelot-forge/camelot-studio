"""The client installer: planning, materialization, verification.

Everything runs against synthetic client directories built with the MPAK
writer, so no game file is needed. The in-client ladder validates the real
thing separately (`notes`, phase 5).
"""
import gzip
import io
import os
import tarfile

import pytest

from camelot import assemble, clientinstall, gamedata, mod
from camelot.contract import Manifest
from camelot.mpak import MpakArchive, write_mpak

MONSTERS = (
    b"Information,,NIF,Skins,\r\n"
    b"ID,name,#,Body,\r\n"
    b"99,Boar,221,109,\r\n"
    b"286,Crab,231,110,\r\n"
)
MONNIFS = (
    b"Information,,NIF,Anim,\r\n"
    b"ID,Textual Name,Name,Set,\r\n"
    b"231,Crab,crab,3,\r\n"
)
BOAR_NIF = b"original-boar-nif"
BOAR_DDS = b"old-boar-texture"
BEAR_DDS = b"bear-texture"


@pytest.fixture
def client(tmp_path):
    """A minimal client tree: two tables, one loose figure, one skin archive."""
    root = tmp_path / "client"
    (root / "figures" / "skins").mkdir(parents=True)
    (root / "figures" / "Boar.NIF").write_bytes(BOAR_NIF)
    write_mpak(str(root / "figures" / "skins" / "skin003.mpk"), "skin003.mpk",
               [("boar.dds", BOAR_DDS), ("bear.dds", BEAR_DDS)])
    write_mpak(str(root / "gamedata.mpk"), "gamedata.mpk",
               [("monsters.csv", MONSTERS), ("monnifs.csv", MONNIFS)])
    return str(root)


def _path(client_dir, rel):
    return os.path.join(client_dir, *rel.split("/"))


def _read_abs(path):
    with open(path, "rb") as fh:
        return fh.read()


def _read(client_dir, rel):
    return _read_abs(_path(client_dir, rel))


def _write(client_dir, rel, data):
    with open(_path(client_dir, rel), "wb") as fh:
        fh.write(data)


def _entry(client_dir, archive_rel, entry):
    archive = MpakArchive(_path(client_dir, archive_rel))
    try:
        return archive.read(entry)
    finally:
        archive.close()


def build_pack(tmp_path, name, *, mod_id, items=(), members=None, version="0.1.0",
               priority=100, requires=(), build=None, doc=None):
    if doc is None:
        doc = mod.deployment(mod_id, version, [], priority=priority, client=list(items))
        doc["requires"] = list(requires)
    files = {"targets/opendaoc/mod.json": mod.deployment_bytes(doc)}
    files.update(members or {})
    # The lock travels inside the pack and covers every member but itself;
    # the loader (and ModPack.load) verifies each line against the member.
    files["checksums.lock"] = assemble.lock_bytes(files)
    manifest = Manifest(pack_id=mod_id, version=version, client_build=build)
    path = tmp_path / name
    assemble.archive(manifest, str(path), files=files)
    return str(path)


def write_tar(path, members):
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as out,
    ):
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            out.addfile(info, io.BytesIO(data))
    with open(path, "wb") as fh:
        fh.write(buffer.getvalue())


def retar(path, changes):
    """Rewrite a pack's members, tampering past the lock."""
    with tarfile.open(path, "r:gz") as tar:
        members = {
            member.name: tar.extractfile(member).read()
            for member in tar.getmembers() if member.isfile()
        }
    members.update(changes)
    write_tar(path, members)


def with_build(client_dir, build):
    blob = ("VS_VERSION_INFO\x00FileVersion " + build + "\x00").encode("utf-16-le")
    with open(os.path.join(client_dir, "game.dll"), "wb") as fh:
        fh.write(blob)


def test_a_loose_replacement_is_written_verified_and_stored(client, tmp_path):
    pack = build_pack(
        tmp_path, "a.tar.gz", mod_id="probe.a",
        items=[mod.client_loose("figures/Boar.NIF")],
        members={"client/figures/Boar.NIF": b"new-boar"},
    )
    report = clientinstall.install_pack(client, pack)
    assert _read(client, "figures/Boar.NIF") == b"new-boar"
    assert report["changed"] == ["figures/Boar.NIF"]
    assert clientinstall.verify(client)["ok"] is True
    pristine = os.path.join(client, ".camelot", "pristine", "figures", "Boar.NIF")
    assert _read_abs(pristine) == BOAR_NIF
    member = os.path.join(client, ".camelot", "members", "probe.a", "0.1.0",
                          "client", "figures", "Boar.NIF")
    assert _read_abs(member) == b"new-boar"


def test_an_archive_entry_is_replaced_and_its_neighbour_kept(client, tmp_path):
    pack = build_pack(
        tmp_path, "b.tar.gz", mod_id="probe.b",
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")],
        members={"client/entries/figures/skins/skin003.mpk/boar.dds": b"new-dds"},
    )
    clientinstall.install_pack(client, pack)
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == b"new-dds"
    assert _entry(client, "figures/skins/skin003.mpk", "bear.dds") == BEAR_DDS
    assert clientinstall.verify(client)["ok"] is True


def test_a_table_set_edits_the_row_and_keeps_the_file_faithful(client, tmp_path):
    pack = build_pack(
        tmp_path, "c.tar.gz", mod_id="probe.c",
        items=[mod.client_gamedata_set("monnifs.csv", 231, {"Name": "ettin"},
                                       expect={"Name": "crab"}, columns={"Name": 2})],
    )
    clientinstall.install_pack(client, pack)
    raw = gamedata.read_table_bytes(client, "monnifs.csv")
    assert b"231,Crab,ettin,3," in raw
    assert raw.replace(b"231,Crab,ettin,3,", b"231,Crab,crab,3,") == MONNIFS
    assert clientinstall.verify(client)["ok"] is True


def test_a_table_add_appends_in_the_reserved_band(client, tmp_path):
    pack = build_pack(
        tmp_path, "d.tar.gz", mod_id="probe.d",
        items=[mod.client_gamedata_add(
            "monsters.csv", 2495, {"name": "Probe Crab", "#": "231", "Body": "110"},
            columns={"name": 1, "#": 2, "Body": 3},
        )],
    )
    clientinstall.install_pack(client, pack)
    raw = gamedata.read_table_bytes(client, "monsters.csv")
    assert raw == MONSTERS + b"2495,Probe Crab,231,110,\r\n"


def test_uninstall_restores_the_exact_original_bytes(client, tmp_path):
    originals = {
        rel: _read(client, rel)
        for rel in ("figures/Boar.NIF", "figures/skins/skin003.mpk", "gamedata.mpk")
    }
    pack = build_pack(
        tmp_path, "e.tar.gz", mod_id="probe.e",
        items=[
            mod.client_loose("figures/Boar.NIF"),
            mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds"),
            mod.client_gamedata_set("monnifs.csv", 231, {"Name": "ettin"},
                                    expect={"Name": "crab"}, columns={"Name": 2}),
        ],
        members={
            "client/figures/Boar.NIF": b"new-boar",
            "client/entries/figures/skins/skin003.mpk/boar.dds": b"new-dds",
        },
    )
    clientinstall.install_pack(client, pack)
    report = clientinstall.uninstall(client, "probe.e")
    assert report["changed"], "the uninstall rewrote the destinations"
    for rel, data in originals.items():
        assert _read(client, rel) == data, rel
    assert clientinstall.verify(client)["ok"] is True
    assert clientinstall.status(client)["installs"] == []


def test_two_mods_editing_different_fields_of_one_row_compose(client, tmp_path):
    a = build_pack(tmp_path, "f1.tar.gz", mod_id="probe.f1",
                   items=[mod.client_gamedata_set("monnifs.csv", 231, {"Name": "ettin"},
                                                  expect={"Name": "crab"},
                                                  columns={"Name": 2})])
    b = build_pack(tmp_path, "f2.tar.gz", mod_id="probe.f2",
                   items=[mod.client_gamedata_set("monnifs.csv", 231, {"Set": "9"},
                                                  expect={"Set": "3"},
                                                  columns={"Set": 3})])
    clientinstall.install_pack(client, a)
    clientinstall.install_pack(client, b)
    table = gamedata.parse_table(
        "monnifs.csv", gamedata.read_table_bytes(client, "monnifs.csv"))
    assert table.row_values(2, {"Name": 2, "Set": 3}) == {"Name": "ettin", "Set": "9"}


def test_two_mods_on_one_destination_are_resolved_by_priority(client, tmp_path):
    low = build_pack(
        tmp_path, "g1.tar.gz", mod_id="probe.low", priority=100,
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")],
        members={"client/entries/figures/skins/skin003.mpk/boar.dds": b"low-dds"},
    )
    high = build_pack(
        tmp_path, "g2.tar.gz", mod_id="probe.high", priority=200,
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")],
        members={"client/entries/figures/skins/skin003.mpk/boar.dds": b"high-dds"},
    )
    clientinstall.install_pack(client, low)
    clientinstall.install_pack(client, high)
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == b"high-dds"
    status = clientinstall.status(client)
    assert status["owners"]["entry:figures/skins/skin003.mpk#boar.dds"] == "probe.high"
    clientinstall.uninstall(client, "probe.high")
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == b"low-dds"
    clientinstall.uninstall(client, "probe.low")
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == BOAR_DDS


def test_an_equal_priority_conflict_refuses_before_writing(client, tmp_path):
    a = build_pack(
        tmp_path, "h1.tar.gz", mod_id="probe.h1", priority=100,
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")],
        members={"client/entries/figures/skins/skin003.mpk/boar.dds": b"a-dds"},
    )
    b = build_pack(
        tmp_path, "h2.tar.gz", mod_id="probe.h2", priority=100,
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "boar.dds")],
        members={"client/entries/figures/skins/skin003.mpk/boar.dds": b"b-dds"},
    )
    clientinstall.install_pack(client, a)
    with pytest.raises(clientinstall.ConflictError, match="claimed at priority 100"):
        clientinstall.install_pack(client, b)
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == b"a-dds"
    assert len(clientinstall.status(client)["installs"]) == 1


def test_two_adds_of_one_id_at_one_priority_are_refused(client, tmp_path):
    a = build_pack(tmp_path, "i1.tar.gz", mod_id="probe.i1",
                   items=[mod.client_gamedata_add("monsters.csv", 2495,
                                                  {"name": "A"}, columns={"name": 1})])
    b = build_pack(tmp_path, "i2.tar.gz", mod_id="probe.i2",
                   items=[mod.client_gamedata_add("monsters.csv", 2495,
                                                  {"name": "B"}, columns={"name": 1})])
    clientinstall.install_pack(client, a)
    with pytest.raises(clientinstall.ConflictError):
        clientinstall.install_pack(client, b)


def test_a_resolved_row_collision_is_warned_about(client, tmp_path):
    a = build_pack(tmp_path, "j1.tar.gz", mod_id="probe.j1", priority=100,
                   items=[mod.client_gamedata_add("monsters.csv", 2495,
                                                  {"name": "A"}, columns={"name": 1})])
    b = build_pack(tmp_path, "j2.tar.gz", mod_id="probe.j2", priority=200,
                   items=[mod.client_gamedata_add("monsters.csv", 2495,
                                                  {"name": "B"}, columns={"name": 1})])
    clientinstall.install_pack(client, a)
    report = clientinstall.install_pack(client, b)
    assert any("also added by probe.j1" in warning for warning in report["warnings"])
    raw = gamedata.read_table_bytes(client, "monsters.csv")
    assert b"2495,B,,,\r\n" in raw
    assert b"2495,A,,,\r\n" not in raw


def test_a_file_changed_outside_the_tool_is_drift_and_adopt_is_an_escape(client, tmp_path):
    pack = build_pack(tmp_path, "k.tar.gz", mod_id="probe.k",
                      items=[mod.client_loose("figures/Boar.NIF")],
                      members={"client/figures/Boar.NIF": b"mod-bytes"})
    clientinstall.install_pack(client, pack)
    assert clientinstall.verify(client)["ok"] is True
    _write(client, "figures/Boar.NIF", b"hand-edited")
    assert not clientinstall.verify(client)["ok"]
    with pytest.raises(clientinstall.DriftError, match="figures/Boar.NIF"):
        clientinstall.install_pack(client, pack)
    report = clientinstall.install_pack(client, pack, adopt=True)
    assert any("adopted" in warning for warning in report["warnings"])
    assert _read(client, "figures/Boar.NIF") == b"mod-bytes"
    # the adopted bytes are the new baseline: removal restores them, not the retail ones
    clientinstall.uninstall(client, "probe.k")
    assert _read(client, "figures/Boar.NIF") == b"hand-edited"


def test_a_crash_before_the_state_save_self_heals(client, tmp_path, monkeypatch):
    pack = build_pack(
        tmp_path, "l.tar.gz", mod_id="probe.l",
        items=[mod.client_gamedata_set("monnifs.csv", 231, {"Name": "ettin"},
                                       expect={"Name": "crab"}, columns={"Name": 2})],
    )
    with monkeypatch.context() as patch:
        patch.setattr(clientinstall, "_save_state", lambda *args, **kwargs: None)
        clientinstall.install_pack(client, pack)
    # the disk holds the new content, the state was never written
    assert b"231,Crab,ettin,3," in gamedata.read_table_bytes(client, "monnifs.csv")
    assert clientinstall.status(client)["installs"] == []
    # the next run must not mistake its own write for drift, and must land the same bytes
    clientinstall.install_pack(client, pack)
    assert b"231,Crab,ettin,3," in gamedata.read_table_bytes(client, "monnifs.csv")
    clientinstall.uninstall(client, "probe.l")
    assert gamedata.read_table_bytes(client, "monnifs.csv") == MONNIFS


def test_requires_is_enforced_both_ways(client, tmp_path):
    base = build_pack(tmp_path, "m1.tar.gz", mod_id="probe.base")
    top = build_pack(tmp_path, "m2.tar.gz", mod_id="probe.top", requires=["probe.base"])
    with pytest.raises(clientinstall.InstallError, match="requires probe.base"):
        clientinstall.install_pack(client, top)
    clientinstall.install_pack(client, base)     # an empty payload installs as a marker
    clientinstall.install_pack(client, top)
    with pytest.raises(clientinstall.InstallError, match="required by probe.top"):
        clientinstall.uninstall(client, "probe.base")
    clientinstall.uninstall(client, "probe.top")
    clientinstall.uninstall(client, "probe.base")


def test_missing_destinations_are_refused(client, tmp_path):
    missing_loose = build_pack(tmp_path, "n1.tar.gz", mod_id="probe.n1",
                               items=[mod.client_loose("figures/Nope.NIF")],
                               members={"client/figures/Nope.NIF": b"x"})
    with pytest.raises(clientinstall.InstallError, match="no such file"):
        clientinstall.install_pack(client, missing_loose)
    missing_entry = build_pack(
        tmp_path, "n2.tar.gz", mod_id="probe.n2",
        items=[mod.client_archive_entry("figures/skins/skin003.mpk", "nope.dds")],
        members={"client/entries/figures/skins/skin003.mpk/nope.dds": b"x"},
    )
    with pytest.raises(clientinstall.InstallError, match="no entry"):
        clientinstall.install_pack(client, missing_entry)
    whole_table = build_pack(
        tmp_path, "n3.tar.gz", mod_id="probe.n3",
        items=[mod.client_archive_entry("gamedata.mpk", "monnifs.csv")],
        members={"client/entries/gamedata.mpk/monnifs.csv": b"x"},
    )
    with pytest.raises(clientinstall.InstallError, match="row ops"):
        clientinstall.install_pack(client, whole_table)
    assert clientinstall.status(client)["installs"] == []
    assert _read(client, "figures/Boar.NIF") == BOAR_NIF
    assert gamedata.read_table_bytes(client, "monnifs.csv") == MONNIFS


def test_a_pack_is_verified_before_it_is_trusted(client, tmp_path):
    pack = build_pack(tmp_path, "o1.tar.gz", mod_id="probe.o",
                      items=[mod.client_loose("figures/Boar.NIF")],
                      members={"client/figures/Boar.NIF": b"new"})
    retar(pack, {"client/figures/Boar.NIF": b"tampered"})
    with pytest.raises(clientinstall.PackError, match="does not match its checksum"):
        clientinstall.install_pack(client, pack)

    unsafe = str(tmp_path / "unsafe.tar.gz")
    write_tar(unsafe, {"client/../evil.nif": b"x"})
    with pytest.raises(clientinstall.PackError, match="unsafe member name"):
        clientinstall.install_pack(client, unsafe)

    no_lock = str(tmp_path / "nolock.tar.gz")
    write_tar(no_lock, {"targets/opendaoc/mod.json": b"{}"})
    with pytest.raises(clientinstall.PackError, match="checksums.lock"):
        clientinstall.install_pack(client, no_lock)


def test_a_pack_referencing_a_missing_member_is_refused(client, tmp_path):
    doc = mod.deployment("probe.p", "1", [],
                         client=[mod.client_loose("figures/Boar.NIF")])
    pack = build_pack(tmp_path, "p1.tar.gz", mod_id="probe.p", doc=doc)
    with pytest.raises(clientinstall.PackError, match="payload member .* is missing"):
        clientinstall.install_pack(client, pack)


def test_a_pack_with_an_out_of_band_id_is_refused(client, tmp_path):
    doc = {
        "mod": {"id": "probe.q", "version": "1", "priority": 100},
        "requires": [], "allocations": [], "rows": [],
        "client": [{
            "kind": "gamedata_row", "action": "add", "table": "monsters.csv",
            "id": 40000, "columns": {"name": 1}, "values": {"name": "X"},
        }],
    }
    pack = build_pack(tmp_path, "q1.tar.gz", mod_id="probe.q", doc=doc)
    with pytest.raises(clientinstall.PackError, match="reserved band"):
        clientinstall.install_pack(client, pack)


def test_paths_resolve_case_insensitively(client, tmp_path):
    pack = build_pack(
        tmp_path, "r1.tar.gz", mod_id="probe.r",
        items=[
            mod.client_archive_entry("figures/SKINS/SKIN003.MPK", "BOAR.DDS"),
            mod.client_loose("figures/boar.nif"),
        ],
        members={
            "client/entries/figures/SKINS/SKIN003.MPK/BOAR.DDS": b"new-dds",
            "client/figures/boar.nif": b"new-boar",
        },
    )
    clientinstall.install_pack(client, pack)
    assert _entry(client, "figures/skins/skin003.mpk", "boar.dds") == b"new-dds"
    assert _read(client, "figures/Boar.NIF") == b"new-boar"
    assert not os.path.exists(_path(client, "figures/SKINS")), "no duplicate directory"


def test_update_replaces_the_version_and_prunes_the_store(client, tmp_path):
    v1 = build_pack(tmp_path, "s1.tar.gz", mod_id="probe.s", version="0.1.0",
                    items=[mod.client_loose("figures/Boar.NIF")],
                    members={"client/figures/Boar.NIF": b"v1"})
    v2 = build_pack(tmp_path, "s2.tar.gz", mod_id="probe.s", version="0.2.0",
                    items=[mod.client_loose("figures/Boar.NIF")],
                    members={"client/figures/Boar.NIF": b"v2"})
    clientinstall.install_pack(client, v1)
    clientinstall.install_pack(client, v2)
    assert _read(client, "figures/Boar.NIF") == b"v2"
    installs = clientinstall.status(client)["installs"]
    assert [install["version"] for install in installs] == ["0.2.0"]
    members_root = os.path.join(client, ".camelot", "members", "probe.s")
    assert os.listdir(members_root) == ["0.2.0"]
    clientinstall.uninstall(client, "probe.s")
    assert _read(client, "figures/Boar.NIF") == BOAR_NIF


def test_the_installer_refuses_while_the_game_runs(client, tmp_path, monkeypatch):
    """The client reads its files once, at startup: installing under a
    running one changes nothing on screen, so the tool says so instead of
    pretending. (The check is Linux-only and best-effort; the test drives it
    directly.)"""
    pack = build_pack(tmp_path, "u1.tar.gz", mod_id="probe.u",
                      items=[mod.client_loose("figures/Boar.NIF")],
                      members={"client/figures/Boar.NIF": b"x"})
    monkeypatch.setattr(clientinstall, "_client_running", lambda: "camelot.exe")
    with pytest.raises(clientinstall.InstallError, match="the game is running"):
        clientinstall.install_pack(client, pack)
    monkeypatch.setattr(clientinstall, "_client_running", lambda: None)
    clientinstall.install_pack(client, pack)          # the check passes once it is gone
    monkeypatch.setattr(clientinstall, "_client_running", lambda: "camelot.exe")
    with pytest.raises(clientinstall.InstallError, match="the game is running"):
        clientinstall.uninstall(client, "probe.u")


def test_a_band_change_does_not_brick_an_installed_mod(client, tmp_path, monkeypatch):
    """The reserved band is a build-time rule: a mod installed while it was
    wider must still verify and uninstall after the constants move (measured
    by the ceiling probe, 2026-10-09, which installs out-of-band ids on
    purpose)."""
    monkeypatch.setattr(mod, "CLIENT_ID_MIN", 1)
    monkeypatch.setattr(mod, "CLIENT_ID_MAX", 0xFFFF)
    pack = build_pack(tmp_path, "w1.tar.gz", mod_id="probe.w",
                      items=[mod.client_gamedata_add(
                          "monsters.csv", 4096, {"name": "Probe"},
                          columns={"name": 1})])
    clientinstall.install_pack(client, pack)
    monkeypatch.setattr(mod, "CLIENT_ID_MIN", 2494)
    monkeypatch.setattr(mod, "CLIENT_ID_MAX", 2499)
    assert clientinstall.verify(client)["ok"] is True
    clientinstall.uninstall(client, "probe.w")
    assert gamedata.read_table_bytes(client, "monsters.csv") == MONSTERS


def test_the_build_binding_is_checked(client, tmp_path):
    with_build(client, "1.127")
    good = build_pack(tmp_path, "t1.tar.gz", mod_id="probe.t1", build="1.127",
                      items=[mod.client_loose("figures/Boar.NIF")],
                      members={"client/figures/Boar.NIF": b"x"})
    foreign = build_pack(tmp_path, "t2.tar.gz", mod_id="probe.t2", priority=200,
                         build="1.130",
                         items=[mod.client_loose("figures/Boar.NIF")],
                         members={"client/figures/Boar.NIF": b"x"})
    clientinstall.install_pack(client, good)
    with pytest.raises(clientinstall.InstallError, match="built against client 1.130"):
        clientinstall.install_pack(client, foreign)
    clientinstall.install_pack(client, foreign, allow_build_mismatch=True)
