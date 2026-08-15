"""ESC-02 — the upward escalation engine.

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
  * the refusal rule through the REAL interface (`run.py record-refusal`):
    previous-rung re-dispatch, the authored-cell floor on the composite
    escalate-then-refuse path, no budget charge, never re-proposed
  * redispatch / amend-session reset + generation bump
  * checkpoint & halt non-bypass — escalation state never makes `begin` dispatch
    a session the `plan` action would have parked
  * the rework integration, end to end through verify's rework path

Run: pytest skills/plan-execute/scripts/test_escalation.py -q
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import article_block as ab  # noqa: E402
import build_plan  # noqa: E402
import dispatch as dsp  # noqa: E402
import escalation as esca  # noqa: E402
import manifest_io as mio  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import stuck_protocol as sp  # noqa: E402
import verify as vfy  # noqa: E402
from test_shipping import make_plan  # noqa: E402

FIXTURES = SCRIPTS.parent / "fixtures"
REPO = SCRIPTS.parents[2]


def _resolver():
    return run._import_resolver()


def _begin(plan_dir, sessions, capsys, **kw):
    capsys.readouterr()                      # drain whatever an earlier command printed
    run.cmd_begin(plan_dir, sessions, **kw)
    out = json.loads(capsys.readouterr().out)
    return {m["id"]: m for m in out.get("batch", [])}, out


def _stamp(plan_dir, version):
    """Re-stamp a fixture plan's manifest — the ONLY way a test opts a plan into
    (or out of) the escalation gate, since the stamp is the gate."""
    m = Path(plan_dir) / "manifest.json"
    man = json.loads(m.read_text())
    man["plan_schema_version"] = version
    m.write_text(json.dumps(man, indent=2, ensure_ascii=False))


def _arm(plan_dir, session_id, *, consecutive, reworks):
    """Put the session in the state a real rework loop would have left: N
    consecutive same-signature failures recorded, N reworks spent, and the
    DISPATCH HISTORY those failures imply.

    The history is not decoration. The climb ACCUMULATES from the rung the session
    is standing on (so a second root cause climbs from there rather than restarting
    at the authored cell), which makes the rung path-dependent — a hand-written
    counter with no dispatch behind it describes a session that never ran."""
    state = rsi.load_state(plan_dir)
    state.setdefault("stuck", {})[session_id] = {
        "sig": "deadbeef", "class": "AssertionError", "locus": "boom",
        "consecutive": consecutive, "attempts": consecutive, "previous_sig": None,
    }
    # ...as of the PREVIOUS dispatch: one rung lower, one failure earlier.
    state.setdefault(esca.STATE_KEY, {}).setdefault(session_id, {}).update(
        {"climb": max(0, consecutive - 2), "last_rung": max(0, consecutive - 2),
         "last_attempts": max(0, consecutive - 1)}
    )
    rsi.save_state(plan_dir, state)
    vs = Path(plan_dir) / "_verify_state"
    vs.mkdir(exist_ok=True)
    (vs / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "rework_count": reworks, "max_rework": 9,
        "gates": [], "gate_status": {}, "on_fail": "rework", "outcome": "rework",
    }))


SESS = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet",
        "reasoning": "high", "task_class": "agentic_build", "prompt": "work"}


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
    assert by_id["s01"]["model_arg"] == "fable"              # sonnet@high +2 rungs
    assert by_id["s01"]["escalated_from"]["authored"] == {"model": "sonnet", "reasoning": "high"}
    assert by_id["s01"]["escalated_from"]["ran"] == {"model": "fable", "reasoning": "medium"}
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
    assert by_id["s02"]["model_arg"] == "fable"              # ALLOW CONTROL: climbs
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
    egress = tmp_path / "egress"
    egress.mkdir()
    monkeypatch.setenv(run._EGRESS_ROOT_ENV, str(egress))
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
    disk are already refused today by the (ungated, 2026-07-11) checkpoint-brief
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
def _record_refusal(plan_dir, sid, model, reasoning, capsys):
    capsys.readouterr()                      # drain whatever an earlier command printed
    run.cmd_record_refusal(plan_dir, sid, model, reasoning,
                           reason="observed: model unavailable at dispatch",
                           source="dispatch_error")
    return json.loads(capsys.readouterr().out)


def test_refused_rung_falls_back_to_the_previous_rung_and_is_never_re_proposed(
    tmp_path, capsys
):
    plan_dir = make_plan(tmp_path, [SESS])
    _stamp(plan_dir, esca.ESCALATION_MIN_SCHEMA)
    _arm(plan_dir, "s01", consecutive=3, reworks=3)          # wants rung 2 = fable@medium
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert by_id["s01"]["model_arg"] == "fable"
    run.cmd_release(plan_dir)

    out = _record_refusal(plan_dir, "s01", "fable", "medium", capsys)
    assert out["charged_against_max_rework"] is False
    assert out["refused"] == ["fable@medium"]

    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    # PREVIOUS rung — not the downward fallback ladder, and not the refused cell.
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    run.cmd_release(plan_dir)

    # NEVER RE-PROPOSED: even with more failure history, the refused cell is gone.
    _arm(plan_dir, "s01", consecutive=4, reworks=4)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) != ("fable", "medium")
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
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("fable", "medium")
    run.cmd_release(plan_dir)
    _record_refusal(plan_dir, "s01", "fable", "medium", capsys)

    # A further same-signature failure must reach the rung ABOVE the refused one.
    _arm(plan_dir, "s01", consecutive=4, reworks=4)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("fable", "high")
    run.cmd_release(plan_dir)
    # ...and the refused cell is still never re-proposed on the way past it.
    assert esca.session_state(plan_dir, "s01")["refused"] == ["fable@medium"]


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
    assert m["model_arg"] == "fable"
    assert m["fallback_model"] is None and m["fallback_reasoning"] is None
    assert "record-refusal" in m["on_dispatch_refusal"]
    assert "do NOT degrade" in m["on_dispatch_refusal"]
    assert "--model fable --reasoning medium" in m["on_dispatch_refusal"]
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
    for model, effort in (("fable", "medium"), ("opus", "high")):
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
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("fable", "medium")
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
    _record_refusal(plan_dir, "s01", "fable", "medium", capsys)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    assert (by_id["s01"]["model_arg"], by_id["s01"]["reasoning"]) == ("opus", "high")
    run.cmd_release(plan_dir)


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
    assert by_id["s01"]["model_arg"] == "fable"
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
GATE_FAIL = {"redx": {"kind": "argv", "argv": ["false"]}}


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


def _attempt(plan_dir, capsys):
    """One real dispatch: `begin` computes + RECORDS the rung, then releases the
    lock. Returns the dispatched batch member."""
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="TODO", note=None)
    by_id, _ = _begin(plan_dir, ["s01"], capsys)
    run.cmd_release(plan_dir)
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DOING", note=None)
    return by_id["s01"]


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
    assert "untried rungs above it: fable@medium" in reason, reason
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
        ("fable", "medium"),     # A failed a third time: rung 2
        ("fable", "medium"),     # B is a DIFFERENT error: progress, hold the rung
        ("fable", "high"),       # B failed twice: armed again, rung 3
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
    assert ("opus", "high") in seen and ("fable", "medium") in seen, seen
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
    for _ in range(2):
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
    for _ in range(2):
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert "escalation_next" in r                 # ALLOW CONTROL: Claude harness announces
    assert not r["escalation_next"]["ran"]["model"].startswith("gpt-")

    # Now plant the durable proof that this session's last dispatch was a
    # codex-harness one — the same event `begin --harness codex` writes.
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"], harness="codex")
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
    for _ in range(2):
        r = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    return r
