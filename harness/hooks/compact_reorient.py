#!/usr/bin/env python3
"""What happens AFTER a compaction, and the note a new session gets.

Split out of compact-policy.py, which had reached its 500-line bound (the
same reason compact_safepoint.py exists). compact-policy.py is the half of this
hook that DECIDES whether a compaction may proceed; `post-compact` and
`session-start` here are the half that RECORDS what happened and RE-ORIENTS
the session that follows.
"""

from __future__ import annotations

import json
import os

from compact_store import (
    clear_deferred,
    kill_switch,
    ledger,
    read_deferred,
    read_policy,
    root,
    tail_lines,
    take_pending,
    write_deferred,
    write_version_file,
)
from context_tokens import compact_summary_chars, context_after_compaction


def _int_or_none(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def cmd_post_compact(payload):
    """PostCompact — ledgers {event:'compacted', type, ctx_before, ctx_after,
    compact_summary_chars}.

    ctx_before comes from THIS SESSION's OWN pending pre-compact record,
    consumed (read + deleted) by session id — never from the last 'pre-compact'
    line in the shared decisions.ndjson. Several sessions append to that one
    ledger and can interleave between one session's PreCompact and its own
    PostCompact call; reading "the last line" would then silently attach
    another session's context to this one.

    ctx_after is DEFERRED, not measured here. It cannot be measured here: the
    compact_boundary record lands after this hook runs, so a transcript read at
    this moment returns the PRE-compaction context and the row claims the
    context did not shrink. This call parks what it knows; measure_deferred()
    below finishes the job on a later prompt.
    """
    session = payload.get("session_id")
    trigger = str(payload.get("trigger") or "unknown")
    # take_pending() runs FIRST, unconditionally — the one choke point every
    # PostCompact call passes through no matter what happens after. A pending
    # record answers only "what did the LAST PreCompact call see", and once
    # THIS PostCompact call has fired that record is stale for every call after
    # it, kill-switched or not. Consuming it here, before kill_switch() or
    # anything else gets a chance to return early, is what stops a record from
    # surviving into a later (possibly manual) compaction's ctx_before — the
    # same leak the blocked-PreCompact branch already closes on its own path.
    pending = take_pending(session)
    if kill_switch():
        return 0
    write_version_file()
    ctx_before = _int_or_none((pending or {}).get("ctx"))
    kind = (read_policy(session) or {}).get("type")
    transcript = payload.get("transcript_path")
    chars = compact_summary_chars(transcript)
    ledger(
        "compacted", session, "trigger:%s" % trigger,
        type=kind, trigger=trigger, ctx_before=ctx_before, ctx_after=None,
        ctx_after_deferred=True, compact_summary_chars=chars,
    )
    write_deferred(session, {
        "ctx_before": ctx_before, "type": kind, "trigger": trigger,
        "compact_summary_chars": chars, "transcript_path": transcript,
        "tries": 0,
    })
    return 0


# The transcript read is bounded but not free, and this runs on EVERY prompt.
# A session whose boundary never becomes readable must stop paying for it.
MAX_MEASURE_TRIES = 8


def measure_deferred(session):
    """Emit the 'compaction-measured' row PostCompact could not write.

    Called from every UserPromptSubmit. Silent and cheap when nothing is owed:
    one read of a path that usually does not exist.

    THE FIRST ATTEMPT NORMALLY FAILS, AND THAT IS CORRECT. The order after a
    compaction is SessionStart -> user prompt -> assistant turn, so at the
    first prompt no post-boundary assistant record exists yet and there is
    nothing to read. The record stays parked and the NEXT prompt measures it.
    A session that never prompts again leaves the compaction unmeasured, which
    is honest — the wrong number it used to write was not.
    """
    owed = read_deferred(session)
    if not isinstance(owed, dict):
        return
    ctx_after, source = context_after_compaction(owed.get("transcript_path"))
    if ctx_after is None:
        tries = owed.get("tries")
        tries = tries + 1 if isinstance(tries, int) and not isinstance(tries, bool) else 1
        if tries >= MAX_MEASURE_TRIES:
            clear_deferred(session)
            ledger("compaction-unmeasurable", session, source,
                   type=owed.get("type"), trigger=owed.get("trigger"),
                   ctx_before=owed.get("ctx_before"), tries=tries)
            return
        owed["tries"] = tries
        write_deferred(session, owed)
        return
    clear_deferred(session)
    ledger(
        "compaction-measured", session, source,
        type=owed.get("type"), trigger=owed.get("trigger"),
        ctx_before=owed.get("ctx_before"), ctx_after=ctx_after,
        compact_summary_chars=owed.get("compact_summary_chars"),
    )


# --- session-start (matcher compact) ----------------------------------------

PLAN_EXECUTE_RUN = "Run: python3 ~/.claude/skills/plan-execute/scripts/run.py plan %s"
TOKEN_LIMIT = 400
BYTES_PER_TOKEN = 4  # the same crude estimate context_tokens.py uses elsewhere


COMPACTED_EVENTS = ("compacted", "compaction-measured")


def _last_compacted_line(session):
    path = os.path.join(root(), "decisions.ndjson")
    for line in reversed(tail_lines(path, 200)):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # 'compaction-measured' carries the REAL ctx_after and is written
        # after 'compacted', so the last row of either kind is the best one.
        if row.get("session") == session and row.get("event") in COMPACTED_EVENTS:
            return row
    return None


def _compaction_line(ctx_before, ctx_after):
    """Both counts trace to a writer of their own (PreCompact / the post-compact
    transcript read) and either can be missing on its own — ctx_before is
    honestly None for EVERY manual compaction (PreCompact only ever runs on the
    `auto` matcher), and ctx_after is None when the post-compact transcript
    can't be read. Print only the half that has a value; never a bare 'None'.
    """
    if ctx_before is not None and ctx_after is not None:
        return "Context at compaction: %s -> %s tokens" % (ctx_before, ctx_after)
    if ctx_after is not None:
        return "Context after compaction: %s tokens" % ctx_after
    if ctx_before is not None:
        return "Context before compaction: %s tokens" % ctx_before
    return None  # both missing: omit the line entirely, not a placeholder


def cmd_session_start(payload):
    """SessionStart(compact) — a short re-orient note, EVERY LINE TRACEABLE TO
    A WRITER. No 'Session decisions' or 'Editing files' section: nothing in
    this design writes them, and an empty labelled section is worse than none.
    A field with no source is OMITTED, never printed as a placeholder.
    """
    session = payload.get("session_id")
    if kill_switch():
        return 0
    policy = read_policy(session) or {}
    lines = []
    kind = policy.get("type")  # written by `prompt`'s classify() (compact-policy.py)
    if kind:
        lines.append("Session type: %s" % kind)
    plan_dir = policy.get("plan_dir")
    if plan_dir:
        lines.append("Active plan: %s" % plan_dir)
    phase = policy.get("safe_point_phase")  # written by `safe-point --phase`
    if phase:
        lines.append("Current phase: %s" % phase)
    if plan_dir:
        lines.append("Next command: %s" % (PLAN_EXECUTE_RUN % plan_dir))
    note = policy.get("safe_point_note")  # written by `safe-point --note`
    if note:
        lines.append("Last note: %s" % note)
    compacted = _last_compacted_line(session)
    if compacted:
        line = _compaction_line(compacted.get("ctx_before"), compacted.get("ctx_after"))
        if line:
            lines.append(line)
    if not lines:
        return 0
    text = "\n".join(lines)
    budget = TOKEN_LIMIT * BYTES_PER_TOKEN
    encoded = text.encode("utf-8")
    if len(encoded) > budget:
        text = encoded[:budget].decode("utf-8", "ignore")
    print(json.dumps(
        {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
        separators=(",", ":"),
    ))
    return 0
