"""The journal: one append-only shard per author-session (phase P1).

Four rules, and what each one buys:

- **one file per author-session** (`journal/<at>-<author>.jsonl`), appended to
  and never rewritten: two people editing at once create two files, and git
  merges them as a union (measured 2026-10-15: two shards merge without a
  conflict, two appends to *one* shard do conflict — which is why a shard is
  never shared);
- **the order is total**: by the op's own timestamp, ties broken by the
  shard's name, and each shard keeps its `seq` order inside. A release records
  the ops it covers as a **cut** (`shard -> last seq`), so "pending" is
  computed and never assumed;
- **a real collision is refused, by name**: `replay()` applies the ops in that
  order and requires each op's `expect` to be what the state holds at that
  point. Two sessions that moved two fixtures merge without a word; two blind
  edits on *one* fixture are refused with the shard, the line, the row and
  both values. Never silently ordered, never merged;
- **history is never erased**: `drop` names an op (`shard` + `seq`) and the
  replay skips it. Undo and revert append a drop; the op stays in the file.

A line is one JSON object, and everything it means is inside it — a carried
server row travels with the fixture op that carried it, with the originals it
replaced, so a merged journal can be read back without a second file:

```json
{"kind": "shard", "format": "camelot.journal/1", "author": "michael",
 "at": "2026-10-15T10:12:03Z", "base": {"gamedata.mpk": "sha256:…"}}
{"kind": "op", "seq": 1, "at": "2026-10-15T10:13:40Z", "family": "world",
 "zone": 0, "op": {"action": "set", "id": 1, "columns": {...},
                   "values": {...}, "expect": {...}}, "rows": [{...}]}
{"kind": "drop", "seq": 2, "at": "2026-10-15T10:20:00Z",
 "target": {"shard": "2026-10-15T10-12-03-michael.jsonl", "seq": 1}}
```
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "FAMILIES",
    "FORMAT",
    "LEGACY_FILE",
    "LEGACY_SCHEMA",
    "Journal",
    "JournalError",
    "Line",
    "migrate_legacy",
    "now",
    "pending_keys",
    "replay",
    "stamp_from_epoch",
]

#: The shard format tag: a reader that does not know it refuses the file, by
#: name. A silent misread of a journal is the most expensive bug a project can
#: have.
FORMAT = "camelot.journal/1"

#: The single file 6.3 wrote instead of shards (`camelot serve --edits`), and
#: the schema it carried. `migrate_legacy()` reads it once and moves it aside.
LEGACY_FILE = "journal.json"
LEGACY_SCHEMA = "camelot.editor/1"

#: What a line can be about. `world` is a zone's placements (a fixture op, with
#: the server rows it carries); `rows` is a server row edited on its own;
#: `tables` is a `gamedata` row; `rules` records a manual step. The families
#: are the release's two destinations, told apart (`vision.md` §4).
FAMILIES = ("world", "rows", "tables", "assets", "rules")

_SHARD_SUFFIX = ".jsonl"


class JournalError(ValueError):
    """A malformed shard, a bad line, or a refusal to write one."""


def now(moment: str | None = None) -> str:
    """The one timestamp shape the journal writes: UTC, seconds, `…Z`."""
    if moment:
        return str(moment)
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stamp_from_epoch(value: Any) -> str | None:
    """`time.time()` → the journal's `…Z` stamp, or None when it is not one.
    (A 6.3 session stamped its journal with a float.)"""
    if value is None:
        return None
    try:
        moment = datetime.fromtimestamp(float(value), UTC)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp_of(path: Path) -> str:
    try:
        return stamp_from_epoch(path.stat().st_mtime) or now()
    except OSError:
        return now()


def _stamp_for_name(moment: str) -> str:
    """`2026-10-15T10:12:03Z` -> `2026-10-15T10-12-03`: a filename a Windows
    share accepts, and one an eye can sort."""
    stamp = re.sub(r"[^0-9A-Za-z]+", "-", str(moment)).strip("-")
    return stamp.removesuffix("Z")


#: `<stamp>-<author>-<n>.jsonl` — the second session of one second.
_INDEXED_SHARD = re.compile(r"^(?P<stem>.+)-(?P<index>\d+)\.jsonl$")


def _shard_order(shard: str) -> tuple[str, int]:
    """The order two shards of one stamp are read in: the one opened first.

    `X-author.jsonl` comes before `X-author-2.jsonl` — and *lexicographically*
    it does not ("-" sorts before "."), so two sessions in the same second
    would replay the later one's ops first. That is a chain replayed out of
    order, which refuses or, worse, applies. The index is compared as a number
    instead; a name that carries none is the first.
    """
    found = _INDEXED_SHARD.match(shard)
    if found is None:
        return (shard, 0)
    return (f"{found.group('stem')}{_SHARD_SUFFIX}", int(found.group("index")))


@dataclass(frozen=True)
class Line:
    """One line of one shard, with where it came from — a refusal must be able
    to say *which* line of *which* file."""

    shard: str
    number: int
    kind: str
    data: dict

    @property
    def seq(self) -> int:
        return int(self.data.get("seq") or 0)

    @property
    def at(self) -> str:
        return str(self.data.get("at") or "")

    @property
    def family(self) -> str:
        return str(self.data.get("family") or "")

    @property
    def zone(self) -> int | None:
        value = self.data.get("zone")
        return int(value) if value is not None else None

    @property
    def op(self) -> dict:
        return dict(self.data.get("op") or {})

    @property
    def rows(self) -> list[dict]:
        return [dict(row) for row in (self.data.get("rows") or ())]

    @property
    def target(self) -> dict:
        return dict(self.data.get("target") or {})

    @property
    def key(self) -> str:
        """`<shard>:<seq>` — how a drop names this op, and how a log reads."""
        return f"{self.shard}:{self.seq}"

    @property
    def where(self) -> str:
        return f"{self.shard} line {self.number}"


@dataclass
class Journal:
    """The shards of one project's journal, plus this session's own.

    Reading is free of the project: a directory and, for writing, the author
    and base a new shard is opened with.
    """

    directory: Path
    author: str = "unknown"
    base: dict = field(default_factory=dict)
    at: str | None = None
    _lines: list[Line] | None = None
    _session: Path | None = None

    def __post_init__(self) -> None:
        self.directory = Path(self.directory)

    # -- reading ---------------------------------------------------------------

    @property
    def paths(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(path for path in self.directory.glob(f"*{_SHARD_SUFFIX}") if path.is_file())

    def lines(self) -> list[Line]:
        """Every line of every shard, in file order — the history as written."""
        if self._lines is None:
            parsed: list[Line] = []
            for path in self.paths:
                parsed.extend(self._read(path))
            self._lines = parsed
        return list(self._lines)

    def _read(self, path: Path) -> list[Line]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise JournalError(f"{path}: {error}") from error
        found: list[Line] = []
        for number, raw in enumerate(text.splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise JournalError(f"{path} line {number}: {error}") from error
            if not isinstance(data, dict):
                raise JournalError(f"{path} line {number}: expected a JSON object")
            kind = str(data.get("kind") or "")
            if kind not in ("shard", "op", "drop"):
                raise JournalError(
                    f"{path} line {number}: a line is a shard header, an op or a drop, "
                    f"not {kind or '(no kind)'}"
                )
            if kind == "shard":
                self._check_header(path, number, data)
                if number != 1:
                    raise JournalError(
                        f"{path} line {number}: the shard header is the first line of the file"
                    )
            elif not found or found[0].kind != "shard":
                raise JournalError(f"{path} line {number}: this shard has no header line")
            found.append(Line(shard=path.name, number=number, kind=kind, data=data))
        if found and not found[0].kind == "shard":
            raise JournalError(f"{path}: this shard has no header line")
        return found

    def _check_header(self, path: Path, number: int, data: dict) -> None:
        found = str(data.get("format") or "")
        if found != FORMAT:
            raise JournalError(
                f"{path} line {number}: shard format {found or '(none)'} is not one this "
                f"build reads (it reads {FORMAT})"
            )

    def ops(self) -> list[Line]:
        """The op lines in the journal's **total order**: by timestamp, ties
        broken by shard name, each shard's own `seq` order inside."""
        return [line for line in self.ordered() if line.kind == "op"]

    def ordered(self, kinds=("op", "drop")) -> list[Line]:
        """Every numbered line in the total order — the one order a merge,
        a build and a report all read the same way."""
        return sorted(
            (line for line in self.lines() if line.kind in kinds),
            key=lambda line: (line.at, _shard_order(line.shard), line.seq),
        )

    def drops(self) -> list[Line]:
        return [line for line in self.lines() if line.kind == "drop"]

    def dropped(self) -> set[str]:
        """The ops a drop names, as `<shard>:<seq>` keys."""
        return {
            f"{line.target.get('shard', '')}:{line.target.get('seq', '')}" for line in self.drops()
        }

    def live(self) -> list[Line]:
        """The ops that still count: everything no drop names."""
        dropped = self.dropped()
        return [line for line in self.ops() if line.key not in dropped]

    def cut(self) -> dict[str, int]:
        """The journal's own cut: the last numbered line of every shard — a
        drop is a change like any other, so it is covered too. A release
        records the cut it holds, and `pending()` reads the rest."""
        cut: dict[str, int] = {}
        for line in self.lines():
            if line.kind == "shard":
                continue
            cut[line.shard] = max(cut.get(line.shard, 0), line.seq)
        return cut

    def pending(self, cut: dict[str, int] | None = None) -> list[Line]:
        """What no release covers yet: every line (an op, a drop) beyond the
        cut, in the total order. A drop below the cut is *not* pending — it is
        part of what the release holds."""
        covered = dict(cut or {})
        return [line for line in self.ordered() if line.seq > covered.get(line.shard, 0)]

    def touches(self, zone: int | None = None, family: str | None = None) -> list[Line]:
        return [
            line
            for line in self.live()
            if (zone is None or line.zone == zone) and (family is None or line.family == family)
        ]

    def live_except(self, keys=()) -> list[Line]:
        """The live lines that no key in *keys* covers — what a set of
        already-published lines leaves pending, in the total order."""
        covered = set(keys)
        return [line for line in self.live() if line.key not in covered]

    # -- writing ---------------------------------------------------------------

    def session(self, *, at: str | None = None) -> Path:
        """This session's shard, created with its header on first use.

        One file per author-session, never shared: that is what makes two
        people's work merge as a union instead of a conflict."""
        if self._session is not None:
            return self._session
        moment = now(at or self.at)
        name = f"{_stamp_for_name(moment)}-{self.author}{_SHARD_SUFFIX}"
        path = self.directory / name
        if path.exists():
            # Two sessions in the same second: keep them apart rather than
            # share a file (a shared shard is the one thing that conflicts).
            index = 2
            while path.exists():
                path = (
                    self.directory
                    / f"{_stamp_for_name(moment)}-{self.author}-{index}{_SHARD_SUFFIX}"
                )
                index += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        header = {
            "kind": "shard",
            "format": FORMAT,
            "author": self.author,
            "at": moment,
            "base": dict(self.base),
        }
        path.write_text(json.dumps(header, ensure_ascii=False) + "\n", encoding="utf-8")
        self._session = path
        self._lines = None
        return path

    def append(self, data: dict, *, at: str | None = None) -> Line:
        """Append one line to this session's shard, numbered in that shard."""
        path = self.session()
        seq = 1 + max(
            (line.seq for line in self.lines() if line.shard == path.name and line.kind != "shard"),
            default=0,
        )
        line = {"kind": data.pop("kind", "op"), "seq": seq, "at": now(at or self.at), **data}
        if line["kind"] not in ("op", "drop"):
            raise JournalError(f"a line is an op or a drop, not {line['kind']!r}")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._lines = None
        return Line(shard=path.name, number=self._count(path), kind=line["kind"], data=line)

    def _count(self, path: Path) -> int:
        return len(path.read_text(encoding="utf-8").splitlines())

    def append_op(
        self,
        op: dict,
        *,
        zone: int,
        family: str = "world",
        rows: Iterable[dict] | None = None,
        at: str | None = None,
    ) -> Line:
        """One authored op, with the server rows it carries (they travel with
        it: a door that follows a moved keep is *one* fact)."""
        if family not in FAMILIES:
            raise JournalError(f"a family is one of {', '.join(FAMILIES)}, not {family!r}")
        data: dict[str, Any] = {"family": family, "zone": int(zone), "op": dict(op)}
        carried = [dict(row) for row in (rows or ())]
        if carried:
            data["rows"] = carried
        return self.append(data, at=at)

    def append_rows(self, patches: Iterable[dict], *, zone: int, at: str | None = None) -> Line:
        """Server rows edited on their own (`rows` family)."""
        rows = [dict(row) for row in patches]
        if not rows:
            raise JournalError("a rows line carries at least one row")
        return self.append({"family": "rows", "zone": int(zone), "rows": rows}, at=at)

    def drop(self, target: Line, *, at: str | None = None) -> Line:
        """Undo, as history: the op stays in the file and the replay skips it."""
        return self.append(
            {"kind": "drop", "target": {"shard": target.shard, "seq": target.seq}}, at=at
        )

    def drop_all(self, *, at: str | None = None) -> int:
        """Revert: one drop per live op, in order — never an erase."""
        live = self.live()
        for line in live:
            self.drop(line, at=at)
        return len(live)

    def invalidate(self) -> None:
        """Forget what was read: the next reader re-reads the files (a `git
        pull` brings ops, and the daemon must see them)."""
        self._lines = None


