"""§10.1 — the file set a gate is pointed at, and the two ways it goes blind.

Contract: ``../references/plan-isolation-contract.md`` §10 (v1, frozen).

Every check here is paired with the WRONG answer it exists to reject, computed in
the same tree: a test that only shows `changed_files` returned something has not
shown that the something is different from what a gate already had. The two
rejected answers are `git diff HEAD` (which cannot see a new file at all) and
line-oriented output (which cannot see a path git chose to quote).

    pytest plan-execute/scripts/test_gate_files.py -q
"""

import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import gate_files as gf  # noqa: E402
import worktree as wt  # noqa: E402


def _repo(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "alpha.py").write_text("A = 1\n")
    wt.git(["init", "-q", "-b", "main"], root, check=True)
    wt.git(["config", "user.email", "t@example.com"], root, check=True)
    wt.git(["config", "user.name", "T"], root, check=True)
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "base"], root, check=True)
    return root


def _diff_head(root):
    """The FORBIDDEN input, computed here so the contrast is measured, not
    asserted from the contract's prose."""
    rc, out, _ = wt.git(["diff", "--name-only", "HEAD"], root, strip=False)
    assert rc == 0
    return sorted(ln.strip() for ln in out.splitlines() if ln.strip())


# ------------------------------------------------- the untracked blind spot
def test_a_whole_new_module_is_in_the_set_and_invisible_to_git_diff_HEAD(tmp_path):
    """The gate that cannot fail. A session whose output is all-new files leaves
    `git diff HEAD` EMPTY — exit 0, zero findings, indistinguishable from clean —
    and `git add -A` then commits code nothing examined."""
    root = _repo(tmp_path)
    (root / "src" / "brand_new.py").write_text("def f():\n    return 1\n")
    assert _diff_head(root) == []                       # the wrong answer, measured
    assert gf.changed_files(root) == ["src/brand_new.py"]


def test_a_wholly_new_DIRECTORY_is_listed_file_by_file(tmp_path):
    """`-uall`, not the default: plain porcelain collapses a new untracked tree to
    one `pkg/` entry, so a gate filtering by extension checks nothing and reports
    a pass over an unrun suite."""
    root = _repo(tmp_path)
    (root / "pkg").mkdir()
    (root / "pkg" / "a.py").write_text("A = 1\n")
    (root / "pkg" / "b.py").write_text("B = 1\n")
    assert gf.changed_files(root) == ["pkg/a.py", "pkg/b.py"]


def test_staged_unstaged_deleted_and_untracked_are_all_in_one_set(tmp_path):
    root = _repo(tmp_path)
    (root / "src" / "beta.py").write_text("B = 1\n")     # untracked
    (root / "staged.py").write_text("S = 1\n")
    wt.git(["add", "staged.py"], root, check=True)       # staged
    (root / "src" / "alpha.py").write_text("A = 2\n")    # unstaged edit
    wt.git(["rm", "-q", "--cached", "src/alpha.py"], root)
    (root / "src" / "alpha.py").write_text("A = 2\n")
    assert set(gf.changed_files(root)) >= {
        "src/beta.py", "staged.py", "src/alpha.py"}


def test_a_rename_contributes_BOTH_paths(tmp_path):
    """A rename record is `XY <new> NUL <orig> NUL` — two fields for one entry.
    Consuming only the first mis-frames every record after it, because the orig
    would then be parsed as the next entry's status code."""
    root = _repo(tmp_path)
    wt.git(["mv", "src/alpha.py", "src/renamed.py"], root, check=True)
    (root / "after.py").write_text("Z = 1\n")            # must still be framed right
    assert gf.changed_files(root) == ["after.py", "src/alpha.py", "src/renamed.py"]


# ----------------------------------------------------------------- quoting
def test_a_non_ascii_path_survives_and_line_output_does_not(tmp_path):
    """Under the default `core.quotePath` git escapes and DOUBLE-QUOTES a
    non-ASCII path. The known positive is the raw output: if git ever stopped
    quoting, this test would be comparing two identical strings and proving
    nothing, so the quoted form is asserted directly."""
    root = _repo(tmp_path)
    (root / "plâno.py").write_text("P = 1\n")
    rc, raw, _ = wt.git(["status", "--porcelain", "-uall"], root, strip=False)
    assert rc == 0 and '"pl\\303\\242no.py"' in raw     # git really does quote it
    assert gf.changed_files(root) == ["plâno.py"]


def test_a_path_with_a_space_is_one_entry(tmp_path):
    root = _repo(tmp_path)
    (root / "a file.py").write_text("A = 1\n")
    assert gf.changed_files(root) == ["a file.py"]


# --------------------------------------------------- the post-commit range
def test_committed_work_needs_the_base_and_is_unioned_with_the_dirty_set(tmp_path):
    """Committed work is invisible to `status`; uncommitted work is invisible to
    a range. A gate spanning the boundary takes the union of both."""
    root = _repo(tmp_path)
    base = wt.git(["rev-parse", "HEAD"], root, check=True)[1]
    (root / "committed.py").write_text("C = 1\n")
    wt.git(["add", "-A"], root, check=True)
    wt.git(["commit", "-q", "-m", "work"], root, check=True)
    (root / "still_dirty.py").write_text("D = 1\n")

    assert gf.changed_files(root) == ["still_dirty.py"]            # status alone
    assert gf.changed_files(root, base) == ["committed.py", "still_dirty.py"]


# ------------------------------------------------------------- failing shut
def test_a_non_repo_answers_None_and_never_an_empty_set(tmp_path):
    """None and [] must not be conflated: a gate that cannot read its file set
    has to fail closed, where an honestly empty set is a WARNING the caller
    surfaces. Returning [] here would make an unreadable tree look clean."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    assert gf.changed_files(outside) is None
    assert gf.status_entries(outside) is None


def test_the_cli_exits_2_and_says_so_when_it_cannot_read_the_tree(tmp_path):
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    p = subprocess.run([sys.executable, str(SCRIPTS / "gate_files.py"),
                        "--cwd", str(outside)], capture_output=True, text=True)
    assert p.returncode == 2 and p.stdout == ""
    assert "cannot read the file set" in p.stderr


def test_the_cli_prints_one_path_per_line_and_nul_on_demand(tmp_path):
    root = _repo(tmp_path)
    (root / "a file.py").write_text("A = 1\n")
    argv = [sys.executable, str(SCRIPTS / "gate_files.py"), "--cwd", str(root)]
    assert subprocess.run(argv, capture_output=True, text=True).stdout == "a file.py\n"
    assert subprocess.run([*argv, "-0"], capture_output=True,
                          text=True).stdout == "a file.py\0"
