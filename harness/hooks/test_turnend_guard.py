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


# json.loads raises RecursionError on this on every Python from 3.11 to 3.15 (3.11 already at about 1,200 levels).
DEEP = "[" * 1_000_000 + "]" * 1_000_000
BROKEN = ["[]", '"x"', "1", DEEP]  # a row that is not a dict, or too deep to parse


@pytest.mark.parametrize("bad", BROKEN, ids=["list", "string", "number", "too-deep"])
@pytest.mark.parametrize("where", ["run_state", "transcript", "reply_state", "block_state"])
def test_a_row_that_is_not_a_dict_is_skipped_never_a_traceback(tmp_path, where, bad):
    plan = plant(tmp_path)
    t = tmp_path / "t.jsonl"
    t.write_text(bad + "\n")
    state = tmp_path / "state"
    state.mkdir()
    if where == "run_state":
        (plan / "run_state.json").write_text(bad)
    elif where == "reply_state":
        (state / f"{SID}.reply.json").write_text(bad)
    elif where == "block_state":
        (state / f"{SID}.json").write_text(bad)
    reply = "Needs you: nothing" if where == "reply_state" else None
    p = payload(tmp_path, transcript_path=str(t), **({"last_assistant_message": reply} if reply else {}))
    r = run(tmp_path, p)
    assert "Traceback" not in r.stderr
    assert r.returncode == (0 if where == "run_state" else 2)


def _blocks(*content):
    """One transcript row whose message holds these content blocks."""
    return json.dumps({"type": "assistant", "message": {"content": list(content)}})


# Rows that are dicts but whose inner fields have the wrong shape, each one reaching the background scan.
SHAPED_ROWS = {
    "message-string": '{"message": "tool_use"}',
    "content-number": '{"message": {"content": 5, "x": "tool_use"}}',
    "input-string": _blocks({"type": "tool_use", "name": "Bash", "id": "t", "input": "run_in_background"}),
    "id-list": _blocks({"type": "tool_use", "name": "Agent", "id": ["x"], "input": {}}),
    "result-ref-list": _blocks({"type": "tool_result", "tool_use_id": ["x"], "content": "x"}),
    "send-to-list": _blocks({"type": "tool_use", "name": "SendMessage", "id": "s", "input": {"to": ["a"]}},
                            {"type": "tool_result", "tool_use_id": "s", "content": "Resuming agent"}),
    "taskstop-input-string": _blocks({"type": "tool_use", "name": "TaskStop", "id": "s", "input": "x"}),
    "too-deep": "[" * 1_000_000 + '"tool_use"' + "]" * 1_000_000,
}
OPEN_REPLY = "The cleanup is still open.\nNext: x. Needs you: nothing"


@pytest.mark.parametrize("row", SHAPED_ROWS.values(), ids=SHAPED_ROWS)
def test_a_wrong_shaped_row_never_breaks_the_background_scan(tmp_path, row):
    """No open plan: the reply guard reaches _scan_blocks. Nothing is outstanding, so the reply blocks."""
    t = tmp_path / "t.jsonl"
    t.write_text(row + "\n")
    r = run(tmp_path, payload(tmp_path, transcript_path=str(t), last_assistant_message=OPEN_REPLY))
    assert "Traceback" not in r.stderr and r.returncode == 2


@pytest.mark.parametrize("row", [
    '{"type": "assistant", "message": "x"}',
    '{"type": "assistant", "message": {"content": 5}}',
    _blocks({"type": "text", "text": 5}),
    "[" * 190_000 + "]" * 190_000,  # inside the 400 KB tail the reply reader takes
], ids=["message-string", "content-number", "text-number", "too-deep"])
def test_a_wrong_shaped_last_row_is_skipped_and_the_reply_before_it_is_read(tmp_path, row):
    """No open plan, no hook input text: last_reply_text reads the file. A bad row is skipped, never repaired."""
    t = tmp_path / "t.jsonl"
    t.write_text(_blocks({"type": "text", "text": OPEN_REPLY}) + "\n" + row + "\n")
    r = run(tmp_path, payload(tmp_path, transcript_path=str(t)))
    assert "Traceback" not in r.stderr and r.returncode == 2


