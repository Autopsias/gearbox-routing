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
import plan_limits as pl  # noqa: E402
import review_context as rvs  # noqa: E402
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


def test_build_carries_review_scope_into_the_manifest(tmp_path):
    """`review_scope` on the spec reaches the manifest, normalised. An undeclared
    scope emits NO KEY AT ALL -- this test used to assert `== []`, which codified
    the defect it now guards: an unconditional emit changes manifest_digest on
    every rebuild of every pre-existing plan, and manifest_digest is what
    _verify_state compares to decide state-drift. Consumers read it through
    `get_scope`/`pl.review_scope`, both of which already default a missing key to
    the whole tree, so absence and [] mean the same thing to every reader."""
    scoped = {**SESS, "id": "s01", "review_scope": ["./skills/x", "skills/y/", " "]}
    bare = {**SESS, "id": "s02", "items": [], "verify": {"gates": ["smoke"]}}
    plan_dir = make_plan(tmp_path, [scoped, bare], gates=GATE_PASS)
    by = mio.session_by_id(mio.load_manifest(plan_dir))
    assert by["s01"]["review_scope"] == ["skills/x", "skills/y/"]
    assert "review_scope" not in by["s02"], by["s02"]
    # ...but a key the plan ALREADY PUBLISHED survives a rebuild even when empty.
    # Emitting "only when non-empty" DROPS `"review_scope": []` from every plan
    # built while the emit was unconditional -- measured on the concurrent
    # repo-health plan, whose s01/s02/s03 carry `[]` and whose four _verify_state
    # files all bind to the digest that would move (2026-08-21). Both mistakes are
    # the same mistake: the manifest must describe what the plan published.
    man_p = plan_dir / "manifest.json"
    man = json.loads(man_p.read_text())
    man["sessions"] = [{**x, "review_scope": []} if x["id"] == "s02" else x
                       for x in man["sessions"]]
    man_p.write_text(json.dumps(man))
    spec = json.loads((plan_dir / "spec.json").read_text())
    kept = build_plan.stamp_spec(spec, plan_dir)
    got = build_plan.gen_manifest(kept)
    s02 = next(x for x in got["sessions"] if x["id"] == "s02")
    assert s02.get("review_scope") == [], s02
    # the readers agree that a missing key is the whole tree
    assert rvs.get_scope(by["s02"]) == [] and pl.review_scope(by["s02"]) == []


def test_build_warns_when_a_session_declares_writes_past_the_measured_p90(tmp_path, capsys):
    """The wiring, not the function: build() must call plan_limits on the
    session's declared writes, or the measured warning exists and never fires."""
    wide = {**SESS, "id": "s01", "items": [f"it-{i}" for i in range(7)]}
    narrow = {**SESS, "id": "s02", "items": ["it-9"]}
    spec = _spec([wide, narrow])
    for it in spec["items"]:                     # one distinct path per item
        it["touches"] = f"skills/x/{it['id']}.py"
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "deploy-targets.json").write_text("{}")
    (root / ".claude" / "eval-gates.json").write_text("{}")
    build_plan.build(spec, root / "_plans" / "fx", project_root=str(root))
    err = capsys.readouterr().err
    assert "s01 (7 files)" in err and "more than 6 files" in err, err
    assert "s02" not in err.split("more than 6 files")[0] if "more than 6 files" in err else True


GATE_IND = {"ind": {"kind": "argv", "argv": ["sh", "-c", "exit 2"], "indeterminate_exit": 2}}
GATE_RC2 = {"ind": {"kind": "argv", "argv": ["sh", "-c", "exit 2"]}}          # same exit, undeclared


