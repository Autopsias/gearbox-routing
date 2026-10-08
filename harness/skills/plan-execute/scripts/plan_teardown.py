"""LND-02 — this plan's two ways out: `land-success` cleanup (this module's
original `step_cleanup`) and the `abandon` path (`remove_plan_worktree` +
`retire_plan`, moved here from ``plan_worktree.py`` — see WHY THIS MODULE
HOLDS BOTH, below). Both are worktree/branch/lock teardown for a plan; neither
is the isolation SETUP that ``plan_worktree.py`` still owns.

Authority: ``../references/plan-isolation-contract.md`` §15. `land-success`:
``unlock -> remove -> prune`` the plan worktree (§12.3, via
``worktree.teardown_worktree``), THEN delete the plan branch — local and
remote — because a landed branch's only copy is now on the default branch.
That is exactly what makes deleting it safe in ``step_cleanup`` and not in
``remove_plan_worktree`` below (also `retire-plan`'s primitive, where §15.1
keeps the branch unconditionally). `abandon`: mark the plan retired
unconditionally, remove the worktree only when clean, ALWAYS keep the branch
(local and remote) — the operator's decision to make, not this module's.

WHY THIS MODULE HOLDS BOTH. A file named for "land cleanup" alone would be the
wrong home for `retire_plan` — abandon is explicitly NOT landing (§15's
`abandon` row is its own case, not a land outcome). Renamed from
``land_cleanup.py`` to ``plan_teardown.py`` (s09) once `remove_plan_worktree`
and `retire_plan` moved in from ``plan_worktree.py`` — a straight relocation,
not a redesign, done to clear ``plan_worktree.py``'s 500-LOC file-size bound.

**Why ``step_cleanup`` does NOT call ``remove_plan_worktree``, despite the
obvious reuse.** That function also clears the plan's RUNTIME claim
(``plan_worktree._clear_claim``) on any non-preserved teardown.
``land_state.context`` — which every `land`/`land-status`/`land-ack` call
resolves through — reads its slug FROM that claim, so clearing it mid-cleanup
made every later read of this plan's own land history (``lst.load``) return
``None``, breaking idempotent resume, `land-status`, and ten pre-existing
tests that inspect ``landed_sha``/``final_record_sha`` after a landed result.
MEASURED 2026-08-22 in this session's own fixture. The worktree teardown is
reused at the ``worktree.py`` layer instead — one level below where the claim
gets touched.

**Why branch deletion verifies ancestry itself instead of plain ``git branch
-d``.** ``-d`` checks merge-into-HEAD (or a configured upstream), and HEAD in
the PRIMARY checkout is deliberately never advanced by land (§4.2's forbidden
list — see ``land_brief.catch_up``): the push lands on the REMOTE, and the
operator's local default branch stays exactly where it was. Plain ``-d`` run
there refuses a branch that unambiguously IS merged — MEASURED as the very
first thing this module's own tests hit. Ancestry is checked directly against
``st["landed_sha"]`` (the exact commit ``land_push._verify_landed`` already
proved is on the target), and only then deleted — the same safety property
``-d`` provides, pointed at the ref this protocol actually cares about instead
of one it promises never to move. Both deletes are LEASED on the exact sha
that check tested (LND-11): ``update-ref -d <ref> <sha>`` locally,
``push --force-with-lease=<ref>:<sha>`` remotely, so a branch that moved after
the check survives instead of losing the commits that moved it.

A worktree teardown refusal (uncommitted, untracked, or ignored-but-locally-
created content — ``worktree.teardown_worktree``'s own check, §12.4) PRESERVES
the worktree exactly like every other teardown in this repo, and on the same
reasoning preserves the branch too: a branch is not deleted out from under a
worktree still holding content nobody looked at. The land itself already
succeeded by the time this runs, so a preserved cleanup is reported on the
`landed` result, never turned into a park — a stuck cleanup after a successful
land is visible and retried simply by calling `land` again (idempotent,
`step_cleanup` is safe to re-run), never silently dropped.
"""

import shlex
from pathlib import Path

import land_state as lst
import plan_worktree as pwt
import run_state_io as rsi
import ship_locks as sl
import ship_state_io as ssio
import worktree as wt
from worktree import git, repo_root


def _delete_local_branch(root, branch, landed_sha):
    """Deletes the EXACT sha the ancestry check tested (``update-ref -d <ref>
    <old>``): a branch advanced after the check no longer matches and survives."""
    rc, tip, _ = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)
    if rc != 0:
        return "absent"                       # already gone — a retried cleanup
    if not landed_sha or git(["merge-base", "--is-ancestor", tip, landed_sha], root)[0] != 0:
        return {"kept": f"{branch} is not (yet) an ancestor of the landed commit"}
    rc, _, err = git(["update-ref", "-d", f"refs/heads/{branch}", tip], root)
    return "deleted" if rc == 0 else {"kept": err}


