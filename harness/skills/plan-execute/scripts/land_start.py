"""LND-13 — the land-repair session may not start before its fast-forward.

``land_repair._pending`` writes ``land.json["start_pending"]`` with each
directive. ``start_blocker`` reads it for ``plan`` (through ``land.land_action``)
and ``begin`` (through ``require_started``). Neither writes ``land.json``.
Only land, under its lease, ends it (``settle``, ``green``) or replaces it.
"""

import shlex
from pathlib import Path

import article_block as ab
import land_state as lst
from dispatch import TERMINAL_STATES
from land_state import rev
from worktree import git


def _status(plan_dir, sid):
    return ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_text()).get(sid)


def settle(plan_dir, st):
    """Land's only clear of ``start_pending``: its session is terminal or retired
    (WONTFIX). A new candidate alone never clears it: a land that issues no new
    directive would leave the TODO session dispatchable on a tree without it."""
    sp = st.get("start_pending")
    if sp and _status(plan_dir, sp["sid"]) in TERMINAL_STATES:
        st["start_pending"] = None


def green(plan_dir, ctx, st, flaky=()):
    """The re-gate is green, so no directive follows. A repair session not yet
    added is dropped. One still TODO parks ``repair-not-needed``: land may not reach
    awaits-review with a session open, and it cannot retire the session itself (it
    holds the ship lock that ``retire-session``'s mutation guard refuses under).
    ``green`` also makes ``start_blocker`` send ``plan``/``begin`` to retire-session.
    ``flaky`` names the round's failures when nothing changed since them
    (``land_flaky.unrepaired``): then they are flaky, never "nothing left to repair".
    Any other open status (DOING, AWAITS_REVIEW, ...) parks ``repair-in-progress``."""
    sp = st.get("start_pending")
    status = _status(plan_dir, sp["sid"]) if sp else "-"
    why = (f"Nothing changed since its round failed, so {', '.join(flaky)} failed, then "
           "passed: flaky, not repaired" if flaky else "Nothing is left for it to repair")
    if status is None:
        st["start_pending"] = None
        lst.save(plan_dir, ctx, st)
    elif status == "TODO":
        st["start_pending"] = {**sp, "green": True, "why": why}
        return lst.park(plan_dir, ctx, st, "repair-not-needed", (
            f"LAND PARKED — the re-gate is green, but repair session {sp['sid']} is still "
            f"TODO. Nothing was pushed. {why}. Retire it, then land again:\n\n  "
            f"{retire_cmd(plan_dir, sp['sid'])}\n  {land_cmd(plan_dir)}"), flaky=list(flaky))
    elif status != "-":
        return lst.park(plan_dir, ctx, st, "repair-in-progress", (
            f"LAND PARKED — the re-gate is green, but repair session {sp['sid']} is "
            f"{status}. Nothing was pushed. Wait for {sp['sid']}, then land again:\n\n  "
            f"{land_cmd(plan_dir)}"))
    return None


def open_repair(plan_dir, st):
    """The sid ``start_pending`` names when that session is added and not terminal."""
    sid = (st.get("start_pending") or {}).get("sid")
    status = _status(plan_dir, sid) if sid else None
    return sid if status is not None and status not in TERMINAL_STATES else None


def land_cmd(plan_dir):
    return shlex.join(["run.py", "land", str(plan_dir)])


def retire_cmd(plan_dir, sid):
    return shlex.join(["run.py", "retire-session", str(plan_dir), "--session", sid,
                       "--reason", "land went green without it"])


def unstarted(plan_dir, st, sid):
    """``start_pending`` still names ``sid`` and it never started: it is not added
    yet or TODO, and its fast-forward never ran (the plan tree lacks the candidate,
    so ``begin`` refused it). A repair made after the fast-forward counts."""
    sp = st.get("start_pending") or {}
    return (sp.get("sid") == sid and _status(plan_dir, sid) in (None, "TODO")
            and git(["merge-base", "--is-ancestor", str(sp.get("candidate_head")), "HEAD"],
                    sp["plan_tree"])[0] != 0)


def start_blocker(plan_dir, sids):
    """LND-13: ``(sid, message)`` when one of ``sids`` is the repair session land
    directed and the plan tree does not hold the candidate head yet, or land went
    green without it (then the message names ``retire-session``); else None.
    Matches the sid land recorded, never an ``lr`` prefix. Reads only."""
    ctx = lst.context(plan_dir)
    st = (lst.load(plan_dir, ctx) or {}) if ctx else {}
    sp = st.get("start_pending")
    if not sp or sp.get("sid") not in sids:
        return None
    retire = f"`{retire_cmd(plan_dir, sp['sid'])}`"
    if sp.get("green"):
        return sp["sid"], (
            f"REPAIR NOT NEEDED — land's re-gate went green while {sp['sid']} had not "
            f"started. {sp.get('why') or 'Nothing is left for it to repair'}. Retire "
            f"it:\n\n  {retire}")
    head, tree, land = sp["candidate_head"], sp["plan_tree"], st.get("land_path")
    # An unreachable commit survives until gc, so "exists" alone is not enough:
    # the candidate must still be the land tree's HEAD.
    if (not head or not land or rev(land, "HEAD") != head
            or git(["cat-file", "-e", f"{head}^{{commit}}"], ctx["root"])[0] != 0):
        return sp["sid"], (
            f"REPAIR NOT STARTED — {sp['sid']} repairs land's candidate {str(head)[:12]}, "
            "and that candidate no longer exists (a later land rebuilt its tree). Run "
            f"`{land_cmd(plan_dir)}` for a new directive. If land is green or has "
            f"landed, nothing needs repair: retire it instead with {retire}")
    if git(["merge-base", "--is-ancestor", head, "HEAD"], tree)[0] == 0:
        return None
    return sp["sid"], (
        f"REPAIR NOT STARTED — {sp['sid']} repairs land's candidate {head[:12]}, and the "
        f"plan tree {tree} does not hold it yet: the fast-forward did not run, or it "
        f"failed. Run it, then dispatch again:\n\n  {sp['command']}")


def require_started(plan_dir, sids):
    """``begin``'s guard: refuse before any worktree or status change."""
    hit = start_blocker(plan_dir, sids)
    if hit:
        raise SystemExit(f"refusing to dispatch {hit[0]}: {hit[1]}")
