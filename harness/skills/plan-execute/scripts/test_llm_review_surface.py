"""The review SURFACE is bounded by a session's review_scope, on BOTH halves.

Why, measured across two plans in one checkout: the surface was the
whole tree, so each session's review read the other's uncommitted source, and
three rework attempts went on findings in files the reviewed session never
touched. `_out_of_surface` already kept other plans' `_plans/**` out; this keeps
other sessions' SOURCE out, and it does so on the diff file the reviewer reads,
not only on the name list -- a reviewer handed a file with the other session's
hunks in it would review them regardless of what the banner said.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import llm_review_surface as srf  # noqa: E402


def _repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    def run(*a):
        return subprocess.run(list(a), cwd=r, check=True, capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    return r, run


def _seed(r, run):
    for d in ("mine", "mine2", "theirs"):
        (r / d).mkdir()
        (r / d / "a.py").write_text("x = 1\n")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "base")
    for d in ("mine", "mine2", "theirs"):
        (r / d / "a.py").write_text("x = 2\n")          # modified, all three
        (r / d / "new.py").write_text("y = 1\n")        # untracked, all three


# ---------- the predicate ----------

def test_scope_is_parsed_from_either_separator():
    assert srf.parse_scope("skills/a, skills/b") == ["skills/a", "skills/b"]
    assert srf.parse_scope("skills/a:skills/b") == ["skills/a", "skills/b"]
    assert srf.parse_scope("./skills/a") == ["skills/a"]
    assert srf.parse_scope("") == [] and srf.parse_scope(None) == []


def test_an_empty_scope_reviews_everything():
    assert srf.in_scope("anything/at/all.py", [])


def test_scope_matches_the_directory_and_everything_under_it():
    assert srf.in_scope("skills/x", ["skills/x"])
    assert srf.in_scope("skills/x/deep/n.py", ["skills/x"])
    assert srf.in_scope("skills/x/deep/n.py", ["skills/x/"])


def test_scope_does_not_leak_across_a_shared_PREFIX():
    """`skills/x` must not swallow `skills/xy` -- a bare startswith would, and
    the failure grows the surface silently into a sibling plan's directory."""
    assert not srf.in_scope("skills/xy/z.py", ["skills/x"])
    assert not srf.in_scope("skills/plan-execute-old/a.py", ["skills/plan-execute"])


# ---------- both halves of the surface honour it ----------

def test_untracked_files_outside_the_scope_are_dropped_WITH_A_REASON(tmp_path):
    r, run = _repo(tmp_path)
    _seed(r, run)
    kept, dropped = srf.untracked_files(str(r), scope=["mine"])
    assert kept == ["mine/new.py"]
    assert {p for p, _ in dropped} == {"mine2/new.py", "theirs/new.py"}
    assert all("review_scope" in why for _, why in dropped), dropped


def test_changed_files_outside_the_scope_are_dropped(tmp_path):
    r, run = _repo(tmp_path)
    _seed(r, run)
    assert srf.diff_stat(str(r)) == ["mine/a.py", "mine2/a.py", "theirs/a.py"]
    assert srf.diff_stat(str(r), scope=["mine"]) == ["mine/a.py"]


def test_git_pathspec_matches_whole_components_like_in_scope_does(tmp_path):
    """The two halves must agree: `mine` must not pull in `mine2` on either."""
    r, run = _repo(tmp_path)
    _seed(r, run)
    assert "mine2/a.py" not in srf.diff_stat(str(r), scope=["mine"])
    kept, _ = srf.untracked_files(str(r), scope=["mine"])
    assert "mine2/new.py" not in kept


def test_the_diff_FILE_the_reviewer_reads_is_scoped_too(tmp_path):
    """Scoping the name list alone is theatre if the file still has their hunks."""
    r, run = _repo(tmp_path)
    _seed(r, run)
    out = tmp_path / "d.diff"
    assert srf.write_diff_file(str(r), None, str(out), scope=["mine"])
    text = out.read_text()
    assert "a/mine/a.py" in text
    assert "theirs/" not in text and "mine2/" not in text, text


def test_no_scope_keeps_the_old_whole_tree_diff_file(tmp_path):
    r, run = _repo(tmp_path)
    _seed(r, run)
    out = tmp_path / "d.diff"
    assert srf.write_diff_file(str(r), None, str(out))
    text = out.read_text()
    assert "a/mine/a.py" in text and "a/theirs/a.py" in text and "a/mine2/a.py" in text


# ---------- refuse, never pass, on a scope that matches nothing ----------

def test_an_empty_SCOPED_surface_is_indeterminate_not_a_pass(tmp_path, capsys):
    """The tree is dirty, so 'nothing to review' looks like success. A wrong
    scope reviewing zero files and reporting green is the silent-green this
    gate exists to refuse -- so it must refuse, and must NAME the scope."""
    r, run = _repo(tmp_path)
    _seed(r, run)
    new_files, code = srf.resolve_surface("low", str(r), None, scope=["nothing-here"])
    assert new_files is None and code == srf.INDETERMINATE
    err = capsys.readouterr().err
    assert "INDETERMINATE" in err and "nothing-here" in err and "SCOPE is wrong" in err


def test_the_scope_is_declared_in_the_surface_banner(tmp_path, capsys):
    r, run = _repo(tmp_path)
    _seed(r, run)
    srf.resolve_surface("low", str(r), None, scope=["mine"])
    assert "SCOPED to mine" in capsys.readouterr().out
    srf.resolve_surface("low", str(r), None)
    assert "scope=WHOLE TREE" in capsys.readouterr().out


