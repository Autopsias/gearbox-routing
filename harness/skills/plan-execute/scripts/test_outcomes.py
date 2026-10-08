"""TEL-01 — the outcome ledger writer.

Covers, per the plan's contract:
  * record shape (schema-validated: required keys, `result` enum, JSON-safe)
  * one write per resolution moment, through the REAL verify/apply fixtures:
    verify-finalize (passed), verify rework, rework exhausted (halt),
    apply of a terminal DONE/BLOCKED closeout with no verify block, and
    retire-session (wontfix) from TODO, PARTIAL, and BLOCKED separately
  * a CONCURRENT-WRITER interleaving test (real OS processes under flock)
  * short-write handling (the write loop, isolated from real I/O)
  * swallow-on-failure (an observer must never crash its caller)
  * kill-switch (PLAN_EXECUTE_NO_OUTCOME_LEDGER)
  * ledger-path env override (PLAN_EXECUTE_OUTCOME_LEDGER)
  * replay idempotency on every resolution path (record_id dedup)
  * the record-receipt attestation handoff (attested / requested / unknown)

Every test writes to an ISOLATED ledger path (autouse fixture) — never the
real repo's tracked `evals/routing/outcomes.ndjson`.

Run: pytest skills/plan-execute/scripts/test_outcomes.py -q
"""

import pathlib

import json
import subprocess
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
import attestation  # noqa: E402
import outcomes as outc  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import verify as vfy  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """Every test gets its OWN ledger file — never the real tracked one."""
    path = tmp_path / "ledger" / "outcomes.ndjson"
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(path))
    return path


def _read_ledger(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


# --------------------------------------------------------------------------
# Fixture builder (mirrors test_verify.py / test_shipping.py)
# --------------------------------------------------------------------------
def _spec(sessions):
    items = sorted({iid for s in sessions for iid in s.get("items", [])})
    return {
        "title": "Outcomes Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": iid, "title": iid.upper(), "category": "work"} for iid in items],
        "phases": [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": items[:1]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def make_plan(tmp_path, sessions, *, gates=None):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text("{}")
    (cl / "eval-gates.json").write_text(json.dumps(gates or {}))
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec(sessions), plan_dir, project_root=str(project_root))
    return plan_dir


def write_closeout(plan_dir, session_id, *, result="DONE", completed=None, extra=None):
    manifest = mio.load_manifest(plan_dir)
    items = mio.session_by_id(manifest)[session_id]["items"]
    completed = items if completed is None else completed
    parsed = {"session": session_id, "result": result, "items_completed": completed,
              "items_blocked": [i for i in items if i not in completed], "notes": {}}
    parsed.update(extra or {})
    cp.persist(plan_dir, session_id, parsed)


def _begin_doing(plan_dir, sid):
    ab.apply_mutation(plan_dir / "PLAN.html", sid, status="DOING", note=None)


GATE_PASS = {"smoke": {"kind": "argv", "argv": ["true"]}}
GATE_FAIL = {"redx": {"kind": "argv", "argv": ["false"]}}
SESS = {"id": "s01", "title": "Session 1", "items": ["it-1"], "model": "Sonnet",
       "reasoning": "high", "task_class": "standard_build", "prompt": "do it"}


def _verify_sess(gates, on_fail="rework", max_rework=1, **extra):
    return {**SESS, "verify": {"gates": gates, "on_fail": on_fail, "max_rework": max_rework}, **extra}


# --------------------------------------------------------------------------
# Record shape
# --------------------------------------------------------------------------
def test_record_shape_is_schema_valid(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="passed", verified=True,
                       attempt=2, rework_count=1, gates_failed=["a", "b", "c", "d"],
                       stuck_armed=True)
    required = {
        "ts", "project", "plan", "session", "generation", "task_class", "backend",
        "model_authored", "reasoning_authored", "tier_authored", "model_ran",
        "reasoning_ran", "tier_ran", "model_ran_source", "effort_mechanism",
        "degraded_from", "escalated_from", "routing_experiment", "routing_provenance",
        "ssot_version_ran", "attempt", "rework_count", "stuck_armed", "gates_failed",
        "result", "verified", "record_id",
    }
    assert required <= set(rec)
    assert rec["result"] in outc.RESULTS
    assert rec["session"] == "s01"
    assert rec["plan"] == "fixture"
    assert len(rec["gates_failed"]) == 3          # truncated to 3
    # OPERATOR DECISION 2026-08-15: no receipt no longer collapses to `unknown`;
    # the honest claim is what was ASKED for, recorded as `requested`.
    assert rec["model_ran_source"] == outc.SOURCE_REQUESTED  # no receipt recorded
    assert rec["model_ran"] == rec["model_authored"]
    json.dumps(rec)  # must be JSON-serializable end to end
    assert "cost_usd" not in rec
    assert "context_reset" not in rec