def _zone_key(key: Any) -> int:
    try:
        return int(key)
    except (TypeError, ValueError):
        raise JournalError(f"{LEGACY_FILE}: {key!r} is not a zone id") from None


def migrate_legacy(
    directory: str | os.PathLike,
    *,
    author: str = "unknown",
    base: dict | None = None,
    at: str | None = None,
) -> dict | None:
    """The 6.3 single-file journal (`journal.json`) → one shard, **once**.

    Lossless: every op becomes a line, every row patch a line of its own (with
    the originals it already carried), and the ops a publish had already
    covered come back as `applied` — the deploy *facts* are the project's
    business, not this module's. The legacy file is **moved aside** as
    `journal.json.migrated`, never deleted: a session is never read from two
    places, and an operator who wants to look can.

    Returns the report — `dir`, `shard`, `ops`, `rows`, `applied` (line keys),
    `published` (the op count 6.3 recorded), `at` — or None when there is
    nothing to migrate. A file whose schema this build does not know is refused
    **by name**.
    """
    root = Path(directory)
    path = root / LEGACY_FILE
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise JournalError(f"{path}: {error}") from error
    if not isinstance(document, dict) or document.get("schema") != LEGACY_SCHEMA:
        found = document.get("schema") if isinstance(document, dict) else None
        raise JournalError(
            f"{path}: this journal is {found!r}; this build understands "
            f"{LEGACY_SCHEMA} and migrates it to shards"
        )
    moment = now(at or _stamp_of(path))
    journal = Journal(root / "journal", author=author, base=dict(base or {}), at=moment)
    zones = document.get("zones") or {}
    patches = document.get("patches") or {}
    ops = 0
    rows = 0
    applied: list[str] = []
    for key in sorted(zones, key=_zone_key):
        for op in zones.get(key) or ():
            journal.append_op(op, zone=_zone_key(key), at=moment)
            ops += 1
    for key in sorted(patches, key=_zone_key):
        for patch in patches.get(key) or ():
            line = journal.append_rows([patch], zone=_zone_key(key), at=moment)
            rows += 1
            if patch.get("applied"):
                applied.append(line.key)
    published = document.get("published") or {}
    path.rename(root / f"{LEGACY_FILE}.migrated")
    block = document.get("mod")
    return {
        "dir": str(root),
        "shard": journal.session().name,
        "ops": ops,
        "rows": rows,
        "applied": applied,
        "published": int(published.get("ops") or 0),
        "published_at": stamp_from_epoch(published.get("at")),
        #: The mod identity the 6.3 file carried. `mod.json` answers first in
        #: the new model, so a caller that *imports* keeps this one.
        "mod": dict(block) if isinstance(block, dict) else {},
        "at": moment,
    }


