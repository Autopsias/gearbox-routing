#!/usr/bin/env python3
"""compaction_blindness.py — the instrument's own blind spots, as one table.

Extracted from compaction_report.py when that file crossed its 500-LOC bound.
It is one unit on purpose: the counter TABLE and the function that fills it must
change together, because every consumer surface (the flag, the verdict line, the
decision card, the rendered banner) is derived from the table alone.
"""

import compaction_retro as cr


# THE one list of counters that can raise `blind`. The flag, the verdict line,
# the decision card and the rendered banner are ALL derived from this list, so
# a future counter cannot reach one surface and miss another — the exact drift
# that let a page blinded by unrecognized_decision_records show a banner whose
# every printed number was zero.
BLINDNESS_HOLES = (
    ("measurement_unavailable_records",
     "record(s) whose reason was measurement_unavailable (the hook could not "
     "read the context and allowed by default)"),
    ("hook_error_records",
     "compaction record(s) written by the fail-open crash path (reason "
     "hook-error) — the hook CRASHED; its 'allow' is the wrapper's stamp, not "
     "a decision"),
    ("unrecognized_decision_records",
     "compaction record(s) carried a decision value this reader does not "
     "recognize (writer/reader vocabulary drift — counted, never zeroed)"),
    ("sessions_with_no_ledger_lines",
     "scanned session(s) started after the hooks activation and left NO "
     "ledger line at all (the instrument never saw them)"),
    ("sessions_hook_inactive",
     "session(s) ran with the kill switch on or the hooks unregistered"),
    ("sessions_with_lines_but_no_heartbeat",
     "session(s) wrote ledger lines but left no heartbeat, so they are "
     "excluded from every verdict"),
    ("sessions_unclassifiable_no_activation",
     "session(s) the instrument could not classify because the hooks "
     "activation record is missing or unparseable (when this is NONZERO, "
     "three of the counters above were not measured at all and their zeros "
     "mean nothing)"),
)


def _blindness(observed, unobserved, hooks_ts):
    """MOVE 2b — the instrument's own blindness as a first-class row. A healthy
    allow rate over a hook that silently stopped measuring looks identical to
    success, and that is the defect this whole plan was built to remove.

    EVERY RECORD-LEVEL COUNTER IS SUMMED FROM THE PER-SESSION COUNTS
    attach_decisions computed — the same admitted rows (probe and
    stale-policy_version records already excluded) every verdict reads. Reading
    the raw decisions list here let a probe row with an unknown decision value
    fire the apex banner over records no verdict can see, and left the
    per-session counters themselves with no reader.

    `sessions_with_no_ledger_lines` counts only sessions that SHOULD have left a
    line and left NOTHING: SCANNED sessions that started at or after the hooks
    activation, produced no heartbeat, AND wrote no decisions line either. A
    scanned session from before the instrument existed is not a blind spot, and
    counting it as one would make the row alarming and meaningless at the same
    time. Neither is a session that DID write a line: it was seen, it is already
    counted in `filtered`, and reporting it as never-seen made the instrument's
    own honesty row dishonest — the one row that cannot be allowed to lie.

    Every filter named above is applied in the code below. This docstring stated
    the `scanned` rule before the code did, which is how a row created purely by
    attach_decisions' setdefault came to fire the INSTRUMENT BLINDNESS banner."""
    rows = list(observed.values()) + list(unobserved.values())
    unavailable = sum(r.get("measurement_unavailable") or 0 for r in rows)
    hook_errors = sum(r.get("hook_errors") or 0 for r in rows)
    # Vocabulary drift between the writer and this reader is itself a form of
    # blindness: a decision value this reader does not recognise would silently
    # zero a column (the Vetoes column spent three review rounds at a
    # structural 0 because the reader counted "veto" where the writer emits
    # "block"). Count it here so drift is a nonzero number, never a clean zero.
    unrecognized = sum(r.get("unrecognized_decisions") or 0 for r in rows)
    silence = cr._silence_counts(observed.values())
    missing, headless, spoke_but_unobserved = 0, 0, 0
    # No activation timestamp means the three counters below cannot be computed
    # at all — the "started after the hooks activation" filter has no operand.
    # Leaving them at 0 made the page print "Instrument healthy: every blindness
    # counter is zero" over the exact input that, with a real hooks_ts, reports
    # blind=True. An unmeasurable counter is its own blind spot, so the sessions
    # that could not be classified are counted as one.
    unclassifiable = len(unobserved) if hooks_ts is None else 0
    if hooks_ts is not None:
        for row in unobserved.values():
            if row.get("started") is None or row["started"] < hooks_ts:
                continue
            # It wrote to the ledger, so the instrument demonstrably saw it. A
            # missing HEARTBEAT is still a hole in the denominator (it is
            # excluded from every verdict), but it is a different hole and is
            # counted as one.
            if (row.get("ledger_records") or 0) or (row.get("stale_records") or 0):
                spoke_but_unobserved += 1
                continue
            if not row.get("scanned"):
                continue
            # A headless Agent-SDK (`sdk-py`) run does not load the user's
            # settings.json hooks at all, so its silence is a SCOPE LIMIT, not a
            # hook that stopped firing. Counted separately, never as a blind spot
            # — `sdk-cli` subagent sessions DO fire the hooks and are counted.
            if (row.get("entrypoint") or "").startswith("sdk-py"):
                headless += 1
            else:
                missing += 1
    out = {
        "measurement_unavailable_records": unavailable,
        "hook_error_records": hook_errors,
        "unrecognized_decision_records": unrecognized,
        "sessions_with_no_ledger_lines": missing,
        "sessions_hook_inactive": silence["hook_inactive"],
        "sessions_no_compaction": silence["no_compaction"],
        "sessions_with_records": silence["has_records"],
        # Only rows the SCANNER actually produced: unobserved also holds
        # ledger-only rows attach_decisions created, which were never scanned.
        "scanned_sessions_never_observed": sum(
            1 for r in unobserved.values() if r.get("scanned")),
        "headless_sdk_sessions_out_of_scope": headless,
        "sessions_with_lines_but_no_heartbeat": spoke_but_unobserved,
        "sessions_unclassifiable_no_activation": unclassifiable,
    }
    # The flag, the verdict line and the banner all read THIS list.
    out["holes"] = [{"key": key, "count": out[key], "what": what}
                    for key, what in BLINDNESS_HOLES]
    out["blind"] = any(h["count"] for h in out["holes"])
    return out
