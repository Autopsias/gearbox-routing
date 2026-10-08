"""LND-02 (s09) — post-land cleanup and the abandon path.

Two things land here, both now in `plan_teardown.py` (moved from
`land_cleanup.py` / `plan_worktree.py` to clear the latter's 500-LOC bound —
see that module's docstring):

  1. `land`'s STEP 9 (`plan_teardown.step_cleanup`) — the plan worktree
     unlocked, removed and pruned, then its branch deleted local AND remote. A
     dirty plan worktree PRESERVES (§12.4) rather than losing content, reported
     on the `landed` result rather than turned into a park (the land already
     succeeded) — and retried simply by calling `land` again once it is clean.
  2. `retire-plan` (`plan_teardown.retire_plan`, §15's `abandon` row) — the
     branch is kept UNCONDITIONALLY, local and remote; only a clean worktree is
     removed.

Each has a KNOWN POSITIVE (clean -> torn down) beside its refusal (dirty ->
preserved and reported), per this repo's testing convention.

    pytest plan-execute/scripts/test_plan_teardown.py -q
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
import plan_scope as ps  # noqa: E402
import plan_teardown as pt  # noqa: E402
from _land_fixture import build_repo, isolate, run_cli, work  # noqa: E402
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _land_with_ack(iso):
    run_cli("land", iso["plan_dir"])
    run_cli("land-ack", iso["plan_dir"], "--note", "approved")
    return run_cli("land", iso["plan_dir"])


def _no_plan_branches(root):
    return git(["branch", "--list", "plan/*"], root)[1] == ""


# --------------------------------------------------------------------- LND-02.1
def test_a_landed_plan_loses_its_worktree_and_both_branches(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # Pushed once by hand here so the remote-delete path is actually exercised —
    # `land` itself never pushes `plan/<slug>` anywhere (only the merge commit
    # goes to the default branch), so a plan that never did this leaves nothing
    # remote to delete (see `_delete_remote_branch`'s "not-pushed" tolerance).
    git(["push", "-q", "origin", iso["branch"]], fx["root"], check=True)
    rc, out = _land_with_ack(iso)
    assert (rc, out["action"]) == (0, "landed"), out
    assert out["cleanup"]["worktree"] == "removed"
    assert out["cleanup"] == {**out["cleanup"], "branch_local": "deleted",
                              "branch_remote": "deleted"}
    assert not iso["tree"].is_dir()
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] != 0
    assert _no_plan_branches(fx["root"])

    rc, out = run_cli("plans-status", "--json", cwd=fx["root"])
    assert rc == 0
    assert not any(b["branch"] == iso["branch"] for b in out["branches"]), out["branches"]
    assert not any(Path(w["path"]).resolve() == iso["tree"].resolve()
                   for w in out["worktrees"]), out["worktrees"]

    # KNOWN POSITIVE — re-running an already-landed, already-clean plan is a
    # cheap no-op, not a re-walk of the protocol.
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["cleanup"]["worktree"]) == (0, "landed", "removed"), out


# --------------------------------------------------------------------- LND-02.2
def test_step_cleanup_preserves_a_dirty_plan_worktree_and_the_branch_until_retried(fx):
    """Direct unit coverage of `step_cleanup` itself. Through the real `land`
    flow the plan worktree is ALWAYS clean by the time this runs — `step_sync`
    refuses UPFRONT on any dirt (§4.6) and re-checks on EVERY call, so there is
    no way to reach `step_cleanup` with a dirty one through the CLI. This
    proves the refusal `step_cleanup` carries anyway, for whatever future
    caller (or race between the push and this step) needs it.
    """
    iso = isolate(fx, "plan-a")
    landed_sha = work(iso["tree"], "src/f.py", "F = 1\n")
    ctx = lst.context(iso["plan_dir"])
    st = {"landed_sha": landed_sha}
    stray = iso["tree"] / "stray.txt"
    stray.write_text("uncommitted\n")

    cleanup = pt.step_cleanup(iso["plan_dir"], ctx, st)
    assert cleanup["worktree"] == "preserved"
    assert cleanup["branch_local"] == cleanup["branch_remote"] == "worktree-preserved"
    assert iso["tree"].is_dir() and stray.is_file()        # nothing lost
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0
    assert lst.load(iso["plan_dir"], ctx)["cleanup"] == cleanup    # recorded, retrievable

    # KNOWN POSITIVE — clean it up and retry: worktree removed, branch deleted.
    stray.unlink()
    cleanup = pt.step_cleanup(iso["plan_dir"], ctx, st)
    assert cleanup["worktree"] == "removed"
    assert cleanup["branch_local"] == "deleted"
    assert not iso["tree"].is_dir()
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] != 0


# --------------------------------------------------------------------- LND-02.3
def test_retire_plan_drops_a_clean_worktree_and_always_keeps_the_branch(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    git(["push", "-q", "origin", iso["branch"]], fx["root"], check=True)

    rc, out = run_cli("retire-plan", iso["plan_dir"], "--reason", "superseded")
    assert (rc, out["status"]) == (0, "removed"), out
    assert not iso["tree"].is_dir()
    # KEPT — local AND remote, exactly the branches `land`'s cleanup would have
    # deleted had this plan landed instead of being abandoned.
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0
    assert git(["ls-remote", "--exit-code", "--heads", "origin", iso["branch"]],
               fx["root"])[0] == 0

    # Idempotent — retiring an already-retired plan just re-reports, no error.
    rc, out = run_cli("retire-plan", iso["plan_dir"])
    assert rc == 0 and out["status"] in ("recovered", "noop")


# --------------------------------------------------------------------- LND-02.4
def test_retire_plan_preserves_and_reports_a_dirty_worktree(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    stray = iso["tree"] / "stray.txt"
    stray.write_text("in-flight work\n")

    rc, out = run_cli("retire-plan", iso["plan_dir"])
    assert (rc, out["status"]) == (1, "preserved"), out
    assert Path(out["path"]).resolve() == iso["tree"].resolve()
    assert iso["tree"].is_dir() and stray.is_file()
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0

    # KNOWN POSITIVE — clean it and retry: the worktree goes, the branch stays.
    stray.unlink()
    rc, out = run_cli("retire-plan", iso["plan_dir"])
    assert (rc, out["status"]) == (0, "removed"), out
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0


def test_retire_plan_on_a_never_isolated_plan_is_a_noop(fx):
    res = pt.retire_plan(str(fx["plans"]["plan-a"]))
    assert res["status"] == "noop"


# --------------------------------------------------------------------- LND-02.5
def test_a_landed_plans_claim_no_longer_refuses_require_live(fx):
    """s09 rework — the claim `ensure_plan_worktree` wrote at `begin` still named
    the (now torn-down) worktree after `step_cleanup`'s teardown, and
    `require_live` read that stale path as "the checkout vanished" and refused
    every later `plan_ship`/`shipping` call on a plan that had already landed
    successfully. Neuter-checked (2026-08-23): commenting out the
    `pwt._clear_claim_path` call `step_cleanup` makes on a non-preserved
    teardown reproduces `WorktreeError: ... and that directory is gone`.
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = _land_with_ack(iso)
    assert (rc, out["action"], out["cleanup"]["worktree"]) == (0, "landed", "removed"), out
    ps.require_live(iso["plan_dir"])                      # no refusal
    # `land_state.context` must still resolve — the whole reason the claim's
    # `path` is nulled instead of the claim being cleared outright.
    ctx = lst.context(iso["plan_dir"])
    assert ctx is not None and ctx["branch"] == iso["branch"]


