#!/usr/bin/env python3
"""What happens when a gate fails: rework, escalate, or halt.

Split out of verify.py, which had grown 71 lines past its size baseline. This is
the decision layer — given a failed gate, work out whether the session climbs a
rung, reworks at the same rung, or stops and asks a human. verify.py keeps the
running of gates; this file keeps the verdict.

The on-disk state helpers come from verify_paths so nothing here imports
verify.py back.
"""
import json
import re
import sys as _sys

import article_block as ab
import escalation as esca
import manifest_io as mio
import outcomes as outc
import route_at_dispatch as rad
import run_state_io as rsi
import stuck_protocol as sp
from verify_pass import GATE_PASSED
from verify_paths import (GATE_FAILED, _feedback_path, _html, _save_state,
                          verify_state_path)




# --------------------------------------------------------------------------
# Fail → rework or halt
# --------------------------------------------------------------------------
def _escalation_descriptor(plan_dir, session_id, steps=None):
    """The rung `begin` would compute for this session RIGHT NOW, or None.

    `steps` overrides the derived climb — used by the halt path, which must name
    the rung the attempt that JUST FAILED ran on, not the one a further attempt
    would have used."""
    import run as _run  # noqa: PLC0415 — lazy: run.py imports verify.py

    manifest = mio.load_manifest(plan_dir)
    session = mio.session_by_id(manifest).get(session_id)
    if not session:
        return None
    session = rad.effective_session(plan_dir, manifest, session)  # v8: the frozen cell
    # The LANE, not the run-level dial: verify has no `--harness` flag, so it
    # recovers the lane from the session's own dispatch history plus the same
    # pre-pass rules `begin` applies. The event read is the LAST
    # `dispatch_started` (see `_dispatch_lane`, run.py) — deliberately NOT
    # `codex_dispatch`: run.py:1017 records that keying off the mere PRESENCE of
    # a `codex_dispatch` event is what pinned the lane to codex forever, so a
    # later edit "aligning" this code to the old wording would reintroduce it.
    provider, ssot_text = _run._load_routing()
    lane = _run._dispatch_lane(session, provider or "anthropic", ssot_text,
                               plan_dir=plan_dir)
    # THE CELL THE CLIMB STARTS FROM is lane-specific (ESC-03): on the codex lane
    # it is the RESOLVED (codex_model, codex_effort) pair, because the manifest's
    # own `model` may be a Claude token translation turned into a gpt-5.6 model —
    # and the openai ladder cannot be walked from `sonnet`. Resolved through the
    # SAME single translation surface `begin` uses, so the rung announced here and
    # the rung dispatched there are the same rung. A session that no longer
    # resolves simply gets no announcement (the dispatcher will block it loudly,
    # which is the right place for that error to appear).
    if lane == "codex":
        try:
            model, reasoning = _run._resolve_codex_dispatch(
                session.get("model"), session.get("reasoning"), ssot_text,
                task_class=(session.get("task_class") or "").strip().lower() or None,
            )
        except _run.UnroutableCodexSession:
            return None
    else:
        model = _run._normalize_model(session.get("model"))
        reasoning = _run._reasoning_tier(session.get("reasoning"))
    return _run._escalation_descriptor(
        plan_dir, manifest, session, model, reasoning, steps=steps, lane=lane,
    )


