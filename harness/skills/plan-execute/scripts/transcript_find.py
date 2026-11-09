#!/usr/bin/env python3
"""Find a dispatch's worker transcript when no `record-receipt` call was made.

The orchestrator is told to run `record-receipt` after every Agent call, and it
forgets (measured: example-project 76 of 98 outcome records since 2026-10-17
had no receipt). The transcript still exists on disk, so `outcomes.compose`
falls back to this search instead of writing `model_ran_source: "requested"`.

A transcript is this dispatch's only when ALL of these hold, and exactly one
file passes (two candidates is a guess, so the answer is None):
  * it is an Agent-tool or Workflow-member transcript under the projects root;
  * its first line falls between the session's LAST `dispatch_started` and
    the next `dispatch_started` of any session (an earlier attempt's
    transcript, or a later dispatch that mentions this session, is never picked);
  * its `.meta.json`, when present, names a plan-execute dispatch agent;
  * its first prompt names the session id as a whole token, any case (`s07`
    never matches `s07b`), and the file names the plan directory.
"""
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path

import safe_json as sj

ROOT_ENV = "PLAN_EXECUTE_TRANSCRIPTS_ROOT"
_GLOBS = ("*/*/subagents/agent-*.jsonl", "*/*/subagents/workflows/*/agent-*.jsonl")
_DISPATCH_AGENTS = ("tier-", "session-effort-worker")


def projects_root():
    if os.environ.get(ROOT_ENV):
        return Path(os.environ[ROOT_ENV])
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def _ts(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def _first_prompt(line):
    """(start epoch, prompt text) of a transcript's first line."""
    rec = json.loads(line)
    content = rec["message"]["content"]
    if not isinstance(content, str):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return _ts(rec["timestamp"]), content


def _is_dispatch_agent(path):
    meta = path.with_name(path.stem + ".meta.json")
    if not meta.exists():
        return True
    agent = json.loads(meta.read_text(encoding="utf-8")).get("agentType") or ""
    return agent.startswith(_DISPATCH_AGENTS)


def _window(plan_dir, session_id, as_of=None):
    """(start, end) epochs of the session's last dispatch before `as_of`; end is
    None when no later dispatch exists. None when there was no such dispatch."""
    starts = [sj.load_dict(ln) for ln in (Path(plan_dir) / "run.ndjson").read_text().splitlines()]
    starts = [r for r in starts if r and r.get("event") == "dispatch_started"
              and (as_of is None or _ts(r["ts"]) <= as_of)]
    mine = [i for i, r in enumerate(starts) if session_id in sj.get_list(r, "session_ids", [])]
    if not mine:
        return None
    since = _ts(starts[mine[-1]]["ts"])
    later = [_ts(r["ts"]) for r in starts[mine[-1] + 1:] if _ts(r["ts"]) > since]
    return since, (min(later) if later else None)


def by_agent(agent_id):
    """The Agent-tool or Workflow-member transcript of this agent id, or None. Never raises."""
    name = f"agent-{glob.escape(agent_id)}.jsonl"
    try:
        for pattern in _GLOBS:
            for path in projects_root().glob(pattern.replace("agent-*.jsonl", name)):
                return path
    except (OSError, ValueError):
        pass
    return None


def find(plan_dir, session_id, until_iso=None):
    """Path of the one transcript the session's last dispatch wrote, or None.
    `until_iso` (a backfill: the outcome record's time) picks the last dispatch
    before it and closes the window there. Never raises."""
    try:
        cap = _ts(until_iso) if until_iso else None
        window = _window(plan_dir, session_id, cap)
        if window is None:
            return None
        since, until = window
        if cap is not None:
            until = cap if until is None else min(until, cap)
        token = re.compile(rf"(?<![A-Za-z0-9]){re.escape(session_id)}(?![A-Za-z0-9])", re.I)
        plan_name = Path(plan_dir).resolve().name
        root = projects_root()
        hits = []
        for pattern in _GLOBS:
            for path in root.glob(pattern):
                try:
                    if path.stat().st_mtime < since:
                        continue
                    with open(path, encoding="utf-8") as fh:
                        start, prompt = _first_prompt(fh.readline())
                    if start < since or (until and start >= until):
                        continue
                    if not token.search(prompt) or not _is_dispatch_agent(path):
                        continue
                    if plan_name in prompt or plan_name in path.read_text(encoding="utf-8"):
                        hits.append(path)
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    continue
        return hits[0] if len(hits) == 1 else None
    except (OSError, ValueError, TypeError, KeyError):
        return None
