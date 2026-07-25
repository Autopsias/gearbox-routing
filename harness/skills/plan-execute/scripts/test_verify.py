"""Verification suite for SESSION VERIFY GATES (the "don't ship trash" boundary).

Covers the deterministic verify state machine without a live orchestrator:
  * build-time resolution of a verify block into the immutable manifest
  * build-time rejection of an unknown verify gate
  * noop when a session declares no verify block
  * happy path: argv gate passes -> verify-finalize flips DOING->DONE
  * gate fail -> bounded rework (session->PARTIAL, feedback file written)
  * rework budget exhausted -> halt (session->BLOCKED, halt flag set)
  * on_fail: halt -> immediate halt even with rework budget
  * state-drift refusal (manifest changed under a live verify state)
  * human checkpoint applied by finalize AFTER gates pass (verify-first)
  * cmd_apply leaves the session DOING while verify is pending
  * verify-simulate passes the whole pipeline (CI smoke)

argv gates use the POSIX ``true``/``false`` programs so pass/fail is real and
deterministic with no mocking. Run: pytest plan-execute/scripts/test_verify.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import manifest_io as mio  # noqa: E402
import run  # noqa: E402
import verify as vfy  # noqa: E402


# --------------------------------------------------------------------------
# Fixture builder (mirrors test_shipping.py)
# --------------------------------------------------------------------------
def _spec(sessions, phases=None):
    items = sorted({iid for s in sessions for iid in s.get("items", [])})
    return {
        "title": "Verify Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": iid, "title": iid.upper(), "category": "work"} for iid in items],
        "phases": phases or [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": items[:1]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def make_plan(tmp_path, sessions, *, phases=None, gates=None):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text(json.dumps(gates or {}))
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec(sessions, phases), plan_dir, project_root=str(project_root))
    return plan_dir


def write_closeout(plan_dir, session_id, *, result="DONE", completed=None, checkpoint=None):
    manifest = mio.load_manifest(plan_dir)
    items = mio.session_by_id(manifest)[session_id]["items"]
    completed = items if completed is None else completed
    parsed = {"session": session_id, "result": result, "items_completed": completed,
              "items_blocked": [i for i in items if i not in completed], "notes": {},
              "human_checkpoint_reason": checkpoint}
    cp.persist(plan_dir, session_id, parsed)


def _status(plan_dir, sid):
    return ab.read_all_statuses((plan_dir / "PLAN.html").read_text()).get(sid)


def _begin_doing(plan_dir, sid):
    """Mimic `begin`: flip the session to DOING before the closeout is applied."""
    ab.apply_mutation(plan_dir / "PLAN.html", sid, status="DOING", note=None)


# argv gates: real, deterministic pass/fail.
GATE_PASS = {"smoke": {"kind": "argv", "argv": ["true"]}}
GATE_FAIL = {"redx": {"kind": "argv", "argv": ["false"]}}
GATE_SKILL = {"rev": {"kind": "skill", "skill": "code-review", "args": ""}}
SESS = {"id": "s01", "title": "Session 1", "items": ["it-1"], "model": "Sonnet", "prompt": "do it"}


def _verify_sess(gates, on_fail="rework", max_rework=1, **extra):
    return {**SESS, "verify": {"gates": gates, "on_fail": on_fail, "max_rework": max_rework}, **extra}


# --------------------------------------------------------------------------
# Build-time
# --------------------------------------------------------------------------
def test_build_resolves_verify_into_manifest(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    v = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]["verify"]
    assert v == {"gates": ["smoke"], "on_fail": "rework", "max_rework": 1}


def test_build_defaults_on_fail_and_max_rework(tmp_path):
    sess = {**SESS, "verify": {"gates": ["smoke"]}}  # only gates declared
    plan_dir = make_plan(tmp_path, [sess], gates=GATE_PASS)
    v = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]["verify"]
    assert v["on_fail"] == "rework" and v["max_rework"] == 1


def test_build_rejects_unknown_verify_gate(tmp_path):
    with pytest.raises((ValueError, SystemExit)):
        make_plan(tmp_path, [_verify_sess(["ghost"])], gates=GATE_PASS)  # 'ghost' not registered


def test_phase_verify_inherited_by_session(tmp_path):
    phases = [{"id": "p1", "verify": {"gates": ["smoke"], "max_rework": 2}}]
    sess = {**SESS, "phase": "p1"}
    plan_dir = make_plan(tmp_path, [sess], phases=phases, gates=GATE_PASS)
    v = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]["verify"]
    assert v["gates"] == ["smoke"] and v["max_rework"] == 2


def test_prompt_announces_verification(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    prompt = (plan_dir / "sessions" / "s01.prompt.md").read_text()
    assert "Verification gates" in prompt and "smoke" in prompt


# --------------------------------------------------------------------------
# Runtime — verify state machine
# --------------------------------------------------------------------------
def test_verify_noop_without_block(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])  # no verify
    write_closeout(plan_dir, "s01")
    assert vfy.verify_begin(plan_dir, "s01")["action"] == "noop"


def test_happy_path_argv_passes_then_done(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    out = vfy.verify_begin(plan_dir, "s01")          # argv gate -> helper runs it
    assert out["action"] == "run-argv" and out["gate"] == "gate:smoke"
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")
    assert out["action"] == "passed"
    fin = vfy.verify_finalize(plan_dir, "s01")
    assert fin["final_status"] == "DONE"
    assert _status(plan_dir, "s01") == "DONE"


def test_gate_fail_reworks_to_partial(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], max_rework=1)], gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert out["action"] == "rework" and out["attempt"] == 1
    assert _status(plan_dir, "s01") == "PARTIAL"
    assert Path(out["feedback_file"]).exists()


def test_rework_exhausted_halts(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], max_rework=1)], gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    vfy.verify_run_argv(plan_dir, "s01", "gate:redx")        # attempt 1 -> rework
    vfy.verify_begin(plan_dir, "s01")                        # re-dispatch -> fresh pass
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")  # attempt 2 -> exhausted
    assert out["action"] == "halted"
    assert _status(plan_dir, "s01") == "BLOCKED"
    import run_state_io as rsi
    assert rsi.is_halted(plan_dir)


def test_on_fail_halt_is_immediate(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], on_fail="halt", max_rework=3)],
                         gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert out["action"] == "halted"            # no rework despite max_rework=3
    assert _status(plan_dir, "s01") == "BLOCKED"


def test_state_drift_refuses(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")  # creates verify state
    # Corrupt the bound manifest digest to simulate a rebuild under live state.
    sp = vfy.verify_state_path(plan_dir, "s01")
    st = json.loads(sp.read_text())
    st["manifest_digest"] = "stale-digest"
    sp.write_text(json.dumps(st))
    out = vfy.verify_begin(plan_dir, "s01")
    assert out["action"] == "failed" and out["reason"] == "state-drift"


def test_human_checkpoint_applied_after_gates_pass(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01", checkpoint="review the public wording")
    vfy.verify_begin(plan_dir, "s01")
    vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")
    fin = vfy.verify_finalize(plan_dir, "s01")
    assert fin["final_status"] == "AWAITS_REVIEW"
    assert _status(plan_dir, "s01") == "AWAITS_REVIEW"


def test_two_gates_sequential_all_pass(tmp_path):
    gates = {**GATE_PASS, "smoke2": {"kind": "argv", "argv": ["true"]}}
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke", "smoke2"])], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    out = vfy.verify_begin(plan_dir, "s01")
    assert out["gate"] == "gate:smoke"
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")
    assert out["action"] == "run-argv" and out["gate"] == "gate:smoke2"  # advanced
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:smoke2")
    assert out["action"] == "passed"


def test_skill_gate_emits_invoke_directive(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["rev"])], gates=GATE_SKILL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    out = vfy.verify_begin(plan_dir, "s01")
    assert out["action"] == "invoke-skill" and out["skill"] == "code-review"
    # Orchestrator reports the skill verdict back:
    out = vfy.verify_record(plan_dir, "s01", "gate:rev", "done")
    assert out["action"] == "passed"


def test_verify_simulate_passes_all(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke", "rev"])],
                         gates={**GATE_PASS, **GATE_SKILL})
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    out = vfy.verify_simulate(plan_dir, "s01")
    assert out["action"] == "done" and out["final_status"] == "DONE"


# --------------------------------------------------------------------------
# cmd_apply integration — DONE closeout with verify stays DOING
# --------------------------------------------------------------------------
def test_apply_leaves_session_doing_when_verify_pending(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    closeout = (
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{},"dispatch_next":true,'
        '"human_checkpoint_reason":null}\n'
        '</plan-execute-closeout>'
    )
    out_file = tmp_path / "co.txt"
    out_file.write_text(closeout)
    run.cmd_apply(str(plan_dir), "s01", str(out_file))
    printed = json.loads(capsys.readouterr().out)
    assert printed["verify_pending"] is True
    assert _status(plan_dir, "s01") == "DOING"   # NOT DONE — gates must pass first