def _escalation_next(plan_dir, session_id):
    """`{authored, ran, rung}` for the next dispatch when it will run ABOVE the
    authored cell, else None.

    Whether the climb will actually BIND is decided by the same
    `run._escalation_descriptor` the dispatcher calls, which flattens a
    non-binding climb to rung 0 — a session pinned to a functional Claude agent
    therefore yields None here and is never told it escalated when it did not. A
    codex-lane session DOES climb since ESC-03, and is announced with the rung it
    will actually run (its gpt-5.6 cell, not its manifest token)."""
    desc = _escalation_descriptor(plan_dir, session_id)
    if not desc or desc["rung"] == 0:
        return None
    # `authored` is the ladder's BASE RUNG — the cell this session resolves to on
    # its own lane. On the Claude lane that equals the manifest token, so the two
    # were used interchangeably. On the codex lane they are NOT the same: a
    # session the manifest authors as `Opus`@`high` resolves to a gpt-5.6 cell,
    # and calling that "what the plan authored" tells the subagent it was
    # authored as a model no human ever wrote down. Carry the manifest's own
    # declaration alongside, so the note can say both without inventing either.
    session = mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id) or {}
    return {"authored": desc["authored"], "ran": desc["ran"], "rung": desc["rung"],
            "model_ran_source": desc["model_ran_source"],
            "declared": {"model": session.get("model"),
                         "reasoning": session.get("reasoning")}}


def _authored_sentence(nxt):
    """The opening sentence of the escalation note, true on BOTH lanes.

    The base rung and the manifest's own token coincide on the Claude lane and
    diverge on the codex lane (see `_escalation_next`). When they diverge, say
    both rather than picking one — claiming the plan authored a gpt-5.6 cell is
    false, and hiding the base rung leaves the raise unexplained.
    """
    base = f"`{nxt['authored']['model']}@{nxt['authored']['reasoning'] or 'unset'}`"
    dm, dr = nxt.get("declared", {}).get("model"), nxt.get("declared", {}).get("reasoning")
    declared = f"`{dm}@{dr or 'unset'}`" if dm else None
    if not declared:
        return f"The plan named no model; its class default is {base}."
    if declared.lower() == base.lower():
        return f"The plan authored this session as {base}."
    return (f"The plan authored this session as {declared}, which resolves on this "
            f"session's lane to {base} — the base rung of the ladder below.")


def _escalation_status(plan_dir, session_id):
    """One line naming the rung this session ACTUALLY REACHED and the rungs above
    it that the exhausted rework budget never bought. "" when the session never
    escalated at all (pre-v6 manifest, opt-out, unpinned model, or a budget that
    ran out before the climb ever armed).

    The position comes from `escalation.last_climb` — what `begin` RECORDED at
    dispatch, not a re-derivation. Re-deriving it here answered a different
    question: by halt time the failure counters have moved past the attempt that
    just failed, and when that final failure carried a DIFFERENT signature from
    the streak that drove the climb, the streak has already reset — so the brief
    named a lower rung than the one that ran and listed rungs that WERE tried as
    untried.

    `last_climb`, not `last_rung`, so a REFUSED rung is still described. Feeding
    the climb back through `escalation.compute` re-applies the refusal step-down,
    which lands the reported rung back on what actually ran AND populates
    `refused_skipped` — so the brief can say "every escalated rung was refused"
    where that is the truth, instead of falling silent because the refusal had
    pushed the recorded rung back to 0."""
    return esca.brief_line(
        _escalation_descriptor(plan_dir, session_id,
                               steps=esca.last_climb(plan_dir, session_id))
    )


_CONVERGENCE = re.compile(
    r"convergence: attempt=(?P<attempt>\d+) prior_fixed=\d+ prior_open=(?P<open>\d+) "
    r"new_in_delta=(?P<delta>\d+) new_outside=(?P<outside>\d+)")


