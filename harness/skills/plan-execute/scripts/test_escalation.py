"""ESC-02 — the upward escalation engine: the ladder, the gate, the refusal.

Every assertion here is PAIRED: a PLANT (the wrong behaviour must be detected)
and an ALLOW CONTROL (the right behaviour must not trip). A check that only ever
sees the passing case cannot fail, which is the defect class this repo keeps
re-finding — and it is exactly why the version-gate half below asserts BOTH that
a v5 plan never escalates AND that the same session on a v6 plan does.

Covered:
  * the ladder itself, through resolve_route.escalate — every start cell climbs
    to the apex and then to 'exhausted'; the provider wall holds
  * the version gate (v5 never escalates, v6 does) and the per-session opt-out
  * FROZEN v5 fixtures: byte-identical Claude-lane dispatch, and the legacy
    gpt-5.5 pin still blocking loudly
  * resume idempotency — same inputs, same rung, no double climb
  * the refusal rule through the REAL interface (`run.py record-refusal`)

The lane/cell, reset, human-gate and rework halves live in
test_escalation_integration.py. Shared helpers: escalation_helpers.py.

Run: pytest skills/plan-execute/scripts/test_escalation.py -q
"""
import json
import shutil
import sys

import pytest

from escalation_helpers import (
    FIXTURES, REPO, SESS, _arm, _begin, _resolver, _stamp, _record_refusal,
    ab, build_plan, egress, esca, make_plan, mio, run,
)


# --------------------------------------------------------------------------
# 1. The ladder — through the SHARED funnel, never a second table
# --------------------------------------------------------------------------
def test_every_anthropic_start_cell_climbs_to_the_apex_then_exhausts():
    rr = _resolver()
    apex = {"model_id": "fable", "native_effort": "xhigh"}
    starts = [
        {"model_id": "haiku", "native_effort": None},
        {"model_id": "sonnet", "native_effort": "medium"},
        {"model_id": "sonnet", "native_effort": "high"},
        {"model_id": "opus", "native_effort": "medium"},
        {"model_id": "opus", "native_effort": "high"},
        {"model_id": "fable", "native_effort": "medium"},
    ]
    for start in starts:
        cur, walk = start, [start]
        for _ in range(12):
            cur = rr.escalate("agentic_build", "anthropic", current=cur)
            if cur == rr.EXHAUSTED:
                break
            walk.append(cur)
        else:
            pytest.fail(f"ladder from {start} did not terminate in 12 rungs: {walk}")
        assert walk[-1] == apex, f"{start} ended at {walk[-1]}, not the apex"
        # THE INVARIANT THE SSOT STATES IN WORDS, asserted mechanically.
        assert not any(r == {"model_id": "opus", "native_effort": "max"} for r in walk)
        assert not any(r["native_effort"] == "max" for r in walk if r["model_id"] == "fable")


def test_openai_climb_ends_at_sol_max_and_never_crosses_providers():
    rr = _resolver()
    cur, walk = {"model_id": "gpt-5.6-luna", "native_effort": "max"}, []
    for _ in range(12):
        cur = rr.escalate("standard_build", "openai", current=cur)
        if cur == rr.EXHAUSTED:
            break
        walk.append(cur)
    assert walk[-1] == {"model_id": "gpt-5.6-sol", "native_effort": "max"}
    # PROVIDER WALL: no Anthropic model may appear on an OpenAI walk, or vice versa.
    assert all(r["model_id"].startswith("gpt-") for r in walk)
    cur, awalk = {"model_id": "sonnet", "native_effort": "high"}, []
    for _ in range(12):
        cur = rr.escalate("agentic_build", "anthropic", current=cur)
        if cur == rr.EXHAUSTED:
            break
        awalk.append(cur)
    assert not any(r["model_id"].startswith("gpt-") for r in awalk)


