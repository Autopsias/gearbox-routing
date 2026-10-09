#!/usr/bin/env python3
"""Shipping badges + the aggregate monitoring summary.

Split out of shipping.py, which the s06 rework pushed past its 795-LOC size
baseline (the ratchet is right: extract, don't bump). This is the READ-ONLY
reporting layer — the step-status vocabulary and the two renderings of it that
run.py's `status` output and the PLAN.html badge consume. Nothing here mutates
shipping state, and nothing here imports shipping back, so shipping.py can
re-export these names for its existing callers without a cycle.
"""
import json
from pathlib import Path

import ship_state_io as ssio

# The durable step-status vocabulary (`_shipping_state/<sid>.json` values).
# Defined here — the lowest module that needs it — and imported by shipping.py;
# moved verbatim, never retyped.
STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED = (
    "pending", "running", "done", "failed", "skipped",
)


def mark_done(state, step):
    """Record ``step`` done. A COMMIT that ran moves HEAD past whatever an earlier
    ``push`` sent, so a ``push`` already recorded done goes back to pending:
    replaying a failed commit (clear-halt, ship-begin, ship-run --step commit)
    otherwise skipped the push and finalized with origin at the OLD sha. A push with nothing new is a no-op, so re-running it is cheap."""
    state["steps"][step] = STEP_DONE
    if step == "commit" and state["steps"].get("push") == STEP_DONE:
        state["steps"]["push"] = STEP_PENDING


def shipping_badge(state):
    """Human-facing badge: committed/pushed/PR-open/deployed, or
    SHIP-FAILED@<step> / SHIP-PENDING / ship-skipped."""
    steps = state.get("steps", {})
    for name, st in steps.items():
        if st == STEP_FAILED:
            return f"SHIP-FAILED@{name}"
    for name, label in (("deploy", "deployed"), ("pr", "PR-open"),
                        ("push", "pushed"), ("commit", "committed")):
        if steps.get(name) == STEP_DONE:
            return label
    if any(st == STEP_RUNNING for st in steps.values()):
        return "SHIP-PENDING"
    if steps and all(st == STEP_SKIPPED for st in steps.values()):
        return "ship-skipped"
    return "ship-pending"


def shipping_summary(plan_dir, manifest, *, tail=8):
    """Aggregate shipping summary (monitoring — Q10 monitoring_blind_spot
    mitigation). Surfaced by `run.py status` so a silent skip/halt is visible in
    the default output, not only in retrospective run.ndjson archaeology."""
    sessions = {}
    for s in manifest.get("sessions", []):
        if not s.get("post_session"):
            continue
        sid = s["id"]
        try:
            state = ssio.load_ship_state(plan_dir, sid)
        except ssio.ShipStateError:
            sessions[sid] = "STATE-CORRUPT"
            continue
        sessions[sid] = shipping_badge(state) if state else "ship-pending"
    if not sessions:
        return None
    return {"sessions": sessions, "recent_events": _recent_shipping_events(plan_dir, tail)}


def _recent_shipping_events(plan_dir, tail):
    p = Path(plan_dir) / "run.ndjson"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("event", "")).startswith("post_session_"):
            ev = {"event": rec["event"], "ts": rec.get("ts"),
                  "session": (rec.get("session_ids") or [None])[0]}
            for k in ("reason", "failed_step", "declared_steps"):
                if k in rec:
                    ev[k] = rec[k]
            out.append(ev)
    return out[-tail:]