def _indeterminate(plan_dir, session_id, state, gate, excerpt):
    """The gate COULD NOT DECIDE. Leave it pending and charge nobody.

    A review gate that times out or returns no parseable block exits its declared
    `indeterminate_exit` (2). That is neither a pass nor a finding: nothing was
    reviewed. Recording it as a failed rework charged the AGENT's budget for the
    harness's own timeout -- measured on s13: the reviewer exceeded
    600s twice on a 21-file surface, the session halted at 2/2 "exhausted", and
    not one finding had been raised. The feedback file carries the reason so the
    operator can raise the timeout FROM A MEASUREMENT or run the reviewer by
    hand; `verify-begin --resume` then re-runs this gate with the budget intact.
    """
    fb = _feedback_path(plan_dir, session_id)
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(
        f"# Verification INDETERMINATE — {session_id}\n\n"
        f"Gate `{gate}` could not decide (its declared indeterminate exit). This is NOT a "
        f"finding and was NOT charged as a rework attempt "
        f"(rework {state['rework_count']}/{state['max_rework']} unchanged). The gate stays "
        f"pending: fix the cause below, then `verify-begin --resume` re-runs it.\n\n"
        f"## Gate output (redacted, tail)\n\n```\n{excerpt}\n```\n"
    )
    # STILL a failure for the STUCK protocol, just not for the BUDGET. Charging
    # nothing was right; recording nothing was not -- `sp.record_failure` has one
    # caller (`_gate_failed`), so routing every declared indeterminate here made
    # `_indeterminate_signature` unreachable in production and a gate that times
    # out forever could never arm. Measured on s04: two 900s timeouts
    # on a 5,543-line surface, nothing armed, nothing suggested splitting it.
    stuck = sp.record_failure(plan_dir, session_id, excerpt)
    if stuck["triggered"]:
        # ARMED but SILENT is the same defect as not arming at all. `_gate_failed`
        # appends the research pass to the feedback the re-dispatch reads and logs
        # `stuck_protocol_armed`; discarding this return here meant a gate that
        # could not decide twice armed nothing an operator or a re-dispatch could
        # see -- while SKILL.md and verify-gates.md promised the opposite.
        fb.write_text(fb.read_text() + sp.research_prompt(stuck))
        rsi.log_event(plan_dir, "stuck_protocol_armed", session_ids=[session_id],
                      gate=gate, signature=stuck["sig"], error_class=stuck["class"],
                      consecutive=stuck["consecutive"])
    _save_state(plan_dir, session_id, state)          # gate_status[gate] stays pending
    ab.apply_mutation(_html(plan_dir), session_id, status="PARTIAL",
                      note=f"verify gate {gate} INDETERMINATE — not charged; re-run verify")
    rsi.log_event(plan_dir, "verify_indeterminate", session_ids=[session_id], gate=gate,
                  rework_count=state["rework_count"], max_rework=state["max_rework"])
    return {"action": "indeterminate", "session": session_id, "gate": gate,
            "rework_count": state["rework_count"], "max_rework": state["max_rework"],
            "feedback_file": str(fb),
            **({"stuck_protocol": stuck} if stuck["triggered"] else {}),
            "hint": ("the gate could not decide (timeout / no parseable answer); budget not "
                     "charged. Raise the gate's timeout from a measured run, or run the "
                     "reviewer by hand, then `verify-begin --session %s --resume`." % session_id)}


def _gate_failed(plan_dir, session_id, state, gate, excerpt):
    """Record a failed gate, then take exactly one of two branches.

    Split into _reworked / _halted when this function moved out of verify.py:
    a new file gets no grandfathering, so the 114-line original had to become
    what it always was — a prelude and two mutually exclusive outcomes.
    """
    state["gate_status"][gate] = GATE_FAILED
    state["failures"][gate] = excerpt
    fb = _feedback_path(plan_dir, session_id)
    fb.parent.mkdir(parents=True, exist_ok=True)
    attempt = state["rework_count"] + 1

    # RS-03 — the stuck protocol's TRIGGER, recorded rather than asserted. Every
    # failure here reduces to a normalised root-cause signature kept in run_state;
    # the SECOND consecutive failure with the same signature arms the protocol and
    # the research pass goes into the feedback the re-dispatch actually reads. A
    # different error next time is progress and resets the counter — which is why
    # this counts signatures, not attempts.
    stuck = sp.record_failure(plan_dir, session_id, excerpt)
    # CLEAN-BRIEF CONTRACT (ESC-02): what an escalated re-dispatch reads is exactly
    # this — the ORIGINAL session prompt plus this redacted, bounded file. Never a
    # prior attempt's transcript. Keeping the rung note in THIS file (rather than
    # anywhere upstream) makes that guarantee mechanical: there is one file, and it
    # is the only thing the re-dispatch gains.
    fb.write_text(
        f"# Verification feedback — {session_id} (attempt {attempt})\n\n"
        f"Gate `{gate}` FAILED. Fix the cause, then this session re-runs and "
        f"re-verifies.\n\n## Gate output (redacted, tail)\n\n```\n{excerpt}\n```\n"
        + (sp.research_prompt(stuck) if stuck["triggered"] else "")
    )
    if stuck["triggered"]:
        rsi.log_event(plan_dir, "stuck_protocol_armed", session_ids=[session_id],
                      gate=gate, signature=stuck["sig"], error_class=stuck["class"],
                      consecutive=stuck["consecutive"])

    if state.get("on_fail", "rework") == "rework" and state["rework_count"] < state["max_rework"]:
        return _reworked(plan_dir, session_id, state, gate, fb, stuck)
    return _halted(plan_dir, session_id, state, gate, excerpt, fb, stuck)


