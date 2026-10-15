"""The team test: two clones, a pull, and what a merge does to a journal.

The *semantic* half lives in `test_journal.py` (a blind edit is refused by
name, two sessions that touched two rows merge without a word). This is the
**git** half — the one P1's exit criteria name:

- two people work in their own clone; the shards they write are two files, so
  a pull merges them as a union and neither loses a line;
- the deploy log merges as a union too (its `.gitattributes` rule), because its
  lines are independent facts and the order is read from the timestamps;
- what the merged journal then *refuses* is the same refusal as ever: a line
  whose `expect` no longer matches the state at its own point in the order.

Nothing here needs a client: a project without a base is a project.
"""

import subprocess

import pytest

from camelot import journal as jr
from camelot import project as pj


def git(*args: str, cwd) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def have_git() -> bool:
    return subprocess.run(["git", "--version"], capture_output=True, check=False).returncode == 0


pytestmark = pytest.mark.skipif(not have_git(), reason="git is not installed")


def identity(root) -> None:
    """Every clone commits as somebody: the author is who signs the shards."""
    git("config", "user.email", "operator@example.invalid", cwd=root)
    git("config", "user.name", "operator", cwd=root)


def op(row_id: int, x: str, expect: str) -> dict:
    return {
        "action": "set",
        "id": row_id,
        "columns": {"X": 3, "Y": 4},
        "values": {"X": x, "Y": "100"},
        "expect": {"X": expect, "Y": "100"},
    }


def write_op(root, author: str, at: str, body: dict) -> jr.Line:
    """One session's own shard, the way the daemon writes it."""
    journal = jr.Journal(root / "journal", author=author, at=at)
    return journal.append_op(body, zone=0, family="world", at=at)


def test_two_clones_merge_their_shards_and_their_deploy_log(tmp_path):
    origin = tmp_path / "origin"
    pj.new(origin, author="michael")
    git("init", "-q", "--initial-branch=main", cwd=origin)
    identity(origin)
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "the project", cwd=origin)

    michael = tmp_path / "michael"
    lyra = tmp_path / "lyra"
    git("clone", "-q", str(origin), str(michael), cwd=tmp_path)
    git("clone", "-q", str(origin), str(lyra), cwd=tmp_path)
    for clone in (michael, lyra):
        identity(clone)

    # two sessions, two shards, two facts — in two clones
    write_op(michael, "michael", "2026-10-16T09:00:00Z", op(1, "20", "10"))
    pj.record_deploy(michael, {"kind": "publish", "ops": 1, "keys": []}, at="2026-10-16T09:05:00Z")
    git("add", "-A", cwd=michael)
    git("commit", "-qm", "michael: move fixture 1", cwd=michael)

    git("pull", "-q", "--no-edit", "--no-rebase", str(michael), "main", cwd=lyra)
    assert (lyra / "journal" / "2026-10-16T09-00-00-michael.jsonl").is_file()
    write_op(lyra, "lyra", "2026-10-16T11:00:00Z", op(2, "40", "30"))
    pj.record_deploy(lyra, {"kind": "publish", "ops": 1, "keys": []}, at="2026-10-16T11:05:00Z")
    git("add", "-A", cwd=lyra)
    git("commit", "-qm", "lyra: move fixture 2", cwd=lyra)

    # the pull back: two shards, two log lines, and neither a conflict nor a loss
    git("pull", "-q", "--no-edit", "--no-rebase", str(lyra), "main", cwd=michael)
    assert not (michael / "journal" / "2026-10-16T11-00-00-lyra.jsonl").read_text().count("<<<<<<<")
    journal = jr.Journal(michael / "journal")
    assert [line.op["id"] for line in journal.live()] == [1, 2]  # in the timestamp order
    assert [line.shard.split("-")[-1][:5] for line in journal.live()] == [
        "micha",
        "lyra.",
    ]
    written = [entry["at"] for entry in pj.deploy_log(michael) if entry.get("kind") == "publish"]
    assert written == ["2026-10-16T09:05:00Z", "2026-10-16T11:05:00Z"]  # the union kept both
    # and the merged journal is one the build can read: two rows, two results
    rows, findings = jr.replay(
        {1: {"X": "10", "Y": "100"}, 2: {"X": "30", "Y": "100"}},
        [line.op for line in journal.live()],
        where=[line.where for line in journal.live()],
    )
    assert findings == [] and rows[1]["X"] == "20" and rows[2]["X"] == "40"


def test_a_pulled_line_that_moved_the_same_row_blind_is_refused(tmp_path):
    """The other half of the team story: two sessions that touched *one* fixture
    without seeing each other. The merge is clean (two files, again) and the
    refusal comes where it belongs — reading the journal back, by name."""
    origin = tmp_path / "origin"
    pj.new(origin, author="michael")
    git("init", "-q", "--initial-branch=main", cwd=origin)
    identity(origin)
    git("add", "-A", cwd=origin)
    git("commit", "-qm", "the project", cwd=origin)

    michael = tmp_path / "michael"
    lyra = tmp_path / "lyra"
    git("clone", "-q", str(origin), str(michael), cwd=tmp_path)
    git("clone", "-q", str(origin), str(lyra), cwd=tmp_path)
    for clone in (michael, lyra):
        identity(clone)

    write_op(michael, "michael", "2026-10-16T09:00:00Z", op(7, "20", "10"))
    git("add", "-A", cwd=michael)
    git("commit", "-qm", "michael", cwd=michael)

    # Lyra, who never saw that line, edits the same fixture from the same basis
    write_op(lyra, "lyra", "2026-10-16T10:00:00Z", op(7, "99", "10"))
    git("add", "-A", cwd=lyra)
    git("commit", "-qm", "lyra", cwd=lyra)

    git("pull", "-q", "--no-edit", "--no-rebase", str(lyra), "main", cwd=michael)
    journal = jr.Journal(michael / "journal")
    assert len(journal.live()) == 2
    rows, findings = jr.replay(
        {7: {"X": "10", "Y": "100"}},
        [line.op for line in journal.live()],
        where=[line.where for line in journal.live()],
    )
    assert rows[7]["X"] == "20"  # the first line landed
    assert len(findings) == 1
    assert findings[0]["where"] == "2026-10-16T10-00-00-lyra.jsonl line 2"
    assert "row 7 moved under this op" in findings[0]["detail"]
    assert "'10'" in findings[0]["detail"] and "'20'" in findings[0]["detail"]
