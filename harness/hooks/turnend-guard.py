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

ponytail: a dispatch that dies without a notification stays "outstanding" and
the guard stays open for it; plan-execute's crash recovery still covers that.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MAX_BLOCKS = 3
BG_TOOLS = ("Agent", "Workflow", "Task")
DOING_RE = re.compile(r'<article class="session" id="([^"]+)"[^>]*data-status="DOING"')
NOTE_RE = re.compile(r"<tool-use-id>([^<]+)</tool-use-id>")


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


def _scan_blocks(line: str, issued: dict, results: dict) -> None:
    """One transcript line: record background tool_use ids and their tool_result text."""
    try:
        msg = (json.loads(line).get("message") or {}).get("content")
    except ValueError:
        return
    for c in msg if isinstance(msg, list) else []:
        if c.get("type") == "tool_use" and (c.get("name") in BG_TOOLS or (
            c.get("name") == "Bash" and (c.get("input") or {}).get("run_in_background")
        )):
            issued[c.get("id")] = c.get("name")
        elif c.get("type") == "tool_result" and c.get("tool_use_id") in issued:
            results[c["tool_use_id"]] = str(c.get("content"))


def background_outstanding(transcript_path: str | None) -> int:
    """Background dispatches issued minus those whose task-notification arrived."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0
    issued, done, results = {}, set(), {}
    with open(transcript_path, errors="replace") as fh:
        for line in fh:
            if "tool_use" in line:
                _scan_blocks(line, issued, results)
            if "<tool-use-id>" in line:
                done.update(NOTE_RE.findall(line))
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
        return 0
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
