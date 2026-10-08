"""SWP-01 — `plans-sweep`: report (and, on `--apply`, remove) stale
plan-lifecycle state across a repo's whole `_plans/` — dead `.lock` plans,
leftover plan worktrees, and orphan `plan/*` branches.

Every check runs against a REAL git repository (no mocked subprocess), the
same discipline `test_registry.py`/`test_worktree.py` already use. Imports
its fixture helpers (`_make_plan`, `_todo_session`, `_write_lock`,
`_dead_pid`) from `test_registry.py`, exactly as `test_registry_owners.py`
already does.

Three fixtures this suite is built to catch — each measured against real
git 2.48.1 before the fix existed (see `worktree.py`'s `cleanup_group`):

  * a `--lock`ed worktree whose directory was deleted BY HAND — `git worktree
    prune` refuses a locked entry, so without an `unlock` first the dead
    registration survives FOREVER, and this proves the sweep instead
    RECOVERS it (a second sweep no longer reports it).
  * a worktree that MOVED along with its whole project root — git's own
    admin files hold absolute paths on both sides, so both go stale even
    though the worktree is intact at its unchanged relative position; this
    proves `git worktree repair` is actually exercised, not merely present
    in the source (the removal would fail with a broken-link error without
    it — see the assertion in that test).
  * an UNKNOWN-class branch (no owner anywhere) — `--apply` must print it
    and never touch it, whatever else it cleans up.

Run: pytest skills/plan-execute/scripts/test_registry_sweep.py -q
"""
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import article_block as ab  # noqa: E402
import registry as reg  # noqa: E402
import registry_sweep as sw  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import worktree as wt  # noqa: E402
from test_registry import _dead_pid  # noqa: E402
from test_registry import _make_plan  # noqa: E402
from test_registry import _todo_session  # noqa: E402
from test_registry import _write_lock  # noqa: E402
from test_worktree import _repo as _wt_repo  # noqa: E402


def _owned_worktree(root, plan_dir, group, sid, *, lock=False):
    """A real, `plan/<group>/<sid>` worktree branched off `main`, plus the
    `_worktrees/<group>.json` ownership record `registry_owners.py` reads —
    the same two-sided fixture shape `test_registry_owners.py` already uses,
    factored out here since this suite needs it repeatedly."""
    branch = wt.member_branch(group, sid)
    path = root / wt.WORKTREE_DIRNAME / group / sid
    path.parent.mkdir(parents=True, exist_ok=True)
    args = ["worktree", "add", "-q", *(["--lock"] if lock else []), "-b", branch, str(path), "main"]
    wt.git(args, root, check=True)
    state_path = plan_dir / "_worktrees" / f"{group}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = ssio.read_json_with_bak(state_path) or {"group": group, "repo_root": str(root),
                                                     "members": {}}
    state["members"][sid] = {"path": str(path), "branch": branch}
    ssio.durable_write_json(state_path, state)
    return path, branch


# --------------------------------------------------------------------------
# collect_sweep — read-only report
# --------------------------------------------------------------------------
def test_collect_sweep_reports_stale_lock_dead_worktree_and_leftover_branch(tmp_path):
    root = _wt_repo(tmp_path)
    stale_lock_plan = _make_plan(root, "stale-lock-plan", _todo_session())
    _write_lock(stale_lock_plan, pid=_dead_pid(), age_s=rsi.STALE_LOCK_SECONDS + 10)

    done_plan = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan / "PLAN.html", "s01", status="DONE")
    _owned_worktree(root, done_plan, "g", "s01")

    data = sw.collect_sweep(root, root / "_plans")
    assert [p["plan"] for p in data["stale_lock_plans"]] == ["stale-lock-plan"]
    assert any(w["owner"]["plan"] == "done-plan" and w["class"] == "leftover"
               for w in data["worktrees"])
    assert any(b["owner"]["plan"] == "done-plan" and b["class"] == "leftover"
               for b in data["branches"])
    # An ACTIVE plan's own branch/worktree never shows up here at all — only
    # non-active state is sweep-worthy.
    active_plan = _make_plan(root, "active-plan", _todo_session())
    _owned_worktree(root, active_plan, "g2", "s01")
    data = sw.collect_sweep(root, root / "_plans")
    assert not any(w["owner"] and w["owner"]["plan"] == "active-plan" for w in data["worktrees"])
    assert not any(b["owner"] and b["owner"]["plan"] == "active-plan" for b in data["branches"])


# --------------------------------------------------------------------------
# apply_sweep — dead locks + LEFTOVER teardown
# --------------------------------------------------------------------------
def test_apply_removes_dead_lock_and_clean_leftover_worktree_and_branch(tmp_path):
    root = _wt_repo(tmp_path)
    stale_lock_plan = _make_plan(root, "stale-lock-plan", _todo_session())
    _write_lock(stale_lock_plan, pid=_dead_pid(), age_s=rsi.STALE_LOCK_SECONDS + 10)

    done_plan = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan / "PLAN.html", "s01", status="DONE")
    path, branch = _owned_worktree(root, done_plan, "g", "s01")

    result = sw.apply_sweep(root, root / "_plans")
    assert result["locks_removed"] == [str(stale_lock_plan)]
    assert not (stale_lock_plan / ".lock").exists()
    cleaned = next(g for g in result["groups_cleaned"] if g["group"] == "g")
    assert cleaned["result"]["removed"] == ["s01"]
    assert cleaned["result"]["preserved"] == []
    assert not path.exists()
    assert wt.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] != 0

    # A second sweep sees nothing left to report or apply.
    data2 = sw.collect_sweep(root, root / "_plans")
    assert data2["stale_lock_plans"] == []
    assert not any(w["owner"] and w["owner"]["plan"] == "done-plan" for w in data2["worktrees"])


