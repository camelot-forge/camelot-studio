"""The editor: a zone's placements, edited and published (spec §6/§9, 6.3).

D2: the daemon is the only writer, the browser a pure consumer. An editing
session is *one mod's authoring directory* (`camelot serve --edits DIR`, the
Phase 5 format): `mod.json` names it, `journal.json` holds the ops the user
made, and `build` assembles the same `fixtures.csv` + `client.patch.json` +
pack the `camelot mod build` command line would — the editor is a front end
to the toolchain, never a parallel implementation.

The ops are zone-local, as `fixtures.csv` is:

- `move` writes `X` and `Y`, and `Z` only for a row that does **not** carry
  `Ground = 1`: the client drops a ground row on the terrain whatever its `Z`
  says (W-3, measured in game 2026-10-12), so the editor never invents one;
- `rotate` writes `A` (degrees), `scale` writes `Scale` (percent);
- `add` builds the whole row from the zone's own catalog (`nifs.csv`) — the
  catalogue's radius and ground flag are the client's own hints. A
  ground-placed entry is written `z = 0, Ground = 1` (the client's own
  convention); anything else lands at the terrain height under the point;
- `remove` drops the row and its line.

Every op but `add` carries `expect`, the fields as the user saw them, so an
edit made against a changed client is refused with both values named, never
applied blind. Undo drops the last op: ops are additive on the base table,
so dropping is exact. `remove` exists only in the journal — the build
assembles whole files, so no row op ever reaches an installer.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import gamedata, modbuild
from ..zones import Fixture, Zone, ZoneError, fixtures_from_table
from .config import ServeConfig
from .models import EditorBuild, EditorEdit, EditorPack, EditorState, EditorZone, WorldFixture

__all__ = ["SCHEMA", "Editor", "EditorError", "build_editor_router"]

#: The journal's schema tag: an editor that does not know it refuses the file.
SCHEMA = "camelot.editor/1"

#: The op kinds the journal holds.
KINDS = ("move", "rotate", "scale", "add", "remove")

#: The columns the editor writes, in the client's own header spelling.
COLUMN_NAMES = ("ID", "NIF #", "Textual Name", "X", "Y", "Z", "A", "Scale",
                "Collide", "Radius", "Animate", "Ground", "Flip", "Cave", "Unique Id")


class EditorError(ValueError):
    """A refused edit, an unusable journal, or a zone that cannot be edited."""


def _number_text(value: Any) -> str:
    """A coordinate the way the tables spell numbers (never `1e+06`)."""
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


class Editor:
    """The journal, the working tables, and the publish path of one mod."""

    def __init__(self, config: ServeConfig):
        self.config = config
        self._journal: dict[str, Any] | None = None

    @property
    def dir(self) -> Path | None:
        return self.config.edits

    @property
    def available(self) -> bool:
        return self.dir is not None

    # -- the journal on disk ---------------------------------------------------

    def _journal_path(self) -> Path:
        return self.dir / "journal.json"

    def _mod(self) -> dict[str, Any]:
        meta = self.dir / "mod.json"
        if meta.is_file():
            document = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                return document
        return {"id": self.dir.name or "world-edits", "version": "1", "priority": 100}

    def _load(self) -> dict[str, Any]:
        if self._journal is None:
            path = self._journal_path()
            if path.is_file():
                document = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(document, dict) or document.get("schema") != SCHEMA:
                    raise EditorError(
                        f"{path}: this journal is {document.get('schema')!r}; "
                        f"this build understands {SCHEMA}")
                self._journal = document
            else:
                self._journal = {"schema": SCHEMA, "mod": self._mod(), "zones": {}}
        return self._journal

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._journal_path().write_text(
            json.dumps(self._journal, indent=1, ensure_ascii=False), encoding="utf-8")

    def ops(self, zone_id: int) -> list[dict]:
        if not self.available:
            return []
        return list(self._load()["zones"].get(str(zone_id), []))

    def state(self) -> EditorState:
        if not self.available:
            return EditorState(available=False)
        journal = self._load()
        zones = [EditorZone(id=int(key), ops=list(ops))
                 for key, ops in sorted(journal["zones"].items(), key=lambda item: int(item[0]))
                 if ops]
        pack = None
        path = self.dir / f"{journal['mod'].get('id', 'world-edits')}.tar.gz"
        if path.is_file():
            stat = path.stat()
            pack = EditorPack(pack=str(path), modified=stat.st_mtime, size=stat.st_size)
        return EditorState(available=True, dir=str(self.dir), mod=journal["mod"],
                           zones=zones, pack=pack)

    # -- the tables ------------------------------------------------------------

    def base_table(self, zone: Zone) -> gamedata.Table:
        """The zone's own `fixtures.csv`, from the archive the client reads.

        W-1 measured that the client reads `csvNNN.mpk`; the editor edits and
        publishes exactly that entry, so a zone that ships no `fixtures.csv`
        there (or no `csv` archive at all) is one the editor refuses rather
        than half-supports.
        """
        try:
            raw = zone.entry("csv", "fixtures.csv")
        except ZoneError as error:
            raise EditorError(
                f"zone {zone.info.zone_id} ({zone.name}) ships no "
                f"csv{zone.info.zone_id:03d}.mpk#fixtures.csv: {error}") from error
        return gamedata.parse_table("fixtures.csv", raw)

    def working_table(self, zone: Zone) -> gamedata.Table:
        """The base table with this zone's journal applied.

        An op the base *already shows* counts as applied — `apply` writes the
        edit into the client, so the journal then describes a state the base
        carries; replaying it must be a no-op, not a conflict (the operator
        hit exactly this right after their first in-game check, 2026-10-12).
        A row that differs from *both* the op's expectation and its values
        still refuses: that is a real conflict, not a replay.
        """
        table = self.base_table(zone)
        for op in self.ops(zone.info.zone_id):
            if self._already_applied(table, op):
                continue
            try:
                table = gamedata.apply_row_ops(table, [op])
            except gamedata.TableError as error:
                raise EditorError(str(error)) from error
        return table

    @staticmethod
    def _already_applied(table: gamedata.Table, op: dict) -> bool:
        """Does the table already show what the op would write?"""
        action = op.get("action")
        try:
            row_id = int(op["id"])
        except (KeyError, TypeError, ValueError):
            return False
        index = table.row_index(row_id)
        if action == "remove":
            return index is None
        if index is None:
            return False
        columns = {name: int(position) for name, position in (op.get("columns") or {}).items()}
        values = {name: str(value) for name, value in (op.get("values") or {}).items()}
        if not values:
            return False
        row = table.rows[index]
        for name, wanted in values.items():
            position = columns.get(name)
            if position is None:
                return False
            current = row[position] if len(row) > position else ""
            if current != wanted:
                return False
        return True

    def fixtures(self, zone: Zone) -> list[Fixture]:
        """The placements as the journal leaves them — what the viewer shows."""
        return fixtures_from_table(zone.name, self.working_table(zone))

    # -- one op ----------------------------------------------------------------

    def _columns(self, table: gamedata.Table) -> dict[str, int]:
        out: dict[str, int] = {}
        for name in COLUMN_NAMES:
            try:
                out[name] = table.column_index(name)
            except gamedata.TableError:
                if name not in ("Flip", "Cave"):  # the columns the client's own file may lack
                    raise
        return out

    def _resolve(self, zone: Zone, table: gamedata.Table, op: dict) -> dict:
        """One request op -> one `gamedata` row op (or a refusal)."""
        kind = op.get("kind")
        if kind not in KINDS:
            raise EditorError(f"unknown edit {kind!r}; expected one of {', '.join(KINDS)}")
        columns = self._columns(table)
        if kind == "add":
            return self._resolve_add(zone, table, columns, op)
        try:
            row_id = int(op["id"])
        except (KeyError, TypeError, ValueError):
            raise EditorError("an edit needs the row's integer id") from None
        index = table.row_index(row_id)
        if index is None:
            raise EditorError(f"zone {zone.info.zone_id}: no fixture with id {row_id}")
        row = table.rows[index]

        def value_at(name: str) -> str:
            position = columns[name]
            return row[position] if len(row) > position else ""

        if kind == "remove":
            return {"action": "remove", "id": row_id,
                    "columns": {"X": columns["X"], "Y": columns["Y"]},
                    "expect": self._expect(op, value_at, ("X", "Y"))}

        values: dict[str, str] = {}
        expect: dict[str, str] = {}
        if kind == "move":
            for field, column in (("x", "X"), ("y", "Y")):
                if op.get(field) is None:
                    raise EditorError(f"move needs {field}")
                values[column] = _number_text(op[field])
            ground = value_at("Ground").strip() not in ("", "0") if "Ground" in columns else False
            if not ground and op.get("z") is not None:
                values["Z"] = _number_text(op["z"])
            expect = self._expect(op, value_at, tuple(values))
        elif kind == "rotate":
            if op.get("angle") is None:
                raise EditorError("rotate needs angle")
            values["A"] = _number_text(op["angle"])
            expect = self._expect(op, value_at, ("A",))
        elif kind == "scale":
            if op.get("scale") is None:
                raise EditorError("scale needs scale")
            values["Scale"] = _number_text(op["scale"])
            expect = self._expect(op, value_at, ("Scale",))

        return {"action": "set", "id": row_id,
                "columns": {name: columns[name] for name in values},
                "values": values, "expect": expect}

    @staticmethod
    def _expect(op: dict, value_at, names: tuple[str, ...]) -> dict[str, str]:
        """What the op must find before it lands: the values the user saw.

        The request may carry them (`expect`); when it does not, the daemon
        fills in what it holds — either way the journal records the basis an
        edit was made against, so replaying it on a client that moved on
        fails loudly instead of overwriting blind.
        """
        given = op.get("expect") or {}
        if not isinstance(given, dict):
            raise EditorError("expect must be an object of column -> value")
        return {name: str(given[name]) if name in given else value_at(name)
                for name in names}

    def _resolve_add(self, zone: Zone, table: gamedata.Table,
                     columns: dict[str, int], op: dict) -> dict:
        try:
            nif_id = int(op["nif_id"])
        except (KeyError, TypeError, ValueError):
            raise EditorError("add needs the catalog's nif_id") from None
        entry = next((row for row in zone.catalog() if row.id == nif_id), None)
        if entry is None:
            raise EditorError(f"zone {zone.info.zone_id}: the catalog has no NIF {nif_id}")
        try:
            x, y = float(op["x"]), float(op["y"])
        except (KeyError, TypeError, ValueError):
            raise EditorError("add needs x and y") from None

        ids = [fixture.id for fixture in fixtures_from_table(zone.name, table)]
        row_id = max(ids) + 1 if ids else 1
        if entry.ground:
            z = 0.0  # the client's own "drop it on the terrain" convention (W-3)
        else:
            try:
                z = zone.height_at(x, y)
            except ZoneError as error:
                raise EditorError(
                    f"zone {zone.info.zone_id}: cannot place at terrain height: {error}") from error
        values = {
            "NIF #": str(entry.id),
            "Textual Name": str(op.get("name") or entry.name),
            "X": _number_text(x), "Y": _number_text(y), "Z": _number_text(z),
            "A": "0", "Scale": "100", "Collide": "0",
            "Radius": _number_text(entry.radius), "Animate": "0",
            "Ground": "1" if entry.ground else "0", "Flip": "0", "Cave": "0",
            "Unique Id": str(row_id),
        }
        return {"action": "add", "id": row_id,
                "columns": {name: columns[name] for name in values}, "values": values}

    # -- writing ---------------------------------------------------------------

    def record(self, zone: Zone, op: dict) -> tuple[dict, Fixture | None]:
        """Validate one op, journal it, and hand back the row it leaves."""
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        ops = journal["zones"].setdefault(str(zone.info.zone_id), [])
        resolved = self._resolve(zone, self.working_table(zone), op)
        try:
            table = gamedata.apply_row_ops(self.working_table(zone), [resolved])
        except gamedata.TableError as error:
            raise EditorError(str(error)) from error
        ops.append(resolved)
        self._save()
        row_id = resolved["id"]
        fixture = next((row for row in fixtures_from_table(zone.name, table)
                        if row.id == row_id), None)
        return resolved, fixture

    def undo(self, zone_id: int) -> dict | None:
        """Drop the zone's last op — exact, because ops are additive."""
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        ops = journal["zones"].get(str(zone_id)) or []
        if not ops:
            return None
        dropped = ops.pop()
        if not ops:
            journal["zones"].pop(str(zone_id))
        self._save()
        return dropped

    def revert(self) -> None:
        """Undo everything: the journal empties, the base tables stand again.

        The build outputs go with it — a pack left behind by a reverted
        journal would claim an edit that no longer exists. The *installed*
        half in the client is untouched: uninstall reads the client's own
        stored state, never this directory.
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        journal["zones"] = {}
        self._save()
        self._clear_build(str(journal["mod"].get("id", "world-edits")))

    def _clear_build(self, mod_id: str) -> None:
        paths = [self.dir / f"{mod_id}.tar.gz", self.dir / "client.patch.json"]
        paths += sorted(self.dir.glob("fixtures-zone*.csv"))
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # -- publishing ------------------------------------------------------------

    def build(self, world) -> dict[str, Any]:
        """Assemble the authoring directory and run `mod build` on it."""
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        entries: list[dict] = []
        files: list[str] = []
        try:
            for key in sorted(journal["zones"], key=int):
                ops = journal["zones"][key]
                if not ops:
                    continue
                zone_id = int(key)
                zone = world.zone(zone_id)
                archive = zone.archive("csv")
                if archive is None:
                    raise EditorError(f"zone {zone_id}: no csv archive to target")
                name = f"fixtures-zone{zone_id:03d}.csv"
                (self.dir / name).write_bytes(
                    gamedata.encode_table(self.working_table(zone)))
                entries.append({
                    "archive": os.path.relpath(archive, str(world.config.root)).replace(os.sep, "/"),
                    "entry": "fixtures.csv",
                    "file": name,
                })
                files.append(name)
            if not entries:
                raise EditorError("nothing to publish: the journal holds no edit")
            (self.dir / "mod.json").write_text(
                json.dumps(journal["mod"], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            (self.dir / "client.patch.json").write_text(
                json.dumps({"archive": entries}, indent=2) + "\n", encoding="utf-8")
            pack = self.dir / f"{journal['mod'].get('id', 'world-edits')}.tar.gz"
            report = modbuild.build_mod_pack(
                str(self.dir), str(world.config.root), str(pack))
        except (ValueError, OSError) as error:
            raise EditorError(f"build refused: {error}") from error
        return {"pack": str(pack), "entries": entries, "files": files, "report": report}

    def apply(self, world) -> dict[str, Any]:
        """Build, then install the client half through the toolchain."""
        from .. import clientinstall

        built = self.build(world)
        try:
            install = clientinstall.install_pack(str(world.config.root), built["pack"])
        except Exception as error:  # InstallError, and anything the installer refuses
            raise EditorError(f"install refused: {error}") from error
        return {**built, "install": install}


# --- routes -------------------------------------------------------------------

def build_editor_router(world) -> APIRouter:
    """The editor's routes (D2: the daemon is the only writer).

    Mounted inside the world router's `/api/v1` prefix; paths here are
    relative to it.
    """
    router = APIRouter()
    editor = world.editor

    def ensure() -> Editor:
        if editor is None or not editor.available:
            raise HTTPException(
                status_code=503,
                detail="the editor needs an authoring directory: "
                       "start the daemon with --edits <dir>")
        return editor

    def http(error: EditorError) -> HTTPException:
        return HTTPException(status_code=400, detail=str(error))

    @router.get("/world/edit", response_model=EditorState)
    def world_edit() -> EditorState:
        """The editing session: the mod, the journal, and the built pack."""
        return editor.state() if editor is not None else EditorState(available=False)

    @router.post("/world/zone/{zone_id}/edits", response_model=EditorEdit)
    def world_edit_apply(zone_id: int, op: dict) -> EditorEdit:
        """One edit (move/rotate/scale/add/remove); journaled, then answered
        with the row as it now stands."""
        try:
            active = ensure()
            zone = world.zone(zone_id)
            resolved, fixture = active.record(zone, op)
        except EditorError as error:
            raise http(error) from error
        except ZoneError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        row = WorldFixture(**vars(fixture)) if fixture is not None else None
        return EditorEdit(op=resolved, fixture=row)

    @router.delete("/world/zone/{zone_id}/edits", response_model=EditorEdit)
    def world_edit_undo(zone_id: int) -> EditorEdit:
        try:
            active = ensure()
            dropped = active.undo(zone_id)
        except EditorError as error:
            raise http(error) from error
        return EditorEdit(op=dropped or {})

    @router.post("/world/mod/build", response_model=EditorBuild)
    def world_mod_build() -> EditorBuild:
        try:
            return EditorBuild(**ensure().build(world))
        except EditorError as error:
            raise http(error) from error

    @router.post("/world/mod/apply", response_model=EditorBuild)
    def world_mod_apply() -> EditorBuild:
        try:
            return EditorBuild(**ensure().apply(world))
        except EditorError as error:
            raise http(error) from error

    @router.post("/world/mod/revert", response_model=EditorState)
    def world_mod_revert() -> EditorState:
        try:
            active = ensure()
            active.revert()
        except EditorError as error:
            raise http(error) from error
        return active.state()

    return router
