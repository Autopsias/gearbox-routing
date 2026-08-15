"""RP-08 — the REPLAN brief carries its own recommendation.

The brief always named the invalidated sessions and offered exactly three
options, but its `recommendation` slot was always null and nothing anywhere
required it to be filled: a REPLAN could reach the operator, and be resolved,
with the judgement never supplied. The module still refuses to INVENT one — it
cannot know which sessions a specific discovery invalidated — so what is tested
here is that the empty slot is no longer SILENTLY optional.

Each block pairs a PLANT (the thing that must now be refused) with an ALLOW (the
same machinery succeeding once the judgement is recorded), because a refusal
test that would also pass against a no-op implementation proves nothing.

  Plant    `resolve-replan` refuses while the brief carries no recommendation,
           naming the missing field and the command that supplies it.
  Allow    `recommend-replan` records it; the brief resolves cleanly and the
           recommendation appears VERBATIM in HALT_NOTICE.txt.
  Control  a plan stamped below `RECOMMENDATION_MIN_SCHEMA` — and a park raised
           before this feature existed — resolve exactly as they did before.

Run: pytest skills/plan-execute/scripts/test_replan_recommendation.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import replan as rp  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_shipping import make_plan  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def _sessions():
    return [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
         "dispatch": {"depends_on": ["s01"]}},
        {"id": "s03", "title": "S3", "model": "Sonnet", "items": ["w-03"], "prompt": "do",
         "dispatch": {"depends_on": ["s02"]}},
    ]


@pytest.fixture
def plan(tmp_path):
    return str(make_plan(tmp_path, _sessions()))


IMPACT = {"invalidates": ["s02", "s03"],
          "reason": "the vendor API we planned s02/s03 around was deprecated this week"}

REC = "amend — s02/s03 are 80% reusable against the replacement endpoint, retiring them loses that"


def _restamp(plan_dir, version):
    """Restamp a built plan's `plan_schema_version` — an older in-flight plan."""
    p = Path(plan_dir) / "manifest.json"
    m = json.loads(p.read_text())
    m["plan_schema_version"] = version
    p.write_text(json.dumps(m, indent=2))


def _park(plan_dir, tmp_path, capsys, impact=IMPACT):
    """Drive s01 through the REAL begin -> apply path with a `plan_impact`."""
    body = {
        "session": "s01", "result": "DONE", "items_completed": ["w-01"],
        "items_blocked": [], "notes": {"w-01": "done", "s01": "s01 complete"},
        "dispatch_next": True, "human_checkpoint_reason": None, "plan_impact": impact,
    }
    out = Path(tmp_path) / "s01.out.md"
    out.write_text(f"work\n\n<plan-execute-closeout>\n{json.dumps(body)}\n"
                   f"</plan-execute-closeout>\n")
    run.cmd_begin(plan_dir, ["s01"])
    capsys.readouterr()
    run.cmd_apply(plan_dir, "s01", str(out))
    applied = json.loads(capsys.readouterr().out)
    run.cmd_release(plan_dir)
    capsys.readouterr()
    return applied


def _notice(plan_dir):
    return (Path(plan_dir) / "HALT_NOTICE.txt").read_text()


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    evs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return [e for e in evs if e.get("event") == name]


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def _amend_s02(plan_dir, capsys):
    class _Args:
        session, depends_on, model, reasoning = "s02", None, None, None
        prompt, allow_builder_drift = "use the replacement API", False
    run.cmd_amend_session(plan_dir, _Args())
    capsys.readouterr()


# ==========================================================================
# PLANT — a brief with no recommendation cannot be resolved
# ==========================================================================
@pytest.mark.parametrize("decision", ["proceed", "amend", "retire"])
def test_plant_resolve_refused_while_the_brief_has_no_recommendation(
    plan, tmp_path, capsys, decision
):
    _park(plan, tmp_path, capsys)
    _amend_s02(plan, capsys)          # the amend/retire change-log gate is satisfied

    with pytest.raises(SystemExit) as exc:
        run.cmd_resolve_replan(plan, "s01", decision, "decided")
    msg = str(exc.value)

    # The refusal NAMES what is missing and how to supply it.
    assert "recommendation" in msg
    assert "recommend-replan" in msg and "--session s01" in msg
    # Nothing was written: the park stands, the halt stands.
    assert rsi.is_halted(plan)
    assert [p["session"] for p in rp.pending(plan)] == ["s01"]
    assert rp.load(plan, "s01")["resolved"] is False


