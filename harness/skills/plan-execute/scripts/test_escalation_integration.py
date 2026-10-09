"""ESC-02 — escalation where it meets the rest of the orchestrator.

The second half of the escalation suite (the ladder, version gate and refusal
rule are in test_escalation.py). Same PAIRED discipline: every check has a plant
and an allow control.

Covered:
  * the LANE and the CELL — what the ladder is actually walked against
  * redispatch / amend-session reset + generation bump
  * checkpoint & halt non-bypass — escalation state never makes `begin` dispatch
    a session the `plan` action would have parked
  * the rework integration, end to end through verify's REAL rework path

Shared helpers: escalation_helpers.py.

Run: pytest skills/plan-execute/scripts/test_escalation_integration.py -q
"""
import json

import pytest

from escalation_helpers import (
    SESS, _arm, _begin, _stamp, _record_refusal,
    ab, dsp, esca, make_plan, mio, rsi, run, sp, vfy,
)

# --------------------------------------------------------------------------
# 5b. The LANE and the CELL — what the ladder is walked against
# --------------------------------------------------------------------------
def test_the_climb_walks_the_lanes_provider_not_the_run_level_dial(tmp_path, capsys,
                                                                   monkeypatch):
    """Under `active_provider: openai` a session that is not opted into Codex
    execution FAILS CLOSED to the Claude lane and runs there — so its ladder is
    the Claude lane's. Walking `providers.openai` instead made the resolver raise
    on a `sonnet` cell; the raise was caught and degraded to "no climb", so the
    rework loop re-dispatched the same rung forever with nothing in the log."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    real = run._load_routing
    monkeypatch.setattr(run, "_load_routing", lambda: ("openai", real()[1]))
    by_id, out = _begin(plan_dir, ["s01"], capsys)
    assert out["active_provider"] == "openai"            # the dial really is flipped
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    assert by_id["s01"]["escalated_from"]["rung"] == 1
    run.cmd_release(plan_dir)


def test_a_session_with_no_reasoning_has_no_rung_to_climb_from(tmp_path, capsys):
    """`model` without `reasoning` is legal, and it is NOT a ladder cell: an unset
    effort is not a rung, and the resolver maps a non-rung effort to the tier's
    FIRST rung — so sonnet@unset would "climb" to sonnet@low and then sonnet@medium,
    two rungs spent walking back to where it started. Fail closed, out loud."""
    sess = {k: v for k, v in SESS.items() if k != "reasoning"}
    plan_dir = make_plan(tmp_path, [sess])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"])
    cap = capsys.readouterr()
    m = {x["id"]: x for x in json.loads(cap.out)["batch"]}["s01"]
    assert m["model_arg"] == "sonnet" and not m["reasoning"]
    assert "escalated_from" not in m
    assert "sonnet@low" not in cap.out, "climbed DOWN into the tier's first rung"
    assert "is not a ladder cell" in cap.err
    run.cmd_release(plan_dir)
    # ALLOW CONTROL: the same session WITH a reasoning does climb.
    (tmp_path / "two").mkdir()
    plan2 = make_plan(tmp_path / "two", [SESS])
    _stamp(plan2, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan2, "s01", consecutive=3, reworks=3)
    by_id, _ = _begin(plan2, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "opus"
    run.cmd_release(plan2)


# --------------------------------------------------------------------------
# 6. Reset + generation bump
# --------------------------------------------------------------------------
def test_redispatch_resets_the_ladder_and_bumps_the_generation(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    _record_refusal(plan_dir, "s01", "fable", "medium", capsys)
    before = esca.session_state(plan_dir, "s01")
    assert before["refused"] and before["generation"] == 0

    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note=None)
    run.cmd_redispatch(plan_dir, "s01", reason="the output no longer holds")
    capsys.readouterr()
    after = esca.session_state(plan_dir, "s01")
    assert after == {"refused": [], "generation": 1, "climb": 0, "last_rung": 0, "last_attempts": 0}
    assert sp.last_failure(plan_dir, "s01") is None
    # A fresh ladder means a fresh climb: nothing left to derive a rung from.
    assert esca.climb_steps(plan_dir, "s01") == 0


def test_amend_session_model_resets_the_ladder(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    _record_refusal(plan_dir, "s01", "fable", "medium", capsys)
    # A mutation refuses on mid-flight verify state (it rewrites manifest.json, and
    # verify state is digest-bound). Settle it — the ladder state under test lives
    # in run_state.json, which no mutation touches.
    (plan_dir / "_verify_state" / "s01.json").unlink()

    class _Args:
        session = "s01"
        depends_on = None
        prompt = None
        model = "Opus"
        reasoning = None
        allow_builder_drift = True

    run.cmd_amend_session(plan_dir, _Args())
    capsys.readouterr()
    after = esca.session_state(plan_dir, "s01")
    assert after == {"refused": [], "generation": 1, "climb": 0, "last_rung": 0, "last_attempts": 0}, (
        "an amendment that moves the AUTHORED cell must start a new cohort — "
        "pre-amend records must not be pooled with post-amend ones"
    )


# --------------------------------------------------------------------------
# 7. Human gates are NOT bypassable by escalation state
# --------------------------------------------------------------------------
def test_escalation_state_never_makes_begin_dispatch_a_parked_session(tmp_path, capsys):
    """The honest guarantee: escalation adds NO new dispatch entry point.
    `cmd_begin` itself gates the halt flag and barred sessions; human-checkpoint
    PARKING happens at the `plan` action — so what this asserts is that armed
    escalation state changes neither decision."""
    gated = {**SESS, "id": "s02", "items": ["it-2"],
             "dispatch": {"requires_human_checkpoint": True, "depends_on": ["s01"],
                          "checkpoint": {"reason": "irreversible", "decision": "go/no-go",
                                         "options": ["go", "stop"]}}}
    plan_dir = make_plan(tmp_path, [SESS, gated])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    for sid in ("s01", "s02"):
        _arm(plan_dir, sid, consecutive=3, reworks=3)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note=None)

    manifest = mio.load_manifest(plan_dir)

    def _action():
        return dsp.next_action(manifest, run._statuses(plan_dir), resume=False,
                               only_session=None, post_session_parked=[])

    armed = _action()
    assert armed["action"] == "checkpoint", f"s02 was not parked at all: {armed}"
    # THE INVARIANT, stated as a comparison rather than as a single snapshot: the
    # decision must be IDENTICAL with the ladder armed and with it cleared. A
    # snapshot alone could pass while escalation quietly changed something else.
    state = rsi.load_state(plan_dir)
    state["stuck"] = {}
    state[esca.STATE_KEY] = {}
    rsi.save_state(plan_dir, state)
    assert _action() == armed, "armed escalation state changed the `plan` decision"

    # ...and the halt gate is likewise untouched by escalation state.
    rsi.set_halt(plan_dir, "operator halt", "s01")
    with pytest.raises(SystemExit):
        run.cmd_begin(plan_dir, ["s02"])


# --------------------------------------------------------------------------
# 8. End-to-end through verify's REAL rework path
# --------------------------------------------------------------------------
# A gate that fails with a REAL, identifiable error, not `["false"]`. These tests
# are about the LADDER, and `false` emits zero bytes — an excerpt that names
# nothing. Since 2026-08-15 the stuck protocol refuses to arm on a locus it cannot
# attribute (see stuck_protocol.signature), so a silent gate no longer buys a rung
# and these tests would have been measuring the wrong thing to stay green.
GATE_FAIL = {"redx": {"kind": "argv", "argv": [
    "sh", "-c", 'echo "ValueError: redx could not reach the fixture host" >&2; exit 1']}}


def test_rework_loop_climbs_after_the_stuck_protocol_arms(tmp_path, capsys):
    """The whole ladder position, driven by real gate failures rather than by
    hand-written state: attempt 1 fails -> same rung; the 2nd same-signature
    failure arms the protocol -> the NEXT attempt climbs, carrying the research
    pass in the same bounded feedback file."""
    sess = {**SESS, "verify": {"gates": ["redx"], "on_fail": "rework", "max_rework": 3}}
    plan_dir = make_plan(tmp_path, [sess], gates=GATE_FAIL)
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    import closeout_pipeline as cp
    cp.persist(plan_dir, "s01", {"session": "s01", "result": "DONE",
                                 "items_completed": ["it-1"], "items_blocked": [],
                                 "notes": {}, "human_checkpoint_reason": None})
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)

    vfy.verify_begin(plan_dir, "s01")
    r1 = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert r1["action"] == "rework"
    assert "escalation_next" not in r1               # attempt 2 runs at the SAME rung

    _fresh_pass(plan_dir)                          # re-dispatch: new closeout, new pass
    r2 = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert r2["action"] == "rework"
    assert r2["stuck_protocol"]["triggered"], "the same failure twice must arm the protocol"
    nxt = r2["escalation_next"]
    assert nxt["authored"] == {"model": "sonnet", "reasoning": "high"}
    assert nxt["ran"] == {"model": "opus", "reasoning": "high"}

    # CLEAN BRIEF: the escalated attempt reads the ORIGINAL prompt plus this ONE
    # bounded, redacted file — never a prior transcript.
    fb = (plan_dir / "_verify_state" / "s01.feedback.md").read_text()
    assert "STUCK PROTOCOL — ARMED" in fb
    assert "runs ABOVE the authored tier" in fb
    assert "opus@high" in fb

    # ...and `begin` actually dispatches on that rung.
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "opus"
    assert by_id["s01"]["subagent_type"] == "tier-opus-high"
    ef = by_id["s01"]["escalated_from"]
    assert ef["rung"] == 1
    # ATTEMPT IS NOT THE RUNG. Two failures were spent, so this is dispatch
    # attempt 3 — running on rung 1. Reporting the rung index in the `attempt`
    # slot would misattribute every escalated record by one in the ledger.
    assert ef["attempt"] == 3, ef
    events = [json.loads(x) for x in (plan_dir / "run.ndjson").read_text().splitlines()]
    assert [e for e in events if e["event"] == "escalation_applied"]
    run.cmd_release(plan_dir)


def _loop_plan(tmp_path, capsys, *, max_rework, gates=None):
    """A plan wired for the REAL rework loop, opened at `verify_begin`.

    The loop is driven through `begin` on purpose: the rung the BLOCKED brief has
    to name is the one `begin` bound to, and only `begin` knows it. A test that
    calls `verify_run_argv` in a bare loop skips every dispatch and so measures a
    session that never actually ran anywhere."""
    sess = {**SESS, "verify": {"gates": ["redx"], "on_fail": "rework",
                               "max_rework": max_rework}}
    plan_dir = make_plan(tmp_path, [sess], gates=gates or GATE_FAIL)
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    import closeout_pipeline as cp
    cp.persist(plan_dir, "s01", {"session": "s01", "result": "DONE",
                                 "items_completed": ["it-1"], "items_blocked": [],
                                 "notes": {}, "human_checkpoint_reason": None})
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    vfy.verify_begin(plan_dir, "s01")
    return plan_dir


def _fresh_pass(plan_dir):
    """What a re-dispatch leaves behind: a NEW closeout applied, and the verify
    pass opened on it. One closeout, one verdict — a gate never re-runs on the
    closeout that already failed, so every loop below re-applies before it
    re-verifies, exactly as the orchestrator must."""
    import closeout_pipeline as cp
    cp.persist(plan_dir, "s01", {"session": "s01", "result": "DONE",
                                 "items_completed": ["it-1"], "items_blocked": [],
                                 "notes": {}, "human_checkpoint_reason": None})
    out = vfy.verify_begin(plan_dir, "s01")
    assert out["action"] != "stale-closeout", out
    return out


def _attempt(plan_dir, capsys):
    """One real dispatch: `begin` computes + RECORDS the rung, then releases the
    lock; the worker's closeout is applied and a fresh verify pass opened.
    Returns the dispatched batch member."""
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    _fresh_pass(plan_dir)
    return by_id["s01"]


# A gate that fails NAMING NOTHING — no error class, no findings block, no file
# reference. s05 and s07 both caught the ladder arming off exactly this kind of
# excerpt (a markdown fence; the review gate's own banner).
GATE_MUTE = {"redx": {"kind": "argv", "argv": [
    "sh", "-c", 'echo "re-run the gate once the issues above are addressed" >&2; exit 1']}}


def test_plant_an_unattributable_failure_never_buys_a_rung(tmp_path, capsys):
    """THE EXPENSIVE HALF of the stuck protocol's signature defect (D2).

    `climb_steps` buys a model rung off the same-signature counter, so a streak
    whose excerpt identifies NOTHING would climb on evidence that was never
    observed. Two identical unattributable failures here must leave the session on
    its authored cell; the ALLOW below is the same loop with a real error class."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=3, gates=GATE_MUTE)
    for _ in range(2):
        assert _attempt(plan_dir, capsys)["model_arg"] == "sonnet"
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
        assert r["action"] == "rework", r
    # The counter DID reach the trigger — this is not passing because nothing repeated.
    stuck = rsi.load_state(plan_dir)["stuck"]["s01"]
    assert stuck["consecutive"] >= sp.TRIGGER_AT, stuck
    assert stuck["armable"] is False, stuck

    assert esca.climb_steps(plan_dir, "s01") == 0
    assert _attempt(plan_dir, capsys)["model_arg"] == "sonnet", "climbed on a mute gate"


