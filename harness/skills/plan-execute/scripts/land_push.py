"""LND-01 — ADVANCING THE DEFAULT BRANCH: the push, the bounded re-sync, the
final record, and the close-out. Contract §4.2, §4.5, §4.7, §4.8, §8.c2, §12.3.

Its own module because it is the only part of the land that WRITES anywhere
outside this plan's own worktrees, and because ``land_steps.py`` sits against
this repo's 500-LOC ratchet. Everything here is downstream of a valid human ack:
``land_steps.step_ack`` is the only route in, and it re-checks §5.1d's binding on
every attempt.

THE SHAPE §4.2 FIXED, because probe 1 measured the obvious alternatives failing:
``git push --force-with-lease=<default>:<EXPECTED> <remote> HEAD:refs/heads/<default>``
from a DETACHED worktree. `git branch -f`, `git push . HEAD:refs/heads/<default>`,
`git fetch --update-head-ok` and `git update-ref refs/heads/<default>` are
forbidden by name — the last two because they SUCCEED, with exit 0, while
silently desynchronising the operator's index from HEAD.
"""

from pathlib import Path

import land_brief as lb
import land_gate
import land_state as lst
import plan_ship as pship
import run_state_io as rsi
import ship_locks as sl
import worktree as wt
from land_state import MAX_RESYNC, PLAN_LANDS_REF, is_stale, park, rev, save
from land_steps import (_conflict_park, step_ack, step_merge, step_record,
                        step_sync, step_worktree)
from worktree import git


def _push_once(ctx, st, path):
    """§4.2's compare-and-swap, or §4.8's durable non-branch ref in a repo with no
    remote. ``--force-with-lease=<default>:<EXPECTED>`` was measured in BOTH
    directions — exit 0 when ``origin/<default>`` is still at ``EXPECTED``, and
    ``! [rejected] HEAD -> main (stale info)`` when another party moved it first.
    A lease that cannot refuse is not a lease.

    ``refs/plan-lands/<slug>`` is safe where §4.2's forbidden ``update-ref
    refs/heads/<default>`` is not, and the difference is the whole point: it is
    NOT a branch, is checked out by nobody, and moving it cannot desynchronise any
    working tree. §4.8a measured that WITHOUT it, routine cleanup plus a `gc`
    destroys the merge commit outright — `cat-file` exit 128, a whole plan's work
    gone with no error at any step.
    """
    if not ctx["remote"]:
        return git(["update-ref", f"{PLAN_LANDS_REF}/{ctx['slug']}", "HEAD"], path)
    return git(["push", f"--force-with-lease={ctx['default']}:{st['expected']}",
                ctx["remote"], f"HEAD:refs/heads/{ctx['default']}"], path, timeout=900)


def _land_target(ctx):
    """The ref a landed merge must be an ancestor of: the remote default branch,
    or §4.8's durable non-branch ref in a repo with no remote."""
    return (f"{ctx['remote']}/{ctx['default']}" if ctx["remote"]
            else f"{PLAN_LANDS_REF}/{ctx['slug']}")


def _verify_landed(ctx, st, path):
    """``git push`` reporting success is NOT proof the commit is on the branch —
    measured twice in this repo, with ``update-ref`` and ``fetch
    --update-head-ok`` both exiting 0 (the second printing nothing at all) and
    leaving the commit off the branch with phantom staged deletions. So ask git,
    after re-fetching, instead of trusting the printed sha."""
    if ctx["remote"]:
        git(["fetch", ctx["remote"], ctx["default"]], ctx["root"], timeout=600)
    target = _land_target(ctx)
    head = rev(path, "HEAD")
    return git(["merge-base", "--is-ancestor", head, target], ctx["root"])[0] == 0, head, target