def test_a_declared_indeterminate_exit_is_not_charged_and_stays_rerunnable(tmp_path):
    """A gate that COULD NOT DECIDE (reviewer timeout, no parseable block) leaves
    the gate pending and the rework budget untouched. s13 halted 2/2 "exhausted"
    on two 600s timeouts with no finding ever raised (2026-08-20)."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["ind"], max_rework=1)], gates=GATE_IND)
    write_closeout(plan_dir, "s01")
    _begin_doing(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:ind")
    assert out["action"] == "indeterminate", out
    st = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert st["rework_count"] == 0 and st["gate_status"]["gate:ind"] == "pending", st
    assert "not charged" in out["hint"] or "budget not" in out["hint"]
    # re-runnable: fix the cause (here: the gate now decides) and resume -- no budget spent
    (plan_dir.parent.parent / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"ind": {"kind": "argv", "argv": ["true"], "indeterminate_exit": 2}}))
    vfy.verify_begin(plan_dir, "s01", resume=True)
    out2 = vfy.verify_run_argv(plan_dir, "s01", "gate:ind")
    assert out2["action"] in ("passed", "done", "run-argv"), out2
    st2 = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert st2["rework_count"] == 0


def test_indeterminate_retry_without_resume_is_refused(tmp_path):
    """RT-04 PLANT — before this session's fix, `resume` was accepted and never
    read: re-calling verify-begin after an `indeterminate` gate silently re-ran
    it whether or not --resume was passed. A blind retry of a gate that just
    hung/timed out, with no acknowledgment anything was done about it, is
    exactly what SKILL.md's documented `--resume` requirement exists to stop."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["ind"], max_rework=1)], gates=GATE_IND)
    write_closeout(plan_dir, "s01")
    _begin_doing(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:ind")
    assert out["action"] == "indeterminate", out
    out2 = vfy.verify_begin(plan_dir, "s01")  # no --resume
    assert out2["action"] == "requires-resume", out2
    assert "resume" in out2["reason"].lower()
    st = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert st["gate_status"]["gate:ind"] == "pending"  # refusal touched nothing


def test_crash_mid_pass_resume_required_then_honored(tmp_path):
    """RT-04 — a fresh state (gate 1 still pending, no rework/indeterminate
    involved) simulates a crash: the orchestrator process died and is re-run.
    ALLOW: --resume proceeds exactly as SKILL.md's crash-recovery section says."""
    gates = {**GATE_PASS, "smoke2": {"kind": "argv", "argv": ["true"]}}
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke", "smoke2"])], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")  # creates state, gate 1 pending — "crash" here
    refused = vfy.verify_begin(plan_dir, "s01")
    assert refused["action"] == "requires-resume", refused
    resumed = vfy.verify_begin(plan_dir, "s01", resume=True)
    assert resumed["action"] in ("invoke-skill", "run-argv"), resumed


def test_rework_redispatch_reentry_needs_no_resume_flag(tmp_path):
    """ALLOW CONTROL — the documented, un-flagged re-entry after a rework
    re-dispatch (a NEW closeout was applied) must NOT be caught by the new
    resume gate; only a same-closeout re-entry is a "resume"."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], max_rework=1)], gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    vfy.verify_run_argv(plan_dir, "s01", "gate:redx")  # attempt 1 -> rework
    write_closeout(plan_dir, "s01")                    # re-dispatch -> new closeout
    out = vfy.verify_begin(plan_dir, "s01")             # no --resume, and correctly so
    assert out["action"] != "requires-resume", out


def test_a_fix_round_runs_review_gates_first_and_a_first_pass_keeps_declared_order(tmp_path):
    """2026-10-03: a rework reorders review gates (skill-kind, or argv running
    llm_review_gate.py) ahead of the test gates, stable inside each group; the
    first pass keeps the declared order. Fails if _reviews_first is removed."""
    rev_argv = {"kind": "argv", "argv": ["true", "x/llm_review_gate.py"]}
    gates = {"t1": GATE_PASS["smoke"], "rv_argv": rev_argv, "t2": GATE_FAIL["redx"],
             "rv_skill": GATE_SKILL["rev"]}
    order = ["t1", "rv_argv", "t2", "rv_skill"]
    plan_dir = make_plan(tmp_path, [_verify_sess(order, max_rework=2)], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    state_p = plan_dir / "_verify_state" / "s01.json"
    vfy.verify_begin(plan_dir, "s01")
    assert json.loads(state_p.read_text())["gates"] == [f"gate:{g}" for g in order]
    vfy.verify_run_argv(plan_dir, "s01", "gate:t1")
    vfy.verify_run_argv(plan_dir, "s01", "gate:rv_argv")
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:t2")["action"] == "rework"
    write_closeout(plan_dir, "s01")                     # re-dispatch -> new closeout
    out = vfy.verify_begin(plan_dir, "s01")
    st = json.loads(state_p.read_text())
    assert st["gates"] == ["gate:rv_argv", "gate:rv_skill", "gate:t1", "gate:t2"], st["gates"]
    assert st["gate_status"] == {n: "pending" for n in st["gates"]}
    assert out["action"] == "run-argv" and out["gate"] == "gate:rv_argv", out


def test_an_UNDECLARED_exit_2_is_still_a_plain_failure(tmp_path):
    """The control: only a gate that DECLARES its indeterminate exit gets the
    no-charge path. An arbitrary script's exit 2 means whatever it means."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["ind"], max_rework=1)], gates=GATE_RC2)
    write_closeout(plan_dir, "s01")
    _begin_doing(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:ind")
    assert out["action"] == "rework", out


# --------------------------------------------------------------------------
# EXPECT — the gate passes on its OUTPUT, not only its exit code
# --------------------------------------------------------------------------
# The failure this closes: a check that ran nothing still exits 0. `true` here
# stands in for `pytest -q` over a path that collected no tests -- the gate is
# green and the session is DONE having proved nothing.
GATE_SILENT_GREEN = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo 'no tests ran'"],
                               "expect": "8/8 passed"}}
