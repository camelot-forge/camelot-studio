"""Building a mod pack from an authoring directory, and the CLI flow.

Synthetic client and mod directories throughout. The pack built here is then
read by `clientinstall.ModPack.load` and installed against the same client,
so the tests exercise the verifier the player-side tool actually uses.
"""
import json
import os
import shutil

import pytest
from _pe import client_dll

from camelot import clientinstall, clientpatch, modbuild
from camelot.cli import main
from camelot.mpak import write_mpak

MONSTERS = (
    b"Information,,NIF,Skins,\r\n"
    b"ID,name,#,Body,\r\n"
    b"99,Boar,231,109,\r\n"
    b"286,Crab,231,110,\r\n"
)
MONNIFS = (
    b"Information,,NIF,Anim,\r\n"
    b"ID,Textual Name,Name,Set,\r\n"
    b"231,Crab,crab,3,\r\n"
)
BOAR_NIF = b"original-boar-nif"


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "client"
    (root / "figures" / "skins").mkdir(parents=True)
    (root / "figures" / "Boar.NIF").write_bytes(BOAR_NIF)
    write_mpak(str(root / "figures" / "skins" / "skin003.mpk"), "skin003.mpk",
               [("boar.dds", b"old-dds")])
    write_mpak(str(root / "gamedata.mpk"), "gamedata.mpk",
               [("monsters.csv", MONSTERS), ("monnifs.csv", MONNIFS)])
    return str(root)


