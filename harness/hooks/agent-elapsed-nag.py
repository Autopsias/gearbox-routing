#!/usr/bin/env python3
"""Remind the orchestrator to report elapsed cost while a dispatch runs long.

CLAUDE.md requires a cost report past 30 minutes on a single dispatched agent,
unprompted. As prose that is ~80% compliance; this makes it deterministic.

Mechanism, VERIFIED against the Claude Code 2.1.251 hook docs before this was
written: a `PreToolUse` hook may return `hookSpecificOutput.additionalContext`
and that text reaches the MODEL. Stderr on exit 0 does NOT — it goes to the
debug log only — so a stderr warning is no use
here. Exiting 0 with no output is an ordinary allow, so this hook stays silent
unless it has something to say and never needs to emit a decision of its own.

Wiring (both events point at this one file):
  PreToolUse   -> agent-elapsed-nag.py pre
  SubagentStop -> agent-elapsed-nag.py stop
  Stop         -> agent-elapsed-nag.py stopbg   (reconciles background Bash tasks)

Background Bash (ETA-01): see _plans/owner-attention-fixes-2026-11-12/_evidence/s05/mechanism.md.
No hook event marks a background Bash task's end; the Stop payload's `background_tasks`
snapshot is the only signal, plus a 24 h expiry. State key: <session>.bash.json.

State is a FIFO of dispatch timestamps per session. `pre` on an Agent/Task call
pushes; `stop` pops the OLDEST. No agent-id correlation is attempted — the id is
not in the PreToolUse payload — so the oldest live entry is treated as the
longest-running unfinished dispatch. That errs toward reminding, which is the
safe direction for a rule about under-reporting.

State lives under ~/.gearbox-state/, OUTSIDE both versioned trees: a log inside the deploy target aborts the next
`gearbox deploy` as an unclassified path.

Self-check: hooks/test_agent_elapsed_nag.py
"""

import fcntl
import json
import os
import sys
import time

BUCKET_SECONDS = 1800  # 30 minutes, per the CLAUDE.md rule this enforces
STATE_ROOT = os.path.join(os.path.expanduser("~"), ".gearbox-state", "agent-nag")
DISPATCH_TOOLS = {"Agent", "Task"}


def state_path(session_id):
    safe = "".join(c for c in str(session_id or "unknown") if c.isalnum() or c in "-_")
    root = os.environ.get("CLAUDE_AGENT_NAG_ROOT", STATE_ROOT)
    return os.path.join(root, f"{safe or 'unknown'}.json")


def first_time(session_id, kind):
    """True only the first time a session starts a background `kind` (agent or bash).

    The arm-a-timer text repeated on every start (6 times in one 2026-11-15 session, for
    jobs under 7 minutes) teaches the agent to skip it; once per session keeps the
    instruction. The past-30-minute reminders are separate and unchanged."""
    marker = state_path(session_id)[: -len(".json")] + f".armed-{kind}"
    os.makedirs(os.path.dirname(marker), exist_ok=True)
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return list(d.get("starts") or []), int(d.get("bucket") or 0)
    except (OSError, ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        return [], 0


def save(path, starts, bucket):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"starts": starts, "bucket": bucket}, fh)
    os.replace(tmp, path)


def reminder(minutes, count):
    others = f" ({count} dispatches open)" if count > 1 else ""
    return (
        f"[cost] The oldest open dispatch has been running {minutes} minutes{others}. "
        "CLAUDE.md requires an UNPROMPTED cost report past 30 minutes: say how long it "
        "has run, what it is doing, and offer the cut as a decision card. Do not wait "
        "to be asked. If it has already been reported at this mark, ignore this."
    )


def arm_timer():
    return (
        "[cost] Background dispatch started. While you wait for it you run no tools, so "
        "this hook cannot remind you later. Arm a wake-up now: CronCreate a one-shot check "
        "30 minutes from now that reports elapsed time, what the agent is doing, and the cut "
        "if it has not closed out. Delete it (CronDelete) when the agent reports first."
    )


BG_MAX_AGE = 24 * 3600  # a killed or lost task is never nagged about after a resume


def bg_path(session_id):
    return state_path(session_id)[: -len(".json")] + ".bash.json"


def bg_load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def bg_save(path, bg):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(bg, fh)
    os.replace(tmp, path)


def bg_arm_timer():
    return (
        "[cost] Background command started. While you wait for it you run no tools, so "
        "this hook cannot remind you later. Arm a wake-up now: CronCreate a one-shot check "
        "30 minutes from now that reports elapsed time, what the command is doing, and the "
        "cut if it has not finished. Delete it (CronDelete) when the command ends first."
    )


def bg_reminder(entry, minutes):
    return (
        f"[cost] A background command has been running {minutes} minutes: {entry['cmd'][:120]!r}. "
        "CLAUDE.md requires an UNPROMPTED report past 30 minutes: say the elapsed time, what "
        "it is doing, and the cut as a decision card. Do not wait to be asked."
    )


