#!/usr/bin/env python3
"""Behaviour tests for hooks/turnend-guard.py — run as a subprocess with a
planted cwd (`_plans/<slug>/{run_state.json,PLAN.html,_closeouts}`), a planted
transcript, and the guard's state dir pointed at tmp. Nothing touches ~/.gearbox-state.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "turnend-guard.py"
SID = "sess-1"


def plant(tmp_path, *, doing=("s03",), closeouts=(), owner=SID):
    plan = tmp_path / "_plans" / "p1"
    (plan / "_closeouts").mkdir(parents=True, exist_ok=True)
    (plan / "run_state.json").write_text(json.dumps(
        {"last_batch": {"session_ids": list(doing), "result": "DISPATCHED", "orchestrator_session_id": owner}}))
    html = "".join(f'<article class="session" id="{s}" data-session-id="{s}" data-status="DOING"></article>\n'
                   for s in doing)
    html += '<article class="session" id="s01" data-session-id="s01" data-status="DONE"></article>\n'
    (plan / "PLAN.html").write_text(html)
    for s in closeouts:
        (plan / "_closeouts" / f"{s}.json").write_text("{}")
    return plan


def transcript(tmp_path, issued, notified, results=None):
    t = tmp_path / "t.jsonl"
    lines = []
    for tid in issued:
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "Agent", "input": {}}]}}))
        if results and tid in results:
            lines.append(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": tid, "content": results[tid]}]}}))
    for tid in notified:
        lines.append(json.dumps({"type": "user", "message": {"content":
            f"<task-notification>\n<task-id>x</task-id>\n<tool-use-id>{tid}</tool-use-id>\n<status>completed</status>\n</task-notification>"}}))
    t.write_text("\n".join(lines) + "\n")
    return t


def run(tmp_path, payload):
    env = {"PATH": "/usr/bin:/bin", "TURNEND_GUARD_STATE": str(tmp_path / "state")}
    return subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), text=True,
                          capture_output=True, env=env, timeout=30)


def payload(tmp_path, **kw):
    p = {"session_id": SID, "cwd": str(tmp_path), "transcript_path": str(tmp_path / "missing.jsonl")}
    p.update(kw)
    return p


def test_blocks_doing_without_closeout_and_names_it(tmp_path):
    plant(tmp_path)
    r = run(tmp_path, payload(tmp_path))
    assert r.returncode == 2
    assert "plan p1" in r.stderr and "s03" in r.stderr and "Block 1 of 3" in r.stderr


def test_allows_when_closeout_exists(tmp_path):
    plant(tmp_path, closeouts=("s03",))
    assert run(tmp_path, payload(tmp_path)).returncode == 0


def test_allows_another_sessions_plan(tmp_path):
    plant(tmp_path, owner="someone-else")
    assert run(tmp_path, payload(tmp_path)).returncode == 0


def test_allows_while_background_dispatch_outstanding(tmp_path):
    plant(tmp_path)
    t = transcript(tmp_path, issued=["toolu_a", "toolu_b"], notified=["toolu_a"])
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 0


def test_blocks_once_every_dispatch_is_notified(tmp_path):
    plant(tmp_path)
    t = transcript(tmp_path, issued=["toolu_a"], notified=["toolu_a"])
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 2


def test_block_cap_then_allows(tmp_path):
    plant(tmp_path)
    codes = [run(tmp_path, payload(tmp_path)).returncode for _ in range(4)]
    assert codes == [2, 2, 2, 0]


def test_new_finding_resets_cap(tmp_path):
    plant(tmp_path)
    for _ in range(3):
        run(tmp_path, payload(tmp_path))
    plant(tmp_path, doing=("s03", "s04"))  # finding changed
    assert run(tmp_path, payload(tmp_path)).returncode == 2


@pytest.mark.parametrize("bad", ["", "not json", json.dumps({"cwd": "/nonexistent"})])
def test_unreadable_input_allows(tmp_path, bad):
    env = {"PATH": "/usr/bin:/bin", "TURNEND_GUARD_STATE": str(tmp_path / "state")}
    r = subprocess.run([sys.executable, str(HOOK)], input=bad, text=True, capture_output=True, env=env)
    assert r.returncode == 0


def test_refused_dispatch_does_not_hold_the_guard_open(tmp_path):
    plant(tmp_path)
    t = transcript(tmp_path, issued=["toolu_a"], notified=[],
                   results={"toolu_a": "Permission for this action was denied by the Claude Code auto mode classifier."})
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 2


def agent_life(tmp_path, *, send_after_note, subagent_age_s):
    """Agent `aX` dispatched, notified, and messaged (before or after the notification);
    its subagent transcript was last written `subagent_age_s` seconds ago."""
    use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_a", "name": "Agent", "input": {}}]}}
    note = {"type": "user", "message": {"content":
            "<task-notification>\n<task-id>aX</task-id>\n<tool-use-id>toolu_a</tool-use-id>\n<status>completed</status>\n</task-notification>"}}
    send = [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_s", "name": "SendMessage", "input": {"to": "aX", "message": "go on"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_s", "content":
                '{"success":true,"message":"Resuming agent aX","resumedAgentId":"aX"}'}]}}]
    rows = [use, note, *send] if send_after_note else [use, *send, note]
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    sub = tmp_path / "t" / "subagents" / "agent-aX.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text("{}\n")
    then = time.time() - subagent_age_s
    os.utime(sub, (then, then))
    return t


@pytest.mark.parametrize("send_after_note,age", [(True, 600), (False, 5)])
def test_allows_while_a_notified_agent_was_resumed_or_is_writing(tmp_path, send_after_note, age):
    plant(tmp_path)
    t = agent_life(tmp_path, send_after_note=send_after_note, subagent_age_s=age)
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 0


def test_blocks_when_the_agent_is_really_dead(tmp_path):
    # Known positive: message sent BEFORE the last notification, transcript idle 10 min.
    plant(tmp_path)
    t = agent_life(tmp_path, send_after_note=False, subagent_age_s=600)
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 2


# Real interim notification, s98 of the Kuzushi kuzushi-videos plan.
INTERIM_NOTE = (
    "<task-notification>\n<task-id>ac7be2c74f44ec80b</task-id>\n<tool-use-id>toolu_a</tool-use-id>\n"
    "<status>completed</status>\n<summary>Agent \"Run plan session s98\" finished</summary>\n"
    "<note>This agent stopped with background work of its own still running. It may resume on its own when "
    "that work completes or reports, and the same task-id notifies again if it does; the result below may be "
    "interim.</note>\n<result>This agent has not reported yet: it is waiting on its own background work and "
    "will deliver its report through SubagentHandback when that finishes.\n</result>\n</task-notification>")
FINAL_NOTE = (
    "<task-notification>\n<task-id>ac7be2c74f44ec80b</task-id>\n<status>completed</status>\n"
    "<note>A task-notification fires each time this agent stops with no live background children of its own.</note>\n"
    "<result>closeout written</result>\n</task-notification>")


def _with_notes(tmp_path, *notes):
    t = transcript(tmp_path, issued=["toolu_a"], notified=[])
    with t.open("a") as fh:
        for n in notes:
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": n}}) + "\n")
    return t


def test_interim_notification_keeps_agent_running(tmp_path):
    plant(tmp_path)
    t = _with_notes(tmp_path, INTERIM_NOTE)
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 0


def test_final_notification_after_interim_ends_it(tmp_path):
    plant(tmp_path)
    t = _with_notes(tmp_path, INTERIM_NOTE, FINAL_NOTE)
    assert run(tmp_path, payload(tmp_path, transcript_path=str(t))).returncode == 2
