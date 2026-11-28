"""Unit tests for the 7-row sec.sast Cloudflare/Sentry decision table.

Zero vendor dependency: uses exposure_node stub and direct exposure/health helpers.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import cf_route_state  # noqa: E402
import exposure  # noqa: E402
import health  # noqa: E402
import routes  # noqa: E402
from exposure_testlib import (  # noqa: E402
    ACK, CONF, DEFERRED_ADMIN, EXPOSED, git, lead, make_repo, plant, unit)


def _record_cf(repo, out, agents=25):
    rc = exposure.main(["check-run", str(out), str(repo), "--record", "--agents", str(agents)])
    assert rc == 0


def _sast_row(repo):
    card = health.load_card(repo)
    st = cf_route_state.sast_state(repo)
    rows = {r["id"]: r for r in routes.fix_rows(card, advisory=True, sast_state=st)}
    return rows.get("sec.sast"), st


def _make_non_py_repo(path, exposure_doc=None):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "main.go").write_text("package main\nfunc main() {}\n")
    (path / "src").mkdir()
    (path / "src" / "api.py").write_text("")  # removed before commit if non-py
    (path / "src" / "api.py").unlink()
    (path / "src" / "api.go").write_text("package src\n")
    if exposure_doc is not None:
        (path / ".claude").mkdir()
        (path / ".claude" / "exposure.json").write_text(json.dumps(exposure_doc))
    git(path, "add", "-A")
    git(path, "commit", "-qm", "init go repo")
    return path


def _accept_direct(repo, findings=(), ledger=None, complete=True, status="fail"):
    out = plant(repo, findings, ledger=ledger,
                run_status="complete" if complete else "partial")
    open_items = []
    for f in findings:
        kind = "confirmed" if f["verdict"] == "confirmed" else "lead"
        open_items.append({
            "kind": kind, "id": f["fingerprint"], "label": f["title"],
            "ref": "src/api.py:1", "files": ["src/api.py"], "evidence_runs": [out.name],
        })
    for u in (ledger or ()):
        if u["status"] != "covered":
            open_items.append({
                "kind": "unit", "id": u["coverage_id"],
                "label": f"{u['surface']} ({u['status']})", "ref": u["surface"],
                "files": u.get("starting_paths", []), "evidence_runs": [out.name],
            })
    exposure.accept(out, status, open_items, exposure._digest(out), complete=complete)
    return out


def test_incomplete_run_retains_confirmed_remediation(exposure_repo, exposure_node):
    health.collect(exposure_repo)
    _record_cf(exposure_repo, plant(exposure_repo, [CONF], run_status="partial"))
    row, st = _sast_row(exposure_repo)
    assert st["latest_complete"] is False and st["open_confirmed"] == 1
    assert row is not None
    assert row["agent"] == "general-purpose"
    assert row["omit_detail"] is True
    assert "prove-then-fix" in row["prompt"]
    assert "exposure.py show-open" in row["prompt"]


def test_pending_sast_distinguishes_refused_audit_from_unrun_probe(
        tmp_path, exposure_home, exposure_node):
    # 1. Plain unrun repo -> PROBE
    plain = make_repo(tmp_path / "plain")
    health.collect(plain)
    row_plain, st_plain = _sast_row(plain)
    assert st_plain["last_refusal"] is None
    assert row_plain["dispatch"] == "probe"

    # 2. Refused check-run -> recovery route, NOT PROBE, even after opt-out + collect
    exp = make_repo(tmp_path / "refused", EXPOSED)
    health.collect(exp)
    bad = plant(exp)
    assert exposure.main(["check-run", str(bad), str(exp), "--record", "--agents", "41"]) == 0
    (exp / ".claude" / "exposure.json").write_text(json.dumps(ACK))
    git(exp, "commit", "-qam", "opt out")
    health.collect(exp, fresh=True)
    row_ref, st_ref = _sast_row(exp)
    assert st_ref["last_refusal"] is not None
    assert row_ref["dispatch"] == "agent"
    assert row_ref["agent"] == "general-purpose"
    assert row_ref["omit_detail"] is True
    assert row_ref["operator"]
    assert "recovery" in row_ref["prompt"].lower()


def test_carried_coverage_only_does_not_dispatch_remediation(
        tmp_path, exposure_home, exposure_node):
    repo = make_repo(tmp_path / "cov", EXPOSED)
    health.collect(repo)
    _record_cf(repo, plant(repo, [], ledger=[unit("api"), unit("admin", "deferred", **DEFERRED_ADMIN)]))
    (repo / ".claude" / "exposure.json").write_text(json.dumps(ACK))
    git(repo, "commit", "-qam", "opt out")

    # 1. Fresh pending + opt-out with carried coverage -> incomplete/coverage-only, not prove-then-fix
    health.collect(repo, fresh=True)
    row1, st1 = _sast_row(repo)
    assert st1["open_coverage"] == 1 and st1["open_confirmed"] == 0
    assert row1["agent"] == "general-purpose"
    assert "Do NOT edit" in row1["prompt"]
    assert "prove-then-fix" not in row1["prompt"]
    assert row1["operator"]

    # 2. Clean Sentry pass raised to warn by open_floor -> still incomplete/coverage-only
    clean_df = tmp_path / "clean.txt"
    clean_df.write_text("route: sentry | 0 confirmed\n")
    assert exposure.main(["record", str(repo), "pass", "--detail-file", str(clean_df)]) == 0
    row2, st2 = _sast_row(repo)
    assert st2["sentry_positive"] is False
    assert "Do NOT edit" in row2["prompt"]
    assert "prove-then-fix" not in row2["prompt"]

    # 3. Blank Sentry warn (recorded as pending, sentry_positive=False) -> still incomplete/coverage-only
    blank_df = tmp_path / "blank.txt"
    blank_df.write_text("   \n")
    assert exposure.main(["record", str(repo), "warn", "--detail-file", str(blank_df)]) == 0
    row3, st3 = _sast_row(repo)
    assert st3["sentry_positive"] is False
    assert "Do NOT edit" in row3["prompt"]
    assert "prove-then-fix" not in row3["prompt"]


def test_fresh_collect_after_tree_change_retains_open_confirmed_remediation(
        exposure_repo, exposure_node):
    health.collect(exposure_repo)
    _record_cf(exposure_repo, plant(exposure_repo, [CONF]))
    (exposure_repo / "app.py").write_text("print('changed')\n")
    git(exposure_repo, "commit", "-qam", "tree change")
    health.collect(exposure_repo, fresh=True)
    row, st = _sast_row(exposure_repo)
    assert row is not None and row["status"] == "pending"
    assert st["open_confirmed"] == 1
    assert row["agent"] == "general-purpose"
    assert "prove-then-fix" in row["prompt"]
    assert "pending" in row["operator"]


def test_na_sast_card_still_routes_open_cloudflare_items(tmp_path, exposure_home):
    repo = _make_non_py_repo(tmp_path / "gorepo", EXPOSED)
    health.collect(repo)
    sast_card = next(c for c in health.load_card(repo)["checks"] if c["id"] == "sec.sast")
    assert sast_card["status"] == "na"
    _accept_direct(repo, findings=[CONF], complete=True, status="fail")
    row, st = _sast_row(repo)
    assert st["open_confirmed"] == 1
    assert row is not None and row["status"] == "na"
    assert row["agent"] == "general-purpose"
    assert row["omit_detail"] is True
    assert "prove-then-fix" in row["prompt"]


def test_na_sast_card_routes_incomplete_run_and_downgrade_or_error(tmp_path, exposure_home):
    # 1. Incomplete zero-open run on na repo
    r_inc = _make_non_py_repo(tmp_path / "go_inc", EXPOSED)
    health.collect(r_inc)
    _accept_direct(r_inc, findings=[], ledger=[unit("api")], complete=False, status="warn")
    row_inc, st_inc = _sast_row(r_inc)
    assert st_inc["latest_complete"] is False
    assert row_inc is not None and row_inc["status"] == "na"
    assert "Do NOT edit" in row_inc["prompt"] and row_inc["operator"]

    # 2. Downgrade on na repo
    r_down = _make_non_py_repo(tmp_path / "go_down", EXPOSED)
    git(r_down, "rm", "-q", ".claude/exposure.json")
    git(r_down, "commit", "-qam", "drop exposure")
    health.collect(r_down)
    row_down, st_down = _sast_row(r_down)
    assert st_down["route_kind"] == "sentry-downgrade"
    assert row_down is not None and row_down["status"] == "na"
    assert "downgrade" in row_down["operator"].lower()

    # 3. Exposure error on na repo
    r_err = _make_non_py_repo(tmp_path / "go_err", EXPOSED)
    (r_err / ".claude" / "exposure.json").write_text('{"exposed": "bad"}')
    health.collect(r_err)
    row_err, st_err = _sast_row(r_err)
    assert st_err["route_kind"] == "error"
    assert row_err is not None and row_err["status"] == "na"
    assert "recovery" in row_err["prompt"].lower() and row_err["operator"]

    # 4. Never-exposed na repo stays CLEAR (dropped)
    r_clean = _make_non_py_repo(tmp_path / "go_clean")
    health.collect(r_clean)
    row_clean, st_clean = _sast_row(r_clean)
    assert st_clean["route_kind"] == "sentry"
    assert row_clean is None


def test_downgrade_only_warn_routes_to_operator_not_semgrep(tmp_path, exposure_home):
    repo = make_repo(tmp_path / "down", EXPOSED)
    (repo / ".claude" / "exposure.json").write_text(json.dumps(ACK))
    git(repo, "commit", "-qam", "opt out")
    health.collect(repo)
    df = tmp_path / "clean.txt"
    df.write_text("route: sentry | 0 confirmed\n")
    assert exposure.main(["record", str(repo), "pass", "--detail-file", str(df)]) == 0
    git(repo, "rm", "-q", ".claude/exposure.json")
    git(repo, "commit", "-qam", "drop exposure")
    exposure.record_route(repo)
    row, st = _sast_row(repo)
    assert st["route_kind"] == "sentry-downgrade" and st["sentry_positive"] is False
    assert row is not None and row["status"] == "warn"
    assert row["agent"] == "general-purpose"
    assert "do NOT run Semgrep or Bandit" in row["prompt"]
    assert "downgrade" in row["operator"].lower()


def test_exposure_error_and_downgrade_are_not_treated_as_opted_out(
        tmp_path, exposure_home, exposure_node):
    # Open confirmed finding + downgrade -> closure says downgrade + operator non-empty
    r1 = make_repo(tmp_path / "err_down", EXPOSED)
    health.collect(r1)
    _record_cf(r1, plant(r1, [CONF]))
    git(r1, "rm", "-q", ".claude/exposure.json")
    git(r1, "commit", "-qam", "drop exposure")
    row1, st1 = _sast_row(r1)
    assert st1["route_kind"] == "sentry-downgrade"
    assert "Closure (downgrade)" in row1["prompt"]
    assert "Closure (opted-out)" not in row1["prompt"]
    assert "downgrade" in row1["operator"].lower()

    # Open confirmed finding + exposure error -> closure says error + operator non-empty
    (r1 / ".claude").mkdir(exist_ok=True)
    (r1 / ".claude" / "exposure.json").write_text('{"exposed": "nope"}')
    row2, st2 = _sast_row(r1)
    assert st2["route_kind"] == "error"
    assert "Closure (error)" in row2["prompt"]
    assert "Closure (opted-out)" not in row2["prompt"]
    assert "error" in row2["operator"].lower()


def test_mixed_sentry_and_cloudflare_keeps_both_work_items(
        tmp_path, exposure_home, exposure_node):
    repo = make_repo(tmp_path / "mixed", EXPOSED)
    health.collect(repo)
    _record_cf(repo, plant(repo, [lead("lead-a")]))
    (repo / ".claude" / "exposure.json").write_text(json.dumps(ACK))
    git(repo, "commit", "-qam", "opt out")
    df = tmp_path / "sentry.txt"
    df.write_text("route: sentry | warn: weak hash in app.py:1\n")
    assert exposure.main(["record", str(repo), "warn", "--detail-file", str(df)]) == 0
    row, st = _sast_row(repo)
    assert st["open_leads"] == 1 and st["sentry_positive"] is True
    assert "Section 1" in row["prompt"] and "Section 2" in row["prompt"]
    assert "last_sentry" in row["prompt"]
    assert "deployment-only" in row["operator"]


def test_route_for_sec_sast_backwards_compat():
    r = routes.route_for("sec.sast")
    assert r["agent"] == "security-scanner"
    assert "semgrep/bandit" in r["prompt"]
    assert not r.get("omit_detail")