def test_climb_steps_follows_the_stuck_counter_not_the_attempt_counter(tmp_path):
    """attempt 1 fail -> same rung. 2nd SAME-signature fail arms the protocol, and
    the NEXT attempt is the first escalated one. A DIFFERENT error is progress and
    buys no bigger model."""
    plan_dir = make_plan(tmp_path, [SESS])
    assert esca.climb_steps(plan_dir, "s01") == 0            # first dispatch
    _arm(plan_dir, "s01", consecutive=1, reworks=1)
    assert esca.climb_steps(plan_dir, "s01") == 0            # rework at the same rung
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    assert esca.climb_steps(plan_dir, "s01") == 1            # FIRST escalated attempt
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    assert esca.climb_steps(plan_dir, "s01") == 2
    # ALLOW CONTROL: a different error buys NO rung. (`_arm` also rewrites the
    # dispatch history, so this isolates the "buys nothing" half; that a real
    # session HOLDS the rung it already reached is asserted in
    # test_a_different_error_holds_the_rung_instead_of_handing_the_model_back.)
    _arm(plan_dir, "s01", consecutive=1, reworks=4)
    assert esca.climb_steps(plan_dir, "s01") == 0


# --------------------------------------------------------------------------
# 2. Version gate + opt-out
# --------------------------------------------------------------------------
def test_v5_manifest_never_escalates_but_v6_does(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [SESS])
    _arm(plan_dir, "s01", consecutive=3, reworks=3)

    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA - 1)         # PLANT: pre-gate plan
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "sonnet"
    assert "escalated_from" not in by_id["s01"]
    run.cmd_release(plan_dir)

    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)             # ALLOW CONTROL: same state, v6
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "opus"               # sonnet@high +2 rungs
    assert by_id["s01"]["escalated_from"]["authored"] == {"model": "sonnet", "reasoning": "high"}
    assert by_id["s01"]["escalated_from"]["ran"] == {"model": "opus", "reasoning": "xhigh"}
    assert by_id["s01"]["model_ran_source"] == "requested"   # never "attested"
    run.cmd_release(plan_dir)


def test_session_opts_out_with_escalation_false(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [{**SESS, "escalation": False},
                                    {**SESS, "id": "s02", "items": ["it-2"]}])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    for sid in ("s01", "s02"):
        _arm(plan_dir, sid, consecutive=3, reworks=3)
    by_id, _ = _begin(plan_dir, ["s01", "s02"], capsys)
    assert by_id["s01"]["model_arg"] == "sonnet"             # opted out, pinned
    assert "escalated_from" not in by_id["s01"]
    assert by_id["s02"]["model_arg"] == "opus"               # ALLOW CONTROL: climbs
    run.cmd_release(plan_dir)


def test_builder_stamp_and_the_gate_cannot_drift_apart():
    """The stamp a fresh plan carries must be at or above the gate that switches
    the feature on — otherwise a brand-new plan would never escalate, and the
    feature would be dead on arrival with every test still green."""
    assert esca.ESCALATION_MIN_SCHEMA == 6
    assert build_plan.PLAN_SCHEMA_VERSION >= esca.ESCALATION_MIN_SCHEMA


def test_builder_refuses_a_non_bool_escalation_and_a_malformed_experiment(tmp_path):
    for name, sess in (
        ("a", {**SESS, "escalation": "false"}),                     # truthy string!
        ("b", {**SESS, "routing_experiment": {"kind": "canary"}}),  # missing proposal_id
        ("c", {**SESS, "routing_experiment": {"kind": "c", "proposal_id": "p", "extra": 1}}),
    ):
        d = tmp_path / name
        d.mkdir()
        with pytest.raises((ValueError, SystemExit)):
            make_plan(d, [sess])
    # ALLOW CONTROL: the well-formed versions of both build fine.
    ok = tmp_path / "ok"
    ok.mkdir()
    make_plan(ok, [{**SESS, "escalation": False,
                    "routing_experiment": {"kind": "canary", "proposal_id": "p1"}}])


