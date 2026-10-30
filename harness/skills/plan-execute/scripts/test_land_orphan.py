"""LND-01 — a land killed by SIGKILL of `run.py` ALONE, and merge failures that
are not conflicts.

SIGKILL of one pid does not reach its children. The `git merge` the killed land
started lives on as an orphan, writes its merge commit once its
`prepare-commit-msg` hook returns, and dies before it removes MERGE_HEAD
(measured, PR #24). Before this file's fix the next lands said
`merge-in-progress`, `git merge --abort` KEPT the orphan's commit, and the land
after that adopted it as a hand-resolved conflict. If the orphan woke during the
next land's own merge, git failed with `cannot lock ref 'HEAD'` and the land
reported `merge-conflict` with 0 conflicted paths.

    pytest plan-execute/scripts/test_land_orphan.py -q
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
from _land_fixture import (RUN_PY, _arm_slow_commit, build_repo,  # noqa: E402
                           isolate, origin_main, run_cli, work)
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def test_git_alive_tells_a_live_git_from_anything_else():
    live = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL)
    try:
        assert lst.git_alive(live.pid)                   # known positive
    finally:
        live.kill()
        live.wait()
    assert not lst.git_alive(live.pid)                   # the same pid, dead
    assert not lst.git_alive(os.getpid())                # alive, but not git


def test_a_killed_lands_orphan_merge_is_named_never_adopted(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    _arm_slow_commit(fx)
    before = origin_main(fx)
    proc = subprocess.Popen([sys.executable, str(RUN_PY), "land", str(iso["plan_dir"])],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(SCRIPTS))
    pid = None
    try:
        deadline = time.time() + 900                     # a hang guard; the hook holds the merge
        while time.time() < deadline and proc.poll() is None:
            st = lst.load(iso["plan_dir"]) or {}
            if st.get("merge_pid") and lst.merge_in_progress(st["land_path"]):
                break
            time.sleep(0.3)
        proc.kill()                                      # run.py ALONE — what an operator does
        proc.wait(timeout=30)
        st = lst.load(iso["plan_dir"])
        pid, land_path, expected = st.get("merge_pid"), st["land_path"], st["expected"]
        # Known positive: the orphan EXISTS, or every assert below proves nothing.
        assert pid and lst.git_alive(pid), "no orphaned git merge — the kill window was wrong"

        # 1. While it runs: named, with its pid, and no `merge --abort` advice.
        rc, out = run_cli("land", iso["plan_dir"])
        assert (rc, out["kind"]) == (1, "merge-still-running"), out
        assert f"kill {pid}" in out["brief"]

        # 2. It finishes on its own and commits — the measured shape.
        (fx["root"] / ".SLOW").unlink()
        for _ in range(600):
            if not lst.git_alive(pid):
                break
            time.sleep(0.1)
        assert not lst.git_alive(pid)
        head = git(["rev-parse", "HEAD"], land_path)[1]
        assert head != expected                          # the orphan committed

        # 3. Named as unwatched, NOT adopted as a hand resolution — on every entry.
        for _ in range(2):
            rc, out = run_cli("land", iso["plan_dir"])
            assert (rc, out["kind"]) == (1, "merge-unwatched"), out
        assert not lst.load(iso["plan_dir"]).get("merge_sha")
        assert f"git reset --hard {expected}" in out["brief"]

        # 4. The brief's own recovery; the land redoes the merge and it is clean.
        git(["reset", "--hard", expected], land_path, check=True)
        rc, out = run_cli("land-resume", iso["plan_dir"])
        assert (rc, out["action"]) == (1, "land-awaits-review"), out
        st = lst.load(iso["plan_dir"])
        assert st["resolution_required"] is False and "merge_pid" not in st
        rc, _ = run_cli("land-ack", iso["plan_dir"], "--note", "approved")
        rc, out = run_cli("land", iso["plan_dir"])
        assert (rc, out["action"]) == (0, "landed"), out
        assert origin_main(fx) != before
    finally:
        (fx["root"] / ".SLOW").unlink(missing_ok=True)
        if pid and lst.git_alive(pid):
            os.kill(pid, 9)


def test_a_merge_failure_with_nothing_conflicted_is_not_a_conflict(fx):
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    # A held HEAD.lock is what a second git writing in the worktree looks like.
    hook = fx["root"] / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text('#!/bin/sh\ntouch "$(git rev-parse --git-path HEAD.lock)"\n')
    hook.chmod(0o755)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["kind"]) == (1, "merge-failed"), out
    assert "HEAD.lock" in out["brief"] and "NOT a merge conflict" in out["brief"]
    assert "merge_pid" not in lst.load(iso["plan_dir"])  # a merge that ENDED is not an orphan
    # The brief's recovery: remove the cause, abort the half-merge, resume.
    hook.unlink()
    land_path = out["land_worktree"]
    (Path(land_path) / git(["rev-parse", "--git-path", "HEAD.lock"], land_path)[1]).unlink()
    git(["merge", "--abort"], land_path)
    rc, out = run_cli("land-resume", iso["plan_dir"])
    assert (rc, out["action"]) == (1, "land-awaits-review"), out