def _remote_tip(root, remote, branch):
    """``(rc, sha)`` from ``ls-remote --exit-code``: rc 2 is "no such branch";
    any other non-zero rc is a failed look (network, auth), never an absence."""
    ref = f"refs/heads/{branch}"
    rc, out, err = git(["ls-remote", "--exit-code", "--heads", remote, ref], root)
    if rc != 0:
        return rc, err
    sha = next((ln.split()[0] for ln in out.splitlines() if ln.split()[1:] == [ref]), None)
    return (0, sha) if sha else (2, None)


def _delete_remote_branch(root, remote, branch, landed_sha):
    """Deletes the remote plan branch only when its tip is proven to be on the
    landed commit, and only AT that tip (LND-11). ``no-remote`` and
    ``not-pushed`` (never pushed, §4.8a) are tolerated silently, as before.

    The tip ``ls-remote`` reports is the ONE sha this function trusts: it is
    fetched, tested for ancestry, and used as the push lease's <expect>, so a
    branch that moved in between is refused by the remote itself. Every doubt
    keeps the branch with a reason: ``{"kept": "<reason>"}``."""
    if not remote:
        return "no-remote"
    rc, tip = _remote_tip(root, remote, branch)
    if rc == 2:
        return "not-pushed"
    if rc != 0:
        return {"kept": f"ls-remote failed: {tip}"}
    if not landed_sha:
        return {"kept": "no landed sha"}
    rc, _, err = git(["fetch", "--no-tags", "--quiet", remote, f"refs/heads/{branch}"], root)
    if rc != 0:
        return {"kept": f"fetch failed: {err}"}
    rc, _, err = git(["merge-base", "--is-ancestor", tip, landed_sha], root)
    if rc == 1:
        return {"kept": f"{remote}/{branch} at {tip[:12]} has commits not on the landed commit"}
    if rc != 0:
        return {"kept": f"ancestry check failed for {tip[:12]}: {err}"}
    rc, _, err = git(["push", f"--force-with-lease=refs/heads/{branch}:{tip}", remote,
                      f":refs/heads/{branch}"], root)
    if rc == 0:
        return "deleted"
    rc, now = _remote_tip(root, remote, branch)       # refused: look once more
    if rc == 2:
        return "already-gone"
    if rc == 0 and now != tip:
        return {"kept": f"{remote}/{branch} moved from {tip[:12]} to {now[:12]}; not deleted"}
    return {"kept": f"push --delete refused: {err}"}


def retry_remote_branch(plan_dir, ctx, st):
    """`finish`'s retry of a remote branch a landed cleanup KEPT (for example a
    transient ls-remote or fetch failure), alone: the worktree and the local
    branch are already gone. Same checks and lease; the answer is recorded."""
    prior = (st.get("cleanup") or {}).get("branch_remote")
    result = _delete_remote_branch(ctx["root"], ctx["remote"], ctx["branch"],
                                   st.get("landed_sha"))
    if result == "not-pushed" and isinstance(prior, dict):
        result = "already-gone"          # it was there once (kept): now it is gone
    st.setdefault("cleanup", {})["branch_remote"] = result
    lst.save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_cleanup", session_ids=[], slug=ctx["slug"],
                  branch_remote=result)
    return result


def manual_remote_delete(plan_dir, ctx, st):
    """For a remote plan branch still KEPT after a finished apply-mode finish:
    the sentence that gives the exact manual delete, never a promised retry.
    None when the branch is not kept or finish has not finished in apply mode."""
    if not isinstance(((st or {}).get("cleanup") or {}).get("branch_remote"), dict):
        return None
    fin = ssio.read_json_with_bak(pwt.plan_state_dir(ctx["root"], ctx["slug"])
                                  / "finish.json") or {}
    if not (fin.get("finished_at") and fin.get("mode") == "apply"):
        return None
    branch, remote, landed = ctx["branch"], ctx["remote"], st.get("landed_sha")
    ref = f"refs/heads/{branch}"
    if remote is None:                 # the remote itself was removed after the plan
        return (f"finish will not retry it again, and this repository no longer has a "
                f"remote, so {ref} is gone with it: there is nothing remote left to delete.")
    rc, tip = _remote_tip(ctx["root"], remote, branch)
    if rc == 2:
        return (f"finish will not retry it again, and {remote} no longer has {ref}: "
                "it is already gone, so there is nothing to delete.")
    if rc != 0:
        return (f"finish will not retry it again, and the branch tip could not be read "
                f"({tip}). First look it up: `{shlex.join(['git', 'ls-remote', remote, ref])}`. "
                f"Then fetch that sha, check it is an ancestor of {landed}, and delete the "
                f"branch with a --force-with-lease push at exactly that sha.")
    fetch = shlex.join(["git", "fetch", remote, ref])
    check = shlex.join(["git", "merge-base", "--is-ancestor", tip, str(landed)])
    push = shlex.join(["git", "push", f"--force-with-lease={ref}:{tip}", remote, f":{ref}"])
    return (f"finish will not retry it again. Fetch the tip (`{fetch}`), check it is on "
            f"the landed commit (`{check}`), then delete it yourself: {push}")


