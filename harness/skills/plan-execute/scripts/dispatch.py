"""Compute the next dispatch action from the manifest + current PLAN.html state.

Pure logic, no side effects. Given the immutable graph and the live per-session
statuses, decide: dispatch a batch, surface a human checkpoint, report blocked,
report halted, or report complete.
"""

DONE_STATES = {"DONE", "WONTFIX", "DEFERRED"}
TERMINAL_BLOCK = {"BLOCKED"}
# A dep is "terminal" when it will never change state on its own: it either
# completed, was deliberately skipped, or terminally failed. Only a session
# whose `depends_on_policy` says so dispatches over a terminally-FAILED dep.
TERMINAL_STATES = DONE_STATES | TERMINAL_BLOCK


def _deps_satisfied(session, statuses):
    """Are this session's upstream deps in an acceptable state to dispatch?

    `depends_on_policy` (written by plan-builder into every manifest, default
    `"all"`) decides what "acceptable" means:

      * ``"all"`` — a hard AND: every dep must be DONE/WONTFIX/DEFERRED.
      * ``"completed_or_terminal"`` — dispatch over the COMPLETED SUBSET once
        every dep has stopped moving, BLOCKED included. This is what keeps a
        capstone/acceptance session from being stranded forever by one upstream
        that closed BLOCKED (and a session using it is expected to say in its
        prompt how it degrades). Note the plan-level stop-the-world rule still
        applies: a BLOCKED session surfaces `action: blocked` first, so the
        capstone dispatches on the operator's `--resume`, not silently.
    """
    d = session.get("dispatch", {}) or {}
    deps = d.get("depends_on", []) or []
    ok_states = (
        TERMINAL_STATES if d.get("depends_on_policy") == "completed_or_terminal" else DONE_STATES
    )
    return all(statuses.get(dep) in ok_states for dep in deps)


def ready_sessions(manifest, statuses, resume=False, post_session_parked=()):
    """Sessions eligible to dispatch now: status TODO|PARTIAL (and AWAITS_REVIEW
    when resuming a PRE-DISPATCH human checkpoint) with all deps satisfied.

    ``post_session_parked`` names the sessions whose AWAITS_REVIEW is the OTHER
    flavor: the session already ran and closed DONE, and was parked afterwards by
    a `human_checkpoint_reason`. Re-dispatching one of those redoes paid work and
    re-trips the same checkpoint forever — so it is excluded from the ready set
    even under ``resume``. It is resolved by `run.py ack-checkpoint`, never by
    `--resume`. The caller derives the set from recorded state (a persisted
    closeout carrying result DONE) — never from prose.
    """
    eligible = {"TODO", "PARTIAL"}
    if resume:
        eligible.add("AWAITS_REVIEW")
    parked = set(post_session_parked or ())
    out = []
    for s in manifest["sessions"]:
        st = statuses.get(s["id"], "TODO")
        if st == "AWAITS_REVIEW" and s["id"] in parked:
            continue
        if st in eligible and _deps_satisfied(s, statuses):
            out.append(s)
    return out


