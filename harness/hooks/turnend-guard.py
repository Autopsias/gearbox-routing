#!/usr/bin/env python3
"""turnend-guard — Stop hook: no turn ends blind on a plan this session runs.

Pattern borrowed from kunchenguid/firstmate's `fm-turnend-guard.sh`. CLAUDE.md already
says "before ending your turn ... do that work now"; as prose that rule fails.
This makes it a mechanism: the harness refuses to end the turn instead.

WHEN IT BLOCKS (exit 2; stderr is shown to the model as the reason to continue):
  a plan under <cwd>/_plans whose last batch THIS session dispatched
  (`run_state.json` -> last_batch.orchestrator_session_id, written by
  run_state_io.record_batch from CLAUDE_CODE_SESSION_ID) has a session whose
  PLAN.html status is DOING with no `_closeouts/<sid>.json`, AND no background
  dispatch of this session is still running.

WHY THE BACKGROUND CHECK: Agent, Workflow and `run_in_background` Bash calls end
the orchestrator's turn legitimately — the harness re-invokes it with a
<task-notification> on completion. Those notifications carry the tool-use id,
so the transcript itself says which dispatches are outstanding. Measured:
a large plan-run transcript scans in well under a second.

LOOP SAFETY: at most MAX_BLOCKS consecutive blocks for one unchanged finding
per session (state under ~/.gearbox-state/turnend-guard/); then the turn may end.
Every failure path allows the stop — a guard that cannot read is not a guard
that should trap the session.

RESUMED AGENTS: an agent that already sent its completion notification can run
again — SendMessage resumes it ("Resuming agent ...") or queues a message for
its next tool round, and a notification can say it "stopped with background work
of its own still running". So an agent also counts as running when a resume or
queued SendMessage targeted it after its last notification (notifications carry
the agent id as <task-id>), or when its subagent transcript
(<transcript>/subagents/agent-*.jsonl) changed within RECENT_S seconds.

ponytail: a dispatch that dies without a notification stays "outstanding" and
the guard stays open for it; plan-execute's crash recovery still covers that.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

MAX_BLOCKS = 3
BG_TOOLS = ("Agent", "Workflow", "Task")
DOING_RE = re.compile(r'<article class="session" id="([^"]+)"[^>]*data-status="DOING"')
NOTE_RE = re.compile(r"<task-notification>(.*?)</task-notification>")
TASK_RE = re.compile(r"<task-id>([^<]+)</task-id>")
TOOL_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")
# An agent that stops while its own background work still runs gets an INTERIM
# "completed" notification; the final one comes later under the same task-id,
# often without a tool-use-id. Seen 2026-10-14, Kuzushi kuzushi-videos s98/s101.
INTERIM = "background work of its own still running"
RESUMED = ("Resuming agent", "queued for delivery")
RECENT_S = 120
# A reply that says "Needs you: nothing" beside work it names as open contradicts itself:
# CLAUDE.md "never write 'Next: nothing' beside open follow-ups you can do yourself".
# Prose failed three times (2026-10-11, 2026-11-10), so the guard checks it.
NOTHING_RE = re.compile(r"Needs you:\W*nothing", re.I)
OPEN_RE = re.compile(r"\b(?:not (?:yet )?(?:filed|done)|still open|left open|uncommitted|open follow-?ups?)\b", re.I)
# A 'Needs you' line that asks a decision must name a pick (per the output style).
# The text rule failed twice ("what do you recommend?" came back), and
# 25 of 1,467 'Needs you' replies in the 30 latest transcripts asked with no pick.
NEEDS_RE = re.compile(r"Needs you:[ \t]*(.*)", re.I)
# A merge that goes through a gate script is the session's own step (CLAUDE.md: "After a
# session opens a pull request it runs merge_when_green.py"; example-project CLAUDE.md: "ALWAYS merge
# with scripts/merge-pr.sh"). The text rule failed a 7th time ("you do everything
# still left to be done, merges and shit"), so the guard checks a 'Needs you' that names one.
GATE_MERGE_RE = re.compile(r"merge-pr\.sh|merge_when_green")
MERGE_WORD_RE = re.compile(r"\bmerg(?:e|es|ing)\b", re.I)
# A background command or agent that TaskStop ended never gets a task-notification.
LAUNCH_ID_RE = re.compile(r"(?:with ID|agentId): ?([A-Za-z0-9_-]+)")
PICK_RE = re.compile(r"\bmy pick\b|\bI recommend\b|\(Recommended\)|\brecommendation\b|\bmy vote\b", re.I)


def unpicked_decision(body: str) -> bool:
    """The last 'Needs you' line offers a choice ('?' or 'or') and the reply names no pick."""
    lines = NEEDS_RE.findall(body)
    if not lines or re.match(r"\W*nothing\b", lines[-1], re.I):
        return False
    return bool(("?" in lines[-1] or re.search(r"\bor\b", lines[-1], re.I)) and not PICK_RE.search(body))


def merge_parked_on_owner(body: str) -> bool:
    """The last 'Needs you' line hands the owner a merge, and the reply names a gate script that runs it."""
    lines = NEEDS_RE.findall(body)
    if not lines or re.match(r"\W*nothing\b", lines[-1], re.I):
        return False
    return bool(MERGE_WORD_RE.search(lines[-1]) and GATE_MERGE_RE.search(body))


def state_dir() -> Path:
    return Path(os.environ.get("TURNEND_GUARD_STATE") or Path.home() / ".gearbox-state" / "turnend-guard")


def open_sessions(plan_dir: Path) -> list[str]:
    """Sessions DOING in PLAN.html with no closeout file."""
    try:
        html = (plan_dir / "PLAN.html").read_text(errors="replace")
    except OSError:
        return []
    return sorted(s for s in DOING_RE.findall(html) if not (plan_dir / "_closeouts" / f"{s}.json").exists())


def plans_of(cwd: Path, session_id: str) -> list[Path]:
    out = []
    for rs in sorted((cwd / "_plans").glob("*/run_state.json")):
        try:
            lb = (json.loads(rs.read_text()).get("last_batch") or {})
        except (OSError, ValueError):
            continue
        if lb.get("orchestrator_session_id") == session_id:
            out.append(rs.parent)
    return out


def _scan_blocks(line: str, issued: dict, results: dict, sends: dict, stopped: set | None = None) -> list:
    """One transcript line: record background tool_use ids and their tool_result text.
    Returns the agent ids a SendMessage on this line resumed or queued a message for."""
    try:
        msg = (json.loads(line).get("message") or {}).get("content")
    except ValueError:
        return []
    woken = []
    for c in msg if isinstance(msg, list) else []:
        if c.get("type") == "tool_use" and (c.get("name") in BG_TOOLS or (
            c.get("name") == "Bash" and (c.get("input") or {}).get("run_in_background")
        )):
            issued[c.get("id")] = c.get("name")
        elif c.get("type") == "tool_use" and c.get("name") == "TaskStop" and stopped is not None:
            stopped.add(str((c.get("input") or {}).get("task_id") or ""))
        elif c.get("type") == "tool_use" and c.get("name") == "SendMessage":
            sends[c.get("id")] = (c.get("input") or {}).get("to")
        elif c.get("type") == "tool_result" and c.get("tool_use_id") in sends:
            if any(m in str(c.get("content")) for m in RESUMED):
                woken.append(sends[c["tool_use_id"]])
        elif c.get("type") == "tool_result" and c.get("tool_use_id") in issued:
            results[c["tool_use_id"]] = str(c.get("content"))
    return woken


def background_outstanding(transcript_path: str | None) -> int:
    """Background dispatches issued minus those whose task-notification arrived,
    plus agents whose latest notification is interim (still waiting on their own work),
    or 1 when a notified agent was resumed or its subagent transcript is fresh."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0
    if subagent_recently_active(transcript_path):
        return 1
    issued, done, results, sends, waiting, stopped = {}, set(), {}, {}, set(), set()
    last_note, resumed = {}, {}  # agent id -> line number
    with open(transcript_path, errors="replace") as fh:
        for i, line in enumerate(fh):
            if "tool_use" in line:
                for a in _scan_blocks(line, issued, results, sends, stopped):
                    resumed[a] = i
            for note in NOTE_RE.findall(line) if "<task-notification>" in line else ():
                done.update(TOOL_RE.findall(note))
                task = (TASK_RE.findall(note) or [None])[0]
                last_note[task] = i
                (waiting.add if INTERIM in note else waiting.discard)(task)
    if any(i > last_note.get(a, -1) for a, i in resumed.items()):
        return 1
    # A launch is done when TaskStop ended it (no notification ever comes) or when a notification
    # names its task id but no tool-use-id. Either one held the guard open for the rest of the
    # session (measured: a stopped wait and an s07 agent silenced both reply checks).
    ended = stopped | set(last_note)
    done.update(tid for tid, res in results.items() if set(LAUNCH_ID_RE.findall(res)) & ended)
    return _count_live(issued, done, results) + len(waiting - {None})


