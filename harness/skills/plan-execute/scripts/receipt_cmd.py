"""`record-receipt`: bind a finished dispatch to its Agent ID, kept out of run.py (file-size ratchet)."""
import sys

import attestation
import escalation as esca
from apply_guards import inline_fix_refusal
import outcomes as outc
import run_state_io as rsi


def record_receipt(plan_dir, session_id, agent_id, transcript, backend, inline_fix, out):
    """TEL-01 — the served-model attestation HANDOFF. The orchestrator calls this
    exactly once per dispatch, AFTER the Agent tool call returns and BEFORE
    apply/verify resolution — the earliest point an Agent ID exists at all, so
    it cannot run any earlier (`begin` precedes the Agent call). The receipt is
    what lets `outcomes.py` write `model_ran_source="attested"` instead of
    downgrading every real outcome to `"unknown"`; see outcomes.py's module
    docstring for the full per-backend rule.

    TEL-01 finding 1 — stamps the receipt with the (generation, dispatch
    ordinal) it belongs to, so a resolution recorded later can tell whether
    this receipt is actually ABOUT it — see `outcomes._fresh_receipt`."""
    if not (agent_id or "").strip():
        raise SystemExit("refusing to record a receipt without --agent-id: the receipt IS the "
                          "correlation between this dispatch and an Agent ID — an empty one "
                          "proves nothing.")
    inline_fix = (inline_fix or "").strip() or None
    refusal = None if inline_fix else inline_fix_refusal(plan_dir, session_id, agent_id, transcript, backend)
    if refusal:
        rsi.log_event(plan_dir, "receipt_refused", session_ids=[session_id], agent_id=agent_id, reason=refusal)
        out({"recorded": False, "failure": "inline-fix-unlabelled", "reason": refusal}, plan_dir=plan_dir)
        sys.exit(1)
    attested, usage = attestation.read_transcript(backend, transcript)
    generation = esca.session_state(plan_dir, session_id)["generation"]
    dispatch_ordinal = outc._dispatch_ordinal(plan_dir, session_id)  # noqa: SLF001 — same module family
    rec = rsi.record_receipt(plan_dir, session_id, agent_id=agent_id, transcript=transcript,
                             backend=backend, attested=attested,
                             generation=generation, attempt=dispatch_ordinal, usage=usage, inline_fix=inline_fix,
                             usage_base=attestation.usage_base(rsi.get_receipt(plan_dir, session_id),
                                                               agent_id, generation, dispatch_ordinal))
    out(
        {
            "recorded": True, "session": session_id, "backend": backend,
            "attested": bool(attested),
            "model_ran_source": outc.SOURCE_ATTESTED if attested else outc.SOURCE_REQUESTED,
            "receipt": rec,
        },
        plan_dir=plan_dir,
    )