def next_action(manifest, statuses, *, resume=False, only_session=None, post_session_parked=()):
    """Return a dict describing what /plan-execute should do next.

    action ∈ {dispatch, checkpoint, blocked, complete}
    """
    by_id = {s["id"]: s for s in manifest["sessions"]}
    parked = [s["id"] for s in manifest["sessions"] if s["id"] in set(post_session_parked or ())]

    # Only SESSION-level BLOCKED stops the world (unless explicitly resumed).
    # statuses carries item articles too; an item left BLOCKED inside a PARTIAL
    # session must not wedge the loop — PARTIAL semantics re-dispatch the session.
    blocked = [s["id"] for s in manifest["sessions"] if statuses.get(s["id"]) in TERMINAL_BLOCK]
    item_ids = {it["id"] for it in manifest.get("items", [])}
    blocked_items = sorted(i for i in item_ids if statuses.get(i) in TERMINAL_BLOCK)

    def _ret(action):
        # Surface blocked ITEMS on every action so they stay visible without
        # stopping the world.
        if blocked_items:
            action["blocked_items"] = blocked_items
        # Same treatment for post-session checkpoints awaiting an ack: they never
        # dispatch again, so if they were not surfaced they would just vanish.
        if parked:
            action["ack_required"] = parked
        return action

    if blocked and not resume:
        return _ret({"action": "blocked", "sessions": blocked})

    # Explicit single-session target.
    if only_session:
        s = by_id.get(only_session)
        if not s:
            return _ret({"action": "error", "message": f"unknown session {only_session!r}"})
        st = statuses.get(only_session, "TODO")
        if st in DONE_STATES:
            return _ret({"action": "complete", "message": f"{only_session} already {st}"})
        if st == "AWAITS_REVIEW" and only_session in parked:
            # Explicitly targeting a finished-and-parked session must not re-run
            # it either — the ack is still the only thing that clears this gate.
            return _ret(
                {
                    "action": "checkpoint",
                    "sessions": [only_session],
                    "message": f"{only_session} already finished and is parked for approval — "
                    "approve with `run.py ack-checkpoint <plan-dir> --session "
                    f"{only_session}` rather than re-dispatching it",
                }
            )
        if not _deps_satisfied(s, statuses) and not resume:
            unmet = [
                d
                for d in s.get("dispatch", {}).get("depends_on", [])
                if statuses.get(d) not in DONE_STATES
            ]
            return _ret(
                {
                    "action": "blocked",
                    "sessions": unmet,
                    "message": f"{only_session} has unmet deps: {unmet}",
                }
            )
        return _ret(_dispatch_or_checkpoint([s], statuses, resume))

    ready = ready_sessions(
        manifest, statuses, resume=resume, post_session_parked=post_session_parked
    )
    if not ready:
        # Anything pending at all? If everything is terminal/done -> complete.
        pending = [
            s["id"]
            for s in manifest["sessions"]
            if statuses.get(s["id"], "TODO") not in DONE_STATES
        ]
        awaiting = [
            s["id"] for s in manifest["sessions"] if statuses.get(s["id"]) == "AWAITS_REVIEW"
        ]
        awaiting_parked = [sid for sid in awaiting if sid in set(parked)]
        if awaiting_parked:
            # These are FINISHED sessions parked for an ack. `--resume` must not
            # re-run them, so say the one thing that actually clears the gate.
            return _ret(
                {
                    "action": "checkpoint",
                    "sessions": awaiting_parked,
                    "message": "post-session checkpoint awaiting approval — approve with "
                    "`run.py ack-checkpoint <plan-dir> --session sNN` (--resume will NOT "
                    "re-dispatch an already-finished session)",
                }
            )
        if awaiting and not resume:
            return _ret(
                {
                    "action": "checkpoint",
                    "sessions": awaiting,
                    "message": "awaiting human review",
                }
            )
        if pending:
            return _ret(
                {
                    "action": "blocked",
                    "sessions": pending,
                    "message": "no ready sessions but work remains (check deps/checkpoints)",
                }
            )
        return _ret({"action": "complete"})

    # Build the next batch: the first ready session + any ready peers sharing its
    # parallel_group.
    head = ready[0]
    pg = head.get("dispatch", {}).get("parallel_group")
    if pg:
        batch = [s for s in ready if s.get("dispatch", {}).get("parallel_group") == pg]
    else:
        batch = [head]
    return _ret(_dispatch_or_checkpoint(batch, statuses, resume))


def _dispatch_or_checkpoint(batch, statuses, resume):
    # If the head session requires a human checkpoint and we're not resuming,
    # surface the checkpoint instead of dispatching.
    head = batch[0]
    if head.get("dispatch", {}).get("requires_human_checkpoint") and not resume:
        return {
            "action": "checkpoint",
            "sessions": [head["id"]],
            "checkpoint_session": head["id"],
            # The author's decision brief (reason/decision/options) — the
            # orchestrator presents it to the operator verbatim. None on
            # legacy (pre-brief) manifests.
            "checkpoint": head.get("dispatch", {}).get("checkpoint"),
        }
    return {
        "action": "dispatch",
        "batch": [
            {
                "id": s["id"],
                "subagent_type": s.get("dispatch", {}).get("subagent_type"),
                "prompt_file": s.get("prompt_file", f"sessions/{s['id']}.prompt.md"),
                "context_file": s.get("context_file", f"sessions/{s['id']}.context.md"),
                "items": s.get("items", []),
                "model": s.get("model"),
            }
            for s in batch
        ],
    }