def test_allow_an_attributable_failure_still_buys_its_rung(tmp_path, capsys):
    """The paired ALLOW: identical loop, but the gate names its error class."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=3)      # GATE_FAIL: ValueError
    for _ in range(2):
        assert _attempt(plan_dir, capsys)["model_arg"] == "sonnet"
        vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert rsi.load_state(plan_dir)["stuck"]["s01"]["armable"] is True
    assert esca.climb_steps(plan_dir, "s01") == 1
    assert _attempt(plan_dir, capsys)["model_arg"] == "opus"


def test_the_ledger_records_the_mechanism_that_ACTUALLY_bound_the_effort(tmp_path, capsys):
    """D3. `effort_mechanism` is the one field that answers "did the
    escalated tier's effort actually bind?" — and it was WRONG on exactly the
    escalated rungs that question is about.

    The climb resolves a `tier-*` agent at dispatch and binds it, but the manifest
    it was re-derived from has no `dispatch.subagent_type`, so every escalated
    session was filed `prompt_directive_advisory`.

    Both halves measured here off REAL dispatches, not fixtures: the authored
    (unescalated) dispatch is the known-positive control."""
    import outcomes as outc

    plan_dir = _loop_plan(tmp_path, capsys, max_rework=3)
    # CONTROL — the authored cell also binds a tier agent, and is recorded as one.
    assert _attempt(plan_dir, capsys)["subagent_type"] == "tier-sonnet-high"
    assert outc.compose(plan_dir, "s01", resolution="verify_rework", result="rework",
                        verified=False)["effort_mechanism"] == "tier_agent"

    vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert _attempt(plan_dir, capsys)["subagent_type"] == "tier-sonnet-high"
    vfy.verify_run_argv(plan_dir, "s01", "gate:redx")

    climbed = _attempt(plan_dir, capsys)
    assert climbed["subagent_type"] == "tier-opus-high"     # the climb DID bind
    assert climbed["effort_mechanism"] == "tier_agent"      # ...and dispatch says so
    # The manifest still carries no subagent_type — the derivation this replaced
    # had nothing to read, which is why it answered wrongly rather than not at all.
    sess = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]
    assert not (sess.get("dispatch") or {}).get("subagent_type")

    rec = outc.compose(plan_dir, "s01", resolution="verify_rework", result="rework",
                       verified=False)
    assert rec["escalated_from"]["rung"] == 1, rec["escalated_from"]
    assert rec["effort_mechanism"] == "tier_agent", rec["effort_mechanism"]


def test_exhausting_the_budget_mid_ladder_names_the_untried_rungs(tmp_path, capsys):
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=2)
    rungs = []
    for _ in range(3):                            # attempts 1, 2, 3 — each dispatched
        m = _attempt(plan_dir, capsys)
        rungs.append((m["model_arg"], m["reasoning"]))
        halted = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert rungs == [("sonnet", "high"), ("sonnet", "high"), ("opus", "high")]
    assert halted["action"] == "halted"
    reason = halted["reason"]
    # The rung the LAST attempt actually ran on...
    assert "escalation stopped at rung 1 (opus@high)" in reason, reason
    # ...and the rungs the exhausted budget never bought.
    assert "fable@medium" in reason, f"untried rungs not named: {reason}"
    assert "max_rework" in reason                     # ...and what to do about it


def test_a_first_attempt_halt_never_claims_an_escalation_that_never_armed(tmp_path, capsys):
    """PLANT for the reverse defect: `max_rework: 0` halts on the first failure,
    where no climb was ever requested. Naming "rung 0" and listing every rung
    above it reads as "we escalated and still failed" — a claim about a climb that
    did not happen."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=0)
    _attempt(plan_dir, capsys)
    halted = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert halted["action"] == "halted"
    assert "escalation" not in halted["reason"].lower(), halted["reason"]
    assert "rung" not in halted["reason"].lower(), halted["reason"]
    # ALLOW CONTROL: the same brief-line renderer DOES speak once a climb happened.
    assert esca.brief_line({"rung": 1, "climb_requested": 1, "refused": [],
                            "ran": {"model": "opus", "reasoning": "high"},
                            "remaining": [{"model": "fable", "reasoning": "medium"}]})


