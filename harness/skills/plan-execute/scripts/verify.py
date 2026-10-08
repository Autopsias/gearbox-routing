"""Session verification gates — run automated checks before a DONE closeout is
*really* DONE (the "don't ship trash" boundary).

A plan built by ``/plan-builder`` may declare, per session (or phase), a
``verify`` block: an ordered list of gate ids (tests / ``/eval --smoke`` /
type-check / a reviewer) that MUST pass before the orchestrator finalizes the
session as DONE. This runs at the ``apply`` boundary, AFTER the closeout is
validated and item statuses are applied, but BEFORE shipping — so a self-reported
DONE that fails its tests never advances or ships.

State machine (mirrors ``shipping.py``, deliberately leaner):

  * Gates are resolved through the SAME ``eval-gates`` registry as shipping's
    ``pre_deploy_gates`` (``shipping.resolve_gate``) — skill-kind (orchestrator
    invokes) or argv-kind (this helper runs, ``shell=False``, allow-listed env).
  * Gates run sequentially within one session. No cross-session resource locks:
    verify gates are read-only checks. A gate that is NOT safe to run
    concurrently must not be placed on a SHARED-TREE ``parallel_group`` member —
    under ``dispatch.isolation: "worktree"`` a member's gates run inside that
    member's own worktree (``gate_cwd``), which resolves the common case. That
    is contract M4, and M4 is NOT machine-enforced: nothing here can read a gate
    script's intent.
  * On all-pass → ``verify-finalize`` flips the session DOING→DONE (or
    →AWAITS_REVIEW when the closeout also asked for a human checkpoint — verify
    runs FIRST, so we never spend human attention on work that fails its gates).
  * On a gate FAIL:
      - ``on_fail: rework`` (default) and rework budget remains → session →
        PARTIAL, a redacted feedback file is written, and the loop re-dispatches
        the session with that feedback appended. Bounded by ``max_rework``.
      - ``on_fail: halt`` OR rework budget exhausted → session → BLOCKED + halt.

Durability + idempotency reuse ``ship_state_io`` (durable JSON writer with
``.bak`` + fsync, validate-on-read) and bind to the manifest digest, so a rebuilt
plan refuses stale verify state (``state-drift``). The ``rework_count`` survives
across re-dispatches (each rework produces a NEW closeout, hence a new
``closeout_digest``) so ``max_rework`` is enforced across attempts, not reset.

The session stays in ``DOING`` for the whole dispatch→verify cycle — no new
dashboard status, so the load-bearing PLAN.html nav JS is untouched. A session
left ``DOING`` with a persisted ``_closeouts/<sid>.json`` and a pending
``_verify_state/<sid>.json`` is the crash-recovery signal: re-run with
``verify-begin --resume``.
"""

from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import evidence_proof as evp
import gate_expect as gx
import gate_policy as gp
import locked_checks as lc
import manifest_io as mio
import outcomes as outc
import plan_scope as pscope
import render_verify as rv
import replan as rp
import run_state_io as rsi
import ship_state_io as ssio
import shipping as shp
import shipping_adapter as adapter
import structural_gate as sg
import stuck_protocol as sp
import verifier_park as vpk
from rework import _gate_failed
from verify_pass import GATE_PASSED, first_pending as _first_pending  # noqa: F401 — re-exported
from verify_pass import (gate_by_name as _gate_by_name, hand_pass_refusal,
                         requires_resume as _requires_resume,
                         reviews_first as _reviews_first, run_argv, stale_closeout as _stale_closeout)
from verify_paths import _html, _now, _save_state, verify_state_path

GATE_PENDING = "pending"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------
def _resolved_verify(manifest, session_id):
    s = mio.session_by_id(manifest).get(session_id)
    return (s.get("verify") if s else None) or None


def compute_gates(plan_dir, manifest, session_id):
    """Ordered gate step descriptors for a session's resolved ``verify`` block.

    Reuses ``shipping.resolve_gate`` so a verify gate and a ``pre_deploy_gate``
    with the same id resolve identically. Raises ``shipping.StepResolveError``
    (unknown gate id; claimed-but-vanished plan worktree) to halt precisely."""
    vb = _resolved_verify(manifest, session_id)
    if not vb:
        return []
    return [shp.resolve_gate(plan_dir, gid, session_id) for gid in (vb.get("gates") or [])]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def _load_state(plan_dir, session_id):
    return ssio.read_json_with_bak(verify_state_path(plan_dir, session_id))