GATE_EXPECT_OK = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo '8/8 passed'"],
                            "expect": "8/8 passed"}}
GATE_EXPECT_RE = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo '12/12 passed'"],
                            "expect": r"/\d+\/\d+ passed/"}}
GATE_EXPECT_BAD_RE = {"suite": {"kind": "argv", "argv": ["true"], "expect": "/[unclosed/"}}


def _run_one(tmp_path, gates):
    plan_dir = make_plan(tmp_path, [_verify_sess(["suite"], max_rework=1)], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    return plan_dir, vfy.verify_run_argv(plan_dir, "s01", "gate:suite")


def test_expect_satisfied_passes_the_gate(tmp_path):
    """The allow-control. Without it a broken EXPECT that fails everything would
    look like a working gate."""
    _plan_dir, out = _run_one(tmp_path, GATE_EXPECT_OK)
    assert out["action"] == "passed", out


def test_exit_zero_without_the_expected_output_FAILS(tmp_path):
    """The whole point: the command succeeded and proved nothing. Before `expect`
    this was a pass, and the session shipped on a suite that collected no tests."""
    plan_dir, out = _run_one(tmp_path, GATE_SILENT_GREEN)
    assert out["action"] == "rework", out
    assert _status(plan_dir, "s01") == "PARTIAL"
    # The rework attempt must be able to ACT on it: the gate's own output reads
    # fine, so the excerpt has to say WHY it failed (see a-finding-the-session-
    # cannot-fix). Assert the reason travels, not just the failure.
    feedback = Path(out["feedback_file"]).read_text()
    assert "EXPECT" in feedback and "8/8 passed" in feedback, feedback
    assert "exited 0" in feedback, feedback


def test_expect_accepts_a_regex_form(tmp_path):
    _plan_dir, out = _run_one(tmp_path, GATE_EXPECT_RE)
    assert out["action"] == "passed", out


def test_an_uncompilable_expect_fails_loud_instead_of_being_ignored(tmp_path):
    """A pattern nobody can compile is a broken gate. Ignoring it would leave the
    registry documenting a guarantee nothing enforces -- silent green again."""
    _plan_dir, out = _run_one(tmp_path, GATE_EXPECT_BAD_RE)
    assert out["action"] == "rework", out
    assert "not a valid regex" in Path(out["feedback_file"]).read_text()


def test_a_gate_with_no_expect_is_unchanged(tmp_path):
    """`expect` is opt-in and additive: every gate authored before it keeps
    deciding on the exit code alone."""
    _plan_dir, out = _run_one(tmp_path, {"suite": {"kind": "argv", "argv": ["true"]}})
    assert out["action"] == "passed", out


def test_expect_does_not_swallow_a_declared_indeterminate_exit(tmp_path):
    """`expect` answers "did exit 0 MEAN the check ran". It must not annex the
    other half: a gate can also exit NON-zero having decided nothing, which is
    what `indeterminate_exit` exists for (a reviewer that times out exits 2, and
    charging the agent's rework budget for the harness's own timeout is the bug
    that field fixed). The two compose because gate_miss reports only on a zero
    exit, so a declared indeterminate code still reaches _indeterminate and is
    NOT charged. Raised by the peer session that owns indeterminate_exit."""
    gates = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo 'timed out'; exit 2"],
                       "expect": "8/8 passed", "indeterminate_exit": 2}}
    plan_dir, out = _run_one(tmp_path, gates)
    assert out["action"] == "indeterminate", out
    st = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert st["rework_count"] == 0, "an indeterminate gate must not spend rework budget"
    assert st["gate_status"]["gate:suite"] == "pending", st


