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
