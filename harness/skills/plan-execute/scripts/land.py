"""LND-01 — THE LAND STAGE. A finished plan does not stop; it lands.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21),
§4 (the land protocol), §5 (approval binding), §8.c (the plan record). Read §4
first — **it is written from a probe transcript that falsified the obvious
design** — this module is its entry point, not its source of truth. The ordered
mechanism lives in ``land_steps.py``, the runtime state in ``land_state.py`` and
the operator-facing briefs in ``land_brief.py``; all three splits exist for the
500-LOC ratchet this repo's ``pre-commit`` enforces.

THE SHAPE, in order (§4.1 -> §4.7). Every step is idempotent and persists its
result BEFORE the next runs, so a crash resumes rather than restarts:

  1. sync   — merge ``<remote>/<default>`` INTO the plan branch, in the plan
              worktree. A conflict parks; nothing is ever auto-resolved.
  2. lease  — the repo-scoped ``git:`` lease FIRST, then a land worktree whose
              path is UNIQUE per plan, so two landers cannot collide on the path
              *before* exclusion begins. This refusal is what makes landing
              serial; it is a mechanism, not a convention.
  3. merge  — ``merge --no-ff`` of the plan branch into a DETACHED land HEAD at a
              captured sha. ``git worktree add <path> <default>`` is refused
              outright (exit 128) while the default branch is checked out.
  4. record — ``record-plan`` (§8.c), staged with an explicit pathspec.
  5. re-gate— the plan's UNION of verify gates, in the land worktree, on the exact
              tree that will be pushed (§5.1b). ALWAYS. There is no skip.
  6. ack    — park ``AWAITS_REVIEW`` until a human ack BOUND to
              ``{plan_head, main_head, gate_digest}`` exists (§5.1).
  7. push   — compare-and-swap onto ``refs/heads/<default>``, bounded re-sync at
              3 attempts; or, with NO remote, a durable ``refs/plan-lands/<slug>``
              (§4.8a measured that without it routine cleanup destroys the merge).
  8. publish— §8.c2's SECOND, explicitly-permitted default-branch mutation, which
              carries the record written after the land push. Not re-gated and not
              re-approved, and that exemption is earned mechanically: its commit is
              verified to touch only ``_plans/<slug>/``. Only then is the land
              worktree removed — it is where step 8 happens.

WHY THE CHECKPOINT IS ENFORCED IN THE MECHANISM AND NOT ON THE DISPATCH PATH.
This repo MEASURED that by-name session dispatch (``begin --sessions sNN``) walks
straight through ``requires_human_checkpoint`` — only ``plan`` surfaces it. So a
land gated by dispatch policy would be skippable by a documented command. Instead
``land_steps.step_ack`` sits between the gates and the push and there is no other
route to ``step_push``: without a valid ack the push is unreachable, whatever
invoked the land.

STEP 9 — CLEANUP (§15's `land-success` row, LND-02, s09): once ``step_finish``
reports ``landed``, ``plan_teardown.step_cleanup`` tears down the PLAN worktree
(distinct from the LAND worktree ``step_finish`` already removed) and deletes
the plan branch, local and remote — reusing ``worktree.teardown_worktree``
for the worktree half so the teardown order is never re-derived (see that
module's own docstring for why it is NOT ``plan_teardown.remove_plan_worktree``
here). It runs under the SAME lease this function's ``finally`` releases below,
so cleanup is never racing another `land`/`begin` for this repo. The abandon
path (retiring an UNLANDED plan, §15's `abandon` row) lives in
``plan_teardown.py``'s ``retire_plan`` and ``run.py``'s ``retire-plan``
command, not here — it keeps
the branch unconditionally (§15.1), which is the one rule this module's
cleanup does NOT follow (a landed branch's only copy is the default branch).
"""

import argparse
import json
import sys
from pathlib import Path

import plan_teardown as pt
import land_gate as lgt
import land_start as lsg
import land_state as lst
import land_push as steps
import run_state_io as rsi
import ship_locks as sl
from land_state import LAND_TIMEOUT, context, load, save
from worktree import git

