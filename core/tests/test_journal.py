"""The journal's shards: the team's concurrency model, and the refusals.

Design: `workspace/notes/project.md` §D5 and §D15. These tests
are the *semantic* half of the team test (two authors, two shards, a merge that
either goes through without a word or is refused by name); the git half — two
clones, a pull — is a harness, not a unit test.
"""

import json

import pytest

from camelot import journal as jr


def op(row_id: int, x: str, expect_x: str | None = None) -> dict:
    """A minimal world op: move a fixture's X, as the editor resolves it."""
    body = {"action": "set", "id": row_id, "columns": {"X": 0}, "values": {"X": x}}
    if expect_x is not None:
        body["expect"] = {"X": expect_x}
    return body


def test_a_shard_is_opened_with_a_header_and_numbered_inside_itself(tmp_path):
    """One file per author-session, with the author and the base it was written
    against in the header — a journal read months later must say who wrote it
    and what they were looking at."""
    journal = jr.Journal(
        tmp_path / "journal",
        author="michael",
        base={"gamedata.mpk": "sha256:aa"},
        at="2026-10-15T10:12:03Z",
    )
    first = journal.append_op(op(1, "10", "5"), zone=0)
    second = journal.append_op(op(2, "20", "5"), zone=0)
    assert first.shard == "2026-10-15T10-12-03-michael.jsonl"
    assert first.seq == 1 and second.seq == 2
    lines = (tmp_path / "journal" / first.shard).read_text().splitlines()
    header = json.loads(lines[0])
    assert header == {
        "kind": "shard",
        "format": jr.FORMAT,
        "author": "michael",
        "at": "2026-10-15T10:12:03Z",
        "base": {"gamedata.mpk": "sha256:aa"},
    }
    assert [json.loads(line)["seq"] for line in lines[1:]] == [1, 2]


def test_two_sessions_in_the_same_second_do_not_share_a_shard(tmp_path):
    """A shared shard is the one thing git cannot merge: the second session
    gets its own file."""
    one = jr.Journal(tmp_path / "journal", author="michael", at="2026-10-15T10:00:00Z")
    two = jr.Journal(tmp_path / "journal", author="lyra", at="2026-10-15T10:00:00Z")
    assert one.session().name == "2026-10-15T10-00-00-michael.jsonl"
    assert two.session().name == "2026-10-15T10-00-00-lyra.jsonl"


def test_the_order_is_total_by_timestamp_then_shard_then_seq(tmp_path):
    """A merged journal is read in one order everywhere: the timestamp first,
    the shard's name to break a tie, the shard's own seq inside."""
    michael = jr.Journal(tmp_path / "journal", author="michael", at="2026-10-15T12:00:00Z")
    michael.append_op(op(1, "10", "5"), zone=0, at="2026-10-15T12:00:00Z")
    lyra = jr.Journal(tmp_path / "journal", author="lyra", at="2026-10-15T11:00:00Z")
    lyra.append_op(op(2, "20", "5"), zone=0, at="2026-10-15T11:00:00Z")
    lyra.append_op(op(3, "30", "5"), zone=0, at="2026-10-15T12:00:00Z")
    reader = jr.Journal(tmp_path / "journal")
    read = [(line.shard.split("-m")[-1][:1], line.seq, line.op["id"]) for line in reader.ops()]
    assert [item[2] for item in read] == [2, 3, 1]  # 11:00, then the 12:00 tie by shard
    assert [(line.op["id"], line.seq) for line in reader.ops()] == [(2, 1), (3, 2), (1, 1)]


def test_a_cut_names_the_last_seq_of_every_shard_and_pending_is_the_rest(tmp_path):
    """A release covers a *cut*, so "what is not released yet" is computed from
    the shards — never stored in a file two branches would both rewrite."""
    michael = jr.Journal(tmp_path / "journal", author="michael", at="2026-10-15T10:00:00Z")
    michael.append_op(op(1, "10", "5"), zone=0)
    michael.append_op(op(2, "20", "5"), zone=0)
    lyra = jr.Journal(tmp_path / "journal", author="lyra", at="2026-10-15T11:00:00Z")
    lyra.append_op(op(3, "30", "5"), zone=0)
    reader = jr.Journal(tmp_path / "journal")
    cut = reader.cut()
    assert cut == {
        "2026-10-15T10-00-00-michael.jsonl": 2,
        "2026-10-15T11-00-00-lyra.jsonl": 1,
    }
    assert reader.pending(cut) == []
    lyra.append_op(op(4, "40", "5"), zone=0)  # a later op in the same session
    reader.invalidate()  # what a pull brings is read when a reader asks again
    assert [line.op["id"] for line in reader.pending(cut)] == [4]
    assert len(reader.pending()) == 4  # no cut at all: nothing is released yet


