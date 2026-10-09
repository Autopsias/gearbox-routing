#!/usr/bin/env python3
"""THE SPEC, written out independently of the hook that implements it.

Every number here is the session prompt's or the recorded run's MEASURED evidence, spelled
out rather than read from compact-policy.py.  That separation is the point: an
earlier version of the envelope check read `mod.MIN_HEADROOM`, so lowering the
constant lowered the bar the assertion applied and the check could not fail.
Its own neuter probe caught it.  Nothing in this file imports the hook.

It also carries the replay of the recorded run, because a ceiling whose only proof is
an argument is exactly what shipped a veto that never disarmed.
"""

from __future__ import annotations

import compact_policy_testkit as kit


# the recorded run, verbatim from a recorded probe run: haiku, a 200,000
# model window, --autocompact 100000, an UNCONDITIONAL veto. These are the seven
# ctx values at which PreCompact fired. The run died with "Prompt is too long"
# immediately after the last one, with trigger still "auto" — zero recovery
# compactions, no harness safety net.
RECORDED_RUN_TRIGGERS = (75_035, 90_628, 106_167, 122_508, 137_361, 152_853, 167_281)
RECORDED_RUN_MODEL_WINDOW = 200_000
RECORDED_RUN_DEATH_CTX = 167_281  # the last block; the request after it failed
RECORDED_RUN_MAX_TURN_GROWTH = 16_341  # the largest step in the series


def replay_recorded_run(mod):
    """The seven triggers through verdict(), exactly as the hook would see them."""
    policy = {"type": "planning", "ceiling_fraction": 0.75}
    effective = RECORDED_RUN_TRIGGERS[0]  # frozen at the first PreCompact call
    out = []
    for ctx in RECORDED_RUN_TRIGGERS:
        ceiling, _ = mod.ceiling_for(policy, effective, RECORDED_RUN_MODEL_WINDOW)
        out.append(mod.verdict("auto", policy, "unknown", ctx,
                               RECORDED_RUN_MODEL_WINDOW, ceiling)[0])
    return out




# The spec's own number, written out HERE rather than read from the module.
# Reading mod.MIN_HEADROOM would make the check move with the rule it checks:
# lowering the constant would lower the bar the assertion applies, and the
# check could not fail. Caught by its own neuter probe.
REQUIRED_MIN_HEADROOM = 100_000

# The window an operator might wrongly assert for a session shaped like the recorded run, and
# the ceiling that follows from it. Written out here, not read from the
# module, for the same reason REQUIRED_MIN_HEADROOM is.
OVERSTATED_WINDOW = 1_000_000
OVERSTATED_CEILING = 300_000


def illegal_blocks(_mod, blocked):
    """The spec verbatim: block ONLY when trigger=='auto' AND a policy exists AND
    safe_point!='ok' AND ctx and model_window are ints
    AND the LIVE headroom (model_window - ctx) >= 100_000 AND ctx < ceiling AND
    (type=='planning' OR safe_point=='hold' OR (type=='default' AND the attempt
    is MID-TURN — v4)). Anything else in `blocked` is a safety bug.

    `model_window - ctx`, written out here rather than taken from the hook: the
    headroom frozen at the first PreCompact does not shrink as the conversation
    grows, so a rule bound on it never disarms — which is what let the veto
    block at ctx 167,281 in the recorded run, the trigger the session died on.
    """
    rows = [b for b in blocked]
    return [
        b for b in rows
        if not (b[0] == "auto" and b[1] is not None
                and b[2] != "ok" and isinstance(b[3], int) and isinstance(b[4], int)
                and (b[4] - b[3]) >= REQUIRED_MIN_HEADROOM
                and isinstance(b[5], int) and b[3] < b[5]
                and (b[1] == "planning" or b[2] == "hold"
                     or (b[1] == "default" and b[6] is True)))
    ]




def c_envelope(m):
    blocked = kit.sweep_blocks(m)
    assert blocked, "the sweep is pointed at nothing"
    illegal = illegal_blocks(m, blocked)
    assert not illegal, "the veto blocks outside its permitted envelope: %s" % illegal[:3]


def c_recorded_replay(m):
    decisions = replay_recorded_run(m)
    assert "allow" in decisions, (
        "the veto never released across the whole recorded run: %s" % decisions
    )


def c_window_uncorroborated(m):
    """A window we only ASSERTED must never let the veto block a small session.

    the recorded run IS this case: a real 200k window, ctx 75k-167k. If the operator
    asserts 1M there, every rule downstream sees ~830k of headroom and the veto
    defers straight past the real limit — the death the recorded run measured. The guard is
    what makes an asserted window safe, so it is checked against the exact
    numbers that killed a run, not a synthetic pair.
    """
    for ctx in RECORDED_RUN_TRIGGERS:
        decision, reason = m.verdict(
            "auto", {"type": "planning", "ceiling_fraction": 0.75}, "hold",
            ctx, OVERSTATED_WINDOW, OVERSTATED_CEILING,
        )
        assert decision == "allow", (
            "the veto blocked at ctx=%d on an ASSERTED %d window while the real "
            "window was %d — this is the recorded run's death: %s"
            % (ctx, OVERSTATED_WINDOW, RECORDED_RUN_MODEL_WINDOW, reason)
        )