def test_a_mutation_never_upgrades_a_running_plans_contract(tmp_path, capsys):
    """A mid-run `amend-session` must NOT move a v5 plan to v6.

    `gen_manifest` stamps the CURRENT builder version, so without the carve-out in
    `plan_mutate.build_generation` an amendment would retroactively switch on every
    v6-gated runtime behaviour — including which MODEL a failing session
    re-dispatches on — for a plan whose author never opted in. That is precisely
    the version-gate failure this repo has already paid for."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, 5)

    class _Args:
        session = "s01"
        depends_on = None
        prompt = "changed"
        model = None
        reasoning = None
        allow_builder_drift = True

    run.cmd_amend_session(plan_dir, _Args())
    capsys.readouterr()
    after = json.loads((plan_dir / "manifest.json").read_text())["plan_schema_version"]
    assert after == 5, f"the mutation upgraded the plan's contract to v{after}"
    # ALLOW CONTROL: a plan already at the current stamp keeps it (no downgrade).
    _stamp(plan_dir, build_plan.PLAN_SCHEMA_VERSION)
    run.cmd_amend_session(plan_dir, _Args())
    capsys.readouterr()
    assert json.loads((plan_dir / "manifest.json").read_text())["plan_schema_version"] \
        == build_plan.PLAN_SCHEMA_VERSION


def test_a_stamp_no_builder_could_write_registers_as_DRIFT(tmp_path):
    """The carve-out that keeps an OLD plan consistent must be ONE-WAY.

    Copying the manifest's stamp into the regenerated one unconditionally meant
    the field was never compared at all — so a hand-edited stamp could opt a plan
    into version-gated runtime behaviour (escalation among it) and never show up
    as drift. Below the builder's own version is legitimate history; above it is
    a forgery."""
    import plan_mutate as pm
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, build_plan.PLAN_SCHEMA_VERSION - 1)     # ALLOW CONTROL: older
    assert not [b for b in pm.consistency_report(plan_dir)["problems"]
                if "gen_manifest" in b], "an older stamp is history, not drift"
    _stamp(plan_dir, build_plan.PLAN_SCHEMA_VERSION + 7)     # PLANT: forged
    assert [b for b in pm.consistency_report(plan_dir)["problems"]
            if "gen_manifest" in b], (
        "a stamp no builder could have written was not reported as drift"
    )


def test_routing_experiment_reaches_the_manifest(tmp_path):
    """s05 only READS this tag off the manifest, so the builder must be the thing
    that puts it there — otherwise the canary label never reaches the ledger."""
    tagged = {**SESS, "routing_experiment": {"kind": "effort_canary", "proposal_id": "rp-7"}}
    plan_dir = make_plan(tmp_path, [tagged, {**SESS, "id": "s02", "items": ["it-2"]}])
    sessions = mio.session_by_id(mio.load_manifest(plan_dir))
    assert sessions["s01"]["routing_experiment"] == {"kind": "effort_canary",
                                                     "proposal_id": "rp-7"}
    # ALLOW CONTROL: an untagged session gains no key at all, so a manifest for a
    # spec that declares none is byte-identical to the pre-change one.
    assert "routing_experiment" not in sessions["s02"]
    assert "escalation" not in sessions["s02"]


def test_a_declared_non_tier_agent_declines_the_climb_out_loud(tmp_path, capsys):
    """A session pinned to a FUNCTIONAL agent (its own tools and instructions)
    keeps that agent. Swapping it for a tier agent to make the climb bind would
    silently discard what the author asked for — so the climb is declined, and
    said out loud rather than announced as if it happened."""
    pinned = {**SESS, "dispatch": {"subagent_type": "digdeep"}}
    plan_dir = make_plan(tmp_path, [pinned])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    capsys.readouterr()
    run.cmd_begin(plan_dir, ["s01"])
    cap = capsys.readouterr()
    m = {x["id"]: x for x in json.loads(cap.out)["batch"]}["s01"]
    assert m["subagent_type"] == "digdeep"
    assert m["model_arg"] == "sonnet"                    # as authored
    assert "escalated_from" not in m
    assert "escalation cannot bind here" in cap.err
    assert "a non-tier agent" in cap.err
    events = [json.loads(x) for x in (plan_dir / "run.ndjson").read_text().splitlines()]
    assert [e for e in events if e["event"] == "escalation_declined"]
    run.cmd_release(plan_dir)

    # ALLOW CONTROL: a declared TIER agent is the effort mechanism, so replacing it
    # with the escalated tier agent IS the climb, not a loss of author intent.
    tiered = {**SESS, "id": "s02", "items": ["it-2"],
              "dispatch": {"subagent_type": "tier-sonnet-high"}}
    (tmp_path / "two").mkdir()
    plan_dir2 = make_plan(tmp_path / "two", [tiered])
    _stamp(plan_dir2, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir2, "s02", consecutive=2, reworks=2)
    by_id, _ = _begin(plan_dir2, ["s02"], capsys)
    assert by_id["s02"]["subagent_type"] == "tier-opus-high"
    assert by_id["s02"]["model_arg"] == "opus"
    run.cmd_release(plan_dir2)


# --------------------------------------------------------------------------
# 3. FROZEN v5 fixtures — the old-plan measurement (see fixtures/README.md)
# --------------------------------------------------------------------------
def _run_fixture(name, tmp_path, monkeypatch):
    egress_root = tmp_path / "egress"
    egress_root.mkdir()
    monkeypatch.setenv(egress._EGRESS_ROOT_ENV, str(egress_root))
    monkeypatch.setenv(run._SSOT_ENV, str(REPO / "model-routing.yaml"))
    plan = tmp_path / "plan"
    shutil.copytree(FIXTURES / name, plan)
    (plan / "EXPECTED-dispatch.json").unlink()
    sids = [s["id"] for s in json.loads((plan / "manifest.json").read_text())["sessions"]]
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
        run.cmd_begin(plan, sids)
    payload = json.loads(buf.getvalue())
    blob = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    blob = (blob.replace(str(plan), "<PLAN_DIR>")
                .replace(str(egress), "<EGRESS>")
                .replace(str(REPO), "<TREE>"))
    return blob + "\n"


def test_frozen_v5_claude_lane_dispatch_is_byte_identical(tmp_path, monkeypatch):
    got = _run_fixture("v5-claude-lane", tmp_path, monkeypatch)
    want = (FIXTURES / "v5-claude-lane" / "EXPECTED-dispatch.json").read_text()
    assert got == want, (
        "a plan built before the escalation gate must dispatch EXACTLY as it did "
        "before the change. If this move is deliberate, update EXPECTED-dispatch.json "
        "in the same commit and say why in the message."
    )
    run.cmd_release(tmp_path / "plan")


def test_frozen_v5_gpt55_pin_still_blocks_with_guidance(tmp_path, monkeypatch):
    got = _run_fixture("v5-gpt55-pin", tmp_path, monkeypatch)
    want = (FIXTURES / "v5-gpt55-pin" / "EXPECTED-dispatch.json").read_text()
    assert got == want
    # ...and it is a BLOCK, not a silent reroute onto a 5.6 model.
    payload = json.loads(got)
    assert payload["action"] == "blocked"
    assert "RETIRED" in payload["unroutable"]["s01"]


def _refusal_set(validate):
    """{plan slug: error class} for every spec.json on disk that `validate` rejects."""
    out = {}
    for s in sorted((REPO / "_plans").glob("*/spec.json")):
        try:
            validate(json.loads(s.read_text()))
        except Exception as e:  # noqa: BLE001 — any refusal is the datum
            out[s.parent.name] = type(e).__name__
    return out


def test_no_existing_spec_is_NEWLY_refused_by_the_new_builder(tmp_path):
    """Measurement (b), as a DIFFERENTIAL — the only form that can be true.

    A plain "every spec validates" assertion is FALSE ON ARRIVAL: six plans on
    disk are already refused today by the (ungated) checkpoint-brief
    rule, none of which this change touches. A gate that is already red cannot
    tell you whether YOUR change broke something. So this compares the refusal SET
    before and after: the version bump must add no new member.

    Deliberately NOT a regenerate-and-diff either — a read-only probe found only 1
    of 26 plans regenerates identically TODAY, before any change (see
    fixtures/README.md)."""
    import subprocess

    specs = sorted((REPO / "_plans").glob("*/spec.json"))
    assert len(specs) >= 20, f"only found {len(specs)} specs — the probe is not looking"

    head = tmp_path / "head"
    head.mkdir()
    tar = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD",
                          "skills/plan-builder/scripts"],
                         capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(head)], input=tar, check=True)
    old_dir = head / "skills" / "plan-builder" / "scripts"
    if not (old_dir / "build_plan.py").exists():
        pytest.skip("no committed build_plan.py to compare against")

    import importlib.util
    spec_o = importlib.util.spec_from_file_location("build_plan_head",
                                                    old_dir / "build_plan.py")
    old = importlib.util.module_from_spec(spec_o)
    sys.path.insert(0, str(old_dir))
    try:
        spec_o.loader.exec_module(old)
    finally:
        sys.path.remove(str(old_dir))

    before = _refusal_set(old.validate_spec)
    after = _refusal_set(build_plan.validate_spec)
    newly = {k: v for k, v in after.items() if k not in before}
    assert not newly, (
        "the version bump NEWLY refuses plans already on disk: "
        f"{sorted(newly)} (pre-existing refusals, untouched: {sorted(before)})"
    )
    # KNOWN-POSITIVE CONTROL: the differential must be able to SEE a new refusal,
    # or its all-clear means nothing. Feed it a validator that refuses everything.
    def _refuse_all(_spec):
        raise ValueError("planted")

    planted = {k: v for k, v in _refusal_set(_refuse_all).items() if k not in before}
    assert planted, "the differential cannot detect a new refusal — it is a no-op check"


# --------------------------------------------------------------------------
# 4. Resume idempotency — computed, never stored-and-fired
# --------------------------------------------------------------------------
def test_same_inputs_yield_the_same_rung_however_often_begin_runs(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    seen = []
    for _ in range(3):
        ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
        by_id, _ = _begin(plan_dir, ["s01"], capsys)
        seen.append((by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]))
        run.cmd_release(plan_dir)
    assert seen == [("opus", "high")] * 3, f"begin double-climbed on resume: {seen}"


# --------------------------------------------------------------------------
# 5. The refusal rule — through the REAL interface, never a mocked internal set
# --------------------------------------------------------------------------
def test_refused_rung_falls_back_to_the_previous_rung_and_is_never_re_proposed(
    tmp_path, capsys
):
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)          # wants rung 2 = opus@xhigh
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "opus"
    run.cmd_release(plan_dir)

    out = _record_refusal(plan_dir, "s01", "opus", "xhigh", capsys)
    assert out["charged_against_max_rework"] is False
    assert out["refused"] == ["opus@xhigh"]

    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    # PREVIOUS rung — not the downward fallback ladder, and not the refused cell.
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    run.cmd_release(plan_dir)

    # NEVER RE-PROPOSED: even with more failure history, the refused cell is gone.
    _arm(plan_dir, "s01", consecutive=4, reworks=4)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) != ("opus", "xhigh")
    run.cmd_release(plan_dir)


def test_a_refused_rung_does_not_cap_the_rest_of_the_ladder(tmp_path, capsys):
    """A refusal takes ONE cell out of the ladder — not everything above it.

    PLANT: recording the stepped-down rung as the climb's own position caps the
    session at the refused rung forever (the next climb can only ever re-propose
    it), while `remaining` still lists the rungs above as untried and the BLOCKED
    brief still advises raising max_rework, which cannot help."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)          # wants rung 2
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "xhigh")
    run.cmd_release(plan_dir)
    _record_refusal(plan_dir, "s01", "opus", "xhigh", capsys)

    # A further same-signature failure must reach the rung ABOVE the refused one.
    _arm(plan_dir, "s01", consecutive=4, reworks=4)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("fable", "medium")
    run.cmd_release(plan_dir)
    # ...and the refused cell is still never re-proposed on the way past it.
    assert esca.session_state(plan_dir, "s01")["refused"] == ["opus@xhigh"]