def _cleanup_groups(plan_dir):
    """Tear down every parallel-group member this plan left behind, through
    ``wt.cleanup_group`` (refuses dirty, `branch -d` never `-D`, never `rm -rf`).
    ``{group: {status, removed, preserved, branches_kept}}`` — {} when none."""
    out = {}
    for sp in sorted((Path(plan_dir) / "_worktrees").glob("*.json")):
        state = ssio.read_json_with_bak(sp)
        group = (state or {}).get("group")
        if not group:
            continue
        res = wt.cleanup_group(plan_dir, group)
        out[group] = {k: res.get(k) for k in ("status", "removed", "preserved", "branches_kept")}
    return out


def step_cleanup(plan_dir, ctx, st):
    """Run (or retry) the cleanup and record it on the land state.

    Never a park: by the time this is reachable the land already succeeded —
    only what it tears down can still refuse, and a refusal there is a report,
    not a rollback of an irreversible push that already happened.

    §15's `land-success` row: "Lease -> released". The land resource itself
    is released by `land.land`'s own `finally` right after this returns; the
    two releases below catch anything ELSE this plan might still hold (a stray
    parallel-group lease from an interrupted run, the `begin` pidfile) so a
    landed plan holds NOTHING, not just the one lease `land` knows by name.
    """
    cleanup = teardown_plan_tree(plan_dir, ctx, st)
    sl.release_all_ship_locks(plan_dir)
    rsi.release_lock(plan_dir)
    return cleanup


def teardown_plan_tree(plan_dir, ctx, st):
    """`step_cleanup` minus its two releases — the plan worktree, the group
    members and the landed plan branch, recorded on the land state.

    `finish` retries a `preserved` cleanup through THIS, never `step_cleanup`:
    finish holds one `git:` lease from its preflight through its checkout step
    and no callee may release it (finish-contract.md, "Lock-ownership rule").
    """
    root = ctx["root"]
    # Recomputed STRUCTURALLY, like every other teardown in this repo: after a
    # whole-project move the re-resolved root is correct while a recorded
    # absolute path is not.
    path = pwt.plan_worktree_path(root, ctx["slug"])
    # Parallel-group member worktrees/branches go FIRST: `branch -d` judges them
    # against the plan worktree (their merge root), which is still there now.
    groups = _cleanup_groups(plan_dir)
    status, extra = wt.teardown_worktree(root, path, False)
    git(["worktree", "prune"], root)
    cleanup = {"worktree": status, "at": lst.now()}
    if groups:
        cleanup["groups"] = groups
    if status == "preserved":
        cleanup["detail"] = extra
        cleanup["branch_local"] = cleanup["branch_remote"] = "worktree-preserved"
    else:
        # The directory is gone but the claim `ensure_plan_worktree` wrote at
        # `begin` still names it — never cleared here (see this module's
        # docstring: a full `_clear_claim` would drop `branch` too, and
        # `land_state.context` reads that on every later call). Nulling only
        # `path` is enough for `require_live` to stop refusing a landed plan.
        pwt._clear_claim_path(root, ctx["slug"])
        cleanup["branch_local"] = _delete_local_branch(root, ctx["branch"], st.get("landed_sha"))
        cleanup["branch_remote"] = _delete_remote_branch(root, ctx["remote"], ctx["branch"],
                                                         st.get("landed_sha"))
    st["cleanup"] = cleanup
    lst.save(plan_dir, ctx, st)
    rsi.log_event(plan_dir, "plan_land_cleanup", session_ids=[], slug=ctx["slug"],
                  worktree=cleanup["worktree"], branch_local=cleanup["branch_local"],
                  branch_remote=cleanup["branch_remote"])
    return cleanup


