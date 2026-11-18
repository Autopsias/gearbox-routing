"""GAT-01 — which land gates run again on a moved main (``fast``) and which keep
their recorded pass (``carry``).

The class comes from the registry field ``phase2`` ("fast" | "carry"). A review
gate always carries. A missing or unknown value is ``fast``: when in doubt the
gate runs again. A carried gate is never invoked: its recorded pass is copied to
the new key with ``carried_from``. Only a settled pass reaches here (the caller
filters on the outcome), so a failed gate never carries.

A carried non-review gate (``ci-check``) is bounded: at most ``MAX_CARRIES`` in a
row, and never once the last full pass is older than ``MAX_AGE_S``. The age is
read from ``full_at``, which ``land_gate`` stamps when the gate really ran; a
record without it cannot prove its age and re-runs.
"""

import calendar
import time

import verify_pass as vp

MAX_CARRIES = 4
MAX_AGE_S = 24 * 3600


def phase2(g):
    """"carry" or "fast". Anything but an explicit "carry" on a non-review gate is fast."""
    return "carry" if vp.is_review_gate(g) or g.get("phase2") == "carry" else "fast"


def _age(stamp, now_ts):
    try:
        t = calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None
    return (time.time() if now_ts is None else now_ts) - t


def carry(rec, g, key, old_key, now_ts=None, phase2_open=False):
    """The record for the new ``key``, copied from ``rec``; None when the gate must run.
    A non-review gate is copied only when the caller says phase 2 is open (setting on)."""
    if phase2(g) != "carry" or not (phase2_open or vp.is_review_gate(g)):
        return None
    n = int(rec.get("carried_n") or 0)
    if not vp.is_review_gate(g):
        age = _age(rec.get("full_at"), now_ts)
        if n >= MAX_CARRIES or age is None or age > MAX_AGE_S:
            return None
        n += 1
    return {**rec, "gate_key": key, "carried_from": old_key, "carried_n": n}


def note(st):
    """The brief and record lines: what carried from the first main, what ran on the new one."""
    carried = st.get("carried_gates")
    if not carried:
        return None
    old, new = st.get("carried_from_main"), st.get("expected")
    lines = [f"carried from {str(old)[:12]}: {', '.join(carried)}",
             f"ran on {str(new)[:12]}: {', '.join(st.get('ran_gates') or []) or 'none'}"]
    if st.get("moved_since_ack"):
        lines.append("files that moved since the ack: " + ", ".join(st["moved_since_ack"]))
    return "\n".join(lines)
