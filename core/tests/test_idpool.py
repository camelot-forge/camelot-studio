"""The no-patch id pool: what a mod may add, and what it may take over.

The client's model table is 2500 entries wide and a row at 2500 truncates the
parse (measured in the workspace, 2026-10-10). Inside that ceiling, two kinds
of ids are usable: ids with no row at all (add), and shipped rows nothing
references (take over). The first is a client fact, the second a server one —
`camelot.modelusage` carries the measured server list.
"""
import pytest

from camelot import modelusage


def test_the_used_models_text_parses_numbers_ranges_and_comments():
    used = modelusage.parse_used_models("# a comment\n1-3; 7, 9\n11")
    assert used == {1, 2, 3, 7, 9, 11}


def test_a_reversed_range_is_refused():
    with pytest.raises(ValueError, match="reversed"):
        modelusage.parse_used_models("9-3")


def test_unreadable_tokens_are_refused():
    with pytest.raises(ValueError, match="unreadable"):
        modelusage.parse_used_models("crab")


def test_the_pool_splits_free_ids_from_reclaimable_rows():
    free, reclaimable = modelusage.pool([2, 4, 9], {4}, max_id=10)
    assert free == [1, 3, 5, 6, 7, 8, 10]
    assert reclaimable == [2, 9]        # row 4 is referenced, so not reclaimable


def test_the_shipped_list_is_the_measured_one():
    used = modelusage.used_model_ids()
    assert 592 in used                      # the sand crab: referenced
    assert 2069 not in used                 # measured free: a gap
    assert 2493 not in used                 # the table's last row: unreferenced
    assert len(used) == 1770                # npctemplate + mob + the C# scan


def test_the_shipped_list_can_be_replaced():
    assert modelusage.used_model_ids("5") == {5}


@pytest.mark.usefixtures("client_dir")
def test_the_real_client_pool_matches_the_measurement(client_dir):
    """253 ids with no row and 674 shipped rows nothing references."""
    ids = modelusage.client_row_ids(client_dir)
    free, reclaimable = modelusage.pool(ids, modelusage.used_model_ids(), 2499)
    assert len(free) == 253
    assert len(reclaimable) == 674
    assert 2069 in free


@pytest.mark.usefixtures("client_dir")
def test_the_cli_reports_the_pool(client_dir, capsys):
    from camelot import cli

    assert cli.main(["client", "free-ids", "--client", client_dir]) == 0
    out = capsys.readouterr().out
    assert "free to add" in out and "253" in out
    assert "safe to take over" in out and "674" in out
