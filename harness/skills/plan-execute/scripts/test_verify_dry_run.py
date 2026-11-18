"""`verify-begin --dry-run` is a query: it writes no verify state, no event row and
runs no gate, for every gate kind and every prior state. `verify-simulate` keeps
writing real state and events. Fixtures come from test_verify.py."""

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_state_io as rsi  # noqa: E402
import verify as vfy  # noqa: E402
import verify_paths  # noqa: E402
from test_verify import (GATE_SKILL, _begin_doing, _verify_sess, make_plan,  # noqa: E402
                         write_closeout)

ARGV_FAIL = {"gx": {"kind": "argv", "argv": ["false"]}}
ARGV_PASS = {"gx": {"kind": "argv", "argv": ["true"]}}
SKILL = {"gx": GATE_SKILL["rev"]}


def _snapshot(plan_dir):
    """Hash every verify-state file and the event log."""
    h = hashlib.sha256()
    files = sorted((plan_dir / ("_verify" + "_state")).glob("*")) + [plan_dir / "run.ndjson"]
    for f in files:
        h.update(f.name.encode() + (f.read_bytes() if f.exists() else b"<absent>"))
    return h.hexdigest()


def _prior(plan_dir, kind, prior):
    """Bring the session to `prior` through the real calls."""
    if prior == "none":
        return
    vfy.verify_begin(plan_dir, "s01")
    if prior == "pending":
        return
    if kind == "skill":
        vfy.verify_record(plan_dir, "s01", "gate:gx", "done" if prior == "passed" else "fail")
    else:
        vfy.verify_run_argv(plan_dir, "s01", "gate:gx")
    if prior == "rework_fresh":
        write_closeout(plan_dir, "s01")


CELLS = [(k, p, r) for k in ("argv", "skill")
         for p in ("none", "pending", "rework", "rework_fresh", "passed") for r in (False, True)]


@pytest.mark.parametrize("block_writers", [False, True])
@pytest.mark.parametrize("kind,prior,resume", CELLS)
def test_dry_run_is_a_query(tmp_path, monkeypatch, kind, prior, resume, block_writers):
    if kind == "skill":
        gates = SKILL
    else:
        gates = ARGV_PASS if prior == "passed" else ARGV_FAIL
    plan_dir = make_plan(tmp_path, [_verify_sess(["gx"])], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    _prior(plan_dir, kind, prior)

    before = _snapshot(plan_dir)
    if block_writers:
        def boom(*a, **k):
            raise AssertionError("the query reached a writer")
        for mod, name in ((vfy, "_save_state"), (verify_paths, "_save_state"), (rsi, "log_event")):
            monkeypatch.setattr(mod, name, boom)
    asked = vfy.verify_begin(plan_dir, "s01", dry_run=True, resume=resume)
    assert _snapshot(plan_dir) == before
    monkeypatch.undo()

    real = vfy.verify_begin(plan_dir, "s01", resume=resume)
    assert asked == real
    if prior == "passed":
        assert real["action"] == "already-verified"
    elif prior == "rework":
        assert real["action"] == "stale-closeout"
    elif prior == "pending" and not resume:
        assert real["action"] == "requires-resume"
    else:
        assert real["action"] == ("invoke-skill" if kind == "skill" else "run-argv")


def test_the_gate_a_dry_run_names_really_fails(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["gx"])], gates=ARGV_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    assert vfy.verify_begin(plan_dir, "s01", dry_run=True)["action"] == "run-argv"
    assert vfy.verify_begin(plan_dir, "s01")["action"] == "run-argv"
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:gx")
    assert out["action"] != "passed"
    assert vfy._load_state(plan_dir, "s01")["gate_status"]["gate:gx"] != "passed"


def test_simulate_still_writes_state_and_events(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["gx"])], gates=ARGV_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    assert vfy.verify_simulate(plan_dir, "s01")["action"] == "done"
    assert vfy._load_state(plan_dir, "s01")["outcome"] == "passed"
    assert "verify_started" in (plan_dir / "run.ndjson").read_text()



def test_dry_run_reports_an_edited_locked_check_and_writes_nothing(tmp_path):
    from test_locked_checks import LOCKED, _plan
    plan_dir, root, _marker = _plan(tmp_path)
    import locked_checks as lc
    import manifest_io as mio
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    f = root / LOCKED
    f.write_text(f.read_text().replace("2\n", "3\n"))
    write_closeout(plan_dir, "s01")
    before = _snapshot(plan_dir)
    asked = vfy.verify_begin(plan_dir, "s01", dry_run=True)
    assert _snapshot(plan_dir) == before
    assert asked["refusal"] == lc.LOCKED_CHECK_EDITED and asked["edited"][0][0] == LOCKED
    assert vfy.verify_begin(plan_dir, "s01")["refusal"] == asked["refusal"]   # the real run gives the same refusal
