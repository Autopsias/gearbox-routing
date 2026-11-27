"""Main's new tests run FIRST: a red one parks the land before any long gate starts.

    pytest skills/plan-execute/scripts/test_land_precheck.py -q

Real `run.py land` against a real bare origin; main moves from a third clone. The gate
counts its runs in a file, so "the long gates never started" is a number, not a claim.
"""

import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_main_tests as lmt  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import build_repo, isolate, main_side, run_cli, work  # noqa: E402
from test_land import _ack_and_land, local_main  # noqa: E402
from worktree import git  # noqa: E402

GATE = "import sys; open(sys.argv[1], 'a').write('ran\\n')"


def counting_fixture(tmp_path, monkeypatch):
    """A land whose one gate counts its runs in `counter`, a python3 with pytest on PATH, and a
    plan with one commit of its own. Returns (fx, iso, counter)."""
    counter = tmp_path / "gate-runs.txt"
    gate = {"kind": "argv", "cwd": ".", "timeout": 120,
            "argv": [sys.executable, "-c", GATE, str(counter)]}
    fx = build_repo(tmp_path, gates={"marker-gate": gate})
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    shim = tmp_path / "shim" / "python3"                  # the gates' python3 needs pytest
    shim.parent.mkdir()
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    return fx, iso, counter


def runs(counter):
    return len(counter.read_text().split()) if counter.exists() else 0

RED = "def test_new():\n    assert 1 == 2, 'main broke it'\n"
GREEN = "def test_new():\n    assert 1 == 1\n"