def _new_state(plan_dir, manifest, session_id, gates, vb):
    co = cp.load_closeout(plan_dir, session_id)
    return {
        "session_id": session_id,
        "manifest_digest": mio.manifest_digest(plan_dir),
        "closeout_digest": co.get("_closeout_digest") if co else None,
        # Which apply this pass verifies (verify_pass.stale_closeout).
        "closeout_persisted_at": co.get("_persisted_at") if co else None,
        "gates": [g["name"] for g in gates],
        "on_fail": vb.get("on_fail", "rework"),
        "max_rework": int(vb.get("max_rework", 1)),
        "rework_count": 0,
        "human_checkpoint_reason": co.get("human_checkpoint_reason") if co else None,
        "require_evidence": bool(vb.get("require_evidence")) if vb else False,
        "gate_status": {g["name"]: GATE_PENDING for g in gates},
        "outcome": None,
        "started_at": _now(),
        "updated_at": _now(),
        "failures": {},
    }


# `gate_cwd` lives in plan_scope beside `plan_cwd`, whose answer it refines
# (size-ratchet extraction, 2026-08-22). Re-exported: callers import it here.
gate_cwd = pscope.gate_cwd


# --------------------------------------------------------------------------
# verify-begin
# --------------------------------------------------------------------------
def verify_begin(plan_dir, session_id, *, dry_run=False, resume=False):
    manifest = mio.load_manifest(plan_dir)
    gates = compute_gates(plan_dir, manifest, session_id)
    vb0 = _resolved_verify(manifest, session_id)
    require_ev = bool(vb0 and vb0.get("require_evidence"))
    # A verify block with no gates but require_evidence STILL runs — the evidence
    # assertion fires at verify-finalize. Only a truly empty block is a noop.
    if not gates and not require_ev and not lc.locked_paths(manifest, session_id):
        return {"action": "noop", "session": session_id}

    co = cp.load_closeout(plan_dir, session_id)
    result = co.get("result") if co else None
    # Only a claimed-complete session is verified. PARTIAL/BLOCKED never reach
    # here from a healthy apply, but guard anyway.
    if result != "DONE":
        return {"action": "noop", "session": session_id, "reason": f"result={result}"}

    state = _load_state(plan_dir, session_id)
    if state is not None:
        # State-drift: a rebuilt manifest invalidates prior verify state.
        if state.get("manifest_digest") != mio.manifest_digest(plan_dir):
            return _fail(plan_dir, session_id, "state-drift",
                         "manifest changed since verify state was written; "
                         f"delete {verify_state_path(plan_dir, session_id)} to re-verify",
                         dry_run)
        if state.get("outcome") == GATE_PASSED:
            return {"action": "already-verified", "session": session_id}
        elif state.get("outcome") == "rework":
            # A prior attempt asked for rework; this is the fresh closeout's pass —
            # and it MUST be a fresh closeout (verify_pass.stale_closeout).
            stale = _stale_closeout(session_id, state, co)
            if stale:
                return stale
            new_digest = co.get("_closeout_digest") if co else None
            state["closeout_digest"] = new_digest
            state["closeout_persisted_at"] = co.get("_persisted_at") if co else None
            state["human_checkpoint_reason"] = co.get("human_checkpoint_reason") if co else None
            gates = _reviews_first(gates)
            state["gate_status"] = {g["name"]: GATE_PENDING for g in gates}
            state["gates"] = [g["name"] for g in gates]
            state["outcome"] = None
            _save_state(plan_dir, session_id, state)
        elif not resume:
            return _requires_resume(session_id, state)  # RT-04
    else:
        vb = _resolved_verify(manifest, session_id)
        state = _new_state(plan_dir, manifest, session_id, gates, vb)
        _save_state(plan_dir, session_id, state)
        rsi.log_event(plan_dir, "verify_started", session_ids=[session_id],
                      gates=state["gates"], attempt=state["rework_count"] + 1)
    return (lc.refusal(plan_dir, manifest, session_id, state, dry_run)  # §5: before any gate
            or _advance(plan_dir, session_id, gates, state, dry_run))


