#!/usr/bin/env python3
"""Behaviour tests for hooks/turnend-guard.py — run as a subprocess with a
planted cwd (`_plans/<slug>/{run_state.json,PLAN.html,_closeouts}`), a planted
transcript, and the guard's state dir pointed at tmp. Nothing touches ~/.gearbox-state.
"""
from __future__ import annotations

import json
import subprocess
import sys
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