def _sig_gate(sigfile):
    """A gate whose failure TEXT is whatever `sigfile` holds — so a test can change
    the root-cause signature between attempts the way reality does."""
    return {"redx": {"kind": "argv", "argv": [
        "sh", "-c", f'echo "ValueError: $(cat {sigfile})" >&2; exit 1']}}


def test_the_halt_names_the_rung_that_ran_even_when_the_last_error_differs(
    tmp_path, capsys
):
    """THE off-by-one this replaced. The climb is driven by a same-signature
    STREAK, so when the final failing attempt carries a DIFFERENT signature the
    streak has already reset — and re-deriving the rung at halt time then names a
    LOWER rung than the one that ran, listing rungs that were tried as untried.

    Here: two identical failures arm the climb, the escalated attempt fails with a
    NEW error, and the brief must still say opus@high (tried) rather than
    sonnet@high with opus@high listed among the untried rungs."""
    sigfile = tmp_path / "sig.txt"
    sigfile.write_text("the same broken thing\n")
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=2, gates=_sig_gate(sigfile))

    for _ in range(2):
        assert _attempt(plan_dir, capsys)["model_arg"] == "sonnet"
        vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert _attempt(plan_dir, capsys)["model_arg"] == "opus"     # the climb bound
    sigfile.write_text("a completely different thing\n")         # progress, then death
    halted = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")

    assert halted["action"] == "halted"
    reason = halted["reason"]
    assert "rung 1 (opus@high)" in reason, reason
    assert "untried rungs above it: opus@xhigh" in reason, reason
    assert "sonnet@high" not in reason, f"named a rung that was NOT the one that ran: {reason}"


