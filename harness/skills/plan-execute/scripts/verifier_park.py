#!/usr/bin/env python3
"""The on-box verifier park: what happens when NO model may review this code.

TWO HALVES, one file, because both read the SAME marker and a second copy of
that constant is a contract that drifts.

GATE SIDE (runs inside the review gate's own subprocess, `llm_review_gate.py`):
`refusal()` answers "may a Claude reviewer see this code at all?". Under
`--harness codex` the family that did NOT write the code is CLAUDE, so the
cross-family reviewer IS `claude -p` — and an operator who chose the Codex
harness to keep their code away from Anthropic must not get that by surprise.
Without the SSOT opt-in the gate prints the `VERIFIER: on_box_human` marker and
exits INDETERMINATE, having reviewed nothing and written nothing. `reviewer_for()`
is the other half of that answer: WITH the opt-in it makes the reviewer Claude, so
a `--reviewer codex` gate cannot quietly become Codex reviewing Codex-built code.

ORCHESTRATOR SIDE (runs inside verify.py): `indeterminate()` splits a gate's
declared indeterminate exit into the two things it can mean, which used to be
one thing:

  * the marker is present  -> NOBODY MAY REVIEW THIS. Park the session at
    AWAITS_REVIEW with a brief naming the two dispositions a human may record
    (`VERIFIED-ON-BOX` / `BLOCKED`) and stop. Never a rework charge — the agent
    did not fail — and never a pass.
  * no marker              -> A TRANSPORT FAILURE. The reviewer could not RUN
    (idle kill, missing `-o`, non-zero exit, schema miss, reviewer absent).
    Still not the agent's failure, so still not charged, but BOUNDED: an
    unbounded no-charge retry is a loop, and `--resume` re-runs the same gate
    forever against the same broken transport. After `MAX_TRANSPORT` attempts it
    becomes a blocker that NAMES the transport cause instead of the agent.

Exit 1 — a real blocking finding — never reaches here. That one charges rework,
which is what the rework budget is for.

`resolve()` is the other end of the park, and it is the half that makes the park
honest. `ack-checkpoint` alone marks a completed closeout DONE; it does not
record a pending VERIFIER gate as satisfied, so acking a parked session would
walk straight through the verification boundary the park exists to enforce.
Resolution therefore has to record a disposition and then either finish the
verify pass (VERIFIED-ON-BOX) or stop the session (BLOCKED).
"""
import json
from pathlib import Path

#: THE FIRST `VERIFIER:` LINE IS THE DISPOSITION. Exactly one is printed per gate
#: run, always before any reviewer output is echoed (the codex backend holds its
#: markers until codex has answered precisely to keep that true). Found BY PREFIX
#: — never by string-equalling line 1, which is the reviewer IDENTITY (prose),
#: with a CLI preamble possibly between the two.
#:
#: NO LINE WINDOW BOUNDS THE SCAN, and none can: the surface preamble prints ONE
#: LINE PER UNTRACKED FILE, so it outruns any constant. Measured, 8
#: untracked files put the marker on stdout line 13, a 10-line window missed it,
#: and a real "no model may review this" went down the transport-retry path to
#: halt the plan blaming a transport failure that never happened. FIRST-match is
#: what the window was really for: text echoed later (an unparseable reviewer's
#: raw result is dumped to stdout, and this repo's own source contains the
#: marker) still cannot contradict the gate's own line.
VERIFIER = "VERIFIER:"
MARKER = f"{VERIFIER} on_box_human"
DEGRADED = "DEGRADED_FROM:"

#: Why no reviewer ran. The gate side owns this one; `codex_review_backend`
#: owns `cross_family` / `cross_family_unavailable`.
CAUSE_NO_OPT_IN = "codex_harness_no_claude_verifier"

#: How many times a TRANSPORT indeterminate may re-run free of charge before it
#: becomes a blocker. Two, matching the gate's own two attempts: a third whole
#: gate run against a reviewer that has failed to start twice is not diagnosis,
#: it is a loop, and the operator needs the transport cause named.
MAX_TRANSPORT = 2

