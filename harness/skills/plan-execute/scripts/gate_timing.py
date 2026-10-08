"""How long a gate actually took, written where the operator already looks.

Nothing measured the most expensive step in the loop. `run.ndjson` carried no
duration on any gate event, so the only way to cost a verify pass was to
subtract the timestamps of two unrelated events -- and the per-ATTEMPT split,
which is where the interesting failure lives, was not recoverable at all.
Measured 2026-08-23 on s09b: attempt 1 consumed its full 1800s wall limit and
was killed, attempt 2 then ran from zero. The gate reported INDETERMINATE after
~36 minutes and no log said why.

A separate module rather than more lines in `llm_review_gate.py` (483 LOC
against a 500 bound), on the same reasoning `proc_group.py` was split out.
`run_state_io.log_event` already takes `**fields`, so it needs no change.

NEVER let this break a gate. A verdict that is correct and unlogged beats a
crash in the instrumentation, so every failure here is swallowed.
"""

from __future__ import annotations

import time


#: `llm_review_gate`'s exit codes, named for the journal. Kept beside the
#: writer so the gate file stays under its 500-LOC bound.
_OUTCOME = {0: "passed", 1: "failed", 2: "indeterminate"}


def outcome_name(rc):
    return _OUTCOME.get(rc, str(rc))


def now():
    return time.monotonic()


def ms_since(t0):
    return int(round((time.monotonic() - t0) * 1000))


def record_attempt(t0, data, attempt_ms, attempt_usd):
    """Append one attempt's wall time and, when the reviewer reported it, its cost.

    `claude -p --output-format json` returns `total_cost_usd` (API-equivalent
    dollars; on a subscription it is quota, not a bill). The gate used to drop
    it, so no spend share per gate existed. The Codex backend reports no cost:
    its entries, and a failed attempt's, are None -- unknown, never 0.
    """
    attempt_ms.append(ms_since(t0))
    cost = data.get("total_cost_usd") if isinstance(data, dict) else None
    ok = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    attempt_usd.append(cost if ok else None)


def log_gate(plan_dir, session, gate, t0, outcome, attempts=(), attempt_usd=()):
    """Append one `gate_completed` event. No plan directory -> nothing to log.

    `attempts` is the per-attempt wall time in ms, in order. Two entries where
    the first is at the timeout is the silent-retry signature; one short entry
    is a healthy run. `attempt_usd` is the matching per-attempt cost.
    """
    plan_dir = (plan_dir or "").strip()
    if not plan_dir:
        return False
    try:
        import run_state_io as rsi

        rsi.log_event(plan_dir, "gate_completed",
                      session_ids=[session] if session else [],
                      gate=gate, outcome=outcome,
                      duration_ms=ms_since(t0),
                      attempt_ms=list(attempts),
                      attempt_usd=list(attempt_usd))
        return True
    except Exception:   # instrumentation must never decide a gate
        return False


def log_run(plan_dir, phase, session, gate, seconds, outcome):
    """Append one `gate_run` event for ANY argv gate, in any repo (2026-10-04).

    `gate_completed` above is the review gate's own per-attempt record; this one
    is written by the RUNNER (`verify` and land), so test gates are timed too and
    `gate_durations.py` can say how long each repo's gates take. `phase` is
    "verify", "land", "land-base" or "land-rerun". Never breaks a gate.
    """
    try:
        import run_state_io as rsi

        rsi.log_event(plan_dir, "gate_run", session_ids=[session] if session else [],
                      phase=phase, gate=gate, outcome=outcome, seconds=round(seconds, 1))
        return True
    except Exception:   # instrumentation must never decide a gate
        return False
