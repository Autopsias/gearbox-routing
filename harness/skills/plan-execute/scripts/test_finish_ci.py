"""FIN-03 (s03) — `finish_ci.ci_verdict`, the CI verdict for a landed sha.

A fake `gh` on PATH answers from canned JSON keyed by its argv, and logs every
call; a real temporary git repo (bare origin + two clones) backs the
nearest-descendant ancestry check, including the fetch it needs. No test calls
the real GitHub API.

    pytest skills/plan-execute/scripts/test_finish_ci.py -q
"""

import json
import os
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish_ci  # noqa: E402

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
spec = json.load(open(os.environ["FAKE_GH_SPEC"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
with open(os.environ["FAKE_GH_LOG"] + ".env", "a") as log:  # what GH_REPO each call saw
    log.write(json.dumps(os.environ.get("GH_REPO")) + "\n")
if "--repo" in argv:  # canned answers are keyed without it
    i = argv.index("--repo")
    del argv[i:i + 2]
if argv[:2] == ["auth", "status"]:
    # like real gh: unscoped, ANY broken host fails; --hostname checks one host
    bad = spec.get("bad_hosts", [])
    host = argv[argv.index("--hostname") + 1] if "--hostname" in argv else None
    sys.exit(spec.get("auth_rc", 0) or int(host in bad if host else bool(bad)))
if argv[:2] == ["run", "list"]:
    for flag in (["--event", "push"], ["--limit", "100"]):
        i = argv.index(flag[0]) if flag[0] in argv else -1
        if i < 0 or argv[i + 1] != flag[1]:
            sys.stderr.write("fake gh: run list without %s\n" % " ".join(flag))
            sys.exit(3)
    key = " ".join(argv[:4])  # run list --commit X | run list --branch B
    print(json.dumps(spec.get("list", {}).get(key, [])))
    sys.exit(0)
if argv[:2] == ["run", "view"] and argv[3:] == ["--json", "jobs"]:
    if argv[2] not in spec.get("jobs", {}):
        sys.exit(1)
    print(json.dumps({"jobs": spec["jobs"][argv[2]]}))
    sys.exit(0)
if argv[:2] == ["run", "view"] and argv[3:] == ["--log-failed"]:
    sys.stdout.write(spec.get("logs", {}).get(argv[2], ""))
    sys.exit(0)
sys.exit(2)
'''


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _commit(cwd, name):
    (Path(cwd) / name).write_text(name)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", name)
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A repo whose local main stops at SHA; DESC1 and DESC2 (descendants) and
    PARENT (an ancestor) exist on origin. DESC1/DESC2 reach `root` only by fetch."""
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "gitconfig").write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin, root, other = tmp_path / "origin.git", tmp_path / "root", tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(root)], check=True,
                   capture_output=True)
    _git(root, "checkout", "-q", "-b", "main")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "verify.yml").write_text("on: push\n")
    shas = {"PARENT": _commit(root, "parent"), "SHA": _commit(root, "plan")}
    _git(root, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True,
                   capture_output=True)
    shas["DESC1"] = _commit(other, "next")
    shas["DESC2"] = _commit(other, "after")
    _git(other, "push", "-q", "origin", "main")
    shas["BASE"] = "b" * 40

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_SPEC", str(tmp_path / "spec.json"))
    monkeypatch.setenv("FAKE_GH_LOG", str(tmp_path / "gh.log"))
    monkeypatch.setattr(finish_ci, "POLL_INTERVAL_S", 0.05)

    class Env:
        pass

    e = Env()
    e.root, e.shas, e.tmp = root, shas, tmp_path

    def spec(**kw):
        (tmp_path / "spec.json").write_text(json.dumps(kw))

    def calls():
        log = tmp_path / "gh.log"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def verdict(timeout_s=0, base_sha=None, gh="gh"):
        return finish_ci.ci_verdict(root, shas["SHA"], branch="main", timeout_s=timeout_s,
                                    gh=gh, base_sha=base_sha)

    e.spec, e.calls, e.verdict = spec, calls, verdict
    spec()
    return e


def run(rid, sha, wf="verify", conclusion="success", status="completed", minute=0,
        event="push", wf_id=None):
    return {"databaseId": rid, "headSha": sha, "status": status,
            "conclusion": conclusion if status == "completed" else "",
            "createdAt": f"2026-10-01T10:{minute:02d}:00Z", "workflowName": wf,
            "workflowDatabaseId": wf_id or zlib.crc32(wf.encode()),
            "event": event, "attempt": 1}


def exact(env, *runs):
    return {f"run list --commit {env.shas['SHA']}": list(runs)}


FAILED_JOBS = [
    {"name": "verify", "conclusion": "failure", "status": "completed",
     "steps": [{"name": "Set up job", "conclusion": "success"},
               {"name": "Tests", "conclusion": "failure"},
               {"name": "Lint", "conclusion": "skipped"}]},
    {"name": "secrets", "conclusion": "skipped", "status": "completed", "steps": []},
]
FAILED_LOG = ("verify\tTests\t2026-10-01T07:30:48Z FAILED skills/a/test_x.py::test_y - AssertionError\n"
              "verify\tTests\t2026-10-01T07:30:48Z FAILED skills/a/test_x.py::test_z[claude] - assert 1\n"
              "verify\tTests\t2026-10-01T07:30:49Z FAILED skills/a/test_x.py::test_y - AssertionError\n"
              "verify\tTests\t2026-10-01T07:30:50Z ===== 2 failed, 10 passed =====\n")


def test_green_across_two_workflows(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(1, s, "verify", minute=1), run(2, s, "lint", minute=2)))
    res = env.verdict()
    assert res["verdict"] == "green"
    assert res["match"] == "exact" and res["run_id"] == 2 and res["run_sha"] == s
    assert [r["run_id"] for r in res["followed_runs"]] == [1, 2]
    assert res["failed_checks"] == [] and res["inherited"] is None and res["reason"] is None
    assert set(res) == {"verdict", "match", "run_id", "run_sha", "failed_checks",
                        "followed_runs", "inherited", "reason"}


def test_one_red_workflow_is_not_hidden_by_a_green_sibling(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(1, s, "verify", "failure", minute=1),
                        run(2, s, "lint", minute=5)), jobs={"1": FAILED_JOBS})
    res = env.verdict()
    assert res["verdict"] == "red" and res["run_id"] == 1
    assert {c["workflow"] for c in res["failed_checks"]} == {"verify"}


def test_red_names_workflow_job_step_and_failing_tests(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(7, s, "verify", "failure")), jobs={"7": FAILED_JOBS},
             logs={"7": FAILED_LOG})
    res = env.verdict()
    assert res["failed_checks"] == [{
        "workflow": "verify", "job": "verify", "step": "Tests",
        "tests": ["skills/a/test_x.py::test_y", "skills/a/test_x.py::test_z[claude]"]}]


def test_red_returns_at_once_while_a_sibling_is_still_running(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(1, s, "verify", "failure"),
                        run(2, s, "lint", status="in_progress")))
    start = time.monotonic()
    res = env.verdict(timeout_s=30)
    assert res["verdict"] == "red" and time.monotonic() - start < 10
    assert res["failed_checks"][0]["job"] == "(no failed job reported)"  # jobs query failed


def test_cancelled_run_follows_the_newer_same_sha_run(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(11, s, conclusion="success", minute=5),
                        run(10, s, conclusion="cancelled", minute=1)))
    res = env.verdict()
    assert res["verdict"] == "green" and res["match"] == "exact" and res["run_id"] == 11
    assert [(r["run_id"], r["conclusion"]) for r in res["followed_runs"]] == [
        (10, "cancelled"), (11, "success")]


def test_exact_run_cancelled_by_a_later_push_falls_back_to_the_nearest_descendant(env):
    sh = env.shas
    assert subprocess.run(["git", "-C", str(env.root), "cat-file", "-e", sh["DESC2"]],
                          capture_output=True).returncode != 0  # only a fetch brings it
    env.spec(list={
        **exact(env, run(20, sh["SHA"], conclusion="cancelled", minute=1)),
        "run list --branch main": [
            run(23, sh["DESC2"], conclusion="success", minute=4),
            run(22, sh["DESC1"], conclusion="cancelled", minute=3),
            run(20, sh["SHA"], conclusion="cancelled", minute=1),
            run(19, sh["PARENT"], conclusion="failure", minute=0),  # ancestor: ignored
        ]})
    res = env.verdict()
    assert res["verdict"] == "green" and res["match"] == "descendant"
    assert res["run_id"] == 23 and res["run_sha"] == sh["DESC2"] != sh["SHA"]
    assert [r["run_id"] for r in res["followed_runs"]] == [20, 22, 23]
    branch_calls = [c for c in env.calls() if c[:4] == ["run", "list", "--branch", "main"]]
    assert branch_calls, "the descendant fallback must query the branch"


def test_all_cancelled_and_no_descendant_is_unknown_no_run_for_sha(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(20, s, conclusion="cancelled")))
    res = env.verdict()
    assert (res["verdict"], res["reason"], res["match"]) == ("unknown", "no-run-for-sha", None)
    assert res["run_id"] == 20  # the newest run looked at


def test_a_schedule_run_on_the_sha_is_ignored(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(30, s, conclusion="failure", event="schedule", minute=9),
                        run(31, s, conclusion="success", minute=1)))
    res = env.verdict()
    assert res["verdict"] == "green" and [r["run_id"] for r in res["followed_runs"]] == [31]
    for argv in (c for c in env.calls() if c[:2] == ["run", "list"]):
        assert "--event" in argv and argv[argv.index("--event") + 1] == "push"
        assert argv[argv.index("--limit") + 1] == "100"


def test_timeout_gives_pending_never_green(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(40, s, status="in_progress")))
    res = env.verdict(timeout_s=3)
    assert (res["verdict"], res["reason"]) == ("pending", "timeout")
    looks = [c for c in env.calls() if c[:3] == ["run", "list", "--commit"]]
    assert len(looks) >= 2, "a non-zero timeout must poll more than once"


def test_zero_timeout_is_one_look(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(40, s, status="queued")))
    assert env.verdict(timeout_s=0)["verdict"] == "pending"
    assert len([c for c in env.calls() if c[:3] == ["run", "list", "--commit"]]) == 1


def test_gh_missing_is_unknown(env):
    res = env.verdict(gh=str(env.tmp / "no-such-gh"))
    assert (res["verdict"], res["reason"]) == ("unknown", "gh-missing")


def test_gh_unauthenticated_is_unknown_and_lists_nothing(env):
    env.spec(auth_rc=1)
    res = env.verdict()
    assert (res["verdict"], res["reason"]) == ("unknown", "gh-unauthenticated")
    assert all(c[:2] != ["run", "list"] for c in env.calls())


def test_no_workflows_is_unknown(env):
    (env.root / ".github" / "workflows" / "verify.yml").unlink()
    assert env.verdict()["reason"] == "no-workflows"


def test_no_run_at_all_is_unknown_no_run_for_sha(env):
    res = env.verdict()
    assert res == finish_ci._result("unknown", "no-run-for-sha")


def test_a_run_that_registers_late_decides_within_the_grace(env, monkeypatch):
    s = env.shas["SHA"]
    late = lambda _t: env.spec(list=exact(env, run(45, s)))  # noqa: E731
    monkeypatch.setattr(finish_ci.time, "sleep", late)
    res = env.verdict(timeout_s=60)
    assert (res["verdict"], res["run_id"]) == ("green", 45)


def test_no_run_after_the_grace_is_unknown_not_timeout(env, monkeypatch):
    monkeypatch.setattr(finish_ci, "NO_RUN_GRACE_S", 0.2)
    start = time.monotonic()
    res = env.verdict(timeout_s=60)
    assert res == finish_ci._result("unknown", "no-run-for-sha")
    assert time.monotonic() - start < 10, "the grace, not the timeout, bounds the wait"


def _red_with_base(env, base_runs, base_jobs=None):
    s, b = env.shas["SHA"], env.shas["BASE"]
    env.spec(list={**exact(env, run(50, s, conclusion="failure")),
                   f"run list --commit {b}": base_runs},
             jobs={"50": FAILED_JOBS, **({"60": base_jobs} if base_jobs else {})})
    return env.verdict(base_sha=b)


def test_inherited_true_when_the_base_failed_the_same_job(env):
    res = _red_with_base(env, [run(60, env.shas["BASE"], conclusion="failure")], FAILED_JOBS)
    assert res["verdict"] == "red" and res["inherited"] is True


def test_inherited_false_when_the_base_passed(env):
    res = _red_with_base(env, [run(60, env.shas["BASE"], conclusion="success")])
    assert res["inherited"] is False


def test_inherited_none_without_a_base_or_a_comparable_base_run(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(50, s, conclusion="failure")), jobs={"50": FAILED_JOBS})
    assert env.verdict(base_sha=None)["inherited"] is None
    assert _red_with_base(env, [])["inherited"] is None
    pending_base = [run(60, env.shas["BASE"], status="in_progress")]
    assert _red_with_base(env, pending_base)["inherited"] is None


def test_two_workflows_sharing_a_name_are_judged_apart(env):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(1, s, "CI", "failure", minute=1, wf_id=101),
                        run(2, s, "CI", "success", minute=5, wf_id=202)),
             jobs={"1": FAILED_JOBS})
    res = env.verdict()
    assert res["verdict"] == "red" and res["run_id"] == 1
    lists = [c for c in env.calls() if c[:2] == ["run", "list"]]
    assert "workflowDatabaseId" in lists[0][lists[0].index("--json") + 1].split(",")


def test_inherited_none_when_job_data_is_missing_on_both_sides(env):
    s, b = env.shas["SHA"], env.shas["BASE"]
    env.spec(list={**exact(env, run(50, s, conclusion="failure")),
                   f"run list --commit {b}": [run(60, b, conclusion="failure")]})
    res = env.verdict(base_sha=b)  # no `jobs` canned: both job lookups fail
    assert res["verdict"] == "red" and res["inherited"] is None


def test_inherited_false_when_job_data_is_missing_but_the_base_passed(env):
    s, b = env.shas["SHA"], env.shas["BASE"]
    env.spec(list={**exact(env, run(50, s, conclusion="failure")),
                   f"run list --commit {b}": [run(60, b, conclusion="success")]})
    res = env.verdict(base_sha=b)  # no `jobs` canned: the job lookup here fails
    assert res["failed_checks"][0]["job"] == "(no failed job reported)"
    assert res["verdict"] == "red" and res["inherited"] is False


def test_auth_check_is_scoped_to_the_repo_host(env):
    s = env.shas["SHA"]
    _git(env.root, "remote", "set-url", "origin", "git@ghe.example.com:o/r.git")
    env.spec(list=exact(env, run(1, s)), bad_hosts=["github.com"])
    assert env.verdict()["verdict"] == "green"
    env.spec(list=exact(env, run(1, s)), bad_hosts=["ghe.example.com"])
    assert env.verdict()["reason"] == "gh-unauthenticated"


@pytest.mark.parametrize("url,host", [
    ("https://github.com/o/r.git", "github.com"),
    ("ssh://git@ghe.example.com:22/o/r", "ghe.example.com"),
    ("git@github.com:o/r.git", "github.com"),
])
def test_host_from_remote_url(env, url, host):
    _git(env.root, "remote", "set-url", "origin", url)
    assert finish_ci._host(env.root) == host


def test_host_of_a_local_remote_is_gh_default(env, monkeypatch):
    monkeypatch.delenv("GH_HOST", raising=False)
    assert finish_ci._host(env.root) == "github.com"
    monkeypatch.setenv("GH_HOST", "ghe.example.com")
    assert finish_ci._host(env.root) == "ghe.example.com"


def test_every_call_is_capped_by_what_is_left_of_the_timeout(env, monkeypatch):
    s = env.shas["SHA"]
    env.spec(list=exact(env, run(1, s, minute=1)))
    real, seen = subprocess.run, []

    def spy(argv, **kw):  # a gh that blocks would be killed at this timeout
        seen.append((argv[1:3], kw.get("timeout")))
        return real(argv, **kw)

    monkeypatch.setattr(finish_ci.subprocess, "run", spy)
    assert env.verdict(timeout_s=20)["verdict"] == "green"
    assert ["auth", "status"] in [a for a, _ in seen]  # the auth check is inside the bound
    assert all(t is not None and t <= 20 for _, t in seen), seen


def test_blocking_calls_never_overrun_a_tiny_timeout(env, monkeypatch):
    seen = []

    def blocking(argv, **kw):  # every subprocess hangs until its timeout kills it
        seen.append(kw.get("timeout"))
        time.sleep(kw["timeout"])
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(finish_ci.subprocess, "run", blocking)
    t0 = time.monotonic()
    res = env.verdict(timeout_s=1)
    assert time.monotonic() - t0 < 2, seen
    assert sum(seen) <= 1.1, seen
    assert res["verdict"] != "green"

def test_mixed_fallback_reports_the_exact_red_run_under_match_descendant(env):
    sh = env.shas  # deliberate (R6): match says a descendant decided SOME workflow
    env.spec(list={
        **exact(env, run(1, sh["SHA"], "verify", "failure", minute=1),
                run(2, sh["SHA"], "lint", "cancelled", minute=2)),
        "run list --branch main": [run(3, sh["DESC1"], "lint", minute=3)]})
    res = env.verdict()
    assert (res["verdict"], res["match"]) == ("red", "descendant")
    assert (res["run_id"], res["run_sha"]) == (1, sh["SHA"])


def test_every_gh_call_is_pinned_to_the_origin_repo_never_gh_repo(env, monkeypatch):
    """GH_REPO naming another repo never redirects a lookup: every `gh run` call
    (exact list, branch list, jobs, failed log, baseline list) carries
    --repo <origin owner/name>, and no gh process sees GH_REPO."""
    sh = env.shas
    _git(env.root, "fetch", "-q", "origin")  # descendants local before origin turns remote
    _git(env.root, "remote", "set-url", "origin", "https://github.com/acme/widgets.git")
    (env.tmp / "gitconfig").write_text('[protocol "https"]\n\tallow = never\n')  # no network
    monkeypatch.setenv("GH_REPO", "evil/elsewhere")
    env.spec(list={
        **exact(env, run(1, sh["SHA"], "verify", "failure", minute=1),
                run(2, sh["SHA"], "lint", "cancelled", minute=2)),
        "run list --branch main": [run(3, sh["DESC1"], "lint", minute=3)],
        f"run list --commit {sh['BASE']}": [run(4, sh["BASE"], "verify", "failure")]},
        jobs={"1": FAILED_JOBS, "4": FAILED_JOBS}, logs={"1": FAILED_LOG})
    res = env.verdict(base_sha=sh["BASE"])
    assert (res["verdict"], res["match"], res["inherited"]) == ("red", "descendant", True)
    assert res["failed_checks"][0]["tests"]  # the --log-failed call answered too
    runs = [c for c in env.calls() if c[0] == "run"]
    assert {(c[1], c[2] if c[1] == "list" else c[3]) for c in runs} == {
        ("list", "--commit"), ("list", "--branch"), ("view", "--json"), ("view", "--log-failed")}
    for c in runs:
        assert c[-2:] == ["--repo", "acme/widgets"], c
    seen = (env.tmp / "gh.log.env").read_text().splitlines()
    assert seen and set(seen) == {"null"}, "a gh process saw GH_REPO"


@pytest.mark.parametrize("url,repo", [
    ("https://github.com/o/r.git", "o/r"),
    ("git@github.com:o/r", "o/r"),
    ("ssh://git@ghe.example.com:22/o/r.git", "ghe.example.com/o/r"),
    ("https://github.com/only-owner", None),
])
def test_repo_from_remote_url(env, url, repo):
    _git(env.root, "remote", "set-url", "origin", url)
    assert finish_ci._repo(env.root) == repo


def test_a_local_remote_gets_no_repo_flag_and_never_gh_repo(env, monkeypatch):
    monkeypatch.setenv("GH_REPO", "evil/elsewhere")
    env.spec(list=exact(env, run(1, env.shas["SHA"])))
    assert finish_ci._repo(env.root) is None
    assert env.verdict()["verdict"] == "green"
    assert all("--repo" not in c for c in env.calls())
    assert set((env.tmp / "gh.log.env").read_text().splitlines()) == {"null"}
