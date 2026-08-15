"""Replan primitives (S03): ack-checkpoint, redispatch, the halt hole, and
`depends_on_policy`.

Each block is a PLANT (the thing that must be refused / must not happen) paired
with an ALLOW (the same machinery succeeding), because a refusal test that would
pass against a no-op implementation proves nothing.

  RP-01  ack-checkpoint resolves a POST-SESSION AWAITS_REVIEW park; `--resume`
         no longer re-dispatches an already-finished session.
  RP-02  redispatch re-runs a finished session with a recorded reason, cascading
         to dependents by DEFAULT.
  Halt   `begin` guards the halt flag (previously only `plan` did).
  Deps   `depends_on_policy: completed_or_terminal` is honoured, so a capstone is
         not stranded forever by one upstream that closed BLOCKED.

Run: pytest skills/plan-execute/scripts/test_replan.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import dispatch as dsp  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
from test_shipping import make_plan  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------
def _sessions():
    """s01 -> s02 -> s03: a producer and two generations of consumers."""
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


def _closeout_text(sid, item, *, result="DONE", checkpoint=None):
    body = {
        "session": sid, "result": result, "items_completed": [item], "items_blocked": [],
        "notes": {item: "done", sid: f"{sid} complete"}, "dispatch_next": True,
        "human_checkpoint_reason": checkpoint,
    }
    return (f"work done.\n\n<plan-execute-closeout>\n{json.dumps(body)}\n"
            f"</plan-execute-closeout>\n")


def _run_session(plan_dir, sid, item, tmp_path, *, checkpoint=None, result="DONE"):
    """Drive one session through the REAL begin -> apply path."""
    out = Path(tmp_path) / f"{sid}.out.md"
    out.write_text(_closeout_text(sid, item, result=result, checkpoint=checkpoint))
    run.cmd_begin(plan_dir, [sid])
    try:
        run.cmd_apply(plan_dir, sid, str(out))
    except SystemExit as e:  # apply exits 1 only on a gate failure
        assert e.code in (0, None), f"apply exited {e.code}"
    run.cmd_release(plan_dir)


def _status(plan_dir, aid):
    return ab.read_status((Path(plan_dir) / "PLAN.html").read_text(), aid)


def _events(plan_dir, name=None):
    p = Path(plan_dir) / "run.ndjson"
    evs = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [e for e in evs if name is None or e.get("event") == name]


def _json_out(capsys):
    return json.loads(capsys.readouterr().out)


def _park_s01(plan_dir, tmp_path, capsys):
    """s01 runs, closes DONE, and parks at AWAITS_REVIEW on its checkpoint."""
    _run_session(plan_dir, "s01", "w-01", tmp_path, checkpoint="confirm the schema before s02")
    capsys.readouterr()
    assert _status(plan_dir, "s01") == "AWAITS_REVIEW"
    return plan_dir


# ==========================================================================
# RP-01 — ack-checkpoint
# ==========================================================================
# PLANT: a session that has not finished must never be ack-able. ack is an
# APPROVAL of recorded work, not a way to mark work complete.
@pytest.mark.parametrize("status", ["TODO", "DOING", "PARTIAL"])
def test_plant_ack_refused_on_unfinished_session(plan, status, capsys):
    if status != "TODO":
        ab.apply_mutation(Path(plan) / "PLAN.html", "s01", status=status, note="x")
    with pytest.raises(SystemExit) as exc:
        run.cmd_ack_checkpoint(plan, "s01")
    assert status in str(exc.value)
    assert _status(plan, "s01") == status
    assert not _events(plan, "checkpoint_approved")


# PLANT: a PRE-DISPATCH checkpoint (session never ran, no closeout on disk) must
# be refused — that gate asks whether to START the session, and `--resume`
# answers it. This is also the legacy-state safe default: no recorded closeout
# means never silently ack.
def test_plant_ack_refused_on_pre_dispatch_checkpoint(plan, capsys):
    run.cmd_checkpoint(plan, "s01")            # the real pre-dispatch gate path
    capsys.readouterr()
    assert _status(plan, "s01") == "AWAITS_REVIEW"
    assert run._awaits_review_kind(plan, "s01") == "pre_dispatch"
    with pytest.raises(SystemExit) as exc:
        run.cmd_ack_checkpoint(plan, "s01")
    assert "PRE-DISPATCH" in str(exc.value)
    assert _status(plan, "s01") == "AWAITS_REVIEW"


# PLANT: a PARTIAL session parked for review has a closeout on disk, but it is a
# continuation point — not a finished result to approve. The discriminator is the
# RECORDED RESULT, not merely the closeout's existence.
def test_plant_ack_refused_when_closeout_is_partial(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path, result="PARTIAL",
                 checkpoint="which half should I finish first?")
    capsys.readouterr()
    assert _status(plan, "s01") == "AWAITS_REVIEW"
    assert run._awaits_review_kind(plan, "s01") == "pre_dispatch"
    with pytest.raises(SystemExit) as exc:
        run.cmd_ack_checkpoint(plan, "s01")
    assert "result='PARTIAL'" in str(exc.value)
    assert _status(plan, "s01") == "AWAITS_REVIEW"


# PLANT (the headline bug): `--resume` must NOT re-dispatch a session that
# already closed DONE and was parked afterwards. Before this fix it did — redoing
# paid work and re-tripping the same checkpoint forever.
def test_plant_resume_does_not_redispatch_parked_done_session(plan, tmp_path, capsys):
    _park_s01(plan, tmp_path, capsys)
    assert run._awaits_review_kind(plan, "s01") == "post_session"

    run.cmd_plan(plan, True, None)             # --resume
    action = _json_out(capsys)
    assert action["action"] != "dispatch", action
    assert action["action"] == "checkpoint"
    assert action["sessions"] == ["s01"]
    assert "ack-checkpoint" in action["message"]
    assert action["ack_required"] == ["s01"]
    assert _status(plan, "s01") == "AWAITS_REVIEW"   # untouched, not re-dispatched

    # The other door: explicitly targeting it with --session must not re-run it.
    run.cmd_plan(plan, True, "s01")
    targeted = _json_out(capsys)
    assert targeted["action"] == "checkpoint"
    assert "ack-checkpoint" in targeted["message"]
    assert _status(plan, "s01") == "AWAITS_REVIEW"


# ALLOW: ack flips the park to DONE, records the approval, is idempotent, and the
# loop then advances to the NEXT session.
def test_allow_ack_flips_logs_is_idempotent_and_unblocks_the_loop(plan, tmp_path, capsys):
    _park_s01(plan, tmp_path, capsys)

    run.cmd_ack_checkpoint(plan, "s01")
    out = _json_out(capsys)
    assert out["acked"] is True
    assert _status(plan, "s01") == "DONE"
    approved = _events(plan, "checkpoint_approved")
    assert len(approved) == 1
    assert approved[0]["session_ids"] == ["s01"]
    assert approved[0]["reason"] == "confirm the schema before s02"

    # Idempotent: a repeat is a no-op success, not an error and not a second event.
    run.cmd_ack_checkpoint(plan, "s01")
    again = _json_out(capsys)
    assert again["already"] is True and again["acked"] is False
    assert _status(plan, "s01") == "DONE"
    assert len(_events(plan, "checkpoint_approved")) == 1

    # And the loop moves on to s02 instead of looping on s01.
    run.cmd_plan(plan, False, None)
    action = _json_out(capsys)
    assert action["action"] == "dispatch"
    assert [m["id"] for m in action["batch"]] == ["s02"]


# ==========================================================================
# RP-02 — redispatch
# ==========================================================================
# PLANT: no reason, no redispatch. The WHY is the only thing that survives a
# discarded result.
@pytest.mark.parametrize("reason", ["", "   ", None])
def test_plant_redispatch_refused_without_reason(plan, tmp_path, capsys, reason):
    _run_session(plan, "s01", "w-01", tmp_path)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        run.cmd_redispatch(plan, "s01", reason)
    assert "--reason" in str(exc.value)
    assert _status(plan, "s01") == "DONE"
    assert not _events(plan, "redispatch")


# PLANT: a session that never finished has no result to replace.
@pytest.mark.parametrize("status", ["TODO", "DOING", "PARTIAL", "AWAITS_REVIEW"])
def test_plant_redispatch_refused_on_non_terminal_session(plan, status, capsys):
    if status != "TODO":
        ab.apply_mutation(Path(plan) / "PLAN.html", "s01", status=status, note="x")
    with pytest.raises(SystemExit) as exc:
        run.cmd_redispatch(plan, "s01", "new facts")
    assert "not a finished state" in str(exc.value)
    assert _status(plan, "s01") == status


# ALLOW: a DONE session resets to TODO, its closeout survives under a versioned
# name, and the reason lands in the event log.
def test_allow_redispatch_resets_preserves_and_logs(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path)
    capsys.readouterr()
    assert _status(plan, "s01") == "DONE"
    assert (Path(plan) / "_closeouts" / "s01.json").exists()

    run.cmd_redispatch(plan, "s01", "upstream API changed shape")
    out = _json_out(capsys)

    assert out["redispatched"] is True and out["from_status"] == "DONE"
    assert _status(plan, "s01") == "TODO"
    assert _status(plan, "w-01") == "TODO"          # its item is invalid too
    # Preserved, and MOVED — a live closeout would still read as "already finished".
    assert (Path(plan) / "_closeouts" / "s01.r1.json").exists()
    assert not (Path(plan) / "_closeouts" / "s01.json").exists()
    ev = _events(plan, "redispatch")
    assert len(ev) == 1 and ev[0]["reason"] == "upstream API changed shape"
    assert "_closeouts/s01.r1.json" in ev[0]["archived"]["s01"]

    # A second round versions again rather than clobbering the first.
    _run_session(plan, "s01", "w-01", tmp_path)
    capsys.readouterr()
    run.cmd_redispatch(plan, "s01", "changed again")
    capsys.readouterr()
    assert (Path(plan) / "_closeouts" / "s01.r1.json").exists()
    assert (Path(plan) / "_closeouts" / "s01.r2.json").exists()

    # And the loop really will run it again.
    run.cmd_plan(plan, False, None)
    assert [m["id"] for m in _json_out(capsys)["batch"]] == ["s01"]


# ALLOW (and the adversarial-review default): dependents derived their output
# from a producer that no longer holds, so they reset TOO, transitively.
def test_allow_redispatch_cascades_dependents_by_default(plan, tmp_path, capsys):
    for sid, item in (("s01", "w-01"), ("s02", "w-02"), ("s03", "w-03")):
        _run_session(plan, sid, item, tmp_path)
    capsys.readouterr()
    assert all(_status(plan, s) == "DONE" for s in ("s01", "s02", "s03"))

    run.cmd_redispatch(plan, "s01", "source data was wrong")
    out = _json_out(capsys)

    assert out["cascaded"] == ["s02", "s03"]        # transitive, not just direct
    assert out["accepted_stale"] == []
    for sid in ("s01", "s02", "s03"):
        assert _status(plan, sid) == "TODO"
        assert (Path(plan) / "_closeouts" / f"{sid}.r1.json").exists()
    assert not _events(plan, "stale_dependents_accepted")


# PLANT for the opt-out: keeping stale dependents must never be SILENT.
def test_plant_allow_stale_dependents_is_recorded_not_silent(plan, tmp_path, capsys):
    for sid, item in (("s01", "w-01"), ("s02", "w-02"), ("s03", "w-03")):
        _run_session(plan, sid, item, tmp_path)
    capsys.readouterr()

    run.cmd_redispatch(plan, "s01", "cosmetic change only", allow_stale_dependents=True)
    out = _json_out(capsys)

    assert out["cascaded"] == [] and out["accepted_stale"] == ["s02", "s03"]
    # "Untouched" means untouched: the kept dependents are reported there too.
    assert out["dependents_untouched"] == {"s02": "DONE", "s03": "DONE"}
    assert _status(plan, "s01") == "TODO"
    assert _status(plan, "s02") == "DONE" and _status(plan, "s03") == "DONE"
    accepted = _events(plan, "stale_dependents_accepted")
    assert len(accepted) == 1
    assert accepted[0]["session_ids"] == ["s02", "s03"]
    assert accepted[0]["because_of"] == "s01"
    assert accepted[0]["reason"] == "cosmetic change only"


# A dependent parked at AWAITS_REVIEW is finished-and-awaiting-ack, so it is
# stale too; an in-flight (DOING) one is never yanked out from under itself.
def test_cascade_scope_awaits_review_resets_doing_is_left_alone(plan, tmp_path, capsys):
    _run_session(plan, "s01", "w-01", tmp_path)
    capsys.readouterr()
    ab.apply_mutation(Path(plan) / "PLAN.html", "s02", status="AWAITS_REVIEW", note="x")
    ab.apply_mutation(Path(plan) / "PLAN.html", "s03", status="DOING", note="x")

    run.cmd_redispatch(plan, "s01", "reason")
    out = _json_out(capsys)
    assert out["cascaded"] == ["s02"]
    assert out["dependents_untouched"] == {"s03": "DOING"}
    assert _status(plan, "s03") == "DOING"


# ==========================================================================
# Halt guard — the inherited hole: only `plan` checked it, so `begin` mutated
# TODO -> DOING straight through a halted plan.
# ==========================================================================
def test_plant_begin_refuses_while_halted(plan, capsys):
    rsi.set_halt(plan, "something went wrong", "s01")
    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan, ["s01"])
    assert "HALTED" in str(exc.value)
    assert _status(plan, "s01") == "TODO"          # no DOING flip happened
    assert not (Path(plan) / ".lock").exists()     # and no lock was taken


@pytest.mark.parametrize("cmd", ["ack", "redispatch"])
def test_plant_replan_subcommands_refuse_while_halted(plan, tmp_path, capsys, cmd):
    _run_session(plan, "s01", "w-01", tmp_path,
                 checkpoint="confirm" if cmd == "ack" else None)
    capsys.readouterr()
    rsi.set_halt(plan, "unrelated failure", "s02")
    before = _status(plan, "s01")
    with pytest.raises(SystemExit) as exc:
        if cmd == "ack":
            run.cmd_ack_checkpoint(plan, "s01")
        else:
            run.cmd_redispatch(plan, "s01", "new facts")
    assert "HALTED" in str(exc.value)
    assert _status(plan, "s01") == before


def test_allow_begin_dispatches_past_halt_with_resume(plan, capsys):
    rsi.set_halt(plan, "operator has reviewed and wants to proceed", "s01")
    run.cmd_begin(plan, ["s01"], resume=True)
    out = _json_out(capsys)
    assert [m["id"] for m in out["batch"]] == ["s01"]
    assert _status(plan, "s01") == "DOING"
    run.cmd_release(plan)


# ==========================================================================
# depends_on_policy — `completed_or_terminal` dispatches over the completed
# subset instead of being stranded forever by a terminally-failed upstream.
# ==========================================================================
def _policy_manifest(policy):
    return {
        "plan_schema_version": 2,
        "items": [{"id": "IT-1"}, {"id": "IT-2"}, {"id": "IT-3"}],
        "sessions": [
            {"id": "s01", "items": ["IT-1"], "dispatch": {}},
            {"id": "s02", "items": ["IT-2"], "dispatch": {}},
            {"id": "s99", "items": ["IT-3"],
             "dispatch": {"depends_on": ["s01", "s02"], "depends_on_policy": policy}},
        ],
    }


def test_plant_blocked_upstream_strands_an_all_consumer(capsys):
    statuses = {"s01": "DONE", "s02": "BLOCKED", "s99": "TODO"}
    ready = [s["id"] for s in dsp.ready_sessions(_policy_manifest("all"), statuses)]
    assert "s99" not in ready


def test_allow_completed_or_terminal_consumer_is_ready(capsys):
    statuses = {"s01": "DONE", "s02": "BLOCKED", "s99": "TODO"}
    ready = [s["id"]
             for s in dsp.ready_sessions(_policy_manifest("completed_or_terminal"), statuses)]
    assert ready == ["s99"]


def test_control_both_policies_identical_when_all_upstreams_done():
    statuses = {"s01": "DONE", "s02": "DONE", "s99": "TODO"}
    for policy in ("all", "completed_or_terminal"):
        ready = [s["id"] for s in dsp.ready_sessions(_policy_manifest(policy), statuses)]
        assert ready == ["s99"], policy


def test_control_pending_upstream_blocks_both_policies():
    # `completed_or_terminal` is not "dispatch whenever" — an upstream still
    # RUNNING must hold the consumer back under either policy.
    statuses = {"s01": "DONE", "s02": "DOING", "s99": "TODO"}
    for policy in ("all", "completed_or_terminal"):
        ready = [s["id"] for s in dsp.ready_sessions(_policy_manifest(policy), statuses)]
        assert ready == [], policy


def test_policy_survives_the_real_builder(tmp_path):
    # The key is written by plan-builder into every manifest; prove the pair
    # round-trips through a real build rather than only through a hand stub.
    sessions = [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
        {"id": "s99", "title": "Capstone", "model": "Sonnet", "items": ["w-99"], "prompt": "do",
         "dispatch": {"depends_on": ["s01"], "depends_on_policy": "completed_or_terminal"}},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    manifest = json.loads((plan_dir / "manifest.json").read_text())
    capstone = next(s for s in manifest["sessions"] if s["id"] == "s99")
    assert capstone["dispatch"]["depends_on_policy"] == "completed_or_terminal"
    ready = [s["id"] for s in dsp.ready_sessions(manifest, {"s01": "BLOCKED", "s99": "TODO"})]
    assert ready == ["s99"]