# --------------------------------------------------------------------- LND-02.6
def test_an_abandoned_plans_claim_no_longer_refuses_require_live(fx):
    """s09 rework — `retire_plan` re-wrote the claim with the dead worktree path
    right after `remove_plan_worktree` cleared it, so `require_live` refused
    immediately after a clean `retire-plan` — telling the operator to "retire
    the plan's isolation state deliberately", which is exactly what they just
    did. Neuter-checked (2026-08-23): restoring the unconditional
    `"path": state["path"]` in `retire_plan`'s `_write_claim` call reproduces
    the same raise.
    """
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    rc, out = run_cli("retire-plan", iso["plan_dir"], "--reason", "superseded")
    assert (rc, out["status"]) == (0, "removed"), out
    ps.require_live(iso["plan_dir"])                      # no refusal


def test_land_cleanup_also_removes_parallel_group_member_worktrees_and_branches(fx):
    import worktree as wt
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    root, plan_dir = fx["root"], iso["plan_dir"]
    members = {}
    for sid in ("s08", "s09"):
        branch = wt.member_branch("tail", sid, "plan-a")
        mpath = root / wt.WORKTREE_DIRNAME / f"plan-a__tail__{sid}"
        git(["worktree", "add", "-q", "-b", branch, str(mpath), iso["branch"]], root, check=True)
        members[sid] = {"branch": branch, "path": str(mpath)}
    wt._save(plan_dir, "tail", {
        "group": "tail", "repo_root": str(root), "merge_root": str(iso["tree"]),
        "plan_slug": "plan-a", "base_ref": "HEAD", "members": members, "merged": []})
    rc, out = _land_with_ack(iso)
    assert (rc, out["action"]) == (0, "landed"), out
    assert sorted(out["cleanup"]["groups"]["tail"]["removed"]) == ["s08", "s09"], out["cleanup"]
    for m in members.values():
        assert not Path(m["path"]).is_dir()
        assert git(["rev-parse", "--verify", "--quiet", m["branch"]], root)[0] != 0


