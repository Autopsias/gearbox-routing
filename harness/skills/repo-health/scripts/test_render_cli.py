"""health_render.py must render when it is RUN, not only when it is imported.

This file once had no `__main__` block, so `python3 health_render.py`
defined its functions and exited 0 having written nothing — a silent no-op
wearing a success code, the exact failure this skill exists to catch. Every
other test imports `render` and calls `render.render()`, so all of them stayed
green while the command line did nothing at all.

This test runs the file the way a person runs it: as a subprocess. Delete the
`__main__` block and it fails.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import health  # noqa: E402

RENDER = Path(__file__).parent / "health_render.py"

CARD = {
    "repo": "fixture", "repo_path": "/fixture", "commit": "abc1234",
    "tree": "abc1234", "date": "2026-08-21",
    "checks": [{
        "id": "cq.lint", "title": "Linter clean", "layer": "code_quality",
        "tier": "advisory", "status": "pass", "detail": "0 errors",
        "fix": "", "recorded": "2026-08-21",
    }],
}


def _fixture(tmp_path):
    d = tmp_path / ".claude" / "health"
    d.mkdir(parents=True)
    (d / "scorecard.json").write_text(json.dumps(CARD, indent=1) + "\n")
    return d


def _run(*argv, cwd=None):
    return subprocess.run([sys.executable, str(RENDER), *argv],
                          capture_output=True, text=True, cwd=cwd)


def test_running_render_as_a_script_actually_writes_the_dashboard(tmp_path):
    d = _fixture(tmp_path)
    r = _run(str(tmp_path))
    assert r.returncode == 0, r.stderr
    # Exit 0 is NOT the assertion — the no-op this guards against exited 0 too.
    assert (d / "HEALTH.html").exists(), f"no dashboard written: {r.stdout!r}"
    assert "verdict:" in r.stdout, f"rendered nothing and said nothing: {r.stdout!r}"
    assert "cq.lint" in (d / "HEALTH.html").read_text()


def test_the_repo_flag_the_comment_points_at_works_too(tmp_path):
    """`health.py render` takes --repo; following that sentence must not crash."""
    d = _fixture(tmp_path)
    r = _run("--repo", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (d / "HEALTH.html").exists()


def test_a_bare_run_renders_the_current_directory(tmp_path):
    d = _fixture(tmp_path)
    r = _run(cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (d / "HEALTH.html").exists()


def test_health_py_render_and_render_py_agree(tmp_path):
    """The two entrypoints must not drift into rendering different pages."""
    d = _fixture(tmp_path)
    _run(str(tmp_path))
    via_render = (d / "HEALTH.html").read_text()
    (d / "HEALTH.html").unlink()
    subprocess.run([sys.executable, str(Path(health.__file__)), "render",
                    "--repo", str(tmp_path)], capture_output=True, text=True, check=True)
    assert (d / "HEALTH.html").read_text() == via_render


# ---------- the page must say when it is not describing your tree ----------
#
# Every case below is keyed on the TREE hash, not the commit sha. A sha-keyed
# check passed the first two of these and failed the other three.

def _git(tmp_path, *args, **kw):
    return subprocess.run(["git", *args], cwd=tmp_path, capture_output=True,
                          text=True, check=True, **kw)


def _git_repo(tmp_path):
    """A real one-commit repo, clean, with the health dir ignored."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("one\n")
    (tmp_path / ".gitignore").write_text(".claude/\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "one")
    return _git(tmp_path, "rev-parse", "HEAD^{tree}").stdout.strip()


def _stamp(d, **fields):
    card = json.loads((d / "scorecard.json").read_text())
    card.update(fields)
    (d / "scorecard.json").write_text(json.dumps(card, indent=1) + "\n")


def _stale_line(d):
    page = (d / "HEALTH.html").read_text()
    return "" if 'class="stale"' not in page else page.split('class="stale">')[1][:200]


def test_a_clean_tree_that_matches_says_nothing(tmp_path):
    """The known negative. A caveat that always prints is not a caveat."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_git_repo(tmp_path))
    _run(str(tmp_path))
    assert _stale_line(d) == ""


def test_a_dirty_tree_says_so(tmp_path):
    """The case a commit-sha check is BLIND to: HEAD never moved, the code did.

    The scorecard shipped had tree "" for exactly this reason and
    the page said nothing at all.
    """
    d = _fixture(tmp_path)
    _stamp(d, tree=_git_repo(tmp_path))
    (tmp_path / "f.txt").write_text("edited\n")
    (tmp_path / "new.txt").write_text("untracked\n")
    _run(str(tmp_path))
    assert "DIRTY" in _stale_line(d)


def test_a_scorecard_collected_dirty_says_so(tmp_path):
    """tree "" is what tree_key() returns for a dirty collect. Never silence."""
    d = _fixture(tmp_path)
    _git_repo(tmp_path)
    _stamp(d, tree="")
    _run(str(tmp_path))
    assert "DIRTY or unidentifiable" in _stale_line(d)


def test_amending_the_message_is_NOT_stale(tmp_path):
    """The false alarm. --amend moves the sha; the tree is byte-identical."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_git_repo(tmp_path))
    _git(tmp_path, "commit", "-q", "--amend", "-m", "reworded")
    _run(str(tmp_path))
    assert _stale_line(d) == "", "cried stale over a reworded commit message"


def test_a_genuinely_changed_tree_says_so(tmp_path):
    d = _fixture(tmp_path)
    _stamp(d, tree=_git_repo(tmp_path))
    (tmp_path / "f.txt").write_text("two\n")
    _git(tmp_path, "commit", "-qam", "two")
    _run(str(tmp_path))
    assert "DIFFERENT tree" in _stale_line(d)


def test_no_git_at_all_makes_no_false_all_clear(tmp_path):
    """Not a git repo: tree_key() cannot identify it, so the page must NOT
    imply these numbers describe the reader's code."""
    d = _fixture(tmp_path)
    _run(str(tmp_path))
    assert _stale_line(d) != ""


def test_a_non_string_commit_does_not_crash_the_render(tmp_path):
    """Guard the parsed value's TYPE, not just the parse."""
    d = _fixture(tmp_path)
    _stamp(d, commit=1234567, tree=_git_repo(tmp_path))
    r = _run(str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (d / "HEALTH.html").exists()


def test_a_missing_scorecard_names_the_next_command(tmp_path):
    r = _run(str(tmp_path))
    assert r.returncode != 0
    assert "health.py collect" in (r.stdout + r.stderr)
    assert "Traceback" not in r.stderr


def test_two_different_repos_is_refused_not_silently_resolved(tmp_path):
    _fixture(tmp_path)
    r = _run(str(tmp_path), "--repo", "/nowhere")
    assert r.returncode != 0
    assert "two different repos" in (r.stdout + r.stderr)


def test_the_same_repo_named_twice_is_not_two_repos(tmp_path):
    """`health_render.py "$(pwd)" --repo .` names ONE directory. Compare resolved."""
    d = _fixture(tmp_path)
    r = subprocess.run([sys.executable, str(RENDER), str(tmp_path), "--repo", "."],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert (d / "HEALTH.html").exists()


# ---------- the two cases review found, round 4 ----------

def _tracked_health_repo(tmp_path):
    """This repo's REAL layout: .claude/health/ is tracked, not ignored."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("one\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "one")
    return _git(tmp_path, "rev-parse", "HEAD^{tree}").stdout.strip()


def test_committing_the_dashboard_is_NOT_a_stale_tree(tmp_path):
    """The false alarm. Committing our own output changes the tree hash, and a
    banner that fires every time you commit the thing it describes trains the
    reader to ignore it — which is worse than no banner at all."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_tracked_health_repo(tmp_path))
    _run(str(tmp_path))
    assert _stale_line(d) == "", "cried stale before anything was committed"
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "commit the health output")
    _run(str(tmp_path))
    assert _stale_line(d) == "", "cried stale over committing its own dashboard"


def test_a_real_code_change_still_reads_stale_when_health_is_tracked(tmp_path):
    """The known positive for the exclusion above: it must not swallow REAL drift."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_tracked_health_repo(tmp_path))
    (tmp_path / "f.txt").write_text("two\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "real change")
    _run(str(tmp_path))
    assert "DIFFERENT tree" in _stale_line(d)


def test_an_untrustworthy_run_is_marked_in_the_history(tmp_path):
    """A score the page called unreliable must not re-enter the trend strip as
    a clean measurement on every later render."""
    d = _fixture(tmp_path)
    _git_repo(tmp_path)
    _stamp(d, tree="")                       # collected dirty
    _run(str(tmp_path))
    line = json.loads((d / "history.jsonl").read_text().splitlines()[-1])
    assert line.get("stale"), f"unreliable run recorded as clean: {line}"
    assert 'class="run now unsure"' in (d / "HEALTH.html").read_text()


def test_a_trustworthy_run_carries_no_marker(tmp_path):
    """The known negative — a marker on every run marks nothing."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_git_repo(tmp_path))
    _run(str(tmp_path))
    line = json.loads((d / "history.jsonl").read_text().splitlines()[-1])
    assert "stale" not in line
    assert 'class="run now unsure"' not in (d / "HEALTH.html").read_text()


def test_a_tree_this_clone_does_not_have_names_THAT_cause(tmp_path):
    """`git diff --quiet` answers 0 = same, 1 = differs, 128 = I cannot read
    that object. Both non-zero cases stay stale — the safe direction — but they
    are different sentences. Telling the reader "this tree has changed since"
    when the truth is "I cannot find that tree here" sends them to re-run
    collect over a difference that does not exist (review).
    """
    d = _fixture(tmp_path)
    _tracked_health_repo(tmp_path)
    _stamp(d, tree="0" * 40)                 # well-formed, never existed here
    _run(str(tmp_path))
    line = _stale_line(d)
    assert "DOES NOT HAVE" in line, line
    assert "has changed since" not in line


def test_a_tree_that_really_changed_still_names_THAT_cause(tmp_path):
    """The known negative for the branch above — the two must not collapse."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_tracked_health_repo(tmp_path))
    (tmp_path / "f.txt").write_text("two\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "real change")
    _run(str(tmp_path))
    line = _stale_line(d)
    assert "DIFFERENT tree" in line and "has changed since" in line
    assert "DOES NOT HAVE" not in line


def test_a_malformed_tree_value_never_reaches_git_as_an_option(tmp_path):
    """`tree` is read from a JSON file and passed to git POSITIONALLY.

    Review reproduced both halves live: tree "HEAD" compared clean and reported
    FRESH — the exact false all-clear this function exists to prevent — and
    tree "--output=/tmp/PWNED" was parsed by git as an OPTION, reported fresh,
    AND created the file. Anything that is not a 40-hex object name is not a
    tree hash and gets no identity at all.
    """
    d = _fixture(tmp_path)
    _tracked_health_repo(tmp_path)
    written = tmp_path / "PWNED"
    for hostile in ("HEAD", f"--output={written}", "HEAD~1", "-x"):
        _stamp(d, tree=hostile)
        _run(str(tmp_path))
        line = _stale_line(d)
        assert line, f"tree {hostile!r} was accepted as a real identity — false all-clear"
        assert "not a tree hash" in line, f"tree {hostile!r} -> {line!r}"
        assert not written.exists(), f"tree {hostile!r} made git write {written}"


def test_a_real_40_hex_tree_is_still_accepted(tmp_path):
    """The known negative — the shape check must not reject genuine hashes."""
    d = _fixture(tmp_path)
    _stamp(d, tree=_tracked_health_repo(tmp_path))
    _run(str(tmp_path))
    assert _stale_line(d) == "", "the shape check rejected a real tree hash"