def make_mod_dir(tmp_path, *, name="mymod", mod_id="probe.x", version="0.1.0",
                 meta=None, loose=None, extra=None, patch=None, rows=None):
    root = tmp_path / name
    (root / "client").mkdir(parents=True, exist_ok=True)
    doc = {"id": mod_id, "version": version}
    doc.update(meta or {})
    (root / "mod.json").write_text(json.dumps(doc), encoding="utf-8")
    for rel, data in (loose or {}).items():
        path = root / "client" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for rel, data in (extra or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if patch is not None:
        (root / "client.patch.json").write_text(json.dumps(patch), encoding="utf-8")
    if rows is not None:
        (root / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    return str(root)


def test_a_loose_mirror_becomes_the_items(client, tmp_path):
    mod_dir = make_mod_dir(tmp_path, loose={"figures/Boar.NIF": b"new-boar"})
    out = tmp_path / "mymod.tar.gz"
    report = modbuild.build_mod_pack(mod_dir, client, str(out))
    assert (report["items"], report["rows"], report["members"]) == (1, 0, 1)

    pack = clientinstall.ModPack.load(str(out))
    assert pack.mod_id == "probe.x"
    assert pack.version == "0.1.0"
    assert pack.items == [{"kind": "loose", "path": "figures/Boar.NIF",
                           "member": "client/figures/Boar.NIF"}]
    assert pack.members["client/figures/Boar.NIF"] == b"new-boar"


def test_a_patch_resolves_against_the_client(client, tmp_path):
    mod_dir = make_mod_dir(
        tmp_path,
        patch={
            "archive": [{"archive": "figures/skins/skin003.mpk", "entry": "boar.dds",
                         "file": "textures/boar.dds"}],
            "gamedata": [{"table": "monnifs.csv", "set": {"231": {"Name": "ettin"}}}],
        },
        extra={"textures/boar.dds": b"new-dds"},
    )
    out = tmp_path / "mymod.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))

    pack = clientinstall.ModPack.load(str(out))
    assert [item["kind"] for item in pack.items] == ["archive_entry", "gamedata_row"]
    entry, op = pack.items
    assert entry["member"] == "client/entries/figures/skins/skin003.mpk/boar.dds"
    assert pack.members[entry["member"]] == b"new-dds"
    assert op["columns"] == {"Name": 2}
    assert op["expect"] == {"Name": "crab"}


def test_rows_and_creatures_land_in_the_deployment_document(client, tmp_path):
    rows = [{"table": "npctemplate", "id": "spawn:probe",
             "values": {"Name": "Crab Thing", "Model": 2495}}]
    mod_dir = make_mod_dir(tmp_path, rows=rows)
    out = tmp_path / "mymod.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out), creatures=1)

    pack = clientinstall.ModPack.load(str(out))
    doc = json.loads(pack.members["targets/opendaoc/mod.json"])
    assert doc["mod"]["id"] == "probe.x"
    assert [row["id"] for row in doc["rows"]] == ["spawn:probe", "spawn:99"]
    assert {a["table"] for a in doc["allocations"]} == {"npctemplate"}
    assert doc["client"] == []


def test_an_add_at_a_referenced_id_is_refused(client, tmp_path):
    """A monsters.csv add may not take an id the shipped server uses."""
    patch = {"gamedata": [{"table": "monsters.csv",
                           "add": [{"id": 592, "values": {"name": "Probe"}}]}]}
    mod_dir = make_mod_dir(tmp_path, patch=patch)
    with pytest.raises(ValueError, match="referenced by the shipped server"):
        modbuild.build_mod_pack(mod_dir, client, str(tmp_path / "x.tar.gz"))


def test_an_add_at_a_free_id_is_accepted(client, tmp_path):
    patch = {"gamedata": [{"table": "monsters.csv",
                           "add": [{"id": 2069, "values": {"name": "Probe"}}]}]}
    mod_dir = make_mod_dir(tmp_path, patch=patch)
    out = tmp_path / "y.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))
    assert out.exists()


def test_a_server_can_replace_the_used_models_list(client, tmp_path):
    """The shipped list is one server's measurement; a file replaces it."""
    patch = {"gamedata": [{"table": "monsters.csv",
                           "add": [{"id": 592, "values": {"name": "Probe"}}]}]}
    mod_dir = make_mod_dir(tmp_path, patch=patch)
    out = tmp_path / "z.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out), used_models="1\n")
    assert out.exists()


def test_priority_and_requires_travel_from_mod_json(client, tmp_path):
    mod_dir = make_mod_dir(tmp_path, meta={"priority": 250, "requires": ["probe.base"]})
    out = tmp_path / "mymod.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))
    pack = clientinstall.ModPack.load(str(out))
    assert pack.priority == 250
    assert pack.requires == ["probe.base"]


def test_a_built_pack_installs_and_uninstalls(client, tmp_path):
    mod_dir = make_mod_dir(tmp_path, loose={"figures/Boar.NIF": b"new-boar"})
    out = tmp_path / "mymod.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))

    clientinstall.install_pack(client, str(out))
    with open(os.path.join(client, "figures", "Boar.NIF"), "rb") as fh:
        assert fh.read() == b"new-boar"
    assert clientinstall.verify(client)["ok"] is True
    clientinstall.uninstall(client, "probe.x")
    with open(os.path.join(client, "figures", "Boar.NIF"), "rb") as fh:
        assert fh.read() == BOAR_NIF


def test_the_build_refuses_what_would_not_hold_together(client, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="mod.json"):
        modbuild.build_mod_pack(str(empty), client, str(tmp_path / "a.tar.gz"))

    missing = make_mod_dir(tmp_path, name="missing", patch={"archive": [
        {"archive": "figures/skins/skin003.mpk", "entry": "boar.dds",
         "file": "textures/nope.dds"}]})
    with pytest.raises(ValueError, match="is missing"):
        modbuild.build_mod_pack(missing, client, str(tmp_path / "b.tar.gz"))

    escaping = make_mod_dir(tmp_path, name="escaping", patch={"archive": [
        {"archive": "figures/skins/skin003.mpk", "entry": "boar.dds",
         "file": "../evil.dds"}]})
    with pytest.raises(ValueError, match="inside the mod"):
        modbuild.build_mod_pack(escaping, client, str(tmp_path / "c.tar.gz"))

    unknown_column = make_mod_dir(tmp_path, name="columns", patch={
        "gamedata": [{"table": "monnifs.csv", "set": {"231": {"Nope": "x"}}}]})
    with pytest.raises(ValueError, match="no column"):
        modbuild.build_mod_pack(unknown_column, client, str(tmp_path / "d.tar.gz"))


def test_the_cli_builds_installs_verifies_and_removes(client, tmp_path):
    mod_dir = make_mod_dir(tmp_path, loose={"figures/Boar.NIF": b"new-boar"})
    out = tmp_path / "mymod.tar.gz"
    assert main(["mod", "build", "--dir", mod_dir, "--client", client,
                 "--out", str(out)]) == 0
    assert main(["client", "install", str(out), "--client", client]) == 0
    assert main(["client", "status", "--client", client]) == 0
    assert main(["client", "verify", "--client", client]) == 0

    # a file touched outside the tool: verify says so, with a non-zero exit
    with open(os.path.join(client, "figures", "Boar.NIF"), "wb") as fh:
        fh.write(b"hand-edited")
    assert main(["client", "verify", "--client", client]) == 1
    # the drift refusal protects the removal too, and names the way back
    assert main(["client", "uninstall", "probe.x", "--client", client]) == 1
    store = os.path.join(client, ".camelot", "pristine", "figures", "Boar.NIF")
    shutil.copyfile(store, os.path.join(client, "figures", "Boar.NIF"))
    assert main(["client", "uninstall", "probe.x", "--client", client]) == 0
    with open(os.path.join(client, "figures", "Boar.NIF"), "rb") as fh:
        assert fh.read() == BOAR_NIF


def test_the_cli_needs_a_client_directory(tmp_path, client):
    mod_dir = make_mod_dir(tmp_path, name="needsclient",
                           loose={"figures/Boar.NIF": b"x"})
    assert main(["mod", "build", "--dir", mod_dir, "--out", str(tmp_path / "x.tar.gz"),
                 "--client", str(tmp_path / "nowhere")]) == 2


# --- binary patches (the model-id ceiling) -----------------------------------

#: 3072 is a multiple of 256, which keeps the section size file-aligned.
CAP = 3072
CEILING = CAP - 1
CAPABILITY = f"client.modelids.{CAP}"


def test_a_model_ids_spec_becomes_the_recipe(client, tmp_path):
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(client_dll())
    mod_dir = make_mod_dir(tmp_path, name="ceiling", meta={"requires": [CAPABILITY]},
                           patch={"binary": [{"model_ids": CAP}]})
    out = tmp_path / "ceiling.tar.gz"
    report = modbuild.build_mod_pack(mod_dir, client, str(out))
    assert report["items"] == 1

    pack = clientinstall.ModPack.load(str(out))
    assert [item["kind"] for item in pack.items] == ["patch"]
    item = pack.items[0]
    assert item["file"] == "game1127.dll"
    assert item["provides"] == [CAPABILITY]
    assert item["append_section"]["name"] == ".cmod"
    assert pack.requires == [CAPABILITY]
    assert pack.ceiling == CEILING


def test_a_built_patch_pack_installs_and_uninstalls(client, tmp_path):
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(client_dll())
    mod_dir = make_mod_dir(tmp_path, name="ceiling", meta={"requires": [CAPABILITY]},
                           patch={"binary": [{"model_ids": CAP}]})
    out = tmp_path / "ceiling.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))
    clientinstall.install_pack(client, str(out))
    with open(os.path.join(client, "game1127.dll"), "rb") as fh:
        assert clientpatch.carried_cap(fh.read()) == CAP
    assert clientinstall.verify(client)["ok"] is True

    clientinstall.uninstall(client, "probe.x")
    with open(os.path.join(client, "game1127.dll"), "rb") as fh:
        assert fh.read() == client_dll()


def test_a_high_id_add_needs_the_capability(client, tmp_path):
    """The build refuses the add first, and names the declaration that lifts
    the band; with it, the same add builds."""
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(client_dll())
    patch = {"gamedata": [{"table": "monsters.csv",
                           "add": [{"id": 3000, "values": {"name": "High"}}]}]}
    without = make_mod_dir(tmp_path, name="nohigh", patch=patch)
    with pytest.raises(ValueError, match="client.modelids"):
        modbuild.build_mod_pack(without, client, str(tmp_path / "x.tar.gz"))

    with_cap = make_mod_dir(tmp_path, name="high",
                            meta={"requires": [CAPABILITY]}, patch=patch)
    out = tmp_path / "y.tar.gz"
    modbuild.build_mod_pack(with_cap, client, str(out))
    pack = clientinstall.ModPack.load(str(out))
    assert pack.items[0]["id"] == 3000


def test_a_model_ids_spec_needs_a_pristine_binary(client, tmp_path):
    mod_dir = make_mod_dir(tmp_path, name="nodll",
                           patch={"binary": [{"model_ids": CAP}]})
    with pytest.raises(ValueError, match="no game1127.dll"):
        modbuild.build_mod_pack(mod_dir, client, str(tmp_path / "x.tar.gz"))


def test_an_already_patched_client_is_refused(client, tmp_path):
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        pristine = client_dll()
        fh.write(pristine)
    patched = clientpatch.apply(pristine, clientpatch.model_id_recipe(
        os.path.join(client, "game1127.dll"), cap=CAP))
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(patched)
    mod_dir = make_mod_dir(tmp_path, name="patched",
                           patch={"binary": [{"model_ids": CAP}]})
    with pytest.raises(ValueError, match="already patched"):
        modbuild.build_mod_pack(mod_dir, client, str(tmp_path / "x.tar.gz"))


def test_a_patch_spec_must_declare_a_capability(client, tmp_path):
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(client_dll())
    mod_dir = make_mod_dir(tmp_path, name="nocap", patch={"binary": [{
        "file": "game1127.dll", "expect": {"0x457d7e": "c4090000"},
        "write": {"0x457d7e": "0f270000"}}]})
    with pytest.raises(ValueError, match="capability it provides"):
        modbuild.build_mod_pack(mod_dir, client, str(tmp_path / "x.tar.gz"))


def test_an_explicit_recipe_travels_as_given(client, tmp_path):
    """The escape hatch: offsets the studio does not know are carried, not
    checked against this client (the installer's preconditions are the
    check, and a recipe may build on another patch)."""
    with open(os.path.join(client, "game1127.dll"), "wb") as fh:
        fh.write(client_dll())
    mod_dir = make_mod_dir(tmp_path, name="explicit", patch={"binary": [{
        "file": "game1127.dll", "provides": ["client.modelids.9999"],
        "expect": {"0x457d7e": "c4090000"},
        "write": {"0x457d7e": "0f270000"}}]})
    out = tmp_path / "explicit.tar.gz"
    modbuild.build_mod_pack(mod_dir, client, str(out))
    pack = clientinstall.ModPack.load(str(out))
    assert pack.items[0]["provides"] == ["client.modelids.9999"]
    assert pack.ceiling == 9998