def _reworked(plan_dir, session_id, state, gate, fb, stuck):
    """Budget remains: spend one rework, and tell the re-dispatch what changed."""
    state["rework_count"] += 1
    state["outcome"] = "rework"
    _save_state(plan_dir, session_id, state)
    ab.apply_mutation(_html(plan_dir), session_id, status="PARTIAL",
                      note=f"verify rework {state['rework_count']}/{state['max_rework']}: "
                           f"gate {gate} failed")
    rsi.log_event(plan_dir, "verify_rework", session_ids=[session_id], gate=gate,
                  attempt=state["rework_count"], max_rework=state["max_rework"])
    # TEL-01 — one record per resolution moment: a rework is recorded here,
    # not just at the eventual pass/halt, so attempts-per-success is
    # computable from the ledger alone.
    outc.write(plan_dir, session_id, resolution=f"verify_rework:{gate}", result="rework",
              verified=False, attempt=state["rework_count"], rework_count=state["rework_count"],
              gates_failed=sorted(state.get("failures") or {})[:3],
              stuck_armed=stuck["triggered"])
    # ESC-02 — the rung the NEXT dispatch will compute, surfaced here so the
    # orchestrator can announce it. Advisory only: `begin` re-derives it from
    # the same inputs and is the single authority, so a crash between these
    # two points cannot double-climb.
    try:
        nxt = _escalation_next(plan_dir, session_id)
    except Exception as e:  # noqa: BLE001 — never let diagnostics break the rework
        # ...but never SILENTLY either: a swallowed TypeError here would make
        # the climb look declined rather than broken, and the tests would still
        # be green. Say it on stderr and carry on with the rework.
        print(f"escalation: could not derive the next rung for {session_id} ({e})",
              file=_sys.stderr)
        nxt = None
    if nxt:
        # Wording states the RUNG, never a delta. Once a session has climbed it
        # never hands the model back (a different error is progress, and progress
        # does not cost you the bigger model), so a later attempt can HOLD this
        # rung rather than rise to it — and "runs one rung UP" would then be
        # prose that is also false.
        fb.write_text(fb.read_text() + (
            f"\n## Escalation — this attempt runs ABOVE the authored tier\n\n"
            f"{_authored_sentence(nxt)} Because the same root cause "
            f"has now failed repeatedly, the standing ladder raises it to "
            f"`{nxt['ran']['model']}@{nxt['ran']['reasoning'] or 'unset'}` "
            f"(rung {nxt['rung']}) for this attempt.\n\n"
            "A stronger model is not a licence to retry the same approach harder. The "
            "research pass above rides along with this rung precisely so the diagnosis "
            "changes before the model does.\n"
        ))
    return {"action": "rework", "session": session_id, "gate": gate,
            "attempt": state["rework_count"], "max_rework": state["max_rework"],
            "feedback_file": str(fb),
            **({"escalation_next": nxt} if nxt else {}),
            **({"stuck_protocol": stuck} if stuck["triggered"] else {})}



