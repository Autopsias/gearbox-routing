#!/usr/bin/env python3
"""skill-usage-log — PreToolUse(Skill): record every skill invocation, so
popularity and undertriggering are MEASURABLE instead of guessed.

WHY THIS EXISTS. The harness carries ~130 skills and decluttering
decisions (retire, merge, sharpen a description) have been made on taste. The
Claude Code team logs skill usage internally for exactly this reason ("find
skills that are popular or are undertriggering compared to our expectations",
claude.com blog "How we use skills"). This hook is the gearbox
version: one ndjson line per Skill call, queried ad hoc with jq/grep.

It is an OBSERVER, never a gate: it always allows, and any failure inside it is
swallowed. A broken usage log must never block a skill invocation.

Same conventions as hooks/dispatch-audit.py (the house observer pattern):
  - allow decision emitted and flushed FIRST, so nothing below can block;
  - pure python, no shell wrapper (a quoting bug once broke that, silently);
  - log lives under ~/.gearbox-state, OUTSIDE both versioned trees — `gearbox deploy`
    is fail-closed on unclassified paths, and `args` is task-derived text that
    must never land in a versioned path (transcript-containment rule).

Wired from settings.json hooks.PreToolUse matcher "Skill".
Self-check: hooks/test_skill_usage_log.sh
"""

import datetime
import json
import os
import sys

ALLOW = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }
}

DEFAULT_LOG = os.path.join(os.path.expanduser("~"), ".gearbox-state", "skills", "usage.ndjson")


def build_record(ev):
    ti = ev.get("tool_input")
    if not isinstance(ti, dict):
        return None
    skill = ti.get("skill")
    if not isinstance(skill, str) or not skill:
        return None
    args = ti.get("args")
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "session_id": ev.get("session_id"),
        "cwd": ev.get("cwd"),
        "skill": skill,
        # Truncated: enough to see HOW a skill gets invoked, small enough that
        # the log stays greppable and task text stays out of any report.
        "args": (args or "")[:120] if isinstance(args, str) else "",
    }


def main():
    # Emit the allow decision FIRST and flush it, so nothing below can prevent
    # the invocation — not an exception, not a full disk, not a bad payload.
    sys.stdout.write(json.dumps(ALLOW) + "\n")
    sys.stdout.flush()
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        if not isinstance(ev, dict):
            return
        rec = build_record(ev)
        if rec is None:
            return
        path = os.environ.get("CLAUDE_SKILL_USAGE_LOG") or DEFAULT_LOG
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # an observer never fails the invocation it observes


if __name__ == "__main__":
    main()
