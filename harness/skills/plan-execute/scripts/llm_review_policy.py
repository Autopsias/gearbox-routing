"""Opt-in review policy for the LLM review gate (canary). Both rules are OFF unless the
gate is given their flag, and both only ever LOWER a block: they never make a finding block.

WHY (review audit of 1,131 verified plan sessions): 66% of failed attempts failed only
on an LLM review gate; 385 of 811 high findings sat in code written to fix an earlier finding; 162
sessions ended at their rework cap instead of on a verdict. A review loop has no "good enough"
point of its own (memory: cap-a-review-loop-that-grows-its-own-surface).

  --stop-after-round N  from attempt N+1, a NEW blocking finding in the fix delta is recorded
                        `noted` with `policy: followup` -- printed, kept in the ledger, never a
                        reason to re-dispatch. Findings in the session's original work, and every
                        open prior, still block.
  --require-repro       asks the reviewer for `repro` on every blocking finding (a failing test,
                        or a command and the output it showed). A finding without one is LABELLED
                        `policy: unproven` and STILL BLOCKS. LABEL ONLY, ON PURPOSE (security
                        review of 07524b8b): clearing it would let the reviewed diff or
                        the intent text tell the reviewer to omit `repro` and so clear every block,
                        which breaks the gate's rule that reviewer output can never clear a
                        finding. The canary measures how many blocks carry no proof.

The stop rule uses `noted`, the status every ledger reader already treats as advisory, so nothing
downstream needs to learn a new word; `policy` says why. It reads only what the gate computes itself
(the attempt number and the content-hash fix delta), never the reviewer's own tags.
"""
import llm_review_ledger as ledger

REPRO_PROMPT = (
    "Each finding of severity high or critical MUST carry a `\"repro\"` field: the name of a test "
    "that fails on this diff, or an exact command and the output you observed that shows the "
    "defect. Put `repro` inside that finding's object in the fenced array."
)


def prompt_extra(args):
    return "\n\n" + REPRO_PROMPT if getattr(args, "require_repro", False) else ""


def apply(args, led, parsed, recs, fail):
    """-> (fail, recs, moved). Re-labels NEW open findings per the flags; priors are never touched."""
    stop, need = getattr(args, "stop_after_round", 0) or 0, getattr(args, "require_repro", False)
    if not (led and fail and (stop or need)):
        return fail, recs, []
    priors = {p.get("fid") for p in led.priors}
    repro = {ledger.fingerprint(f): str(f.get("repro") or "").strip() for f in parsed if isinstance(f, dict)}
    moved = []
    for r in recs:
        if r.get("status") != "open" or r.get("fid") in priors:
            continue
        # The gate's OWN content-hash delta, never the record's `new_in`: for a path outside
        # that delta, judge() passes the reviewer's self-reported tag through, so a steered
        # reviewer could tag an original-work finding "delta" (security review).
        if stop and led.attempt > stop and ledger._norm(r.get("file") or "") in (led.delta or ()):
            r.update(status="noted", policy="followup")
        elif need and not repro.get(r.get("fid")):
            r["policy"] = "unproven"  # label only: still open, still blocks (see docstring)
        else:
            continue
        moved.append(r)
    return any(r.get("status") == "open" for r in recs), recs, moved


def report(moved):
    """The lines the orchestrator records as follow-ups. Empty when nothing moved."""
    if not moved:
        return ""
    rows = "\n".join(f"  - [{r['policy']}] {r.get('severity')} {r.get('file')}:{r.get('line')} "
                     f"{r.get('summary')}" for r in moved)
    return (f"\n[review-policy] {len(moved)} finding(s) relabelled. followup = new in fix code after "
            f"the stop round, recorded and NOT blocking: record it as a follow-up. unproven = no "
            f"repro, STILL blocking: reproduce it before fixing it.\n{rows}")