def test_a_NONZERO_exit_is_never_reported_as_exited_zero(tmp_path):
    """A failing command whose output ALSO misses the expect must be reported as
    what it was. Prefixing its excerpt with "exited 0 but..." would make the gate
    report state something that did not happen -- worse than saying less."""
    gates = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo boom; exit 3"],
                       "expect": "8/8 passed"}}
    _plan_dir, out = _run_one(tmp_path, gates)
    assert out["action"] == "rework", out
    feedback = Path(out["feedback_file"]).read_text()
    assert "exited 0" not in feedback, feedback
    assert "boom" in feedback, feedback


@pytest.mark.parametrize("expect,output,ok", [
    ("ALL MET", "x\nALL MET\n", True),
    ("ALL MET", "x\nUNMET: 2\n", False),
    (r"/^ok$/m", "noise\nok\n", True),
    (r"/^ok$/m", "not ok at all", False),
    ("", "anything", True),                       # absent EXPECT never fails a gate
    (None, "", True),
    ("/(/", "anything", False),                   # uncompilable -> loud
    ("/x/q", "x", False),                         # unknown flag -> loud, not ignored
    # A value that begins AND ends with "/" is ALWAYS a regex (sed/JS convention),
    # so a literal path is misread -- LOUDLY, and the message names the escape.
    ("/usr/bin/env", "ran /usr/bin/env", False),
    (r"/\/usr\/bin\/env/", "ran /usr/bin/env", True),   # the documented escape works
])
def test_expect_mismatch_unit(expect, output, ok):
    import gate_expect as gx
    assert (gx.expect_mismatch(expect, output) is None) is ok


def test_build_defaults_on_fail_and_max_rework(tmp_path):
    sess = {**SESS, "verify": {"gates": ["smoke"]}}  # only gates declared
    plan_dir = make_plan(tmp_path, [sess], gates=GATE_PASS)
    v = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]["verify"]
    # 2 since 2026-08-20 (build_plan.DEFAULT_MAX_REWORK): at 1 the loop is one
    # review, one fix, one re-review -- nothing left for the reviewer to verify
    # the fix. Measured over 21h / two plans, see that constant's comment.
    assert v["on_fail"] == "rework" and v["max_rework"] == 2


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
    write_closeout(plan_dir, "s01")                          # re-dispatch -> new closeout
    vfy.verify_begin(plan_dir, "s01")                        # -> fresh pass
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


