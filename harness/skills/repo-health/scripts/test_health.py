"""Known-positive + known-negative test for the repo-health collector.

The dirty fixture plants one instance of each blocking defect class; the test
fails if any check stops detecting its class (a check that cannot fail is
worse than no check).
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health  # noqa: E402
import health_render as render  # noqa: E402
import secrets_scan  # noqa: E402

BAD_WORKFLOW = """\
name: ci
on: pull_request_target
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}
      - uses: someone/random-action@v1
      - run: echo hi
"""


def make_repo(tmp_path, dirty):
    repo = tmp_path / ("dirty" if dirty else "clean")
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    if dirty:
        wf = repo / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(BAD_WORKFLOW)
        (repo / ".env").write_text("API_KEY=hunter2\n")
        (repo / "nb.ipynb").write_text(json.dumps(
            {"cells": [{"cell_type": "code", "outputs": [{"text": "leak"}]}]}))
    else:
        (repo / "AGENTS.md").write_text("# Repo\nRun `pytest` to test.\n")
        (repo / "README.md").write_text("# Clean\n" + "line\n" * 20 + "```\npytest\n```\n")
    (repo / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def statuses(repo):
    card = json.loads((repo / ".claude" / "health" / "scorecard.json").read_text())
    return {c["id"]: c["status"] for c in card["checks"]}


def test_dirty_fixture_fails_every_blocking_class(tmp_path):
    repo = make_repo(tmp_path, dirty=True)
    health.collect(repo)
    st = statuses(repo)
    assert st["sec.workflow-permissions"] == "fail"
    assert st["sec.action-pinning"] == "fail"        # someone/random-action@v1
    assert st["sec.dangerous-workflow"] == "fail"    # pull_request_target + head checkout
    assert st["sec.tracked-sensitive"] == "fail"     # tracked .env
    assert st["ai.agents-md"] == "fail"
    assert st["ci.timeouts"] == "warn"
    assert st["ci.pre-commit"] == "warn"        # no local hook gate
    assert st["hyg.notebook-outputs"] == "warn"  # committed outputs
    # a branch with a commit main does not have = forgotten work
    def _git(*args):
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                        "-c", "user.name=t", *args], check=True, capture_output=True)
    assert health.hyg_unmerged_work(repo)["status"] == "pass"
    _git("checkout", "-qb", "stranded")
    _git("commit", "-qm", "wip", "--allow-empty")
    _git("checkout", "-q", "-")
    assert health.hyg_unmerged_work(repo)["status"] == "warn"
    render.render(repo)
    page = (repo / ".claude" / "health" / "HEALTH.html").read_text()
    assert "AT RISK" in page and "sec.tracked-sensitive" in page


def test_clean_fixture_passes_and_records_result(tmp_path):
    repo = make_repo(tmp_path, dirty=False)
    health.collect(repo)
    st = statuses(repo)
    assert st["sec.tracked-sensitive"] == "pass"
    assert st["sec.workflow-permissions"] == "na"    # no workflows
    assert st["ai.agents-md"] == "pass"
    assert st["sec.secrets-history"] == "pending"
    health.set_result(repo, "sec.secrets-history", "pass", "gitleaks: no leaks")
    assert statuses(repo)["sec.secrets-history"] == "pass"
    render.render(repo)
    page = (repo / ".claude" / "health" / "HEALTH.html").read_text()
    assert "AT RISK" not in page
    hist = (repo / ".claude" / "health" / "history.jsonl").read_text().splitlines()
    assert len(hist) == 1 and json.loads(hist[0])["bf"] == 0


def test_set_keeps_an_exposure_error_detail_through_render(tmp_path):
    # SKILL.md --deep records a broken .claude/exposure.json as pending with
    # this detail; it must reach the page intact, not as a blank pending row.
    import html
    repo = make_repo(tmp_path, dirty=False)
    health.collect(repo)
    detail = ("exposure error: /r/.claude/exposure.json: "
              '"exposed": true needs a non-empty "reason"')
    health.main(["set", "sec.sast", "pending", detail, "--repo", str(repo)])
    card = json.loads((repo / ".claude" / "health" / "scorecard.json").read_text())
    sast = next(c for c in card["checks"] if c["id"] == "sec.sast")
    assert (sast["status"], sast["detail"]) == ("pending", detail)
    render.render(repo)
    page = (repo / ".claude" / "health" / "HEALTH.html").read_text()
    assert html.escape(detail.split(": ", 1)[1]) in page


def _c(tier, status):
    return {"tier": tier, "status": status}


def test_score_formula_caps_and_monotonicity():
    # all pass -> 100; na/pending excluded from the average
    assert render.overall_score([_c("blocking", "pass"), _c("advisory", "na")])[0] == 100
    # blocking fail caps at 59 even when everything else passes
    checks = [_c("blocking", "fail")] + [_c("advisory", "pass")] * 20
    n, note = render.overall_score(checks)
    assert n == 59 and "blocking" in note
    # blocking pending caps at 89 (no green until verified)
    n, _ = render.overall_score([_c("blocking", "pending"), _c("advisory", "pass")])
    assert n == 89
    # weights: blocking warn (2 * 0.5) + advisory pass (1 * 1) = 2/3 -> 67, capped 67
    assert render.overall_score([_c("blocking", "warn"), _c("advisory", "pass")])[0] == 67
    # monotonic: fixing any one check never lowers the score
    base = [_c("blocking", "fail"), _c("advisory", "warn"), _c("advisory", "fail")]
    n0 = render.overall_score(base)[0]
    for i, better in [(0, "pass"), (1, "pass"), (2, "warn")]:
        fixed = [dict(c) for c in base]
        fixed[i]["status"] = better
        assert render.overall_score(fixed)[0] >= n0
    assert render.band(49) == "red" and render.band(50) == "amber" and render.band(90) == "green"


# --- sec.secrets-history: ranged, capped, and the run still writes its history line ---

def _scanner(tmp_path, body):
    f = tmp_path / "scanner.sh"
    f.write_text("#!/bin/sh\necho \"$@\" > \"$0.argv\"\n" + body)
    return f


def _hist(repo):
    f = repo / ".claude" / "health" / "history.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines()]


def _head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def _scan_and_render(repo, monkeypatch, scanner, cap="60"):
    monkeypatch.setenv("REPO_HEALTH_GITLEAKS", f"sh {scanner}")
    monkeypatch.setenv("REPO_HEALTH_SECRETS_CAP_S", cap)
    health.collect(repo)
    health.main(["scan-secrets", "--repo", str(repo)])
    render.render(repo)


def test_pending_scan_still_writes_history_and_keeps_the_old_marker(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, dirty=False)
    head = _head(repo)
    hf = repo / ".claude" / "health"
    hf.mkdir(parents=True)
    (hf / "history.jsonl").write_text(json.dumps(
        {"ts": "2026-01-01", "commit": "old", "secrets_scanned": head}) + "\n")
    _scan_and_render(repo, monkeypatch, _scanner(tmp_path, "sleep 30\n"), cap="1")
    assert statuses(repo)["sec.secrets-history"] == "pending"
    card = json.loads((hf / "scorecard.json").read_text())
    assert "passed its 1s cap" in next(
        c for c in card["checks"] if c["id"] == "sec.secrets-history")["detail"]
    hist = _hist(repo)
    assert len(hist) == 2 and hist[-1]["commit"] != "old"
    assert hist[-1]["secrets_scanned"] == head          # marker unchanged, still carried


def test_recorded_marker_gives_a_ranged_scan_and_a_completed_scan_moves_it(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, dirty=False)
    first = _head(repo)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "two", "--allow-empty"], check=True)
    hf = repo / ".claude" / "health"
    hf.mkdir(parents=True)
    (hf / "history.jsonl").write_text(json.dumps(
        {"ts": "2026-01-01", "commit": "old", "secrets_scanned": first}) + "\n")
    scanner = _scanner(tmp_path, "exit 0\n")
    _scan_and_render(repo, monkeypatch, scanner)
    assert f"--log-opts {first}..{_head(repo)}" in (tmp_path / "scanner.sh.argv").read_text()
    assert statuses(repo)["sec.secrets-history"] == "pass"
    assert _hist(repo)[-1]["secrets_scanned"] == _head(repo) != first
    # the clone's own record, not a committed history line, sets where the next scan starts
    two = _head(repo)
    assert secrets_scan.local_marker_file(repo).read_text().strip() == two
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "three", "--allow-empty"], check=True)
    with (hf / "history.jsonl").open("a") as f:      # a forged line that names the newest commit
        f.write(json.dumps({"ts": "2026-02-01", "commit": "x", "secrets_scanned": _head(repo)}) + "\n")
    _scan_and_render(repo, monkeypatch, scanner)
    assert f"--log-opts {two}..{_head(repo)}" in (tmp_path / "scanner.sh.argv").read_text()


def test_marker_that_is_not_an_ancestor_means_a_full_scan(tmp_path, monkeypatch):
    repo = make_repo(tmp_path, dirty=False)
    hf = repo / ".claude" / "health"
    hf.mkdir(parents=True)
    (hf / "history.jsonl").write_text(json.dumps(
        {"ts": "2026-01-01", "commit": "old", "secrets_scanned": "0" * 40}) + "\n")
    assert secrets_scan.last_marker(repo) == "0" * 40
    with (hf / "history.jsonl").open("a") as f:      # a ref is not a marker: "HEAD..HEAD" scans nothing
        f.write(json.dumps({"ts": "2026-01-02", "commit": "x", "secrets_scanned": "HEAD"}) + "\n")
    assert secrets_scan.last_marker(repo) == "0" * 40
    _scan_and_render(repo, monkeypatch, _scanner(tmp_path, "exit 1\n"))
    assert "--log-opts" not in (tmp_path / "scanner.sh.argv").read_text()
    assert statuses(repo)["sec.secrets-history"] == "fail"      # exit 1 = leaks
    assert _hist(repo)[-1]["secrets_scanned"] == "0" * 40       # a leak never moves the marker


def test_scan_secrets_prints_one_json_line_and_a_non_git_target_is_pending(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, dirty=False)
    scanner = _scanner(tmp_path, "exit 0\n")         # no backslash inside the f-string (3.11)
    monkeypatch.setenv("REPO_HEALTH_GITLEAKS", f"sh {scanner}")
    health.collect(repo)
    capsys.readouterr()
    health.main(["scan-secrets", "--repo", str(repo)])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1 and json.loads(out[0])["status"] == "pass"
    plain = tmp_path / "not-git"
    plain.mkdir()
    status, _, scanned = secrets_scan.scan(plain)
    assert status == "pending" and scanned == ""


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_dirty_fixture_fails_every_blocking_class(Path(td))
        test_clean_fixture_passes_and_records_result(Path(td))
    test_score_formula_caps_and_monotonicity()
    print("ok")


def _verify_row(repo, makefile):
    (repo / "Makefile").write_text(makefile)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    health.collect(repo, fresh=True)
    card = json.loads((repo / ".claude" / "health" / "scorecard.json").read_text())
    return next(c for c in card["checks"] if c["id"] == "ai.verify-command")


def test_verify_command_is_a_pending_collect_probe_never_a_pass(tmp_path):
    """A `test:` target that cannot start used to read pass."""
    repo = make_repo(tmp_path, dirty=False)
    row = _verify_row(repo, "test:\n\t@python3 -m pytest -q\nlint:\n\ttrue\n")
    assert row["status"] == "pending" and "run: python3 -m pytest -q --collect-only -q" in row["detail"]
    row = _verify_row(repo, "test:\n\t@echo ok\n")
    assert row["status"] == "pending" and "no collect form" in row["detail"]
    health.set_result(repo, "ai.verify-command", "fail", "No module named pytest")
    assert statuses(repo)["ai.verify-command"] == "fail"
