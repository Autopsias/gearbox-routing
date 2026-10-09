#!/usr/bin/env python3
"""agent-janitor-sessionend — SessionEnd hook: reap the ENDING session's own
scratchpad-tied orphans the moment it terminates, instead of waiting up to 30
minutes for the next launchd tick.

WHY SessionEnd, NOT Stop: Stop fires "when Claude finishes
responding" -- once per assistant TURN. Wiring the reaper to Stop would run it
dozens of times in a single working session. SessionEnd fires exactly once,
when the session actually terminates.

Reads `session_id` from the hook's stdin JSON (the harness's documented
mechanism for passing per-event context to a hook) and hands it straight to
`agent_janitor.py reap --apply --session-id <id>` UNCHANGED -- all of the
exact-mapping resolution (resolve to exactly one scratchpad dir, refuse a
missing/malformed/ambiguous id non-zero, never fall back to machine-wide
reaping) lives in agent_janitor.py itself, not duplicated here.

Runs the DEPLOYED copy (~/.claude/scripts/agent_janitor.py), never a
source-tree copy -- this hook is itself deployed via `gearbox deploy`
(settings.json + hooks/ are both HARNESS-CODE, deploy.pathspec fail-closed).

Declared `"async": true` in settings.json so a slow or wedged reap can never
delay session shutdown; this script's own foreground work (parse stdin, spawn
the janitor, return) is kept minimal on top of that, not instead of it.
An observer/mutator hook must never let a broken stdin payload or a
missing janitor script raise -- any failure here degrades to a no-op exit 0;
the scheduled launchd reaper (every 30 min) is the backstop.

Self-check: hooks/test_agent_janitor_sessionend.py
"""

import json
import os
import subprocess
import sys

JANITOR = os.path.expanduser("~/.claude/scripts/agent_janitor.py")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0

    session_id = event.get("session_id")
    if not session_id or not isinstance(session_id, str):
        return 0
    if not os.path.isfile(JANITOR):
        return 0

    try:
        subprocess.run(
            [sys.executable or "python3", JANITOR, "reap", "--apply",
             "--session-id", session_id],
            capture_output=True, timeout=25,
        )
    except Exception:  # a SessionEnd hook must never raise -- degrade to no-op
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
