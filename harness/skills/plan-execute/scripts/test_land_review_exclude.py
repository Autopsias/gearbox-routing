"""The land re-gate's `.review-exclude` list (contract §5.1b): read from the BASE.

    pytest plan-execute/scripts/test_land_review_exclude.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from _land_fixture import build_repo, isolate, main_side, run_cli, work  # noqa: E402
from worktree import git  # noqa: E402


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def test_the_regate_also_excludes_the_bases_review_exclude_list(fx):
    """`.review-exclude` drops data paths from the land review, read from the BASE:
    the plan's own edit to the list (here: dropping its code) must not count."""
    (fx["root"] / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "cwd": ".", "timeout": 120,
                         "env_allowlist": ["PLAN_EXECUTE_REVIEW_EXCLUDE"],
                         "argv": ["/bin/sh", "-c",
                                  "printf '%s' \"$PLAN_EXECUTE_REVIEW_EXCLUDE\" > /tmp/land-exclude.txt"]}}))
    main_side(fx, ".review-exclude", "# stored samples\ndata/samples/\n")
    iso = isolate(fx, "plan-a")
    git(["pull", "-q", "--no-rebase", "--no-edit", "origin", "main"], iso["tree"], check=True)
    work(iso["tree"], ".review-exclude", "data/samples/\nsrc/\n", msg="plan widens the list")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out.get("kind") != "sync-conflict", out
    ex = Path("/tmp/land-exclude.txt").read_text().split(",")
    assert ex == ["_plans/plan-a", "data/samples/"], ex
