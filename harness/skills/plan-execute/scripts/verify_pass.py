"""One verify PASS: which gate is next, and whether a pass may open at all.

ONE closeout, ONE verdict. After a `rework` verdict the next pass needs a
closeout that `apply` persisted AFTER that verdict; the one that failed is not
re-verified. Measured on an isolation plan: the
orchestrator re-ran the gates on the closeout that had just failed, 20 times
over 17 hours, never re-dispatching — so the stuck protocol armed five times
and the escalation ladder, computed only in `begin`, never got a dispatch to
climb on.
"""

GATE_PASSED = "passed"


def gate_by_name(gates, name):
    for g in gates:
        if g["name"] == name:
            return g
    return None


def first_pending(state):
    for name in state["gates"]:
        if state["gate_status"].get(name) != GATE_PASSED:
            return name
    return None


def requires_resume(session_id, state):
    """RT-04 — re-entering EXISTING, still-open verify state (outcome neither
    GATE_PASSED nor a fresh post-rework pass — the caller already handles those
    two) with no rework in between means this call is RESUMING something: a
    crash mid-pass, or a retry after a gate came back `indeterminate`
    (rework.py's `_indeterminate` leaves the gate pending, rework_count
    untouched, and says "verify-begin --resume re-runs it"). SKILL.md
    documents `--resume` for both; before this fix the flag was accepted and
    never read, so a blind re-call silently re-ran a gate that had just hung
    or timed out with no acknowledgment anything was done about it. Refuse —
    never halt the plan over this, it is a missing flag, not a broken one."""
    return {"action": "requires-resume", "session": session_id,
            "reason": (
                f"session {session_id} has an open verify pass (outcome="
                f"{state.get('outcome')!r}) from an earlier verify-begin call — this "
                "looks like a crash-interrupted pass, or a retry after a gate came back "
                "`indeterminate`. Pass --resume to continue it deliberately (fix the "
                "cause named in the gate's feedback_file first, if one was written).")}


def stale_closeout(session_id, state, co, *, strict=False):
    """ONE closeout, ONE verdict. After a `rework` verdict the next pass needs a
    closeout `apply` persisted AFTER that verdict; the one that failed is not
    re-verified. Returns the refusal payload, or None when the pass may open.
    `strict` is the `verify-run` door: a rework verdict closes the pass outright —
    only `verify-begin` (on a fresh closeout) opens the next one."""
    if state.get("outcome") != "rework":
        return None
    seen = state.get("closeout_persisted_at")
    now = (co or {}).get("_persisted_at")
    if strict or (seen is not None and now == seen):
        return {"action": "stale-closeout", "session": session_id,
                "rework_count": state.get("rework_count"),
                "reason": (f"session {session_id}'s closeout on disk is the one that already "
                           f"failed (rework {state.get('rework_count')}); its gates do not run "
                           "again. Re-dispatch through `begin --sessions "
                           f"{session_id}` (it computes the escalation rung), `apply` the new "
                           "closeout, then `verify-begin`.")}
    return None


def hand_pass_refusal(session_id, state, gates, gate):
    """Why `verify-record` may not hand-pass `gate`, or None. A rework verdict
    closed the pass (stale closeout); an argv gate is run by `verify-run-argv`,
    never reported by hand; an unknown name is an error, not a pass."""
    if state.get("outcome") == "rework":
        return stale_closeout(session_id, state, None, strict=True)
    g = gate_by_name(gates, gate)
    if g is None or g["kind"] == "argv":
        return {"action": "error", "message": f"{session_id} {gate!r} " + (
            "is an argv gate; a script must run it" if g else "names no gate")}
    return None


def is_review_gate(g):
    """A gate that asks a reviewer: skill-kind, or an argv gate that runs
    llm_review_gate.py (llm-review-*, cross-family-review-*)."""
    return g["kind"] == "skill" or any("llm_review_gate.py" in a for a in g.get("argv") or [])


def reviews_first(gates):
    """Fix rounds: run review gates before the test gates. A review
    finding sends the session back for another round, so a test run spent before
    it is wasted (on one session: over an hour of passing tests before a
    review failed). Stable inside each group; first-pass order is untouched."""
    return sorted(gates, key=lambda g: not is_review_gate(g))


def run_argv(plan_dir, session_id, g, dry_run):
    """Run argv gate `g` for `verify` and return its result dict. A dry run returns
    the gate's `fixture_fake` (or a plain pass) and runs nothing. A real run is
    timed and logged as a `gate_run` event, as land logs its gates."""
    fake = g.get("fixture_fake")
    if dry_run and fake:
        return {"returncode": fake.get("returncode", 0), "stdout": fake.get("stdout", ""),
                "stderr": fake.get("stderr", "")}
    if dry_run:
        return {"returncode": 0, "stdout": "(dry-run pass)", "stderr": ""}
    import gate_timing as gt
    import locked_checks as lc
    import plan_scope as pscope
    import review_context as rvs
    import ship_state_io as ssio

    cwd = pscope.gate_cwd(plan_dir, session_id, g)
    env = {**rvs.declared_env(g, plan_dir, session_id, cwd), **lc.gate_env(plan_dir, session_id)}
    t0 = gt.now()
    res = ssio.run_deploy_argv(g["argv"], cwd=cwd, timeout=g.get("timeout", 1200),
                               env_allowlist=g.get("env_allowlist", []), env_extra=env)
    gt.log_run(plan_dir, "verify", session_id, g["name"], gt.ms_since(t0) / 1000,
               ssio.argv_outcome(res, g.get("indeterminate_exit")))
    return res
