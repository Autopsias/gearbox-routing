"""Accepted risks, project-root detection, CI median, live-worktree skip (SK-01)."""
import itertools
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import accepted
import health
from shape import detect_shape, tracked_files

TODAY = date(2026, 11, 8)


def row(kind="advisory", expired=False, fix=False, despite=False):
    return {"id": "ADV-1", "package": "pkg", "kind": kind, "reason": "r", "ruling": "R5",
            "accepted_on": "2026-11-07", "review_by": "2027-01-01",
            "expires": "2026-11-01" if expired else "2027-05-06",
            "fix_available": fix, "accept_despite_fix": despite}


@pytest.mark.parametrize("listed,expired,fix,despite,kind",
                         list(itertools.product([True, False], [True, False], [True, False],
                                                [True, False], ["advisory", "maintenance"])))
def test_accepted_matrix(listed, expired, fix, despite, kind):
    rows = [row(kind, expired, fix, despite)] if listed else []
    ident = "ADV-1" if kind == "advisory" else "pkg"
    acc, exp, open_ = accepted.classify(rows, [(kind, ident)], TODAY)
    should_accept = listed and not expired and (not fix or despite)
    assert bool(acc) == should_accept
    assert open_ == ([] if should_accept else [(kind, ident)])
    assert bool(exp) == (listed and expired and not should_accept)
    if should_accept:
        assert "accepted (R5)" in accepted.detail(acc, exp, open_)
    if exp:
        assert "expired 2026-11-01" in accepted.detail(acc, exp, open_)


def test_kind_must_match_and_wrong_ident_stays_open():
    assert accepted.classify([row("advisory")], [("maintenance", "pkg")], TODAY)[2]
    assert accepted.classify([row("advisory")], [("advisory", "OTHER")], TODAY)[2]


def git(repo, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=repo,
                   check=True, capture_output=True)


def make(tmp_path, files):
    repo = tmp_path / "r"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for f, body in files.items():
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def test_rows_file_schema_and_cli(tmp_path):
    bad = {"id": "x"}
    repo = make(tmp_path, {"pyproject.toml": "[project]\nname='a'\n", "a.py": "x=1\n",
                           ".claude/health/accepted-risks.json": json.dumps(
                               [row(expired=False), bad])})
    out = accepted.helper_command("accepted", repo, ["advisory:ADV-1", "advisory:NOPE"])
    assert out["open"] == [["advisory", "NOPE"]] or out["open"] == [("advisory", "NOPE")]
    assert not out["all_accepted"] and "row 1 ignored" in out["detail"]
    ok = accepted.helper_command("accepted", repo, ["advisory:ADV-1"])
    assert ok["all_accepted"] and "accepted (R5)" in ok["detail"]


def test_broken_file_is_named_not_empty(tmp_path):
    repo = make(tmp_path, {"a.py": "x=1\n", ".claude/health/accepted-risks.json": "{nope"})
    out = accepted.helper_command("accepted", repo, ["advisory:ADV-1"])
    assert not out["all_accepted"] and "unreadable" in out["detail"]


def test_project_dir_found_under_apps_api_and_probes_run_there(tmp_path):
    repo = make(tmp_path, {"apps/api/pyproject.toml": "[project]\nname='a'\n",
                           "apps/api/app.py": "x=1\n", "README.md": "x\n",
                           ".github/workflows/ci.yml": "name: ci\non: push\n"})
    venv = repo / "apps/api/.venv/lib/python3.12/site-packages"
    venv.mkdir(parents=True)
    shape = detect_shape(repo, tracked_files(repo))
    assert accepted.project_dir(shape["manifests"]) == "apps/api"
    assert accepted.site_packages(repo, "apps/api") == str(venv)
    probes = {c["id"]: c["detail"] for c in health.pending_checks(repo, shape)}
    assert "cd apps/api && python3" in probes["sec.supply-chain"]
    assert f"pip-audit --path {venv}" in probes["sec.dep-vulns"]
    assert "startedAt,updatedAt" in probes["ci.wall-clock"]
    assert accepted.helper_command("project", repo, []) == {"dir": "apps/api",
                                                           "site_packages": str(venv)}


def test_root_project_has_no_cd(tmp_path):
    repo = make(tmp_path, {"pyproject.toml": "[project]\nname='a'\n", "a.py": "x=1\n"})
    shape = detect_shape(repo, tracked_files(repo))
    sc = {c["id"]: c["detail"] for c in health.pending_checks(repo, shape)}["sec.supply-chain"]
    assert "cd " not in sc


def test_ci_probe_uses_real_gh_fields_and_median():
    runs = [{"conclusion": "success", "startedAt": "2026-11-08T10:00:00Z",
             "updatedAt": f"2026-11-08T10:{m:02d}:00Z"} for m in (5, 10, 30)]
    runs.append({"conclusion": "failure", "startedAt": "2026-11-08T10:00:00Z",
                 "updatedAt": "2026-11-08T11:00:00Z"})
    assert accepted.median_minutes(runs) == 10.0
    assert accepted.median_minutes([]) is None
    src = (Path(__file__).parent / "probes.py").read_text()
    assert "startedAt,updatedAt,conclusion,workflowName" in src and "durationMs" not in src
    assert "durationMs" not in (Path(__file__).parent.parent / "SKILL.md").read_text()


def test_unmerged_work_skips_live_worktree_branches(tmp_path):
    repo = make(tmp_path, {"a.py": "x=1\n"})
    for b in ("stale", "plan/live"):
        git(repo, "checkout", "-q", "-b", b)
        (repo / f"{b.replace('/', '_')}.txt").write_text(b)
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", b)
    git(repo, "checkout", "-q", "main")
    git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "plan/live")
    c = health.hyg_unmerged_work(repo)
    assert "stale" in c["detail"] and "plan/live" not in c["detail"].split("(")[0]
    assert "1 branches skipped: live plan worktrees" in c["detail"]


def test_unsafe_manifest_folder_is_rejected_and_paths_quoted(tmp_path):
    repo = make(tmp_path, {"a;touch x/pyproject.toml": "[project]\nname='a'\n",
                           "a;touch x/app.py": "x=1\n"})
    shape = detect_shape(repo, tracked_files(repo))
    assert accepted.project_dir(shape["manifests"]) is None
    probes = {c["id"]: c["detail"] for c in health.pending_checks(repo, shape)}
    assert "touch" not in probes["sec.supply-chain"] and "touch" not in probes["sec.dep-vulns"]
    assert accepted.cd_prefix("a b") == "cd 'a b' && "
    venv = repo / "x y/.venv/lib/python3.12/site-packages"
    venv.mkdir(parents=True)
    cmd = accepted.audit_commands(repo, {"py": ["a.py"], "manifests": []}, "x y")[0]
    assert "--path '" in cmd and "cd 'x y' &&" in cmd


def test_deeply_nested_accepted_risks_is_refused_not_a_crash(tmp_path):
    repo = make(tmp_path, {"a.py": "x=1\n", ".claude/health/accepted-risks.json":
                           "[" * 100000 + "]" * 100000})
    rows, problems = accepted.load_rows(repo)
    assert rows == [] and "unreadable" in problems[0]
