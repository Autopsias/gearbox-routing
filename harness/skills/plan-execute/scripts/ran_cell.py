"""The (model_ran, reasoning_ran, model_ran_source) cell of one outcome-ledger
record — split out of :mod:`outcomes` to keep it under its size limit."""

SOURCE_ATTESTED = "attested"
SOURCE_REQUESTED = "requested"
SOURCE_UNKNOWN = "unknown"


def ran_cell(attested, model_authored, reasoning_authored, escalated_from, degraded_from):
    """(model_ran, reasoning_ran, model_ran_source) — STRICTLY receipt-gated.

    `attested` is the FRESH receipt's attestation (`outcomes._fresh_receipt`),
    or None when there is no receipt for this dispatch.

    No `record-receipt` for this session at all -> ("unknown", "unknown",
    "unknown"), full stop, even if the closeout claims an `escalated_from` or
    `degraded_from` substitution: those are honest reports of what the
    ORCHESTRATOR asked for, never proof of what actually served the request
    (see module docstring).

    TEL-01 finding 1 — a receipt does NOT get cleared on re-dispatch
    (`run_state_io.get_receipt`'s own docstring says so): if `record-receipt`
    is skipped on a later attempt, the receipt still on file describes an
    EARLIER attempt. `record_receipt` stamps the (generation, dispatch
    ordinal) it was recorded for; if those don't match what THIS resolution
    is actually about, the receipt is stale evidence for a different
    dispatch and must degrade to `unknown` exactly like having no receipt at
    all — never silently carry an old attempt's model forward."""
    # OPERATOR DECISION, 2026-08-15 — "the model that did the work is the model we
    # asked for". A MISSING or STALE receipt no longer collapses the record to
    # `unknown`; it falls through to the requested cell below, recorded honestly as
    # SOURCE_REQUESTED. What is still refused is treating a stale receipt's
    # ATTESTED model as proof for a different dispatch — that hazard is unchanged,
    # and it is why the freshness check still exists rather than being deleted.
    #
    # Why the operator accepted it: an OBSERVED downgrade is already recorded
    # (`degraded_from`, resolved below), so the only unrecorded case is Claude Code
    # silently honouring a blocked model override by falling back with no error —
    # rare, and in this lineup nearly always one rung, fable -> opus. Measured
    # 2026-08-15: with the strict rule, 6 of 6 real records in the deployed ledger
    # read `unknown@unknown`, so the strict rule did not buy accuracy — it bought
    # an empty dataset and a learning loop that could never run.
    # A transcript names the served model, not the effort. Fall back to the effort
    # this attempt was DISPATCHED at (the climbed/degraded cell), never the
    # authored one — a climbed attempt would otherwise land in the wrong cell.
    esc_ran = (escalated_from.get("ran") or {}) if isinstance(escalated_from, dict) else {}
    deg = degraded_from if isinstance(degraded_from, dict) else {}
    reasoning_dispatched = esc_ran.get("reasoning") or deg.get("reasoning") or reasoning_authored
    if attested and attested.get("model"):
        return attested["model"], attested.get("reasoning") or reasoning_dispatched, SOURCE_ATTESTED
    # Receipt exists (an Agent ID was correlated) but no served-model evidence —
    # the best honest claim is "what we asked for", which is the escalated/
    # degraded cell when one applies, else the authored one.
    if escalated_from and isinstance(escalated_from, dict):
        ran = escalated_from.get("ran") or {}
        if ran.get("model"):
            return ran["model"], ran.get("reasoning") or reasoning_authored, SOURCE_REQUESTED
    if degraded_from and isinstance(degraded_from, dict) and degraded_from.get("ran"):
        return degraded_from["ran"], degraded_from.get("reasoning") or reasoning_authored, SOURCE_REQUESTED
    return model_authored or "unknown", reasoning_authored, SOURCE_REQUESTED