def test_a_drop_removes_an_op_from_the_state_but_not_from_history(tmp_path):
    """Undo and revert append a drop; the op stays in the file, which is what
    makes the history readable and a merge safe."""
    journal = jr.Journal(tmp_path / "journal", author="michael", at="2026-10-15T10:00:00Z")
    keep = journal.append_op(op(1, "10", "5"), zone=0)
    undo_me = journal.append_op(op(2, "20", "5"), zone=0)
    journal.drop(undo_me)
    assert [line.key for line in journal.ops()] == [keep.key, undo_me.key]
    assert [line.key for line in journal.live()] == [keep.key]
    assert journal.dropped() == {undo_me.key}
    # a drop is a change too: the cut covers it, so no release can pretend not to
    assert journal.cut() == {"2026-10-15T10-00-00-michael.jsonl": 3}
    assert [line.kind for line in journal.pending({"2026-10-15T10-00-00-michael.jsonl": 2})] == [
        "drop"
    ]


def test_drop_all_reverts_and_is_itself_history(tmp_path):
    journal = jr.Journal(tmp_path / "journal", author="michael")
    journal.append_op(op(1, "10", "5"), zone=0)
    journal.append_op(op(2, "20", "5"), zone=0)
    assert journal.drop_all() == 2
    assert journal.live() == []
    assert len(journal.drops()) == 2


def test_a_refusal_names_the_shard_the_line_and_both_values(tmp_path):
    """The whole point: a blind edit is refused, and the message is one an
    operator can act on."""
    michael = jr.Journal(tmp_path / "journal", author="michael", at="2026-10-15T10:00:00Z")
    michael.append_op(op(7, "10", "5"), zone=0, at="2026-10-15T10:00:00Z")
    lyra = jr.Journal(tmp_path / "journal", author="lyra", at="2026-10-15T11:00:00Z")
    lyra.append_op(op(7, "99", "5"), zone=0, at="2026-10-15T11:00:00Z")
    reader = jr.Journal(tmp_path / "journal")
    lines = reader.live()
    rows, findings = jr.replay(
        {7: {"X": "5"}}, [line.op for line in lines], where=[line.where for line in lines]
    )
    assert rows[7]["X"] == "10"  # the first op landed
    assert len(findings) == 1
    refused = findings[0]
    assert refused["where"].startswith("2026-10-15T11-00-00-lyra.jsonl line ")
    assert "row 7 moved under this op" in refused["detail"]
    assert "'5'" in refused["detail"] and "10" in refused["detail"]


def test_two_sessions_that_touched_two_rows_merge_without_a_word(tmp_path):
    """The common case, and the reason the format is sharded: parallel work on
    different things is not a conflict."""
    michael = jr.Journal(tmp_path / "journal", author="michael")
    michael.append_op(op(1, "10", "5"), zone=0, at="2026-10-15T10:00:00Z")
    lyra = jr.Journal(tmp_path / "journal", author="lyra")
    lyra.append_op(op(2, "20", "5"), zone=0, at="2026-10-15T11:00:00Z")
    reader = jr.Journal(tmp_path / "journal")
    lines = reader.live()
    rows, findings = jr.replay(
        {1: {"X": "5"}, 2: {"X": "5"}},
        [line.op for line in lines],
        where=[line.where for line in lines],
    )
    assert findings == []
    assert (rows[1]["X"], rows[2]["X"]) == ("10", "20")


def test_a_later_session_that_saw_the_first_is_not_a_conflict(tmp_path):
    """A keep moved twice, by two sessions, in order: the second one's expect
    names what the first left, so the chain applies."""
    first = jr.Journal(tmp_path / "journal", author="michael")
    first.append_op(op(1, "10", "5"), zone=0, at="2026-10-15T10:00:00Z")
    second = jr.Journal(tmp_path / "journal", author="lyra")
    second.append_op(op(1, "40", "10"), zone=0, at="2026-10-16T09:00:00Z")
    reader = jr.Journal(tmp_path / "journal")
    lines = reader.live()
    rows, findings = jr.replay({1: {"X": "5"}}, [line.op for line in lines])
    assert findings == [] and rows[1]["X"] == "40"


def test_an_op_on_a_row_that_exists_must_carry_its_expect(tmp_path):
    """Half the safety is here: an op that does not say what it was written
    against cannot be checked, so it is refused rather than trusted."""
    rows, findings = jr.replay({3: {"X": "5"}}, [op(3, "10")])
    assert rows[3]["X"] == "5" and len(findings) == 1
    assert "must carry the expect" in findings[0]["detail"]


