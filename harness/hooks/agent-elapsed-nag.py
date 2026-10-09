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

State is a FIFO of dispatch timestamps per session. `pre` on an Agent/Task call
pushes; `stop` pops the OLDEST. No agent-id correlation is attempted — the id is
not in the PreToolUse payload — so the oldest live entry is treated as the
longest-running unfinished dispatch. That errs toward reminding, which is the
safe direction for a rule about under-reporting.

State lives under ~/.gearbox-state/, OUTSIDE both versioned trees: a log inside the deploy target aborts the next
`gearbox deploy` as an unclassified path.

Self-check: hooks/test_agent_elapsed_nag.py
"""

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


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return list(d.get("starts") or []), int(d.get("bucket") or 0)
    except Exception:
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


def run(mode, ev):
    """Return the text to inject, or None. Pure enough to test directly."""
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
    except Exception:
        pass  # an observer never fails the tool call it observes


if __name__ == "__main__":
    main()