AWAITS = "awaits_review"
VERIFIED_ON_BOX = "VERIFIED-ON-BOX"
BLOCKED = "BLOCKED"
DECISIONS = {"verified-on-box": VERIFIED_ON_BOX, "blocked": BLOCKED}

_REASONS = {
    CAUSE_NO_OPT_IN: (
        "this session was built under the Codex harness and no Claude verifier is "
        "opted in (model-routing.yaml "
        "verification.claude_verifier_under_codex_harness is absent or false)"),
    "cross_family": (
        "the working tree is RESTRICTED — sending it to Codex would be forbidden "
        "egress, so no cross-family reviewer may run"),
    "cross_family_unavailable": (
        "the cross-family reviewer is unavailable on this box (not installed, not "
        "logged in, or quota-exhausted)"),
}


# --------------------------------------------------------------------------
# Shared: reading the marker off a gate's stdout
# --------------------------------------------------------------------------
def _first(stdout, prefix):
    """The FIRST line carrying this machine-owned prefix, or None. WHOLE stdout —
    see `VERIFIER` for why a line window cannot bound this search."""
    return next((ln for ln in (stdout or "").splitlines()
                 if ln.startswith(prefix)), None)


def marker_in(stdout):
    """Did this gate say a HUMAN has to verify?

    The FIRST `VERIFIER:` line decides. `on_box_claude` / `cross_family` there
    mean a reviewer DID run, so a later line quoting the marker cannot turn one
    of those into a park — and nothing the preamble prints can push the real one
    out of range."""
    return (_first(stdout, VERIFIER) or "").startswith(MARKER)


def cause_in(stdout):
    """The `DEGRADED_FROM:` value printed with the marker, or the no-opt-in cause
    when the gate printed the marker without one. None when there is no marker at
    all: the two lines are one pair, so an orphan `DEGRADED_FROM:` is not a
    cause."""
    if not marker_in(stdout):
        return None
    return (_first(stdout, DEGRADED) or "")[len(DEGRADED):].strip() or CAUSE_NO_OPT_IN


def reason_for(cause):
    return _REASONS.get(cause, f"no reviewer could run ({cause})")


# --------------------------------------------------------------------------
# Gate side — the opt-in, read in the gate's own subprocess
# --------------------------------------------------------------------------
def _ssot_path():
    """Same resolution run.py uses: the env override first, else the SSOT that
    sits three directories above this script (`~/.claude/model-routing.yaml`
    when deployed, the repo root in a checkout)."""
    import os  # noqa: PLC0415 — one call, and the module stays import-light

    return Path(os.environ.get("PLAN_EXECUTE_ROUTING_SSOT")
                or Path(__file__).resolve().parents[3] / "model-routing.yaml")


def claude_verifier_opted_in(path=None):
    """Is `verification.claude_verifier_under_codex_harness` TRUE in the SSOT?

    Absent, false, or an unreadable SSOT all mean NO — fail closed. This grants
    an Anthropic model sight of code an operator deliberately routed to Codex,
    so "I could not tell" must never resolve to yes.

    A LINE MATCH, not a YAML parse: this runs in the gate subprocess, which has
    no PyYAML guarantee and no business loading a 2000-line SSOT to read one
    bool. The key is unique in the file (the routing guard keeps it so)."""
    import re  # noqa: PLC0415

    try:
        text = Path(path or _ssot_path()).read_text(encoding="utf-8")
    except OSError:
        return False
    m = re.search(r"^\s*claude_verifier_under_codex_harness:\s*(\S+)", text, re.M)
    return bool(m) and m.group(1).strip().strip('"\'').lower() == "true"