@pytest.mark.parametrize("bad", ["[]", '{"sig": []}', '{"sig": {}}', DEEP], ids=["list", "sig-list", "sig-dict", "too-deep"])
def test_a_wrong_shaped_reply_state_is_read_as_absent(tmp_path, bad):
    """No open plan: the reply guard reads its once-per-reply state. Bad state means the reply blocks."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / f"{SID}.reply.json").write_text(bad)
    r = run(tmp_path, payload(tmp_path, last_assistant_message=OPEN_REPLY))
    assert "Traceback" not in r.stderr and r.returncode == 2


@pytest.mark.parametrize("n", ['"x"', "[1]", "null", "1.5"], ids=["string", "list", "null", "float"])
def test_a_block_counter_of_the_wrong_type_restarts_the_count(tmp_path, n):
    plan = plant(tmp_path)
    (tmp_path / "state").mkdir()
    sig = json.dumps([(str(plan), ["s03"])])
    (tmp_path / "state" / f"{SID}.json").write_text(f'{{"sig": {json.dumps(sig)}, "n": {n}}}')
    r = run(tmp_path, payload(tmp_path))
    assert "Traceback" not in r.stderr and r.returncode == 2 and "Block 1 of 3" in r.stderr


def test_blocks_doing_without_closeout_and_names_it(tmp_path):
    plant(tmp_path)
    r = run(tmp_path, payload(tmp_path))
    assert r.returncode == 2
    assert "plan p1" in r.stderr and "s03" in r.stderr and "Block 1 of 3" in r.stderr


def test_escaping_session_id_writes_no_state_outside_the_state_dir(tmp_path):
    sid = "../../escape"
    plant(tmp_path, owner=sid)
    r = run(tmp_path, payload(tmp_path, session_id=sid))
    assert r.returncode == 0
    assert not list(tmp_path.glob("**/escape*.json")) and not (tmp_path / "state").exists()


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


@pytest.mark.parametrize("bad", ["", "not json", json.dumps({"cwd": "/nonexistent"}), "[]", '"x"', "1", "null", DEEP],
                         ids=["empty", "not-json", "no-session", "list", "string", "number", "null", "too-deep"])
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


def reply_transcript(tmp_path, text):
    t = tmp_path / "reply.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
    return t


@pytest.mark.parametrize("text,expected", [
    ("Done.\nThe four follow-ups are not filed.\nNext: x. Needs you: nothing", 2),
    ("Done.\nNext: x. Needs you: nothing", 0),
    ("Still open: the cleanup.\nNext: x. Needs you: the OAuth key", 0),
    ("Done.\nNext: x. Needs you: nothing\nState: committed no (uncommitted until the gate passes)", 0),
    ("The cleanup is still open.\nNext: x. Needs you: nothing\nState: committed no", 2),
    ("Deployed.\n**Not checked**\n- The fill rate in the owner's apps.\nNext: a timer. Needs you: nothing", 2),
    # Past tense still blocks, but the message names the words (2026-11-27: a guess cost a turn).
    ("I ran the two checks that my last reply left open.\nNext: x. Needs you: nothing", 2),
    # A decision in 'Needs you' with no pick blocks; the same with a pick, or "nothing, unless … or …", passes.
    ("Done.\nNext: wait. Needs you: run the plan now, both, or neither?", 2),
    ("Done.\nNext: wait. Needs you: run the plan now, or wait? My pick: run it now.", 0),
    ("Done.\nNext: x. Needs you: nothing, unless you want the PR or a look at the failure.", 0),    # A merge a gate script runs, handed to the owner, blocks (the 2026-11-18 13:35 reply).
    ("## What you can do\n1. Merge example-project PR 886 with `scripts/merge-pr.sh 886`.\n"
     "Next: on your yes, I deploy. Needs you: the deploy card and the two example-project merges (the numbered list above).", 2),
    ("I merged PR 886 with `scripts/merge-pr.sh 886`.\nNext: x. Needs you: the deploy card", 0),
    ("PR 890 waits on `merge_when_green.py 890`.\nNext: x. Needs you: nothing", 0),
])
def test_reply_contradiction_blocks_once(tmp_path, text, expected):
    t = reply_transcript(tmp_path, text)
    pl = payload(tmp_path, transcript_path=str(t))
    r = run(tmp_path, pl)
    assert r.returncode == expected
    if "open work" in r.stderr:
        hit = r.stderr.split("(matched: '")[1].split("')")[0]
        assert hit.lower() in text.lower()
    assert run(tmp_path, pl).returncode == 0  # once per reply


def test_reply_guard_reads_the_hook_input_before_a_lagging_transcript(tmp_path):
    """The transcript can still hold the previous reply when Stop fires; the input has this one."""
    bad, good = "The cleanup is still open.\nNext: x. Needs you: nothing", "Done.\nNext: x. Needs you: nothing"
    old_bad = payload(tmp_path, transcript_path=str(reply_transcript(tmp_path, bad)), last_assistant_message=good)
    assert run(tmp_path, old_bad).returncode == 0      # the old reply in the file is not this turn's
    old_good = payload(tmp_path, transcript_path=str(reply_transcript(tmp_path, good)), last_assistant_message=bad)
    assert run(tmp_path, old_good).returncode == 2     # this turn's reply blocks though the file lags


def test_reply_guard_waits_while_background_work_runs(tmp_path):
    """The reply names open work, but a background command still runs: a wait, not an end."""
    t = reply_transcript(tmp_path, "The gate run is not done.\nNext: x. Needs you: nothing")
    bg = [{"type": "assistant", "message": {"content": [
              {"type": "tool_use", "id": "toolu_bg", "name": "Bash", "input": {"run_in_background": True}}]}},
          {"type": "user", "message": {"content": [
              {"type": "tool_result", "tool_use_id": "toolu_bg", "content": "Command running in background"}]}}]
    reply = t.read_text()
    t.write_text("".join(json.dumps(x) + "\n" for x in bg) + reply)
    pl = payload(tmp_path, transcript_path=str(t))
    assert run(tmp_path, pl).returncode == 0
    t.write_text(reply)                       # control: the same reply with nothing running blocks
    assert run(tmp_path, pl).returncode == 2


def test_a_background_command_taskstop_ended_does_not_hold_the_guard_open(tmp_path):
    """A stopped launch gets no task-notification; it must not count as running."""
    reply = reply_transcript(tmp_path, "The gate run is not done.\nNext: x. Needs you: nothing").read_text()
    launch = [{"type": "assistant", "message": {"content": [
                  {"type": "tool_use", "id": "toolu_bg", "name": "Bash", "input": {"run_in_background": True}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_bg",
                  "content": "Command running in background with ID: bb8ber8am. Output is being written to: /tmp/x"}]}}]
    stop = [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_stop", "name": "TaskStop", "input": {"task_id": "bb8ber8am"}}]}}]
    t = tmp_path / "reply.jsonl"
    pl = payload(tmp_path, transcript_path=str(t))
    t.write_text("".join(json.dumps(x) + "\n" for x in launch) + reply)
    assert run(tmp_path, pl).returncode == 0           # control: still running, a wait
    t.write_text("".join(json.dumps(x) + "\n" for x in launch + stop) + reply)
    assert run(tmp_path, pl).returncode == 2           # stopped: the reply is an end, and it blocks


def test_a_notification_with_only_the_task_id_ends_the_agent(tmp_path):
    """A final notification can carry the task id and no tool-use-id; it still ends the launch."""
    reply = reply_transcript(tmp_path, "The gate run is not done.\nNext: x. Needs you: nothing").read_text()
    launch = [{"type": "assistant", "message": {"content": [
                  {"type": "tool_use", "id": "toolu_ag", "name": "Agent", "input": {}}]}},
              {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_ag",
                  "content": "Async agent launched successfully.\nagentId: a296fa (internal ID)"}]}}]
    note = [{"type": "user", "message": {"content":
             "<task-notification>\n<task-id>a296fa</task-id>\n<status>completed</status>\n</task-notification>"}}]
    t = tmp_path / "reply.jsonl"
    pl = payload(tmp_path, transcript_path=str(t))
    t.write_text("".join(json.dumps(x) + "\n" for x in launch) + reply)
    assert run(tmp_path, pl).returncode == 0           # control: no notification yet, a wait
    t.write_text("".join(json.dumps(x) + "\n" for x in launch + note) + reply)
    assert run(tmp_path, pl).returncode == 2
