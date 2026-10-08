"""FIN-01 / FIN-02 (s02) — `run.py finish`: record, leftovers and checkout.

Every test runs on the real bare-origin fixture (``_land_fixture``): a real
``origin.git``, a real primary checkout that owns ``main``, real plan worktrees.
Only ``finish_ci.ci_verdict`` (s03's, built in parallel) is stubbed.

    python3 -m pytest -q test_finish.py
"""

import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish  # noqa: E402
import finish_ci  # noqa: E402
import finish_record as fr  # noqa: E402
import land_state as lst  # noqa: E402
import plan_teardown as pt  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import (build_repo, isolate, local_main, main_side, origin_main,  # noqa: E402
                           run_cli, work)
from worktree import git  # noqa: E402

INDEX = ("# Active Plans\n\n"
         "- [plan-a](_plans/plan-a/PLAN.html) — A, running\n"
         "- [other](_plans/other/PLAN.html) — O, running\n")


@pytest.fixture
def ci(monkeypatch):
    calls, verdicts = [], []

    def stub(root, sha, *, branch, timeout_s, gh="gh", base_sha=None):
        calls.append({"sha": sha, "branch": branch, "timeout_s": timeout_s,
                      "base_sha": base_sha})
        return {"verdict": verdicts.pop(0) if verdicts else "green", "match": "exact",
                "run_id": 1, "run_sha": sha, "failed_checks": [], "followed_runs": [],
                "inherited": None, "reason": None}

    monkeypatch.setattr(finish_ci, "ci_verdict", stub)
    return calls, verdicts