# ---- Teardown — §12.3's unlock -> remove -> prune, and its two recoveries -
def remove_plan_worktree(plan_dir, *, force=False):
    """Remove this plan's worktree. The BRANCH is kept: an unlanded plan branch
    is the only copy of work nobody merged, and deleting it is §15.1's
    operator-named decision, not a lifecycle transition. ``wt.teardown_worktree``
    is the single implementation of the fixed order — ``repair`` (the
    moved-worktree recovery) -> the dirty-content refusal -> ``unlock`` ->
    ``remove``, and ``unlock`` + ``prune`` when the directory was deleted by
    hand. Reused, never re-derived."""
    state = pwt.load_state(plan_dir)
    if not state:
        return {"status": "noop", "reason": f"no {pwt.STATE_FILENAME}"}
    root = repo_root(plan_dir) or Path(state["repo_root"])
    # Recomputed STRUCTURALLY, like `cleanup_group`: after a whole-project move
    # the re-resolved root is correct while the recorded absolute string is not.
    path = pwt.plan_worktree_path(root, state["plan_slug"])
    status, extra = wt.teardown_worktree(root, path, force)
    git(["worktree", "prune"], root)
    state["teardown"] = {"status": status, "detail": extra, "at": lst.now()}
    pwt._save(plan_dir, state)
    if status != "preserved":
        pwt._clear_claim(root, state["plan_slug"])
    rsi.log_event(plan_dir, "plan_worktree_teardown", session_ids=[],
                  slug=state["plan_slug"], status=status, path=str(path),
                  branch=state["branch"], detail=extra)
    return {"status": status, "path": str(path), "branch": state["branch"],
            "detail": extra}


# ---- Abandon — §15's `abandon` row (LND-02, s09) -------------------------
def retire_plan(plan_dir, *, reason=None):
    """The operator ends this plan without landing it.

    Two INDEPENDENT actions, not one: the plan is marked retired unconditionally
    (item 2's "mark the plan retired in its state"); the worktree is removed
    ONLY when ``wt.teardown_worktree``'s dirty-content refusal lets it (§12.4 —
    never overridden with ``force`` here, so a dirty abandon PRESERVES its
    content and reports the exact path instead of losing it). The BRANCH —
    local and remote — is ALWAYS kept (§15.1): an abandoned plan's branch is the
    only copy of work nobody merged, and deleting it is an operator decision
    naming the branch, never a lifecycle transition this function makes on its
    own. Reuses ``remove_plan_worktree`` for the worktree half rather than
    re-deriving it; only the branch-kept default that function already has
    applies here too, so nothing extra is needed for that half.

    Idempotent — retiring an already-retired plan just re-reports its current
    worktree state.
    """
    state = pwt.load_state(plan_dir)
    if not state:
        return {"status": "noop", "reason": f"no {pwt.STATE_FILENAME} — this plan was never isolated"}
    result = remove_plan_worktree(plan_dir, force=False)
    root = repo_root(plan_dir) or Path(state["repo_root"])
    slug = state["plan_slug"]
    state = pwt.load_state(plan_dir) or state          # re-read: teardown just wrote to it
    state["retired"] = {"reason": reason, "at": lst.now(), "worktree": result["status"]}
    pwt._save(plan_dir, state)
    # §15's table keeps `plan-state/<slug>/` for `abandon` (stamped
    # `state: abandoned`), never blank the way a landed plan's claim is
    # cleared — a later `plan-adopt`/sweep needs to see a DELIBERATE
    # retirement, not an orphan (§1.5's UNKNOWN, untouchable-by-default class).
    # Written regardless of the teardown result above: a preserved (dirty)
    # worktree is still a retired plan. `path` is carried over ONLY when the
    # teardown above left the directory in place ("preserved") — anything else
    # means it is gone, and re-stamping the dead path here (s09 rework: this is
    # exactly what made `require_live` refuse right after a clean retire-plan)
    # would undo the null `remove_plan_worktree` just wrote.
    pwt._write_claim(root, slug, {
        "plan_slug": slug, "branch": state["branch"],
        "path": state["path"] if result["status"] == "preserved" else None,
        "base_ref": state.get("base_ref"), "default_branch": state.get("default_branch"),
        "created_at": state.get("created_at"), "plan_dir": str(plan_dir),
        "state": "abandoned", "abandoned_at": lst.now(), "reason": reason,
    })
    sl.release_all_ship_locks(plan_dir)
    rsi.release_lock(plan_dir)
    rsi.log_event(plan_dir, "plan_retired", session_ids=[], slug=slug,
                  status=result["status"], path=result["path"], branch=result["branch"],
                  reason=reason)
    return {"status": result["status"], "path": result["path"], "branch": result["branch"],
            "detail": result["detail"], "reason": reason}