def already_landed(ctx, st):
    """Has the irreversible write ALREADY happened, according to git?

    Once it has, re-walking the steps before the push is not merely wasteful, it
    is WRONG: `origin/<default>` now contains this plan's work, so §10.1's
    surface `<expected>..HEAD` is EMPTY and the re-gate parks a land that
    SUCCEEDED. Measured 2026-08-22 by resuming after a refused teardown — the
    second invocation reported `gate-empty-surface` on a plan already on main.

    Asked of git IN THE ROOT REPO, never of the record and never of the land
    worktree: `landed_sha` alone is the printed sha, and a printed sha is not
    proof the commit is on the branch — but requiring the WORKTREE to still
    exist reintroduced the very defect this fixes. An operator who removes a
    preserved land worktree by hand removes nothing git needs to answer this
    question, and a resume that then re-walks the protocol parks a land that is
    already on the target (measured: `land-worktree-failed`, then
    `gate-empty-surface` forever).
    """
    if not st.get("landed_sha"):
        return False
    if ctx["remote"]:
        git(["fetch", ctx["remote"], ctx["default"]], ctx["root"], timeout=600)
    return git(["merge-base", "--is-ancestor", st["landed_sha"], _land_target(ctx)],
               ctx["root"])[0] == 0


def _resync(plan_dir, ctx, st, path):
    """§4.5 — a ``(fetch first)`` rejection is not a failure: fetch, merge, re-gate,
    push again with a FRESHLY captured ``EXPECTED``.

    §4.5a decides whether that may retry unattended. BOTH must hold: the plan-side
    tree unchanged (the merge needed no resolution) AND an IDENTICAL gate digest.
    The human approved *this plan's work against a passing gate set*; neither fact
    changed, and the moved ``main_head`` is RECORDED in the ack rather than hidden.
    Either condition failing returns the plan to ``AWAITS_REVIEW``, where §5.3's
    diff view shows the new commits and the changed results — not the plan again.
    Without the carve-out, attempt 2 would be unreachable and the bound of 3 would
    guard a loop that cannot spin.
    """
    git(["fetch", ctx["remote"], ctx["default"]], ctx["root"], timeout=600)
    before_digest = st.get("gate_digest")
    rc, out, err = git(["merge", "--no-ff", "--no-edit", ctx["upstream"]], path, timeout=600)
    if rc != 0:
        return _conflict_park(plan_dir, ctx, st, path, (err or out)[:2000])
    st.update({"expected": rev(ctx["root"], ctx["upstream"]),
               "merge_sha": rev(path, "HEAD"), "resolution_required": False})
    st.pop("record_sha", None)
    save(plan_dir, ctx, st)
    for step in (step_record, land_gate.step_gate):
        blocked = step(plan_dir, ctx, st)
        if blocked is not None:
            return blocked
    # §4.5a's carve-out needs BOTH facts to still hold, and "the plan-side tree is
    # unchanged" is a claim about git, not about `resolution_required`. A plan
    # branch that moved out of band during the push window produces a different
    # merged candidate, and reusing the ack across it lands work nobody approved.
    live_plan = rev(ctx["root"], ctx["branch"])
    unattended = (not st.get("resolution_required")
                  and st.get("gate_digest") == before_digest
                  and live_plan == st.get("plan_head"))
    if unattended and st.get("ack"):
        # RECORDED, never hidden (§4.5a): the ack now names the sha the candidate
        # was actually rebuilt onto and the merge commit that carries it.
        st["ack"].update({"main_head": st["expected"], "merge_sha": st["merge_sha"]})
        st["ack"].setdefault("resyncs", []).append(
            {"at": lst.now(), "main_head": st["expected"], "merge_sha": st["merge_sha"]})
        save(plan_dir, ctx, st)
        rsi.log_event(plan_dir, "plan_land_resync_unattended", session_ids=[],
                      slug=ctx["slug"], main_head=st["expected"], gate_digest=before_digest)
        return None
    st["ack"] = None
    return step_ack(plan_dir, ctx, st)


