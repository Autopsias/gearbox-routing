"""ISO-02 §8.b — a `_plans/` path merged in from the default branch is not the plan's.

Split out of ``test_plan_ship.py`` under this repo's 800-LOC test bound. On
2026-10-02 `ship-run --step commit` refused a plan whose branch had merged
origin/main: a harvest commit on main had changed a `_plans/` file, and the
`base..HEAD` tree diff counted it as the plan's own. Real git, real worktree.

    pytest plan-execute/scripts/test_plan_ship_merged_main.py -q
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import plan_ship as pship  # noqa: E402
import plan_worktree as pwt  # noqa: E402
from worktree import WorktreeError, git  # noqa: E402
from test_plan_ship import SLUG, _repo, _touch  # noqa: E402


@pytest.fixture
def iso(tmp_path):
    """The same isolated plan `test_plan_ship.iso` builds."""
    r = _repo(tmp_path)
    r["state"] = pwt.ensure_plan_worktree(r["plan_dir"], project_root=r["root"])
    r["tree"] = Path(r["state"]["path"])
    return r


def _main_edits(iso, rel, text):
    """Main moves on with a `_plans/` edit (a harvest commit) and the plan branch
    merges it — the 2026-10-02 false refusal."""
    root = iso["root"]
    _touch(root, rel, text)
    git(["add", "-f", "--", rel], root, check=True)
    git(["commit", "-q", "-m", "harvest"], root, check=True)
    git(["push", "-q", "origin", "main"], root, check=True)


def _merge_main(tree, *extra):
    git(["merge", "-q", "--no-edit", *extra, "origin/main"], tree, check=True)


def _plan_commits(tree, rel, text):
    _touch(tree, rel, text)
    git(["add", "-f", "--", rel], tree, check=True)
    git(["commit", "-q", "-m", "chore: sweep"], tree, check=True)


def test_a_plans_path_merged_in_from_main_is_not_the_plans_own(iso):
    tree, base = iso["tree"], iso["state"]["base_ref"]
    _main_edits(iso, "_plans/other-plan/_evidence/t.txt", "from main\n")
    _merge_main(tree)
    # Control: the plain tree diff from the base still sees it.
    assert pship.plans_paths(tree, base) == ["_plans/other-plan/_evidence/t.txt"]
    assert pship.plans_paths(tree, base, default="main") == []
    _touch(tree, "src/b.py", "B = 1\n")
    assert pship.commit(tree, "feat(demo): work", iso["plan_dir"])["status"] == "committed"


def test_a_plans_path_the_plan_commits_itself_is_still_refused(iso):
    tree = iso["tree"]
    _plan_commits(tree, f"_plans/{SLUG}/PLAN.html", "<html>swept</html>\n")
    _touch(tree, "src/b.py", "B = 1\n")
    with pytest.raises(WorktreeError, match=f"_plans/{SLUG}/PLAN.html"):
        pship.commit(tree, "feat(demo): work", iso["plan_dir"])


def test_a_merged_path_and_the_plans_own_path_together_are_refused(iso):
    tree, base = iso["tree"], iso["state"]["base_ref"]
    _plan_commits(tree, f"_plans/{SLUG}/PLAN.html", "<html>swept</html>\n")
    _main_edits(iso, "_plans/other-plan/_evidence/t.txt", "from main\n")
    _merge_main(tree)
    assert pship.plans_paths(tree, base, default="main") == [f"_plans/{SLUG}/PLAN.html"]
    _touch(tree, "src/b.py", "B = 1\n")
    with pytest.raises(WorktreeError, match=f"_plans/{SLUG}/PLAN.html"):
        pship.commit(tree, "feat(demo): work", iso["plan_dir"])


def test_a_plans_path_both_sides_edited_is_still_the_plans_own(iso):
    """Main ALSO edited the file the plan swept: the merge keeps the plan's copy,
    which differs from main's — still refused."""
    tree, base = iso["tree"], iso["state"]["base_ref"]
    _plan_commits(tree, f"_plans/{SLUG}/PLAN.html", "<html>plan</html>\n")
    _main_edits(iso, f"_plans/{SLUG}/PLAN.html", "<html>main</html>\n")
    _merge_main(tree, "-X", "ours")
    assert pship.plans_paths(tree, base, default="main") == [f"_plans/{SLUG}/PLAN.html"]


def test_a_missing_default_branch_raises_instead_of_reading_as_clean(iso):
    tree = iso["tree"]
    _plan_commits(tree, f"_plans/{SLUG}/PLAN.html", "<html>swept</html>\n")
    with pytest.raises(WorktreeError, match="merge-base"):
        pship.plans_paths(tree, iso["state"]["base_ref"], default="no-such-branch")
