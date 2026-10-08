#!/usr/bin/env python3
"""Escalation state: what the ladder has already done, on disk.

Split out of escalation.py, which had grown 42 lines past its size baseline.
The layering is one-directional — this module reads and writes run_state.json
and the verify state, and escalation.py's pure derivation half reads it.

NEVER the manifest and never a digest input, so escalation state can never trip
`state-drift`.
"""
from pathlib import Path

import run_state_io as rsi
import ship_state_io as ssio

STATE_KEY = "escalation"




# --------------------------------------------------------------------------
# Persisted state — run_state.json only (mutable runtime state, NEVER the
# manifest, never a digest input, so it can never trip `state-drift`).
# --------------------------------------------------------------------------
def rung_key(model, reasoning):
    """The stable identity of one ladder cell, used as the refused-set member.

    `inherit`/`unset` rather than the empty string so a hand-read run_state is
    legible and two different absences never collide."""
    return f"{model or 'inherit'}@{reasoning or 'unset'}"


def session_state(plan_dir, session_id):
    """This session's escalation state, defaulted. Read-only — never writes."""
    blob = (rsi.load_state(plan_dir).get(STATE_KEY) or {}).get(session_id) or {}
    return {
        "refused": list(blob.get("refused") or []),
        "generation": int(blob.get("generation") or 0),
        # TWO POSITIONS, deliberately, because a refusal makes them differ:
        #   `climb`     — how far UP THE LADDER the climb has got to. Only ever
        #                 rises. This is what the next climb accumulates from.
        #   `last_rung` — the rung the last dispatch actually BOUND to. A refused
        #                 rung steps this DOWN to the previous one.
        # Collapsing them capped the ladder permanently: the step-down wrote the
        # lower rung back as the accumulator, so `climb_steps` could only ever
        # re-propose the refused rung, and every rung ABOVE a refused one became
        # unreachable while `remaining` still listed them as untried.
        "climb": int(blob.get("climb") or 0),
        "last_rung": int(blob.get("last_rung") or 0),
        # The stuck-protocol `attempts` counter as of that dispatch. Its ONLY job
        # is to answer "has a new failure been recorded since we last dispatched?",
        # which is what keeps the accumulating climb idempotent on resume.
        "last_attempts": int(blob.get("last_attempts") or 0),
    }


def has_state(plan_dir, session_id):
    """Has `begin` ever recorded a dispatch for this session here? Read-only."""
    return bool((rsi.load_state(plan_dir).get(STATE_KEY) or {}).get(session_id))


def _save_session_state(plan_dir, session_id, rec):
    state = rsi.load_state(plan_dir)
    esc = state.setdefault(STATE_KEY, {})
    esc[session_id] = rec
    rsi.save_state(plan_dir, state)
    return rec


def record_refusal(plan_dir, session_id, model, reasoning, reason, source):
    """THE explicit refusal interface (nothing infers a refusal — see the module
    docstring). Marks one ladder cell unavailable for the rest of this session.

    Idempotent: recording the same cell twice is a no-op, so an orchestrator that
    retries the report cannot corrupt the set."""
    rec = session_state(plan_dir, session_id)
    key = rung_key(model, reasoning)
    if key not in rec["refused"]:
        rec["refused"].append(key)
        _save_session_state(plan_dir, session_id, rec)
    rsi.log_event(plan_dir, "escalation_rung_refused", session_ids=[session_id],
                  rung=key, model=model, reasoning=reasoning, reason=reason,
                  source=source, refused=rec["refused"])
    return rec


def record_dispatch(plan_dir, session_id, rung, climb=None):
    """Record where a dispatch got to. Called by `cmd_begin` once the cell is
    settled (after any decline or refusal step-down), never before.

    `rung` is what BOUND; `climb` is the ladder position that was ASKED FOR
    (they differ exactly when a refused rung forced a step-down). See
    `session_state` for why conflating them capped the ladder permanently.

    This is an OBSERVATION, not a stored-and-fired decision — the distinction the
    module docstring turns on. `compute()` still re-derives the rung from scratch
    on every call; what this adds is the one fact no derivation can recover:

      * WHICH RUNG THE ATTEMPT THAT JUST FAILED RAN ON. The failure counters move
        on after that attempt, so re-deriving the rung at halt time answers a
        different question. When the FINAL failure carries a different signature
        from the streak that drove the climb, `consecutive` has already reset and
        the derived rung is lower than the one that really ran — which named the
        wrong rung in the BLOCKED brief and listed rungs that WERE tried as
        untried.
      * THE BASE OF THE NEXT CLIMB. The climb ACCUMULATES across root causes: each
        streak is measured from the rung the session is standing on, not from the
        authored cell. Deriving the rung from the current streak alone stalled the
        ladder — a session that climbed to rung 2 on cause A, then failed twice
        consecutively on cause B, sat at rung 2 while demonstrably stuck again
        (measured 2026-08-14). And a different error must not hand the model BACK
        either, so this value only ever rises within a generation.

    Re-running `begin` on resume re-records the same values and derives the same
    rung, because the climb advances on a NEW FAILURE (`attempts` moving), never on
    a new dispatch."""
    rec = session_state(plan_dir, session_id)
    rung = max(0, int(rung or 0))
    rec["last_rung"] = rung
    # Only ever rises — a refusal step-down must not un-climb the ladder.
    rec["climb"] = max(rec["climb"], rung if climb is None else max(0, int(climb)))
    rec["last_attempts"] = _attempts(plan_dir, session_id)
    return _save_session_state(plan_dir, session_id, rec)


def last_rung(plan_dir, session_id):
    """The rung the most recent dispatch bound to (0 = as authored). Read-only."""
    return session_state(plan_dir, session_id)["last_rung"]


def last_climb(plan_dir, session_id):
    """The ladder position the climb has REACHED, refusals included. Read-only.

    Differs from `last_rung` only when a refused rung forced a step-down, which is
    exactly the case the BLOCKED brief has to be able to describe."""
    return session_state(plan_dir, session_id)["climb"]


def reset(plan_dir, session_id, why):
    """Fresh session, fresh ladder. Clears the refused set AND bumps the
    generation counter.

    The generation bump is what keeps the OUTCOME LEDGER honest: an amendment
    that changes the authored cell (or a deliberate redispatch) starts a new
    cohort, and records from before it must not be averaged together with records
    from after it as though they described the same experiment."""
    rec = session_state(plan_dir, session_id)
    new = {"refused": [], "generation": rec["generation"] + 1,
           "climb": 0, "last_rung": 0, "last_attempts": 0}
    _save_session_state(plan_dir, session_id, new)
    rsi.log_event(plan_dir, "escalation_reset", session_ids=[session_id],
                  reason=why, generation=new["generation"],
                  cleared_refused=rec["refused"])
    return new


def _rework_count(plan_dir, session_id):
    st = ssio.read_json_with_bak(
        Path(plan_dir) / "_verify_state" / f"{session_id}.json"
    )
    return int((st or {}).get("rework_count") or 0)


def _stuck(plan_dir, session_id):
    return (rsi.load_state(plan_dir).get("stuck") or {}).get(session_id) or {}


def _attempts(plan_dir, session_id):
    """Total failures recorded for this session — the counter that moves exactly
    once per failed attempt, whatever the signature."""
    return int(_stuck(plan_dir, session_id).get("attempts") or 0)
