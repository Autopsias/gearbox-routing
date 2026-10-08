"""LND-01 — the ORDERED steps of the land protocol. Order is normative (§4).

Authority: ``../references/plan-isolation-contract.md`` §4 (the land protocol),
§5 (approval binding), §8.c (the plan record), §10.1 (gate inputs). ``land.py``
is the entry point and ``land_state.py`` the state spine; this file is the
mechanism. Split for the 500-LOC ratchet, exactly as ``plan_hooks.py`` was.

Every step takes ``(plan_dir, ctx, st)`` and returns **None to continue** or an
action dict to STOP. Every one persists its result before the next runs, so a
crash between any two steps resumes rather than restarts.

MEASURED, NOT ASSUMED (s03b probe 1): ``git worktree add <path> <default>`` while
the default branch is checked out is REFUSED, exit 128 — so the land worktree is
**detached at a captured sha**; and ``git fetch --update-head-ok`` /
``git update-ref refs/heads/<default>`` both exit 0 **while silently
desynchronising the operator's index from HEAD**. Those two are forbidden by name
in §4.2 and appear nowhere in this file.
"""

import uuid
from pathlib import Path

import land_brief as lb
import land_flaky as lfk
import land_repair as lrp
import land_state as lst
import plan_scope as ps
import plan_ship as pship
import run_state_io as rsi
import worktree as wt
from land_state import (conflicted, park,
                        porcelain, rev, save)
from worktree import git


# --------------------------------------------------------------------------
# 1 — sync `<remote>/<default>` INTO the plan branch
# --------------------------------------------------------------------------
def step_sync(plan_dir, ctx, st):
    """A conflict here ALWAYS parks (§4.6). Nothing is ever auto-resolved, and the
    plan worktree is restored with ``merge --abort`` so the park is clean — the
    conflicted tree the operator resolves by hand is the LAND worktree's (step 3),
    which is left in place for exactly that purpose."""
    tree = ctx["plan_tree"]
    if not tree or not Path(tree).is_dir():
        return park(plan_dir, ctx, st, "plan-worktree-missing",
                    f"LAND PARKED — this plan claims the worktree {tree} and it is gone. "
                    "Re-run `begin` (which re-attaches a vanished worktree) before landing.")
    if porcelain(tree).strip():
        return park(plan_dir, ctx, st, "plan-worktree-dirty",
                    f"LAND PARKED — {tree} holds uncommitted content. A land merges a "
                    "COMMITTED tree; uncommitted work exists nowhere else and would be "
                    f"silently left behind. Commit or discard it, then re-run "
                    f"`run.py land {plan_dir}`.")
    if ctx["remote"]:
        git(["fetch", ctx["remote"], ctx["default"]], ctx["root"], timeout=600)
    if rev(tree, ctx["upstream"]) is None:
        return park(plan_dir, ctx, st, "default-unresolved",
                    f"LAND PARKED — {ctx['upstream']} does not resolve. §4.0 resolves the "
                    "default branch ONCE at `begin` and never re-derives it; a default "
                    "that vanished mid-plan is a decision, not a guess.")
    rc, out, err = git(["merge", "--no-edit", ctx["upstream"]], tree, timeout=600)
    if rc != 0:
        conflicts, status = conflicted(tree), porcelain(tree)
        git(["merge", "--abort"], tree)
        return park(plan_dir, ctx, st, "sync-conflict",
                    lb.conflict(tree, plan_dir, conflicts, status),
                    conflicts=conflicts, detail=(err or out)[:2000])
    st["plan_head"] = rev(tree, "HEAD")
    st["synced_at"] = lst.now()
    save(plan_dir, ctx, st)
    return None


