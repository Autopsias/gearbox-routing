"""WIR-02 (s07) — the whole end of a plan on a throwaway repository.

A plan is built, begun (which arms finish), closed, landed and finished through
the real `run.py`, on the bare-origin fixture, with a fake `gh` on PATH. Nothing
is stubbed but GitHub. At the end the checkout is clean and level with origin.

    pytest skills/plan-execute/scripts/test_finish_e2e.py -q
"""

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish  # noqa: E402
import registry_plans as rp  # noqa: E402
from _land_fixture import build_repo, local_main, origin_main, run_cli, work  # noqa: E402
from test_land_repair import _close  # noqa: E402
from worktree import git  # noqa: E402

FAKE_GH = r'''#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
if a[:2] == ["run", "list"]:
    sha = a[a.index("--commit") + 1] if "--commit" in a else "0" * 40
    print(json.dumps([{"databaseId": 7, "headSha": sha, "status": "completed",
                       "conclusion": "success", "createdAt": "2026-10-01T10:00:00Z",
                       "workflowName": "ci", "event": "push"}]))
sys.exit(0)
'''


@pytest.fixture
def fx(tmp_path, monkeypatch):
    fx = build_repo(tmp_path / "repo", built=True)
    root = fx["root"]
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    fr = __import__("finish_record")
    fr._build_plan().amend_gitignore(root)          # plan-builder leaves these lines
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "ci"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    fx["plan"] = fx["plans"]["plan-a"]
    return fx


def _landed(fx, begin=True):
    plan = fx["plan"]
    if begin:
        assert run_cli("begin", plan, "--sessions", "s01")[0] == 0
    else:                                    # a plan that completed before arming shipped
        from run import _plan_worktree_prep  # noqa: PLC0415
        import manifest_io as mio  # noqa: PLC0415
        _plan_worktree_prep(plan, mio.load_manifest(plan), None)
    tree = Path(__import__("plan_scope").plan_worktree(plan))
    work(tree, "src/f.py", "F = 1\n")
    _close(plan, "s01", "plan-a-i1")
    assert run_cli("plan", plan)[1]["action"] == "land"
    assert run_cli("land", plan)[1]["action"] == "land-awaits-review"
    assert run_cli("land-ack", plan, "--note", "e2e")[1]["action"] == "land-acked"
    rc, out = run_cli("land", plan)
    assert (rc, out["action"]) == (0, "landed"), out


def _state(fx):
    ctx = finish.context(fx["plan"])
    p = finish.state_path(ctx["root"], ctx["slug"])
    return json.loads(p.read_text()) if p.exists() else {}


def test_a_plan_ends_with_finish_and_leaves_nothing_behind(fx):
    plan, root = fx["plan"], fx["root"]
    assert "armed_at" not in _state(fx)
    _landed(fx)
    assert "armed_at" in _state(fx) and "finished_at" not in _state(fx)
    assert rp._finish_unfinished(plan) is True       # the janitor must not reap it yet
    rc, nxt = run_cli("plan", plan)
    assert nxt["action"] == "finish" and nxt["finish_mode"] == "apply", nxt
    assert "finished_at" not in _state(fx)           # returning `finish` wrote nothing
    rc, out = run_cli("finish", plan, "--no-wait")
    assert (rc, out["action"], out["mode"]) == (0, "finished", "apply"), out
    assert out["record"]["status"] != "parked" and out["ci"]["verdict"] == "green", out
    assert out["checkout"]["status"] != "blocked", out
    assert _state(fx)["finished_at"] and rp._finish_unfinished(plan) is False
    assert run_cli("plan", plan)[1]["action"] == "complete"
    assert git(["status", "--porcelain"], root, check=True)[1] == ""
    assert local_main(fx) == origin_main(fx)
    assert git(["rev-parse", "HEAD"], root, check=True)[1] == origin_main(fx)


def test_a_plan_with_no_finish_state_gets_report_only_and_pushes_nothing(fx):
    plan = fx["plan"]
    _landed(fx, begin=False)
    assert _state(fx) == {}
    assert run_cli("plan", plan)[1]["finish_mode"] == "report-only"
    before = origin_main(fx)
    rc, out = run_cli("finish", plan, "--no-wait")
    assert (rc, out["action"], out["mode"]) == (0, "finished", "report-only"), out
    assert origin_main(fx) == before
    assert _state(fx)["reported_at"]
    assert run_cli("plan", plan)[1]["action"] == "complete"


def test_reopening_a_finished_plan_starts_a_new_finish_cycle(fx):
    plan = fx["plan"]
    _landed(fx)
    assert run_cli("finish", plan, "--no-wait")[1]["action"] == "finished"
    assert run_cli("plan", plan)[1]["action"] == "complete"
    finish.arm(plan)                      # what `run.py begin` calls on a reopened plan
    st = _state(fx)
    assert "armed_at" in st and not set(st) - {"armed_at", "generation"}, st
    nxt = run_cli("plan", plan)[1]
    assert nxt["action"] == "finish" and nxt["finish_mode"] == "apply", nxt


def test_a_one_session_status_on_a_done_session_never_starts_finish(fx):
    """`plan --session s01` on a done s01 answers for s01 alone: with s02 still
    TODO it must not hand back `finish` (or `land`) as if the plan had ended."""
    import plan_mutate as pm  # noqa: PLC0415
    plan = fx["plan"]
    assert run_cli("begin", plan, "--sessions", "s01")[0] == 0
    work(Path(__import__("plan_scope").plan_worktree(plan)), "src/f.py", "F = 1\n")
    _close(plan, "s01", "plan-a-i1")
    cat, group = __import__("land_repair")._source_item(plan, pm.load_spec(plan))
    pm.add_session(plan, sid="s02", title="Two", task_class="standard_build",
                   gates=["marker-gate"], prompt="second", infographic_group=group,
                   new_items=[json.dumps(
                       {"id": "plan-a-i2", "category": cat, "title": "I2",
                        "research_status": "skipped", "research_reason": "fixture"})])
    rc, out = run_cli("plan", plan, "--session", "s01")
    assert (out["action"], out.get("scope")) == ("complete", "session"), out
    assert "notify_complete" not in out
    assert run_cli("plan", plan)[1]["action"] not in ("finish", "land", "complete")


def test_a_one_session_status_on_the_last_done_session_returns_the_plan_answer(fx):
    """With every session terminal, `plan --session s01` must match plain `plan`
    (land here), never a scoped `complete` that hides the pending land."""
    plan = fx["plan"]
    assert run_cli("begin", plan, "--sessions", "s01")[0] == 0
    work(Path(__import__("plan_scope").plan_worktree(plan)), "src/f.py", "F = 1\n")
    _close(plan, "s01", "plan-a-i1")
    whole = run_cli("plan", plan)[1]
    scoped = run_cli("plan", plan, "--session", "s01")[1]
    assert whole["action"] == "land" and scoped == whole, (whole, scoped)