def _advance(plan_dir, session_id, gates, state, dry_run):
    name = _first_pending(state)
    if name is None:
        state["outcome"] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return {"action": "passed", "session": session_id}
    g = _gate_by_name(gates, name)
    if g["kind"] == "skill":
        out = {"action": "invoke-skill", "session": session_id, "gate": name,
               "skill": g["skill"], "args": g.get("args", ""), "step": name,
               "success_criteria": g.get("success_criteria")}
        # An isolated member's gate must look at THAT MEMBER's worktree, not the
        # shared tree its peers are still editing (contract M4). The orchestrator
        # invokes a skill-kind gate itself, so the location has to travel to it.
        wt_cwd = gate_cwd(plan_dir, session_id, g)
        if wt_cwd and wt_cwd != g.get("cwd"):
            out["cwd"] = wt_cwd
        return out
    # argv-kind gate: this helper runs it.
    if dry_run:
        return _run_gate(plan_dir, session_id, name, dry_run=True)
    return {"action": "run-argv", "session": session_id, "gate": name, "step": name}


# --------------------------------------------------------------------------
# verify-record (skill-kind outcome) / verify-run-argv (argv-kind)
# --------------------------------------------------------------------------
# A gate's failure output is not stderr — it IS the finding list, and the whole
# list is what the rework attempt has to act on. `adapter.redact`'s default 800
# chars is sized for a shipping stderr tail; applied here it kept only the LAST
# findings and silently dropped the first ones. That truncation destroyed real
# review findings twice (s01 2026-08-14, s03 2026-08-14) — in both cases the lost
# text was unrecoverable, because the raw gate output is not persisted anywhere
# else. Still bounded (redaction and a cap both still apply), just wide enough to
# hold a full findings array.
GATE_EXCERPT_MAX = 8000


def verify_record(plan_dir, session_id, gate, status, result_file=None):
    state = _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    if refusal := hand_pass_refusal(session_id, state, gates, gate):
        return refusal
    try:
        excerpt = adapter.redact(Path(result_file).read_text(), max_len=GATE_EXCERPT_MAX) if result_file else ""
    except OSError:
        excerpt = ""
    if status == "done":
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run=False)
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def _run_gate(plan_dir, session_id, gate, *, dry_run=False):
    state = _load_state(plan_dir, session_id)
    if state.get("outcome") == "rework":   # the pass is closed; verify-begin opens the next
        return _stale_closeout(session_id, state, None, strict=True)
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    g = _gate_by_name(gates, gate)
    if g is None or g["kind"] != "argv":   # a bare name is a MISS, not a kind clash
        why = (f"is a {g['kind']}-kind gate, not argv" if g else
               "names no gate -- known: " + (", ".join(x["name"] for x in gates) or "(none)"))
        return {"action": "error", "message": f"{session_id} {gate!r} {why}"}
    fake = g.get("fixture_fake")
    res = run_argv(plan_dir, session_id, g, dry_run)
    # `ssio.argv_outcome` (shared with the land re-gate): a TIMEOUT or exec error
    # (`returncode` None) means the gate never ANSWERED, and scoring it `fail`
    # charged a timed-out review as a real rework failure (measured).
    outcome = ssio.argv_outcome(res, g.get("indeterminate_exit"))
    # EXPECT is a claim-check, meaningful only for a gate that ANSWERED: a timed-out
    # run has no output to search, so `gate_miss` would turn "never answered" into a
    # hard fail, the charge the indeterminate path exists to prevent.
    miss = (None if outcome == "indeterminate"
            else gx.gate_miss(g, res, skip=dry_run and not fake))
    if outcome == "pass" and miss is None:
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run)
    excerpt = adapter.redact((res.get("stderr") or "") + (res.get("stdout") or ""),
                             max_len=GATE_EXCERPT_MAX)
    if miss is not None:   # ran and decided, so never INDETERMINATE; say WHY or a rework
        return _gate_failed(plan_dir, session_id, state, gate,   # reads only the happy output
                            gx.expect_excerpt(gate, miss, excerpt))
    if outcome == "indeterminate":   # could not decide: never charged, two flavours
        return vpk.indeterminate(plan_dir, session_id, state, gate, excerpt, res)
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def verify_run_argv(plan_dir, session_id, gate):
    return _run_gate(plan_dir, session_id, gate, dry_run=False)


# --------------------------------------------------------------------------
# Evidence gate (Vista ③) — structural "mechanism engagement" assertion
# --------------------------------------------------------------------------