def _lease_park(plan_dir, ctx, st, when):
    return park(plan_dir, ctx, st, f"lease-lost-{when}",
                f"LAND PARKED — this plan no longer holds {ctx['resource']} ({when} the "
                "push). §3.5: an expired lease is taken over only with the takeover "
                "recorded on both sides, and the displaced holder REFUSES rather than "
                "writing. A post-mutation loss is a park, never a rollback — the write "
                "already happened and you need to see both, not one of them undone.")


def step_push(plan_dir, ctx, st):
    path = st["land_path"]
    if st.get("landed_sha"):
        # A crash between the push and the state write, or a re-entry after a
        # REFUSED teardown, must not push a second time. Ask git whether the
        # commit is already on the target rather than trusting the record: that
        # is the same discipline `_verify_landed` applies, pointed at resume.
        ok, _head, _target = _verify_landed(ctx, st, path)
        if ok:
            return None
        st.pop("landed_sha", None)
    for attempt in range(1, MAX_RESYNC + 1):
        if not sl.our_lease(plan_dir, ctx["resource"]):          # §3.6, before
            return _lease_park(plan_dir, ctx, st, "before")
        blocked = step_ack(plan_dir, ctx, st)                    # §5.1d, before the push
        if blocked is not None:
            return blocked
        rc, out, err = _push_once(ctx, st, path)
        text = (err or "") + (out or "")
        if rc == 0:
            if not sl.our_lease(plan_dir, ctx["resource"]):      # §3.6, after
                return _lease_park(plan_dir, ctx, st, "after")
            ok, head, target = _verify_landed(ctx, st, path)
            if not ok:
                return park(plan_dir, ctx, st, "push-unverified",
                            f"LAND PARKED — the push exited 0 but {head[:12]} is NOT an "
                            f"ancestor of {target}. A command reporting success is not "
                            "proof the commit is on the branch — measured twice. Nothing "
                            "was cleaned up; the branch is kept.")
            st.update({"landed_sha": head, "landed_base": st["expected"],
                       "landed_on": target, "attempts": attempt})
            save(plan_dir, ctx, st)
            return None
        st.setdefault("push_attempts", []).append(
            {"at": lst.now(), "attempt": attempt, "expected": st["expected"],
             "rc": rc, "output": text[:2000]})
        save(plan_dir, ctx, st)
        if not is_stale(text) or attempt == MAX_RESYNC:
            # §4.7 — cleanup is CONDITIONAL on a successful push. A rejection keeps
            # the branch (local AND remote) and the land worktree. The work exists
            # only there, and a delete that runs before the push is how a rejected
            # land loses it (§2.3).
            return park(plan_dir, ctx, st, "push-rejected",
                        lb.rejected("the default branch moved past the 3-attempt bound"
                                    if is_stale(text) else "refused by the remote",
                                    text, path, ctx["branch"], plan_dir, ctx["remote"]),
                        attempts=attempt, detail=text[:2000])
        resynced = _resync(plan_dir, ctx, st, path)
        if resynced is not None:
            return resynced
    return park(plan_dir, ctx, st, "resync-exhausted",
                lb.rejected(f"{MAX_RESYNC} re-syncs exhausted", "", path, ctx["branch"],
                            plan_dir, ctx["remote"]), attempts=MAX_RESYNC)