# Re-exported so callers and tests have ONE import surface for the land stage
# (the same courtesy `plan_worktree` extends for the version gate).
gate_digest = lst.gate_digest
union_gates = lst.union_gates
land_state_path = lst.state_path
require_started = lsg.require_started    # LND-13: `begin`'s one-line guard


def _preflight(plan_dir, ctx):
    """§4.0 — assert, immediately before the merge, that the recorded
    ``<default>`` still resolves and still names the ref it did at ``begin``.
    A default branch renamed mid-plan is a park with a brief, not a guess: the
    alternative is landing onto the wrong branch."""
    if not ctx["default"]:
        return {"action": "error",
                "message": f"{plan_dir} recorded no default branch; §4.0 resolves it once "
                           "at `begin` and every later stage reads it from there."}
    if ctx.get("remote_ambiguous"):
        return {"action": "land-parked", "kind": "remote-ambiguous",
                "brief": f"LAND PARKED — this repo has {len(ctx['remote_ambiguous'])} "
                         f"remotes ({', '.join(ctx['remote_ambiguous'])}) and none is "
                         "`origin`, so there is no single ref §4.2's push can mean. "
                         "Treating that as \"no remote\" would report a shipped land that "
                         "never left the machine, and guessing a target would push to a "
                         "remote nobody chose. Name the intended one:\n\n"
                         "  git remote rename <the-one-you-push-to> origin\n"
                         f"  run.py land-resume {ctx['plan_dir']}"}
    rc, out, _ = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], ctx["root"])
    live = out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else None
    if live and live != ctx["default"]:
        return {"action": "land-parked", "kind": "default-renamed",
                "brief": f"LAND PARKED — this plan pinned `{ctx['default']}` as the default "
                         f"branch at `begin`, and the repo now names `{live}`. §4.0 resolves "
                         "the default ONCE and never re-derives it; landing onto a branch "
                         "nobody chose is exactly what that rule prevents."}
    return None


def _remote_kept(msg, cleanup, manual=None):
    """LND-11: a remote plan branch the cleanup KEPT stays named on every
    already-landed answer, so it is never silently left behind. ``manual`` (set
    once a finished finish has stopped retrying) replaces the promised retry."""
    kept = cleanup.get("branch_remote")
    if isinstance(kept, dict):
        return (f"{msg}; remote plan branch kept (`cleanup.branch_remote`: "
                f"{kept.get('kept')}) — " + (manual or "`run.py finish` retries its delete"))
    return msg