def _halted(plan_dir, session_id, state, gate, excerpt, fb, stuck):
    """No budget left (or on_fail: halt): stop and say where the climb stopped."""
    state["outcome"] = "halted"
    _save_state(plan_dir, session_id, state)
    reason = (f"verify gate {gate} failed; on_fail={state.get('on_fail')}, "
              f"rework {state['rework_count']}/{state['max_rework']} exhausted")
    # The gate prints one `convergence:` line per review (llm_review_ledger). When
    # the LAST attempt's findings were all in code untouched since the previous
    # one, the agent did not regress -- the reviewer sampled. A halt worded as
    # "the session failed" sends the operator to redispatch; this sends them to
    # split, which is the only thing that converges.
    m = _CONVERGENCE.search(excerpt or "")
    if m and int(m.group("open")) == 0 and int(m.group("delta")) == 0 and int(m.group("outside")) > 0:
        reason += (" SURFACE TOO LARGE FOR ONE REVIEW PASS: the final attempt's findings were "
                   "all in code untouched since the previous attempt -- the reviewer's sample, "
                   "not the agent's regression. Split the session; redispatching it re-samples.")
    # ESC-02 — when the budget runs out MID-LADDER, say where the climb stopped
    # and which rungs were never tried. Without this the BLOCKED brief reads
    # "we tried and failed" when the truth is "we stopped one rung short", and
    # the operator cannot tell those apart. Silent when no climb ever armed.
    # Best-effort: a missing manifest or resolver must not swallow the halt reason.
    try:
        ladder = _escalation_status(plan_dir, session_id)
    except Exception as e:  # noqa: BLE001 — diagnostics must never mask the halt
        print(f"escalation: could not describe the ladder for {session_id} ({e})",
              file=_sys.stderr)
        ladder = ""
    if ladder:
        reason += f". {ladder}"
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    rsi.log_event(plan_dir, "verify_failed", session_ids=[session_id], gate=gate,
                  excerpt=excerpt[:200])
    # TEL-01 — rework exhausted (or on_fail: halt fired on the first failure);
    # either way there is no result enum value beyond "exhausted" for a verify
    # halt, so both causes record the same result — the halt reason string
    # (not a ledger field) is where the distinction actually lives.
    outc.write(plan_dir, session_id, resolution=f"verify_halt:{gate}", result="exhausted",
              verified=False, attempt=state["rework_count"] + 1, rework_count=state["rework_count"],
              gates_failed=sorted(state.get("failures") or {})[:3],
              stuck_armed=stuck["triggered"])
    return {"action": "halted", "session": session_id, "gate": gate, "reason": reason,
            "feedback_file": str(fb),
            **({"stuck_protocol": stuck} if stuck["triggered"] else {})}


def settle_blocked_closeout(plan_dir, session_id, closeout):
    """Close the verify cycle that a BLOCKED closeout ends.

    A rework may end with a genuine BLOCKED result. The state still carried
    `outcome=rework` from the cycle that ORDERED the rework, so plan_mutate read
    the session as work in flight (SETTLED_VERIFY = passed|halted) when it had
    already stopped. Leaves rework_count and the failure history intact; a cycle
    that already passed or halted is untouched.
    """
    path = verify_state_path(plan_dir, session_id)
    if not path.exists():
        return False
    state = json.loads(path.read_text())
    if state.get("outcome") in (GATE_PASSED, "halted"):
        return False
    state["closeout_digest"] = closeout.get("_closeout_digest")
    state["closeout_persisted_at"] = closeout.get("_persisted_at")
    state["outcome"] = "halted"
    _save_state(plan_dir, session_id, state)
    rsi.log_event(plan_dir, "verify_closed_by_blocked_closeout",
                  session_ids=[session_id], rework_count=state.get("rework_count", 0))
    return True