def test_a_different_error_holds_the_rung_instead_of_handing_the_model_back(
    tmp_path, capsys
):
    """The ratchet. A different error resets the same-signature streak — that is
    progress — but progress must not drop the session back to the authored tier
    for the next attempt."""
    sigfile = tmp_path / "sig.txt"
    sigfile.write_text("the same broken thing\n")
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=4, gates=_sig_gate(sigfile))
    for _ in range(2):
        _attempt(plan_dir, capsys)
        vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert _attempt(plan_dir, capsys)["model_arg"] == "opus"
    sigfile.write_text("a completely different thing\n")
    vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    # PLANT: deriving from the (now reset) streak alone would say sonnet here.
    assert _attempt(plan_dir, capsys)["model_arg"] == "opus"


def test_a_SECOND_root_cause_climbs_from_the_rung_reached_not_from_the_floor(
    tmp_path, capsys
):
    """The climb ACCUMULATES. Two failures on cause A take the session to rung 1;
    a different error holds it there; then two consecutive failures on cause B
    arm the protocol AGAIN and must buy another rung.

    PLANT: deriving the rung from the live same-signature streak alone stalls —
    the streak restarts at 1, so the session sits at rung 1 while demonstrably
    stuck a second time, and with a realistic max_rework the ladder top is never
    reachable at all."""
    sigfile = tmp_path / "sig.txt"
    sigfile.write_text("cause A\n")
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=5, gates=_sig_gate(sigfile))
    seen = []
    for i in range(6):
        if i == 3:
            sigfile.write_text("cause B — a completely different thing\n")
        m = _attempt(plan_dir, capsys)
        seen.append((m["model_arg"], m["reasoning"]))
        vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert seen == [
        ("sonnet", "high"),      # attempt 1 — nothing armed
        ("sonnet", "high"),      # A failed once: rework at the same rung
        ("opus", "high"),        # A failed twice: armed, rung 1
        ("opus", "xhigh"),       # A failed a third time: rung 2
        ("opus", "xhigh"),       # B is a DIFFERENT error: progress, hold the rung
        ("fable", "medium"),     # B failed twice: armed again, rung 3
    ], seen


