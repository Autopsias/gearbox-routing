"""Tests for codex_watchdog.py — no-OUTPUT hang detection (OR-02, S05 2026-07-03).

Run: pytest scripts/test_codex_watchdog.py -q  (from ~/.claude)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_watchdog as wd  # noqa: E402


def test_missing_file_reports_missing(tmp_path):
    raw = tmp_path / "codex.raw.md"
    result = wd.check(raw, state={})
    assert result["status"] == "missing"


def test_first_observation_is_growing(tmp_path):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("some output so far")
    result = wd.check(raw, now=1000.0, state={})
    assert result["status"] == "growing"
    assert result["size"] == len("some output so far")


def test_growth_since_last_check_is_growing(tmp_path):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("x" * 100)
    prev_state = {"size": 50, "last_growth_at": 900.0}
    result = wd.check(raw, now=1000.0, state=prev_state)
    assert result["status"] == "growing"


def test_no_growth_under_window_is_stalled_ok(tmp_path):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("x" * 100)
    prev_state = {"size": 100, "last_growth_at": 950.0}
    result = wd.check(raw, now=1000.0, no_output_window_s=600.0, state=prev_state)
    assert result["status"] == "stalled_ok"
    assert result["stalled_for_seconds"] == 50.0


def test_no_growth_over_window_is_hung(tmp_path):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("x" * 100)
    # last growth 700s ago, window is 600s -> hung.
    prev_state = {"size": 100, "last_growth_at": 300.0}
    result = wd.check(raw, now=1000.0, no_output_window_s=600.0, state=prev_state)
    assert result["status"] == "hung"
    assert result["stalled_for_seconds"] == 700.0


def test_hung_reproduces_the_1h43m_incident_shape(tmp_path):
    # The actual incident: 0 bytes growth for 1h43m (6180s) at 0% CPU.
    raw = tmp_path / "codex.raw.md"
    raw.write_text("[codex] starting review...\n")
    prev_state = {"size": raw.stat().st_size, "last_growth_at": 0.0}
    result = wd.check(raw, now=6180.0, no_output_window_s=600.0, state=prev_state)
    assert result["status"] == "hung"


def test_check_persists_sidecar_across_calls(tmp_path):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("abc")
    r1 = wd.check(raw, now=1000.0)  # no state override -> uses/writes sidecar
    assert r1["status"] == "growing"
    # No growth on the second call -> stalled_ok (reads the sidecar just written).
    r2 = wd.check(raw, now=1010.0, no_output_window_s=600.0)
    assert r2["status"] == "stalled_ok"
    assert r2["stalled_for_seconds"] == 10.0
    wd.reset(raw)
    assert not wd._sidecar_path(raw).exists()


def test_cli_check_exit_code_hung_is_nonzero(tmp_path, capsys):
    raw = tmp_path / "codex.raw.md"
    raw.write_text("x")
    wd.reset(raw)
    # Prime the sidecar with an old growth timestamp by calling check twice
    # through the pure function with an injected state, then via CLI-shaped
    # call to confirm main()'s exit-code contract.
    import json
    import time

    sidecar = wd._sidecar_path(raw)
    sidecar.write_text(json.dumps({"size": raw.stat().st_size, "last_growth_at": time.time() - 700}))
    code = wd.main(["check", str(raw), "--no-output-window-s", "600"])
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "hung"
    wd.reset(raw)