def _base(tmp, amend=True):
    """Two plans, an index, and (as plan-builder leaves it) the ignore lines."""
    fx = build_repo(tmp, slugs=("plan-a", "other"))
    root = fx["root"]
    (root / "_plans_index.md").write_text(INDEX)
    if amend:
        fr._build_plan().amend_gitignore(root)
    git(["add", "-A"], root, check=True)
    git(["commit", "-q", "-m", "index"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)
    fx["plan"] = fx["plans"]["plan-a"]
    return fx


@pytest.fixture
def fx(tmp_path):
    return _base(tmp_path)


def _arm(fx):
    ctx = finish.context(fx["plan"])
    finish._save(ctx, {"armed_at": "2026-10-01T00:00:00Z"})
    return ctx


def _state(fx):
    ctx = finish.context(fx["plan"])
    return json.loads(finish.state_path(ctx["root"], ctx["slug"]).read_text())


def _dirty_record(fx):
    """What an orchestrator leaves in the owner: its own record, changed."""
    (fx["plan"] / "PLAN.html").write_text("<html>done</html>\n")
    (fx["plan"] / "sessions").mkdir(exist_ok=True)
    (fx["plan"] / "sessions" / "s01.md").write_text("closeout\n")
    idx = fx["root"] / "_plans_index.md"
    idx.write_text(idx.read_text().replace("A, running", "A, complete"))


def _status(fx):
    return git(["status", "--porcelain"], fx["root"], strip=False)[1]


def _changed(fx, a, b):
    return sorted(git(["diff", "--name-only", f"{a}..{b}"], fx["root"])[1].split())


def _landed(fx):
    """An isolated plan whose merge is already on origin/main — land.json seeded."""
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    base = origin_main(fx)
    git(["push", "-q", "origin", f"{iso['branch']}:main"], fx["root"], check=True)
    ctx = lst.context(fx["plan"])
    lst.save(fx["plan"], ctx, {"state": "landed", "landed_on": "origin/main",
                               "landed_sha": origin_main(fx), "landed_base": base,
                               "cleanup": {"worktree": "preserved"}})
    _arm(fx)
    return iso


# --------------------------------------------------------------------- FIN-01
def test_clean_apply_pass_commits_the_record_and_leaves_the_owner_clean(fx, ci, monkeypatch):
    _arm(fx)
    _dirty_record(fx)
    before = origin_main(fx)
    ci_sha = local_main(fx)
    seen = []
    real_checkout = fr.checkout

    def spy(ctx, *a, **kw):
        seen.append(sl.lease_status(ctx["plan_dir"], ctx["resource"])["status"])
        return real_checkout(ctx, *a, **kw)

    monkeypatch.setattr(fr, "checkout", spy)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finished" and out["mode"] == "apply", out["brief"]
    rec = out["record"]
    assert rec["status"] == "pushed" and rec["sha"] == origin_main(fx)
    assert rec["paths"] == ["_plans/plan-a/PLAN.html", "_plans/plan-a/sessions/s01.md",
                            "_plans_index.md"]
    assert _changed(fx, before, rec["sha"]) == rec["paths"]
    assert out["checkout"]["status"] == "fast-forwarded", out["brief"]
    assert local_main(fx) == origin_main(fx) == rec["sha"]
    assert _status(fx) == ""
    assert seen == ["held"]                       # still ours after the leftovers step
    ctx = finish.context(fx["plan"])
    assert sl.lease_status(fx["plan"], ctx["resource"])["status"] != "held"
    assert ci[0] == [{"sha": ci_sha, "branch": "main", "timeout_s": 0, "base_sha": None}]
    st = _state(fx)
    assert st["mode"] == "apply" and st["finished_at"] and st["target_sha"] == rec["sha"]
    assert st["origin_sha"] == before and st["armed_at"]
    events = (fx["plan"] / "run.ndjson").read_text()
    assert '"event": "plan_finish"' in events


def test_a_stray_file_is_never_committed_and_an_unrelated_edit_blocks(fx, ci):
    _arm(fx)
    _dirty_record(fx)
    (fx["root"] / "stray.txt").write_text("mine\n")
    (fx["root"] / "shared.txt").write_text("my edit\n")
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["record"]["status"] == "pushed"
    assert "stray.txt" not in out["record"]["paths"]
    assert "shared.txt" not in _changed(fx, out["preflight"]["origin_sha"], origin_main(fx))
    co = out["checkout"]
    assert co["status"] == "blocked" and co["restored"] == []
    assert {(b["path"], b["why"]) for b in co["blocking"]} == {
        ("stray.txt", "outside-record"), ("shared.txt", "outside-record")}
    assert co["command"] == (f"git -C {co['owner']} fetch origin && "
                             f"git -C {co['owner']} merge --ff-only origin/main")
    assert (fx["root"] / "stray.txt").read_text() == "mine\n"
    assert (fx["root"] / "shared.txt").read_text() == "my edit\n"
    assert (fx["plan"] / "PLAN.html").read_text() == "<html>done</html>\n"
    assert out["action"] == "finished"            # a blocked checkout is a report


def test_another_plans_row_and_folder_are_never_staged_and_block(fx, ci):
    _arm(fx)
    _dirty_record(fx)
    other = fx["plans"]["other"]
    (other / "PLAN.html").write_text("<html>other, mid-run</html>\n")
    idx = fx["root"] / "_plans_index.md"
    idx.write_text(idx.read_text().replace("O, running", "O, half"))
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["record"]["status"] == "pushed"
    assert not any(p.startswith("_plans/other/") for p in out["record"]["paths"])
    pushed_index = git(["show", "origin/main:_plans_index.md"], fx["root"], strip=False)[1]
    assert "O, running" in pushed_index and "A, complete" in pushed_index
    co = out["checkout"]
    assert co["status"] == "blocked"
    assert {(b["path"], b["why"], b["plan"]) for b in co["blocking"]} == {
        ("_plans/other/PLAN.html", "other-plan", "other"),
        ("_plans_index.md", "other-plan", "other")}
    assert (other / "PLAN.html").read_text() == "<html>other, mid-run</html>\n"


def test_a_staged_record_path_blocks_with_staged(fx, ci):
    _arm(fx)
    _dirty_record(fx)
    git(["add", "_plans/plan-a/PLAN.html"], fx["root"], check=True)
    out = finish.finish(fx["plan"], timeout_s=0)
    co = out["checkout"]
    assert co["status"] == "blocked" and co["restored"] == []
    assert {"path": "_plans/plan-a/PLAN.html", "why": "staged", "plan": "plan-a"} \
        in co["blocking"]
    assert local_main(fx) != origin_main(fx)


def test_a_missing_ignore_line_is_appended_and_nothing_else_changes(tmp_path, ci):
    fx = _base(tmp_path, amend=False)
    old = (fx["root"] / ".gitignore").read_text()
    fr._build_plan().amend_gitignore(fx["root"])      # what plan-builder does at build
    _arm(fx)
    _dirty_record(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert ".gitignore" in out["record"]["paths"]
    pushed = git(["show", "origin/main:.gitignore"], fx["root"], strip=False)[1]
    assert pushed == (fx["root"] / ".gitignore").read_text()
    assert pushed.startswith(old.rstrip() + "\n")
    added = pushed[len(old.rstrip()):].split("\n")
    assert [ln for ln in added if ln and not ln.startswith("#")] == \
        fr._build_plan().GITIGNORE_LINES
    assert out["checkout"]["status"] == "fast-forwarded", out["brief"]
    assert _status(fx) == ""


def test_non_isolated_local_ahead_parks_and_pushes_nothing(fx, ci):
    _arm(fx)
    (fx["root"] / "src" / "a.py").write_text("A = 2\n")
    git(["commit", "-qam", "local only"], fx["root"], check=True)
    _dirty_record(fx)
    before = origin_main(fx)
    rc, out = run_cli("finish", fx["plan"], "--no-wait")
    assert rc == 1 and out["action"] == "finish-parked"
    assert out["preflight"]["park_reason"] == "local-ahead"
    assert out["record"]["status"] == "skipped" and out["ci"] is None
    assert "push origin main" in out["brief"]
    assert origin_main(fx) == before and ci[0] == []
    assert "finished_at" not in _state(fx)



def test_a_staged_path_outside_the_record_parks_and_pushes_nothing(fx, ci, monkeypatch):
    _arm(fx)
    _dirty_record(fx)

    def sneak(tmp):                     # anything that stages a path outside the record
        (Path(tmp) / "shared.txt").write_text("smuggled\n")
        git(["add", "shared.txt"], tmp, check=True)
        return False

    monkeypatch.setattr(fr, "_gitignore", sneak)
    before = origin_main(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-parked"
    assert (out["record"]["status"], out["record"]["park_reason"]) == ("parked", "outside-record")
    assert out["record"]["outside_record"] == ["shared.txt"]
    assert out["checkout"]["status"] == "skipped" and out["ci"] is None
    assert origin_main(fx) == before and "finished_at" not in _state(fx)
    assert not list((fx["root"] / ".plan-worktrees").glob("*__finish-*"))


# --------------------------------------------------------------------- FIN-02
def test_owner_ahead_of_origin_blocks_and_restores_nothing(fx, ci):
    _landed(fx)
    _dirty_record(fx)
    (fx["root"] / "src" / "a.py").write_text("A = 3\n")
    git(["commit", "-qam", "local only"], fx["root"], check=True)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finished" and out["record"]["status"] == "pushed"
    assert out["leftovers"]["unpushed_default"] == 1
    co = out["checkout"]
    assert co["status"] == "blocked" and co["restored"] == []
    assert co["blocking"] == [{"path": None, "why": "owner-not-behind", "plan": None}]
    assert (fx["plan"] / "PLAN.html").read_text() == "<html>done</html>\n"
    assert ci[0][0]["base_sha"] is not None


def test_a_preserved_cleanup_is_retried_once_through_teardown_plan_tree(fx, ci, monkeypatch):
    iso = _landed(fx)
    _dirty_record(fx)
    calls = []
    real = pt.teardown_plan_tree
    monkeypatch.setattr(pt, "teardown_plan_tree",
                        lambda *a: calls.append(1) or real(*a))
    monkeypatch.setattr(pt, "step_cleanup", lambda *a: pytest.fail("releases the lease"))
    out = finish.finish(fx["plan"], timeout_s=0)
    left = out["leftovers"]
    assert calls == [1]
    assert (left["plan_worktree"], left["plan_branch"]) == ("removed", "removed")
    assert [(r["target"], r["result"]) for r in left["retried"]] == [
        ("worktree", "removed"), ("branch", "removed")]
    assert not iso["tree"].is_dir()
    assert out["checkout"]["status"] == "fast-forwarded", out["brief"]
    assert _status(fx) == ""


def test_a_worktree_holding_uncommitted_files_is_never_removed(fx, ci):
    iso = _landed(fx)
    (iso["tree"] / "wip.txt").write_text("unsaved\n")
    out = finish.finish(fx["plan"], timeout_s=0)
    left = out["leftovers"]
    assert (left["plan_worktree"], left["plan_branch"]) == ("preserved", "preserved")
    wt_retry = left["retried"][0]
    assert (wt_retry["target"], wt_retry["result"]) == ("worktree", "refused")
    assert "wip.txt" in wt_retry["reason"]
    assert (iso["tree"] / "wip.txt").read_text() == "unsaved\n"
    assert git(["rev-parse", "--verify", "--quiet", iso["branch"]], fx["root"])[0] == 0
    assert out["action"] == "finished"            # a refusal is a report, not a park



def test_a_lease_lost_during_the_record_stops_every_later_write(fx, ci, monkeypatch):
    iso = _landed(fx)
    _dirty_record(fx)
    real = fr.record

    def then_lose_it(plan_dir, ctx, *a, **kw):
        out = real(plan_dir, ctx, *a, **kw)
        sl.release_ship_lock(plan_dir, ctx["resource"])      # expired mid-run
        return out

    monkeypatch.setattr(fr, "record", then_lose_it)
    monkeypatch.setattr(pt, "teardown_plan_tree", lambda *a: pytest.fail("deleted without the lease"))
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["record"]["status"] == "pushed"
    left = out["leftovers"]
    assert (left["plan_worktree"], left["plan_branch"]) == ("preserved", "preserved")
    assert [(r["target"], r["result"]) for r in left["retried"]] == [
        ("worktree", "refused"), ("branch", "refused")]
    assert all(r["reason"].startswith("lease-lost") for r in left["retried"])
    assert iso["tree"].is_dir()
    co = out["checkout"]
    assert co["status"] == "blocked" and co["restored"] == []
    assert co["blocking"] == [{"path": None, "why": "lease-lost", "plan": None}]
    assert (fx["plan"] / "PLAN.html").read_text() == "<html>done</html>\n"


def test_a_failed_first_save_after_the_lease_still_releases_it(fx, ci, monkeypatch):
    _arm(fx)

    def boom(*a):
        raise OSError("disk full")

    monkeypatch.setattr(finish, "_save", boom)
    with pytest.raises(OSError, match="disk full"):
        finish.finish(fx["plan"], timeout_s=0)
    ctx = finish.context(fx["plan"])
    assert sl.lease_status(fx["plan"], ctx["resource"])["status"] != "held"


def test_an_edit_made_after_the_checks_is_never_overwritten(fx, ci):
    _dirty_record(fx)
    ctx = _arm(fx)
    rec, target, _ = fr.record(fx["plan"], ctx, origin_main(fx), apply=True,
                               lease_ok=lambda: True)
    assert rec["status"] == "pushed"
    plan_html = fx["plan"] / "PLAN.html"

    def edit_then_ok():                   # check c runs after check b read the bytes
        plan_html.write_text("<html>edited after the check</html>\n")
        return True

    co = fr.checkout(ctx, "_plans/plan-a", target,
                     lambda p: fr.blob(ctx["root"], target, p), apply=True,
                     lease_ok=edit_then_ok)
    assert co["status"] == "blocked"
    assert co["blocking"] == [{"path": "_plans/plan-a/PLAN.html",
                               "why": "differs-from-origin", "plan": "plan-a"}]
    assert "_plans/plan-a/PLAN.html" not in co["restored"]
    assert plan_html.read_text() == "<html>edited after the check</html>\n"
    assert local_main(fx) != target


def test_a_chmod_only_edit_blocks_like_a_byte_edit_and_keeps_its_mode(fx, ci):
    _dirty_record(fx)
    ctx = _arm(fx)
    rec, target, _ = fr.record(fx["plan"], ctx, origin_main(fx), apply=True,
                               lease_ok=lambda: True)
    assert rec["status"] == "pushed"
    plan_html = fx["plan"] / "PLAN.html"

    def chmod_then_ok():                  # same bytes as the target, new mode
        plan_html.chmod(0o755)
        return True

    blocked = [{"path": "_plans/plan-a/PLAN.html", "why": "differs-from-origin",
                "plan": "plan-a"}]
    for apply, lease_ok in ((True, chmod_then_ok), (False, lambda: True)):  # re-check, b
        co = fr.checkout(ctx, "_plans/plan-a", target,
                         lambda p: fr.blob(ctx["root"], target, p), apply=apply,
                         lease_ok=lease_ok)
        assert (co["status"], co["blocking"], co["restored"]) == ("blocked", blocked, [])
    assert plan_html.stat().st_mode & 0o100
    assert plan_html.read_text() == "<html>done</html>\n"
    assert local_main(fx) != target

def test_a_chmod_only_plan_file_is_staged_with_its_exec_bit(fx, ci):
    ctx = _arm(fx)
    (fx["plan"] / "PLAN.html").chmod(0o755)          # same bytes, new mode only
    rec, target, _ = fr.record(fx["plan"], ctx, origin_main(fx), apply=True,
                               lease_ok=lambda: True)
    assert rec["status"] == "pushed" and rec["paths"] == ["_plans/plan-a/PLAN.html"]
    tree = git(["ls-tree", target, "--", "_plans/plan-a/PLAN.html"], fx["root"])[1]
    assert tree.startswith("100755 ")

def test_a_deleted_plan_file_is_removed_but_an_absent_runtime_file_is_kept(fx, ci):
    for name in ("old.md", "LAND_NOTICE.txt"):
        (fx["plan"] / name).write_text("x\n")
    git(["add", "-f", "--", "_plans/plan-a"], fx["root"], check=True)
    git(["commit", "-q", "-m", "two files"], fx["root"], check=True)
    git(["push", "-q", "origin", "main"], fx["root"], check=True)
    for name in ("old.md", "LAND_NOTICE.txt"):
        (fx["plan"] / name).unlink()
    ctx = _arm(fx)
    rec, target, _ = fr.record(fx["plan"], ctx, origin_main(fx), apply=True,
                               lease_ok=lambda: True)
    assert rec["status"] == "pushed" and rec["paths"] == ["_plans/plan-a/old.md"]
    tree = git(["ls-tree", "--name-only", target, "--", "_plans/plan-a/"], fx["root"])[1]
    assert "_plans/plan-a/LAND_NOTICE.txt" in tree.split()
    assert "_plans/plan-a/old.md" not in tree.split()


def test_a_worktree_side_rename_skips_its_source_token(monkeypatch):
    out = " R new.txt\0old.txt\0 M other.txt\0"
    monkeypatch.setattr(fr, "git", lambda *a, **kw: (0, out, ""))
    assert fr._porcelain("unused") == [(" R", "new.txt"), (" M", "other.txt")]


# ------------------------------------------------------------ mode and re-run
def test_report_only_writes_nothing_then_apply_performs_it(fx, ci):
    _dirty_record(fx)                             # no finish.json: an old plan
    before, local, status = origin_main(fx), local_main(fx), _status(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["mode"] == "report-only" and out["action"] == "finished"
    assert out["record"]["status"] == "would-push"
    assert out["checkout"]["status"] == "would-fast-forward" and out["checkout"]["restored"] == []
    assert (origin_main(fx), local_main(fx), _status(fx)) == (before, local, status)
    st = _state(fx)
    assert st["mode"] == "report-only" and st["reported_at"] and st["finished_at"]

    again = finish.finish(fx["plan"], timeout_s=0)
    assert again["record"] is None and origin_main(fx) == before

    out = finish.finish(fx["plan"], timeout_s=0, apply=True)
    assert out["mode"] == "apply"
    assert out["record"]["status"] == "pushed"
    assert out["checkout"]["status"] == "fast-forwarded", out["brief"]
    assert _status(fx) == "" and _state(fx)["mode"] == "apply"


def test_a_rerun_with_ci_pending_repolls_the_same_sha_and_leaves_steps_alone(fx, ci):
    calls, verdicts = ci
    verdicts += ["pending", "green"]
    _arm(fx)
    _dirty_record(fx)
    first = finish.finish(fx["plan"], timeout_s=0)
    assert first["ci"]["verdict"] == "pending" and "--no-wait" in first["brief"]
    time.sleep(0.01)
    moved = main_side(fx, "elsewhere.txt", "x\n")          # origin moves on
    again = finish.finish(fx["plan"], timeout_s=0)
    assert again["record"] is None and again["checkout"] is None
    assert calls[1]["sha"] == calls[0]["sha"]
    assert again["ci"]["verdict"] == "green" and origin_main(fx) == moved
    st = _state(fx)
    assert st["ci"]["verdict"] == "green" and st["checked_at"]
    third = finish.finish(fx["plan"], timeout_s=0)              # green: no re-poll
    assert len(calls) == 2 and third["ci"]["verdict"] == "green"


def _kill_ci(monkeypatch, then=None):
    """ci_verdict raises once (a run stopped mid-poll); `then` runs first."""
    def boom(*a, **kw):
        if then:
            then()
        raise KeyboardInterrupt
    monkeypatch.setattr(finish_ci, "ci_verdict", boom)


def test_a_run_killed_during_the_ci_poll_resumes_without_repeating_the_steps(fx, ci, monkeypatch):
    calls, _ = ci
    stub = finish_ci.ci_verdict
    _arm(fx)
    _dirty_record(fx)
    _kill_ci(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        finish.finish(fx["plan"], timeout_s=0)
    st = _state(fx)
    assert st["steps_done_at"] and not st.get("finished_at") and st["mode"] == "apply"
    assert st["target_sha"] and st["generation"]
    pushed = origin_main(fx)
    monkeypatch.setattr(finish_ci, "ci_verdict", stub)
    assert finish.finish_action(fx["plan"], {"action": "complete"})["action"] == "finish"
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finished" and out["record"]["status"] == "pushed"
    assert origin_main(fx) == pushed                       # steps 1-3 were not repeated
    assert len(calls) == 1 and calls[0]["sha"] == st["ci_sha"]   # CI polled once
    assert _state(fx)["finished_at"]
    assert finish.finish_action(fx["plan"], {"action": "complete"})["action"] == "complete"


def test_a_killed_report_only_run_never_blocks_the_steps_of_an_apply_run(fx, ci, monkeypatch):
    _dirty_record(fx)
    before = origin_main(fx)
    _kill_ci(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        finish.finish(fx["plan"], timeout_s=0)
    st = _state(fx)
    assert st["mode"] == "report-only" and st["steps_done_at"] and not st.get("finished_at")
    assert origin_main(fx) == before
    monkeypatch.setattr(finish_ci, "ci_verdict", ci_stub(ci))
    out = finish.finish(fx["plan"], timeout_s=0, apply=True)
    assert out["mode"] == "apply" and out["record"]["status"] == "pushed"
    assert origin_main(fx) != before and _state(fx)["mode"] == "apply"


def ci_stub(ci):
    calls, verdicts = ci

    def stub(root, sha, *, branch, timeout_s, gh="gh", base_sha=None):
        calls.append(sha)
        return {"verdict": "green", "match": "exact", "run_id": 1, "run_sha": sha,
                "failed_checks": [], "followed_runs": [], "inherited": None, "reason": None}
    return stub


def test_a_slow_report_only_poll_never_overwrites_a_newer_apply_run(fx, ci, monkeypatch):
    _dirty_record(fx)
    stub = ci_stub(ci)

    def apply_meanwhile():
        monkeypatch.setattr(finish_ci, "ci_verdict", stub)
        finish.finish(fx["plan"], timeout_s=0, apply=True)

    def slow(*a, **kw):
        apply_meanwhile()
        return stub(*a[:2], **kw)

    monkeypatch.setattr(finish_ci, "ci_verdict", slow)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded" and "newer finish run" in out["brief"]
    st = _state(fx)
    assert st["mode"] == "apply" and st["finished_at"] and "reported_at" not in st


def _pending(fx, ci):
    """A finished apply run whose CI verdict is still pending (a re-run re-polls)."""
    ci[1].append("pending")
    _arm(fx)
    _dirty_record(fx)
    finish.finish(fx["plan"], timeout_s=0)
    return finish.context(fx["plan"])


def test_cli_without_a_remote_finishes_and_with_two_parks(fx):
    git(["remote", "remove", "origin"], fx["root"], check=True)
    rc, out = run_cli("finish", fx["plan"], "--no-wait")
    assert rc == 0 and out["action"] == "finished", out
    assert (out["record"]["status"], out["checkout"]["status"], out["ci"]) == \
        ("no-remote", "no-remote", None)
    ctx = finish.context(fx["plan"])
    finish.state_path(ctx["root"], ctx["slug"]).unlink()
    for name in ("up", "down"):
        git(["remote", "add", name, str(fx["origin"])], fx["root"], check=True)
    rc, out = run_cli("finish", fx["plan"], "--no-wait")
    assert rc == 1 and out["preflight"]["park_reason"] == "remote-ambiguous"


# ------------------------------------------------- s06 (operator 2026-10-02)
_OLD_LINES = fr._build_plan().GITIGNORE_LINES[:5]     # the list before LAND_NOTICE/_worktrees


def _old_amend_gitignore(project_root):
    """plan-builder's amend_gitignore BEFORE s02, verbatim but for the line list."""
    gi = Path(project_root) / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    needed = [ln for ln in _OLD_LINES if ln not in existing]
    if not needed:
        return None
    new_body = (
        existing.rstrip()
        + ("\n\n# plan-execute runtime state (auto-added by plan-builder)\n"
           + "\n".join(needed) + "\n")
        if existing
        else "# plan-execute runtime state (auto-added by plan-builder)\n"
        + "\n".join(needed) + "\n"
    )
    gi.write_text(new_body)
    return gi


@pytest.mark.parametrize("head,extra,status", [
    (b"__pycache__/\n\n", "", "fast-forwarded"),             # a blank-line end
    (b"__pycache__/\r\n*.log  \r\n", "", "fast-forwarded"),   # CRLF, trailing spaces
    (b"__pycache__/\n", "", "fast-forwarded"),                # 5 old lines vs origin's 7
    (b"__pycache__/\n", "mine.txt\n", "blocked"),             # anything else still blocks
])
def test_an_old_plan_builder_gitignore_amendment_is_restored(tmp_path, ci, head, extra, status):
    fx = _base(tmp_path, amend=False)
    gi = fx["root"] / ".gitignore"
    gi.write_bytes(head)
    git(["commit", "-q", "--allow-empty", "-am", "gitignore"], fx["root"], check=True)
    git(["push", "-q", "origin", "main"], fx["root"], check=True)
    _old_amend_gitignore(fx["root"])                  # uncommitted, as old builds left it
    gi.write_text(gi.read_text() + extra)
    _arm(fx)
    _dirty_record(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert ".gitignore" in out["record"]["paths"]     # origin gets all 7 lines
    co = out["checkout"]
    assert co["status"] == status, out["brief"]
    if status == "fast-forwarded":
        assert ".gitignore" in co["restored"] and _status(fx) == ""
        assert gi.read_bytes() == fr.blob(fx["root"], "origin/main", ".gitignore")
    else:
        assert {"path": ".gitignore", "why": "differs-from-origin", "plan": None} \
            in co["blocking"]
        assert gi.read_text().endswith("mine.txt\n")    # never restored


@pytest.mark.parametrize("match", ["exact", "descendant"])
def test_the_brief_says_when_ci_tested_a_descendant(match):
    out = {"plan": "plan-a", "mode": "apply", "action": "finished", "plan_dir": "/p",
           "ci": {"verdict": "green", "match": match, "run_sha": "d00d" * 10, "reason": None}}
    brief = finish.fn.brief(out, [])
    assert ("CI tested a later commit, " + "d00d" * 10 in brief) == (match == "descendant")


def test_a_real_land_then_finish_hands_ci_the_landed_base(fx, ci):
    _arm(fx)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert run_cli("land", fx["plan"])[1]["action"] == "land-awaits-review"
    assert run_cli("land-ack", fx["plan"], "--note", "ok")[1]["action"] == "land-acked"
    rc, out = run_cli("land", fx["plan"])
    assert (rc, out["action"]) == (0, "landed"), out
    landed = lst.load(fx["plan"])
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finished", out["brief"]
    assert ci[0][-1]["base_sha"] == landed["landed_base"] and landed["landed_base"]
    assert ci[0][-1]["sha"] == landed["landed_sha"]


# --------------------------------------------------------------------- FIN-09
@pytest.mark.parametrize("how", ["plain", "symlink"])
def test_finish_refuses_a_folder_that_is_not_a_plan_and_pushes_nothing(fx, ci, how):
    """The record boundary is derived from the folder finish is given, so any folder
    other than ``<root>/_plans/<slug>`` with a manifest is refused before a read."""
    other = fx["root"] / "skills" / "x"
    other.mkdir(parents=True)
    (other / "manifest.json").write_text("{}\n")
    (other / "notes.md").write_text("not a plan record\n")
    target = other
    if how == "symlink":                       # inside `_plans/`, resolving outside it
        target = fx["root"] / "_plans" / "escape"
        target.symlink_to(other, target_is_directory=True)
    before = origin_main(fx)
    out = finish.finish(target, timeout_s=0, apply=True)
    assert out["action"] == "finish-parked" and str(other.resolve()) in out["brief"], out
    assert out["preflight"]["park_reason"] == "not-a-plan"
    assert origin_main(fx) == before and ci[0] == []
    assert finish.finish_action(target, {"action": "complete"})["action"] == "complete"
    finish.arm(target)                         # a no-op, never a crash
    assert not (finish.state_path(fx["root"], "x").exists()
                or finish.state_path(fx["root"], "escape").exists())


def _seed_landed(fx, **extra):
    isolate(fx, "plan-a")
    ctx = lst.context(fx["plan"])
    lst.save(fx["plan"], ctx, {"state": "landed", "landed_sha": origin_main(fx), **extra})
    return ctx


def test_a_plan_landed_before_finish_existed_is_not_unfinished(fx):
    import registry_plans as rp  # noqa: PLC0415
    ctx = _seed_landed(fx)                      # no landed_base, no finish.json
    assert not finish.state_path(ctx["root"], ctx["slug"]).exists()
    assert rp._finish_unfinished(fx["plan"]) is False


def test_a_plan_landed_after_finish_existed_stays_unfinished_until_finished(fx):
    import registry_plans as rp  # noqa: PLC0415
    ctx = _seed_landed(fx, landed_base=origin_main(fx))
    assert not finish.state_path(ctx["root"], ctx["slug"]).exists()
    assert rp._finish_unfinished(fx["plan"]) is True
    finish._save(ctx, {"armed_at": "2026-10-01T00:00:00Z"})
    assert rp._finish_unfinished(fx["plan"]) is True
    finish._save(ctx, {"finished_at": "2026-10-02T00:00:00Z"})
    assert rp._finish_unfinished(fx["plan"]) is False
