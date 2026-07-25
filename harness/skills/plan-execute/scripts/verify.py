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
    verify gates are read-only checks; a gate that is NOT safe to run
    concurrently must not be placed on a ``parallel_group`` session.
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

from datetime import UTC, datetime
from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import gate_policy as gp
import manifest_io as mio
import render_verify as rv
import run_state_io as rsi
import ship_state_io as ssio
import shipping as shp
import shipping_adapter as adapter
import structural_gate as sg

GATE_PENDING = "pending"
GATE_PASSED = "passed"
GATE_FAILED = "failed"


def _now():
    return datetime.now(UTC).isoformat()


def _html(plan_dir):
    return Path(plan_dir) / "PLAN.html"


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------
def _resolved_verify(manifest, session_id):
    s = mio.session_by_id(manifest).get(session_id)
    return (s.get("verify") if s else None) or None


def compute_gates(plan_dir, manifest, session_id):
    """Ordered gate step descriptors for a session's resolved ``verify`` block.

    Reuses ``shipping.resolve_gate`` so a verify gate and a ``pre_deploy_gate``
    with the same id resolve identically. Raises ``shipping.StepResolveError`` on
    an unknown gate id so the caller halts with a precise reason."""
    vb = _resolved_verify(manifest, session_id)
    if not vb:
        return []
    return [shp.resolve_gate(plan_dir, gid) for gid in (vb.get("gates") or [])]


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
def verify_state_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.json"


def _load_state(plan_dir, session_id):
    return ssio.read_json_with_bak(verify_state_path(plan_dir, session_id))


def _save_state(plan_dir, session_id, state):
    state["updated_at"] = _now()
    ssio.durable_write_json(verify_state_path(plan_dir, session_id), state)


def _feedback_path(plan_dir, session_id):
    return Path(plan_dir) / "_verify_state" / f"{session_id}.feedback.md"


def _new_state(plan_dir, manifest, session_id, gates, vb):
    co = cp.load_closeout(plan_dir, session_id)
    return {
        "session_id": session_id,
        "manifest_digest": mio.manifest_digest(plan_dir),
        "closeout_digest": co.get("_closeout_digest") if co else None,
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


def _gate_by_name(gates, name):
    for g in gates:
        if g["name"] == name:
            return g
    return None


def _first_pending(state):
    for name in state["gates"]:
        if state["gate_status"].get(name) != GATE_PASSED:
            return name
    return None


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
    if not gates and not require_ev:
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
        if state.get("outcome") == "rework":
            # A prior attempt asked for rework; this is the fresh closeout's pass.
            new_digest = co.get("_closeout_digest") if co else None
            state["closeout_digest"] = new_digest
            state["human_checkpoint_reason"] = co.get("human_checkpoint_reason") if co else None
            state["gate_status"] = {g["name"]: GATE_PENDING for g in gates}
            state["gates"] = [g["name"] for g in gates]
            state["outcome"] = None
            _save_state(plan_dir, session_id, state)
    else:
        vb = _resolved_verify(manifest, session_id)
        state = _new_state(plan_dir, manifest, session_id, gates, vb)
        _save_state(plan_dir, session_id, state)
        rsi.log_event(plan_dir, "verify_started", session_ids=[session_id],
                      gates=state["gates"], attempt=state["rework_count"] + 1)
    return _advance(plan_dir, session_id, gates, state, dry_run)


def _advance(plan_dir, session_id, gates, state, dry_run):
    name = _first_pending(state)
    if name is None:
        state["outcome"] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return {"action": "passed", "session": session_id}
    g = _gate_by_name(gates, name)
    if g["kind"] == "skill":
        return {"action": "invoke-skill", "session": session_id, "gate": name,
                "skill": g["skill"], "args": g.get("args", ""), "step": name,
                "success_criteria": g.get("success_criteria")}
    # argv-kind gate: this helper runs it.
    if dry_run:
        return _run_gate(plan_dir, session_id, name, dry_run=True)
    return {"action": "run-argv", "session": session_id, "gate": name, "step": name}


# --------------------------------------------------------------------------
# verify-record (skill-kind outcome) / verify-run-argv (argv-kind)
# --------------------------------------------------------------------------
def verify_record(plan_dir, session_id, gate, status, result_file=None):
    state = _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    excerpt = ""
    if result_file:
        try:
            excerpt = adapter.redact(Path(result_file).read_text())
        except OSError:
            excerpt = ""
    if status == "done":
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run=False)
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def _run_gate(plan_dir, session_id, gate, *, dry_run=False):
    state = _load_state(plan_dir, session_id)
    gates = compute_gates(plan_dir, mio.load_manifest(plan_dir), session_id)
    g = _gate_by_name(gates, gate)
    if g is None or g["kind"] != "argv":
        return {"action": "error", "message": f"{gate} is not an argv-kind gate"}
    fake = g.get("fixture_fake")
    if dry_run and fake:
        res = {"returncode": fake.get("returncode", 0), "stdout": fake.get("stdout", ""),
               "stderr": fake.get("stderr", "")}
    elif dry_run:
        res = {"returncode": 0, "stdout": "(dry-run pass)", "stderr": ""}
    else:
        res = ssio.run_deploy_argv(g["argv"], cwd=g["cwd"],
                                   env_allowlist=g.get("env_allowlist", []),
                                   timeout=g.get("timeout", 1200))
    if res.get("returncode") == 0:
        state["gate_status"][gate] = GATE_PASSED
        _save_state(plan_dir, session_id, state)
        return _advance(plan_dir, session_id, gates, state, dry_run)
    excerpt = adapter.redact((res.get("stderr") or "") + (res.get("stdout") or ""))
    return _gate_failed(plan_dir, session_id, state, gate, excerpt)