def test_plant_missing_recommendation_is_refused_before_the_change_log_gate(
    plan, tmp_path, capsys
):
    """Ordering matters: the operator needs the judgement BEFORE they choose, so
    the incomplete brief is the first thing named — not a paperwork failure
    discovered after they have already acted."""
    _park(plan, tmp_path, capsys)
    with pytest.raises(SystemExit) as exc:
        run.cmd_resolve_replan(plan, "s01", "amend", "trust me")
    assert "recommendation" in str(exc.value)
    assert "change log" not in str(exc.value)


def test_plant_the_park_itself_announces_the_missing_recommendation(plan, tmp_path, capsys):
    """Loud, not silent: the operator's own surfaces say the brief is incomplete."""
    applied = _park(plan, tmp_path, capsys)
    assert applied["replan"]["needs_recommendation"] is True
    assert "recommend-replan" in applied["next"]

    notice = _notice(plan)
    assert "RECOMMENDATION: NOT RECORDED" in notice
    assert "recommend-replan" in notice
    # …alongside the options, which the notice never used to carry at all.
    assert "1. amend" in notice and "2. retire" in notice and "3. proceed" in notice
    assert IMPACT["reason"] in notice

    run.cmd_plan(plan, False, None)
    action = _json_out(capsys)
    assert action["action"] == "replan"
    assert action["replan_needs_recommendation"] == ["s01"]
    assert "recommend-replan" in action["hint"]
    assert "RECOMMENDATION: NOT RECORDED" in action["replan_text"]


# ==========================================================================
# ALLOW — record the judgement, then resolve
# ==========================================================================
def test_allow_recorded_recommendation_resolves_and_renders_verbatim(plan, tmp_path, capsys):
    _park(plan, tmp_path, capsys)

    run.cmd_recommend_replan(plan, "s01", REC)
    recorded = _json_out(capsys)
    assert recorded["recorded"] is True and recorded["recommendation"] == REC
    assert recorded["halt_notice_updated"] is True
    assert REC in recorded["brief_text"]

    # It renders where the operator already looks: the halt notice, verbatim…
    assert f"RECOMMENDATION: {REC}" in _notice(plan)
    assert "NOT RECORDED" not in _notice(plan)
    # …and the plan action's brief, beside the options.
    run.cmd_plan(plan, False, None)
    action = _json_out(capsys)
    assert action["replan"][0]["recommendation"] == REC
    assert f"RECOMMENDATION: {REC}" in action["replan_text"]
    assert "replan_needs_recommendation" not in action
    assert "recommend-replan" not in action["hint"]

    # And the decision now resolves cleanly, carrying the judgement into the record.
    _amend_s02(plan, capsys)
    run.cmd_resolve_replan(plan, "s01", "amend", "s02 rewritten for the replacement API")
    out = _json_out(capsys)
    assert out["resolved"] is True and out["halt_cleared"] is True
    assert out["recommendation"] == REC
    assert rp.load(plan, "s01")["resolution"]["recommendation"] == REC
    assert _events(plan, "replan_resolved")[0]["recommendation"] == REC
    assert not rsi.is_halted(plan)

    # The loop advances again.
    run.cmd_plan(plan, False, None)
    assert [m["id"] for m in _json_out(capsys)["batch"]] == ["s02"]


def test_allow_recording_is_recorded_state_not_a_second_halt(plan, tmp_path, capsys):
    """Recording a recommendation changes the BRIEF, not the halt: no second
    `halt_set`, and the notice keeps reporting when the plan actually stopped."""
    _park(plan, tmp_path, capsys)
    halts_before = len(_events(plan, "halt_set"))
    at_before = rsi.load_state(plan)["halt"]["at"]

    run.cmd_recommend_replan(plan, "s01", REC)
    capsys.readouterr()

    assert len(_events(plan, "halt_set")) == halts_before
    assert rsi.load_state(plan)["halt"]["at"] == at_before
    assert f"PLAN HALTED at {at_before}" in _notice(plan)
    ev = _events(plan, "replan_recommendation")
    assert len(ev) == 1 and ev[0]["recommendation"] == REC and ev[0]["replaced"] is None


