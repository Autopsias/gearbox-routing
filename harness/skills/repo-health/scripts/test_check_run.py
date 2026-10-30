"""Tests for check-run, gearbox's own completion check for one Cloudflare quick run:
how one run's documents map to a status.

Most tests put a fake `node` on PATH so they test the mapping, not Node;
the ones marked needs_node run the real validators."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cf_check  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
from exposure_testlib import (  # noqa: E402
    CONF, CONFIRMED, EXPOSED, REAL_NODE, REAL_VENDORED_TREE, TODAY,
    _card_sast, _cli_record, _commit_change, _reviewing, _run, archived_candidate,
    cli, git, lead, make_repo, needs_node, plant,
    record_run, rejected, unit)


def test_check_run_clean(exposure_repo, exposure_node):
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pass", res
    assert res["detail"].startswith("new since no prior run: 0 leads, 0 deferred units; carried: 0")


def test_check_run_confirmed_is_fail(exposure_repo, exposure_node):
    res = cf_check.check_run(plant(exposure_repo, [CONFIRMED, lead("l1")]), exposure_repo)
    assert res["status"] == "fail"
    assert "confirmed: Planted bug (high) (src/api.py:7)" in res["detail"]


def test_check_run_lead_is_warn_with_its_fact(exposure_repo, exposure_node):
    res = cf_check.check_run(plant(exposure_repo, [lead("l1")]), exposure_repo)
    assert res["status"] == "warn"
    assert "needs validation: The proxy may strip the header." in res["detail"]
    assert "severity" not in res["detail"] and "(high)" not in res["detail"]


def test_check_run_rejected_is_dropped(exposure_repo, exposure_node):
    rej = {"verdict": "rejected", "fingerprint": "r1", "title": "Not a bug", "trace": []}
    res = cf_check.check_run(plant(exposure_repo, [rej]), exposure_repo)
    assert res["status"] == "pass" and "Not a bug" not in res["detail"]


@pytest.mark.parametrize("status", ["in_progress", "incomplete", ...])
def test_check_run_incomplete_status_is_warn(exposure_repo, exposure_node, status):
    res = cf_check.check_run(plant(exposure_repo, run_status=status,
                                   incomplete_reason="validation_budget_exhausted"), exposure_repo)
    assert res["status"] == "warn"
    assert "incomplete run: run_status" in res["detail"]


@pytest.mark.parametrize("state", ["in_progress", "blocked", "planned"])
def test_check_run_open_unit_is_warn(exposure_repo, exposure_node, state):
    extra = {"unresolved": ["Policy file absent."]} if state == "blocked" else {}
    res = cf_check.check_run(plant(exposure_repo, ledger=[unit("api"), unit("db", state, **extra)]),
                             exposure_repo)
    assert res["status"] == "warn" and f"1 unit(s) {state}" in res["detail"]


def test_check_run_missing_artifact_is_warn(exposure_repo, exposure_node):
    u = unit("api")
    u["local_checks"][0].update(method="local", artifact="agents/hunter-1/artifacts/out.txt")
    res = cf_check.check_run(plant(exposure_repo, ledger=[u]), exposure_repo)
    assert res["status"] == "warn"
    assert "missing artifact: agents/hunter-1/artifacts/out.txt" in res["detail"]


def test_check_run_local_check_is_warn_even_with_artifact(exposure_repo, exposure_node):
    u = unit("api")
    u["local_checks"][0].update(method="local", artifact="agents/hunter-1/artifacts/out.txt")
    out = plant(exposure_repo, ledger=[u])
    (out / "agents" / "hunter-1" / "artifacts").mkdir(parents=True)
    (out / "agents" / "hunter-1" / "artifacts" / "out.txt").write_text("x")
    res = cf_check.check_run(out, exposure_repo)
    assert res["status"] == "warn" and "local check in a source-only run" in res["detail"]


def test_check_run_failing_validator_is_pending(exposure_repo, exposure_node):
    exposure_node(1, "ERROR: findings[0] is not an object")
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res == {"status": "pending", "detail":
                   "validate-findings.cjs failed: ERROR: findings[0] is not an object"}


def test_check_run_without_node_is_pending(exposure_repo, exposure_node):
    (Path(os.environ["PATH"]) / "node").unlink()
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res == {"status": "pending",
                   "detail": "node not found: the Cloudflare validators need Node"}


# 5a: unresolved candidate
def test_check_run_unresolved_candidate_is_warn(exposure_repo, exposure_node):
    led = [unit("api", "candidate", result_fingerprints=["cand-1"])]
    res = cf_check.check_run(plant(exposure_repo, [], ledger=led), exposure_repo)
    assert res["status"] == "warn"
    assert "candidate without a final record: cand-1" in res["detail"]


# 5b: the run must describe this repo at this commit
def test_check_run_report_from_another_repo_is_pending(tmp_path, exposure_repo, exposure_node):
    other = make_repo(tmp_path / "other", EXPOSED)
    out = plant(exposure_repo, target=str(other.resolve()))
    assert cf_check.check_run(out, exposure_repo) == {
        "status": "pending", "detail": "run does not match this repo: target"}


def test_check_run_report_from_older_commit_is_pending(exposure_repo, exposure_node):
    old = exposure.head(exposure_repo)
    (exposure_repo / "app.py").write_text("print('v2')\n")
    git(exposure_repo, "commit", "-qam", "v2")
    out = plant(exposure_repo, source_ref=old)
    assert cf_check.check_run(out, exposure_repo)["detail"] == "run does not match this repo: source_ref"


def test_check_run_same_commit_with_changed_file_is_pending(exposure_repo, exposure_node):
    out = plant(exposure_repo)
    (exposure_repo / "app.py").write_text("print('changed')\n")
    assert cf_check.check_run(out, exposure_repo) == {
        "status": "pending", "detail": "worktree dirty, commit or stash first: app.py"}


def test_check_run_wrong_profile_or_folder_is_pending(tmp_path, exposure_repo, exposure_node):
    assert cf_check.check_run(plant(exposure_repo, profile="standard"), exposure_repo)["detail"] \
        == "run does not match this repo: profile"
    stray = tmp_path / "stray-cloudflare"
    stray.mkdir()
    assert cf_check.check_run(plant(exposure_repo, out=stray), exposure_repo)["detail"] \
        == "run does not match this repo: run folder"


# 5c: deferred coverage warns, deliberate exclusions do not
def test_check_run_deferred_discovery_is_warn(exposure_repo, exposure_node):
    led = [unit("api"), unit("admin", "deferred", wave=2,
                             unresolved=["quick_profile_final_critic"])]
    res = cf_check.check_run(plant(exposure_repo, ledger=led), exposure_repo)
    assert res["status"] == "warn"
    assert "deferred: 1 (quick_profile_final_critic): admin / Access control" in res["detail"]


def test_check_run_scope_exclusion_does_not_warn(exposure_repo, exposure_node):
    led = [unit("api"), unit("vendor", "not_applicable", unresolved=["no entry point"])]
    res = cf_check.check_run(plant(exposure_repo, ledger=led), exposure_repo)
    assert res["status"] == "pass"
    assert "excluded on purpose: 1 (no entry point)" in res["detail"]


def test_out_of_scope_unit_in_a_whole_repo_run_is_not_a_pass(exposure_repo, exposure_node):
    led = [unit("api"), unit("vendor", "out_of_scope", unresolved=["outside scope_paths"])]
    res = cf_check.check_run(plant(exposure_repo, ledger=led), exposure_repo)
    assert res["status"] == "warn" and "1 unit(s) out_of_scope" in res["detail"]


def test_symlinked_report_file_is_not_a_pass(tmp_path, exposure_repo, exposure_node):
    out = plant(exposure_repo)
    (tmp_path / "elsewhere.md").write_text("# real text\n")
    (out / "REPORT.md").unlink()
    (out / "REPORT.md").symlink_to(tmp_path / "elsewhere.md")
    assert cf_check.check_run(out, exposure_repo)["status"] == "warn"


# 5d: every decisive blocker survives into the detail
def test_check_run_lead_keeps_every_blocker(exposure_repo, exposure_node):
    rec = lead("l2", blockers=("The proxy may strip X-User.", "The route may be internal."),
               line=40)
    res = cf_check.check_run(plant(exposure_repo, [rec]), exposure_repo)
    assert ("needs validation: The proxy may strip X-User.; The route may be internal."
            " - check: deployment: Ask the owner whether the proxy strips X-User."
            " (src/api.py:40)") in res["detail"]


# 5e: contract guard
def test_contract_tree_matches_vendor_json_and_the_files_on_disk():
    vendor_json = Path(__file__).parent.parent / "vendor.json"
    tree = next(v["tree"] for v in json.loads(vendor_json.read_text())
                if v["name"] == "cloudflare-security-audit")
    assert tree == exposure.CONTRACT_TREE
    assert exposure._vendored_tree() == exposure.CONTRACT_TREE     # hashed from disk


def test_check_run_after_a_repin_is_pending(exposure_repo, exposure_node, monkeypatch):
    import vendor
    # the files on disk hash to another tree; vendor.json is never consulted
    monkeypatch.setattr(exposure, "_vendored_tree", REAL_VENDORED_TREE)
    monkeypatch.setattr(vendor, "local_tree", lambda entry: "0" * 40)
    assert cf_check.check_run(plant(exposure_repo), exposure_repo) == {"status": "pending", "detail":
        "check-run was written for tree ccbc33ed; re-check the run contract after the re-pin"}
    assert exposure.route(exposure_repo)[1].startswith("check-run was written for tree")   # M2


# 5j: new leads are not hidden behind standing warns
def test_check_run_names_what_is_new_since_the_prior_run(exposure_repo, exposure_node):
    prev = exposure.cf_output_dir(exposure_repo, datetime(2026, 10, 1, 9, 0, 0))
    deferred_a = unit("admin", "deferred", unresolved=["quick_profile_final_critic"])
    deferred_b = unit("billing", "deferred", unresolved=["quick_profile_final_critic"])
    record_run(plant(exposure_repo, [lead("lead-a")], ledger=[unit("api"), deferred_a], out=prev),
               exposure_repo)
    rec_b = lead("lead-b", blockers=("Brand new fact.",))
    res = cf_check.check_run(plant(exposure_repo, [lead("lead-a"), rec_b],
                                   ledger=[unit("api"), deferred_a, deferred_b]), exposure_repo)
    assert res["detail"].startswith(
        f"new since {TODAY}: 1 leads, 1 deferred units; carried: 2")
    assert res["detail"].index("Brand new fact.") < res["detail"].index("The proxy may strip")
    assert "billing / Access control, admin / Access control" in res["detail"]


# real validators on a planted clean fixture
@pytest.mark.skipif(REAL_NODE is None, reason="node not on PATH: the real validators need it")
def test_clean_fixture_passes_the_real_validators(exposure_repo):
    res = cf_check.check_run(plant(exposure_repo), exposure_repo)
    assert res["status"] == "pass", res


@pytest.mark.skipif(REAL_NODE is None, reason="node not on PATH: the real validators need it")
def test_real_validator_rejects_a_broken_ledger(exposure_repo):
    out = plant(exposure_repo, ledger=[unit("api", "covered", agent_id=None)])
    res = cf_check.check_run(out, exposure_repo)
    assert res["status"] == "pending" and "validate-coverage-ledger.cjs failed" in res["detail"]


def test_record_needs_an_agent_count(exposure_repo):
    p = cli("check-run", exposure_repo, exposure_repo, "--record")
    assert p.returncode == 2 and "--record needs --agents N" in p.stderr


# M4: the agent cap is bound to the run and to the recorded count
def test_agent_cap_exceeded_is_pending(exposure_repo, exposure_node):
    assert cf_check.check_run(plant(exposure_repo), exposure_repo, agents=41) == {
        "status": "pending", "detail": "agent cap exceeded: 41 calls, cap 40"}
    assert cf_check.check_run(plant(exposure_repo), exposure_repo, agents=40)["status"] == "pass"
    assert cf_check.check_run(plant(exposure_repo, budget=None), exposure_repo)["detail"] \
        == "run does not match this repo: budget"


# H3: an empty or unfinished audit never passes
def test_empty_ledger_is_not_a_pass(exposure_repo, exposure_node):
    res = cf_check.check_run(plant(exposure_repo, ledger=[]), exposure_repo)
    assert res["status"] == "warn" and "no coverage unit reviewed" in res["detail"]


def test_missing_report_file_is_not_a_pass(exposure_repo, exposure_node):
    out = plant(exposure_repo)
    (out / "REPORT.md").unlink()
    res = cf_check.check_run(out, exposure_repo)
    assert res["status"] == "warn" and "report file not readable text: REPORT.md (" in res["detail"]


@pytest.mark.skipif(REAL_NODE is None, reason="node not on PATH: the real validators need it")
def test_real_validators_accept_an_empty_run_that_check_run_refuses(exposure_repo):
    res = cf_check.check_run(plant(exposure_repo, ledger=[]), exposure_repo)
    assert res["status"] == "warn" and "no coverage unit reviewed" in res["detail"]


# H4: read once; a lost or broken file is pending, never empty data
def test_file_lost_after_validation_keeps_the_read_content(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("l1")])
    exposure_node(pre=f"/bin/rm -f '{out}/findings.json' '{out}/coverage-ledger.json'")
    res = cf_check.check_run(out, exposure_repo)
    assert res["status"] == "warn" and "needs validation:" in res["detail"]
    assert not (out / "findings.json").exists()             # the stub really removed it


@pytest.mark.parametrize("name", ["run-metadata.json", "coverage-ledger.json", "findings.json"])
def test_missing_or_broken_document_is_pending(exposure_repo, exposure_node, name):
    out = plant(exposure_repo)
    (out / name).unlink()
    assert cf_check.check_run(out, exposure_repo)["detail"].startswith(f"cannot read {name}")
    (out / name).write_text("{broken")
    assert cf_check.check_run(out, exposure_repo)["detail"].startswith(f"cannot parse {name}")


# s07 inventory: a document is a bounded regular file, never a FIFO, link or giant
def test_oversized_or_special_document_is_pending(exposure_repo, exposure_node):
    out = plant(exposure_repo)
    (out / "findings.json").write_bytes(b"[" + b" " * exposure.MAX_DOC + b"]")
    assert "findings.json: over" in cf_check.check_run(out, exposure_repo)["detail"]
    (out / "findings.json").unlink()
    os.mkfifo(out / "findings.json")                 # a read would hang for ever
    assert "findings.json: not a regular file" in cf_check.check_run(out, exposure_repo)["detail"]
    (out / "findings.json").unlink()
    (out / "findings.json").symlink_to(out / "coverage-ledger.json")
    assert "findings.json: not a regular file" in cf_check.check_run(out, exposure_repo)["detail"]


# M1: a validator that hangs or cannot start is pending, through the CLI
def test_validator_timeout_and_launch_failure_are_pending(exposure_repo, exposure_node, exposure_home):
    out = plant(exposure_repo)
    env = {**os.environ, "HOME": str(exposure_home), "EXPOSURE_VALIDATOR_TIMEOUT": "1"}
    exposure_node(pre="/bin/sleep 5")
    p = cli("check-run", out, exposure_repo, env=env)
    assert p.stdout.splitlines() == ["pending", "validate-findings.cjs did not finish in 1 s"]
    node = Path(os.environ["PATH"]) / "node"
    node.write_text("#!/nonexistent/interpreter\n")
    p = cli("check-run", out, exposure_repo, env=env)
    assert p.stdout.splitlines()[0] == "pending"
    assert p.stdout.splitlines()[1].startswith("validate-findings.cjs could not start:")


# R2-H2: a commit made while check-run runs never yields a stale pass
def test_commit_during_check_run_is_pending(exposure_repo, exposure_node, monkeypatch):
    out = plant(exposure_repo)
    real = cf_check._incomplete

    def commit_midway(*args):
        (exposure_repo / "app.py").write_text("print('v2')\n")
        git(exposure_repo, "commit", "-qam", "v2")
        return real(*args)
    monkeypatch.setattr(cf_check, "_incomplete", commit_midway)
    assert cf_check.check_run(out, exposure_repo) == {
        "status": "pending", "detail": "source changed during check-run"}


# R2-M4: --agents below what the ledger shows is pending; both numbers recorded
def test_agents_below_the_ledger_floor_is_pending(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead("l1")], ledger=[unit("api"), unit("db", agent_id="hunter-2")])
    assert cf_check.agent_floor(json.loads((out / "coverage-ledger.json").read_text()),
                                [lead("l1")]) == 8          # 4 recon + 1 critic + 2 + 1
    assert cf_check.check_run(out, exposure_repo, agents=7) == {"status": "pending", "detail":
        "agent count below the ledger floor: 7 reported, the ledger shows at least 8"}
    res = cf_check.check_run(out, exposure_repo, agents=8)
    assert res["status"] == "warn" and "ledger floor 8 agent calls" in res["detail"]


# R2-L3: the detail lists the first five leads and points at the report
def test_detail_lists_at_most_five_leads(exposure_repo, exposure_node):
    out = plant(exposure_repo, [lead(f"l{i}", blockers=(f"Fact {i}.",)) for i in range(7)])
    detail = cf_check.check_run(out, exposure_repo)["detail"]
    assert detail.count("needs validation:") == 5
    assert f"(+2 more: see {out / 'NEEDS-VALIDATION.md'})" in detail


# R3-H1: an archived candidate needs a final record too (real validators)
@needs_node
def test_archived_candidate_without_a_final_record_is_not_a_pass(exposure_repo):
    res = cf_check.check_run(plant(exposure_repo, [], ledger=archived_candidate()), exposure_repo)
    assert res["status"] == "warn"
    assert "candidate without a final record: cand-1" in res["detail"]
    res = cf_check.check_run(plant(exposure_repo, [rejected("cand-1")],
                                   ledger=archived_candidate()), exposure_repo)
    # the final record settles cand-1; the wave-2 live unit is the only failed check left (s10)
    assert res["status"] == "warn" and "candidate without a final record" not in res["detail"]
    assert "incomplete run: quick profile allows one hunter wave; past wave 1: " in res["detail"]


# R3-M1: the whole repo, never a scoped run
@pytest.mark.parametrize("scope", [..., [], ["README.md"]], ids=["missing", "empty", "narrowed"])
def test_scoped_run_is_pending(exposure_repo, exposure_node, scope):
    assert cf_check.check_run(plant(exposure_repo, scope_paths=scope), exposure_repo) == {
        "status": "pending", "detail": "run does not match this repo: scope_paths"}


# attacker pass: reviewed paths must exist, report files must not be empty
def test_reviewed_path_outside_the_repo_is_not_a_pass(exposure_repo, exposure_node):
    u = unit("api", reviewed_paths=["src/ghost.py"])
    res = cf_check.check_run(plant(exposure_repo, ledger=[u]), exposure_repo)
    assert res["status"] == "warn"
    assert "reviewed path not tracked at HEAD: src/ghost.py" in res["detail"]


def test_empty_report_file_is_not_a_pass(exposure_repo, exposure_node):
    out = plant(exposure_repo)
    (out / "REPORT.md").write_text("")
    res = cf_check.check_run(out, exposure_repo)
    assert res["status"] == "warn" and "report file not readable text: REPORT.md (" in res["detail"]


# R4-M1: a file hidden by a committed .gitignore is not reviewed source
def test_ignored_file_is_not_reviewed_source(exposure_repo, exposure_node):
    (exposure_repo / ".gitignore").write_text("local_cfg.py\n")
    git(exposure_repo, "add", ".gitignore")
    git(exposure_repo, "commit", "-qm", "ignore local config")
    (exposure_repo / "local_cfg.py").write_text("TOKEN_CHECK = False\n")
    assert exposure.dirty(exposure_repo) == []            # git status alone cannot see it
    res = cf_check.check_run(plant(exposure_repo, ledger=[unit("api", reviewed_paths=["local_cfg.py"])]),
                             exposure_repo)
    assert res["status"] == "warn"
    assert "reviewed path not tracked at HEAD: local_cfg.py" in res["detail"]


@needs_node
def test_symlink_to_an_ignored_file_is_not_reviewed_source(exposure_repo):
    (exposure_repo / ".gitignore").write_text("ignored.py\n")
    os.symlink("ignored.py", exposure_repo / "reviewed.py")
    git(exposure_repo, "add", "-A")
    git(exposure_repo, "commit", "-qm", "link reviewed.py to an ignored file")
    (exposure_repo / "ignored.py").write_text("TOKEN_CHECK = False\n")
    assert exposure.dirty(exposure_repo) == []
    res = cf_check.check_run(plant(exposure_repo, ledger=[_reviewing("reviewed.py")]), exposure_repo)
    assert res["status"] == "warn", res
    assert ("reviewed path is a symlink or submodule at HEAD, not a regular file: reviewed.py"
            in res["detail"])


def test_submodule_is_not_reviewed_source(exposure_repo, exposure_node):
    (exposure_repo / "sub").mkdir()                       # an uninitialised submodule: clean tree
    git(exposure_repo, "update-index", "--add", "--cacheinfo",
        f"160000,{exposure.head(exposure_repo)},sub")
    git(exposure_repo, "commit", "-qm", "add a submodule entry")
    res = cf_check.check_run(plant(exposure_repo, ledger=[_reviewing("sub")]), exposure_repo)
    assert res["status"] == "warn" and "symlink or submodule at HEAD" in res["detail"]


# S7-M1: a folder is not a regular file, for review or revalidation (real validators)
@needs_node
def test_folder_review_is_not_a_pass_and_does_not_revalidate(exposure_repo):
    res = cf_check.check_run(plant(exposure_repo, ledger=[_reviewing("src")]), exposure_repo)
    assert res["status"] == "warn", res
    assert "reviewed path is a folder, not a regular file: src" in res["detail"]
    _run(exposure_repo, [CONF], [unit("api", "candidate", result_fingerprints=["conf-1"])])
    _commit_change(exposure_repo, "src/api.py", "x = 2\n")
    folder = _reviewing("src")
    folder.update(prior_status="prior_confirmed_changed_source", revalidates=["conf-1"])
    assert _run(exposure_repo, [], [folder])["status"] == "fail"


# S7-M2: malformed bookkeeping, or any crash, records pending over an older pass
@pytest.mark.parametrize("meta", [
    {"resolved_prior_leads": [{"fingerprint": [], "reason": "x"}]},
    {"resolved_prior_leads": {"lead-a": "x"}},
    {"resolved_prior_leads": [{"fingerprint": "lead-a", "reason": "x" * 2001}]}],
    ids=["list-fingerprint", "not-a-list", "long-reason"])
def test_bad_bookkeeping_records_pending_over_an_old_pass(exposure_repo, exposure_node, exposure_home, meta):
    health.collect(exposure_repo)
    assert _cli_record(exposure_repo, plant(exposure_repo), exposure_home).stdout.startswith("pass")
    assert _card_sast(exposure_repo)["status"] == "pass"
    p = _cli_record(exposure_repo, plant(exposure_repo, **meta), exposure_home)
    assert p.stdout.startswith("pending\nbad bookkeeping field: resolved_prior_leads")
    assert _card_sast(exposure_repo)["status"] == "pending"
    bad_unit = unit("api", revalidates="conf-1")
    assert "revalidates on unit" in cf_check.check_run(plant(exposure_repo, ledger=[bad_unit]),
                                                       exposure_repo)["detail"]


def test_a_crash_inside_check_run_records_pending(exposure_repo, exposure_node, monkeypatch):
    health.collect(exposure_repo)
    exposure.record(exposure_repo, "pass", "an older pass")

    def boom(*a):
        raise RuntimeError("planted crash")
    monkeypatch.setattr(cf_check, "check_run", boom)
    assert exposure.main(["check-run", str(plant(exposure_repo)), str(exposure_repo), "--record",
                          "--agents", "25"]) == 2
    card = _card_sast(exposure_repo)
    assert card["status"] == "pending" and "check-run error: RuntimeError: planted crash" in \
        card["detail"]


# ------------------------------------------------------------ s07 rework round 2

# S7R2-M1: run text that is not valid UTF-8 records pending over an older pass
def test_run_text_that_is_not_utf8_records_pending(exposure_repo, exposure_node, exposure_home):
    health.collect(exposure_repo)
    assert _cli_record(exposure_repo, plant(exposure_repo), exposure_home).stdout.startswith("pass")
    bad = dict(lead("lead-a"), title="Lead \ud800")
    p = _cli_record(exposure_repo, plant(exposure_repo, [bad]), exposure_home)
    assert p.stdout.startswith("pending\nfindings.json holds text that is not valid UTF-8"), \
        p.stdout + p.stderr
    assert _card_sast(exposure_repo)["status"] == "pending"
