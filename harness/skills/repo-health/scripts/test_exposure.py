"""Tests for exposure.py: the exposed opt-in, the run folder, the --deep route,
and recording sec.sast on the Sentry routes."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import exposure  # noqa: E402
import health  # noqa: E402
import runlog  # noqa: E402
from exposure_testlib import (  # noqa: E402
    ACK, CONF, CONFIRMED, EXPOSED, GIT, HOSTILE,
    SCRIPT, SENTRY, _card_sast, _commit_change, _sentry_then_downgrade, cli,
    git, lead, make_repo, plant, record_run, unit)



def test_absent_file_is_not_exposed(tmp_path):
    repo = make_repo(tmp_path / "r")
    assert exposure.read_exposure(repo) == {
        "exposed": False, "reason": None, "since": None, "error": None}


def test_exposed_file_is_read(tmp_path):
    repo = make_repo(tmp_path / "r", EXPOSED)
    got = exposure.read_exposure(repo)
    assert got == {"exposed": True, "reason": EXPOSED["reason"],
                   "since": "2026-10-01", "error": None}


def test_not_exposed_file_is_read(tmp_path):
    repo = make_repo(tmp_path / "r", {"exposed": False, "reason": "internal only"})
    got = exposure.read_exposure(repo)
    assert got["exposed"] is False and got["error"] is None
    assert got["reason"] == "internal only"


@pytest.mark.parametrize("raw,why", [
    ("{not json", "not readable JSON"),
    ('["exposed"]', "must be a JSON object"),
    ('{"exposed": "yes", "reason": "x"}', '"exposed" must be true or false'),
    ('{"reason": "x"}', '"exposed" must be true or false'),
    ('{"exposed": true, "reason": "   "}', 'needs a non-empty "reason"'),
    ('{"exposed": true}', 'needs a non-empty "reason"'),
    ('{"exposed": true, "reason": 3}', '"reason" must be a string'),
    ('{"exposed": true, "reason": "x", "since": "Oct 1"}', '"since" must be YYYY-MM-DD'),
], ids=["bad-json", "not-object", "exposed-string", "exposed-missing", "reason-blank",
        "reason-missing", "reason-number", "since-bad"])
def test_malformed_file_is_an_error_never_not_exposed(tmp_path, raw, why):
    repo = make_repo(tmp_path / "r", raw=raw)
    got = exposure.read_exposure(repo)
    assert got["error"] and why in got["error"]
    assert str(repo.resolve() / ".claude" / "exposure.json") in got["error"]


def test_dangling_symlink_is_an_error(tmp_path):
    repo = make_repo(tmp_path / "r")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "exposure.json").symlink_to(tmp_path / "nowhere.json")
    assert exposure.read_exposure(repo)["error"]


def test_repo_given_as_dot_keeps_its_name(tmp_path, exposure_home, monkeypatch):
    repo = make_repo(tmp_path / "myrepo")
    monkeypatch.chdir(repo)
    out = exposure.cf_output_dir(".")
    assert out.parent.name.startswith("myrepo-") and len(out.parent.name) == len("myrepo-") + 8
    assert out.parent.parent == exposure_home.resolve() / ".gearbox-state" / "repo-health"
    assert out.is_dir() and out.name.endswith("-cloudflare")
    exposure.accept(plant(repo, out=out), "pass")
    assert exposure.prior_runs(".") == [out]


def test_two_clones_with_one_name_get_separate_parents(tmp_path, exposure_home):
    a = make_repo(tmp_path / "a" / "app")
    b = make_repo(tmp_path / "b" / "app")
    assert exposure.cf_output_dir(a).parent != exposure.cf_output_dir(b).parent


def test_two_calls_in_one_second_never_share_a_folder(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    now = datetime(2026, 10, 23, 12, 0, 0)
    first, second = exposure.cf_output_dir(repo, now), exposure.cf_output_dir(repo, now)
    assert first != second and first.is_dir() and second.is_dir()
    assert first.name.startswith("2026-10-23-120000-")


def test_output_dir_retries_when_a_name_is_taken(tmp_path, exposure_home, monkeypatch):
    repo = make_repo(tmp_path / "r")
    now = datetime(2026, 10, 23, 12, 0, 0)
    taken = exposure.runs_parent(repo) / "2026-10-23-120000-aaaaaa-cloudflare"
    taken.mkdir(parents=True)
    hexes = iter(["aaaaaa", "bbbbbb"])
    monkeypatch.setattr(runlog.secrets, "token_hex", lambda n: next(hexes))
    assert exposure.cf_output_dir(repo, now).name == "2026-10-23-120000-bbbbbb-cloudflare"


def test_output_dir_is_outside_the_repo(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    out = exposure.cf_output_dir(repo)
    assert repo.resolve() not in out.resolve().parents


def test_output_dir_inside_the_repo_raises(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r")
    monkeypatch.setenv("HOME", str(repo / "home"))     # planted: ~ inside the repo
    with pytest.raises(ValueError, match="inside the repo"):
        exposure.cf_output_dir(repo)
    assert not (repo / "home").exists()


def test_route_cli_exposed_repo(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r", EXPOSED)
    p = cli(repo, env={**os.environ, "HOME": str(exposure_home)})
    kind, out = p.stdout.splitlines()[0].split(" ", 1)
    assert p.returncode == 0 and kind == "cloudflare-quick"
    assert Path(out).is_dir() and repo.resolve() not in Path(out).resolve().parents
    assert p.stdout.splitlines()[1] == f"reason: {EXPOSED['reason']}"


def test_route_cli_plain_repo(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    p = cli(repo, env={**os.environ, "HOME": str(exposure_home)})
    assert (p.returncode, p.stdout) == (0, "sentry\n")


def test_route_cli_malformed_repo(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r", raw="{oops")
    p = cli(repo, env={**os.environ, "HOME": str(exposure_home)})
    assert p.returncode == 2 and p.stdout.startswith("error: ")
    assert "exposure.json" in p.stdout


def test_route_refuses_a_dirty_worktree(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r", EXPOSED)
    (repo / "app.py").write_text("print('changed')\n")
    p = cli(repo, env={**os.environ, "HOME": str(exposure_home)})
    assert (p.returncode, p.stdout) == (
        2, "error: worktree dirty, commit or stash first: app.py\n")
    assert not exposure.prior_runs(repo)        # no folder made for a refused run


def test_route_ignores_repo_health_output(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r", EXPOSED)
    (repo / ".claude" / "health").mkdir()
    (repo / ".claude" / "health" / "scorecard.json").write_text("{}")
    assert exposure.route(repo)[0] == "cloudflare-quick"
    (repo / "new.py").write_text("x = 1\n")       # untracked source is dirty
    assert exposure.route(repo) == ("error", "worktree dirty, commit or stash first: new.py")


def test_route_downgrade_survives_fresh_collects_until_acknowledged(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    (repo / ".gitignore").write_text(".claude/health/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore health")
    health.collect(repo)
    kind, out = exposure.route(repo)
    assert kind == "cloudflare-quick" and Path(out).is_dir()

    missing = str(repo.resolve() / ".claude" / "exposure.json")
    (repo / ".claude" / "exposure.json").unlink()          # removed, not committed
    line = ("route changed from cloudflare-quick to sentry: "
            f"exposure.json missing at {missing}")
    health.collect(repo, fresh=True)
    assert exposure.route(repo) == ("sentry-downgrade", missing)
    _sentry_then_downgrade(repo, line)

    git(repo, "rm", "-q", "--cached", ".claude/exposure.json")
    git(repo, "commit", "-qm", "drop exposure")             # removal committed
    for _ in range(3):
        health.collect(repo, fresh=True)
        assert _card_sast(repo)["status"] != "warn"         # the card forgot ...
        assert exposure.route(repo) == ("sentry-downgrade", missing)   # ... the route did not
        _sentry_then_downgrade(repo, line)

    (repo / ".claude" / "exposure.json").write_text(json.dumps(
        {"exposed": False, "reason": "moved behind the VPN"}))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "acknowledge")
    assert exposure.route(repo) == ("sentry", None)


# s11 review M: a git failure or an unreadable old blob is an error, never "never exposed"
def test_history_read_failure_is_an_error_never_sentry(tmp_path, exposure_home, monkeypatch):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qm", "drop exposure")
    env = {**os.environ, "HOME": str(exposure_home)}
    assert cli(repo, env=env).stdout.startswith("sentry-downgrade ")        # healthy git
    for p in (cli(repo, env={**env, "GIT_DIR": "/nonexistent"}),
              cli(tmp_path / "definitely-not-here", env=env)):
        assert p.returncode == 2 and p.stdout.startswith("error: history: "), p.stdout
    real_git, real_run = exposure._git, exposure.subprocess.run
    monkeypatch.setattr(exposure, "_git", lambda r, *a: (128, "") if a[0] == "log"
                        else real_git(r, *a))
    assert exposure.route(repo) == ("error", "history: git log of .claude/exposure.json failed")
    monkeypatch.setattr(exposure, "_git", real_git)
    monkeypatch.setattr(exposure.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 128, b"", b"") if "show" in cmd else real_run(cmd, **kw))
    kind, why = exposure.route(repo)
    assert kind == "error" and why.startswith("history: cannot read .claude/exposure.json"), why


def test_a_repo_with_no_commit_yet_is_still_sentry(tmp_path, exposure_home):
    (tmp_path / "new").mkdir()
    git(tmp_path / "new", "init", "-q")
    assert exposure.route(tmp_path / "new") == ("sentry", None)


def test_downgrade_keeps_a_sentry_fail_and_is_idempotent(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qm", "drop exposure")
    health.collect(repo)
    health.set_result(repo, "sec.sast", "fail", "route: sentry | confirmed: SQL injection")
    exposure.record_route(repo)
    exposure.record_route(repo)
    sast = _card_sast(repo)
    assert sast["status"] == "fail"
    assert sast["detail"].count("route changed from cloudflare-quick to sentry") == 1


@pytest.mark.parametrize("how", ["modified", "staged", "untracked"])
def test_uncommitted_acknowledgement_is_still_a_downgrade(tmp_path, exposure_home, how):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    f = repo / ".claude" / "exposure.json"
    if how == "untracked":
        git(repo, "rm", "-q", ".claude/exposure.json")
        git(repo, "commit", "-qm", "drop exposure")
        f.parent.mkdir()
    f.write_text(json.dumps(ACK))
    if how == "staged":
        git(repo, "add", ".claude/exposure.json")
    assert exposure.route(repo) == ("sentry-downgrade", str(f.resolve()))
    git(repo, "add", ".claude/exposure.json")
    git(repo, "commit", "-qm", "acknowledge")
    assert exposure.route(repo) == ("sentry", None)


def test_uncommitted_acknowledgement_is_named_in_the_record(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    (repo / ".claude" / "exposure.json").write_text(json.dumps(ACK))
    health.collect(repo)
    health.set_result(repo, "sec.sast", "pass", SENTRY)
    exposure.record_route(repo)
    assert _card_sast(repo)["detail"].startswith(
        "route changed from cloudflare-quick to sentry: exposure.json not committed with a reason")


# M5: the "was exposed" memory survives a fresh clone (git history, not ~/.gearbox-state)
def test_fresh_clone_of_a_once_exposed_repo_is_a_downgrade(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qm", "drop exposure")
    clone = tmp_path / "elsewhere" / "svc"
    subprocess.run([GIT, "clone", "-q", str(repo), str(clone)], check=True)
    assert exposure.prior_runs(clone) == []
    assert exposure.route(clone)[0] == "sentry-downgrade"
    plain = make_repo(tmp_path / "never", {"exposed": False})
    assert exposure.route(plain) == ("sentry", None)       # never exposed: no warning


def test_record_keeps_hostile_text_and_runs_nothing(tmp_path, exposure_home, exposure_node):
    hostile = {"exposed": True, "reason": 'API "$(touch REASON)" `touch R2`'}
    repo = make_repo(tmp_path / "svc", hostile)
    (repo / ".gitignore").write_text(".claude/health/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "ignore health")
    health.collect(repo)
    rec = dict(CONFIRMED, title=HOSTILE)
    out = plant(repo, [rec, lead("l1", blockers=(HOSTILE,))])
    work = tmp_path / "cwd"
    work.mkdir()
    env = {**os.environ, "HOME": str(exposure_home)}
    p = subprocess.run([sys.executable, str(SCRIPT), "check-run", str(out), str(repo),
                        "--record", "--agents", "25"], capture_output=True, text=True,
                       cwd=work, env=env)
    assert p.returncode == 0, p.stderr
    sast = _card_sast(repo)
    assert sast["status"] == "fail"
    assert f"confirmed: {HOSTILE} (high)" in sast["detail"]
    assert f"needs validation: {HOSTILE} - check:" in sast["detail"]
    assert sast["detail"].startswith(f"route: cloudflare-quick ({hostile['reason']}) | ")
    assert sast["detail"].endswith(f"agents: 25 calls (cap 40), model sonnet | output: {out}")
    assert not list(work.iterdir()) and not list(repo.glob("[A-Z]*"))   # nothing ran


def test_record_route_records_an_exposure_error(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", raw='{"exposed": "yes"}')
    health.collect(repo)
    exposure.record_route(repo)
    sast = _card_sast(repo)
    assert sast["status"] == "pending"
    assert sast["detail"] == (f"exposure error: {repo.resolve()}/.claude/exposure.json: "
                              '"exposed" must be true or false')


# M2: the route stops before the audit when check-run could not finish it
def test_route_without_node_stops_before_the_audit(exposure_repo, exposure_node):
    (Path(os.environ["PATH"]) / "node").unlink()
    assert exposure.route(exposure_repo) == (
        "error", "node not found: the Cloudflare validators need Node")
    assert exposure.prior_runs(exposure_repo) == []


# L2: the dirty refusal names up to five files
def test_dirty_refusal_names_at_most_five_files(exposure_repo):
    for i in range(7):
        (exposure_repo / f"f{i}.py").write_text("x = 1\n")
    msg = exposure.route(exposure_repo)[1]
    assert msg == ("worktree dirty, commit or stash first: "
                   "f0.py, f1.py, f2.py, f3.py, f4.py (+2 more)")


# R2-H1: a symlinked exposure.json (or .claude) never decides the route
def test_symlinked_exposure_file_is_an_error(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc")
    (repo / "acks").mkdir()
    (repo / "acks" / "ack.json").write_text(json.dumps(EXPOSED))
    (repo / ".claude").mkdir()
    (repo / ".claude" / "exposure.json").symlink_to("../acks/ack.json")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "symlinked opt-in")
    (repo / "acks" / "ack.json").write_text(json.dumps(ACK))   # uncommitted target change
    kind, why = exposure.route(repo)
    assert kind == "error" and why.endswith("must be a regular file in the repo, not a symlink")


def test_symlinked_claude_dir_is_an_error(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc")
    (repo / "elsewhere").mkdir()
    (repo / "elsewhere" / "exposure.json").write_text(json.dumps(ACK))
    (repo / ".claude").symlink_to("elsewhere")
    assert exposure.read_exposure(repo)["error"]


def test_acknowledgement_must_match_a_regular_file_in_head(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    f = repo / ".claude" / "exposure.json"
    f.unlink()
    f.symlink_to("/dev/null")                   # HEAD holds a symlink, not the opt-in
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "symlink in HEAD")
    f.unlink()
    f.write_text(json.dumps(ACK))               # a regular ack, but not what HEAD holds
    assert exposure.route(repo)[0] == "sentry-downgrade"


# R2-M1: history is read as JSON, so any valid spelling of the key counts
@pytest.mark.parametrize("raw", [
    '{"\\u0065xposed":true,"reason":"api"}',
    '{\n  "exposed":\n    true,\n  "reason": "api"\n}',
], ids=["escaped-key", "multiline"])
def test_history_parses_each_version_as_json(tmp_path, exposure_home, raw):
    repo = make_repo(tmp_path / "svc", raw=raw)
    assert exposure.read_exposure(repo)["exposed"] is True
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qm", "drop exposure")
    assert exposure.route(repo)[0] == "sentry-downgrade"


# R2-M2: a parent swapped for a symlink into the repo is caught after mkdir
def test_parent_swapped_during_mkdir_is_refused(tmp_path, exposure_home, monkeypatch):
    repo = make_repo(tmp_path / "svc")
    inside = repo / "inside"
    inside.mkdir()
    parent = exposure.runs_parent(repo)
    parent.mkdir(parents=True)
    real_mkdir = os.mkdir

    def swap_then_mkdir(path, *a, **k):
        if str(path).endswith("-cloudflare"):
            parent.rename(parent.with_name("moved"))
            parent.symlink_to(inside)
        return real_mkdir(path, *a, **k)
    monkeypatch.setattr(exposure.os, "mkdir", swap_then_mkdir)
    with pytest.raises(ValueError, match="refused"):
        exposure.cf_output_dir(repo)
    assert list(inside.iterdir()) == []         # the folder made inside was removed


# R2-M3: the Sentry routes record from a file too, with no shell
def test_record_from_a_detail_file_keeps_hostile_text(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc")
    health.collect(repo)
    detail = f"route: sentry | confirmed: {HOSTILE} at app.py:1"
    df = tmp_path / "detail.txt"
    df.write_text(detail + "\n")
    work = tmp_path / "cwd"
    work.mkdir()
    p = subprocess.run([sys.executable, str(SCRIPT), "record", str(repo), "fail",
                        "--detail-file", str(df)], capture_output=True, text=True, cwd=work,
                       env={**os.environ, "HOME": str(exposure_home)})
    assert p.returncode == 0, p.stderr
    assert (_card_sast(repo)["status"], _card_sast(repo)["detail"]) == ("fail", detail)
    assert not list(work.iterdir())


# R3-M3: record only where the Sentry pass owns sec.sast, and never pass on a downgrade
def test_record_refuses_an_exposed_repo_and_pass_on_a_downgrade(tmp_path, exposure_home):
    env = {**os.environ, "HOME": str(exposure_home)}
    df = tmp_path / "detail.txt"
    df.write_text("route: sentry | 0 confirmed\n")
    exp = make_repo(tmp_path / "exp", EXPOSED)
    p = cli("record", exp, "pass", "--detail-file", df, env=env)
    assert p.returncode == 2 and "this repo is exposed" in p.stderr
    down = make_repo(tmp_path / "down", EXPOSED)
    git(down, "rm", "-q", ".claude/exposure.json")
    git(down, "commit", "-qm", "drop exposure")
    health.collect(down)
    p = cli("record", down, "pass", "--detail-file", df, env=env)
    assert p.returncode == 2 and "pass on a downgraded route" in p.stderr
    assert cli("record", down, "warn", "--detail-file", df, env=env).returncode == 0
    broken = make_repo(tmp_path / "broken", raw="{oops")
    assert cli("record", broken, "pass", "--detail-file", df, env=env).returncode == 2


# R3-L1: a stop that is not about exposure.json says "route error"
def test_non_exposure_stop_is_a_route_error(exposure_repo, exposure_node):
    (Path(os.environ["PATH"]) / "node").unlink()
    health.collect(exposure_repo)
    exposure.record_route(exposure_repo)
    assert _card_sast(exposure_repo)["detail"] == \
        "route error: node not found: the Cloudflare validators need Node"


# R5-M2: a merge that drops exposure.json does not hide the history behind it
def test_exposure_behind_a_merge_still_counts(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "r")
    git(repo, "checkout", "-qb", "side")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "exposure.json").write_text(json.dumps(EXPOSED))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "expose")
    git(repo, "checkout", "-q", "-")
    _commit_change(repo, "app.py", "print('main')\n")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    git(repo, "rm", "-qf", ".claude/exposure.json")
    git(repo, "commit", "-qm", "merge side without exposure.json")
    assert exposure.route(repo, make_dir=False)[0] == "sentry-downgrade"


# S7-M4: leaving the exposed route never hides what is still open
def test_acknowledged_exit_keeps_an_open_confirmed_finding(exposure_repo, exposure_node, exposure_home):
    health.collect(exposure_repo)
    record_run(plant(exposure_repo, [CONF], ledger=[unit("api", "candidate",
                                                   result_fingerprints=["conf-1"])]), exposure_repo)
    (exposure_repo / ".claude" / "exposure.json").write_text(json.dumps(
        {"exposed": False, "reason": "moved behind the VPN"}))
    git(exposure_repo, "commit", "-qam", "no longer exposed")
    assert exposure.route(exposure_repo, make_dir=False)[0] == "sentry"
    df = exposure_repo.parent / "detail.txt"
    df.write_text("route: sentry | 0 confirmed")
    env = {**os.environ, "HOME": str(exposure_home)}
    assert cli("record", exposure_repo, "pass", "--detail-file", df, env=env).returncode == 0
    card = _card_sast(exposure_repo)
    assert card["status"] == "fail"
    assert "still open from the exposed route: confirmed Planted IDOR" in card["detail"]


def test_downgrade_keeps_open_items(exposure_repo, exposure_node, exposure_home):
    health.collect(exposure_repo)
    record_run(plant(exposure_repo, [CONF], ledger=[unit("api", "candidate",
                                                   result_fingerprints=["conf-1"])]), exposure_repo)
    git(exposure_repo, "rm", "-q", ".claude/exposure.json")
    git(exposure_repo, "commit", "-qm", "drop exposure")
    exposure.record(exposure_repo, "warn", "route: sentry | 0 confirmed")   # as the Sentry pass did
    exposure.record_route(exposure_repo)
    card = _card_sast(exposure_repo)
    assert card["status"] == "fail" and "confirmed Planted IDOR" in card["detail"]
    assert exposure.open_floor(exposure_repo, "pass", "") [0] == "fail"


def test_open_lead_keeps_at_least_warn(exposure_repo, exposure_node):
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)
    assert exposure.open_floor(exposure_repo, "pass", "d") == (
        "warn", "d | still open from the exposed route: lead Lead lead-a")
    assert exposure.open_floor(exposure_repo, "fail", "d")[0] == "fail"