def _check_evidence(plan_dir, session_id):
    """A ``require_evidence`` session must carry an ``evidence`` list in its
    closeout whose every path exists and is non-empty. This converts the
    "verify the fix actually engaged" discipline (grep the log; if count==0 it's
    a no-op) from remembered vigilance into a refusal-to-finalize-without-proof.

    Relative paths resolve against the PLAN DIRECTORY first (the convention the
    session prompts declare — ``_evidence/<sid>/…``), falling back to ``cwd``
    (project-root-relative paths like ``docs/operations/…``).

    LND-03: existence is not proof — the artifact an earlier run wrote is already
    at the declared path, whether because a worktree is a checkout of a PINNED
    BASE that contains it or because the file is simply still in the shared tree.
    ``evidence_proof.artifact_problem`` therefore also asks whether the artifact
    changed since the commit this session was dispatched at, and REFUSES (never
    grants) when it cannot tell. Returns ``(ok, excerpt)``."""
    co = cp.load_closeout(plan_dir, session_id)
    ev = (co or {}).get("evidence")
    if not isinstance(ev, list) or not ev:
        return False, (
            "require_evidence is set for this session but its closeout carried no "
            "`evidence` array. Add an `evidence` list of paths (eval JSON, log-grep "
            "output, a screenshot, a command transcript) that PROVE the work engaged "
            "— a count==0 grep is a no-op no matter what the metrics say — then "
            "return DONE again."
        )
    problems = []
    for p in ev:
        why = evp.artifact_problem(plan_dir, session_id, p)
        if why:
            label = p if isinstance(p, str) and p.strip() else repr(p)
            problems.append(f"{label} ({why})")
    if problems:
        return False, ("Declared evidence is not usable proof that THIS run engaged (it must exist, be "
                       "non-empty, and have changed since the commit this session started "
                       "from):\n  - "
                       + "\n  - ".join(problems))
    return True, ""


def _check_named_checks(plan_dir, session_id):
    """Assert the manifest's ``verify.checks`` artifact contracts.

    Each ``checks[]`` entry names an ``evidence_path`` the session MUST have
    produced. plan-builder's docs promise "the runner asserts each path exists
    and is non-empty" — before 2026-08-07 nothing did (the checks were rendered
    into prompts but never executed; found by an adversarial review of a live
    plan). The ``assert`` field stays a human-readable semantic contract; this
    function enforces the structural half: existence + non-emptiness, with the
    same plan-dir-then-cwd resolution ``_check_evidence`` uses. Runs whenever
    ``checks`` is non-empty — independent of ``require_evidence`` (the schema
    allows checks to stand alone). Returns ``(ok, excerpt)``."""
    try:
        vb = _resolved_verify(mio.load_manifest(plan_dir), session_id)
    except Exception:  # noqa: BLE001 — no manifest → nothing to assert
        return True, ""
    checks = (vb or {}).get("checks") or []
    problems = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        p = c.get("evidence_path")
        name = c.get("name") or "<unnamed>"
        # require_fresh=False: this contract is "the file must show X", and it
        # legitimately names a repo document the session only READS. Asking it to
        # have changed this run would fail such a check unfixably and burn a
        # rework attempt — the closeout's `evidence` array is the surface that
        # claims proof-of-engagement, and it is the one that asks for freshness.
        why = evp.artifact_problem(plan_dir, session_id, p, require_fresh=False)
        if not why:
            continue
        if not isinstance(p, str) or not p.strip():
            problems.append(f"check '{name}': evidence_path missing/empty in manifest")
            continue
        problems.append(f"check '{name}': {p} ({why})"
                        + (f" — must show: {c.get('assert')}" if c.get("assert") else ""))
    if problems:
        return False, ("Named evidence-artifact contracts (verify.checks) failed:\n  - "
                       + "\n  - ".join(problems))
    return True, ""


# --------------------------------------------------------------------------
# verify-finalize
# --------------------------------------------------------------------------
def _evidence_refusal(plan_dir, session_id, state, skip_evidence):
    """A refusal dict when the proof artifacts are not on disk, else None."""
    # Evidence assertion runs LAST — after every gate has passed — so a session is
    # finalized only when its proof artifacts are actually on disk. A miss reworks
    # or halts exactly like a failed gate (synthetic gate name ``evidence``).
    if state.get("require_evidence") and not skip_evidence:
        ok, excerpt = _check_evidence(plan_dir, session_id)
        if not ok:
            return _gate_failed(plan_dir, session_id, state, "evidence", excerpt)

    # Named artifact contracts (verify.checks) are asserted independently of
    # require_evidence — a standalone checks block is valid per the schema and
    # was silently inert before 2026-08-07.
    if not skip_evidence:
        ok, excerpt = _check_named_checks(plan_dir, session_id)
        if not ok:
            return _gate_failed(plan_dir, session_id, state, "evidence", excerpt)
    return None


