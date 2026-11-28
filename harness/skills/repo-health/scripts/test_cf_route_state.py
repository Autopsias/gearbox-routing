"""Tests for producer completion/evidence_runs, ~/.gearbox-state sidecars, cf_route_state, and show-open.

Zero vendor dependency: runs with the stub node fixture and never invokes the vendored
Cloudflare validator tree.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cf_route_state  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
import runlog  # noqa: E402
from exposure_testlib import (  # noqa: E402
    DEFERRED_ADMIN, EXPOSED, _card_sast, git, lead, make_repo, plant, rejected, unit)


def _record_cf(repo, out, agents=25):
    rc = exposure.main(["check-run", str(out), str(repo), "--record", "--agents", str(agents)])
    assert rc == 0
    return json.loads((out / runlog.ACCEPTED).read_text())


def _local_lead(fp, blockers=("Needs a local check.",)):
    rec = lead(fp, blockers=blockers)
    rec["validation_plan"] = {"local": "Run pytest on the handler."}
    return rec


def _make_legacy_marker(out):
    mark_path = out / runlog.ACCEPTED
    mark = json.loads(mark_path.read_text())
    mark.pop("complete", None)
    for item in mark["open"]:
        item.pop("evidence_runs", None)
    mark_path.write_text(json.dumps(mark))


def test_check_run_record_stamps_boolean_complete(exposure_repo, exposure_node):
    health.collect(exposure_repo)
    bad = plant(exposure_repo)
    assert exposure.main(["check-run", str(bad), str(exposure_repo), "--record", "--agents", "41"]) == 0
    refusal = cf_route_state.sast_state(exposure_repo)["last_refusal"]
    assert refusal is not None and refusal["origin"] == "check-run"
    assert "agent cap exceeded" in refusal["reason"]

    complete_out = plant(exposure_repo, [lead("lead-a")])
    mark = _record_cf(exposure_repo, complete_out)
    assert mark["complete"] is True
    assert mark["open"][0]["evidence_runs"] == [complete_out.name]
    st = cf_route_state.sast_state(exposure_repo)
    assert st["latest_complete"] is True
    assert st["last_refusal"] is None
    assert st["open_leads"] == 1 and st["open_confirmed"] == 0 and st["open_coverage"] == 0

    partial_out = plant(exposure_repo, [lead("lead-a")], run_status="partial")
    mark2 = _record_cf(exposure_repo, partial_out)
    assert mark2["complete"] is False
    assert mark2["open"][0]["evidence_runs"] == [partial_out.name, complete_out.name]
    assert cf_route_state.sast_state(exposure_repo)["latest_complete"] is False

    # Repair marker must not mask newest non-repair marker's complete flag.
    runlog.repair_log(exposure_repo)
    assert cf_route_state.sast_state(exposure_repo)["latest_complete"] is False


def test_deployment_checkpoint_uses_full_validation_plan(tmp_path, exposure_home, exposure_node):
    # 1. 6th lead (beyond the 5-lead detail cap) still sets has_deployment_check.
    r1 = make_repo(tmp_path / "r1", EXPOSED)
    health.collect(r1)
    leads = [_local_lead(f"l{i}") for i in range(5)] + [lead("l5"), _local_lead("l6")]
    _record_cf(r1, plant(r1, leads))
    st1 = cf_route_state.sast_state(r1)
    assert st1["open_leads"] == 7 and st1["has_deployment_check"] is True
    assert st1["missing_verified_ids"] == []

    # 2. Carried lead keeps deployment check when omitted by a later complete run.
    _record_cf(r1, plant(r1, []))
    assert cf_route_state.sast_state(r1)["has_deployment_check"] is True

    # 3. Incomplete run rejecting the lead keeps the deployment check.
    r2 = make_repo(tmp_path / "r2", EXPOSED)
    health.collect(r2)
    _record_cf(r2, plant(r2, [lead("lead-a")]))
    _record_cf(r2, plant(r2, [rejected("lead-a")], run_status="partial"))
    st2 = cf_route_state.sast_state(r2)
    assert st2["open_leads"] == 1 and st2["has_deployment_check"] is True

    # 4. Incomplete local-only re-report via missing artifact (cf_check._incomplete -> complete=False).
    r3 = make_repo(tmp_path / "r3", EXPOSED)
    health.collect(r3)
    out_a = plant(r3, [lead("lead-a")])
    _record_cf(r3, out_a)
    u = unit("api")
    u["local_checks"][0].update(method="local", artifact="agents/hunter-1/artifacts/missing.txt")
    out_b = plant(r3, [_local_lead("lead-a")], ledger=[u])  # run_status="complete" but missing artifact
    mark_b = _record_cf(r3, out_b)
    assert mark_b["complete"] is False
    assert mark_b["open"][0]["evidence_runs"] == [out_b.name, out_a.name]
    st3 = cf_route_state.sast_state(r3)
    assert st3["latest_complete"] is False and st3["has_deployment_check"] is True

    # 5. Local-only lead whose blocker mentions "deployment:" does NOT trigger has_deployment_check.
    r4 = make_repo(tmp_path / "r4", EXPOSED)
    health.collect(r4)
    _record_cf(r4, plant(r4, [_local_lead("lead-local", blockers=("deployment: verify locally",))]))
    st4 = cf_route_state.sast_state(r4)
    assert st4["open_leads"] == 1 and st4["has_deployment_check"] is False


def test_tampered_accepted_findings_triggers_missing_evidence_recovery(
        tmp_path, exposure_home, exposure_node):
    # Case 1: Two-run tampering retaining an older same-ID record.
    r1 = make_repo(tmp_path / "r1", EXPOSED)
    health.collect(r1)
    out1 = plant(r1, [lead("lead-a")])
    _record_cf(r1, out1)
    out2 = plant(r1, [_local_lead("lead-a")], run_status="partial")
    _record_cf(r1, out2)
    assert cf_route_state.sast_state(r1)["missing_verified_ids"] == []
    orig2 = (out2 / "findings.json").read_text()
    (out2 / "findings.json").write_text("[]\n")
    assert cf_route_state.sast_state(r1)["missing_verified_ids"] == ["lead-a"]
    (out2 / "findings.json").write_text(orig2)
    (out1 / "findings.json").write_text("[]\n")
    assert cf_route_state.sast_state(r1)["missing_verified_ids"] == ["lead-a"]

    # Case 2: Legacy marker + incomplete re-report preserves deployment check and fails closed
    # when the older run drops out of prior_runs.
    r2 = make_repo(tmp_path / "r2", EXPOSED)
    health.collect(r2)
    leg = plant(r2, [lead("lead-a")])
    _record_cf(r2, leg)
    _make_legacy_marker(leg)
    inc = plant(r2, [_local_lead("lead-a")], run_status="partial")
    assert _record_cf(r2, inc)["open"][0]["evidence_runs"] is None
    st2 = cf_route_state.sast_state(r2)
    assert st2["missing_verified_ids"] == [] and st2["has_deployment_check"] is True
    (leg / "findings.json").write_text("[]\n")
    assert leg not in runlog.prior_runs(r2)
    assert cf_route_state.sast_state(r2)["missing_verified_ids"] == ["lead-a"]

    # Case 3: Three-run A/B/C: legacy A (deployment) -> complete B (omits lead) -> incomplete C (local).
    r3 = make_repo(tmp_path / "r3", EXPOSED)
    health.collect(r3)
    run_a = plant(r3, [lead("lead-a")])
    _record_cf(r3, run_a)
    _make_legacy_marker(run_a)
    mark_b = _record_cf(r3, plant(r3, []))
    assert mark_b["complete"] is True and mark_b["open"][0]["evidence_runs"] is None
    run_c = plant(r3, [_local_lead("lead-a")], run_status="partial")
    mark_c = _record_cf(r3, run_c)
    assert mark_c["complete"] is False and mark_c["open"][0]["evidence_runs"] is None
    st3 = cf_route_state.sast_state(r3)
    assert st3["missing_verified_ids"] == [] and st3["has_deployment_check"] is True
    assert [r["run"] for r in cf_route_state.show_open_payload(r3)["leads"][0]["records"]] == [
        run_c.name, run_a.name]


def test_dirty_tree_does_not_mark_old_sentry_current(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "plain")
    health.collect(repo)
    df = tmp_path / "detail.txt"
    df.write_text("route: sentry | warn: weak hash in app.py:1\n")
    assert exposure.main(["record", str(repo), "warn", "--detail-file", str(df)]) == 0

    clean = cf_route_state.sast_state(repo)
    assert clean["sentry_positive"] is True
    assert clean["sentry_current"] is True
    assert clean["sentry_carried"] is False
    assert cf_route_state.show_open_payload(repo)["last_sentry"]["freshness"] == "current"

    # Dirtying the worktree invalidates currentness immediately.
    (repo / "app.py").write_text("print('dirty')\n")
    dirty_st = cf_route_state.sast_state(repo)
    assert dirty_st["sentry_positive"] is True
    assert dirty_st["sentry_current"] is False
    assert dirty_st["sentry_carried"] is True
    assert (cf_route_state.show_open_payload(repo)["last_sentry"]["freshness"]
            == "carried (prior or dirty tree)")

    # Recording while dirty stores tree == "" which must still NOT compare current.
    assert exposure.main(["record", str(repo), "warn", "--detail-file", str(df)]) == 0
    dirty_rec = cf_route_state.sast_state(repo)
    assert dirty_rec["sentry_current"] is False and dirty_rec["sentry_carried"] is True

    # Committing a new tree leaves the prior record carried, not current.
    git(repo, "commit", "-qam", "dirty change")
    new_tree = cf_route_state.sast_state(repo)
    assert new_tree["sentry_current"] is False and new_tree["sentry_carried"] is True


def test_blank_sentry_warn_with_open_coverage_does_not_stamp_positive_sentry(
        tmp_path, exposure_home, exposure_node):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    health.collect(repo)
    _record_cf(repo, plant(repo, [], ledger=[unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)]))

    # Trigger an exposure error refusal, then opt out cleanly to sentry.
    (repo / ".claude" / "exposure.json").write_text('{"exposed": "bad"}')
    exposure.record_route(repo)
    assert cf_route_state.sast_state(repo)["last_refusal"]["origin"] == "record-route"

    (repo / ".claude" / "exposure.json").write_text(json.dumps(
        {"exposed": False, "reason": "internal only now"}))
    git(repo, "commit", "-qam", "opt out")

    blank = tmp_path / "blank.txt"
    blank.write_text("   \n\t ")
    assert exposure.main(["record", str(repo), "warn", "--detail-file", str(blank)]) == 0
    assert _card_sast(repo)["status"] == "pending"
    st = cf_route_state.sast_state(repo)
    assert st["open_coverage"] == 1
    assert st["sentry_positive"] is False and st["sentry_carried"] is False
    assert st["last_sentry"]["status"] == "pending"
    assert st["last_refusal"] is not None  # pending record does not clear prior refusal

    # Valid non-pending Sentry pass clears refusal and keeps pre-open_floor detail in last-sentry.
    clean_df = tmp_path / "clean.txt"
    clean_df.write_text("route: sentry | 0 confirmed\n")
    assert exposure.main(["record", str(repo), "pass", "--detail-file", str(clean_df)]) == 0
    st2 = cf_route_state.sast_state(repo)
    assert st2["last_refusal"] is None
    assert st2["sentry_positive"] is False and st2["last_sentry"]["status"] == "pass"
    assert st2["last_sentry"]["detail"] == "route: sentry | 0 confirmed"


def test_repeated_record_route_preserves_sentry_finding_false(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "svc", EXPOSED)
    (repo / ".claude" / "exposure.json").write_text(json.dumps(
        {"exposed": False, "reason": "moved internal"}))
    git(repo, "commit", "-qam", "opt out")
    health.collect(repo)
    df = tmp_path / "clean.txt"
    df.write_text("route: sentry | 0 confirmed\n")
    assert exposure.main(["record", str(repo), "pass", "--detail-file", str(df)]) == 0
    assert cf_route_state.sast_state(repo)["sentry_positive"] is False

    # Drop exposure.json so route becomes sentry-downgrade; repeated record_route must not flip it.
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qam", "drop exposure")
    for _ in range(3):
        exposure.record_route(repo)
    assert _card_sast(repo)["status"] == "warn"
    st = cf_route_state.sast_state(repo)
    assert st["route_kind"] == "sentry-downgrade"
    assert st["sentry_positive"] is False and st["sentry_carried"] is False
    assert st["last_sentry"]["sentry_finding"] is False


def test_cf_route_state_is_strictly_read_only_without_node_or_vendor(
        tmp_path, exposure_home, monkeypatch, capsys):
    plain = make_repo(tmp_path / "plain")
    exposed = make_repo(tmp_path / "exposed", EXPOSED)

    # Remove node from PATH and point VENDOR at a nonexistent directory.
    bin_only_git = tmp_path / "bin"
    bin_only_git.mkdir()
    git_bin = Path("/usr/bin/git")
    if not git_bin.exists():
        import shutil
        git_bin = Path(shutil.which("git") or "git")
    (bin_only_git / "git").symlink_to(git_bin)
    monkeypatch.setenv("PATH", str(bin_only_git))
    monkeypatch.setattr(exposure, "VENDOR", tmp_path / "no-such-vendor")
    assert os.environ["PATH"] == str(bin_only_git)

    for repo, want_kind in ((plain, "sentry"), (exposed, "cloudflare-quick")):
        st = cf_route_state.sast_state(repo)
        assert st["route_kind"] == want_kind
        assert st["repo_root"] == str(repo.resolve())
        assert st["latest_complete"] is None
        assert st["open_set_error"] is None
        assert st["missing_verified_ids"] == []
        assert st["has_deployment_check"] is False
        assert exposure.main(["show-open", str(repo)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["route_kind"] == want_kind
        assert "unhashed supplementary prose" in payload["note"]
        assert not (repo / ".claude" / "health").exists()
    assert not (exposure_home / ".gearbox-state").exists()