# --------------------------------------------------------------------------
# Close-out — cleanup is CONDITIONAL on a VERIFIED push (§4.7)
# --------------------------------------------------------------------------
def publish_final_record(plan_dir, ctx, st, *, message=None):
    """§8.c2 step 2 — the FINAL record's own, second default-branch mutation.

    The land's own closeout and everything written AFTER the land push cannot ride
    the first push, and the draft that stopped at "committed during cleanup" would
    have left the final record on a detached worktree that cleanup then removes,
    on no branch at all. §8.c2 calls that a second silent loss and fixes it by
    ordering rather than hope.

    It obeys every rule the first push does — the lease is already held, a
    FRESHLY captured ``EXPECTED``, a ``record-plan`` staging confined to
    ``_plans/<slug>/``, a compare-and-swap push — and it is NOT re-gated and NOT
    re-approved. That exemption is earned MECHANICALLY, not asserted: the commit
    it adds is verified to touch only paths under the record pathspec, and a
    commit naming anything else PARKS instead of pushing. No code can ride it.

    Exposed as a function, not just a step, because s09's cleanup entries need the
    same publish and must not re-derive it.
    """
    path, pathspec = st["land_path"], f"{lst.ps.PLANS_DIR}/{ctx['slug']}"
    if not Path(path).is_dir():
        # Resuming after the land worktree is gone (removed by hand after a
        # preserved park). The record either already rode its own push — asked
        # of git, not of the state file — or it cannot be published from here.
        frs = st.get("final_record_sha")
        if frs and git(["merge-base", "--is-ancestor", frs, _land_target(ctx)],
                       ctx["root"])[0] == 0:
            return None
        return park(plan_dir, ctx, st, "final-record-worktree-gone",
                    f"LAND PARKED — the merge is on {st.get('landed_on')}, but the final "
                    f"record is not published and the land worktree {path} no longer "
                    "exists to publish it from. Re-run `run.py land-resume "
                    f"{plan_dir}` after restoring the worktree, or publish the plan "
                    "record by hand and re-run.")
    for attempt in range(1, MAX_RESYNC + 1):
        if not sl.our_lease(plan_dir, ctx["resource"]):
            return _lease_park(plan_dir, ctx, st, "before-final-record")
        st["expected"] = rev(ctx["root"], ctx["upstream"])
        before = rev(path, "HEAD")
        try:
            res = pship.record_plan(path, plan_dir, message or
                                    f"plan({ctx['slug']}): final record")
        except wt.WorktreeError as e:
            return park(plan_dir, ctx, st, "final-record-failed",
                        f"LAND PARKED AFTER A SUCCESSFUL LAND — the merge is on "
                        f"{st['landed_on']}, but record-plan refused in {path}:\n{e}\n\n"
                        "Nothing was cleaned up. Fix and re-run `run.py land-resume "
                        f"{plan_dir}`.")
        if res["status"] == "already-recorded" and before == st.get("final_record_sha"):
            return None                       # nothing new to publish
        head = rev(path, "HEAD")
        recorded = lst.range_files(path, before, head)
        if recorded is None:
            return park(plan_dir, ctx, st, "final-record-range-unreadable",
                        f"LAND PARKED — §8.c2's pathspec check could not read "
                        f"{str(before)[:12]}..{str(head)[:12]} in {path}, so it cannot say "
                        "whether the final-record commit is confined to the plan's "
                        "directory. \"git errored\" and \"no stray paths\" must never be "
                        "the same answer (§10.1) — fail closed, push nothing.")
        stray = [p for p in recorded
                 if p != pathspec and not p.startswith(pathspec + "/")]
        if stray:
            return park(plan_dir, ctx, st, "final-record-outside-pathspec",
                        f"LAND PARKED — the final-record commit carries {len(stray)} path(s) "
                        f"outside {pathspec}: {stray[:10]}. §8.c2 exempts this push from the "
                        "re-gate and the human ack ONLY because its diff is verified to "
                        "contain no code. It does, so it parks.", stray=stray)
        rc, out, err = _push_once(ctx, st, path)
        if rc == 0:
            st["final_record_sha"] = head
            save(plan_dir, ctx, st)
            rsi.log_event(plan_dir, "plan_land_final_record", session_ids=[],
                          slug=ctx["slug"], sha=head, attempt=attempt,
                          landed_on=st.get("landed_on"))
            return None
        text = (err or "") + (out or "")
        if not is_stale(text) or attempt == MAX_RESYNC:
            return park(plan_dir, ctx, st, "final-record-rejected",
                        lb.rejected("the final-record push was refused", text, path,
                                    ctx["branch"], plan_dir, ctx["remote"]),
                        attempts=attempt, detail=text[:2000])
        # A `(fetch first)` here re-syncs under §4.5's bound, exactly as the land
        # push does. No re-gate and no re-approval: the pathspec check above is
        # re-run on the next attempt against a freshly captured EXPECTED.
        git(["fetch", ctx["remote"], ctx["default"]], ctx["root"], timeout=600)
        rc, out, err = git(["merge", "--no-ff", "--no-edit", ctx["upstream"]], path,
                           timeout=600)
        if rc != 0:
            return _conflict_park(plan_dir, ctx, st, path, (err or out)[:2000])
    return park(plan_dir, ctx, st, "final-record-rejected",
                lb.rejected(f"{MAX_RESYNC} final-record re-syncs exhausted", "", path,
                            ctx["branch"], plan_dir, ctx["remote"]), attempts=MAX_RESYNC)


