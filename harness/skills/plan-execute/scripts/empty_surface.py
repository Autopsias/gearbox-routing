"""An EMPTY review surface: not a transport failure, so never retried as one.

Both halves of one marker, like `verifier_park`: the gate prints `MARKER` on
stdout (`llm_review_surface.resolve_surface`) and still exits 2, so the
three-outcome contract is unchanged; verify reads it (`verifier_park.indeterminate`)
and calls `block`.

Why (2026-10-14, kuzushi-belt-lock s02): a session that shipped through merged
PRs got three identical empty diffs and a halt blaming a transport that never
failed. The remedy is the closeout field `review_base` (`review_context.declared_base`).
"""
import sys

MARKER = "SURFACE: empty"


def report(level, surface, scope, exclude):
    """Gate side: the marker on stdout for verify, the reason on stderr."""
    print(MARKER)
    print(message(level, surface, scope, exclude), file=sys.stderr)


def message(level, surface, scope, exclude):
    """The INDETERMINATE text for a surface with nothing in it — naming whichever
    of the two filters was applied.

    NO SILENT SHRINKING on this branch either. The land re-gate excludes
    `_plans/<slug>/` and sets NO scope, so a land whose surface came out empty
    BECAUSE of the exclusion used to say nothing about it at all, and the
    operator was sent looking for a missing --base that was never the cause.
    """
    msg = (f"llm-review-{level}: INDETERMINATE — the reviewed surface is EMPTY "
           f"({surface} has 0 changed files and there are 0 untracked files), so a "
           f"green verdict would say nothing about the session's work. If the session "
           f"COMMITTED its changes, pass --base <ref> (or PLAN_EXECUTE_REVIEW_BASE) "
           f"naming the commit it started from. A plan-execute session that shipped "
           f"through MERGED PRs sets `review_base` in _closeouts/<sid>.json instead.")
    if scope:
        msg += (f" NOTE: review_scope {', '.join(scope)} was applied -- if the "
                f"session's work lives outside it, the SCOPE is wrong; an empty "
                f"scoped surface is refused here rather than passed.")
    if exclude:
        msg += (f" NOTE: {', '.join(exclude)} was EXCLUDED from the surface; if the "
                f"session's work lives there, the exclusion is wrong.")
    return msg


def block(plan_dir, session_id, state, gate, excerpt, stdout):
    """Verify side: None unless the gate printed `MARKER` at the start of a stdout
    line. Otherwise block NOW, naming the remedy: the same surface comes back on
    every run, so a retry is waste. Not charged -- the agent's work is not in question.
    """
    if not any(ln.startswith(MARKER) for ln in (stdout or "").splitlines()):
        return None
    import article_block as ab  # noqa: PLC0415
    import run_state_io as rsi  # noqa: PLC0415
    from verify_paths import _html, _save_state  # noqa: PLC0415

    state["outcome"] = "blocked"
    _save_state(plan_dir, session_id, state)
    reason = (f"verify gate {gate} found an EMPTY review surface (not a transport "
              f"failure, never a finding; rework {state['rework_count']}/"
              f"{state['max_rework']} untouched). If {session_id} shipped its work "
              f"through merged PRs, add \"review_base\": \"<commit its first PR "
              f"started from>\" to _closeouts/{session_id}.json (the closeout digest "
              f"ignores it), then `clear-halt` and `verify-begin --session "
              f"{session_id} --resume`. If the work is uncommitted elsewhere, or "
              f"outside review_scope, fix that instead.")
    ab.apply_mutation(_html(plan_dir), session_id, status="BLOCKED", note=reason)
    rsi.set_halt(plan_dir, f"{session_id}: {reason}", session_id)
    rsi.log_event(plan_dir, "verify_empty_surface", session_ids=[session_id],
                  gate=gate, rework_count=state["rework_count"], excerpt=excerpt[:200])
    return {"action": "halted", "session": session_id, "gate": gate, "reason": reason,
            "empty_surface": True, "rework_count": state["rework_count"]}
