"""The second clear of one session's halt needs a cost line."""
import json
import pytest
import run_state_io as rsi


def _plan(tmp_path):
    (tmp_path / "run_state.json").write_text(json.dumps({"halt": {"set": False}}))
    return tmp_path


def test_first_clear_is_free_second_needs_the_cost_line(tmp_path):
    d = _plan(tmp_path)
    rsi.set_halt(d, "gate failed", "s05")
    rsi.clear_halt(d)                                   # 1st: free
    rsi.set_halt(d, "gate failed again", "s05")
    with pytest.raises(rsi.HaltClearRefused):
        rsi.clear_halt(d)                               # 2nd, no line: refused
    assert rsi.is_halted(d)                             # still halted
    rsi.clear_halt(d, cost_report="2h, 3 passes, diff unchanged; cut: commit body")
    assert not rsi.is_halted(d)
    events = [json.loads(ln) for ln in (d / "run.ndjson").read_text().splitlines()]
    cleared = [e for e in events if e["event"] == "halt_cleared"]
    assert [e["clears"] for e in cleared] == [1, 2]
    assert cleared[1]["cost_report"].startswith("2h")


def test_the_count_is_per_session(tmp_path):
    d = _plan(tmp_path)
    rsi.set_halt(d, "x", "s05")
    rsi.clear_halt(d)
    rsi.set_halt(d, "y", "s06")
    rsi.clear_halt(d)      # a different session: free
    assert not rsi.is_halted(d)


def test_a_stuck_session_needs_a_decision_to_clear(tmp_path):
    d = _plan(tmp_path)
    st = json.loads((d / "run_state.json").read_text())
    st["stuck"] = {"s05": {"consecutive": 2, "attempts": 8, "armable": True}}
    (d / "run_state.json").write_text(json.dumps(st))
    rsi.set_halt(d, "gate failed", "s05")
    with pytest.raises(rsi.HaltClearRefused, match="stuck"):
        rsi.clear_halt(d)                               # even the FIRST clear
    assert rsi.is_halted(d)
    rsi.clear_halt(d, decision="accept the finding on record")
    assert not rsi.is_halted(d)
    ev = [json.loads(ln) for ln in (d / "run.ndjson").read_text().splitlines()]
    assert [e["decision"] for e in ev if e["event"] == "halt_cleared"] == ["accept the finding on record"]


def test_the_third_clear_of_a_review_halt_takes_only_a_checked_exit(tmp_path):
    """2026-11-16 (s04): 12 clears, 13 review runs, each clear a free-text line."""
    import llm_review_ledger as L
    d = _plan(tmp_path)
    st = json.loads((d / "run_state.json").read_text())
    st["halt_clears"] = {"s05": 2, "s06": 2}
    (d / "run_state.json").write_text(json.dumps(st))
    (tmp_path / "a.py").write_text("1")
    L.record(L.context(str(d), "s05.high.codex", str(tmp_path), ["a.py"]), [
        {"kind": "finding", "attempt": 1, "fid": "f1", "file": "a.py", "line": 1,
         "severity": "high", "summary": "crash window", "status": "open"}])
    rsi.set_halt(d, "review failed", "s05")
    with pytest.raises(rsi.HaltClearRefused, match="--exit accept"):
        rsi.clear_halt(d, cost_report="3h", decision="one more review")     # free text: refused
    with pytest.raises(rsi.HaltClearRefused, match="1 open blocking"):
        rsi.clear_halt(d, cost_report="3h", exit_="accept")                 # nothing accepted yet
    assert rsi.is_halted(d)
    (d / "_verify_state" / "s05.accepted.json").write_text(json.dumps({"accepted": [
        {"file": "a.py", "contains": "crash window", "reason": "proved by a test"}]}))
    rsi.clear_halt(d, cost_report="3h", exit_="accept")
    assert not rsi.is_halted(d)

    rsi.set_halt(d, "tests failed", "s06")           # control: not a review halt, no exit needed
    rsi.clear_halt(d, cost_report="1h")
    assert not rsi.is_halted(d)