def _count_live(issued: dict, done: set, results: dict) -> int:
    # A dispatch the harness refused (permission denied, hook block) never ran, so
    # it cannot be "still running": a background Bash counts only once its result
    # confirms the launch; an Agent/Workflow drops on a refusal text.
    live = 0
    for tid, name in issued.items():
        if tid in done:
            continue
        res = results.get(tid, "")
        if name == "Bash" and "running in background" not in res:
            continue
        if name != "Bash" and ("denied" in res or "Blocked" in res):
            continue
        live += 1
    return live


def subagent_recently_active(transcript_path: str) -> bool:
    """Any subagent transcript of this session written within RECENT_S seconds."""
    now = time.time()
    for f in Path(transcript_path).with_suffix("").joinpath("subagents").glob("agent-*.jsonl"):
        try:
            if now - f.stat().st_mtime < RECENT_S:
                return True
        except OSError:
            continue
    return False


def last_reply_text(transcript_path: str | None) -> str:
    """Text of the last assistant message in the transcript tail ('' when unreadable)."""
    try:
        with open(transcript_path or "", "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 400_000))
            lines = fh.read().decode(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") != "assistant":
            continue
        text = "".join(c.get("text", "") for c in (msg.get("message") or {}).get("content") or []
                       if isinstance(c, dict) and c.get("type") == "text")
        if text:
            return text
    return ""


def reply_guard(payload: dict, session_id: str) -> int:
    """Block ONCE per reply that pairs 'Needs you: nothing' with open work,
    or that asks a decision in 'Needs you' and names no pick."""
    # The transcript "is written asynchronously and may lag the in-memory conversation"
    # (hooks docs, `transcript_path`), so it held the PREVIOUS reply at 2 of 3 turn ends on
    # 2026-11-17: one reply passed that the rule blocks, and the next was blocked for it.
    text = payload.get("last_assistant_message")
    if not isinstance(text, str) or not text:
        text = last_reply_text(payload.get("transcript_path"))
    # The `State:` line reports delivery ("committed no" on work that waits for a
    # gate); it is not open work. And a reply sent while background work runs is a
    # wait, not an end: both blocked every reworded status reply.
    body = re.sub(r"(?m)^State:.*$", "", text)
    if NOTHING_RE.search(body) and OPEN_RE.search(body):
        if background_outstanding(payload.get("transcript_path")):
            return 0
        msg = ("TURN-END GUARD: your reply says 'Needs you: nothing' but also names open work. "
               "Do the work you can do now, or put each item in a decision card or a dated timer, "
               "then send the reply again. This block fires once per reply.\n")
    elif merge_parked_on_owner(body):
        msg = ("TURN-END GUARD: your 'Needs you' line hands the owner a merge that a gate script runs "
               "(merge-pr.sh or merge_when_green.py). A merge through the gate script is yours: run it, "
               "then send the reply again. This block fires once per reply.\n")
    elif unpicked_decision(body):
        msg = ("TURN-END GUARD: your 'Needs you' line asks for a decision, but the reply names no pick. "
               "Say what is to decide, give each option in one line, and name your pick "
               "('my pick: ...'), then send the reply again. This block fires once per reply.\n")
    else:
        return 0
    sf = state_dir() / f"{session_id}.reply.json"
    sig = hashlib.sha1(text.encode()).hexdigest()
    try:
        if json.loads(sf.read_text()).get("sig") == sig:
            return 0
    except (OSError, ValueError):
        pass
    try:
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"sig": sig}))
    except OSError:
        return 0
    sys.stderr.write(msg)
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    session_id = payload.get("session_id")
    if not session_id:
        return 0
    cwd = Path(payload.get("cwd") or os.getcwd())
    findings = [(p, open_sessions(p)) for p in plans_of(cwd, session_id)]
    findings = [(p, s) for p, s in findings if s]
    if not findings:
        return reply_guard(payload, session_id)
    if background_outstanding(payload.get("transcript_path")):
        return 0

    sig = json.dumps([(str(p), s) for p, s in findings])
    sd = state_dir()
    sf = sd / f"{session_id}.json"
    try:
        st = json.loads(sf.read_text())
    except (OSError, ValueError):
        st = {}
    n = st.get("n", 0) if st.get("sig") == sig else 0
    if n >= MAX_BLOCKS:
        return 0
    try:
        sd.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"sig": sig, "n": n + 1}))
    except OSError:
        return 0

    lines = ["TURN-END GUARD: this session dispatched plan work that is still open, and no dispatched agent is running."]
    for p, s in findings:
        lines.append(f"  plan {p.name}: session(s) {', '.join(s)} are DOING with no closeout.")
    lines.append("Do not end the turn. Apply the closeout you already have, or re-dispatch the session "
                 "(`/plan-execute <plan-dir> --session sNN`, crash recovery), or set its status if the work is parked. "
                 f"Block {n + 1} of {MAX_BLOCKS}; after that the turn may end.")
    sys.stderr.write("\n".join(lines) + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