def test_the_apex_halt_does_not_blame_a_refusal_that_never_happened(tmp_path, capsys):
    """rung 0 with a climb requested has TWO causes — every rung refused, or the
    authored cell IS the apex. Reporting the first when the truth is the second
    sends the operator hunting for a dispatch failure that never occurred."""
    apex = {**SESS, "model": "Fable", "reasoning": "xhigh"}
    plan_dir = make_plan(tmp_path, [apex])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    desc = run._escalation_descriptor(plan_dir, mio.load_manifest(plan_dir),
                                      mio.session_by_id(mio.load_manifest(plan_dir))["s01"],
                                      "fable", "xhigh", steps=2)
    line = esca.brief_line(desc)
    assert "already the top of this provider's ladder" in line, line
    assert "refused" not in line, line


def test_an_all_refused_halt_says_so_instead_of_falling_silent(tmp_path, capsys):
    """When every escalated rung is refused the session runs AT the authored cell,
    so the rung that last bound is 0 — and a brief keyed on that alone goes silent,
    leaving the operator unable to tell "never climbed" from "all rungs refused".
    Keying it on the CLIMB (which refusals do not lower) and re-applying the
    step-down recovers both facts at once."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=3)
    seen = []
    while True:
        m = _attempt(plan_dir, capsys)
        seen.append((m["model_arg"], m["reasoning"]))
        if m["model_arg"] != "sonnet":                # refuse EVERY escalated rung
            _record_refusal(plan_dir, "s01", m["model_arg"], m["reasoning"], capsys)
            continue                                  # a refusal costs no budget
        res = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
        if res["action"] == "halted":
            halted = res
            break
    assert ("opus", "high") in seen and ("opus", "xhigh") in seen, seen
    assert seen[-1] == ("sonnet", "high"), "did not fall back to the authored cell"
    assert halted["action"] == "halted"
    reason = halted["reason"]
    assert "never left the authored tier" in reason, reason
    assert "opus@high" in reason, reason               # names WHICH rung was refused
    # ALLOW CONTROL: the silent case is still silent — a plain halt with no climb.
    (tmp_path / "ctl2").mkdir()
    plan2 = _loop_plan(tmp_path / "ctl2", capsys, max_rework=0)
    _attempt(plan2, capsys)
    assert "escalation" not in vfy.verify_run_argv(plan2, "s01", "gate:redx")["reason"].lower()


def test_a_declined_climb_halts_saying_DECLINED_not_out_of_budget(tmp_path, capsys):
    """A session pinned to a functional agent can never climb. When it exhausts its
    budget the brief must say the climb was DECLINED — not list "untried rungs"
    (measured from the rung it would have reached, so the list silently omits every
    rung below it) and advise raising max_rework, which cannot buy any of them."""
    sess = {**SESS, "dispatch": {"subagent_type": "digdeep"},
            "verify": {"gates": ["redx"], "on_fail": "rework", "max_rework": 3}}
    plan_dir = make_plan(tmp_path, [sess], gates=GATE_FAIL)
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    import closeout_pipeline as cp
    cp.persist(plan_dir, "s01", {"session": "s01", "result": "DONE",
                                 "items_completed": ["it-1"], "items_blocked": [],
                                 "notes": {}, "human_checkpoint_reason": None})
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    vfy.verify_begin(plan_dir, "s01")
    while True:
        m = _attempt(plan_dir, capsys)
        assert m["subagent_type"] == "digdeep"        # never swapped for a tier agent
        res = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
        if res["action"] == "halted":
            break
    reason = res["reason"]
    assert "DECLINED" in reason, reason
    assert "declared_subagent_type" in reason, reason
    assert "Raising max_rework cannot help" in reason, reason
    assert "untried rungs" not in reason, reason


def test_a_non_tier_agent_is_never_TOLD_it_escalated(tmp_path, capsys):
    """`begin` declines the climb for a session pinned to a functional agent. The
    rework feedback file — the ONLY thing the next attempt reads — must decline it
    too, or the subagent is told it escalated when it ran on the same tier as last
    time. One predicate, both consumers."""
    sess = {**SESS, "dispatch": {"subagent_type": "digdeep"},
            "verify": {"gates": ["redx"], "on_fail": "rework", "max_rework": 3}}
    plan_dir = make_plan(tmp_path, [sess], gates=GATE_FAIL)
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    import closeout_pipeline as cp
    cp.persist(plan_dir, "s01", {"session": "s01", "result": "DONE",
                                 "items_completed": ["it-1"], "items_blocked": [],
                                 "notes": {}, "human_checkpoint_reason": None})
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    vfy.verify_begin(plan_dir, "s01")
    for i in range(2):
        if i:
            _fresh_pass(plan_dir)                     # each attempt re-applies first
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert r["stuck_protocol"]["triggered"]           # the climb WOULD have armed
    assert "escalation_next" not in r
    fb = (plan_dir / "_verify_state" / "s01.feedback.md").read_text()
    assert "ABOVE the authored tier" not in fb, fb[-400:]
    # ALLOW CONTROL: the identical loop on an unpinned session DOES announce it.
    assert "escalation_next" in _loop_announce(tmp_path / "ctl", capsys)


def test_a_codex_harness_session_is_announced_its_OWN_rung_not_a_claude_one(tmp_path, capsys):
    """The rung announced in the feedback file must be the one the attempt will
    ACTUALLY run on — which is lane-specific.

    Before ESC-03 the codex lane did not climb at all, and this test asserted the
    announcement fell silent there. Since ESC-03 it climbs on `providers.openai`,
    so the assertion moves UP rather than away: the announcement must name that
    session's gpt-5.6 rung and NEVER a Claude one. Announcing `fable@medium` while
    the attempt runs `codex exec -m gpt-5.6-…` is the defect either way."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=5)
    for i in range(2):
        if i:
            _fresh_pass(plan_dir)                     # each attempt re-applies first
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert "escalation_next" in r                 # ALLOW CONTROL: Claude harness announces
    assert not r["escalation_next"]["ran"]["model"].startswith("gpt-")

    # Now plant the durable proof that this session's last dispatch was a
    # codex-harness one — the same event `begin --harness codex` writes.
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"], harness="codex")
    _fresh_pass(plan_dir)
    fb = plan_dir / "_verify_state" / "s01.feedback.md"
    fb.unlink()
    r2 = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert "escalation_next" in r2, r2
    nxt = r2["escalation_next"]
    # THE PROVIDER WALL, at the announcement: both halves of the pair are OpenAI.
    assert nxt["authored"]["model"].startswith("gpt-5.6-"), nxt
    assert nxt["ran"]["model"].startswith("gpt-5.6-"), nxt
    assert "fable" not in fb.read_text()
    assert "ABOVE the authored tier" in fb.read_text()

    # ...and it moves BACK. run.ndjson is append-only and no redispatch clears it,
    # so keying off "has this session EVER touched Codex" pinned the lane to codex
    # permanently: the session would silently stop being told about a climb that
    # `cmd_begin` was in fact applying. The LAST dispatch is what decides.
    _attempt(plan_dir, capsys)                        # a real Claude-harness begin
    fb.unlink()
    r3 = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert "escalation_next" in r3, r3
    assert "ABOVE the authored tier" in fb.read_text()