def test_an_escalated_member_is_told_to_refuse_not_to_degrade(tmp_path, capsys):
    """Two standing instructions fire on the SAME observed event — "degrade to
    `fallback_model`" and "report it with record-refusal". On an escalated member
    only the second is correct (the downward ladder from an escalated cell lands
    at or below the authored tier), so the choice is removed rather than ranked:
    `fallback_model` is null and `on_dispatch_refusal` names the command."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    m = by_id["s01"]
    assert m["model_arg"] == "opus"
    assert m["fallback_model"] is None and m["fallback_reasoning"] is None
    assert "record-refusal" in m["on_dispatch_refusal"]
    assert "do NOT degrade" in m["on_dispatch_refusal"]
    assert "--model opus --reasoning xhigh" in m["on_dispatch_refusal"]
    run.cmd_release(plan_dir)

    # ALLOW CONTROL: an UNescalated fable session keeps its normal degrade target,
    # so the suppression is scoped to the climb and has not broken degradation.
    (tmp_path / "two").mkdir()
    plan2 = make_plan(tmp_path / "two", [{**SESS, "model": "Fable", "reasoning": "medium"}])
    _stamp(plan2, esca.ESCALATION_MIN_SCHEMA)
    by_id, _ = _begin(plan2, ["s01"], capsys)
    assert by_id["s01"]["fallback_model"] == "opus"
    assert "on_dispatch_refusal" not in by_id["s01"]
    run.cmd_release(plan2)


def test_the_documented_refusal_recovery_actually_walks(tmp_path, capsys):
    """`record-refusal` says "then re-run `plan`". Walk exactly that, with NO hand
    reset of the session status in between.

    PLANT: a refused dispatch never ran, but the session was left at DOING — so
    the documented recovery answered `blocked` and the loop this command exists to
    unblock could not be walked at all. Every other refusal test here resets the
    status by hand, which is precisely what hid it."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=2, reworks=2)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    assert run._statuses(plan_dir)["s01"] == "DOING"
    run.cmd_release(plan_dir)

    out = _record_refusal(plan_dir, "s01", "opus", "high", capsys)
    assert out["status"] == "TODO", "a dispatch that never ran must not stay DOING"

    capsys.readouterr()
    run.cmd_plan(plan_dir, resume=False, only_session=None)   # the documented next step
    planned = json.loads(capsys.readouterr().out)
    assert planned["action"] == "dispatch", planned
    assert [m for m in planned["batch"] if m["id"] == "s01"], planned
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("sonnet", "high")
    run.cmd_release(plan_dir)