# --------------------------------------------------------------------------
# 2 — the UNIQUE, DETACHED land worktree (the lease is already held — §4.1)
# --------------------------------------------------------------------------
def step_worktree(plan_dir, ctx, st):
    """§4.1 — the path is unique per plan (``<slug>__land-<token>``), never a fixed
    ``_land``. With a fixed path two landers collide on ``git worktree add``
    *before* either reaches the lease, and the loser's error is a filesystem error
    whose text says nothing about plans. Uniqueness makes the collision impossible
    and leaves the lease as the single arbiter.

    §1.1a — a SIBLING of the plan worktree, never nested inside it: a nested
    worktree makes its parent unremovable without ``--force`` (measured, exit 128)
    and pollutes every §10.1 gate file set with ``?? nested/``.
    """
    expected = rev(ctx["root"], ctx["upstream"])
    path = st.get("land_path")
    # The candidate is stale if EITHER side moved. Keying only on the default
    # branch left a land worktree holding a merge of an OLD plan head: the plan
    # branch gained a commit, the re-gate ran against a tree that never contained
    # it, and the human was shown a candidate that was not the plan (measured
    # 2026-08-22 — a `git rm` of the file the gate refuses on changed nothing).
    fresh = (st.get("expected") == expected
             and (not st.get("merge_sha") or st.get("merged_plan_head") == st.get("plan_head")))
    if path and Path(path).is_dir() and fresh:
        return None
    if path and Path(path).is_dir():
        if conflicted(path) or lst.merge_in_progress(path):
            # NEVER force-remove a worktree an operator is resolving by hand
            # (§12.4): the content it holds is exactly the git-invisible work a
            # forced removal destroys. They abort or finish it; we do not decide.
            return park(plan_dir, ctx, st, "land-worktree-busy",
                        f"LAND PARKED — {path} holds an unfinished merge, and the candidate "
                        "has moved since it was created, so it must be rebuilt. Refusing to "
                        "force-remove a worktree you may be resolving by hand.\n\n"
                        f"  cd {path}\n  git status\n  git merge --abort   # or finish it\n"
                        f"  run.py land-resume {plan_dir}")
        # §12.4 — force=False, because `teardown_worktree`'s dirty-content
        # refusal IS the safety check and `--force` is how you route around it.
        # The guard above catches a conflict and a half-finished merge; it does
        # NOT catch the plainer case, an operator's untracked notes or an edited
        # tracked file left in a parked land worktree. Measured: both were
        # destroyed here, silently, on the next `land`. `step_finish` has always
        # used force=False for this same call.
        status, extra = wt.teardown_worktree(ctx["root"], Path(path), False)
        git(["worktree", "prune"], ctx["root"])
        if status == "preserved":
            return park(plan_dir, ctx, st, "land-worktree-dirty",
                        lb.preserved(path, plan_dir, extra), detail=extra)
        for key in ("merge_sha", "merged_plan_head", "record_sha", "gate_key"):
            st.pop(key, None)
    token = st.get("land_token") or uuid.uuid4().hex[:8]
    path = str(Path(ctx["root"]) / wt.WORKTREE_DIRNAME / f"{ctx['slug']}__land-{token}")
    # §4.1a step 3 — the CLAIM is written BEFORE the worktree exists, never after.
    # The token is a fresh uuid4, so a crash in the reverse order would leave a
    # REGISTERED worktree that no state file names and no rerun can re-derive:
    # §1.5's permanently untouchable UNKNOWN, created by an ordinary
    # interruption. `plan_worktree._ensure_under_lease` already orders it this
    # way; this is the same rule, not a second one.
    st.update({"land_token": token, "land_path": path, "expected": expected,
               "state": "creating-land-worktree"})
    save(plan_dir, ctx, st)
    rc, out, err = git(["worktree", "add", "--detach", path, expected], ctx["root"])
    if rc != 0:
        # §4.1a rollback — remove what THIS call created and clear the claim
        # while the lease is still held, never leaving it to expire holding a
        # half-built land.
        wt.teardown_worktree(ctx["root"], Path(path), True)
        git(["worktree", "prune"], ctx["root"])
        for key in ("land_path", "land_token"):
            st.pop(key, None)
        return park(plan_dir, ctx, st, "land-worktree-failed",
                    f"LAND PARKED — could not create the land worktree at {path}: "
                    f"{(err or out).strip()}")
    st["state"] = "merging"
    save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_worktree_created", session_ids=[], slug=ctx["slug"],
                  path=path, expected=expected, detached=True)
    return None


