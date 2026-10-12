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

**The zone's server rows (B2, W-4's first half).** A fixture move may carry
`rows`: the server rows that move with it (the keep and its doors — the case
W-1 measured; the browser proposes the pairs from the fixture's own mesh box,
this side validates them against the live rows and computes the new values).
Rows are also editable on their own (`row-move`, `row-remove`), whose
coordinates are zone-local too — the row a viewer holds is (B1 measured that
split), and only the write turns it into the server's world coordinates.
Those edits are **patches to rows that already exist**, which is the one
thing the Phase 5 loader does not do (it allocates ids for *new* rows): the
daemon writes them behind the explicit `--db-write` flag, records the
originals it read, and `apply`/`revert` are symmetric — a patch's reversal is
its original values, a removal's is the row re-inserted whole. A patch is
server-specific by nature (it names a row by the server's own key), and it is
it is never silent: without `--db-write` every row op is refused.

**The mouse grammar (the UX pass of 2026-10-12).** Left is selection and only
selection: a click picks, a drag draws a box that takes every fixture and every
server row whose own point falls inside it (what is drawn is what is caught),
shift adds. The **middle button** is the edit gesture — move, shift+middle
rotate (15°, about the selection's own pivot), alt+middle lift, ctrl+middle
scale — because the left button was doing three jobs at once. Right orbits,
shift+right pans, the wheel zooms, F focuses, Del removes, Escape lets go. A
gesture that ends where it started is **not** an edit and journals nothing.
The editor lives in its own floating, draggable panel over the scene (the
viewer stays a viewer), and the panel reads like a repository: the publish
button carries the count of unpublished changes, and the session, the written
rows and the *client's own installed half* are named separately.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import gamedata, modbuild, serverrows
from ..zones import Fixture, Zone, ZoneError, fixtures_from_table
from .config import ServeConfig
from .models import (
    EditorBuild,
    EditorClient,
    EditorEdit,
    EditorPack,
    EditorRestore,
    EditorRowOp,
    EditorState,
    EditorZone,
    WorldFixture,
)

__all__ = ["SCHEMA", "Editor", "EditorError", "build_editor_router"]

#: The journal's schema tag: an editor that does not know it refuses the file.
SCHEMA = "camelot.editor/1"

#: The op kinds the journal holds. The last two edit the zone's *server* rows.
KINDS = ("move", "rotate", "scale", "add", "remove", "row-move", "row-remove")

#: Server-row edits need a live connection: without one there is nothing to
#: read the originals from, and a patch that cannot be reversed is not made.
ROW_KINDS = ("row-move", "row-remove")

#: The server's heading is a 12-bit compass (measured); `fixtures.csv`'s `A`
#: is degrees. Both turn clockwise on a north-up map, so a rotation delta
#: carries over with the unit change alone.
HEADING_UNITS = 4096.0

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


def _frame(angle: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """A fixture's own frame as map vectors: its local `+x` and `+y`.

    Measured conventions: `A` is degrees with 0 = the mesh's front to the
    north, 90 = east (the keep's gatehouse is north at `A = 0`), and the map
    is `(x east, y south)`. So `+y` maps to `(sin A, -cos A)`.
    """
    radians = math.radians(angle)
    return ((math.cos(radians), math.sin(radians)),
            (math.sin(radians), -math.cos(radians)))


def _move_with_fixture(before: dict, after: dict, rows, origin) -> list[dict]:
    """The row patches a fixture move implies (B2: the keep and its doors).

    Each row's position is re-projected through the fixture's frame, so a
    rotation carries the rows around the fixture exactly; a row keeps its
    height *relative to the fixture* (a gate on a wall stays on the wall);
    scaling a fixture does not scale its server rows, and that is written
    down rather than guessed at. `origin` is the zone's world rectangle:
    the fixture is in zone-local coordinates, the rows in world ones.
    """
    left, top = origin
    old_x, old_y = _frame(before["angle"])
    new_x, new_y = _frame(after["angle"])
    delta_angle = (float(after["angle"]) - float(before["angle"])) % 360
    patches = []
    for table, key, row in rows:
        dx = (float(row["X"]) - left) - before["x"]
        dy = (float(row["Y"]) - top) - before["y"]
        local_x = dx * old_x[0] + dy * old_x[1]
        local_y = dx * old_y[0] + dy * old_y[1]
        new_local_x = after["x"] + local_x * new_x[0] + local_y * new_y[0]
        new_local_y = after["y"] + local_x * new_x[1] + local_y * new_y[1]
        values: dict[str, str] = {
            "X": _number_text(round(left + new_local_x)),
            "Y": _number_text(round(top + new_local_y)),
            "Z": _number_text(round(float(row["Z"]) - before["z"] + after["z"])),
        }
        if "Heading" in serverrows.COLUMNS[table]:
            values["Heading"] = str(
                round((float(row["Heading"]) + delta_angle * HEADING_UNITS / 360.0)
                      % HEADING_UNITS))
        patches.append({"table": table, "key": key, "values": values,
                        "originals": {name: str(row.get(name, "")) for name in values}})
    return patches


class Editor:
    """The journal, the working tables, and the publish path of one mod."""

    def __init__(self, config: ServeConfig, *, connect_=None):
        self.config = config
        self._journal: dict[str, Any] | None = None
        self._connection: dict[str, Any] | None = None
        #: injected by the tests; production opens MySQL through `serverrows`.
        self._connect = connect_ or serverrows.connect

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
                document.setdefault("patches", {})
                self._journal = document
            else:
                self._journal = {"schema": SCHEMA, "mod": self._mod(),
                                 "zones": {}, "patches": {}, "originals": {}}
        return self._journal

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self._journal_path().write_text(
            json.dumps(self._journal, indent=1, ensure_ascii=False), encoding="utf-8")

    def ops(self, zone_id: int) -> list[dict]:
        if not self.available:
            return []
        return list(self._load()["zones"].get(str(zone_id), []))

    def patches(self, zone_id: int) -> list[dict]:
        """The zone's server-row patches (moves and removals), journal order."""
        if not self.available:
            return []
        return list(self._load()["patches"].get(str(zone_id), []))

    def patch_rows(self, zone_id: int, rows: dict[str, list[dict]]) -> tuple[dict, list[str]]:
        """The server rows with this zone's journal patches applied — what the
        viewer must show, so an edited door appears where it was recorded."""
        notes: list[str] = []
        for patch in self.patches(zone_id):
            table = patch["table"]
            rows_for = rows.get(table)
            if not rows_for:
                continue
            key_column = serverrows.KEY_COLUMNS[table]
            for index, row in enumerate(rows_for):
                if str(row.get(key_column)) != str(patch["key"]):
                    continue
                if patch.get("deleted"):
                    rows_for.pop(index)
                    notes.append(f"{table}: removed by the journal")
                else:
                    updated = dict(row)
                    updated.update(patch["values"])
                    rows_for[index] = updated
                break
        return rows, notes

    def state(self) -> EditorState:
        if not self.available:
            return EditorState(available=False)
        journal = self._load()
        zones = [EditorZone(id=int(key), ops=list(ops))
                 for key, ops in sorted(journal["zones"].items(), key=lambda item: int(item[0]))
                 if ops]
        patches = [EditorRowOp(zone=int(key), patches=list(entries))
                   for key, entries in sorted(journal["patches"].items(),
                                              key=lambda item: int(item[0]))
                   if entries]
        pack = None
        path = self.dir / f"{journal['mod'].get('id', 'world-edits')}.tar.gz"
        if path.is_file():
            stat = path.stat()
            pack = EditorPack(pack=str(path), modified=stat.st_mtime, size=stat.st_size)
        return EditorState(available=True, dir=str(self.dir), mod=journal["mod"],
                           zones=zones, rows=patches, pack=pack,
                           client=self.client_state(),
                           published=dict(journal.get("published") or {}))

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
        And so does a *run* of ops on one row whose last step is where the base
        already stands: that is a chain a publish has carried (25344 → 20992 →
        19968 landing on a base that reads 19968), and refusing it would leave
        the session unable to publish again.
        A row that differs from *both* the op's expectation and its values
        still refuses: that is a real conflict, not a replay.
        """
        table = self.base_table(zone)
        pending = list(self.ops(zone.info.zone_id))
        index = 0
        while index < len(pending):
            op = pending[index]
            if self._already_applied(table, op):
                index += 1
                continue
            try:
                table = gamedata.apply_row_ops(table, [op])
                index += 1
            except gamedata.TableError as error:
                carried = self._published_run(table, pending, index)
                if carried is None:
                    raise EditorError(str(error)) from error
                index += carried
        return table

    def _published_run(self, table: gamedata.Table, pending: list[dict],
                       index: int) -> int | None:
        """How many ops to skip when the run that starts here was published.

        A run is the consecutive ops on one row. It counts as published when
        its *last* step is a state the table already shows — whatever the
        intermediate steps asked for, a published chain ends where the base
        stands.
        """
        row_id = pending[index].get("id")
        if row_id is None:
            return None
        end = index
        while end + 1 < len(pending) and pending[end + 1].get("id") == row_id:
            end += 1
        if end == index:
            return None
        return end - index + 1 if self._already_applied(table, pending[end]) else None

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

    def record(self, zone: Zone, op: dict) -> tuple[dict, Fixture | None, list[dict]]:
        """Validate one op, journal it, and hand back what it leaves.

        Returns `(resolved, fixture, patches)`: the client row op (empty for a
        server-row op), the fixture it leaves (None for a removal), and the
        server-row patches the op implies — the rows a fixture move carries
        (B2), or the row op itself (W-4's first half).
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        zone_key = str(zone.info.zone_id)
        if op.get("kind") in ROW_KINDS:
            patches = self._resolve_rows(zone, op)
            journal["patches"].setdefault(zone_key, []).extend(patches)
            self._remember_originals(journal, patches)
            self._save()
            return {}, None, patches

        current = self.working_table(zone)
        resolved = self._resolve(zone, current, op)
        try:
            after = gamedata.apply_row_ops(current, [resolved])
        except gamedata.TableError as error:
            raise EditorError(str(error)) from error
        patches = self._pair_patches(zone, current, after, resolved, op.get("rows") or [])
        journal["zones"].setdefault(zone_key, []).append(resolved)
        if patches:
            journal["patches"].setdefault(zone_key, []).extend(patches)
            self._remember_originals(journal, patches)
        self._save()
        row_id = resolved["id"]
        fixture = next((row for row in fixtures_from_table(zone.name, after)
                        if row.id == row_id), None)
        return resolved, fixture, patches

    def _placement(self, zone: Zone, table: gamedata.Table, row_id: int) -> dict:
        """A fixture's placement as the *client* renders it (`Ground = 1`
        drops the row on the terrain — W-3 — so the frame uses that z)."""
        index = table.row_index(row_id)
        if index is None:
            raise EditorError(f"zone {zone.info.zone_id}: no fixture with id {row_id}")
        row = table.rows[index]
        columns = self._columns(table)

        def cell(name: str, default: str = "") -> str:
            position = columns.get(name)
            if position is None or len(row) <= position:
                return default
            return row[position]

        x = float(cell("X", "0") or 0)
        y = float(cell("Y", "0") or 0)
        ground = cell("Ground").strip() not in ("", "0")
        return {"x": x, "y": y,
                "z": zone.height_at(x, y) if ground else float(cell("Z", "0") or 0),
                "angle": float(cell("A", "0") or 0)}

    def _pair_patches(self, zone: Zone, before_table: gamedata.Table,
                      after_table: gamedata.Table, resolved: dict,
                      keys) -> list[dict]:
        """The server rows a fixture op carries (B2), validated live.

        The browser proposes the pairs (it holds the fixture's own mesh box);
        this side checks every key against the database it is about to write.
        """
        if not keys:
            return []
        grouped: dict[str, list[str]] = {}
        for entry in keys:
            table = str(entry.get("table", ""))
            key = str(entry.get("key", ""))
            if table not in serverrows.KEY_COLUMNS or not key:
                raise EditorError(
                    f"a paired row needs a patchable table and a key: {entry!r}")
            grouped.setdefault(table, []).append(key)
        rows: list[tuple[str, str, dict]] = []
        for table, wanted in grouped.items():
            found = serverrows.read_rows_by_key(self._read_connect(), table, wanted)
            for key in wanted:
                if key not in found:
                    raise EditorError(
                        f"{table}: no row with key {key!r} in the server's database")
                rows.append((table, key, found[key]))
        before = self._placement(zone, before_table, resolved["id"])
        after = self._placement(zone, after_table, resolved["id"])
        rect = zone.info.world_rect
        patches = _move_with_fixture(before, after, self._journal_rows(zone, rows),
                                     (rect[0], rect[1]))
        # The values come from the rows as the journal *already* leaves them (a
        # keep moved twice before an apply stacks, exactly as the fixture's own
        # ops do), but a patch's `originals` are what the database holds: that
        # is what a revert has to put back, whatever the journal did between.
        stored = {(table, key): row for table, key, row in rows}
        for patch in patches:
            held = stored[(patch["table"], patch["key"])]
            patch["originals"] = {name: str(held.get(name, ""))
                                  for name in patch["values"]}
        return patches

    @staticmethod
    def _remember_originals(journal: dict, patches: list[dict]) -> None:
        """The values a row had when this *session* first touched it.

        A patch's own `originals` are what the database held before that one
        patch — which stops being the truth once a publish has written the
        session's earlier edits. The ledger is what `revert` restores to, so
        `restore all` means the state the session started from, not the state
        of the last publish. Per column, first touch wins.
        """
        ledger = journal.setdefault("originals", {})
        for patch in patches:
            row = ledger.setdefault(f"{patch['table']}:{patch['key']}", {})
            for column, value in patch["originals"].items():
                row.setdefault(column, value)

    def _journal_rows(self, zone: Zone, rows: list[tuple[str, str, dict]]):
        """The rows as the zone's journal already leaves them.

        A patch's values are absolute, so a second edit must start from the row
        the *viewer* shows — the database plus the journal's patches — never
        from the database alone, or the first edit's delta is dropped when both
        land at once.
        """
        current = {(table, key): dict(row) for table, key, row in rows}
        for patch in self.patches(zone.info.zone_id):
            found = current.get((patch["table"], str(patch["key"])))
            if found is None or patch.get("deleted"):
                continue
            found.update(patch["values"])
        return [(table, key, current[(table, key)]) for table, key, _row in rows]

    def _resolve_rows(self, zone: Zone, op: dict) -> list[dict]:
        """One row op (move/remove of a server row) -> a recorded patch."""
        table = str(op.get("table", ""))
        key = str(op.get("key", ""))
        if table not in serverrows.KEY_COLUMNS or not key:
            raise EditorError("a row op needs a patchable table and the row's key")
        found = serverrows.read_rows_by_key(self._read_connect(), table, [key])
        if key not in found:
            raise EditorError(f"{table}: no row with key {key!r} in the server's database")
        stored = found[key]
        current = self._journal_rows(zone, [(table, key, stored)])[0][2]
        columns = serverrows.COLUMNS[table]
        if op["kind"] == "row-remove":
            return [{"table": table, "key": key, "values": {}, "deleted": True,
                     "originals": {name: str(stored.get(name, "")) for name in columns}}]
        try:
            x, y = float(op["x"]), float(op["y"])
        except (KeyError, TypeError, ValueError):
            raise EditorError("row-move needs x and y") from None
        # The op is zone-local, the row it moves is not: the terrain is sampled
        # where the viewer clicked, the row is written in world coordinates.
        left, top = zone.info.world_rect[0], zone.info.world_rect[1]
        # A moved row keeps its height *above the terrain*: a spawn placed on
        # the ground stays on it, a gate keeps sitting on its wall. An op that
        # names a `z` means it (the editor's height gesture): then that is the
        # height, because the user asked for one.
        if op.get("z") is not None:
            try:
                z = float(op["z"])
            except (TypeError, ValueError):
                raise EditorError("row-move: z must be a number") from None
        else:
            offset = float(current["Z"]) - zone.height_at(float(current["X"]) - left,
                                                          float(current["Y"]) - top)
            z = zone.height_at(x, y) + offset
        values: dict[str, str] = {
            "X": _number_text(round(left + x)), "Y": _number_text(round(top + y)),
            "Z": _number_text(round(z)),
        }
        if "Heading" in columns and op.get("heading") is not None:
            values["Heading"] = str(int(op["heading"]))
        return [{"table": table, "key": key, "values": values,
                 "originals": {name: str(stored.get(name, "")) for name in values}}]

    def undo(self, zone_id: int) -> dict | None:
        """Drop the zone's last op — exact, because ops are additive.

        Server-row patches undo first (they are the more recent kind); a
        patch that was already *written* to the database is refused: undo
        must never leave a live change with no journal entry behind it.
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        patches = journal["patches"].get(str(zone_id)) or []
        if patches:
            if patches[-1].get("applied"):
                raise EditorError(
                    "the last edit is already written to the server's database: "
                    "use revert (it puts the recorded originals back)")
            dropped = patches.pop()
            if not patches:
                journal["patches"].pop(str(zone_id))
            self._save()
            return dropped
        ops = journal["zones"].get(str(zone_id)) or []
        if not ops:
            return None
        dropped = ops.pop()
        if not ops:
            journal["zones"].pop(str(zone_id))
        self._save()
        return dropped

    # -- the server's database (patches) ---------------------------------------

    def _connect_for(self) -> Callable[[], Any]:
        if self.config.db_config is None:
            raise EditorError(
                "editing the server's own rows needs its database: "
                "start the daemon with --db-config <serverconfig.xml>")
        if self._connection is None:
            self._connection = serverrows.parse_server_config(str(self.config.db_config))
        return lambda: self._connect(self._connection)

    def _read_connect(self) -> Callable[[], Any]:
        """A connection maker for the reads (originals, validation)."""
        return self._connect_for()

    def _write_connect(self) -> Callable[[], Any]:
        """A connection maker for the writes — behind the explicit flag."""
        if not self.config.db_write:
            raise EditorError(
                "writing the server's own rows needs --db-write: patches and "
                "removals are recorded and reversible, but never silent")
        return self._connect_for()

    def _write_patches(self, patches: list[dict]) -> int:
        """Apply the journal's patches to the database, in one pass."""
        connect_ = self._write_connect()
        updates = [patch for patch in patches if not patch.get("deleted")]
        removals = [patch for patch in patches if patch.get("deleted")]
        written = 0
        if updates:
            written += serverrows.apply_patches(connect_, updates)
        for patch in removals:
            written += serverrows.delete_rows(connect_, patch["table"], [patch["key"]])
        for patch in patches:
            patch["applied"] = True
        self._save()
        return written

    def _restore_patches(self, patches: list[dict]) -> None:
        """Put recorded originals back: a patch's values, a removal's row —
        the per-patch view of a revert, kept for the tests' own reach."""
        connect_ = self._write_connect()
        updates = [{"table": patch["table"], "key": patch["key"],
                    "values": patch["originals"]}
                   for patch in patches if not patch.get("deleted")]
        if updates:
            serverrows.apply_patches(connect_, updates)
        by_table: dict[str, list[dict]] = {}
        for patch in patches:
            if patch.get("deleted"):
                by_table.setdefault(patch["table"], []).append(patch["originals"])
        for table, rows in by_table.items():
            serverrows.insert_rows(connect_, table, rows)

    def revert(self) -> int:
        """Undo everything: the journal empties, the originals stand again.

        The rows the session wrote go back to what they held when the session
        *first* touched them (the ledger — a publish in between does not move
        the target), a removal re-inserts the whole row, then the client ops go
        and the build outputs with them. The *installed* client half is
        untouched: uninstall reads the client's own state, and `restore` is the
        call that does both. Returns the rows put back.
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        patches = [patch for entries in journal["patches"].values() for patch in entries]
        ledger = journal.get("originals") or {}
        written = len([patch for patch in patches if patch.get("applied")])
        if written:
            self._restore_originals(ledger, patches)
        journal["zones"] = {}
        journal["patches"] = {}
        journal["originals"] = {}
        self._save()
        self._clear_build(str(journal["mod"].get("id", "world-edits")))
        return written

    def _restore_originals(self, ledger: dict, patches: list[dict]) -> None:
        """Put the session's first originals back, per row.

        The ledger is the truth; a patch recorded before the ledger existed
        falls back to its own `originals`, which is the best it knows. A row
        removed and moved in the same session is re-inserted once.
        """
        connect_ = self._write_connect()
        updates: list[dict] = []
        inserts: dict[str, dict[str, dict]] = {}
        for patch in patches:
            if not patch.get("applied"):
                continue
            table, key = patch["table"], patch["key"]
            values = dict(ledger.get(f"{table}:{key}") or patch["originals"])
            if patch.get("deleted"):
                inserts.setdefault(table, {})[key] = values
                continue
            values.pop(serverrows.KEY_COLUMNS.get(table), None)
            if values:
                updates.append({"table": table, "key": key, "values": values})
        # one UPDATE per row, and the *first* patch of a row wins: without a
        # ledger that is the oldest — closest to what the session started from
        seen: set[tuple[str, str]] = set()
        unique = []
        for update in updates:
            name = (update["table"], update["key"])
            if name in seen:
                continue
            seen.add(name)
            unique.append(update)
        if unique:
            serverrows.apply_patches(connect_, unique)
        for table, rows in inserts.items():
            serverrows.insert_rows(connect_, table, list(rows.values()))

    def mod_id(self) -> str:
        """The mod this session is: its own `mod.json`, else its directory."""
        if not self.available:
            return ""
        return str(self._load()["mod"].get("id") or self.dir.name or "world-edits")

    def client_state(self) -> EditorClient:
        """The client's own half, as the client itself records it (D6).

        A failure to read it is not an error here: the state is shown, and
        `restore` is where a refusal has to be raised.
        """
        from .. import clientinstall

        mod_id = self.mod_id()
        try:
            state = clientinstall.status(str(self.config.root))
        except (clientinstall.InstallError, OSError):
            return EditorClient(mod=mod_id)
        for install in state["installs"]:
            if install.get("mod") == mod_id:
                return EditorClient(
                    mod=mod_id, installed=True, version=str(install.get("version") or ""),
                    claims=[str(claim) for claim in install.get("claims") or []])
        return EditorClient(mod=mod_id)

    def restore(self) -> tuple[EditorClient, int]:
        """Everything back: the client's own half, then the journal.

        `revert` alone leaves a client that still shows an edit the journal no
        longer knows (the operator hit exactly that on 2026-10-12). The
        uninstall therefore comes *first*: a refusal (the game running, another
        mod needing this one) leaves the session untouched, never half-put-back.
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        removed = self.client_state()
        if removed.installed:
            from .. import clientinstall

            try:
                clientinstall.uninstall(str(self.config.root), removed.mod)
            except clientinstall.InstallError as error:
                raise EditorError(
                    f"the client's half cannot be removed: {error}") from error
        return removed, self.revert()

    def _clear_build(self, mod_id: str) -> None:
        paths = [self.dir / f"{mod_id}.tar.gz", self.dir / "client.patch.json",
                 self.dir / "server.patch.json"]
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
            has_patches = any(journal["patches"].values())
            if not entries and not has_patches:
                raise EditorError("nothing to publish: the journal holds no edit")
            (self.dir / "mod.json").write_text(
                json.dumps(journal["mod"], indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            (self.dir / "client.patch.json").write_text(
                json.dumps({"archive": entries}, indent=2) + "\n", encoding="utf-8")
            (self.dir / "server.patch.json").write_text(
                json.dumps({"patches": journal["patches"]}, indent=1,
                           ensure_ascii=False) + "\n", encoding="utf-8")
            if not entries:
                # A server-only edit (a moved door has no client half): the
                # record is `server.patch.json`, applied by this daemon.
                report = {"client_items": 0}
                pack = None
            else:
                pack = self.dir / f"{journal['mod'].get('id', 'world-edits')}.tar.gz"
                report = modbuild.build_mod_pack(
                    str(self.dir), str(world.config.root), str(pack))
        except (ValueError, OSError) as error:
            raise EditorError(f"build refused: {error}") from error
        return {"pack": str(pack) if pack is not None else None, "entries": entries,
                "files": files, "report": report}

    def apply(self, world) -> dict[str, Any]:
        """Publish: the client half through the installer, the server rows
        through the database — both recorded, both reversible.

        A publish **commits**: the client now carries the journal's ops, so
        they leave it (the next edit starts from what the client holds), and
        the rows it wrote are marked written. Keeping them would make the next
        edit fail against its own work — the operator hit exactly that on
        2026-10-12.
        """
        if not self.available:
            raise EditorError("the daemon was started without --edits <dir>")
        journal = self._load()
        pending = [patch for entries in journal["patches"].values()
                   for patch in entries if not patch.get("applied")]
        client_ops = any(journal["zones"].values())
        if not pending and not client_ops:
            raise EditorError("nothing to apply: the journal is empty")
        if pending and not self.config.db_write:
            # refused *before* any client write, so nothing lands half-applied
            self._write_connect()
        built = self.build(world) if client_ops else {
            "pack": None, "entries": [], "files": [], "report": {}}
        install = None
        if client_ops:
            from .. import clientinstall

            try:
                install = clientinstall.install_pack(str(world.config.root), built["pack"])
            except Exception as error:  # InstallError, and anything the installer refuses
                raise EditorError(f"install refused: {error}") from error
        written = self._write_patches(pending) if pending else 0
        if client_ops:
            # The commit: the client holds them now, and the marker tells the
            # panel what the last publish covered.
            journal = self._load()
            journal["published"] = {
                "ops": sum(len(ops) for ops in journal["zones"].values()),
                "at": time.time(),
            }
            journal["zones"] = {}
            self._save()
        return {**built, "install": install, "patches": written}


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
            resolved, fixture, patches = active.record(zone, op)
        except EditorError as error:
            raise http(error) from error
        except ZoneError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        row = WorldFixture(**vars(fixture)) if fixture is not None else None
        return EditorEdit(op=resolved, fixture=row, patches=patches)

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

    @router.post("/world/mod/restore", response_model=EditorRestore)
    def world_mod_restore() -> EditorRestore:
        """Everything back: the client's installed half and the journal."""
        try:
            active = ensure()
            removed, rows = active.restore()
        except EditorError as error:
            raise http(error) from error
        return EditorRestore(client=removed, rows=rows, state=active.state())

    return router
