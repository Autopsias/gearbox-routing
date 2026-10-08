"""ISO-02 §8.b — staging a plan worktree whose `_plans/` is GIT-IGNORED.

Split out of ``test_plan_ship.py`` 2026-08-26 under this repo's 800-LOC test
bound. Same contract, same real-git discipline: every check here runs against a
REAL repository with a REAL linked worktree, because the failure it exists to
prevent is a git behaviour — `git add` exiting 1 over an ignored path named by a
NEGATIVE `:(exclude)` pathspec, while staging every file correctly.

    pytest plan-execute/scripts/test_plan_ship_staging.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import plan_ship as pship  # noqa: E402
import plan_worktree as pwt  # noqa: E402
from worktree import WorktreeError, git  # noqa: E402
from test_plan_ship import SLUG, _init, _touch  # noqa: E402


def _repo_plans_ignored(tmp_path, *, force_add_record=False):
    """A repo whose `.gitignore` covers `_plans/` — the shape that broke.

    `_repo` TRACKS the plan directory, so `:(exclude)_plans` never names an
    IGNORED path there and the old code passed. Every repo that git-ignores its
    plan directory hit the bug on every single session commit.

    `force_add_record` tracks one file under `_plans/` AT THE BASE, which is the
    tracked-but-ignored case: `git check-ignore _plans` then reports NOT
    ignored. It is force-added before the base commit on purpose — committing a
    `_plans/` path inside the worktree's own `base..HEAD` range is what §8.b
    refuses, so a test that arranged it there would trip that guard instead of
    the behaviour it means to check.
    """
    origin = tmp_path / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / ".gitignore").write_text("__pycache__/\n_plans/\n")
    plan_dir = root / "_plans" / SLUG
    plan_dir.mkdir(parents=True)
    (plan_dir / "PLAN.html").write_text("<html>base</html>\n")
    (plan_dir / "manifest.json").write_text(json.dumps({"plan_schema_version": 7,
                                                        "sessions": []}))
    _init(root)
    git(["add", "-A"], root, check=True)
    if force_add_record:
        (plan_dir / "record.json").write_text('{"v": 1}\n')
        git(["add", "-f", "--", f"_plans/{SLUG}/record.json"], root, check=True)
    git(["commit", "-q", "-m", "base"], root, check=True)
    git(["remote", "add", "origin", str(origin)], root, check=True)
    git(["push", "-q", "-u", "origin", "main"], root, check=True)
    tracked = bool(git(["ls-files", "--", "_plans"], root, check=True)[1])
    ignored = git(["check-ignore", "-q", "--", "_plans"], root)[0] == 0
    # The two cases are genuinely different, and each must be the one it claims.
    assert tracked is force_add_record
    assert ignored is not force_add_record
    state = pwt.ensure_plan_worktree(plan_dir, project_root=root)
    return {"root": root, "origin": origin, "plan_dir": plan_dir,
            "state": state, "tree": Path(state["path"])}


@pytest.fixture
def iso_plans_ignored(tmp_path):
    return _repo_plans_ignored(tmp_path)


@pytest.fixture
def iso_plans_force_added(tmp_path):
    return _repo_plans_ignored(tmp_path, force_add_record=True)


def test_a_gitignored_plans_dir_does_not_halt_a_commit_that_succeeded(iso_plans_ignored):
    """THE REPORTED BUG. `git add -A -- . ':(exclude)_plans'` stages every file
    correctly and then exits 1, because naming `_plans` in the pathspec makes
    git report it as an ignored path even through a NEGATIVE `:(exclude)`
    (measured, git 2.48.1). Under `check=True` that benign exit raised and
    halted four sessions of one plan on commits that had already succeeded.

    This test FAILS on the parent commit.
    """
    iso = iso_plans_ignored
    tree = iso["tree"]
    # What the review gate does: copy the plan journal into the worktree.
    _touch(tree, f"_plans/{SLUG}/run.ndjson", '{"event": "gate"}\n')
    _touch(tree, "src/b.py", "B = 1\n")

    result = pship.commit(tree, "feat(demo): session work", iso["plan_dir"])

    assert result["status"] == "committed"
    assert result["files"] == ["src/b.py"]
    assert pship.plans_paths(tree, iso["state"]["base_ref"]) == []
    # And the journal is still on disk, unstaged — nothing was destroyed.
    assert (tree / "_plans" / SLUG / "run.ndjson").is_file()


def test_a_genuine_staging_failure_still_raises(iso_plans_ignored):
    """KNOWN NEGATIVE. Without this, the fix is indistinguishable from
    `check=False`. An unreadable file makes `git add` fail for a REAL reason
    ('unable to index file'), and that must still halt the session."""
    iso = iso_plans_ignored
    tree = iso["tree"]
    locked = _touch(tree, "src/locked.py", "SECRET = 1\n")
    locked.chmod(0o000)
    try:
        with pytest.raises(WorktreeError):
            pship.commit(tree, "feat(demo): work", iso["plan_dir"])
    finally:
        locked.chmod(0o644)

    # KNOWN POSITIVE — readable again, the same call commits normally, so the
    # refusal above is about the unreadable file and not about this fixture.
    assert pship.commit(tree, "feat(demo): work",
                        iso["plan_dir"])["status"] == "committed"


def test_a_force_added_plans_path_is_still_kept_out_of_the_commit(iso_plans_force_added):
    """§8.b in the case that rules out the obvious fix. With a file under
    `_plans/` force-added, `git check-ignore _plans` reports NOT ignored — so
    'drop the exclude when _plans is ignored' would stage the plan record into
    the session commit. Measured on a repo of exactly this shape: without the
    exclusion `git add -A -- .` stages `_plans/<slug>/record.json` alongside
    `real.py`; with it, only `real.py`."""
    iso = iso_plans_force_added
    tree = iso["tree"]
    rec = tree / "_plans" / SLUG / "record.json"
    assert rec.is_file() and git(["check-ignore", "-q", "--", "_plans"], tree)[0] != 0

    rec.write_text('{"v": 2}\n')
    _touch(tree, "src/b.py", "B = 1\n")

    # KNOWN POSITIVE — a bare add WOULD sweep the record, so the assertion below
    # is about the exclusion and not about an already-clean tree.
    assert "_plans/" in git(["status", "--porcelain"], tree, check=True)[1]

    result = pship.commit(tree, "feat(demo): work", iso["plan_dir"])

    assert result["files"] == ["src/b.py"]
    assert pship.plans_paths(tree, iso["state"]["base_ref"]) == []
    # The modification is still in the worktree, unstaged — not reverted.
    assert rec.read_text() == '{"v": 2}\n'


def test_the_hook_retry_also_survives_a_gitignored_plans_dir(iso_plans_ignored):
    """The retry after a tree-rewriting hook re-stages, so it had the SAME
    defect as the first call site and must be fixed by the same helper."""
    iso = iso_plans_ignored
    tree, root = iso["tree"], iso["root"]
    hooks = root / ".git" / "hooks"
    (hooks / "pre-commit").write_text(
        "#!/bin/sh\n"
        "if [ ! -f .rewrote ]; then : > .rewrote; echo 'formatted' >> src/b.py; exit 1; fi\n"
        "exit 0\n")
    (hooks / "pre-commit").chmod(0o755)
    _touch(tree, f"_plans/{SLUG}/run.ndjson", '{"event": "gate"}\n')
    _touch(tree, "src/b.py", "B = 1\n")

    result = pship.commit(tree, "feat(demo): work", iso["plan_dir"])

    assert result["status"] == "committed"
    assert "formatted" in (tree / "src" / "b.py").read_text()
    assert not any(f.startswith("_plans/") for f in result["files"])