def step_final_record(plan_dir, ctx, st):
    return publish_final_record(plan_dir, ctx, st)


def step_finish(plan_dir, ctx, st):
    """Remove the LAND worktree — §8.c2 step 4, and LAST, because step 2 happens
    inside it. The plan worktree, the plan branch and the remote branch are §15's
    cleanup lifecycle (s09) and are deliberately untouched here: deleting a branch
    before the record is published destroys the only other copy of it (§2.3).

    ``teardown_worktree`` is reused, never re-derived — it is the single
    implementation of §12.3's ``unlock`` -> ``remove`` -> ``prune`` order, and its
    dirty-content refusal (``preserved``) is the safety check, not an obstacle to
    route around with ``--force`` (§12.4).
    """
    catch = lb.catch_up(git, ctx["root"], ctx["default"], ctx["remote"], ctx["slug"])
    before = rev(ctx["root"], ctx["default"])
    status, extra = wt.teardown_worktree(ctx["root"], Path(st["land_path"]), False)
    git(["worktree", "prune"], ctx["root"])
    if status == "preserved":
        # §12.4 — the dirty-content refusal IS the safety check, not an obstacle
        # to route around with `--force`. And it is not a successful land either:
        # marking `landed` here would short-circuit every later invocation and
        # hide an unresolved worktree behind a green state forever.
        st.update({"landed_at": lst.now(), "land_worktree_teardown":
                   {"status": status, "detail": extra}})
        return park(plan_dir, ctx, st, "land-worktree-preserved",
                    f"THE LAND SUCCEEDED — {st['landed_sha'][:12]} is on {st['landed_on']} "
                    "and the final record is published. Only the teardown refused.\n\n"
                    + lb.preserved(st["land_path"], plan_dir, extra)
                    + "\n\nThe push is idempotent and will not repeat.", detail=extra)
    st.update({"state": "landed", "landed_at": lst.now(),
               "land_worktree_teardown": {"status": status, "detail": extra},
               "local_default_before": before,
               "local_default_after": rev(ctx["root"], ctx["default"])})
    st["brief"] = lb.landed(st["landed_sha"], st["landed_on"] if ctx["remote"] else None,
                            catch, extra=f"{PLAN_LANDS_REF}/{ctx['slug']}")
    st["final_record_published"] = bool(st.get("final_record_sha"))
    st.pop("park", None)
    save(plan_dir, ctx, st)
    lst.write_notice(plan_dir, st["brief"])
    unchanged = st["local_default_before"] == st["local_default_after"]
    rsi.log_event(plan_dir, "plan_landed", session_ids=[], slug=ctx["slug"],
                  merge_sha=st["landed_sha"], landed_on=st["landed_on"],
                  local_default_unchanged=unchanged, land_worktree=status)
    return {"action": "landed", "merge_sha": st["landed_sha"], "landed_on": st["landed_on"],
            "brief": st["brief"], "catch_up": catch,
            "local_default_unchanged": unchanged, "land_worktree_teardown": status}


step_gate = land_gate.step_gate


# The ORDERED protocol, §4.1 -> §8.c2. Order is normative; `land.land` walks it
# and stops at the first step that returns an action.
STEPS = (step_sync, step_worktree, step_merge, step_record, land_gate.step_gate,
         step_ack, step_push, step_final_record)