def run_bg(mode, ev):
    """Background Bash tracking. Keys on tool_input.run_in_background and tool_use_id,
    never on command text. Entries: {tool_use_id: {t, cmd, bucket}}."""
    path = bg_path(ev.get("session_id"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # parallel tool calls run their hooks at once: lock the read-update-save
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run_bg_locked(mode, ev, path)


def _keep_running(bg, tasks):
    """Keep, per command, as many entries as the Stop snapshot shows running, newest first.
    The snapshot cannot say which twin ended. A re-run after a failure is the common twin,
    and then the ended one is the older: keeping it reported 43, 63 and 90 minutes for a
    job that was 20 minutes old. Cost: when two twins run side by side and
    the newer ends first, the older one's reminder is late by the gap between their starts."""
    running = {}
    for t in tasks:
        if isinstance(t, dict) and t.get("status", "running") == "running":
            cmd = str(t.get("command") or "")
            running[cmd] = running.get(cmd, 0) + 1
    keep = {}
    for k, v in sorted(bg.items(), key=lambda kv: kv[1]["t"], reverse=True):
        cmd = v.get("cmd", "")[:1000]
        if running.get(cmd, 0) > 0:
            running[cmd] -= 1
            keep[k] = v
    return keep


def _drop_ended(bg, transcript_path):
    """Drop entries whose finished notice is in the transcript. A command that ends mid-turn
    stays listed until the next Stop; its <task-notification> carries the tool-use id, which
    is the entry key. 2026-11-20: 4 reminders (53 to 274 minutes) named commands that had ended."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except (OSError, TypeError):
        return bg
    return {k: v for k, v in bg.items() if f"<tool-use-id>{k}</tool-use-id>" not in text}


def _run_bg_locked(mode, ev, path):
    bg = bg_load(path)
    now = time.time()
    n = len(bg)
    bg = {k: v for k, v in bg.items()
          if isinstance(v, dict) and isinstance(v.get("t"), (int, float)) and now - v["t"] < BG_MAX_AGE}
    tasks = ev.get("background_tasks")
    if mode == "stopbg" and isinstance(tasks, list):
        # shortcut: no task id in the PreToolUse payload, so match on the exact command
        # string the Stop snapshot reports (capped at 1000 chars); upgrade if ids appear.
        bg = _keep_running(bg, tasks)
    text = None
    if mode == "pre":
        ti = ev.get("tool_input") or {}
        if ev.get("tool_name") == "Bash" and ti.get("run_in_background") is True:
            bg[str(ev.get("tool_use_id") or now)] = {"t": now, "cmd": str(ti.get("command") or ""), "bucket": 0}
            text = bg_arm_timer() if first_time(ev.get("session_id"), "bash") else None
        elif bg:
            k, v = min(bg.items(), key=lambda kv: kv[1]["t"])
            current = int((now - v["t"]) // BUCKET_SECONDS)
            if current >= 1 and current > v.get("bucket", 0):
                bg = _drop_ended(bg, ev.get("transcript_path"))  # read only when about to speak
                if k in bg:
                    v["bucket"] = current
                    text = bg_reminder(v, int((now - v["t"]) // 60))
    if text or len(bg) != n or mode == "stopbg":
        bg_save(path, bg)
    return text


def run(mode, ev):
    """Return the text to inject, or None. Pure enough to test directly."""
    if mode == "stopbg":
        return run_bg(mode, ev)
    if mode == "pre" and ev.get("tool_name") not in DISPATCH_TOOLS:
        bg_text = run_bg(mode, ev)
        if bg_text:
            return bg_text
    path = state_path(ev.get("session_id"))
    starts, bucket = load(path)
    now = time.time()

    if mode == "stop":
        if starts:
            starts.pop(0)
            save(path, starts, bucket if starts else 0)
        return None

    if ev.get("tool_name") in DISPATCH_TOOLS:
        starts.append(now)
        save(path, starts, bucket)
        # Agents run in the background unless the call says otherwise.
        if (ev.get("tool_input") or {}).get("run_in_background", True) is not False:
            return arm_timer() if first_time(ev.get("session_id"), "agent") else None
        return None

    if not starts:
        return None
    elapsed = now - min(starts)
    current = int(elapsed // BUCKET_SECONDS)
    if current >= 1 and current > bucket:
        save(path, starts, current)
        return reminder(int(elapsed // 60), len(starts))
    return None


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        if not isinstance(ev, dict):
            return
        text = run(mode, ev)
        if text:
            out = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": text,
                }
            }
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 — hook must fail open: an observer never fails the tool call
        pass  # an observer never fails the tool call it observes


if __name__ == "__main__":
    main()
