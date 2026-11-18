"""GAT-01 — the registry class decides what carries over a moved main; a carried gate
is copied, never invoked. Split from ``test_land_carry.py`` (file-size limit), whose
fixtures it imports.

    pytest skills/plan-execute/scripts/test_land_gate_split.py -q
"""

import json
import time

import pytest

import land_carry as lc
import land_state as lst
from _land_fixture import build_repo, isolate
from test_land_carry import TEST, REVIEW, OLD, SLUG, _state

CI_CARRY = {**TEST, "phase2": "carry"}
HOURS = 3600


_REAL_OPEN = getattr(lc, "_phase2_open", None)


@pytest.fixture(autouse=True)
def _phase2_window(monkeypatch):
    """Most tests are about the class rule, so phase 2 is open; the mode test restores the real check."""
    monkeypatch.setattr(lc, "_phase2_open", lambda st: True, raising=False)


def _ci_gates(**by_id):
    st = _state()
    st["argv_gates"] = {gid: {"gate_id": gid, "outcome": "pass", "gate_key": OLD,
                              "findings_count": 0, "full_at": lst.now(), **rec}
                        for gid, rec in by_id.items()}
    return st


def _apply_ci(tmp_path, st, gates):
    fx = build_repo(tmp_path)
    iso = isolate(fx, SLUG)
    lc.apply(iso["plan_dir"], lst.context(iso["plan_dir"]), st, gates, "new",
             {"decision": "carry", "reason": None, "classes": {}, "basis": {"main_head": "M2"}})
    return st


def test_apply_carries_only_gates_the_registry_marks_carry_and_never_invokes_them(
        tmp_path, monkeypatch):
    import land_gate as lg  # noqa: PLC0415
    st = _ci_gates(**{"ci-check": {}, "fast": {}, "unknown": {}, "missing": {}})
    gates = [("ci-check", CI_CARRY), ("fast", {**TEST, "phase2": "fast"}),
             ("unknown", {**TEST, "phase2": "sometimes"}), ("missing", TEST)]
    _apply_ci(tmp_path, st, gates)
    assert set(st["argv_gates"]) == {"ci-check"}        # unknown and missing re-run: they are fast
    rec = st["argv_gates"]["ci-check"]
    assert (rec["carried_from"], rec["carried_n"], rec["gate_key"]) == (OLD, 1, "new")
    assert st["carried_gates"] == ["ci-check"] and st["carried_from_main"] == "M1"
    assert st["ran_gates"] == ["fast", "missing", "unknown"]
    monkeypatch.setattr(lg, "_run_argv_step", lambda *a: pytest.fail("a carried gate was run"))
    assert lg._cached_pass(st, "ci-check", "new") is rec   # what step_gate asks before any run
    assert lg._cached_pass(st, "fast", "new") is None      # a fast gate has nothing to replay


def _in_mode(monkeypatch, mode, phase):
    import land_phase2 as lp2  # noqa: PLC0415
    monkeypatch.setattr(lc, "_phase2_open", _REAL_OPEN)
    monkeypatch.setattr(lp2, "PHASE2_MODE", mode)
    monkeypatch.setattr(lst, "remaining", lambda: 100 if phase == "2" else None)
    monkeypatch.setattr(lp2, "lst", lst)
    return phase


@pytest.mark.parametrize("mode, phase, ci_kept", [
    ("off", "2", False), ("shadow", "2", False), ("on", "1", False), ("on", "2", True)])
def test_a_non_review_gate_carries_only_in_phase_two_with_the_setting_on(
        tmp_path, monkeypatch, mode, phase, ci_kept):
    """§18.3 / §18.11: off, shadow and phase 1 behave as before; a review gate carries in all."""
    _in_mode(monkeypatch, mode, phase)
    st = _ci_gates(**{"ci-check": {}, "review": {}})
    st["phase"] = phase
    _apply_ci(tmp_path, st, [("ci-check", CI_CARRY), ("review", {**REVIEW, "phase2": "fast"})])
    assert ("ci-check" in st["argv_gates"]) is ci_kept and "review" in st["argv_gates"]
    assert st["carried_gates"] == (["ci-check", "review"] if ci_kept else ["review"])
    ev = [json.loads(ln) for f in tmp_path.rglob("run.ndjson") for ln in f.read_text().splitlines()]
    ev = [e for e in ev if e.get("event") == "land_carry"][-1]
    assert ev.get("shadow_carried") == (["ci-check"] if mode == "shadow" else None)


@pytest.mark.parametrize("rec, kept", [
    ({"outcome": "fail"}, False),                         # a failed gate never carries
    ({"outcome": "indeterminate"}, False),
    ({"carried_n": 3}, True),                             # the fourth carry in a row
    ({"carried_n": 4}, False),                            # the fifth resets: a full run
    ({"full_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 25 * HOURS))}, False),
    ({"full_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 23 * HOURS))}, True),
    ({"full_at": None}, False),                           # an age it cannot prove
])
def test_the_periodic_full_run_and_the_failed_gate_rule(tmp_path, rec, kept):
    st = _ci_gates(**{"ci-check": rec})
    _apply_ci(tmp_path, st, [("ci-check", CI_CARRY)])
    assert ("ci-check" in st["argv_gates"]) is kept
    assert (st["carried_gates"], st["ran_gates"]) == (
        (["ci-check"], []) if kept else ([], ["ci-check"]))


def test_the_land_brief_lists_what_carried_what_ran_and_what_moved():
    import land_gate_split as lgs  # noqa: PLC0415
    st = {"carried_gates": ["ci-check", "llm-review"], "ran_gates": ["full-suite-gate"],
          "carried_from_main": "M1" * 6, "expected": "M2" * 6, "moved_since_ack": ["a.py"]}
    out = lgs.note(st).splitlines()
    assert out[0].startswith("carried from M1M1M1M1M1M1: ci-check, llm-review")
    assert out[1].startswith("ran on M2M2M2M2M2M2: full-suite-gate")
    assert "a.py" in out[2] and lgs.note({"carried_gates": []}) is None
