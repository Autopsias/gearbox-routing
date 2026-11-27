"""Touch guard: a declared file written in the MAIN checkout is refused at apply.

Run: pytest skills/plan-execute/scripts/test_touch_guard.py -q
Every case uses a throwaway git repository, never the real checkout.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

import touch_guard as tg
from escalation_helpers import SESS, make_plan, run

SCRIPTS = Path(__file__).resolve().parent
PLAN = "p1"
REC = f"_plans/{PLAN}"


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


def _repo(root, plan_dir, *, worktree=True):
    """Git repo at root holding plan_dir, plus a plan worktree; returns the worktree path (or None)."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text(".plan-worktrees/\nignored.txt\n")
    (root / "src").mkdir(exist_ok=True)
    _write(root / "src" / "a.py", "x = 1\n", age=3600)  # a tracked file untouched since before dispatch
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    if not worktree:
        return None
    wtree = root / ".plan-worktrees" / PLAN
    _git(root, "worktree", "add", "-q", "-b", f"plan/{PLAN}", str(wtree))
    (Path(plan_dir) / "_plan_worktree.json").write_text(json.dumps({"path": str(wtree)}))
    return wtree


def _dispatch(plan_dir, ago=30):
    """A dispatch_started event stamped `ago` seconds in the past."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - ago))
    with open(Path(plan_dir) / "run.ndjson", "a") as f:
        f.write(json.dumps({"ts": ts, "event": "dispatch_started", "session_ids": ["s01"]}) + "\n")


def _write(path, text="x\n", age=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


def _case(tmp_path, touches, *, worktree=True):
    root = tmp_path / "repo"
    plan_dir = root / "_plans" / PLAN
    plan_dir.mkdir(parents=True)
    wtree = _repo(root, plan_dir, worktree=worktree)
    manifest = {"items": [{"id": "it-1", "touches": touches}], "sessions": [{"id": "s01", "items": ["it-1"]}]}
    _dispatch(plan_dir)
    return root, plan_dir, wtree, manifest


def test_plant_main_checkout_write_refused(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py")
    _write(root / "src" / "a.py", "x = 2\n")
    res = tg.judge(plan_dir, man, "s01")
    assert res["verdict"] == "refused" and res["paths"] == ["src/a.py"]
    assert res["worktree"] == str(wtree)


def test_incident_replay_plan_folder_file_refused(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, f"{REC}/_decisions/land-followups.md")
    _write(root / REC / "_decisions" / "land-followups.md")
    res = tg.judge(plan_dir, man, "s01")
    assert res["verdict"] == "refused" and res["paths"] == [f"{REC}/_decisions/land-followups.md"]


def test_write_in_worktree_passes(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py")
    _write(wtree / "src" / "a.py", "x = 2\n")
    assert tg.judge(plan_dir, man, "s01")["verdict"] == "ok"


def test_plan_record_dirty_in_main_passes(tmp_path):
    names = ["PLAN.html", "run_state.json", "_closeouts/s01.json"]
    root, plan_dir, wtree, man = _case(tmp_path, ", ".join(f"{REC}/{n}" for n in names))
    for n in names:
        _write(root / REC / n)
    assert tg.judge(plan_dir, man, "s01")["verdict"] == "ok"


def test_unrelated_dirty_path_passes(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py")
    _write(root / "other.py")
    assert tg.judge(plan_dir, man, "s01")["verdict"] == "ok"


def test_git_status_failure_is_refused_not_clean(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py")
    (wtree / ".git").write_text("gitdir: /nonexistent\n")
    res = tg.judge(plan_dir, man, "s01")
    assert res["verdict"] == "error" and "git status failed" in res["reason"]
    sent = {}
    with pytest.raises(SystemExit):
        tg.refuse_if_declared_write_in_main(plan_dir, man, "s01", lambda *a, **k: None,
                                            lambda o, **k: sent.update(o))
    assert sent["applied"] is False and sent["failure"] == "touch-guard-error"


def test_shared_tree_session_not_judged(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py", worktree=False)
    _write(root / "src" / "a.py", "x = 2\n")
    assert tg.judge(plan_dir, man, "s01")["verdict"] == "not-judged"


def test_dirty_before_dispatch_passes(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py")
    _write(root / "src" / "a.py", "x = 2\n", age=3600)
    res = tg.judge(plan_dir, man, "s01")
    assert res["verdict"] == "ok" and res["predispatch"] == ["src/a.py"]


def test_ignored_declared_path_written_in_main_refused(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, ["ignored.txt"])
    _write(root / "ignored.txt")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
    assert "ignored.txt" not in status.stdout  # plain git status cannot see it
    assert tg.judge(plan_dir, man, "s01")["paths"] == ["ignored.txt"]


def test_refusal_logs_event_and_names_where_it_belongs(tmp_path):
    root, plan_dir, wtree, man = _case(tmp_path, "src/a.py, see the notes")
    _write(root / "src" / "a.py", "x = 2\n")
    events, sent = [], {}
    with pytest.raises(SystemExit):
        tg.refuse_if_declared_write_in_main(plan_dir, man, "s01", lambda d, e, **k: events.append((e, k)),
                                            lambda o, **k: sent.update(o))
    assert str(wtree) in sent["reason"] and str(root) in sent["reason"]
    refused = [k for e, k in events if e == "touch_guard_refused"]
    assert refused and refused[0]["path"] == "src/a.py" and refused[0]["plan"] == PLAN
    assert any(e == "touch_guard_skipped_entry" for e, _ in events)  # prose entry is logged, not judged


def test_plan_record_list_covers_every_name_the_orchestrator_writes():
    """Fails when a script writes a new top-level file in the plan folder that PLAN_RECORD does not name."""
    not_record = {"sessions", "_evidence", "_decisions", ".claude"}  # builder input / session deliverables
    seen = set()
    for f in SCRIPTS.glob("*.py"):
        if f.name.startswith(("test_", "_evidence")):
            continue
        seen |= set(re.findall(r'plan_dir\)? */ *"([^"/]+)"', f.read_text()))
    assert seen - set(tg.PLAN_RECORD) - not_record == set()
    import plan_record
    assert {n.rstrip("/") for n in plan_record.RUNTIME_LOCAL} <= set(tg.PLAN_RECORD)


def test_apply_refuses_declared_main_checkout_write(tmp_path, capsys):
    """Drives cmd_apply: a valid DONE closeout must still be refused when a declared file sits in main."""
    plan_dir = make_plan(tmp_path, [SESS])
    root = plan_dir.parents[1]
    wtree = _repo(root, plan_dir)
    mpath = plan_dir / "manifest.json"
    man = json.loads(mpath.read_text())
    man["items"][0]["touches"] = "src/a.py"
    mpath.write_text(json.dumps(man))
    _dispatch(plan_dir)
    _write(root / "src" / "a.py", "x = 2\n")
    f = tmp_path / "out.txt"
    f.write_text('<plan-execute-closeout>\n{"session":"s01","result":"DONE","items_completed":["it-1"],'
                 '"items_blocked":[],"notes":{},"dispatch_next":false,"human_checkpoint_reason":null}\n'
                 '</plan-execute-closeout>')
    capsys.readouterr()
    code = 0
    try:
        run.cmd_apply(str(plan_dir), "s01", str(f))
    except SystemExit as e:
        code = e.code or 0
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["applied"] is False and out["failure"] == "declared-write-in-main-checkout"
    assert str(wtree) in out["reason"]
    assert run._statuses(plan_dir)["s01"] != "DONE"
    assert "touch_guard_refused" in (plan_dir / "run.ndjson").read_text()
    run.cmd_release(plan_dir)