# --------------------------------------------------------------------------
# 3 — merge the plan branch into the detached land HEAD (§4.2, §4.4)
# --------------------------------------------------------------------------
def step_merge(plan_dir, ctx, st):
    """§4.4 — ONE strategy: ALWAYS ``--no-ff``. There is no fast-forward path.

    The reason is §5's, not taste: the ack binds to ``{plan_head, main_head,
    gate_digest}`` and a ``--no-ff`` land produces one merge commit that IS the
    approved candidate and can be compared by sha. A fast-forward produces no such
    commit, so "did the thing I approved land?" has no answer expressible in git.
    """
    path = st["land_path"]
    lst.invalidate_stale_ack(st)          # §5.1d, the BEFORE-the-merge comparison
    if conflicted(path):
        return _conflict_park(plan_dir, ctx, st, path, None)
    if lst.merge_in_progress(path):
        return park(plan_dir, ctx, st, "merge-in-progress",
                    f"LAND PARKED — a merge is half-finished in {path} (MERGE_HEAD exists) "
                    "with nothing conflicted. Something was interrupted mid-merge; the mess "
                    "is confined to this land worktree and nothing was pushed.\n\n"
                    f"  cd {path}\n"
                    "  git status              # read ALL of it, not just the U lines\n"
                    "  git merge --abort       # discard the half-merge, then:\n"
                    f"  run.py land-resume {plan_dir}\n\n"
                    "This is NOT auto-concluded: a merge nobody watched finish is exactly "
                    "the tree an approval should not be issued against.")
    head = rev(path, "HEAD")
    if head != st["expected"]:
        if not st.get("merge_sha"):
            # A hand-resolved conflict re-entering via `land-resume` (§4.6 step 4).
            # A resolution ALWAYS changes the plan-side tree, so §4.5a's unattended
            # carve-out can never apply and the ack is dropped here, not later.
            st.update({"merge_sha": head, "merged_plan_head": st.get("plan_head"),
                       "resolution_required": True, "ack": None})
            save(plan_dir, ctx, st)
        return None
    rc, out, err = git(["merge", "--no-ff", "-m", f"plan({ctx['slug']}): land", ctx["branch"]],
                       path, timeout=600)
    if rc != 0:
        return _conflict_park(plan_dir, ctx, st, path, (err or out)[:2000])
    st.update({"merge_sha": rev(path, "HEAD"), "merged_plan_head": st.get("plan_head"),
               "resolution_required": False})
    save(plan_dir, ctx, st)
    return None


def _conflict_park(plan_dir, ctx, st, path, detail):
    """§4.6 — the land worktree is LEFT IN PLACE with the conflict intact."""
    conflicts = conflicted(path)
    return park(plan_dir, ctx, st, "merge-conflict",
                lb.conflict(path, plan_dir, conflicts, porcelain(path)),
                conflicts=conflicts, detail=detail)


# --------------------------------------------------------------------------
# 4 — record-plan (§8.c) and §5.1a's pathspec assertion
# --------------------------------------------------------------------------
def step_record(plan_dir, ctx, st):
    """§8.c — the plan RECORD rides the same push that advances the default branch.

    §5.1a bounds what that adds to the approved candidate: the merge sha PLUS at
    most one commit whose entire diff is confined to ``_plans/<slug>/``. The
    record's CONTENT is deliberately not pre-approved (``run.ndjson`` is appended
    to while the copy runs, so it cannot be even in principle) — the guarantee is
    about the PATH, and it is asserted here rather than assumed.
    """
    path, merge_sha = st["land_path"], st["merge_sha"]
    if not (st.get("record_sha") and rev(path, "HEAD") == st["record_sha"]):
        # DISCARD a record commit a previous entry made and this step REFUSED.
        # Without it the fix below is a deadlock rather than a refusal: the stray
        # path stays inside `merge_sha..HEAD` forever, so removing the hook that
        # caused it changes nothing and the operator has no way out. It also
        # keeps §5.1a's OWN bound — "the merge sha plus AT MOST ONE commit" —
        # which the retry would otherwise break from inside the check that
        # enforces it: `park()` rewrites LAND_NOTICE.txt inside the plan dir, so
        # `record_plan` finds a difference and commits again on every entry.
        # Only a CLEAN worktree is reset: `--hard` would drop an operator's
        # uncommitted edit, and §12.4 says that content is theirs, not ours.
        if rev(path, "HEAD") != merge_sha:
            if porcelain(path).strip():
                return park(plan_dir, ctx, st, "land-worktree-dirty",
                            f"LAND PARKED — {path} holds a record commit this land refused "
                            "AND uncommitted content, so it can be neither reused nor "
                            f"reset:\n\n{porcelain(path).strip()}\n\nDeal with that "
                            f"content, then:\n\n  cd {path}\n"
                            f"  git reset --hard {merge_sha}   # back to the approved merge\n"
                            f"  run.py land-resume {plan_dir}")
            git(["reset", "--hard", merge_sha], path)
        try:
            res = pship.record_plan(path, plan_dir, f"plan({ctx['slug']}): record")
        except wt.WorktreeError as e:
            return park(plan_dir, ctx, st, "record-plan-failed",
                        f"LAND PARKED — record-plan refused in {path}:\n{e}")
        st["record_status"] = res["status"]
    # THE CHECK RUNS BEFORE ITS OWN STATE WRITE, and on EVERY entry. Ordered the
    # other way — `record_sha` first, park after — the refusal fired exactly once:
    # `park()` persisted the sha, the resume short-circuited above it, and the
    # second run walked to `land-awaits-review` offering the human a candidate
    # that still carried the stray path. Measured with a `pre-commit` hook that
    # stages an extra file, which is the formatter-hook shape this exists to
    # catch. `record_sha` is therefore written only once this HOLDS.
    pathspec = f"{ps.PLANS_DIR}/{ctx['slug']}"
    recorded = lst.range_files(path, st["merge_sha"], "HEAD")
    if recorded is None:
        # `range_files` answers None when git itself failed. An empty list here
        # would fail OPEN — "git errored" and "no stray paths" read identically
        # and the candidate walks on to the human ack. Match the sibling
        # (`gate_files.range_files` -> `changed_files`): fail closed, park.
        return park(plan_dir, ctx, st, "record-range-unreadable",
                    f"LAND PARKED — §5.1a's pathspec check could not read "
                    f"{st['merge_sha'][:12]}..HEAD in {path}, so it cannot say whether "
                    "the record commit is confined to the plan's own directory. A check "
                    "that cannot read its inputs never reports clean (§10.1).")
    stray = [p for p in recorded
             if p != pathspec and not p.startswith(pathspec + "/")]
    if stray:
        return park(plan_dir, ctx, st, "record-outside-pathspec",
                    f"LAND PARKED — the plan record commit carries {len(stray)} path(s) "
                    f"outside {pathspec}: {stray[:10]}. §5.1a binds the ack to the merge "
                    "plus at most one commit whose entire diff is inside the plan's own "
                    "directory; anything else is a candidate nobody approved (§5.2).\n\n"
                    "Nothing was pushed and the record commit was NOT kept — the land "
                    f"worktree is reset to the merge on every retry, so this clears the "
                    "moment the thing staging those paths does. It is almost always a "
                    "`pre-commit` hook that formats or adds files:\n\n"
                    f"  cd {path}\n"
                    "  git config --get core.hooksPath; ls .git/hooks\n"
                    f"  run.py land-resume {plan_dir}",
                    stray=stray)
    st["record_sha"] = rev(path, "HEAD")
    save(plan_dir, ctx, st)
    return None