def land(plan_dir):
    """Run (or resume) the land protocol. Idempotent at every step."""
    plan_dir = str(Path(plan_dir).resolve())
    ctx = context(plan_dir)
    if ctx is None:
        return {"action": "not-isolated",
                "message": "this plan has no isolated branch or worktree, so there is "
                           "nothing to land (§6's version gate, or §7.2's non-git target)."}
    guard = _preflight(plan_dir, ctx)
    if guard is not None:
        return guard
    st = load(plan_dir, ctx) or lst.new_state(ctx)
    if st.get("state") == "landed":
        cleanup = st.get("cleanup")
        if cleanup is not None and cleanup.get("worktree") != "preserved":
            return {"action": "landed", "merge_sha": st.get("landed_sha"),
                    "landed_on": st.get("landed_on"), "brief": st.get("brief"),
                    "cleanup": cleanup,
                    "message": _remote_kept("already landed — nothing to do", cleanup,
                                            pt.manual_remote_delete(plan_dir, ctx, st))}
        # STEP 9 never ran (a plan landed before s09 recorded no `cleanup` at
        # all) or it REFUSED (dirty content in the plan worktree, §12.4). Retry
        # it alone, under a fresh lease — `step_cleanup` is idempotent, so
        # re-running it on an already-clean plan costs a few no-op git calls,
        # never re-walks the (already-succeeded) protocol above it.
        try:
            sl.acquire_ship_lock(plan_dir, ctx["resource"], guarded_timeout=LAND_TIMEOUT)
        except rsi.LockError as e:
            return {"action": "land-locked", "resource": ctx["resource"], "message": str(e)}
        try:
            cleanup = pt.step_cleanup(plan_dir, ctx, st)
        finally:
            sl.release_ship_lock(plan_dir, ctx["resource"])
        msg = _remote_kept("already landed — nothing to do"
                           if cleanup.get("worktree") != "preserved"
                           else "already landed — cleanup still refused, see `cleanup.detail`",
                           cleanup, pt.manual_remote_delete(plan_dir, ctx, st))
        return {"action": "landed", "merge_sha": st.get("landed_sha"),
                "landed_on": st.get("landed_on"), "brief": st.get("brief"),
                "cleanup": cleanup, "message": msg}
    try:
        # §4.1 — LOCKS FIRST, worktree second. Acquired before anything is
        # created, so the lease (not a filesystem collision) is the single
        # arbiter between two landers.
        sl.acquire_ship_lock(plan_dir, ctx["resource"], guarded_timeout=LAND_TIMEOUT)
    except rsi.LockError as e:
        return {"action": "land-locked", "resource": ctx["resource"], "message": str(e)}
    try:
        # RESUME AFTER THE POINT OF NO RETURN. Everything up to and including the
        # push is skipped once git confirms the merge is already on the target:
        # the sync, the merge and the re-gate all describe a world that no longer
        # exists, and re-running them parks a land that succeeded.
        sequence = ((steps.step_final_record,) if steps.already_landed(ctx, st)
                    else steps.STEPS)
        for step in sequence:
            out = step(plan_dir, ctx, st)
            if out is not None:
                return out
        finish = steps.step_finish(plan_dir, ctx, st)
        # STEP 9 — post-land cleanup (§15's `land-success` row). Only when the
        # land itself succeeded: `step_finish`'s OWN preserved-land-worktree
        # park (`land-worktree-preserved`) must reach the operator unchanged,
        # not be reinterpreted as a landed result with a cleanup note.
        if finish.get("action") == "landed":
            finish["cleanup"] = pt.step_cleanup(plan_dir, ctx, st)
        return finish
    finally:
        # §15 — a parked plan holds NOTHING. The lease is released on every exit,
        # including the AWAITS_REVIEW park, which may outlive it by days.
        sl.release_ship_lock(plan_dir, ctx["resource"])


def land_ack(plan_dir, note=None):
    """Record the human land ack, BOUND to the candidate the human was shown (§5.1).

    This is the only writer of ``ack``. It refuses unless a candidate is actually
    awaiting review, because an ack recorded before the merge and the re-gate
    would be bound to nothing — and a binding that is not checked, or is checked
    against values invented after the fact, is decoration.
    """
    ctx = context(plan_dir)
    if ctx is None:
        return {"action": "error", "message": f"{plan_dir} is not an isolated plan"}
    st = load(plan_dir, ctx) or {}
    if st.get("state") != "awaiting_review" or not st.get("candidate"):
        return {"action": "error", "state": st.get("state"),
                "message": "there is no land candidate awaiting review. Run "
                           f"`run.py land {plan_dir}` first — it merges, records and "
                           "re-gates, and only then asks."}
    cand = st["candidate"]
    st["ack"] = {"plan_head": cand["plan_head"], "main_head": cand["main_head"],
                 "gate_digest": cand["gate_digest"], "merge_sha": cand.get("merge_sha"),
                 "record_pathspec": f"_plans/{ctx['slug']}",
                 "at": lst.now(), "note": note}
    st["state"] = "acked"
    st.pop("invalidated_from", None)      # this candidate IS the approved one now
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_acked", session_ids=[], slug=ctx["slug"], note=note,
                  **{k: st["ack"][k]
                     for k in ("plan_head", "main_head", "gate_digest", "merge_sha")})
    return {"action": "land-acked", "ack": st["ack"], "next": f"run.py land {plan_dir}"}