def migration_facts(report: dict) -> list[dict]:
    """The deploy-log lines a migration owes.

    A 6.3 file had already *published* some of its ops — they were cleared from
    the file, so they cannot become shard lines, only a fact — and the
    migration itself is one. The caller appends these where its log lives
    (`project.record_deploy`).
    """
    facts: list[dict] = []
    if report["applied"] or report["published"]:
        facts.append(
            {
                "kind": "publish",
                "ops": report["published"],
                "keys": list(report["applied"]),
                "legacy": True,
                "at": report["published_at"] or report["at"],
            }
        )
    facts.append(
        {
            "kind": "migrated",
            "from": LEGACY_FILE,
            "ops": report["ops"],
            "rows": report["rows"],
            "published": report["published"],
            "shard": report["shard"],
            "at": report["at"],
        }
    )
    return facts


def replay(
    rows: dict[int, dict[str, str]],
    ops: Iterable[dict],
    *,
    where: Iterable[str] | None = None,
) -> tuple[dict[int, dict[str, str]], list[dict]]:
    """Apply world ops to a zone's rows, refusing a blind edit **by name**.

    *rows* maps a fixture's id to its own column values (the shape the zone's
    `fixtures.csv` has: every value a string, the way the tables spell them).
    *ops* are the op bodies in journal order; *where* names each one's origin
    (`shard line N`) so a refusal can be read and acted on.

    Returns the resulting rows and the findings — empty when every op's
    `expect` matched what the state held at its own point in the order. The
    replay **stops at the first refusal**: later ops were written against a
    state that never happened, so pretending to apply them would be a lie.
    """
    result = {key: dict(value) for key, value in rows.items()}
    labels = list(where or ())
    findings: list[dict] = []

    def refuse(op: dict, index: int, detail: str) -> None:
        findings.append(
            {
                "op": op,
                "where": labels[index] if index < len(labels) else "",
                "detail": detail,
            }
        )

    for index, op in enumerate(ops):
        action = str(op.get("action") or "")
        try:
            row_id = int(op.get("id"))
        except (TypeError, ValueError):
            refuse(op, index, "an op must name the row it edits")
            break
        values = {str(key): str(value) for key, value in (op.get("values") or {}).items()}
        expect = {str(key): str(value) for key, value in (op.get("expect") or {}).items()}
        current = result.get(row_id)

        if action == "add":
            if current is not None:
                refuse(
                    op,
                    index,
                    f"row {row_id} is already there: an add that lands on it "
                    "would overwrite a row it never read",
                )
                break
            result[row_id] = values
            continue
        if current is None:
            refuse(op, index, f"row {row_id} is gone")
            break
        if not expect:
            refuse(
                op,
                index,
                f"row {row_id} already exists, so the op must carry the expect "
                "it was written against",
            )
            break
        wrong = {
            column: (value, current.get(column))
            for column, value in expect.items()
            if str(current.get(column, "")) != value
        }
        if wrong:
            detail = "; ".join(
                f"{column}: the op expects {wanted!r}, the row holds {found!r}"
                for column, (wanted, found) in sorted(wrong.items())
            )
            refuse(op, index, f"row {row_id} moved under this op — {detail}")
            break
        if action == "remove":
            result.pop(row_id, None)
        else:
            result[row_id] = {**current, **values}
    return result, findings


def pending_keys(
    journal: Journal,
    *,
    published: Iterable[str] = (),
    cut: dict[str, int] | None = None,
) -> set[str]:
    """The keys of the lines a working copy still holds beyond what is written
    (`project.md` §D5).

    A release's `cut` is the coarse answer — every line beyond it is pending,
    and a drop below it is not, since it is part of what that release carries.
    While no release exists, the finer one is the ops no publish covers yet.
    One rule, read by the editor and by the re-base.
    """
    if cut is None:
        return {line.key for line in journal.live_except(published)}
    dropped = journal.dropped()
    return {line.key for line in journal.pending(cut) if line.key not in dropped}