# --------------------------------------------------------------------------
# 6 — the human land decision (§5). NOTHING ELSE REACHES STEP 7.
# --------------------------------------------------------------------------
def step_ack(plan_dir, ctx, st):
    """§5.1d — the binding is CHECKED or it is decoration.

    ``{plan_head, main_head, gate_digest}`` is re-compared against the recorded
    ack immediately before the merge and immediately again before the push. Any
    mismatch returns the plan to ``AWAITS_REVIEW``; it is never reported as a
    warning and never pushed "since we're already here".

    This function is the ONLY gate on the push, and it lives here rather than on
    the dispatch path on purpose: a by-name session dispatch (``begin --sessions
    sNN``) walks straight through checkpoints — measured in this repo — so a land
    checkpoint enforced by dispatch policy would be skippable. Enforced HERE it is
    un-skippable by construction: there is no code path to ``step_push`` that does
    not come through this comparison.
    """
    # §5.1d against GIT, not against `st`. `plan_head` was recorded by `step_sync`
    # at the top of THIS invocation, but the plan branch is a shared ref: an
    # out-of-band move (a peer session, a hook, an amended commit) between then
    # and here would leave the recorded value describing a candidate that no
    # longer exists, and the ack would still match it.
    live = rev(ctx["root"], ctx["branch"])
    if live and live != st.get("plan_head"):
        st["plan_head_moved"] = {"recorded": st.get("plan_head"), "live": live,
                                 "seen_at": lst.now()}
        st["plan_head"] = live
        st["ack"] = None
    cand = lst.candidate(st)
    ack = st.get("ack") or None
    if ack and all(ack.get(k) == v for k, v in cand.items()):
        return None
    # §5.3 shows the human the DIFF against what they approved, so the prior ack
    # has to survive `invalidate_stale_ack` clearing it a step earlier.
    prior = ack or st.get("invalidated_from")
    st.update({"state": "awaiting_review", "ack": None, "invalidated_from": prior,
               "candidate": {**cand, "merge_sha": st.get("merge_sha")}})
    st["brief"] = lb.review({**cand, "merge_sha": st.get("merge_sha")}, st.get("gates") or [],
                            plan_dir, st["land_path"], ctx["root"], prior=prior,
                            warning="\n".join(filter(None, [st.get("at_land_warning"),
                                                            lrp.cost_note(st),
                                                            lfk.flaky_note(st)])) or None)
    save(plan_dir, ctx, st)
    lst.write_notice(plan_dir, st["brief"])
    rsi.log_event(plan_dir, "plan_land_awaits_review", session_ids=[], slug=ctx["slug"],
                  invalidated=bool(prior), **cand)
    return {"action": "land-awaits-review", "candidate": cand, "brief": st["brief"],
            "invalidated_prior_ack": prior,
            "ack_with": f"run.py land-ack {plan_dir} --note '<why>'"}