def test_blocked_rework_closeout_settles_the_old_verify_cycle(tmp_path, capsys):
    """A terminal BLOCKED rework must not leave stale `outcome=rework` state."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], max_rework=2)], gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")

    done = tmp_path / "done.txt"
    done.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{},"dispatch_next":false,'
        '"human_checkpoint_reason":null}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(str(plan_dir), "s01", str(done))
    capsys.readouterr()
    vfy.verify_begin(plan_dir, "s01")
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:redx")["action"] == "rework"

    blocked = tmp_path / "blocked.txt"
    blocked.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"BLOCKED","items_completed":[],'
        '"items_blocked":["it-1"],"notes":{},"dispatch_next":false,'
        '"human_checkpoint_reason":null,"decision_brief":{'
        '"attempts":["verified once"],"findings":[{"source":"gate:redx",'
        '"takeaway":"the deterministic gate failed"}],'
        '"options":["stop"],"recommendation":"stop"}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(str(plan_dir), "s01", str(blocked))

    state = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert state["outcome"] == "halted"
    assert state["rework_count"] == 1
    assert _status(plan_dir, "s01") == "BLOCKED"


def test_an_INDETERMINATE_that_arms_the_stuck_protocol_SAYS_SO(tmp_path, monkeypatch):
    """`_indeterminate` discarded `sp.record_failure`'s return, so a gate that
    could not decide twice armed the protocol INVISIBLY: no STUCK PROTOCOL
    section in the feedback the operator reads, no `stuck_protocol` key in the
    return, no `stuck_protocol_armed` event. Meanwhile SKILL.md and
    verify-gates.md promised exactly that behaviour — a documented promise the
    code did not keep. Armed-but-silent is the same defect as not arming.

    Neuter by dropping the `if stuck["triggered"]` block and this fails."""
    import rework as rw
    import run_state_io as rsi_
    plan_dir = tmp_path / "p"
    (plan_dir / "_verify_state").mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html></html>")

    events = []
    monkeypatch.setattr(rsi_, "log_event",
                        lambda pd, ev, **kw: events.append(ev))
    monkeypatch.setattr(rw.rsi, "log_event",
                        lambda pd, ev, **kw: events.append(ev))
    monkeypatch.setattr(rw.ab, "apply_mutation", lambda *a, **k: None)

    state = {"rework_count": 0, "max_rework": 2, "gate_status": {}, "failures": {}}
    # The REAL banner shape. A first draft of this test used prose
    # ("llm-review-medium: INDETERMINATE ...") which `_INDETERMINATE` does not
    # match, so the signature came back `unclassified`/armable=False and the test
    # failed against CORRECT code. A fixture pointed at the wrong input reports a
    # defect that is not there — the same error as one pointed at nothing
    # reporting none, with the sign flipped.
    excerpt = ("[llm-review-gate] level=medium verdict=indeterminate\n"
               "the reviewer exceeded its per-attempt budget")
    out = None
    for _ in range(rw.sp.TRIGGER_AT):            # same signature, TRIGGER_AT times
        out = rw._indeterminate(plan_dir, "s01", state, "gate:llm-review-medium", excerpt)

    assert out["action"] == "indeterminate"
    assert out["rework_count"] == 0, "still charges nothing — that half must not regress"
    assert "stuck_protocol" in out, "armed but the caller cannot see it"
    fb = (plan_dir / "_verify_state" / "s01.feedback.md").read_text()
    assert "STUCK PROTOCOL" in fb, "armed but the re-dispatch never reads it"
    assert "stuck_protocol_armed" in events, "armed but nothing was logged"


def test_a_rebuild_after_midnight_does_not_change_the_manifest(tmp_path):
    """gen_manifest read the CLOCK for `created`, so regenerating a plan the next
    day produced a different manifest_digest -- and manifest_digest is what
    _verify_state compares to decide state-drift. The plan then halts, mid-verify,
    for a change nobody made. Measured on 2026-08-21, when this plan's own digest
    moved at midnight. A spec that pins `created` must round-trip; one that does
    not still stamps today, so plans built before this are untouched."""
    plan_dir = make_plan(tmp_path, [{**SESS, "id": "s01"}], gates=GATE_PASS)
    spec = json.loads((plan_dir / "spec.json").read_text())
    man = json.loads((plan_dir / "manifest.json").read_text())
    assert spec.get("created"), "build() must pin created onto the spec"
    assert man["created"] == spec["created"]
    # THE POINT: regenerate as if it were any other day. Nothing may move.
    spec_old = {**spec, "created": "2019-01-01"}
    assert build_plan.gen_manifest(spec_old)["created"] == "2019-01-01"
    # control: a spec with no date still gets one, so old plans keep working
    spec_bare = {k: v for k, v in spec.items() if k != "created"}
    assert build_plan.gen_manifest(spec_bare)["created"], "a dateless spec must still stamp one"
    # ...but a REBUILD of such a plan must take the date the plan already
    # published, not today's. Stamping today here would drift every one of the
    # plans already on disk -- none of which carries `created` in spec.json --
    # which is the very halt this change removes. plan_mutate already does this
    # when it regenerates a plan; build() did not.
    (plan_dir / "manifest.json").write_text(json.dumps({**man, "created": "2019-01-01"}))
    assert build_plan.stamp_spec(spec_bare, plan_dir)["created"] == "2019-01-01"
    # and with no plan on disk at all, today is the only answer available
    assert build_plan.stamp_spec(spec_bare, tmp_path / "nope")["created"] != "2019-01-01"
    # a manifest that parses to the WRONG SHAPE is not a date either
    (plan_dir / "manifest.json").write_text('["not", "a", "manifest"]')
    assert build_plan.stamp_spec(spec_bare, plan_dir)["created"] != "2019-01-01"


def test_a_rebuild_does_not_re_probe_the_research_environment(tmp_path):
    """`created` was fixed and its SIBLING was left: research_env.probed_at is
    clock-derived too, so a rebuild re-probed it and moved manifest_digest just
    the same (measured 1e021aeb -> a5283e62, 2026-08-21). research_env_record's
    own docstring already promised this ("never re-probed... reproduces the same
    bytes") -- true on the gen_manifest path, false on the build() path. Both
    clock-derived keys now follow ONE rule: the spec, else what the plan already
    published, else observe afresh."""
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    spec = {"items": [{"id": "it-1", "research_status": "unavailable"}]}
    fresh = build_plan.stamp_spec(spec, plan_dir)         # nothing published yet
    assert fresh["research_env"]["probed_at"], fresh
    # a REBUILD takes what the plan already published, for BOTH keys
    (plan_dir / "manifest.json").write_text(json.dumps(
        {"created": "2019-01-01",
         "research_env": {"available": True, "probed_at": "2019-01-01"}}))
    again = build_plan.stamp_spec(spec, plan_dir)
    assert again["research_env"]["probed_at"] == "2019-01-01", again["research_env"]
    assert again["created"] == "2019-01-01"
    # control: a spec making NO research claim gains no record, published or not
    assert "research_env" not in build_plan.stamp_spec({"items": [{"id": "it-1"}]}, plan_dir)


def test_a_rebuild_leaves_an_UNCHANGED_manifest_byte_for_byte(tmp_path):
    """Pinning the clock keys is not enough, because `manifest_digest` hashes RAW
    BYTES and the bytes on disk are not canonical. Measured 2026-08-21: 4 of the
    28 plans in this repo carry a trailing newline nothing in build_plan writes,
    and rebuilding _plans/plan-level-git-isolation-2026-08-20 moved its digest
    75bd9a7c60aa -> 35271aafb96d over that ONE byte with zero content difference
    -- which halts every bound _verify_state on state-drift for a change nobody
    made. So a rebuild compares VALUES and only writes when the dispatch graph
    actually differs."""
    plan_dir = make_plan(tmp_path, [{**SESS, "id": "s01"}], gates=GATE_PASS)
    man_p = plan_dir / "manifest.json"
    man_p.write_bytes(man_p.read_bytes() + b"\n")   # whatever reformatted it on disk
    before = mio.manifest_digest(plan_dir)
    spec = json.loads((plan_dir / "spec.json").read_text())
    build_plan.build(spec, plan_dir, preserve_state=True)
    assert mio.manifest_digest(plan_dir) == before, "a no-op rebuild moved the digest"
    # CONTROL: a REAL change to the dispatch graph must still be written, or this
    # guard would freeze the manifest instead of stabilising it.
    changed = {**spec, "sessions": [{**spec["sessions"][0], "title": "renamed"}]}
    build_plan.build(changed, plan_dir, preserve_state=True)
    assert mio.manifest_digest(plan_dir) != before
    assert mio.session_by_id(mio.load_manifest(plan_dir))["s01"]["title"] == "renamed"


def test_a_bare_gate_name_is_refused_by_name_and_exits_nonzero(tmp_path, capsys):
    """The false-green measured 2026-08-22. Gate names carry a `gate:` prefix, so
    `--gate ind` names nothing -- but `cmd_verify_run` printed `action: "error"`
    and exited 0, so an orchestrator reading the exit code advanced past a gate
    that had never run. The old message ("not an argv-kind gate") also sent the
    session hunting for a kind mismatch that did not exist."""
    plan_dir = make_plan(tmp_path, [_verify_sess(["ind"], max_rework=1)], gates=GATE_IND)
    write_closeout(plan_dir, "s01")
    _begin_doing(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")

    out = vfy.verify_run_argv(plan_dir, "s01", "ind")       # the bare name
    assert out["action"] == "error", out
    assert "names no gate" in out["message"], out
    assert "gate:ind" in out["message"], out   # names what IS known

    with pytest.raises(SystemExit) as exc:                  # the exit code, not the payload
        run.cmd_verify_run(plan_dir, "s01", "ind")
    assert exc.value.code == 1

    st = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert st["gate_status"]["gate:ind"] == "pending"       # the refusal ran nothing

    # KNOWN NEGATIVE: the correct name still passes and still exits 0.
    out2 = vfy.verify_run_argv(plan_dir, "s01", "gate:ind")
    assert out2["action"] != "error", out2


def test_verify_record_refuses_an_argv_gate_and_an_unknown_gate(tmp_path):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_record(plan_dir, "s01", "gate:smoke", "done")
    assert out["action"] == "error" and "argv" in out["message"]
    out = vfy.verify_record(plan_dir, "s01", "gate:nope", "done")
    assert out["action"] == "error" and "names no gate" in out["message"]