def test_result_enum_is_refused_when_unknown(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    out = outc.write(plan_dir, "s01", resolution="unit", result="not-a-real-result", verified=True)
    assert out["written"] is False
    assert not isolated_ledger.exists() or _read_ledger(isolated_ledger) == []


# --------------------------------------------------------------------------
# One write per resolution moment — through the REAL verify pipeline
# --------------------------------------------------------------------------
def test_verify_finalize_passed_writes_one_record(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [_verify_sess(["smoke"])], gates=GATE_PASS)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    out = vfy.verify_begin(plan_dir, "s01")
    assert out["action"] == "run-argv"
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:smoke")
    assert out["action"] == "passed"
    fin = vfy.verify_finalize(plan_dir, "s01")
    assert fin["action"] == "done"

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "passed"
    assert recs[0]["verified"] is True
    assert recs[0]["session"] == "s01"

    # REPLAY IDEMPOTENCY: verify_finalize can be re-invoked after outcome=passed
    # (the caller side documents this explicitly) — it must not duplicate.
    fin2 = vfy.verify_finalize(plan_dir, "s01")
    assert fin2["action"] == "done"
    assert len(_read_ledger(isolated_ledger)) == 1


def test_verify_rework_writes_one_record_per_attempt(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [_verify_sess(["redx"], max_rework=1)], gates=GATE_FAIL)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert out["action"] == "rework"

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "rework"
    assert recs[0]["verified"] is False
    assert recs[0]["attempt"] == 1
    assert recs[0]["gates_failed"] == ["gate:redx"]

    # A SECOND rework attempt (re-dispatch -> fresh verify pass) still produces
    # its OWN record — the ledger counts every attempt.
    write_closeout(plan_dir, "s01")                    # the re-dispatch applied a new closeout
    vfy.verify_begin(plan_dir, "s01")
    out2 = vfy.verify_run_argv(plan_dir, "s01", "gate:redx")
    assert out2["action"] == "halted"  # max_rework=1 exhausted on the 2nd failure
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 2
    assert recs[1]["result"] == "exhausted"
    assert recs[1]["verified"] is False


def test_apply_terminal_done_with_no_verify_block_writes_done_unverified(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])  # no `verify` key at all
    run.cmd_begin(plan_dir, ["s01"])
    tmp = tmp_path / "closeout-s01.txt"
    tmp.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp))

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "done_unverified"
    assert recs[0]["verified"] is False


def test_apply_terminal_blocked_writes_blocked(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_begin(plan_dir, ["s01"])
    tmp = tmp_path / "closeout-s01.txt"
    tmp.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"BLOCKED","items_completed":[],'
        '"items_blocked":["it-1"],"notes":{},'
        '"decision_brief":{"attempts":["tried it"],'
        '"findings":[{"source":"none","takeaway":"nothing found"}],'
        '"options":["stop"],"recommendation":"stop"}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp))

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "blocked"
    assert recs[0]["verified"] is False


def test_apply_terminal_reapplied_same_generation_writes_second_record(tmp_path, isolated_ledger):
    """TEL-01 finding 3 — a session re-applied WITHOUT an intervening
    redispatch/amend (same escalation generation: nothing here ever bumps it,
    e.g. an orchestrator retry after a bad first closeout, or a corrected
    resubmission) must still get its own ledger record. `apply_terminal`'s
    `resolution` string is the SAME constant regardless of DONE vs BLOCKED, so
    before the fix (hardcoded `attempt=1`) the second, genuinely different
    resolution collided on `record_id` with the first and was silently
    dropped — corrupting attempts-per-success. This fails without the fix:
    only 1 record would exist, and it would be the FIRST (blocked) one."""
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_begin(plan_dir, ["s01"])
    tmp = tmp_path / "closeout-s01.txt"
    tmp.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"BLOCKED","items_completed":[],'
        '"items_blocked":["it-1"],"notes":{},'
        '"decision_brief":{"attempts":["tried it"],'
        '"findings":[{"source":"none","takeaway":"nothing found"}],'
        '"options":["stop"],"recommendation":"stop"}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp))
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "blocked"
    assert recs[0]["attempt"] == 1
    gen_first = recs[0]["generation"]

    # No `redispatch`/`amend-session` in between — same escalation generation,
    # a corrected closeout resubmitted straight to `apply` (a realistic
    # orchestrator retry: nothing in `cmd_apply` refuses re-applying a
    # terminal session).
    tmp2 = tmp_path / "closeout-s01-retry.txt"
    tmp2.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp2))

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 2, "the second resolution was dropped as a false duplicate"
    assert recs[1]["generation"] == gen_first  # no generation bump happened
    assert recs[1]["result"] == "done_unverified"
    assert recs[1]["attempt"] == 2
    assert recs[0]["record_id"] != recs[1]["record_id"]