def test_the_floor_holds_on_the_composite_escalate_then_refuse_path(tmp_path, capsys):
    """Refuse EVERY escalated rung in turn. The session must land back on the
    AUTHORED cell and stop there — it must never walk the DOWNWARD fallback
    ladder below the tier the plan author chose."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    for model, effort in (("fable", "medium"), ("opus", "xhigh"), ("opus", "high")):
        _record_refusal(plan_dir, "s01", model, effort, capsys)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("sonnet", "high")
    assert "escalated_from" not in by_id["s01"]              # rung 0 = as authored
    # PLANT: the downward ladder from sonnet is haiku — it must NEVER appear here.
    assert by_id["s01"]["model_arg"] != "haiku"
    run.cmd_release(plan_dir)


def test_a_refusal_without_a_reason_is_refused(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    with pytest.raises(SystemExit):
        run.cmd_record_refusal(plan_dir, "s01", "fable", "medium", reason="  ",
                               source="dispatch_error")


def test_a_refusal_against_an_unknown_session_is_refused(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    with pytest.raises(SystemExit):
        run.cmd_record_refusal(plan_dir, "s99", "fable", "medium", reason="x",
                               source="dispatch_error")


def test_a_rung_key_that_names_no_rung_is_REFUSED_not_silently_recorded(tmp_path, capsys):
    """`--model fable` with no `--reasoning` used to record `fable@unset`, print
    `"recorded": true`, and let the NEXT dispatch send `fable@medium` — the exact
    rung just refused. A refusal that reads as accepted and changes nothing is the
    worst of the three outcomes, so a key that matches no rung is a hard error."""
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "xhigh")
    run.cmd_release(plan_dir)

    with pytest.raises(SystemExit) as e:                 # PLANT: effort omitted
        run.cmd_record_refusal(plan_dir, "s01", "fable", "", reason="observed",
                               source="dispatch_error")
    assert "fable@medium" in str(e.value), "the error must name the rungs that ARE valid"
    with pytest.raises(SystemExit):                      # PLANT: a rung below the floor
        run.cmd_record_refusal(plan_dir, "s01", "haiku", "", reason="observed",
                               source="dispatch_error")
    assert esca.session_state(plan_dir, "s01")["refused"] == [], "a refused refusal recorded state"

    # ALLOW CONTROL: the key as dispatched is accepted, and it actually bites.
    _record_refusal(plan_dir, "s01", "opus", "xhigh", capsys)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    run.cmd_release(plan_dir)


def test_authored_sentence_does_not_claim_an_unpinned_session_was_authored():
    import rework
    base = {"authored": {"model": "sonnet", "reasoning": "medium"}}
    assert rework._authored_sentence({**base, "declared": {}}) == (
        "The plan named no model; its class default is `sonnet@medium`.")
    pinned = {**base, "declared": {"model": "sonnet", "reasoning": "medium"}}
    assert "authored this session as `sonnet@medium`" in rework._authored_sentence(pinned)


