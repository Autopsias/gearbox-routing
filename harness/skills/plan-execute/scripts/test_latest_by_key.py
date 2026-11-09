"""Latest row per key FIRST, then the filter: an old row must not survive its update."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import llm_review_ledger as L  # noqa: E402
from latest_by_key import latest_then_filter  # noqa: E402


def _is_open(row):
    return row["state"] == "open"


def _fid(row):
    return row["fid"]


def test_a_key_updated_out_of_the_filter_does_not_keep_its_old_row():
    rows = [{"fid": "a", "state": "open"}, {"fid": "a", "state": "fixed"}]
    assert latest_then_filter(rows, _fid, _is_open) == []


def test_a_key_updated_into_the_filter_returns_the_new_row_only():
    old, new = {"fid": "a", "state": "fixed"}, {"fid": "a", "state": "open"}
    assert latest_then_filter([old, new], _fid, _is_open) == [new]


def test_a_tie_goes_to_the_later_row():
    first, second = {"fid": "a", "state": "open", "n": 1}, {"fid": "a", "state": "open", "n": 2}
    assert latest_then_filter([first, second], _fid) == [second]


def test_no_filter_returns_every_key_in_first_seen_order():
    rows = [{"fid": "b", "n": 1}, {"fid": "a", "n": 2}, {"fid": "b", "n": 3}]
    assert latest_then_filter(rows, _fid) == [rows[2], rows[1]]


def test_a_row_the_key_cannot_read_reaches_the_caller():
    with pytest.raises(KeyError):
        latest_then_filter([{"state": "open"}], _fid)


def _finding(fid, attempt, status, severity="high"):
    return {"kind": "finding", "fid": fid, "attempt": attempt, "status": status, "severity": severity}


def test_open_priors_drops_a_finding_fixed_on_a_later_attempt():
    recs = [_finding("a", 1, "open"), _finding("a", 2, "fixed"), _finding("b", 2, "open")]
    assert [p["fid"] for p in L.open_priors(recs)] == ["b"]


def test_open_priors_reads_the_severity_of_the_latest_record():
    recs = [_finding("a", 1, "open", "high"), _finding("a", 2, "open", "low")]
    assert L.open_priors(recs) == []


def test_open_priors_goes_by_attempt_when_the_file_order_disagrees():
    recs = [_finding("a", 2, "fixed"), _finding("a", 1, "open")]
    assert L.open_priors(recs) == []


def test_latest_by_id_keeps_advisory_findings_at_their_latest_record():
    recs = [_finding("a", 1, "open"), _finding("a", 2, "noted", "low")]
    assert L.latest_by_id(recs) == {"a": recs[1]}


@pytest.mark.parametrize("fn", [L.open_priors, L.latest_by_id])
def test_the_ledger_readers_still_raise_on_a_finding_with_no_id(fn):
    with pytest.raises(KeyError):
        fn([{"kind": "finding", "attempt": 1}])