def land_verify(plan_dir, decision, note=None):
    """Record the HUMAN's on-box disposition for a re-gate NO MODEL MAY RUN.

    The land-scope counterpart of `run.py ack-checkpoint --session sNN --decision
    verified-on-box|blocked`, and it exists because that session-scope park had no
    equivalent here. Under the Codex harness with no Claude-verifier opt-in every
    review gate refuses before reading anything (`verifier_park.refusal`, exit 2).
    At land scope that reached `gate-indeterminate`, whose brief says "re-run
    `run.py land`" — and re-running reproduces the identical refusal, because a
    policy answer is deterministic. Every documented escape from an indeterminate
    (raise the timeout from a measurement, narrow the scope, run the gate by hand)
    assumes a TIMEOUT or an unparseable verdict. None of them applies. So a
    Codex-built plan could never land, by any command.

    WHAT THIS IS NOT. It does not reclassify the refusal as a pass: the recorded
    outcome is `verified-on-box`, its own value, stamped with the candidate's
    `gate_key`, logged to run.ndjson, printed in the ack brief's gate table and
    carried into §5.1c's digest — so the approval a human gives at `land-ack` is
    bound to a digest that says a HUMAN, not a model, cleared that gate.

    And it does not weaken the egress rule by one line. No reviewer runs, here or
    afterwards; the gate's refusal stands exactly as written. What changes is only
    that the refusal now has the same recorded human disposition the session scope
    has had since the park was built.
    """
    ctx = context(plan_dir)
    if ctx is None:
        return {"action": "error", "message": f"{plan_dir} is not an isolated plan"}
    if decision not in lgt.DECISIONS:
        return {"action": "error", "message": f"unknown decision {decision!r}; use "
                                              f"{' or '.join(lgt.DECISIONS)}"}
    st = load(plan_dir, ctx) or {}
    parked = st.get("park") or {}
    gates = parked.get("on_box") or []
    # THE PARK IS THE PRECONDITION, not just the gate key. A disposition is a
    # human's answer to a gate that REFUSED; recorded against a gate that never
    # refused it would be a verdict nobody asked for, on a tree no gate declined
    # to read — which is the rubber stamp `cmd_ack_checkpoint` refuses one scope
    # over.
    if (st.get("state") != "parked" or parked.get("kind") != lgt.ON_BOX_PARK
            or not gates or not st.get("gate_key")):
        return {"action": "error", "state": st.get("state"), "park": parked.get("kind"),
                "message": "there is no on-box verification park to resolve. This records "
                           "what a HUMAN found in a merged tree that no model may review; a "
                           "gate that has not refused is not yours to dispose of. Run "
                           f"`run.py land {plan_dir}` — it re-gates, and parks here only if "
                           "a gate refuses on policy."}
    for gid in gates:
        st.setdefault("on_box_gates", {})[gid] = {
            "gate_id": gid, "outcome": decision, "gate_key": st["gate_key"],
            "findings_count": 0, "cause": (parked.get("on_box_causes") or {}).get(gid),
            "note": note, "recorded_at": lst.now()}
    # The digest describes a result set this record just changed — the same
    # belt-and-braces `land_record` applies, and for the same reason.
    st.pop("gate_digest", None)
    save(plan_dir, ctx, st)
    # The park record and LAND_NOTICE.txt are LEFT AS THEY ARE, exactly as
    # `land_record` leaves them: the next `land` rewrites both (a fresh park, or
    # `step_ack`'s approval brief), and leaving them means a human who changes
    # their mind before re-running can simply record the other disposition.
    rsi.log_event(plan_dir, "plan_land_verified", session_ids=[], slug=ctx["slug"],
                  decision=decision, gates=len(gates), gate_key=st["gate_key"], note=note)
    return {"action": "land-verified", "decision": decision, "gates": gates,
            "gate_key": st["gate_key"], "note": note,
            "next": f"run.py land {plan_dir}"}