# ---------- the surface-size banner ----------

def test_surface_size_counts_diff_and_untracked_lines(tmp_path):
    r, run = _repo(tmp_path)
    _seed(r, run)
    # _seed: 3 dirs x (a.py modified 1 line -> +1/-1, new.py 1 line untracked)
    files, added, deleted = srf.surface_size(str(r), None, [], ["mine/new.py"])
    assert files == 4 and added == 3 + 1 and deleted == 3
    files_s, added_s, deleted_s = srf.surface_size(str(r), None, ["mine"], ["mine/new.py"])
    assert files_s == 2 and added_s == 2 and deleted_s == 1


def test_size_banner_names_the_band_and_warns_past_600():
    assert "~87%" in srf.size_banner((2, 40, 10))
    assert "EXPECT findings on a rework" not in srf.size_banner((2, 40, 10))
    big = srf.size_banner((30, 900, 200))
    assert "~28%" in big and "EXPECT findings on a rework" in big and "sample, not a regression" in big


def test_modified_files_a_scope_drops_are_PRINTED_not_silent(tmp_path, capsys):
    """A scope narrows the diff by pathspec. A reader must be able to tell a
    correct scope (the other plan's files) from a wrong one (this session's own
    work edited outside its declared scope) -- and can only do that if the gate
    says what it did not review."""
    r, run = _repo(tmp_path)
    _seed(r, run)
    srf.resolve_surface("low", str(r), None, scope=["mine"])
    err = capsys.readouterr().err
    assert "outside review_scope" in err
    assert "theirs/a.py" in err and "mine2/a.py" in err, err
    assert "mine/a.py" not in err


def test_a_root_dotfile_keeps_its_dot_in_scope():
    """`lstrip("./")` strips the CHARACTER SET: `.complexity-exceptions` became
    `complexity-exceptions` and matched nothing. Found in the first real manifest."""
    assert srf.parse_scope(".complexity-exceptions, ./.x/y") == [".complexity-exceptions", ".x/y"]
    assert srf.in_scope(".complexity-exceptions", [".complexity-exceptions"])
    assert srf.in_scope("./.x/y/z.py", [".x"])
    assert not srf.in_scope("complexity-exceptions", [".complexity-exceptions"])


def test_a_plan_dir_with_no_journal_refuses_instead_of_reviewing_the_wrong_files(tmp_path, capsys):
    """A worktree gets the plan's TRACKED files and not its gitignored run.ndjson,
    so `review_context.derive_base` returns None and the surface silently becomes
    `git diff HEAD`. For a session that already COMMITTED, that is not a smaller
    surface -- it is a different one, holding whatever happens to be dirty. The
    emptiness guard does not catch it: one unrelated dirty file makes it
    non-empty and a clean review of the wrong files is a genuine PASS
    (found by the example-isolation-plan session)."""
    plan = tmp_path / "_plans" / "p"
    plan.mkdir(parents=True)      # no run.ndjson in it
    out = srf.resolve_surface("medium", str(tmp_path), None, str(plan), "s01")
    assert out[0] is None and out[1] == srf.INDETERMINATE
    err = capsys.readouterr().err
    assert "INDETERMINATE" in err and "run.ndjson" in err
    assert "not this session's work" in err.replace("NOT", "not")


def test_a_journal_that_simply_names_no_dispatch_is_LOUD_not_fatal(tmp_path, capsys):
    """The control. Sessions dispatched before session_base_recorded existed have
    no derivable base, and refusing every one would fail closed on a healthy plan.
    They keep the fallback -- but never silently."""
    plan = tmp_path / "_plans" / "p"
    plan.mkdir(parents=True)
    (plan / "run.ndjson").write_text('{"event": "dispatch_started", "session_ids": ["sZZ"]}\n')
    srf.resolve_surface("medium", str(tmp_path), None, str(plan), "s01")
    err = capsys.readouterr().err
    assert "NO REVIEW BASE" in err                 # said out loud
    assert "INDETERMINATE — no --base" not in err  # but not refused


def test_a_dropped_path_is_named_with_the_reason_that_actually_dropped_it(tmp_path, capsys):
    """With BOTH --scope and --exclude set, every dropped path was reported as
    "outside review_scope" — including the ones the EXCLUSION dropped, so anyone
    auditing why the surface shrank read a wrong answer.

    No shipped caller sets both TODAY (`verify` passes a scope, the land re-gate
    passes only the exclusion), so this is the defensive half of the rule and
    this test is its only exercise. The two single-flag cases below are the ones
    production actually takes, and they are asserted as exact full lines.
    """
    r, run = _repo(tmp_path)
    _seed(r, run)
    srf.resolve_surface("low", str(r), None, scope=["mine", "mine2"], exclude=["mine2"])
    lines = [ln for ln in capsys.readouterr().err.splitlines() if "not this session" in ln]
    reasons = {ln.rsplit(": ", 1)[1]: ln for ln in lines}
    assert "outside review_scope" in reasons["theirs/a.py"], reasons["theirs/a.py"]
    assert "excluded from the review surface" in reasons["mine2/a.py"], reasons["mine2/a.py"]
    # KNOWN POSITIVE — with only ONE of the two set, the single reason is right.
    srf.resolve_surface("low", str(r), None, scope=["mine"])
    only_scope = capsys.readouterr().err
    assert "not this session's surface (outside review_scope): theirs/a.py" in only_scope
    srf.resolve_surface("low", str(r), None, exclude=["theirs"])
    only_excl = capsys.readouterr().err
    assert ("not this session's surface (excluded from the review surface): theirs/a.py"
            in only_excl)
