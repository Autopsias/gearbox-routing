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


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_dirty_fixture_fails_every_blocking_class(Path(td))
        test_clean_fixture_passes_and_records_result(Path(td))
    test_score_formula_caps_and_monotonicity()
    print("ok")