def verify_run_argv(plan_dir, session_id, gate):
    return _run_gate(plan_dir, session_id, gate, dry_run=False)


# --------------------------------------------------------------------------
# Fail → rework or halt
# --------------------------------------------------------------------------
def _gate_failed(plan_dir, session_id, state, gate, excerpt):
    state["gate_status"][gate] = GATE_FAILED
    state["failures"][gate] = excerpt
    fb = _feedback_path(plan_dir, session_id)
    fb.parent.mkdir(parents=True, exist_ok=True)
    attempt = state["rework_count"] + 1
    fb.write_text(
        f"# Verification feedback — {session_id} (attempt {attempt})\n\n"
        f"Gate `{gate}` FAILED. Fix the cause, then this session re-runs and "
        f"re-verifies.\n\n## Gate output (redacted, tail)\n\n```\n{excerpt}\n```\n"
    )

    rework_ok = state.get("on_fail", "rework") == "rework" and state["rework_count"] < state["max_rework"]
    if rework_ok:
        state["rework_count"] += 1
        state["outcome"] = "rework"
        _save_state(plan_dir, session_id, state)
        ab.apply_mutation(_html(plan_dir), session_id, status="PARTIAL",
                          note=f"verify rework {state['rework_count']}/{state['max_rework']}: "
                               f"gate {gate} failed")
        rsi.log_event(plan_dir, "verify_rework", session_ids=[session_id], gate=gate,
                      attempt=state["rework_count"], max_rework=state["max_rework"])
        return {"action": "rework", "session": session_id, "gate": gate,
                "attempt": state["rework_count"], "max_rework": state["max_rework"],
                "feedback_file": str(fb)}

    state["outcome"] = "halted"
    _save_state(plan_dir, session_id, state)
    reason = (f"verify gate {gate} failed; on_fail={state.get('on_fail')}, "
              f"rework {state['rework_count']}/{state['max_rework']} exhausted")
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    rsi.log_event(plan_dir, "verify_failed", session_ids=[session_id], gate=gate,
                  excerpt=excerpt[:200])
    return {"action": "halted", "session": session_id, "gate": gate, "reason": reason,
            "feedback_file": str(fb)}


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
    (project-root-relative paths like ``docs/operations/…``). Returns
    ``(ok, excerpt)``."""
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
        if not isinstance(p, str) or not p.strip():
            problems.append(f"{p!r} (not a non-empty path string)")
            continue
        path = Path(p)
        if not path.is_absolute():
            in_plan = Path(plan_dir) / path
            path = in_plan if in_plan.exists() else Path.cwd() / path
        if not path.exists():
            problems.append(f"{p} (does not exist)")
        elif path.is_file() and path.stat().st_size == 0:
            problems.append(f"{p} (empty file — no proof inside)")
        elif path.is_dir() and not any(path.iterdir()):
            problems.append(f"{p} (empty directory)")
    if problems:
        return False, ("Declared evidence failed the presence / non-empty check:\n  - "
                       + "\n  - ".join(problems))
    return True, ""


# --------------------------------------------------------------------------
# verify-finalize
# --------------------------------------------------------------------------
def verify_finalize(plan_dir, session_id, _state=None, *, skip_evidence=False):
    state = _state or _load_state(plan_dir, session_id)
    if state is None:
        return {"action": "error", "message": f"no verify state for {session_id}"}
    if _first_pending(state) is not None:
        return {"action": "error", "message": "verify not complete; gates still pending"}

    # Evidence assertion runs LAST — after every gate has passed — so a session is
    # finalized only when its proof artifacts are actually on disk. A miss reworks
    # or halts exactly like a failed gate (synthetic gate name ``evidence``).
    if state.get("require_evidence") and not skip_evidence:
        ok, excerpt = _check_evidence(plan_dir, session_id)
        if not ok:
            return _gate_failed(plan_dir, session_id, state, "evidence", excerpt)

    state["outcome"] = GATE_PASSED
    _save_state(plan_dir, session_id, state)

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
                "gate_auto_continue": True, "structural_gate": gate, "render_verify": render}
    if hc:
        rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="AWAITS_REVIEW")
        rsi.log_event(plan_dir, "checkpoint_reached", session_ids=[session_id], reason=hc)
        return {"action": "done", "session": session_id, "final_status": "AWAITS_REVIEW",
                "human_checkpoint_reason": hc, "structural_gate": gate, "render_verify": render}
    rsi.log_event(plan_dir, "verify_passed", session_ids=[session_id], then="DONE")
    return {"action": "done", "session": session_id, "final_status": "DONE",
            "structural_gate": gate, "render_verify": render}


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
