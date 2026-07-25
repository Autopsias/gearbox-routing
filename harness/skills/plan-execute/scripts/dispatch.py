"""Compute the next dispatch action from the manifest + current PLAN.html state.

Pure logic, no side effects. Given the immutable graph and the live per-session
statuses, decide: dispatch a batch, surface a human checkpoint, report blocked,
report halted, or report complete.
"""

DONE_STATES = {"DONE", "WONTFIX", "DEFERRED"}
TERMINAL_BLOCK = {"BLOCKED"}


def _deps_satisfied(session, statuses):
    deps = session.get("dispatch", {}).get("depends_on", []) or []
    return all(statuses.get(d) in DONE_STATES for d in deps)


def ready_sessions(manifest, statuses, resume=False):
    """Sessions eligible to dispatch now: status TODO|PARTIAL (and AWAITS_REVIEW
    when resuming a human checkpoint) with all deps DONE."""
    eligible = {"TODO", "PARTIAL"}
    if resume:
        eligible.add("AWAITS_REVIEW")
    out = []
    for s in manifest["sessions"]:
        st = statuses.get(s["id"], "TODO")
        if st in eligible and _deps_satisfied(s, statuses):
            out.append(s)
    return out


def next_action(manifest, statuses, *, resume=False, only_session=None):
    """Return a dict describing what /plan-execute should do next.

    action ∈ {dispatch, checkpoint, blocked, complete}
    """
    by_id = {s["id"]: s for s in manifest["sessions"]}

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

    ready = ready_sessions(manifest, statuses, resume=resume)
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
