"""The editor: a zone's placements, edited and published (spec §6/§9, 6.3).

D2: the daemon is the only writer, the browser a pure consumer. An editing
session is *the project's own directory* (`camelot serve --project DIR`, the
design in `workspace/notes/`): `mod.json` names it, `journal/`
holds the ops as **one append-only shard per author-session** (the format, the
total order and the refusals live in `camelot/journal.py`),
`deploy/log.jsonl` records what was *published*, and `build` assembles the
same `fixtures.csv` + `client.patch.json` + pack the `camelot mod build`
command line would — the editor is a front end to the toolchain, never a
parallel implementation.

**Nothing about a session is mutable on disk.** The ops are appended to their
shard; a publish is a *fact* recorded in the deploy log (the keys it covered);
and what is *pending* is computed from the two — the client and the server's
rows carry everything a publish covered, so the panel shows the rest. A legacy
single-file `journal.json` (6.3) is migrated **once**, on first read, and moved
aside: a session is never read from two places.

The ops are zone-local, as `fixtures.csv` is:

- `move` writes `X` and `Y`, and `Z` only for a row that does **not** carry
  `Ground = 1`: the client drops a ground row on the terrain whatever its `Z`
  says (W-3, measured in game 2026-10-12), so the editor never invents one;
- `rotate` writes `A` (degrees), and `X`/`Y` when the op names them: the
  editor's rotate gesture turns the whole selection about its own pivot, so a
  fixture *moves* as it turns and the rows it carries must ride that frame;
  `scale` writes `Scale` (percent);
- `add` builds the whole row from the zone's own catalog (`nifs.csv`) — the
  catalogue's radius and ground flag are the client's own hints. A
  ground-placed entry is written `z = 0, Ground = 1` (the client's own
  convention); anything else lands at the terrain height under the point;
- `remove` drops the row and its line.

Every op but `add` carries `expect`, the fields as the user saw them, so an
edit made against a changed client is refused with both values named, never
applied blind. Undo **appends a drop** (history is never erased) and the replay
skips it: ops are additive on the base table, so dropping is exact. `remove`
exists only in the journal — the build assembles whole files, so no row op ever
reaches an installer.

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

**The mouse grammar (the UX pass of 2026-10-12, its second cut).** Left is
selection first: a click picks, a box takes every fixture and every server row
whose own point falls inside it (what is drawn is what is caught), ctrl adds,
shift+click toggles. The **mode buttons** (select, move, rotate, scale, add)
arm the left button *on the selection*: a drag that starts on it applies the
mode, a drag anywhere else is still a box. The **middle button** applies the
same gesture from anywhere — move, shift+middle rotate (15°, about the
selection's own pivot), alt+middle lift (16), ctrl+middle scale (5%). One
gesture is one transform, computed from the items' own origins and quantized
once (a snapped *delta*, never each item's position), so a selection keeps its
shape exactly; the daemon hears one op per changed item, and a gesture that ends
where it started journals nothing. Right orbits, shift+right or shift+left
pans, the wheel zooms, F focuses, Del removes, Escape lets go. The editor lives
in its own floating, draggable panel over the scene (the viewer stays a viewer),
and the panel reads like a repository: the publish button carries the count of
unpublished changes, and the session, the written rows and the *client's own
installed half* are named separately.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import gamedata, modbuild, serverrows
from .. import journal as journal_mod
from .. import project as project_mod
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

#: The **legacy** single-file journal's schema tag (6.3). Kept as an alias of
#: the kernel's own constant, so a build has one place to know it.
SCHEMA = journal_mod.LEGACY_SCHEMA

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
        self._journal: journal_mod.Journal | None = None
        self._connection: dict[str, Any] | None = None
        #: injected by the tests; production opens MySQL through `serverrows`.
        self._connect = connect_ or serverrows.connect

    @property
    def dir(self) -> Path | None:
        return self.config.project

    @property
    def available(self) -> bool:
        return self.dir is not None

    # -- the journal on disk ---------------------------------------------------

    def _mod(self) -> dict[str, Any]:
        meta = self.dir / "mod.json"
        if meta.is_file():
            document = json.loads(meta.read_text(encoding="utf-8"))
            if isinstance(document, dict):
                return document
        return {"id": self.dir.name or "world-edits", "version": "1", "priority": 100}

    def _base(self) -> dict:
        """The base fingerprints a new shard records it was written against
        (`project.json` when the directory is a project, nothing when it is
        still a bare authoring directory)."""
        try:
            return project_mod.load(self.dir).fingerprints
        except project_mod.ProjectError:
            return {}

    def _holder(self) -> str:
        """Who holds this checkout, as a phrase a refusal and the panel both
        use (`project.md` §D13)."""
        try:
            state = project_mod.lock_state(self.dir)
        except project_mod.ProjectError:
            state = None
        if state is None:
            return "another process"
        return f"{state.author} (pid {state.pid} on {state.host})"

    def _writable(self) -> None:
        """Refuse a write when this daemon does not hold the project's lock
        (`project.md` §D13): the lock is about one *checkout*, and a second
        writer would produce two journals for one session."""
        if not self.config.read_only:
            return
        raise EditorError(
            f"this project is held by {self._holder()}: this daemon is read-only — "
            "close the other one, or stop it and reload"
        )

    def journal(self) -> journal_mod.Journal:
        """The session's journal: one shard per author-session, under
        `<dir>/journal/`. Read on first use, and the legacy single file
        migrated away on the way in."""
        if self._journal is None:
            self.migrate()
            self._journal = journal_mod.Journal(
                self.dir / "journal",
                author=project_mod.author_name(self.dir),
                base=self._base(),
            )
        return self._journal

    # -- what a publish covered, and what is pending ---------------------------

    def publish_lines(self) -> list[dict]:
        """Every publish this project recorded, oldest first."""
        return [
            entry for entry in project_mod.deploy_log(self.dir) if entry.get("kind") == "publish"
        ]

    def published_keys(self) -> set[str]:
        """The journal lines a publish covered: the client's half and the
        server's rows carry them, so they are not pending any more."""
        keys: set[str] = set()
        for entry in self.publish_lines():
            keys.update(str(key) for key in entry.get("keys") or ())
        return keys

    def published(self) -> dict:
        """What the panel shows about the last publish (the op count at that
        moment, so a session ahead of its client half reads differently from a
        published one) — legacy publishes of a migrated session included."""
        lines = self.publish_lines()
        if not lines:
            return {}
        last = lines[-1]
        return {"ops": int(last.get("ops") or 0), "at": last.get("at")}

    def pending_lines(self) -> list[journal_mod.Line]:
        """The ops no publish covers yet, in the journal's total order."""
        return self.journal().live_except(self.published_keys())

    # -- migrating the legacy single file -------------------------------------- 

    def migrate(self) -> dict | None:
        """`journal.json` (the 6.3 single-file journal) → one shard, once.

        The conversion is `journal.migrate_legacy` (the kernel owns the legacy
        format too, so `camelot project import` can run it without a daemon);
        what this method adds is the *facts*: the ops a publish had already
        covered become a `publish` line in the deploy log, and the migration
        itself is recorded. Returns the `migrated` entry, or None.
        """
        report = journal_mod.migrate_legacy(
            self.dir, author=project_mod.author_name(self.dir), base=self._base()
        )
        if report is None:
            return None
        if report["applied"] or report["published"]:
            project_mod.record_deploy(
                self.dir,
                {
                    "kind": "publish",
                    "ops": report["published"],
                    "keys": report["applied"],
                    "legacy": True,
                },
                at=report["published_at"] or report["at"],
            )
        entry = project_mod.record_deploy(
            self.dir,
            {"kind": "migrated", "from": journal_mod.LEGACY_FILE, "ops": report["ops"],
             "rows": report["rows"], "published": report["published"],
             "shard": report["shard"]},
            at=report["at"],
        )
        self._journal = None
        return entry

    def ops(self, zone_id: int) -> list[dict]:
        """The zone's *pending* fixture ops (what the client does not carry)."""
        if not self.available:
            return []
        return [
            line.op
            for line in self.pending_lines()
            if line.zone == zone_id and line.family == "world" and line.op
        ]

    def patches(self, zone_id: int) -> list[dict]:
        """**Every** row patch this zone's journal holds — pending *and*
        published (the panel shows what the session did, not only what is left
        to write), in the journal's order."""
        if not self.available:
            return []
        return [
            patch
            for line in self.journal().live()
            if line.zone == zone_id
            for patch in line.rows
        ]

    def pending_patches(self) -> list[tuple[journal_mod.Line, dict]]:
        """The row patches no publish covers yet, with the line that carries
        them (the build and the write both need the pairing)."""
        return [
            (line, patch)
            for line in self.pending_lines()
            for patch in line.rows
        ]

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
        journal = self.journal()
        pending = self.pending_lines()
        zones: dict[int, list[dict]] = {}
        for line in pending:
            if line.family == "world" and line.op and line.zone is not None:
                zones.setdefault(line.zone, []).append(line.op)
        # The panel shows what the session did, and says which rows a publish
        # already wrote — a *derived* flag (`applied` was a stored one before
        # P1): the deploy log's keys are the truth, not a mutable marker.
        written = self.published_keys()
        rows: dict[int, list[dict]] = {}
        for line in journal.live():
            if not line.rows or line.zone is None:
                continue
            applied = line.key in written
            rows.setdefault(line.zone, []).extend(
                {**patch, "applied": applied} for patch in line.rows
            )
        mod = self._mod()
        pack = None
        path = self.dir / f"{mod.get('id', 'world-edits')}.tar.gz"
        if path.is_file():
            stat = path.stat()
            pack = EditorPack(pack=str(path), modified=stat.st_mtime, size=stat.st_size)
        return EditorState(
            available=True,
            dir=str(self.dir),
            read_only=bool(self.config.read_only),
            holder=self._holder() if self.config.read_only else "",
            mod=mod,
            zones=[EditorZone(id=zone, ops=ops) for zone, ops in sorted(zones.items())],
            rows=[EditorRowOp(zone=zone, patches=patches) for zone, patches in sorted(rows.items())],
            pack=pack,
            client=self.client_state(),
            published=self.published(),
        )

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
            # A turn about the selection's own pivot moves the fixture as it
            # turns it: an op that names the position it landed on is written
            # with it, or the journal would keep the old one and the rows it
            # carries would turn about a place the fixture no longer stands.
            for field, column in (("x", "X"), ("y", "Y")):
                if op.get(field) is not None:
                    values[column] = _number_text(op[field])
            expect = self._expect(op, value_at, tuple(values))
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
        spent = self._spent_ids(zone.info.zone_id)
        while row_id in spent:  # an id this project spent is never handed out again
            row_id += 1
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
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
        if op.get("kind") in ROW_KINDS:
            patches = self._resolve_rows(zone, op)
            self.journal().append_rows(patches, zone=zone.info.zone_id)
            return {}, None, patches

        current = self.working_table(zone)
        resolved = self._resolve(zone, current, op)
        try:
            after = gamedata.apply_row_ops(current, [resolved])
        except gamedata.TableError as error:
            raise EditorError(str(error)) from error
        patches = self._pair_patches(zone, current, after, resolved, op.get("rows") or [])
        # The rows a fixture op carries ride *inside* its own line: a door that
        # follows a moved keep is one fact, in one place, with its originals.
        self.journal().append_op(resolved, zone=zone.info.zone_id, family="world", rows=patches)
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

    def _ledger(self) -> dict[str, dict]:
        """The values a row had when this *session* first touched it.

        A patch's own `originals` are what the database held before that one
        patch — which stops being the truth once a publish has written the
        session's earlier edits. The ledger is what `revert` restores to, so
        `restore all` means the state the session started from, not the state
        of the last publish. It is **derived** from the journal (the first
        patch of a row, in order, is the one closest to what the session
        started from), never stored beside it.
        """
        ledger: dict[str, dict] = {}
        for line in self.journal().live():
            for patch in line.rows:
                row = ledger.setdefault(f"{patch['table']}:{patch['key']}", {})
                for column, value in (patch.get("originals") or {}).items():
                    row.setdefault(column, value)
        return ledger

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
        """Drop the zone's last pending op — exact, because ops are additive.

        The drop is **appended** (the op stays in the shard, the replay skips
        it: history is never erased). A line a publish covered is refused
        instead: the client and the server's rows carry it, so dropping it
        would leave a live change with no journal entry behind it — `revert`
        is the call that puts back what the session changed.
        """
        if not self.available:
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
        pending = [line for line in self.pending_lines() if line.zone == zone_id]
        if not pending:
            if any(self.published_keys()):
                raise EditorError(
                    "the last edit is published: the client and the server's rows "
                    "carry it — use revert (it puts the recorded originals back)")
            return None
        last = pending[-1]
        self.journal().drop(last)
        return last.op if last.family == "world" and last.op else {"rows": last.rows}

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
        """Undo everything: the journal's ops are dropped, the originals stand again.

        The rows the session wrote go back to what they held when the session
        *first* touched them (the derived ledger — a publish in between does
        not move the target), a removal re-inserts the whole row, then every op
        is dropped (as *history*: the ops stay in their shard and the replay
        skips them) and the build outputs go with them. The *installed* client
        half is untouched: uninstall reads the client's own state, and
        `restore` is the call that does both. Returns the rows put back.
        """
        if not self.available:
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
        journal = self.journal()
        written = len(
            [patch for line in journal.live() if line.key in self.published_keys()
             for patch in line.rows]
        )
        if written:
            self._restore_originals()
        dropped = journal.drop_all()
        project_mod.record_deploy(
            self.dir,
            {"kind": "revert", "ops": dropped, "rows": written},
        )
        self._clear_build(str(self._mod().get("id", "world-edits")))
        return written

    def _restore_originals(self) -> None:
        """Put the session's first originals back, per row, for every row a
        publish actually wrote.

        The ledger is *derived* now (the first patch of a row, in order); a
        patch from a migrated session falls back to its own `originals`, which
        is the best it knows. A row removed and moved in the same session is
        re-inserted once.
        """
        connect_ = self._write_connect()
        ledger = self._ledger()
        published = self.published_keys()
        patches = [
            patch for line in self.journal().live() if line.key in published
            for patch in line.rows
        ]
        updates: list[dict] = []
        inserts: dict[str, dict[str, dict]] = {}
        for patch in patches:
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
        return str(self._mod().get("id") or self.dir.name or "world-edits")

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
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
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
        """Assemble the project's build outputs and run `mod build` on them.

        What is built is what no publish covers yet: the pending world ops,
        per zone (a zone the client half already carries is not rebuilt), and
        the pending row patches. The build is also where the **ids** are owned
        (`project.md` §D8): every `add`'s id is checked against the base it is
        about to be written over and against what this project already dropped,
        and what it allocates is recorded in `ids.json` — so rebuilding the same
        journal allocates the same ids, byte for byte.
        """
        if not self.available:
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
        pending = self.pending_lines()
        by_zone: dict[int, list[journal_mod.Line]] = {}
        for line in pending:
            if line.zone is not None:
                by_zone.setdefault(line.zone, []).append(line)
        patches: dict[str, list[dict]] = {}
        for line, patch in self.pending_patches():
            patches.setdefault(str(line.zone), []).append(patch)
        entries: list[dict] = []
        files: list[str] = []
        try:
            for zone_id in sorted(by_zone):
                zone = world.zone(zone_id)
                lines = by_zone[zone_id]
                self._allocate(zone, lines)
                ops = [line.op for line in lines if line.family == "world" and line.op]
                if not ops:
                    continue
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
            if not entries and not patches:
                raise EditorError("nothing to publish: the journal holds no edit")
            (self.dir / "mod.json").write_text(
                json.dumps(self._mod(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
            (self.dir / "client.patch.json").write_text(
                json.dumps({"archive": entries}, indent=2) + "\n", encoding="utf-8")
            (self.dir / "server.patch.json").write_text(
                json.dumps({"patches": patches}, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8")
            if not entries:
                # A server-only edit (a moved door has no client half): the
                # record is `server.patch.json`, applied by this daemon.
                report = {"client_items": 0}
                pack = None
            else:
                pack = self.dir / f"{self._mod().get('id', 'world-edits')}.tar.gz"
                report = modbuild.build_mod_pack(
                    str(self.dir), str(world.config.root), str(pack))
        except (ValueError, OSError) as error:
            raise EditorError(f"build refused: {error}") from error
        return {"pack": str(pack) if pack is not None else None, "entries": entries,
                "files": files, "report": report}

    def _recorded_ids(self) -> dict:
        """The id record as the build reads it (`project.md` §D8). A malformed
        file is a refusal that names it, never a silent fresh start."""
        try:
            return project_mod.ids(self.dir)
        except project_mod.ProjectError as error:
            raise EditorError(str(error)) from error

    def _spent_ids(self, zone_id: int) -> set[int]:
        """The ids this zone may never hand out again (`project.md` §D8): the
        tombstones, and the allocations whose line is no longer live — the build
        retires those, but a re-add must not be handed one before it runs."""
        recorded = self._recorded_ids()
        spent = {int(item) for item in (recorded.get("tombstones") or {}).get(str(zone_id), ())}
        claimed = (recorded.get("zones") or {}).get(str(zone_id)) or {}
        if claimed:
            live = {line.key for line in self.journal().live() if line.op.get("action") == "add"}
            spent.update(int(row_id) for key, row_id in claimed.items() if key not in live)
        return spent

    def _allocate(self, zone: Zone, lines: list[journal_mod.Line]) -> None:
        """The build's own id bookkeeping (`project.md` §D8).

        An `add` keeps the id the operator saw — re-allocating it silently would
        move a row out from under them — but the **build** is what checks it:
        free in the client's own `fixtures.csv`, claimed by no other line, and
        never one this project spent. What it confirms goes to `ids.json`, keyed
        by the line that asked for it (`<shard>:<seq>`), so a rebuild allocates
        the same ids and a merge never has to touch the file.
        """
        recorded = self._recorded_ids()
        allocated: dict[str, dict[str, int]] = {
            str(key): dict(value) for key, value in (recorded.get("zones") or {}).items()
        }
        tombstones: dict[str, set[int]] = {
            str(key): {int(item) for item in value}
            for key, value in (recorded.get("tombstones") or {}).items()
        }
        zone_key = str(zone.info.zone_id)
        known = dict(allocated.get(zone_key) or {})
        gone = set(tombstones.get(zone_key) or ())
        live = {line.key for line in self.journal().live() if line.op.get("action") == "add"}
        # Retire first: an allocation whose line is gone is spent, whatever the
        # base says later — that is the tombstone.
        retired = [key for key in known if key not in live]
        for key in retired:
            gone.add(known.pop(key))
        adds = [line for line in lines if line.op.get("action") == "add"]
        if adds:
            base_ids = {row.id for row in fixtures_from_table(zone.name, self.base_table(zone))}
            claimed = {row_id: key for key, row_id in known.items()}
            for line in adds:
                row_id = int(line.op["id"])
                if line.key in known:
                    if known[line.key] != row_id:
                        raise EditorError(
                            f"{line.where}: an earlier build recorded id {known[line.key]} for "
                            f"this line, the op now says {row_id} — the journal was edited by hand"
                        )
                    continue  # a rebuild: the allocation stands
                if row_id in base_ids:
                    raise EditorError(
                        f"{line.where}: id {row_id} is taken by the client's own "
                        "fixtures.csv — drop the object and add it again (the base moved)"
                    )
                if row_id in gone:
                    raise EditorError(
                        f"{line.where}: id {row_id} was allocated and dropped by this project "
                        "— an id is never reused: drop the object and add it again"
                    )
                if row_id in claimed:
                    raise EditorError(
                        f"{line.where}: id {row_id} is already allocated to {claimed[row_id]} "
                        "— drop the object and add it again"
                    )
                known[line.key] = row_id
                claimed[row_id] = line.key
        if known:
            allocated[zone_key] = known
        else:
            allocated.pop(zone_key, None)
        if gone:
            tombstones[zone_key] = gone
        else:
            tombstones.pop(zone_key, None)
        if adds or retired:
            project_mod.record_ids(self.dir, allocated, tombstones)

    def apply(self, world) -> dict[str, Any]:
        """Publish: the client half through the installer, the server rows
        through the database — both recorded, both reversible.

        A publish **commits**: the client and the server's rows now carry the
        ops, so they are not pending any more (they stay in the journal as
        history) and the deploy log records which lines it covered. Keeping
        them pending would make the next edit fail against its own work — the
        operator hit exactly that on 2026-10-12.
        """
        if not self.available:
            raise EditorError("the daemon was started without --project <dir>")
        self._writable()
        lines = self.pending_lines()
        client_ops = [
            line for line in lines if line.family == "world" and line.op
        ]
        patches = [patch for _line, patch in self.pending_patches()]
        if not lines:
            if self.published_keys() or self.client_state().installed:
                raise EditorError(
                    "nothing to publish: the client and the server's rows already "
                    "carry this session (revert puts back what it changed)")
            raise EditorError("nothing to publish: the journal holds no edit")
        if patches and not self.config.db_write:
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
        written = self._write_patches(patches) if patches else 0
        # The commit, as a fact in the deploy log: these lines are carried by
        # the client and the rows are written. No op is ever erased.
        project_mod.record_deploy(
            self.dir,
            {"kind": "publish", "ops": len(client_ops), "rows": written,
             "install": bool(install), "keys": [line.key for line in lines]},
        )
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
                       "start the daemon with --project <dir>")
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