def test_apply_partial_writes_nothing(tmp_path, isolated_ledger):
    """PARTIAL is not a terminal resolution the plan enumerates — no verify path,
    no apply-terminal path produces it, so the ledger must stay silent."""
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_begin(plan_dir, ["s01"])
    tmp = tmp_path / "closeout-s01.txt"
    tmp.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"PARTIAL","items_completed":[],'
        '"items_blocked":[],"notes":{}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp))
    assert _read_ledger(isolated_ledger) == []


# --------------------------------------------------------------------------
# Retirement (WONTFIX) — TODO, PARTIAL, BLOCKED, separately
# --------------------------------------------------------------------------
class _RetireArgs:
    def __init__(self, session):
        self.session = session
        self.reason = "no longer needed"
        self.cascade = False
        self.drop_dependency = False
        self.allow_builder_drift = False


def test_retire_from_todo_writes_wontfix(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_retire_session(plan_dir, _RetireArgs("s01"))
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "wontfix"
    assert recs[0]["verified"] is False


def test_retire_from_partial_writes_wontfix(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="PARTIAL", note=None)
    run.cmd_retire_session(plan_dir, _RetireArgs("s01"))
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "wontfix"


def test_retire_from_blocked_writes_wontfix(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="BLOCKED", note=None)
    run.cmd_retire_session(plan_dir, _RetireArgs("s01"))
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["result"] == "wontfix"


def test_retire_replay_is_idempotent(tmp_path, isolated_ledger):
    """Retiring an already-WONTFIX session is refused by plan_mutate (nothing to
    retire twice) — proven here as a NO-OP on the ledger, not a duplicate."""
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_retire_session(plan_dir, _RetireArgs("s01"))
    assert len(_read_ledger(isolated_ledger)) == 1
    with pytest.raises(SystemExit):
        run.cmd_retire_session(plan_dir, _RetireArgs("s01"))
    assert len(_read_ledger(isolated_ledger)) == 1


# --------------------------------------------------------------------------
# The attestation handoff (record-receipt)
# --------------------------------------------------------------------------
def test_record_receipt_no_transcript_is_requested_not_attested(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_record_receipt(plan_dir, "s01", "agent-123", None, "claude")
    receipt = rsi.get_receipt(plan_dir, "s01")
    assert receipt["agent_id"] == "agent-123"
    assert receipt["attested"] is None

    out = outc.write(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    rec = _read_ledger(isolated_ledger)[0]
    assert rec["model_ran_source"] == outc.SOURCE_REQUESTED
    assert rec["model_ran"] == "sonnet"
    assert out["written"] is True


def test_stale_receipt_never_contributes_attestation(tmp_path, isolated_ledger):
    """TEL-01 finding 1, still enforced after the 2026-08-15 operator decision.

    A receipt recorded for attempt 1 must NOT be read as PROOF for attempt 2's
    resolution just because `record-receipt` was skipped on the re-dispatch. What
    changed is only the fallback: the record now reads `requested` + THIS
    resolution's cell instead of `unknown`. What did NOT change, and is the point
    of the (generation, dispatch-ordinal) check, is that a stale receipt can never
    contribute ATTESTED — attempt 1's proof is not attempt 2's."""
    plan_dir = make_plan(tmp_path, [SESS])  # no verify block
    run.cmd_begin(plan_dir, ["s01"])  # dispatch #1
    # Attempt 1's receipt must CARRY ATTESTATION, or this test cannot fail: with a
    # bare receipt both the fresh and the stale path yield `requested`, so removing
    # the freshness check would change nothing and the assertion below would pass
    # over the very defect it exists to catch. (Caught by probing, 2026-08-15.)
    transcript = tmp_path / "attempt-1-headless.json"
    transcript.write_text(json.dumps({"modelUsage": {
        "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 5},
        "claude-opus-4-6": {"inputTokens": 900, "outputTokens": 400},
    }}))
    run.cmd_record_receipt(plan_dir, "s01", "agent-attempt-1", str(transcript), "claude")
    receipt = rsi.get_receipt(plan_dir, "s01")
    assert receipt["generation"] == 0
    assert receipt["attempt"] == 1
    assert (receipt.get("attested") or {}).get("model")   # the fixture really attests

    # Attempt 2: a re-dispatch happens (another `begin`) but the orchestrator
    # skips `record-receipt` this time — the stale attempt-1 receipt is still
    # on file (get_receipt never clears it on its own).
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"])

    tmp = tmp_path / "closeout-s01.txt"
    tmp.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(tmp))

    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert recs[0]["model_ran_source"] == outc.SOURCE_REQUESTED
    assert recs[0]["model_ran_source"] != outc.SOURCE_ATTESTED   # the property that matters
    assert recs[0]["model_ran"] == recs[0]["model_authored"]
    # and specifically NOT attempt 1's attested model leaking into attempt 2
    assert "opus" not in str(recs[0]["model_ran"]).lower()


def test_record_receipt_with_modelusage_transcript_attests(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    transcript = tmp_path / "headless-result.json"
    transcript.write_text(json.dumps({
        "modelUsage": {
            "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 5},
            "claude-sonnet-4-6": {"inputTokens": 900, "outputTokens": 400},
        }
    }))
    run.cmd_record_receipt(plan_dir, "s01", "agent-123", str(transcript), "claude")
    outc.write(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    rec = _read_ledger(isolated_ledger)[0]
    assert rec["model_ran_source"] == outc.SOURCE_ATTESTED
    assert rec["model_ran"] == "claude-sonnet-4-6"   # helper haiku key excluded


def test_attest_picks_highest_token_model_not_arbitrary_key(tmp_path):
    """TEL-01 finding 2 — with two non-haiku entries, the JSON key that happens
    to sort/iterate first must NOT win. This fails without the fix (the old
    `non_helper[0]` picks whichever dict key iteration puts first, which here
    is `claude-opus-4-6` — the one with FEWER tokens, i.e. the wrong model)."""
    transcript = tmp_path / "headless-result.json"
    transcript.write_text(json.dumps({
        "modelUsage": {
            "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 5},
            "claude-opus-4-6": {"inputTokens": 50, "outputTokens": 20},
            "claude-sonnet-4-6": {"inputTokens": 900, "outputTokens": 400},
        }
    }))
    attested = attestation.attest_from_transcript("claude", str(transcript))
    assert attested == {"model": "claude-sonnet-4-6", "reasoning": None}


def test_attest_refuses_to_guess_on_a_genuine_tie(tmp_path):
    """A tied max token count between two non-haiku models is genuinely
    ambiguous — refuse rather than name one with false confidence."""
    transcript = tmp_path / "headless-result.json"
    transcript.write_text(json.dumps({
        "modelUsage": {
            "claude-opus-4-6": {"inputTokens": 500, "outputTokens": 200},
            "claude-sonnet-4-6": {"inputTokens": 500, "outputTokens": 200},
        }
    }))
    assert attestation.attest_from_transcript("claude", str(transcript)) is None


def test_codex_backend_never_attests_even_with_a_transcript(tmp_path):
    """MEASURED (judge.py): the codex exec --json stream carries no served-model
    field in any envelope — the cap is real, not a missing feature."""
    fake_stream = "does not matter — codex attestation is never attempted"
    assert attestation.attest_from_transcript("codex", fake_stream) is None


def test_no_receipt_records_what_was_asked_for_never_attested(tmp_path, isolated_ledger):
    """A closeout's own `escalated_from` is still not PROOF — only a fresh receipt
    earns `attested`. After the 2026-08-15 operator decision it does, however, earn
    `requested`: the escalated cell is what the orchestrator actually asked for, so
    that is what the ledger records."""
    plan_dir = make_plan(tmp_path, [SESS])
    write_closeout(plan_dir, "s01", extra={
        "escalated_from": {"authored": {"model": "sonnet", "reasoning": "high"},
                           "ran": {"model": "fable", "reasoning": "xhigh"},
                           "attempt": 3, "rung": 2, "generation": 0},
    })
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    assert rec["model_ran_source"] == outc.SOURCE_REQUESTED
    assert rec["model_ran_source"] != outc.SOURCE_ATTESTED   # never proof
    assert rec["model_ran"] == "fable"                       # what was ASKED for on this attempt
    assert rec["escalated_from"]["ran"]["model"] == "fable"  # still carried through verbatim


# --------------------------------------------------------------------------
# Kill switch / env override / swallow-on-failure
# --------------------------------------------------------------------------
def test_kill_switch_disables_the_writer(tmp_path, isolated_ledger, monkeypatch):
    plan_dir = make_plan(tmp_path, [SESS])
    monkeypatch.setenv(outc.KILL_SWITCH_ENV, "1")
    out = outc.write(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert out == {"written": False, "reason": "kill_switch"}
    assert not isolated_ledger.exists()


def test_ledger_path_env_override_is_honored(tmp_path, monkeypatch):
    plan_dir = make_plan(tmp_path, [SESS])
    custom = tmp_path / "elsewhere" / "custom.ndjson"
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(custom))
    assert outc.ledger_path() == custom
    outc.write(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert custom.exists()
    assert len(_read_ledger(custom)) == 1


def test_swallow_on_failure_never_raises(tmp_path, monkeypatch):
    """A ledger path whose PARENT is a plain file (not a directory) makes every
    mkdir/open fail — write() must still return cleanly, never raise."""
    plan_dir = make_plan(tmp_path, [SESS])
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file, not a directory")
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(blocker / "outcomes.ndjson"))
    out = outc.write(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert out["written"] is False
    assert out["reason"] == "error"


# --------------------------------------------------------------------------
# Short-write handling (isolated from real file I/O)
# --------------------------------------------------------------------------
class _FlakyWriter:
    """A file-like object whose .write() only ever consumes a few bytes at a
    time — proves `_write_full`'s retry loop, not the OS's real behavior."""

    def __init__(self, chunk=3):
        self.chunk = chunk
        self.buf = bytearray()

    def write(self, data):
        n = min(self.chunk, len(data))
        self.buf.extend(data[:n])
        return n


def test_write_full_loops_on_short_writes():
    fh = _FlakyWriter(chunk=3)
    payload = b'{"hello":"world","n":12345}\n'
    written = outc._write_full(fh, payload)
    assert written == len(payload)
    assert bytes(fh.buf) == payload


# --------------------------------------------------------------------------
# Replay idempotency at the compose/append layer directly
# --------------------------------------------------------------------------
def test_append_dedup_does_not_duplicate_a_replayed_record(tmp_path, isolated_ledger):
    plan_dir = make_plan(tmp_path, [SESS])
    for _ in range(3):
        out = outc.write(plan_dir, "s01", resolution="verify_finalize", result="passed",
                         verified=True, attempt=1, rework_count=0)
    recs = _read_ledger(isolated_ledger)
    assert len(recs) == 1
    assert out["duplicate"] is True


# --------------------------------------------------------------------------
# Concurrent writers (real OS processes) — flock must serialize whole lines
# --------------------------------------------------------------------------
def test_concurrent_writers_produce_only_whole_lines(tmp_path):
    ledger = tmp_path / "concurrent.ndjson"
    n_procs, n_each = 6, 15
    script = (
        "import json, sys; sys.path.insert(0, %r); import outcomes as outc\n"
        "path = %r\n"
        "proc = int(sys.argv[1])\n"
        "for i in range(%d):\n"
        "    rec = {'record_id': f'p{proc}-{i}', 'i': i, 'proc': proc, "
        "'payload': 'x' * 200}\n"
        "    outc._append_dedup(__import__('pathlib').Path(path), rec)\n"
    ) % (str(SCRIPTS), str(ledger), n_each)
    script_path = tmp_path / "writer.py"
    script_path.write_text(script)

    procs = [
        subprocess.Popen([sys.executable, str(script_path), str(i)])
        for i in range(n_procs)
    ]
    for p in procs:
        assert p.wait() == 0

    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(lines) == n_procs * n_each
    seen = set()
    for ln in lines:
        rec = json.loads(ln)  # every line must be a WHOLE, parseable JSON object
        assert rec["record_id"] not in seen
        seen.add(rec["record_id"])


# ---------------------------------------------------------------------------
# Round-2 review findings (orchestrator fix, 2026-08-15): the apply-side attempt
# ordinal and rework_count must not invent numbers that disagree with verify.py.
# ---------------------------------------------------------------------------
def test_apply_attempt_resets_at_a_generation_bump(tmp_path, monkeypatch):
    """_apply_attempt counted batch_completed across ALL generations while its
    docstring promised a per-generation reset. The ordinal then kept climbing
    through a redispatch and disagreed with verify.py's rework_count-derived
    attempt — two yardsticks feeding one attempts-per-success metric.

    Fails without the fix: the third call returns 3 instead of 1.
    """
    import json as _json

    import run as _run

    plan = tmp_path / "plan"
    plan.mkdir()
    nd = plan / "run.ndjson"

    def log(event, **kw):
        with nd.open("a") as fh:
            fh.write(_json.dumps({"event": event, "session_ids": ["s01"], **kw}) + "\n")

    assert _run._apply_attempt(plan, "s01") == 1
    log("batch_completed")
    assert _run._apply_attempt(plan, "s01") == 2
    log("batch_completed")
    assert _run._apply_attempt(plan, "s01") == 3

    # A redispatch/amend bumps the generation -> new cohort, ordinal restarts.
    log("escalation_reset", generation=1)
    assert _run._apply_attempt(plan, "s01") == 1, (
        "a generation bump must restart the apply ordinal; counting across "
        "generations mixes two cohorts in one metric"
    )
    log("batch_completed")
    assert _run._apply_attempt(plan, "s01") == 2

    # Another session's events must never move this session's ordinal.
    with nd.open("a") as fh:
        fh.write(_json.dumps({"event": "batch_completed", "session_ids": ["s02"]}) + "\n")
    assert _run._apply_attempt(plan, "s01") == 2


def test_terminal_and_retire_records_never_fabricate_a_rework_count(tmp_path, monkeypatch):
    """`rework_count` was written as `_attempt - 1` at apply_terminal and
    administrative_retire. Both fire for sessions that ran NO gate at all, so a
    re-application was recorded as a gate rework and polluted any aggregate that
    mixes these rows with verify.py's real counts.

    Fails without the fix: rework_count comes back 1 on the second apply.
    """
    import run as _run

    src = (pathlib.Path(_run.__file__)).read_text()
    # Assert on the SOURCE of the two write sites rather than on a mocked call:
    # the defect was a literal expression, and a behavioural mock could be
    # satisfied by a value that happens to be 0 on the first attempt.
    assert "rework_count=_attempt - 1" not in src, (
        "rework_count must not be derived from the apply ordinal — a session "
        "with no verify block has zero gate reworks by definition"
    )
    assert src.count("rework_count=0") >= 2, (
        "both apply_terminal and administrative_retire must record zero reworks"
    )


# --------------------------------------------------------------------------
# TH-01 — orchestrator context size carried from dispatch to ledger line
# --------------------------------------------------------------------------
def test_orchestrator_ctx_tokens_carries_the_dispatch_time_int(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"],
                  orchestrator_ctx_tokens=123456)
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert rec["orchestrator_ctx_tokens"] == 123456


def test_orchestrator_ctx_tokens_is_null_when_the_dispatch_could_not_measure(tmp_path):
    plan_dir = make_plan(tmp_path, [SESS])
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"],
                  orchestrator_ctx_tokens=None)
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert "orchestrator_ctx_tokens" in rec          # the FIELD is always present
    assert rec["orchestrator_ctx_tokens"] is None


def test_orchestrator_ctx_tokens_never_carries_an_earlier_dispatchs_reading(tmp_path):
    """A re-dispatch that could not measure must read null, not the previous
    attempt's number — that number was taken at a different point in the run."""
    plan_dir = make_plan(tmp_path, [SESS])
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"],
                  orchestrator_ctx_tokens=99000)
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"],
                  orchestrator_ctx_tokens=None)
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="passed", verified=True)
    assert rec["orchestrator_ctx_tokens"] is None


def test_begin_stamps_the_field_on_every_dispatch_started(tmp_path, monkeypatch):
    """The end-to-end half: a REAL `begin` writes the field (int or null), so a
    plan run after this ships has it on every dispatch record."""
    plan_dir = make_plan(tmp_path, [SESS])
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    run.cmd_begin(plan_dir, ["s01"])
    run.cmd_release(plan_dir)
    events = [json.loads(ln) for ln in (plan_dir / "run.ndjson").read_text().splitlines()
              if ln.strip()]
    started = [e for e in events if e["event"] == "dispatch_started"]
    assert started, "begin wrote no dispatch_started event"
    assert "orchestrator_ctx_tokens" in started[-1]
    assert started[-1]["orchestrator_ctx_tokens"] is None   # env deliberately unset


# --------------------------------------------------------------------------
# routing_provenance — the VALUE, not just the key
# --------------------------------------------------------------------------
# REGRESSION 2026-09-04: SSOT v22 (commit 8303082) replaced this function's
# `import run as _run` with `import provider_lane as pl` and left three `_run`
# uses below it. Every call raised NameError, the bare `except` swallowed it,
# and the fallback labelled 114/114 records `pinned_override` — so the
# aggregator's proposal pool was empty for five days while
# test_record_shape_is_schema_valid stayed green on the key's mere presence.
# A known POSITIVE is what that test lacked.
@pytest.mark.parametrize(("task_class", "backend", "model", "reasoning", "expected"), [
    ("deep_reasoning", "claude", "opus", "medium", "default_resolved"),
    ("agentic_build", "claude", "opus", "high", "default_resolved"),
    ("standard_build", "claude", "sonnet", "medium", "default_resolved"),
    ("mechanical", "claude", "haiku", None, "default_resolved"),
    ("agentic_build", "codex", "gpt-5.6-sol", "xhigh", "default_resolved"),
    ("mechanical", "claude", "sonnet", "max", "pinned_override"),      # zai cell, claude lane
    ("deep_reasoning", "claude", "opus", "low", "pinned_override"),    # canary rung
])
def test_routing_provenance_resolves_the_class_default(task_class, backend, model,
                                                       reasoning, expected):
    assert outc._routing_provenance(None, {}, task_class, backend, model, reasoning) == expected


def test_routing_provenance_experimental_wins_over_the_default_cell():
    sess = {"routing_experiment": {"kind": "canary", "proposal_id": "4abfa765d726"}}
    assert outc._routing_provenance(None, sess, "deep_reasoning", "claude",
                                    "opus", "medium") == "experimental"


def test_attested_model_without_reasoning_keeps_the_climbed_effort(tmp_path, isolated_ledger):
    """A transcript names the served model but not the effort. After a climb
    (authored sonnet@medium, dispatched sonnet@high) the attested row must read
    the effort the attempt was DISPATCHED at, not the authored one — otherwise
    /routing-retro books the climbed attempt under the wrong cell."""
    plan_dir = make_plan(tmp_path, [dict(SESS, reasoning="medium")])
    transcript = tmp_path / "headless-result.json"
    transcript.write_text(json.dumps({"modelUsage": {
        "claude-sonnet-4-6": {"inputTokens": 900, "outputTokens": 400}}}))
    run.cmd_record_receipt(plan_dir, "s01", "agent-123", str(transcript), "claude")
    write_closeout(plan_dir, "s01", extra={
        "escalated_from": {"authored": {"model": "sonnet", "reasoning": "medium"},
                           "ran": {"model": "sonnet", "reasoning": "high"},
                           "attempt": 2, "rung": 1, "generation": 0},
    })
    rec = outc.compose(plan_dir, "s01", resolution="unit", result="done_unverified", verified=False)
    assert rec["model_ran_source"] == outc.SOURCE_ATTESTED
    assert rec["reasoning_authored"] == "medium"
    assert rec["reasoning_ran"] == "high"