def test_allow_recommendation_survives_an_idempotent_re_park(plan, tmp_path, capsys):
    """A crash replay re-parks the same record; the judgement was made once and
    must stay made."""
    _park(plan, tmp_path, capsys)
    run.cmd_recommend_replan(plan, "s01", REC)
    capsys.readouterr()
    manifest = json.loads((Path(plan) / "manifest.json").read_text())

    again = rp.park(plan, "s01", IMPACT, manifest)
    assert again["brief"]["recommendation"] == REC
    assert again["needs_recommendation"] is False
    assert rp.needs_recommendation(rp.load(plan, "s01")) is False


# ==========================================================================
# PLANT — recording refusals
# ==========================================================================
def test_plant_recommend_refusals(plan, tmp_path, capsys):
    # Empty judgement is not a judgement.
    _park(plan, tmp_path, capsys)
    with pytest.raises(SystemExit) as exc:
        run.cmd_recommend_replan(plan, "s01", "   ")
    assert "empty recommendation" in str(exc.value)
    assert rp.needs_recommendation(rp.load(plan, "s01")) is True

    # A session with no park has nothing to recommend on.
    with pytest.raises(SystemExit) as exc2:
        run.cmd_recommend_replan(plan, "s02", REC)
    assert "no REPLAN park" in str(exc2.value)

    # Advice recorded after the decision advises nobody.
    run.cmd_recommend_replan(plan, "s01", REC)
    capsys.readouterr()
    run.cmd_resolve_replan(plan, "s01", "proceed", "s02/s03 never touched that endpoint")
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc3:
        run.cmd_recommend_replan(plan, "s01", "too late")
    assert "already resolved" in str(exc3.value)
    assert rp.load(plan, "s01")["brief"]["recommendation"] == REC


# ==========================================================================
# CONTROL — nothing in flight gains the requirement retroactively
# ==========================================================================
def test_control_plan_below_the_gate_resolves_without_a_recommendation(
    plan, tmp_path, capsys
):
    """v4: `plan_impact` is live (>= 3) but the recommendation requirement is
    not (< 5). The SAME park resolves with no recommendation at all."""
    _restamp(plan, rp.RECOMMENDATION_MIN_SCHEMA - 1)
    applied = _park(plan, tmp_path, capsys)

    assert applied["replan"]["parked"] is True          # still parks
    assert applied["replan"]["needs_recommendation"] is False
    assert applied["replan"]["brief"]["recommendation_required"] is False
    assert "NOT RECORDED" not in _notice(plan)

    run.cmd_resolve_replan(plan, "s01", "proceed", "s02/s03 still hold")
    out = _json_out(capsys)
    assert out["resolved"] is True and out["halt_cleared"] is True
    assert out["recommendation"] is None
    assert not rsi.is_halted(plan)


def test_control_a_park_raised_before_this_feature_is_never_blocked(plan, tmp_path, capsys):
    """Belt and braces on the version stamp: a park RECORD written by the old
    code carries no `recommendation_required` flag. It must resolve untouched
    even on a plan whose stamp is new enough to require one."""
    _park(plan, tmp_path, capsys)
    rec = rp.load(plan, "s01")
    del rec["brief"]["recommendation_required"]          # a pre-RP-08 record
    rp._write_record(plan, "s01", rec)

    assert rp.needs_recommendation(rp.load(plan, "s01")) is False
    run.cmd_resolve_replan(plan, "s01", "proceed", "s02/s03 still hold")
    assert _json_out(capsys)["resolved"] is True
    assert not rsi.is_halted(plan)


def test_control_the_gate_is_what_makes_the_difference(plan, tmp_path, capsys):
    """The pairing that stops the two controls above from passing against an
    implementation that requires nothing: same plan, same closeout, stamped at
    the gate — and the resolve is refused."""
    _restamp(plan, rp.RECOMMENDATION_MIN_SCHEMA)
    _park(plan, tmp_path, capsys)
    with pytest.raises(SystemExit) as exc:
        run.cmd_resolve_replan(plan, "s01", "proceed", "s02/s03 still hold")
    assert "recommendation" in str(exc.value)
    assert rsi.is_halted(plan)