def _final_status_and_note(plan_dir, session_id, state):
    """(final_status, note, hc, hc_auto_continue) for a session that just passed."""
    hc = state.get("human_checkpoint_reason")
    # OR-03: apply the same per-gate notify-and-continue policy the run.cmd_apply
    # (non-verify) path applies to the AWAITS_REVIEW-ack gate. A rubber-stamp gate
    # on an opted-in session auto-continues to DONE with a notification + a
    # gate_auto_continue event instead of parking in AWAITS_REVIEW. Fail-closed:
    # requires_human_checkpoint / irreversible / non-allowlisted → still blocks.
    hc_dispatch = None
    try:
        s = mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id)
        hc_dispatch = s.get("dispatch") if s else None
    except Exception:  # noqa: BLE001 — resolve conservatively (→ block) on any error
        hc_dispatch = None
    hc_auto_continue = bool(hc) and gp.is_notify_and_continue("session_review_ack", hc_dispatch)
    if hc and hc_auto_continue:
        final_status = "DONE"
        note = "verified; session complete (gate auto-continued — notify-and-continue)"
    elif hc:
        final_status = "AWAITS_REVIEW"
        note = f"verified; checkpoint: {hc}"
    else:
        final_status = "DONE"
        note = "verified; session complete"
    return final_status, note, hc, hc_auto_continue


def _passed_result(plan_dir, session_id, hc, hc_auto_continue, gate, render, replan):
    """Log the pass and return the closeout dict for whichever branch applies."""
    if hc and hc_auto_continue:
        rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="DONE")
        rsi.log_event(
            plan_dir, "gate_auto_continue", session_ids=[session_id],
            gate_type="session_review_ack", reason=hc, policy="notify-and-continue",
        )
        notify = rsi.notify_gate_continue(plan_dir, session_id, "session_review_ack", hc)
        rsi.log_event(
            plan_dir, "gate_notify", session_ids=[session_id],
            gate_type="session_review_ack", notify=notify.get("action"),
        )
        return {"action": "done", "session": session_id, "final_status": "DONE",
                "gate_auto_continue": True, "structural_gate": gate, "render_verify": render,
                **({"replan": replan} if replan else {})}
    if hc:
        rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="AWAITS_REVIEW")
        rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[session_id], reason=hc)
        return {"action": "done", "session": session_id, "final_status": "AWAITS_REVIEW",
                "human_checkpoint_reason": hc, "structural_gate": gate, "render_verify": render}
    rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="DONE")
    return {"action": "done", "session": session_id, "final_status": "DONE",
            "structural_gate": gate, "render_verify": render,
            **({"replan": replan} if replan else {})}


# --------------------------------------------------------------------------
# helpers / status / simulate
# --------------------------------------------------------------------------
def _fail(plan_dir, session_id, reason, message, dry_run):
    if not dry_run:
        ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=f"verify {reason}")
        rsi.set_halt(plan_dir, f"{session_id}: verify {reason} — {message}", session_id)
        rsi.log_event(plan_dir, "verify_failed", session_ids=[session_id], reason=reason)
    return {"action": "failed", "session": session_id, "reason": reason, "message": message}


def verify_status(plan_dir, session_id):
    manifest = mio.load_manifest(plan_dir)
    gates = [g["name"] for g in compute_gates(plan_dir, manifest, session_id)]
    state = _load_state(plan_dir, session_id)
    return {"session": session_id, "gates": gates,
            "state": state and {k: state[k] for k in
                                ("gate_status", "outcome", "rework_count", "max_rework",
                                 "require_evidence")
                                if k in state}}