def reviewer_for(harness, reviewer):
    """WHICH FAMILY REVIEWS, once the harness is known. Call BEFORE the identity
    line — line 1 must name the family that actually runs.

    `refusal()` answers only "may this run proceed?"; it never swapped the
    reviewer, so a `cross-family-review-*` gate on a CODEX-built session kept the
    registry's `--reviewer codex` and — with the opt-in ON — reviewed Codex-built
    code with Codex, then reported a cross-family PASS. Measured:
    1 codex invocation, 0 claude, action=passed. That is worse than the gate not
    running, because it is a false assurance.

    Under the codex harness the family that did NOT write the code is CLAUDE, so
    with the opt-in the cross-family reviewer IS `claude -p`; without it
    `refusal()` parks and nothing runs at all. The `claude` harness is untouched:
    `--reviewer codex` there is already cross-family and stays codex.
    """
    return "claude" if harness == "codex" and claude_verifier_opted_in() else reviewer


def refusal(harness, level, out=None):
    """The gate's harness check. `None` to proceed, or the exit code to return.

    Called AFTER the identity line so line 1 stays the reviewer identity, and
    BEFORE the surface is prepared so a refused run writes nothing to the ledger
    and leaves no half-review behind.
    """
    import sys  # noqa: PLC0415

    if harness != "codex" or claude_verifier_opted_in():
        return None
    w = out or sys.stdout
    print(MARKER, file=w)
    print(f"{DEGRADED} {CAUSE_NO_OPT_IN}", file=w)
    print(f"[llm-review-gate] level={level} verdict=INDETERMINATE "
          f"reviewer=none reason=no-claude-verifier-under-codex-harness", file=w)
    print("This session was built under the Codex harness, so the family that did "
          "NOT write this code is CLAUDE — and running the on-box `claude -p` "
          "reviewer would send it to Anthropic. That is opt-in only: set "
          "`verification.claude_verifier_under_codex_harness: true` in "
          "model-routing.yaml to allow it. Until then this is an on-box HUMAN "
          "verification: review the diff yourself and record VERIFIED-ON-BOX, or "
          "record BLOCKED.", file=w)
    return 2


# --------------------------------------------------------------------------
# Orchestrator side — the split, the park, and the transport bound
# --------------------------------------------------------------------------
def indeterminate(plan_dir, session_id, state, gate, excerpt, res):
    """Route a gate that exited its declared indeterminate exit.

    `res["stdout"]` and NOT `excerpt`: the excerpt is stderr+stdout concatenated
    and truncated to `GATE_EXCERPT_MAX`, so a "first 10 lines of stdout" scan
    over it reads the wrong stream from the wrong end."""
    stdout = (res or {}).get("stdout") or ""
    if marker_in(stdout):
        return park(plan_dir, session_id, state, gate, cause_in(stdout), excerpt)
    return _transport(plan_dir, session_id, state, gate, excerpt)


def _transport(plan_dir, session_id, state, gate, excerpt):
    """The reviewer could not RUN. Not charged — but bounded."""
    from rework import _indeterminate  # noqa: PLC0415 — verify.py imports both

    counts = state.setdefault("transport_indeterminate", {})
    counts[gate] = int(counts.get(gate, 0)) + 1
    if counts[gate] > MAX_TRANSPORT:
        return _transport_exhausted(plan_dir, session_id, state, gate, excerpt)
    # `_indeterminate` persists the state (counter included), leaves the gate
    # PENDING, and leaves `rework_count` alone. Only the bound is new.
    out = _indeterminate(plan_dir, session_id, state, gate, excerpt)
    out["transport_attempt"] = counts[gate]
    out["transport_budget"] = MAX_TRANSPORT
    return out


def _transport_exhausted(plan_dir, session_id, state, gate, excerpt):
    """The bound is spent. Blame the TRANSPORT, by name, not the agent."""
    import article_block as ab  # noqa: PLC0415
    import run_state_io as rsi  # noqa: PLC0415
    from verify_paths import _html, _save_state  # noqa: PLC0415

    state["outcome"] = "blocked"
    _save_state(plan_dir, session_id, state)
    reason = (f"verify gate {gate} could not RUN {MAX_TRANSPORT + 1} times "
              f"(transport failure, never a finding): the reviewer never started or "
              f"never answered, so nothing was reviewed and no rework was charged "
              f"(rework {state['rework_count']}/{state['max_rework']} untouched). "
              f"Fix the transport — the cause is in the gate output below — then "
              f"`clear-halt` and `verify-begin --session {session_id} --resume`.")
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    rsi.log_event(plan_dir, "verify_transport_exhausted", session_ids=[session_id],
                  gate=gate, attempts=MAX_TRANSPORT + 1,
                  rework_count=state["rework_count"], excerpt=excerpt[:200])
    return {"action": "halted", "session": session_id, "gate": gate, "reason": reason,
            "transport": True, "rework_count": state["rework_count"]}


