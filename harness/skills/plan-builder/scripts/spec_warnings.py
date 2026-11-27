"""Advisory warnings and a field check for the plan spec, split out of build_plan.py.

build_plan.py is over its size limit and the ratchet allows no growth, so the
prompt-size warning and the blocking-rule pieces live here.
"""
import sys


# CALIBRATED, not guessed. Measured across 207 sessions in 25 plans
# on disk: p50 = 2,486 chars of `prompt`, p75 = 4,133, p90 = 6,409, p95 = 8,088.
# The threshold is p90, so this fires on roughly one session in ten.
#
# Why it is worth a warning at all: on the adaptive-routing plan, s02 (1,486
# chars) finished in 26 minutes and s03 (7,708) took five and a half hours
# across two attempts. Both declared ONE item, so nothing else in the builder
# could tell them apart — item count is the progress unit and it does not track
# work at all.
#
# Why it WARNS and never refuses: size predicts cost only loosely. s09 on that
# same plan sat at p84 and finished in six minutes, because most of its time was
# spent waiting on trials rather than writing code. This flags a card worth a
# second look; it does not know how long the work takes. A deliberately large
# session is a legitimate choice, so there is no flag to silence it — the plan
# author reads the line and decides.
_SESSION_PROMPT_P90 = 6400


def oversized_session_warning(spec):
    """Warn on any session whose `prompt` exceeds the corpus p90 (see above)."""
    big = [
        (s["id"], len(s.get("prompt") or ""))
        for s in spec.get("sessions", [])
        if len(s.get("prompt") or "") > _SESSION_PROMPT_P90
    ]
    if not big:
        return
    listed = ", ".join(f"{sid} ({n:,} chars)" for sid, n in sorted(big, key=lambda x: -x[1]))
    print(
        f"WARNING: {len(big)} session(s) carry a work brief above the {_SESSION_PROMPT_P90:,}-char "
        f"p90 of every plan measured on this machine: {listed}. A brief this size usually means "
        "several sessions' worth of work on one card — the largest measured case ran 12x longer "
        "than the smallest, and both declared one item, so item count cannot warn you. Consider "
        "splitting at an item boundary. This is advisory: a deliberately large session is fine.",
        file=sys.stderr,
    )


def warn_missing_blocking_rule(spec, resolve_verify):
    """One warning naming each session with an automated review gate and no verify.blocking_rule."""
    phases = {p["id"]: p for p in spec.get("phases") or []}
    bare = []
    for s in spec.get("sessions", []):
        vb = resolve_verify(s, phases) or {}
        gates = vb.get("gates") or []
        if any(g.startswith(("llm-review-", "cross-family-review-")) or g == "adversarial-review"
               for g in gates) and not vb.get("blocking_rule"):
            bare.append(s["id"])
    if bare:
        print(
            f"WARNING: session(s) {', '.join(bare)} have an automated review gate but no "
            "verify.blocking_rule. Without it the reviewer decides alone what blocks. Write 2-3 "
            "numbered conditions, each a failure a test could show. See references/schemas.md -> "
            "'verify.blocking_rule'.",
            file=sys.stderr,
        )


def check_blocking_rule(block, where):
    """Refuse a verify.blocking_rule that is not a non-empty string."""
    rule = block.get("blocking_rule")
    if rule is not None and (not isinstance(rule, str) or not rule.strip()):
        raise ValueError(f"{where}.blocking_rule must be a non-empty string, got {rule!r}")
