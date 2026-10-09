#!/usr/bin/env python3
"""The turn boundary: a `default` session compacts when a turn STARTS, not mid-task.

MEASURED over ~/.gearbox-state/compaction/decisions.ndjson: of the automatic compactions
ALLOWED in `default` sessions, only about a fifth were allowed within 30 s of the
user's prompt and most came more than a minute into the turn.
Orchestrator and planning sessions had the safe-point regime; a default session
had nothing. (An earlier count read the `compacted` record, which lands minutes AFTER the
allow, so no compaction could ever score as a turn start; count the allow.)

The mechanism needs no new event. Claude Code re-attempts a blocked compaction
on its own: with a block pending, the first attempt of the NEXT turn fires a
median well under a second after the UserPromptSubmit hook (nearly all under 5 s,
nearly all of the rest under 30 s). So `prompt` stamps the turn start, and `pre-compact` defers any attempt
that arrives after the start window has closed.

FAIL OPEN: a session with no readable stamp is never mid-turn.
"""

from __future__ import annotations

from compact_store import now_ms

# The start window. 30 s keeps nearly all measured turn-start attempts.
TURN_START_MS = 30_000

# The deferral's own share of the window headroom, tighter than any session
# type's. MEASURED over turns that carried a deferred compaction to their
# end: context grew p50 6k, p90 34k, p99 111k tokens. 0.15 of a 1M session's
# ~730k headroom is ~110k — room for the p99 turn, not for a runaway one.
# ponytail: one fraction for every window size; ceiling_for's MIN_HEADROOM
# clamp is what keeps a 200k session safe, not this number.
TURN_END_FRACTION = 0.15


def stamp(policy):
    """The policy `prompt` must write: the same document, with the turn start."""
    if not isinstance(policy, dict):
        return None
    policy["turn_start_ms"] = now_ms()
    return policy


def mid_turn(policy):
    """True only when a turn start is KNOWN and its start window has closed."""
    started = (policy or {}).get("turn_start_ms")
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return False
    return now_ms() - started >= TURN_START_MS


def defers(policy, point):
    """(mid, policy_for_the_ceiling) — what pre-compact needs, in one call.

    Only a `default` session with no live `hold` takes the tighter ceiling: a
    hold is a skill's explicit request and keeps the type's own fraction.
    """
    mid = (policy or {}).get("type") == "default" and mid_turn(policy)
    if mid and point != "hold":
        return True, dict(policy, ceiling_fraction=TURN_END_FRACTION)
    return mid, policy
