"""R6 §17.2 inv. 5 and §17.3 — the owner's answer goes straight on with the land.

``land-ack``, ``land-record`` and ``land-verify`` are short-lived processes, so a lease they
take dies with them. Each therefore (a) takes the per-plan run lock at its CLI entry, (b)
records the answer, then (c) runs ``land(answered=True)`` in the SAME process: that takes the
lease and ends this plan's reservation in one host-mutex hold, so no other plan's land can
slip between the answer and the next step. For ``land-ack`` the next step is the push, through
the one existing push path (``land_push.STEPS``); there is no second one.
"""

from pathlib import Path

import land_state as lst
import lease_guard as lg
import run_state_io as rsi


def run_locked(plan_dir, fn):
    """``fn(resolved plan dir)`` under the per-plan run lock, or the ``land-running`` park
    when another process of this plan holds it. Nothing is recorded in that case."""
    plan_dir = str(Path(plan_dir).resolve())
    if lst.context(plan_dir) is None:     # not a real isolated plan: no lock file, fn explains
        return fn(plan_dir)
    try:
        with lg.run_lock(plan_dir):
            return fn(plan_dir)
    except lg.RunLocked:
        return {"action": "land-parked", "kind": "land-running",
                "brief": f"LAND NOT STARTED — another process of this plan is running land "
                         f"({plan_dir}). Nothing was recorded. Re-run this command once that "
                         "run has ended."}


def then_land(plan_dir, recorded, land_fn):
    """After a recorded answer other than a refusal, continue the land in this process."""
    if recorded.get("action") == "error":
        return recorded
    return {**land_fn(plan_dir, answered=True), "recorded": recorded}


def ack_push(plan_dir, note, ack_fn, land_fn):
    """Record the ack, then push it (or say why it was not pushed) in the same step."""
    rec = ack_fn(plan_dir, note)
    if rec.get("action") != "land-acked":
        return rec
    out = land_fn(plan_dir, answered=True)
    ctx = lst.context(plan_dir)
    st = (lst.load(plan_dir, ctx) or {}) if ctx else {}
    action = out.get("action")
    moved = None
    if action == "landed":
        outcome = "carried" if (st.get("ack") or {}).get("carries") else "pushed"
    elif action == "land-awaits-review":
        outcome, moved = "awaits-review", (st.get("last_carry") or {}).get("reason") or "ack-binding"
    elif action == "land-locked" or out.get("kind") == "land-reserved":
        outcome = "locked"
    else:
        outcome = out.get("kind") or action
    rsi.log_event(plan_dir, "land_ack_push", session_ids=[], slug=Path(plan_dir).name,
                  outcome=outcome, moved=moved)
    return {**out, "ack": st.get("ack") or rec["ack"], "outcome": outcome, **({"moved": moved} if moved else {})}