def _brief(session_id, gate, cause, prior=None):
    return (
        f"# On-box verification required — {session_id}\n\n"
        f"Gate `{gate}` reviewed NOTHING and is not a pass. Reason: "
        f"{reason_for(cause)} (`DEGRADED_FROM: {cause}`).\n\n"
        f"This is NOT a rework: no attempt was charged, no model escalation "
        f"armed, and the agent's work is not in question. The verification "
        f"BOUNDARY is — no model may read this code here, so a human must.\n\n"
        f"## Your decision — one of exactly two\n\n"
        f"* `VERIFIED-ON-BOX` — you reviewed the diff yourself and it is sound.\n"
        f"  `run.py ack-checkpoint <plan-dir> --session {session_id} "
        f"--decision verified-on-box [--note '…']`\n"
        f"  Records the disposition, clears the pending verifier gate, finishes "
        f"the verify pass, and lets this session's deferred shipping proceed.\n"
        f"* `BLOCKED` — it is not sound, or you will not review it.\n"
        f"  `run.py ack-checkpoint <plan-dir> --session {session_id} "
        f"--decision blocked [--note '…']`\n"
        f"  Records the disposition and stops the session. Nothing ships.\n"
        + ("" if not prior else
           f"\n## This session ALSO asked for a human — and that ask still stands\n\n"
           f"> {prior}\n\n"
           f"It is a different question by a different author, so resolving the "
           f"park does not answer it: `VERIFIED-ON-BOX` puts this text back on the "
           f"closeout and shipping stays DEFERRED until a plain `ack-checkpoint "
           f"<plan-dir> --session {session_id}` answers it.\n")
    )


def park(plan_dir, session_id, state, gate, cause, excerpt):
    """No model may review this code: park at AWAITS_REVIEW for a human."""
    import article_block as ab  # noqa: PLC0415
    import run_state_io as rsi  # noqa: PLC0415
    from verify_paths import _feedback_path, _html, _now, _save_state  # noqa: PLC0415

    reason = reason_for(cause)
    state["gate_status"][gate] = AWAITS
    state["on_box_review"] = {"gate": gate, "cause": cause, "reason": reason,
                              "parked_at": _now()}
    state["human_checkpoint_reason"] = reason
    # THE SUBAGENT MAY HAVE ASKED FOR A HUMAN OF ITS OWN — a DIFFERENT AUTHOR'S
    # claim on this one field. The park overwrote it and `verify_finalize` then
    # cleared it to None, so the ask was gone from disk with no record and the
    # session reached DONE with shipping proceeding (measured against a
    # control pair). Neither claim may destroy the other: what the park displaces
    # is stashed here, shown in the brief, and put back by `unpark_closeout`.
    prior = closeout_checkpoint(plan_dir, session_id)
    if prior:
        state["on_box_prior_checkpoint"] = prior
    _save_state(plan_dir, session_id, state)
    # THE CLOSEOUT, not just the verify state: `shipping.checkpoint_pending`
    # reads the closeout's `human_checkpoint_reason`, and it is what makes
    # shipping DEFER while the park stands. `closeout_digest` deliberately
    # excludes this field, so writing it cannot read as state-drift.
    # One field, two authors — both visible, neither silently replaced.
    set_closeout_checkpoint(plan_dir, session_id, reason if not prior else (
        f"{reason}\n\nALSO STANDING — this session's own checkpoint: {prior}"))
    fb = _feedback_path(plan_dir, session_id)
    fb.parent.mkdir(parents=True, exist_ok=True)
    fb.write_text(_brief(session_id, gate, cause, prior)
                  + f"\n## Gate output (redacted, tail)\n\n```\n{excerpt}\n```\n")
    ab.apply_mutation(_html(plan_dir), session_id, status="AWAITS_REVIEW",
                      note=f"on-box verification required: {reason}")
    rsi.log_event(plan_dir, "verifier_park", session_ids=[session_id], gate=gate,
                  cause=cause, rework_count=state["rework_count"])
    rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[session_id], reason=reason)
    return {"action": "awaits-review", "session": session_id, "gate": gate,
            "cause": cause, "human_checkpoint_reason": reason,
            "rework_count": state["rework_count"], "feedback_file": str(fb),
            "decisions": sorted(DECISIONS)}