def land_record(plan_dir, gate, status, findings_count=None, result_file=None):
    """Record a SKILL-kind re-gate result the orchestrator ran — the same shape
    ``ship-record``/``verify-record`` already use. An argv gate never comes here:
    this helper runs those itself, because an exit code cannot be self-attested.

    **WHAT THIS CAN AND CANNOT GUARANTEE, said plainly.** The result is
    caller-supplied, so this cannot prove the skill ran. That is the SAME bounded
    gap §3.7 already states for every skill directive — "an orchestrating
    conversation is not a process, so no OS-level exclusion is available against
    it at all" — and no amount of Python closes it. What IS enforced:

    * the record is STAMPED with the ``gate_key`` in flight, and ``step_gate``
      ignores a record stamped with any other. A verdict issued against one
      candidate can never be reused for a different merge, plan head or gate set;
    * a ``passed`` verdict may carry a ``--result-file``, and if it does the path
      must EXIST and be NON-EMPTY — the same refusal the evidence gate applies,
      so "I ran it" can be made to cost an artifact;
    * only ``land`` ever pushes, and it re-checks §5.1d's binding first.

    An operator who wants an unforgeable land gate should declare it ``argv``.
    That is stated here rather than implied, because a green that cannot be
    distinguished from an unlaunchable one is the failure this repo has recorded
    twice.
    """
    ctx = context(plan_dir)
    if ctx is None:
        return {"action": "error", "message": f"{plan_dir} is not an isolated plan"}
    st = load(plan_dir, ctx) or {}
    if not st.get("gate_key"):
        return {"action": "error",
                "message": "no land re-gate is in progress — a verdict recorded now would "
                           "be bound to no candidate. Run `run.py land <plan-dir>` first."}
    outcome = "pass" if status in ("passed", "pass", "ok") else "fail"
    if outcome == "pass" and result_file is not None:
        rf = Path(result_file)
        if not rf.is_file() or rf.stat().st_size == 0:
            return {"action": "error", "gate": gate,
                    "message": f"--result-file {result_file} does not exist or is empty. A "
                               "PASS that names an artifact must be able to show it."}
    st.setdefault("skill_gates", {})[gate] = {
        "outcome": outcome, "gate_key": st["gate_key"],
        "findings_count": int(findings_count or (0 if outcome == "pass" else 1)),
        "result_file": str(result_file) if result_file else None,
        "recorded_at": lst.now()}
    # The digest describes a result set this record just changed. `step_gate`
    # recomputes it on every entry, so this is belt-and-braces against a park
    # that returns BEFORE the recomputation (an unreadable surface, say) leaving
    # `candidate()` quoting a digest for results nobody holds any more.
    st.pop("gate_digest", None)
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_gate_recorded", session_ids=[], slug=ctx["slug"],
                  gate=gate, outcome=outcome, gate_key=st["gate_key"],
                  result_file=str(result_file) if result_file else None)
    return {"action": "land-gate-recorded", "gate": gate, "outcome": outcome,
            "gate_key": st["gate_key"]}


def land_status(plan_dir):
    ctx = context(plan_dir)
    if ctx is None:
        return {"action": "not-isolated"}
    st = load(plan_dir, ctx) or {}
    return {"action": "land-status", "state": st.get("state", "pending"),
            "candidate": st.get("candidate"), "ack": st.get("ack"),
            "land_worktree": st.get("land_path"), "park": st.get("park"),
            "gate_digest": st.get("gate_digest"), "landed_sha": st.get("landed_sha"),
            "brief": st.get("brief")}