def test_a_red_test_added_on_main_parks_before_the_long_gates(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    main_side(fx, "tests/test_new.py", RED)
    rc, out = run_cli("land", iso["plan_dir"])
    assert (out["action"], out["kind"], out["failed"]) == ("land-parked", "gate-failed", ["main-new-tests"]), out
    assert "tests/test_new.py" in out["brief"] and "main broke it" in out["brief"]
    assert runs(counter) == 0                                       # no long gate started
    st = lst.load(str(iso["plan_dir"]))
    assert (st["state"], st["park"]["kind"]) == ("parked", "gate-failed")
    assert "main_tests_key" not in st                               # a red is never settled


def test_a_green_test_added_on_main_continues_and_is_not_run_twice(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    main_side(fx, "tests/test_new.py", GREEN)
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review" and runs(counter) == 1, out
    st = lst.load(str(iso["plan_dir"]))
    assert st["main_tests"]["tests"] == ["tests/test_new.py"] and st["main_tests"]["red"] == []
    assert st["main_tests_key"] == st["gate_key"]


def test_with_an_earlier_record_only_what_main_changed_since_is_run(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    main_side(fx, "tests/test_old.py", GREEN)
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    main_side(fx, "tests/test_new.py", RED)
    out = run_cli("land", iso["plan_dir"])[1]
    assert (out["kind"], out["failed"]) == ("gate-failed", ["main-new-tests"]), out
    assert "tests/test_new.py" in out["brief"] and "test_old.py" not in out["brief"]
    assert runs(counter) == 1                                       # only the first land's gate run
    main_side(fx, "tests/test_new.py", GREEN)                       # main fixed it: the land goes on
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    assert runs(counter) == 2


def test_no_changed_test_files_means_no_run(tmp_path, monkeypatch):
    fx, iso, counter = counting_fixture(tmp_path, monkeypatch)
    main_side(fx, "src/other.py", "OTHER = 1\n")
    assert run_cli("land", iso["plan_dir"])[1]["action"] == "land-awaits-review"
    st = lst.load(str(iso["plan_dir"]))
    assert "main_tests" not in st and runs(counter) == 1


def test_select_keeps_live_test_files_and_names_the_checks(tmp_path):
    for rel in ("scripts/test_a.py", "tests/test_b.py", "fixtures/test_probe.py", "scripts/helper.py",
                "scripts/quality/check_file_sizes.py", "scripts/quality/check_types.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    files = ["scripts/test_a.py", "tests/test_b.py", "tests/test_gone.py", "fixtures/test_probe.py",
             "scripts/helper.py", "scripts/quality/check_file_sizes.py", "scripts/quality/check_types.py",
             "evals/routing/tasks/t1/test_x.py"]
    tests, checks = lmt.select(tmp_path, files)
    assert tests == ["scripts/test_a.py", "tests/test_b.py"]        # deleted, fixtures, tasks: out
    assert checks == ["scripts/quality/check_file_sizes.py", "scripts/quality/check_types.py"]


@pytest.mark.parametrize("rc,verdict", [(0, "green"), (5, "green"), (1, "red"), (2, "red")])
def test_pytest_exit_codes(tmp_path, monkeypatch, rc, verdict):
    monkeypatch.setattr(lmt.ssio, "run_deploy_argv", lambda argv, **kw: {
        "returncode": 0 if argv[1] == "-c" else rc, "stdout": "out", "stderr": ""})
    red, undecided, _ = lmt._failures(str(tmp_path), ["tests/test_a.py"], [], str(tmp_path))
    assert (verdict == "red") == bool(red) and undecided == []


def test_a_python3_without_pytest_is_undecided_not_red(tmp_path, monkeypatch):
    seen = []

    def fake(argv, **kw):
        seen.append(argv[1:3])
        return {"returncode": 1, "stdout": "", "stderr": "No module named pytest"}

    monkeypatch.setattr(lmt.ssio, "run_deploy_argv", fake)
    red, undecided, _ = lmt._failures(str(tmp_path), ["tests/test_a.py"], [], str(tmp_path))
    assert red == [] and undecided == ["pytest"] and seen == [["-c", "import pytest"]]


def test_a_timeout_is_undecided_not_red(tmp_path, monkeypatch):
    monkeypatch.setattr(lmt.ssio, "run_deploy_argv", lambda argv, **kw: {
        "returncode": 0 if argv[1] == "-c" else None, "stdout": "", "stderr": "timeout"})
    red, undecided, _ = lmt._failures(str(tmp_path), ["tests/test_a.py"], [], str(tmp_path))
    assert red == [] and undecided == ["pytest"]


@pytest.mark.parametrize(("disabled_remote", "ignored_plan"),
                         [(None, False), ("origin", False), ("public", True)])
def test_a_repo_with_no_remote_lands_locally_and_says_so(tmp_path, disabled_remote, ignored_plan):
    fx = build_repo(tmp_path)
    git(["remote", "remove", "origin"], fx["root"], check=True)
    if ignored_plan:
        ignore = fx["root"] / ".gitignore"
        with ignore.open("a") as handle:
            handle.write("\n_plans/\n")
        git(["add", ".gitignore"], fx["root"], check=True)
        git(["rm", "-r", "--cached", "--", "_plans/plan-a"], fx["root"], check=True)
        git(["commit", "-m", "Keep plan records private"], fx["root"], check=True)
    iso = isolate(fx, "plan-a")
    if disabled_remote:
        root = fx["root"]
        git(["remote", "add", disabled_remote, str(tmp_path / "public-export")], root, check=True)
        git(["remote", "set-url", "--push", disabled_remote, "DISABLED://export-only"], root, check=True)
        git(["symbolic-ref", f"refs/remotes/{disabled_remote}/HEAD",
             f"refs/remotes/{disabled_remote}/public-main"], root, check=True)
    work(iso["tree"], "src/f.py", "F = 1\n")
    before = local_main(fx)
    rc, out = _ack_and_land(iso["plan_dir"])
    assert (rc, out["action"]) == (0, "landed")
    assert local_main(fx) == before                        # §4.3 holds here too
    ref = "refs/plan-lands/plan-a"
    st = lst.load(iso["plan_dir"])
    # §8.c2: in a no-remote repo the FINAL-record commit is what the durable ref
    # is re-pointed at, and the merge is its ancestor.
    assert git(["rev-parse", "--verify", "--quiet", ref], fx["root"])[1] == st["final_record_sha"]
    assert git(["merge-base", "--is-ancestor", out["merge_sha"], ref], fx["root"])[0] == 0
    assert "LANDED LOCALLY ONLY" in out["brief"] and ref in out["brief"]
    if ignored_plan:
        assert st["record_status"] == "kept-local"
        assert (iso["plan_dir"] / "PLAN.html").is_file()
        assert git(["ls-tree", "-r", ref, "--", "_plans/plan-a"], fx["root"])[1] == ""
    # §4.8a — the durable ref is the only thing keeping the commit reachable.
    git(["reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all"],
        fx["root"], check=True)
    git(["gc", "--prune=now"], fx["root"], check=True)
    assert git(["cat-file", "-t", out["merge_sha"]], fx["root"]) == (0, "commit", "")
