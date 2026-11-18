"""verify-begin's read side: what the next real call would answer, with no write.

`verify-begin --dry-run` is a QUERY. It computes the answer in memory and cannot
reach `_save_state`, `rsi.log_event`, a status write or a gate command, so a gate
that never ran is never recorded as passed (incident 2026-09-23; probe
2026-11-12). `verify-simulate` is the other user of the old flag: it passes every
gate without running it and DOES write state and events. It now says so through
its own `simulate=True` on `verify_begin`.

`verify` imports this module, so `verify` is imported lazily inside the functions.
"""

import closeout_pipeline as cp
import locked_checks as lc
import manifest_io as mio
import plan_scope as pscope
from verify_pass import GATE_PASSED, first_pending, gate_by_name, requires_resume, reviews_first, stale_closeout


def prologue(plan_dir, session_id):
    """(early answer or None, manifest, gates, verify block, closeout) -- the part
    of verify-begin that only reads. An early answer is a noop."""
    import verify as vfy
    manifest = mio.load_manifest(plan_dir)
    gates = vfy.compute_gates(plan_dir, manifest, session_id)
    vb = vfy._resolved_verify(manifest, session_id, plan_dir)
    co = cp.load_closeout(plan_dir, session_id)
    # A verify block with no gates but require_evidence STILL runs -- the evidence
    # assertion fires at verify-finalize. Only a truly empty block is a noop.
    if not gates and not (vb and vb.get("require_evidence")) and not lc.locked_paths(manifest, session_id):
        return {"action": "noop", "session": session_id}, manifest, gates, vb, co
    result = co.get("result") if co else None
    # Only a claimed-complete session is verified.
    if result != "DONE":
        return {"action": "noop", "session": session_id, "reason": f"result={result}"}, manifest, gates, vb, co
    return None, manifest, gates, vb, co


def directive(plan_dir, session_id, g):
    """The step the orchestrator takes for gate `g`: invoke a skill, or run an argv gate."""
    name = g["name"]
    if g["kind"] != "skill":
        return {"action": "run-argv", "session": session_id, "gate": name, "step": name}
    out = {"action": "invoke-skill", "session": session_id, "gate": name,
           "skill": g["skill"], "args": g.get("args", ""), "step": name,
           "success_criteria": g.get("success_criteria")}
    # An isolated member's gate must look at THAT MEMBER's worktree, not the shared
    # tree its peers are still editing (contract M4); the orchestrator invokes a
    # skill-kind gate itself, so the location travels to it.
    wt_cwd = pscope.gate_cwd(plan_dir, session_id, g)
    if wt_cwd and wt_cwd != g.get("cwd"):
        out["cwd"] = wt_cwd
    return out


def begin_query(plan_dir, session_id, resume):
    """What a real `verify_begin(resume=resume)` would return next, written nowhere.
    A prior state is read, never saved; a refusal is returned without its halt."""
    import verify as vfy
    early, manifest, gates, vb, co = prologue(plan_dir, session_id)
    if early:
        return early
    state = vfy._load_state(plan_dir, session_id)
    if state is None:
        state = vfy._new_state(plan_dir, manifest, session_id, gates, vb)
    else:
        if state.get("manifest_digest") != mio.manifest_digest(plan_dir):
            return vfy._fail(plan_dir, session_id, "state-drift",
                             "manifest changed since verify state was written", True)
        if state.get("outcome") == GATE_PASSED:
            return {"action": "already-verified", "session": session_id}
        if state.get("outcome") == "rework":
            stale = stale_closeout(session_id, state, co)
            if stale:
                return stale
            gates = reviews_first(gates)   # the fresh pass, as verify_begin opens it
            state["gate_status"] = {g["name"]: vfy.GATE_PENDING for g in gates}
            state["gates"] = [g["name"] for g in gates]
        elif not resume:
            return requires_resume(session_id, state)
    if lc.locked_paths(manifest, session_id) and (edited := lc.check(plan_dir, session_id)):
        return {"action": "refused", "session": session_id, "refusal": lc.LOCKED_CHECK_EDITED,
                "edited": [list(e) for e in edited]}   # the refusal verify_begin would charge, uncharged
    name = first_pending(state)
    if name is None:
        return {"action": "passed", "session": session_id}
    return directive(plan_dir, session_id, gate_by_name(gates, name))
