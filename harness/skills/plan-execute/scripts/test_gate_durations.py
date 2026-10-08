import json

import gate_durations as gd
import gate_timing as gt


def test_a_logged_gate_run_is_counted_and_old_ones_are_skipped(tmp_path, capsys):
    plan = tmp_path / "_plans" / "p"
    plan.mkdir(parents=True)
    assert gt.log_run(str(plan), "verify", "s01", "tests", 12.34, "pass")
    assert gt.log_run(str(plan), "land", None, "tests", 30, "fail")
    with open(plan / "run.ndjson", "a") as f:
        f.write(json.dumps({"ts": "2000-01-01", "event": "gate_run", "gate": "tests", "seconds": 999}) + "\n")
        f.write("not json\n")
    assert sorted(gd.runs(tmp_path, since="2001")["tests"]) == [12.3, 30.0]
    gd.main([str(tmp_path), "--since", "2001"])
    assert "tests" in capsys.readouterr().out


def test_logging_never_breaks_a_gate(tmp_path):
    assert gt.log_run(str(tmp_path / "missing"), "verify", "s01", "g", 1, "pass") is False
