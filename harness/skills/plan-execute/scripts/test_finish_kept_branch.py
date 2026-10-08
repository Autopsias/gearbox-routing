"""Kept-remote-branch tests for `finish` (split out of test_plan_teardown.py,
which is locked for this session).

    pytest plan-execute/scripts/test_finish_kept_branch.py -q
"""

import sys
from pathlib import Path

import shlex

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
import plan_teardown as pt  # noqa: E402
from _land_fixture import build_repo, run_cli  # noqa: E402
from test_plan_teardown import _hook, _origin_tip, _pushed_and_landed  # noqa: E402
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def test_a_still_kept_branch_after_a_finished_apply_finish_gets_the_manual_command(fx, monkeypatch):
    import finish
    import finish_ci
    iso, landed = _pushed_and_landed(fx)
    ctx = lst.context(iso["plan_dir"])
    kept = {"kept": "ls-remote failed: x"}
    st = {"state": "landed", "landed_on": "origin/main", "landed_sha": landed,
          "cleanup": {"worktree": "removed", "branch_local": "deleted", "branch_remote": kept}}
    lst.save(iso["plan_dir"], ctx, st)
    assert pt.manual_remote_delete(iso["plan_dir"], ctx, st) is None   # not finished yet
    _, out = run_cli("land", iso["plan_dir"])
    assert "`run.py finish` retries" in out["message"], out
    monkeypatch.setattr(finish_ci, "ci_verdict", lambda *a, **k: {"verdict": "green"})
    _hook(monkeypatch, "ls-remote", fake=(128, "", "could not resolve host"))
    finish.arm(iso["plan_dir"])
    done = finish.finish(iso["plan_dir"], timeout_s=0)
    assert "--force-with-lease=refs/heads/" in done["brief"], done["brief"]   # only 1st ls-remote fails
    assert "<tip>" not in done["brief"]
    monkeypatch.undo()
    cmd = pt.manual_remote_delete(iso["plan_dir"], ctx, lst.load(iso["plan_dir"], ctx))
    assert f"--force-with-lease=refs/heads/{iso['branch']}:{_origin_tip(fx, iso['branch'])}" in cmd
    _, out = run_cli("land", iso["plan_dir"])
    assert "retries" not in out["message"] and "--force-with-lease" in out["message"], out


def test_a_kept_remote_branch_that_is_not_there_any_more_reads_already_gone(fx):
    iso, landed = _pushed_and_landed(fx)
    ctx = lst.context(iso["plan_dir"])
    st = {"state": "landed", "landed_sha": landed, "cleanup": {"branch_remote": {"kept": "x"}}}
    git(["push", "-q", "origin", f":refs/heads/{iso['branch']}"], fx["root"], check=True)
    assert pt.retry_remote_branch(iso["plan_dir"], ctx, st) == "already-gone"


def _kept_after_finish(fx, monkeypatch, rc, tip, **ctx_extra):
    """A kept remote branch after a finished apply finish; ls-remote answers (rc, tip)."""
    import finish
    iso, landed = _pushed_and_landed(fx)
    ctx = {**lst.context(iso["plan_dir"]), **ctx_extra}
    st = {"state": "landed", "landed_sha": landed, "cleanup": {"branch_remote": {"kept": "x"}}}
    finish._save(finish.context(iso["plan_dir"]),
                 {"finished_at": "2026-10-04T00:00:00Z", "mode": "apply"})
    monkeypatch.setattr(pt, "_remote_tip", lambda *a: (rc, tip))
    return pt.manual_remote_delete(iso["plan_dir"], ctx, st), ctx, landed


def _commands(text):
    return [shlex.split(c) for c in text.split("`")[1::2]] + [shlex.split(text.rsplit(": ", 1)[1])]


def test_a_readable_tip_gives_fetch_then_ancestry_then_the_leased_delete(fx, monkeypatch):
    tip = "a" * 40
    msg, ctx, landed = _kept_after_finish(fx, monkeypatch, 0, tip)
    ref = f"refs/heads/{ctx['branch']}"
    assert _commands(msg) == [["git", "fetch", "origin", ref],
                              ["git", "merge-base", "--is-ancestor", tip, landed],
                              ["git", "push", f"--force-with-lease={ref}:{tip}", "origin", f":{ref}"]]


def test_a_branch_already_gone_says_so_and_gives_no_delete(fx, monkeypatch):
    msg, _, _ = _kept_after_finish(fx, monkeypatch, 2, None)
    assert "already gone" in msg and "push" not in msg and "`" not in msg


def test_an_unreadable_tip_gives_the_lookup_first_and_never_a_placeholder(fx, monkeypatch):
    msg, ctx, _ = _kept_after_finish(fx, monkeypatch, 128, "could not resolve host")
    assert f"`git ls-remote origin refs/heads/{ctx['branch']}`" in msg
    assert "<tip>" not in msg and "could not resolve host" in msg
    assert "--force-with-lease=" not in msg          # no command to paste without a sha


def test_odd_branch_and_remote_names_stay_one_argument_each(fx, monkeypatch):
    tip = "b" * 40
    branch, remote = "plan/a b$(true)", "or$(true)igin"
    msg, _, landed = _kept_after_finish(fx, monkeypatch, 0, tip, branch=branch, remote=remote)
    ref = f"refs/heads/{branch}"
    assert _commands(msg)[-1] == ["git", "push", f"--force-with-lease={ref}:{tip}", remote, f":{ref}"]
    assert _commands(msg)[0] == ["git", "fetch", remote, ref]



def test_a_removed_remote_says_nothing_is_left_and_never_reads_a_tip(fx, monkeypatch):
    """FIN-18: the remote was removed after the plan finished; no ls-remote is run."""
    def boom(*a):
        raise AssertionError("_remote_tip called with no remote")
    monkeypatch.setattr(pt, "_remote_tip", boom)
    import finish
    iso, landed = _pushed_and_landed(fx)
    ctx = {**lst.context(iso["plan_dir"]), "remote": None}
    st = {"state": "landed", "landed_sha": landed, "cleanup": {"branch_remote": {"kept": "x"}}}
    finish._save(finish.context(iso["plan_dir"]),
                 {"finished_at": "2026-10-04T00:00:00Z", "mode": "apply"})
    msg = pt.manual_remote_delete(iso["plan_dir"], ctx, st)
    assert "no longer has a remote" in msg and "nothing remote left to delete" in msg
    assert "`" not in msg and "None" not in msg
