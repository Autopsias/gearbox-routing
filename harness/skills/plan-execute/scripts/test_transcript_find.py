"""The outcome record attests the served model from the worker transcript when
the orchestrator skipped `record-receipt` (outcomes._receipt -> transcript_find).

Run: pytest skills/plan-execute/scripts/test_transcript_find.py -q
"""
import json

import pytest

import outcomes as outc
import run
import run_state_io as rsi
import transcript_find
from test_outcomes import SESS, _read_ledger, make_plan


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    """Every test gets its OWN ledger file — never the real tracked one."""
    path = tmp_path / "ledger" / "outcomes.ndjson"
    monkeypatch.setenv(outc.LEDGER_PATH_ENV, str(path))
    return path


def _plant_transcript(root, plan_dir, session_id, agent_id, *, ts, agent_type="tier-sonnet-low",
                      model="claude-sonnet-5-5"):
    """An Agent-tool transcript as Claude Code writes it, with no receipt for it."""
    sub = root / "-proj" / "parent-session" / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    prompt = (f"You are executing plan session {session_id}. Read "
              f"{plan_dir}/sessions/{session_id}.prompt.md in full.")
    lines = [
        {"type": "user", "timestamp": ts, "agentId": agent_id,
         "message": {"role": "user", "content": prompt}},
        {"type": "assistant", "timestamp": ts, "message": {
            "id": "msg_1", "model": model, "stop_reason": "end_turn",
            "usage": {"input_tokens": 12, "output_tokens": 34,
                      "cache_read_input_tokens": 56, "cache_creation_input_tokens": 0}}},
    ]
    path = sub / f"agent-{agent_id}.jsonl"
    path.write_text("".join(json.dumps(ln) + "\n" for ln in lines))
    (sub / f"agent-{agent_id}.meta.json").write_text(json.dumps({"agentType": agent_type}))
    return path


def _apply_done(tmp_path, plan_dir):
    closeout = tmp_path / "closeout-s01.txt"
    closeout.write_text(
        '<plan-execute-closeout>\n'
        '{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{}}\n'
        '</plan-execute-closeout>'
    )
    run.cmd_apply(plan_dir, "s01", str(closeout))


def test_missing_receipt_attests_from_the_transcript_on_disk(tmp_path, isolated_ledger, monkeypatch):
    """The orchestrator dispatched s01 and never ran `record-receipt`. The
    worker's transcript is on disk, so the record is still `attested`, with
    the transcript's model and usage — not `requested`."""
    root = tmp_path / "projects"
    monkeypatch.setenv("PLAN_EXECUTE_TRANSCRIPTS_ROOT", str(root))
    plan_dir = make_plan(tmp_path, [SESS])
    run.cmd_begin(plan_dir, ["s01"])
    _plant_transcript(root, plan_dir, "s01", "a1b2c3", ts=outc._now())
    assert rsi.get_receipt(plan_dir, "s01") is None

    _apply_done(tmp_path, plan_dir)

    rec = _read_ledger(isolated_ledger)[0]
    assert rec["model_ran_source"] == outc.SOURCE_ATTESTED
    assert rec["model_ran"] == "claude-sonnet-5-5"
    assert rec["usage"]["source"] == "subagent-transcript"
    assert rec["usage"]["output_tokens"] == 34


def test_transcript_search_refuses_a_guess(tmp_path, isolated_ledger, monkeypatch):
    """No attestation from a transcript that predates the dispatch, names
    another session, comes from a helper agent, or is one of two candidates."""
    root = tmp_path / "projects"
    monkeypatch.setenv("PLAN_EXECUTE_TRANSCRIPTS_ROOT", str(root))
    plan_dir = make_plan(tmp_path, [SESS])
    _plant_transcript(root, plan_dir, "s01", "before", ts="2020-01-01T00:00:00Z")
    run.cmd_begin(plan_dir, ["s01"])
    now = outc._now()
    _plant_transcript(root, plan_dir, "s01b", "other", ts=now)
    _plant_transcript(root, plan_dir, "s01", "helper", ts=now, agent_type="general-purpose")
    assert transcript_find.find(plan_dir, "s01") is None

    _plant_transcript(root, plan_dir, "s01", "one", ts=now)
    assert transcript_find.find(plan_dir, "s01").name == "agent-one.jsonl"
    _plant_transcript(root, plan_dir, "s01", "two", ts=now)
    assert transcript_find.find(plan_dir, "s01") is None   # two candidates is a guess

    _apply_done(tmp_path, plan_dir)
    assert _read_ledger(isolated_ledger)[0]["model_ran_source"] == outc.SOURCE_REQUESTED


def test_transcript_search_cap_picks_the_dispatch_before_it(tmp_path, monkeypatch):
    """A backfill passes the outcome record's time: the window is the dispatch
    before it, so attempt 1's transcript is found for attempt 1's record only."""
    root = tmp_path / "projects"
    monkeypatch.setenv("PLAN_EXECUTE_TRANSCRIPTS_ROOT", str(root))
    plan_dir = make_plan(tmp_path, [SESS])
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"])
    _plant_transcript(root, plan_dir, "s01", "first", ts=outc._now())
    resolved_1 = outc._now()
    rsi.log_event(plan_dir, "dispatch_started", session_ids=["s01"])
    assert transcript_find.find(plan_dir, "s01", resolved_1).name == "agent-first.jsonl"
    assert transcript_find.find(plan_dir, "s01") is None   # attempt 2 has no transcript
