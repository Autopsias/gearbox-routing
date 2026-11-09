"""Guards that refuse a closeout before `apply` reads it.

Read-only answers about a session's DISPATCH HISTORY (run.ndjson), kept out of
run.py so the orchestrator's apply path stays one screen: which harness last
dispatched the session, whether a codex dispatch left its receipt, and — in
`escalation.undispatched_rework` — whether `begin` ran since the last failure.
"""
import sys
from pathlib import Path

import attestation
import safe_json as sj
import transcript_find

# The two sentences the app writes when an agent stops while its own background
# command still runs. The notification OPENS with one of them, so only the opening
# is matched: a final message can quote a sentence later in its prose without
# becoming an interim stop. The hook's INTERIM string must sit inside one (test I6).
INTERIM_OPENINGS = (
    "This agent has not reported yet: it is waiting on its own background work",
    "This agent stopped with background work of its own still running",
)


def is_interim_stop(raw):
    """True when `raw` has no closeout block and OPENS with an app interim sentence."""
    return "<plan-execute-closeout" not in raw and raw.lstrip().startswith(INTERIM_OPENINGS)


def refuse_if_interim(raw, plan_dir, session_id, log_event, out):
    """An interim notification is not a return: log it, change nothing (no BLOCKED,
    no halt) and exit 1. The agent's final message re-applies. Otherwise, return."""
    if is_interim_stop(raw):
        log_event(plan_dir, "apply_interim_refused", session_ids=[session_id])
        out({"applied": False, "failure": "agent-not-finished",
             "reason": "This agent stopped to wait for its own background work and has not "
                       "reported yet. Nothing was changed; apply its final message when it arrives."},
            plan_dir=plan_dir)
        sys.exit(1)


def inline_fix_refusal(plan_dir, session_id, agent_id, transcript, backend):
    """Why this receipt cannot stand without a stated reason, or None to accept it.

    The agent's transcript shows no request after this attempt's `begin`, so an agent
    did not do this attempt's work. Replay 2026-11-07 over 277 real Claude receipts:
    it fires on 16, in one plan: 15 inline or environment fixes, 1 agent run before `begin`."""
    if backend != "claude":  # a codex stream has no per-request timestamps to compare
        return None
    try:
        window = transcript_find._window(plan_dir, session_id)  # noqa: SLF001 — same module family
    except OSError:
        return None
    # shortcut: no readable transcript is accepted, so an unknown id passes; refuse it if that is ever seen.
    last = attestation.last_request_epoch(transcript or transcript_find.by_agent(agent_id))
    if window is None or last is None or last >= window[0]:
        return None
    return (f"Agent {agent_id} made no request after the last `begin` for {session_id}, so no agent "
            "did this attempt's work. Dispatch what `begin` returned, then record that agent. If "
            "you did this fix yourself, record it with --inline-fix '<reason>'.")


def unlabelled_inline_fix(plan_dir, session_id):
    """The reason of a refused receipt that no later receipt or `begin` replaced, or None."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    refused = None
    for ln in lines:
        rec = sj.load_dict(ln)
        if rec is None or session_id not in sj.get_list(rec, "session_ids", []):
            continue
        if rec.get("event") == "receipt_refused":
            refused = rec.get("reason")
        elif rec.get("event") in ("receipt_recorded", "dispatch_started"):
            refused = None
    return refused


def refuse_if_unlabelled_inline_fix(plan_dir, session_id, out):
    """A refused receipt can go unread (its output is often discarded), so `apply` repeats it."""
    reason = unlabelled_inline_fix(plan_dir, session_id)
    if reason:
        out({"applied": False, "failure": "inline-fix-unlabelled", "reason": reason}, plan_dir=plan_dir)
        sys.exit(1)


def last_dispatch_harness(plan_dir, session_id):
    """Which harness this session's MOST RECENT `begin` ran under ("claude" /
    "codex"), or None when it has never been dispatched. Read-only.

    `dispatch_started` is written by every `begin` on both harnesses and carries
    `harness: "codex"` only on the codex one, so the newest such event naming this
    session is the whole answer — and, unlike a one-way `codex_dispatch` marker,
    it moves back when the session is next run on Claude."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    seen = None
    for ln in lines:
        rec = sj.load_dict(ln)
        if rec is None:
            continue
        if rec.get("event") == "dispatch_started" and session_id in sj.get_list(rec, "session_ids", []):
            seen = rec.get("harness") or "claude"
    return seen


