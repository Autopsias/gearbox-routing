"""RV-02 (session-review-two-rounds s03): the size check runs before any reviewer."""
import json

import llm_review_gate as g
import review_size_check as rsc
from test_llm_review_gate import _repo


def _run(tmp_path, monkeypatch, capsys, limit, enforce=True, accept=None, rerun=False):
    repo = _repo(tmp_path)  # one new file, 2 lines
    p = tmp_path / "_plans" / "p"
    vs = p / ("_verify" + "_state")
    vs.mkdir(parents=True, exist_ok=True)
    (p / "run.ndjson").write_text(
        '{"event": "dispatch_started", "session_ids": ["s01"], "ts": "2026-08-20T00:00:00+00:00"}\n')
    if accept:
        (vs / "s01.accepted.json").write_text(json.dumps({"accepted": [accept]}))
    monkeypatch.setattr(rsc, "LIMIT_LINES", limit)
    monkeypatch.setattr(rsc, "ENFORCE", enforce)
    started = []
    monkeypatch.setattr(g, "run_once", lambda prompt, cwd, timeout: (
        started.append(1) or {"result": "REVIEWED_FILES: 1\n```json\n[]\n```"}, ""))
    monkeypatch.setattr(g, "reviewed_count", lambda r: 1)
    argv = ["--level", "low", "--cwd", str(repo), "--plan-dir", str(p), "--session", "s01"]
    rc = g.main(argv)
    if rerun:
        rc = g.main(argv)
    led = "".join(f.read_text() for f in vs.glob("*.findings.ndjson"))
    return rc, capsys.readouterr(), led, len(started)


def test_I1_over_the_limit_no_acceptance_starts_no_reviewer(tmp_path, monkeypatch, capsys):
    rc, cap, _, started = _run(tmp_path, monkeypatch, capsys, limit=1)
    assert (rc, started) == (g.INDETERMINATE, 0)
    assert "limit 1" in cap.err and "split" in cap.err and "escalate" in cap.err


def test_I2_under_the_limit_the_gate_runs_as_today(tmp_path, monkeypatch, capsys):
    rc, _, _, started = _run(tmp_path, monkeypatch, capsys, limit=100)
    assert (rc, started) == (g.PASS, 1)


def test_I3_an_acceptance_lets_the_review_run_and_stays_in_the_ledger(tmp_path, monkeypatch, capsys):
    rc, _, led, started = _run(tmp_path, monkeypatch, capsys, limit=1,
                               accept={"size": True, "reason": "one cohesive module"})
    assert (rc, started) == (g.PASS, 1)
    assert '"kind": "size_accepted"' in led and "one cohesive module" in led


def test_I4_attempt_two_never_trips_the_check(tmp_path, monkeypatch, capsys):
    # attempt 1 passes under a high limit; attempt 2 re-runs the same tree over a tiny one
    _run(tmp_path, monkeypatch, capsys, limit=100)
    monkeypatch.setattr(rsc, "LIMIT_LINES", 1)
    (tmp_path / "brand_new.py").write_text("def f():\n    return 1\n")
    g.main(["--level", "low", "--cwd", str(tmp_path), "--plan-dir", str(tmp_path / "_plans" / "p"),
                 "--session", "s01"])
    assert "surface size" not in capsys.readouterr().err


def test_not_enforced_prints_and_runs(tmp_path, monkeypatch, capsys):
    rc, cap, _, started = _run(tmp_path, monkeypatch, capsys, limit=1, enforce=False)
    assert (rc, started) == (g.PASS, 1) and "printed only" in cap.out
