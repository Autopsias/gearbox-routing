"""Tests for the one rule (s08): any failed check keeps every open item open and
keeps the result off pass. Covers the s07 round-3 findings H1, H2, M1 and the
empty Sentry detail."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
from exposure_testlib import (  # noqa: E402
    CONF, DEFERRED_ADMIN, _card_sast, _cli_record, _run, cli, fast, git, lead, make_repo,
    needs_node, plant, record_run, rejected, unit)


def _invalid_admin(how, out):
    """A "covered" admin unit that fails one completion check."""
    u = unit("admin")
    chk = u["local_checks"][0]
    if how == "folder":
        u["reviewed_paths"] = chk["reviewed_paths"] = ["src"]
    elif how == "local":
        chk.update(method="local", artifact="agents/hunter-1/artifacts/out.txt")
        (out / "agents" / "hunter-1" / "artifacts").mkdir(parents=True)
        (out / "agents" / "hunter-1" / "artifacts" / "out.txt").write_text("x")
    else:
        chk.update(method="local", artifact="agents/hunter-1/artifacts/missing.txt")
    return u


# S7R3-H1: an invalid covered unit never resolves an inherited one (real validators)
@needs_node
@pytest.mark.parametrize("how", ["folder", "local", "missing-artifact", "valid"])
def test_invalid_covered_unit_never_resolves_an_inherited_unit(exposure_repo, how):
    admin = unit("admin")["coverage_id"]
    first = _run(exposure_repo, [lead("lead-a")],
                 [unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)])     # run 1: defer
    assert first["status"] == "warn"
    out = exposure.cf_output_dir(exposure_repo)
    ledger = [unit("api"), unit("admin") if how == "valid" else _invalid_admin(how, out)]
    second = record_run(plant(exposure_repo, [rejected("lead-a")], ledger=ledger, out=out),
                        exposure_repo)                                          # run 2: "covered"
    third = _run(exposure_repo)                                                 # run 3: omit
    if how == "valid":                  # the known positive: a valid run does resolve both
        assert (second["status"], third["status"]) == ("pass", "pass"), (second, third)
        return
    why = {"folder": "reviewed path is a folder", "local": "local check in a source-only run",
           "missing-artifact": "missing artifact: agents/hunter-1/artifacts/missing.txt"}[how]
    assert second["status"] == "warn" and why in second["detail"], second
    assert "nothing resolved here: the run failed a completion check" in second["detail"]
    assert {(i["kind"], i["id"]) for i in second["open"]} == {("lead", "lead-a"), ("unit", admin)}
    assert third["status"] == "warn", third
    assert ("missing here: lead Lead lead-a, open unit admin / Access control"
            in third["detail"])


# S7R3-H2: only the files repo-health writes are exempt from the dirty check
def test_changed_tracked_source_under_claude_health_is_never_a_pass(
        exposure_repo, exposure_node, exposure_home):
    src = exposure_repo / ".claude" / "health" / "auth.py"
    src.parent.mkdir()
    src.write_text("ALLOW = False\n")
    git(exposure_repo, "add", "-A")
    git(exposure_repo, "commit", "-qm", "auth under .claude/health")
    health.collect(exposure_repo)                   # its own output stays exempt ...
    assert exposure.dirty(exposure_repo) == []
    src.write_text("ALLOW = True\n")                # ... changed tracked source does not
    assert exposure.route(exposure_repo) == (
        "error", "worktree dirty, commit or stash first: .claude/health/auth.py")
    u = unit("api", reviewed_paths=[".claude/health/auth.py"])
    u["local_checks"][0]["reviewed_paths"] = [".claude/health/auth.py"]
    out = plant(exposure_repo, ledger=[u])
    assert cf_check.check_run(out, exposure_repo) == {
        "status": "pending",
        "detail": "worktree dirty, commit or stash first: .claude/health/auth.py"}
    p = _cli_record(exposure_repo, out, exposure_home)
    assert p.stdout.startswith("pending\nworktree dirty, commit or stash first: "
                               ".claude/health/auth.py"), p.stdout + p.stderr
    assert _card_sast(exposure_repo)["status"] == "pending"


# S7R3-M1: the marker is written through a fresh file, never through a planted one
@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_accept_never_follows_or_opens_a_planted_marker(tmp_path, exposure_repo, exposure_node,
                                                        exposure_home, kind):
    out = plant(exposure_repo)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n")
    if kind == "symlink":
        (out / exposure.ACCEPTED).symlink_to(outside)
    else:
        os.mkfifo(out / exposure.ACCEPTED)
    with fast():
        p = _cli_record(exposure_repo, out, exposure_home)  # check-run refuses the folder ...
        assert p.stdout.startswith("pending\nrun folder already holds gearbox-accepted.json")
        exposure.accept(out, "pass", [])                    # ... and accept() replaces it
    assert outside.read_text() == "keep me\n"
    assert not (out / exposure.ACCEPTED).is_symlink() and (out / exposure.ACCEPTED).is_file()
    assert json.loads((out / exposure.ACCEPTED).read_text())["status"] == "pass"


# S7R3-M1: the status is recorded only after acceptance succeeds
def test_a_stop_during_acceptance_never_leaves_a_pass(exposure_repo, exposure_node, monkeypatch):
    health.collect(exposure_repo)
    record_run(plant(exposure_repo, [lead("lead-a")]), exposure_repo)          # lead-a open

    def killed(*_):
        assert _card_sast(exposure_repo)["status"] == "pending"     # already pending here
        raise SystemExit("killed during accept")
    monkeypatch.setattr(exposure, "accept", killed)
    with pytest.raises(SystemExit):
        exposure.main(["check-run", str(plant(exposure_repo, [rejected("lead-a")])),
                       str(exposure_repo), "--record", "--agents", "25"])
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and "accepting a pass result" in card["detail"]


# llm-review r3: an empty Sentry detail never leaves the older detail under a new status
@pytest.mark.parametrize("text", ["", "  \n\t "])
def test_empty_detail_file_never_keeps_the_older_detail(tmp_path, exposure_home, text):
    repo = make_repo(tmp_path / "r")
    health.collect(repo)
    env = {**os.environ, "HOME": str(exposure_home)}
    df = tmp_path / "detail.txt"
    df.write_text("route: sentry | confirmed: Old finding at app.py:1")
    assert cli("record", repo, "fail", "--detail-file", df, env=env).returncode == 0
    df.write_text(text)
    assert cli("record", repo, "pass", "--detail-file", df, env=env).returncode == 0
    card = _card_sast(repo)
    assert card["status"] == "pending" and card["detail"].startswith("empty detail")
    assert "Old finding" not in card["detail"]


# ------------------------------------------------------------ s08 rework round 1

# S8-H1: check-run reads the exposure route itself; any route but cloudflare-quick is pending
@pytest.mark.parametrize("raw", ['{"exposed": "yes", "reason": "x"}', None], ids=["error", "sentry"])
def test_check_run_refuses_a_route_that_is_not_cloudflare_quick(tmp_path, exposure_home,
                                                               exposure_node, raw):
    repo = make_repo(tmp_path / "r", raw=raw)
    health.collect(repo)
    exposure.record(repo, "pass", "an older pass")
    out = plant(repo)
    p = _cli_record(repo, out, exposure_home)
    want = "pending\n" + (f"{repo.resolve()}/.claude/exposure.json: \"exposed\" must be true or "
                          "false" if raw else "route is sentry, not cloudflare-quick")
    assert p.stdout.startswith(want), p.stdout + p.stderr
    card = _card_sast(repo)
    assert card["status"] == "pending" and card["detail"].startswith("route: not cloudflare-quick (")
    assert not os.path.lexists(out / exposure.ACCEPTED)        # nothing accepted


# S8-H2: a candidate with unresolved reasons is a failed check and stays open (real validators)
@needs_node
def test_unresolved_candidate_with_a_final_record_is_not_a_pass(exposure_repo):
    _run(exposure_repo, [lead("lead-a")])
    cand = unit("api", "candidate", result_fingerprints=["cand-1"],
                unresolved=["validation_budget_exhausted"])
    res = _run(exposure_repo, [rejected("cand-1"), rejected("lead-a")], [cand])
    assert res["status"] == "warn", res
    assert "unresolved (validation_budget_exhausted): cand-1" in res["detail"]
    assert {(i["kind"], i["id"]) for i in res["open"]} == {("candidate", "cand-1"),
                                                         ("lead", "lead-a")}
    clean = unit("api", "candidate", result_fingerprints=["cand-1"])
    res = _run(exposure_repo, [rejected("cand-1"), rejected("lead-a")], [clean])
    assert res["status"] == "pass" and res["open"] == [], res      # a later valid run clears it


# S8-M1: a deferred unit is a failed check (real validators)
@needs_node
def test_a_run_with_deferred_work_never_resolves_a_finding(exposure_repo):
    found = [unit("api", "candidate", result_fingerprints=["conf-1"])]
    assert _run(exposure_repo, [CONF], found)["status"] == "fail"
    res = _run(exposure_repo, [rejected("conf-1")],
               found + [unit("admin", "deferred", **DEFERRED_ADMIN)])  # reject + new deferred
    assert res["status"] == "fail" and "1 unit(s) deferred" in res["detail"], res
    assert ("confirmed", "conf-1") in {(i["kind"], i["id"]) for i in res["open"]}
    res = _run(exposure_repo, [], [unit("api"), unit("admin")])        # cover the deferred unit
    assert res["status"] == "fail", res                                # conf-1 still open
    res = _run(exposure_repo, [rejected("conf-1")], found + [unit("admin")])   # a clean run
    assert res["status"] == "pass" and res["open"] == [], res


# S8R3-M1: every distinct evidence owner counts toward the agent floor (real validators)
@needs_node
def test_local_check_owners_raise_the_agent_floor(exposure_repo):
    health.collect(exposure_repo)
    _run(exposure_repo, [lead("lead-a")])                                     # lead-a open
    u = unit("api")
    u["local_checks"] += [dict(u["local_checks"][0], agent_id=f"checker-{i}") for i in (1, 2, 3)]
    out = plant(exposure_repo, [rejected("lead-a")], ledger=[u])
    assert cf_check.agent_floor([u], [rejected("lead-a")]) == 10    # 4 + 1 + 4 owners + 1
    p = cli("check-run", out, exposure_repo, "--record", "--agents", "9")
    assert p.stdout.startswith("pending\nagent count below the ledger floor: 9 reported, "
                               "the ledger shows at least 10"), p.stdout + p.stderr
    assert _card_sast(exposure_repo)["status"] == "pending"
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]
    # the known positive: the real validators accept this ledger, and 10 calls clear lead-a
    assert cf_check.check_run(out, exposure_repo, agents=10)["status"] == "pass"


# S8R3-M2: the run must carry the vendored execution policy, exactly
@pytest.mark.parametrize("policy", [..., "unsandboxed", cf_check.POLICY],
                         ids=["missing", "wrong", "valid"])
def test_execution_policy_is_required(exposure_repo, exposure_node, exposure_home, policy):
    health.collect(exposure_repo)
    _run(exposure_repo, [lead("lead-a")])                                     # lead-a open
    out = plant(exposure_repo, [rejected("lead-a")], execution_policy=policy)
    p = _cli_record(exposure_repo, out, exposure_home)
    if policy == cf_check.POLICY:       # the known positive: the right policy clears lead-a
        assert p.stdout.startswith("pass"), p.stdout + p.stderr
        return
    assert p.stdout.startswith("pending\nrun does not match this repo: execution_policy"), \
        p.stdout + p.stderr
    assert _card_sast(exposure_repo)["status"] == "pending"
    assert ("lead", "lead-a") in exposure.open_set(exposure_repo)[0]


# S9 Codex M2: a quick run has exactly one hunter wave (vendored SKILL.md, "quick"): a unit
# past wave 1 is a failed check, so an inherited lead stays open (real validators)
@needs_node
@pytest.mark.parametrize("wave", [2, 1])
def test_quick_run_past_wave_one_resolves_nothing(exposure_repo, wave):
    assert _run(exposure_repo, [lead("lead-a")])["status"] == "warn"           # run 1
    res = _run(exposure_repo, [rejected("lead-a")], [unit("api", wave=wave)])  # run 2
    if wave == 1:                       # the known positive: a one-wave run resolves lead-a
        assert res["status"] == "pass", res
        return
    assert res["status"] == "warn", res
    assert "quick profile allows one hunter wave; past wave 1: " in res["detail"]
    assert {(i["kind"], i["id"]) for i in res["open"]} == {("lead", "lead-a")}
    assert set(exposure.open_set(exposure_repo)[0]) == {("lead", "lead-a")}