def last_codex_dispatch(plan_dir, session_id):
    """The MOST RECENT `codex_dispatch` event for `session_id`, or None when the
    session was never dispatched under `--harness codex`.

    Most recent, not any: after a re-dispatch it is the newest attempt that has
    to have produced output. An older attempt's file still sitting on disk must
    not vouch for a newer one that never ran."""
    try:
        lines = (Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    last = None
    for ln in lines:
        rec = sj.load_dict(ln)
        if rec is None:
            continue
        if rec.get("event") == "codex_dispatch" and session_id in sj.get_list(rec, "session_ids", []):
            last = rec
    return last


def missing_dispatch_receipt(plan_dir, session_id):
    """Plain-language refusal reason when this session's closeout has no
    corresponding Codex dispatch, or None to accept it.

    The receipt is the EXISTENCE of the `-o` last-message file named by the
    session's most recent `codex_dispatch` event (or its fallback twin). Not its
    contents: an empty file means `codex exec` ran and said nothing, which is the
    contract's "missing closeout" row — that already blocks the session, with a
    better reason than this check could give.

    No replay bypass is needed and none exists: the runtime directory is either
    the shared plan `_codex/` or the isolated member worktree's ignored
    `.plan-worktrees/codex/`, and only the ONE file for the next attempt is
    overwritten. Re-running `apply` therefore finds the same receipt. Deleting
    that runtime directory by hand costs you the ability to re-apply —
    deliberately, since after that nothing on disk says the dispatch happened."""
    ev = last_codex_dispatch(plan_dir, session_id)
    if ev is None:
        return None                                  # not a Codex-harness session
    # A dispatch on the Claude harness AFTER the last codex_dispatch supersedes
    # it: run.ndjson is append-only so the codex event can never be cleared, and
    # keying off it alone pinned the receipt requirement onto every later
    # closeout for a session that has legitimately re-run on Claude (crash
    # recovery) — measured, a dead codex dispatch permanently wedged
    # apply. The refusal text already scopes itself to `begin --harness codex`;
    # this makes the condition match that scope. A genuine codex-lane apply
    # (last dispatch_started harness == "codex") still demands its receipt.
    if last_dispatch_harness(plan_dir, session_id) == "claude":
        return None
    paths = [p for p in (ev.get("last_message_file"),
                         ev.get("fallback_last_message_file")) if p]
    # The event records the path exactly as `begin` spelled it, which is RELATIVE
    # whenever the plan dir was given relatively (the shipped loop-smoke fixture
    # does exactly that). Resolve relative paths from the plan directory, then
    # retain the legacy basename fallback for shared-plan receipts so `apply`
    # from a different cwd than `begin` is still a valid replay.
    plan_root = Path(plan_dir)
    shared_codex_dir = plan_root / "_codex"
    candidates = []
    for raw_path in paths:
        path = Path(raw_path)
        candidates.append(path)
        if not path.is_absolute():
            candidates.append(plan_root / path)
        candidates.append(shared_codex_dir / path.name)
    if any(path.exists() for path in candidates):
        return None
    return (
        f"no Codex dispatch receipt for {session_id}. `begin --harness codex` emitted a "
        f"dispatch command for this session, but the file `codex exec` writes with `-o` "
        f"({', '.join(paths) or 'none recorded'}) does not exist — so no Codex process "
        f"ran for it, and this closeout cannot have come from the model the plan chose. "
        f"Run the session's `dispatch_cmd` (in run.ndjson's last `codex_dispatch` event) "
        f"and apply its `-o` file. Never write, repair or synthesise a closeout, and "
        f"never do the session's work inline — that silently runs the whole plan on the "
        f"orchestrator's own model."
    )