# --------------------------------------------------------------------- LND-11
# The remote (and local) plan branch is deleted only AT the sha proven to be on
# the landed commit. `pt.git` is wrapped to act at the exact moment between the
# check and the delete — the race each lease exists to win.
def _pushed_and_landed(fx):
    """A plan branch pushed to origin and merged there: `(iso, landed_sha)`."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    git(["push", "-q", "origin", iso["branch"]], fx["root"], check=True)
    git(["push", "-q", "origin", f"{iso['branch']}:main"], fx["root"], check=True)
    return iso, git(["rev-parse", "refs/heads/main"], fx["origin"], check=True)[1]


def _origin_tip(fx, branch):
    rc, out, _ = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], fx["origin"])
    return out if rc == 0 else None


def _hook(monkeypatch, verb, before=None, after=None, fake=None):
    """Run `before`/`after` around the first git call whose verb is `verb`, or
    answer it with `fake` instead of running it."""
    real, fired = pt.git, []

    def wrapped(args, cwd, **kw):
        if args[0] != verb or fired:
            return real(args, cwd, **kw)
        fired.append(args)
        if fake is not None:
            return fake
        if before:
            before()
        out = real(args, cwd, **kw)
        if after:
            after()
        return out
    monkeypatch.setattr(pt, "git", wrapped)
    return fired


def test_a_local_branch_advanced_between_the_check_and_the_delete_survives(fx, monkeypatch):
    iso = isolate(fx, "plan-a")
    landed = work(iso["tree"], "src/f.py", "F = 1\n")
    moved = []
    _hook(monkeypatch, "merge-base",
          after=lambda: moved.append(work(iso["tree"], "src/g.py", "G = 1\n", "late")))
    out = pt._delete_local_branch(fx["root"], iso["branch"], landed)
    assert isinstance(out, dict) and out["kept"], out
    assert git(["rev-parse", iso["branch"]], fx["root"])[1] == moved[0]   # nothing lost


def test_a_merged_local_branch_is_deleted_leased_on_its_tested_sha(fx, monkeypatch):
    iso = isolate(fx, "plan-a")
    landed = work(iso["tree"], "src/f.py", "F = 1\n")
    seen = _hook(monkeypatch, "update-ref")
    assert pt._delete_local_branch(fx["root"], iso["branch"], landed) == "deleted"
    assert seen == [["update-ref", "-d", f"refs/heads/{iso['branch']}", landed]]
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] != 0


def test_a_merged_remote_branch_is_deleted_under_a_lease_on_its_tip(fx, monkeypatch):
    iso, landed = _pushed_and_landed(fx)
    tip = _origin_tip(fx, iso["branch"])
    seen = _hook(monkeypatch, "push")
    assert pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed) == "deleted"
    assert f"--force-with-lease=refs/heads/{iso['branch']}:{tip}" in seen[0]
    assert _origin_tip(fx, iso["branch"]) is None


def test_a_remote_branch_with_a_commit_pushed_after_the_land_is_kept(fx):
    iso, landed = _pushed_and_landed(fx)
    extra = work(iso["tree"], "src/g.py", "G = 1\n", "after the land")
    git(["push", "-q", "origin", iso["branch"]], fx["root"], check=True)
    out = pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed)
    assert "not on the landed commit" in out["kept"], out
    assert _origin_tip(fx, iso["branch"]) == extra


def test_a_remote_branch_that_moves_between_the_check_and_the_push_is_kept(fx, monkeypatch):
    iso, landed = _pushed_and_landed(fx)
    extra = work(iso["tree"], "src/g.py", "G = 1\n", "racing push")
    _hook(monkeypatch, "push", before=lambda: git(
        ["push", "-q", "origin", f"{extra}:refs/heads/{iso['branch']}"], fx["root"], check=True))
    out = pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed)
    assert "moved from" in out["kept"], out
    assert _origin_tip(fx, iso["branch"]) == extra                  # the lease held


def test_a_remote_branch_deleted_between_the_check_and_the_push_is_already_gone(fx, monkeypatch):
    iso, landed = _pushed_and_landed(fx)
    _hook(monkeypatch, "push", before=lambda: git(
        ["update-ref", "-d", f"refs/heads/{iso['branch']}"], fx["origin"], check=True))
    assert pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed) \
        == "already-gone"


def test_an_ls_remote_failure_keeps_the_branch_and_is_not_not_pushed(fx):
    iso, landed = _pushed_and_landed(fx)
    git(["remote", "set-url", "origin", str(fx["tmp"] / "no-such.git")], fx["root"], check=True)
    out = pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed)
    assert out["kept"].startswith("ls-remote failed:"), out
    assert _origin_tip(fx, iso["branch"]) is not None


@pytest.mark.parametrize("verb,fake,reason", [
    ("fetch", (128, "", "network down"), "fetch failed: network down"),
    ("merge-base", (128, "", "bad object"), "ancestry check failed"),
])
def test_a_failed_fetch_or_ancestry_check_keeps_the_branch(fx, monkeypatch, verb, fake, reason):
    iso, landed = _pushed_and_landed(fx)
    _hook(monkeypatch, verb, fake=fake)
    out = pt._delete_remote_branch(fx["root"], "origin", iso["branch"], landed)
    assert reason in out["kept"], out
    assert _origin_tip(fx, iso["branch"]) is not None


def test_no_landed_sha_keeps_the_remote_branch(fx):
    iso, _ = _pushed_and_landed(fx)
    assert pt._delete_remote_branch(fx["root"], "origin", iso["branch"], None) \
        == {"kept": "no landed sha"}
    # KNOWN POSITIVES — the two old answers are unchanged.
    assert pt._delete_remote_branch(fx["root"], None, iso["branch"], None) == "no-remote"
    assert pt._delete_remote_branch(fx["root"], "origin", "plan/never-pushed", None) \
        == "not-pushed"


def test_a_first_call_remote_failure_is_retried_by_finish_then_deleted(fx, monkeypatch):
    import finish
    import finish_ci
    iso, landed = _pushed_and_landed(fx)
    ctx = lst.context(iso["plan_dir"])
    st = {"state": "landed", "landed_on": "origin/main", "landed_sha": landed}
    lst.save(iso["plan_dir"], ctx, st)
    _hook(monkeypatch, "ls-remote", fake=(128, "", "could not resolve host"))
    cleanup = pt.teardown_plan_tree(iso["plan_dir"], ctx, st)
    monkeypatch.undo()
    assert (cleanup["worktree"], cleanup["branch_local"]) == ("removed", "deleted")
    assert cleanup["branch_remote"]["kept"].startswith("ls-remote failed:"), cleanup
    assert _origin_tip(fx, iso["branch"]) is not None

    rc, out = run_cli("land", iso["plan_dir"])          # land names the kept branch
    assert out["action"] == "landed" and "cleanup.branch_remote" in out["message"], out

    monkeypatch.setattr(finish_ci, "ci_verdict", lambda *a, **k: {"verdict": "green"})
    finish.arm(iso["plan_dir"])
    left = finish.finish(iso["plan_dir"], timeout_s=0)["leftovers"]
    assert [(r["target"], r["result"]) for r in left["retried"]] == [("remote-branch", "removed")]
    assert left["plan_branch_remote"] == "deleted"
    assert _origin_tip(fx, iso["branch"]) is None
    assert lst.load(iso["plan_dir"], ctx)["cleanup"]["branch_remote"] == "deleted"