def _loop_announce(root, capsys):
    root.mkdir()
    plan_dir = _loop_plan(root, capsys, max_rework=3)
    for i in range(2):
        if i:
            _fresh_pass(plan_dir)                     # each attempt re-applies first
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    return r


# --------------------------------------------------------------------------
# 5z. THE BYPASS — the ladder is computed in `begin`; nothing else may re-run a
#     rework. Measured on an isolation plan:
#     one `begin`, eight rework verdicts, zero re-dispatches — the orchestrator
#     fixed the findings inline and re-ran the gates on the closeout that had
#     just failed, 20 times; 11 armed records across the ledger, 0 climbs.
# --------------------------------------------------------------------------
def _closeout_file(tmp_path):
    f = tmp_path / "co.txt"
    f.write_text('<plan-execute-closeout>\n'
                 '{"session":"s01","result":"DONE","items_completed":["it-1"],'
                 '"items_blocked":[],"notes":{},"human_checkpoint_reason":null}\n'
                 '</plan-execute-closeout>')
    return str(f)


def test_plant_a_rework_applied_without_begin_is_refused(tmp_path, capsys):
    """A failure is on record and `begin` has not run since: this closeout came
    from a dispatch the ladder never saw. `apply` refuses and mutates nothing."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=1)      # attempts=2, begin last saw 1
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run.cmd_apply(str(plan_dir), "s01", _closeout_file(tmp_path))
    assert e.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["failure"] == "undispatched-rework" and "begin --sessions s01" in out["reason"]
    assert ab.read_all_statuses((plan_dir / "PLAN.html").read_text())["s01"] == "DOING"
    assert not (plan_dir / "_closeouts" / "s01.json").exists()   # not even read in
    # ALLOW CONTROL: the same closeout, after a real `begin`, is accepted.
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["escalated_from"]["rung"] == 1   # ...and it climbed on the way
    run.cmd_release(plan_dir)
    capsys.readouterr()
    run.cmd_apply(str(plan_dir), "s01", _closeout_file(tmp_path))
    assert "failure" not in json.loads(capsys.readouterr().out)


def test_plant_the_failed_closeout_is_not_verified_twice(tmp_path, capsys):
    """One closeout, one verdict. After `rework`, neither `verify-run` nor
    `verify-begin` re-runs the gates on the closeout that failed; a newly applied
    closeout opens the next pass. The CLI says so with exit 2."""
    plan_dir = _loop_plan(tmp_path, capsys, max_rework=3)
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:redx")["action"] == "rework"
    vs = plan_dir / "_verify_state" / "s01.json"
    assert json.loads(vs.read_text())["rework_count"] == 1
    # PLANT: both doors, same closeout.
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:redx")["action"] == "stale-closeout"
    assert vfy.verify_begin(plan_dir, "s01")["action"] == "stale-closeout"
    assert json.loads(vs.read_text())["rework_count"] == 1       # not charged again
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        run.cmd_verify_run(plan_dir, "s01", "gate:redx")
    assert e.value.code == 2
    assert "begin --sessions s01" in json.loads(capsys.readouterr().out)["reason"]
    # ALLOW CONTROL: a fresh apply opens the next pass and the gate runs again.
    assert _fresh_pass(plan_dir)["action"] == "run-argv"
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:redx")["action"] == "rework"
    assert json.loads(vs.read_text())["rework_count"] == 2