def land_action(plan_dir, action):
    """Insert ``land`` between the last terminal session and ``complete`` (§4, §15),
    and hold back a land-repair session that may not start yet (LND-13).

    A plan below §6's version gate holds no isolation claim, so ``context``
    returns None and this falls straight through to ``complete`` exactly as it
    does today. That is what keeps the whole land stage inert for every plan
    currently on disk. A scoped answer (``scope: session``) now appears only while
    other sessions are open, so it is not the plan's end and passes through unchanged.
    """
    if action.get("action") == "dispatch":
        # LND-13: a repair session whose fast-forward has not landed must not start,
        # on the ready path and on `plan --session` alike (both reach here).
        hit = lsg.start_blocker(plan_dir, [m["id"] for m in action.get("batch") or []])
        if hit:
            return {**{k: v for k, v in action.items() if k != "batch"}, "action": "blocked",
                    "sessions": [hit[0]], "reason": "repair-start-pending",
                    "message": hit[1]}
    if action.get("action") != "complete" or action.get("scope") == "session":
        return action
    ctx = context(plan_dir)
    if ctx is None:
        return action
    st = load(plan_dir, ctx) or {}
    if st.get("state") == "landed":
        return action
    out = {**action, "action": "land", "land_state": st.get("state") or "pending",
           "plan_branch": ctx["branch"], "default_branch": ctx["default"],
           "hint": f"every session is terminal — land the plan: `run.py land {plan_dir}`"}
    if st.get("brief"):
        out["land_brief"] = st["brief"]
    if st.get("state") == "awaiting_review":
        out["ack_with"] = f"run.py land-ack {plan_dir} --note '<why>'"
    # The on-box park's resolution, surfaced the same way the ack park's is. An
    # orchestrator reading only this dict would otherwise see a park with a brief
    # and no command, and the one thing it must not do is re-run `land`.
    if (st.get("park") or {}).get("kind") == lgt.ON_BOX_PARK and st.get("state") == "parked":
        out["verify_with"] = (f"run.py land-verify {plan_dir} --decision "
                              f"{'|'.join(lgt.DECISIONS)} --note '<what you found>'")
    return out


# The CLI seams `run.py` binds to. One table, so a new land subcommand is one
# row here and a parser in `run_parsers._add_land_parsers` — never a branch in
# `run.py`'s dispatch.
CLI = {
    "land": lambda plan_dir, args: land(plan_dir),
    "land-resume": lambda plan_dir, args: land(plan_dir),
    "land-ack": lambda plan_dir, args: land_ack(plan_dir, args.note),
    "land-verify": lambda plan_dir, args: land_verify(plan_dir, args.decision, args.note),
    "land-record": lambda plan_dir, args: land_record(
        plan_dir, args.gate, args.status, args.findings_count,
        getattr(args, "result_file", None)),
    "land-status": lambda plan_dir, args: land_status(plan_dir),
}


def _cli(argv=None):
    p = argparse.ArgumentParser(prog="land", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("land", "land-resume", "land-status"):
        sub.add_parser(name).add_argument("plan_dir")
    ack = sub.add_parser("land-ack")
    ack.add_argument("plan_dir")
    ack.add_argument("--note", default=None)
    ver = sub.add_parser("land-verify")
    ver.add_argument("plan_dir")
    ver.add_argument("--decision", required=True, choices=list(lgt.DECISIONS))
    ver.add_argument("--note", default=None)
    rec = sub.add_parser("land-record")
    rec.add_argument("plan_dir")
    rec.add_argument("--gate", required=True)
    rec.add_argument("--status", required=True, choices=["passed", "failed"])
    rec.add_argument("--findings-count", type=int, default=None)
    rec.add_argument("--result-file", default=None)
    a = p.parse_args(argv)
    if a.cmd in ("land", "land-resume"):
        return land(a.plan_dir)
    if a.cmd == "land-status":
        return land_status(a.plan_dir)
    if a.cmd == "land-ack":
        return land_ack(a.plan_dir, a.note)
    if a.cmd == "land-verify":
        return land_verify(a.plan_dir, a.decision, a.note)
    return land_record(a.plan_dir, a.gate, a.status, a.findings_count, a.result_file)


if __name__ == "__main__":  # pragma: no cover — operator/evidence entry point
    print(json.dumps(_cli(), indent=2, ensure_ascii=False))
    sys.exit(0)