# --------------------------------------------------------------------------
# Resolution — the half without which the park is a dead end
# --------------------------------------------------------------------------
def _closeout(plan_dir, session_id):
    """(path, record) for this session's closeout; record is None when unreadable."""
    path = Path(plan_dir) / "_closeouts" / f"{session_id}.json"
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return path, None
    return path, rec if isinstance(rec, dict) else None


def closeout_checkpoint(plan_dir, session_id):
    """The checkpoint the SESSION ITSELF asked for, or None. Read-only."""
    return (_closeout(plan_dir, session_id)[1] or {}).get("human_checkpoint_reason")


def set_closeout_checkpoint(plan_dir, session_id, reason):
    """Write (or clear) the closeout's `human_checkpoint_reason` IN PLACE.

    `_persisted_at` and `_closeout_digest` are preserved untouched: this is not a
    new closeout, and re-digesting one would look like a session that re-ran."""
    path, rec = _closeout(plan_dir, session_id)
    if rec is None:
        return False
    rec["human_checkpoint_reason"] = reason
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
    return True


def unpark_closeout(plan_dir, session_id, state):
    """Undo the PARK's write to the closeout — RESTORING, never blank-clearing.

    Called from `verify.verify_finalize`, the one place that knows verification is
    COMPLETE (`resolve` doing it un-deferred shipping while a LATER gate was still
    pending). What goes back is what the park displaced: None when it wrote onto
    an empty field, the session's OWN checkpoint text when it did not — which
    leaves shipping deferred until a human answers THAT one."""
    prior = state.pop("on_box_prior_checkpoint", None)
    if state.pop("on_box_resolved", None) != VERIFIED_ON_BOX:
        return False
    return set_closeout_checkpoint(plan_dir, session_id, prior)


def pending_park(plan_dir, session_id):
    """This session's open on-box park, or None. Read-only."""
    return (_verify_state(plan_dir, session_id)).get("on_box_review") or None


def pending_gate(plan_dir, session_id):
    """The verify gate this session still OWES, or None. Read-only.

    `pending_park` refuses a rubber-stamp ack only while the park is open. Once
    `resolve` records a disposition the park is gone — and if that park was the
    first of several gates, the session is at AWAITS_REVIEW with verification
    still incomplete and nothing left to refuse the ack. This is that guard:
    `first_pending` is the same predicate `verify._advance` steers on, so "a gate
    is still owed" means exactly what it means to the verify pass itself.
    """
    from verify_pass import first_pending  # noqa: PLC0415

    state = _verify_state(plan_dir, session_id)
    if not state.get("gates") or not isinstance(state.get("gate_status"), dict):
        return None
    return first_pending(state)


def _verify_state(plan_dir, session_id):
    """This session's verify state as a dict — {} when there is none."""
    import ship_state_io as ssio  # noqa: PLC0415
    from verify_paths import verify_state_path  # noqa: PLC0415

    state = ssio.read_json_with_bak(verify_state_path(plan_dir, session_id))
    return state if isinstance(state, dict) else {}


