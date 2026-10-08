#!/usr/bin/env python3
"""Where verify keeps its per-session state, and how it writes it.

A leaf module: verify.py and rework.py both need these, and routing them through
verify.py would make the import graph a cycle. Five one-liners, but they are the
only place the on-disk layout of `_verify_state/` is spelled out.
"""
from datetime import UTC, datetime
from pathlib import Path

import ship_state_io as ssio

# The one definition of the failed-gate sentinel: verify.py records it, rework.py
# writes it. Its two siblings (GATE_PENDING / GATE_PASSED) stay in verify.py,
# which is the only file that reads them.
GATE_FAILED = "failed"
# The refusal locked_checks.py records when the worker changed a locked check or a
# test-configuration file (contract §5). Charged like GATE_FAILED, never equal to it.
LOCKED_CHECK_EDITED = "LOCKED_CHECK_EDITED"


def _now():
    return datetime.now(UTC).isoformat()

def _html(plan_dir):
    return Path(plan_dir) / "PLAN.html"

def verify_state_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.json"

def _save_state(plan_dir, session_id, state):
    state["updated_at"] = _now()
    ssio.durable_write_json(verify_state_path(plan_dir, session_id), state)

def _feedback_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.feedback.md"