def test_apply_preserves_a_leftover_worktree_holding_real_content(tmp_path):
    """The same S02 §6 refusal `worktree.py`'s own cleanup tests already
    prove, exercised through the sweep: real content must never be
    destroyed just because its owning plan is done."""
    root = _wt_repo(tmp_path)
    done_plan = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan / "PLAN.html", "s01", status="DONE")
    path, branch = _owned_worktree(root, done_plan, "g", "s01")
    (path / "scratch.txt").write_text("uncommitted real output\n")

    result = sw.apply_sweep(root, root / "_plans")
    cleaned = next(g for g in result["groups_cleaned"] if g["group"] == "g")
    assert cleaned["result"]["preserved"], cleaned
    assert path.is_dir() and (path / "scratch.txt").exists()
    assert wt.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] == 0


# --------------------------------------------------------------------------
# FIXTURE 1 — a `--lock`ed worktree whose directory was deleted BY HAND
# --------------------------------------------------------------------------
def test_apply_recovers_a_locked_worktree_deleted_by_hand(tmp_path):
    root = _wt_repo(tmp_path)
    done_plan = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan / "PLAN.html", "s01", status="DONE")
    path, branch = _owned_worktree(root, done_plan, "g", "s01", lock=True)
    shutil.rmtree(path)

    # NEUTER PROBE: plain `git worktree prune` alone (no unlock) is the
    # documented dead end this fixture exists to catch — it must NOT reclaim
    # a locked-and-deleted entry. If this assertion ever fails, the fixture
    # no longer proves what this test claims.
    wt.git(["worktree", "prune"], root)
    assert any(w["branch"] == branch for w in reg.list_worktrees(root)), (
        "fixture invalid: plain prune already reclaimed the locked entry"
    )

    data_before = sw.collect_sweep(root, root / "_plans")
    assert any(w["owner"] and w["owner"]["plan"] == "done-plan" for w in data_before["worktrees"])

    result = sw.apply_sweep(root, root / "_plans", data=data_before)
    cleaned = next(g for g in result["groups_cleaned"] if g["group"] == "g")
    assert cleaned["result"]["recovered"] == ["s01"], cleaned
    assert cleaned["result"]["preserved"] == []
    assert not any(w["branch"] == branch for w in reg.list_worktrees(root))
    assert wt.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] != 0

    # Recovered, not reported forever: a second sweep sees nothing left here.
    data_after = sw.collect_sweep(root, root / "_plans")
    assert not any(w["owner"] and w["owner"]["plan"] == "done-plan" for w in data_after["worktrees"])


# --------------------------------------------------------------------------
# FIXTURE 2 — a worktree MOVED along with its whole project root
# --------------------------------------------------------------------------
def test_apply_repairs_a_worktree_moved_with_its_project_root(tmp_path):
    root = _wt_repo(tmp_path)
    done_plan = _make_plan(root, "done-plan", _todo_session())
    ab.apply_mutation(done_plan / "PLAN.html", "s01", status="DONE")
    path, branch = _owned_worktree(root, done_plan, "g", "s01")

    new_root = tmp_path / "moved"
    shutil.move(str(root), str(new_root))
    new_plan_dir = new_root / "_plans" / "done-plan"

    # Confirm the move actually broke git's own bookkeeping (both admin
    # files hold absolute paths) — otherwise this fixture proves nothing.
    porcelain = reg.list_worktrees(new_root)
    moved_row = next(w for w in porcelain if w["branch"] == branch)
    assert moved_row["prunable"] is True, "fixture invalid: the move did not break git's admin"

    res = wt.cleanup_group(new_plan_dir, "g")
    # If `git worktree repair` were skipped, `_porcelain` would raise inside
    # the worktree (its own `.git` file is broken too — see worktree.py's
    # `teardown_worktree` docstring) and this session would show up
    # PRESERVED with an error, not removed.
    assert res["removed"] == ["s01"], res
    assert res["preserved"] == [], res
    assert wt.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], new_root)[0] != 0


# --------------------------------------------------------------------------
# FIXTURE 3 — an UNKNOWN-class branch: `--apply` prints it, never touches it
# --------------------------------------------------------------------------
def test_apply_leaves_an_unknown_branch_alone(tmp_path):
    root = _wt_repo(tmp_path)
    # A `plan/*` branch with NO `_worktrees/*.json` anywhere claiming it —
    # unowned, unreapable (ADR-0002).
    orphan_branch = "plan/orphan-group/s99"
    wt.git(["branch", orphan_branch], root, check=True)

    data = sw.collect_sweep(root, root / "_plans")
    row = next(b for b in data["branches"] if b["branch"] == orphan_branch)
    assert row["class"] == "unknown"

    result = sw.apply_sweep(root, root / "_plans", data=data)
    assert orphan_branch in result["skipped_unknown"]
    assert wt.git(["rev-parse", "--verify", "--quiet", f"refs/heads/{orphan_branch}"], root)[0] == 0