def resolve(plan_dir, session_id, decision, note=None):
    """Record the human's disposition and either finish or stop the session."""
    import outcomes as outc  # noqa: PLC0415
    import run_state_io as rsi  # noqa: PLC0415
    import ship_state_io as ssio  # noqa: PLC0415
    from verify_paths import _save_state, verify_state_path  # noqa: PLC0415

    key = (decision or "").strip().lower()
    if key not in DECISIONS:
        return {"action": "error", "session": session_id,
                "message": f"unknown decision {decision!r}; use {' or '.join(sorted(DECISIONS))}"}
    state = ssio.read_json_with_bak(verify_state_path(plan_dir, session_id))
    parked = (state or {}).get("on_box_review")
    if not parked:
        return {"action": "error", "session": session_id,
                "message": f"{session_id} has no open on-box verifier park to resolve"}

    disposition = DECISIONS[key]
    verified = disposition == VERIFIED_ON_BOX
    gate = parked["gate"]
    _record(plan_dir, session_id, parked, disposition, note)
    rsi.log_event(plan_dir, "verifier_disposition", session_ids=[session_id],
                  gate=gate, cause=parked.get("cause"),
                  disposition=disposition, note=note)
    outc.write(plan_dir, session_id, resolution=f"verifier_on_box:{gate}",
               result="passed" if verified else "blocked", verified=verified,
               attempt=state["rework_count"] + 1, rework_count=state["rework_count"])

    state.pop("on_box_review", None)
    if not verified:
        return _resolve_blocked(plan_dir, session_id, state, gate, disposition, note)

    # The gate is SATISFIED — by a human, recorded as such — so the verify pass
    # continues exactly where it stopped. `_advance` finalizes when this was the
    # last gate and runs the next one when it was not.
    import verify as vfy  # noqa: PLC0415 — verify imports this module

    state["gate_status"][gate] = vfy.GATE_PASSED
    # RESTORED, not cleared: `_final_status_and_note` steers on this field, so
    # blanking it marked a session with its OWN pending checkpoint DONE.
    state["human_checkpoint_reason"] = state.get("on_box_prior_checkpoint")
    # Clearing the closeout's checkpoint is what un-defers shipping
    # (`shipping.checkpoint_pending`), so it may NOT happen here: a session whose
    # park was the FIRST of several gates still owes the rest, and clearing it
    # here left a plain `ack-checkpoint` free to mark the session DONE with a
    # later gate never run (measured on a two-gate session). This
    # marker is the standing instruction; `verify.verify_finalize` — the one
    # place that knows verification is COMPLETE — is where it is honoured.
    state["on_box_resolved"] = disposition
    _save_state(plan_dir, session_id, state)
    out = vfy._advance(plan_dir, session_id,
                       vfy.compute_gates(plan_dir, _manifest(plan_dir), session_id),
                       state, dry_run=False)
    if out.get("action") == "passed":
        out = vfy.verify_finalize(plan_dir, session_id)
    out["verifier_disposition"] = disposition
    return out


def _manifest(plan_dir):
    import manifest_io as mio  # noqa: PLC0415

    return mio.load_manifest(plan_dir)


def _resolve_blocked(plan_dir, session_id, state, gate, disposition, note):
    import article_block as ab  # noqa: PLC0415
    import run_state_io as rsi  # noqa: PLC0415
    from verify_paths import _html, _save_state  # noqa: PLC0415

    # `halted`, not `blocked`: a BLOCKED disposition is final, and plan_mutate
    # treats only passed|halted as settled -- `blocked` refused every retire.
    state["outcome"] = "halted"
    state["gate_status"][gate] = "blocked"
    _save_state(plan_dir, session_id, state)
    reason = (f"on-box verification recorded {disposition} for gate {gate}"
              + (f": {note}" if note else "") + ". Nothing ships.")
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    return {"action": "blocked", "session": session_id, "gate": gate,
            "verifier_disposition": disposition, "reason": reason,
            "rework_count": state["rework_count"]}


def _record(plan_dir, session_id, parked, disposition, note):
    """The disposition, in the closeout, beside the work it judges."""
    from verify_paths import _now  # noqa: PLC0415

    path, rec = _closeout(plan_dir, session_id)
    if rec is None:
        return
    rec.setdefault("verifier_dispositions", []).append(
        {"gate": parked["gate"], "cause": parked.get("cause"),
         "disposition": disposition, "note": note, "recorded_at": _now()})
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