def verify_simulate(plan_dir, session_id):
    """CI / smoke: run the full verify pipeline, auto-passing every gate (no real
    skill / real command). Produces real state + events."""
    out = verify_begin(plan_dir, session_id, dry_run=True)
    guard = 0
    while out.get("action") in ("invoke-skill", "run-argv") and guard < 50:
        guard += 1
        if out["action"] == "invoke-skill":
            state = _load_state(plan_dir, session_id)
            state["gate_status"][out["gate"]] = GATE_PASSED
            _save_state(plan_dir, session_id, state)
            gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
            out = _advance(plan_dir, session_id, gates, state, dry_run=True)
        else:
            out = _run_gate(plan_dir, session_id, out["gate"], dry_run=True)
    if out.get("action") == "passed":
        out = verify_finalize(plan_dir, session_id, skip_evidence=True)
    return out


def verify_finalize(plan_dir, session_id, _state=None, *, skip_evidence=False):
    state = _state or _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    if _first_pending(state) is not None:
        return {"action": "error", "message": "verify not complete; gates still pending"}

    refusal = _evidence_refusal(plan_dir, session_id, state, skip_evidence)
    if refusal is not None:
        return refusal

    # An on-box park DEFERRED shipping by writing the closeout's checkpoint, and
    # HERE — every gate passed, evidence proved — is the first moment it is true
    # that verification is complete. `resolve` used to clear it itself, which
    # un-deferred shipping while a LATER gate was still pending. RESTORE, never
    # blank-clear: the park may have written over a checkpoint the SESSION asked
    # for, and that one is not answered by resolving the park.
    vpk.unpark_closeout(plan_dir, session_id, state)

    state["outcome"] = GATE_PASSED
    _save_state(plan_dir, session_id, state)

    final_status, note, hc, hc_auto_continue = _final_status_and_note(
        plan_dir, session_id, state)
    ab.apply_mutation(_html(plan_dir), session_id, status=final_status, note=note)

    # PS-01 structural DONE-gate (PRIMARY, browser-free) — same discipline as
    # run.cmd_apply's item-level check, applied here to the session-level
    # write this function just made (the write cmd_apply deferred while a
    # verify block was pending). Re-read PLAN.html from disk; a mismatch means
    # the dashboard did NOT actually flip even though apply_mutation ran, and
    # that is treated exactly like a failed gate — rework or halt, never a
    # silent DONE.
    gate = sg.run_gate(plan_dir, {session_id: final_status})
    if gate["status"] == "failed":
        return _gate_failed(plan_dir, session_id, state, "structural", "; ".join(gate["reasons"]))

    # SECONDARY, best-effort visual confirmation. Never blocking on its own —
    # an environment with no headless Chrome must still be able to finish —
    # but a CONFIRMED real layout finding (not merely "unavailable") is treated
    # the same as a failed gate: the dashboard is genuinely broken, not just
    # unconfirmed.
    try:
        render = rv.check(plan_dir)
    except Exception as e:  # pragma: no cover - defensive
        render = {"status": "unavailable", "reason": f"render-verify errored: {e}"}
    if render.get("status") == "failed":
        return _gate_failed(
            plan_dir, session_id, state, "render",
            f"headless-Chrome layout-audit banner found a real finding: {render.get('reason')}",
        )

    # RP-05 — a verify-gated session's `plan_impact` parks HERE, not at `apply`:
    # a discovery that invalidates future work is only worth an operator's
    # attention once the session that made it has passed its own gates. When the
    # session ALSO parks on a human checkpoint, that gate goes first and
    # `ack-checkpoint` raises the REPLAN afterwards (same precedence as apply).
    replan = None
    if final_status != "AWAITS_REVIEW":
        manifest = mio.load_manifest(plan_dir)
        try:
            impact = rp.closeout_impact(cp.load_closeout(plan_dir, session_id), manifest)
        except (OSError, ValueError):
            impact = None
        if impact:
            replan = rp.park(plan_dir, session_id, impact, manifest)

    # TEL-01 — verify-finalize(passed): every gate (structural + render + the
    # declared verify block) has now actually passed. One record here, before
    # the AWAITS_REVIEW/DONE branching below, since "passed" is the same fact
    # on all three paths — only the human-checkpoint routing differs.
    outc.write(plan_dir, session_id, resolution="verify_finalize", result="passed",
              verified=True, attempt=state["rework_count"] + 1, rework_count=state["rework_count"],
              stuck_armed=sp.armed(sp.last_failure(plan_dir, session_id)))

    return _passed_result(plan_dir, session_id, hc, hc_auto_continue,
                          gate, render, replan)