def test_an_add_that_lands_on_an_existing_row_is_refused(tmp_path):
    """An id is not free just because the session that chose it did not see the
    row another session added."""
    add = {"action": "add", "id": 9, "columns": {"X": 0}, "values": {"X": "1"}}
    rows, findings = jr.replay({9: {"X": "5"}}, [add])
    assert rows[9]["X"] == "5" and len(findings) == 1
    assert "already there" in findings[0]["detail"]


def test_a_remove_checks_what_it_removes(tmp_path):
    remove = {"action": "remove", "id": 4, "columns": {"X": 0}, "expect": {"X": "5"}}
    rows, findings = jr.replay({4: {"X": "5"}}, [remove])
    assert rows == {} and findings == []
    rows, findings = jr.replay({4: {"X": "6"}}, [remove])
    assert 4 in rows and len(findings) == 1


def test_a_malformed_line_is_refused_with_its_file_and_line(tmp_path):
    """Never skip silently: a journal that cannot be read must say where."""
    directory = tmp_path / "journal"
    directory.mkdir()
    (directory / "2026-10-15T10-00-00-michael.jsonl").write_text(
        json.dumps({"kind": "shard", "format": jr.FORMAT, "author": "m", "at": "x"}) + "\n{oh no\n"
    )
    with pytest.raises(jr.JournalError) as error:
        jr.Journal(directory).lines()
    assert "line 2" in str(error.value)


def test_a_shard_without_a_header_is_refused_by_name(tmp_path):
    directory = tmp_path / "journal"
    directory.mkdir()
    (directory / "shard.jsonl").write_text(json.dumps({"kind": "op", "seq": 1, "at": "x"}) + "\n")
    with pytest.raises(jr.JournalError, match="no header line"):
        jr.Journal(directory).lines()


def test_a_shard_a_build_does_not_know_is_refused_by_name(tmp_path):
    directory = tmp_path / "journal"
    directory.mkdir()
    (directory / "shard.jsonl").write_text(
        json.dumps({"kind": "shard", "format": "camelot.journal/9", "at": "x"}) + "\n"
    )
    with pytest.raises(jr.JournalError) as error:
        jr.Journal(directory).lines()
    assert "camelot.journal/9" in str(error.value) and jr.FORMAT in str(error.value)


def test_the_rows_an_op_carries_travel_inside_its_own_line(tmp_path):
    """A door that follows a moved keep is one fact: the patch, with the
    originals it replaced, rides in the line that carried it."""
    journal = jr.Journal(tmp_path / "journal", author="michael")
    patch = {"table": "door", "key": "1", "values": {"X": "9"}, "originals": {"X": "8"}}
    line = journal.append_op(op(1, "10", "5"), zone=0, rows=[patch])
    assert line.rows == [patch]
    assert Journal_reads_its_own(tmp_path) == [patch]


def Journal_reads_its_own(tmp_path):
    reader = jr.Journal(tmp_path / "journal")
    return [row for line in reader.touches(family="world") for row in line.rows]


def test_a_row_edited_on_its_own_is_its_own_line(tmp_path):
    journal = jr.Journal(tmp_path / "journal", author="michael")
    journal.append_rows([{"table": "mob", "key": "abc", "values": {"X": "1"}}], zone=0)
    reader = jr.Journal(tmp_path / "journal")
    patch = reader.touches(family="rows")[0].rows[0]
    assert patch["table"] == "mob" and patch["key"] == "abc"
    empty = jr.Journal(tmp_path / "empty", author="michael")
    with pytest.raises(jr.JournalError, match="at least one row"):
        empty.append_rows([], zone=0)


def test_a_family_this_build_does_not_know_is_refused(tmp_path):
    journal = jr.Journal(tmp_path / "journal", author="michael")
    with pytest.raises(jr.JournalError, match="a family is one of"):
        journal.append_op(op(1, "1", "0"), zone=0, family="spells")


def test_invalidate_re_reads_what_a_pull_brought(tmp_path):
    """The daemon must see ops that arrived by `git pull` without a restart."""
    # The clock is pinned on purpose: the order is (at, shard, seq), and a
    # wall clock that drifts past the teammate's timestamp flips it — this
    # test failed for real the day after it was written (2026-10-16, 21:07Z).
    journal = jr.Journal(tmp_path / "journal", author="michael",
                         at="2026-10-16T07:00:00Z")
    journal.append_op(op(1, "10", "5"), zone=0)
    reader = jr.Journal(tmp_path / "journal")
    assert len(reader.ops()) == 1
    other = jr.Journal(tmp_path / "journal", author="lyra", at="2026-10-16T08:00:00Z")
    other.append_op(op(2, "20", "5"), zone=0)
    assert len(reader.ops()) == 1  # still what it read
    reader.invalidate()
    assert [line.op["id"] for line in reader.ops()] == [1, 2]
