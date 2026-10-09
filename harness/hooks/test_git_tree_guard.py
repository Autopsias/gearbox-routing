"""Self-check for hooks/git-tree-guard.py.

The guard exists because a real command destroyed real work, so the
KNOWN POSITIVE — a dirty tree plus `git reset --hard` must be refused — is the
test that matters. The known negatives matter just as much: a guard that blocks
ordinary work gets disabled, and then it protects nothing.

Run: python3 -m pytest hooks/test_git_tree_guard.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent / "git-tree-guard.py"


def run(command, cwd, env=None):
    payload = json.dumps({"tool_input": {"command": command}, "cwd": str(cwd)})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, env=env)


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    (tmp_path / "tracked.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


def dirty(repo):
    (repo / "tracked.txt").write_text("MODIFIED — not committed anywhere\n")
    return repo


# --- known positives: the tree is dirty, the command discards it -------------
@pytest.mark.parametrize("command", [
    "git reset --hard HEAD",
    "git reset --hard",
    "git reset -q --hard HEAD",
    "git revert --abort 2>/dev/null; git reset -q --hard HEAD 2>/dev/null",  # the real one
    "cd /tmp && git reset --hard origin/main",
    "git checkout -- .",
    "git checkout .",
    "git restore .",
    "git clean -fd",
    "git -C . reset --hard",
])
def test_blocks_when_tracked_work_would_be_lost(repo, command):
    r = run(command, dirty(repo))
    assert r.returncode == 2, f"{command!r} was ALLOWED over a dirty tree"
    assert "BLOCKED" in r.stderr and "tracked modification" in r.stderr


def test_the_block_names_what_is_at_risk(repo):
    r = run("git reset --hard", dirty(repo))
    assert "tracked.txt" in r.stderr
    assert "GEARBOX_ALLOW_DIRTY_RESET=1" in r.stderr      # the way through is stated


# --- known negatives: blocking these would make the guard get switched off ---
def test_allows_when_the_tree_is_clean(repo):
    assert run("git reset --hard HEAD", repo).returncode == 0


def test_allows_a_path_scoped_checkout(repo):
    assert run("git checkout -- tracked.txt", dirty(repo)).returncode == 0


def test_allows_a_soft_or_mixed_reset(repo):
    d = dirty(repo)
    assert run("git reset --soft HEAD~1", d).returncode == 0
    assert run("git reset HEAD", d).returncode == 0


def test_allows_ordinary_commands(repo):
    d = dirty(repo)
    for command in ("git status", "git diff", "pytest -q", "ls -la",
                    "git stash list", "git clean -n", "git log --oneline"):
        assert run(command, d).returncode == 0, command


def test_does_not_fire_on_the_words_inside_a_string(repo):
    d = dirty(repo)
    for command in ('echo "never run git reset --hard here"',
                    "grep -rn 'git reset --hard' docs/",
                    "cat <<'EOF'\ngit reset --hard\nEOF"):
        assert run(command, d).returncode == 0, command


def test_escape_hatch_lets_a_deliberate_discard_through(repo):
    r = run("GEARBOX_ALLOW_DIRTY_RESET=1 git reset --hard", dirty(repo))
    assert r.returncode == 0


def test_allows_outside_a_git_repo(tmp_path):
    assert run("git reset --hard", tmp_path).returncode == 0


def test_never_crashes_on_junk_input():
    r = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True)
    assert r.returncode == 0


# --- security review: the escape hatch and the wrapper -------------
@pytest.mark.parametrize("command", [
    'echo "GEARBOX_ALLOW_DIRTY_RESET=1" && git reset --hard',   # token, not a prefix
    "grep GEARBOX_ALLOW_DIRTY_RESET= notes.md; git reset --hard",
])
def test_the_token_alone_does_not_open_the_hatch(repo, command):
    assert run(command, dirty(repo)).returncode == 2, command


@pytest.mark.parametrize("command", [
    "bash -c 'git reset --hard'",
    'sh -c "git reset --hard HEAD"',
    "zsh -c 'cd /tmp && git clean -fd'",
])
def test_blocks_through_a_shell_wrapper(repo, command):
    assert run(command, dirty(repo)).returncode == 2, command


def test_the_hatch_still_works_as_a_real_prefix(repo):
    assert run("GEARBOX_ALLOW_DIRTY_RESET=1 git reset --hard", dirty(repo)).returncode == 0


def test_the_hatch_works_from_the_environment(repo):
    import os
    env = dict(os.environ, GEARBOX_ALLOW_DIRTY_RESET="1")
    assert run("git reset --hard", dirty(repo), env=env).returncode == 0


# --- end-to-end run: the session cwd is not the target repo --------
# The guard was live, its unit tests green, and a real `cd <scratch repo> && git
# reset --hard` STILL destroyed the file — the hook judged the clean session
# directory. A live run, not a config check, is what found it.
def test_blocks_a_cd_into_a_dirty_repo_from_a_clean_cwd(repo, tmp_path):
    clean = tmp_path / "clean"
    clean.mkdir()
    subprocess.run(["git", "init", "-q", str(clean)], check=True)
    r = run(f"cd {dirty(repo)} && git reset --hard HEAD", clean)
    assert r.returncode == 2
    assert "tracked.txt" in r.stderr


def test_blocks_git_C_at_a_dirty_repo_from_a_clean_cwd(repo, tmp_path):
    clean = tmp_path / "clean2"
    clean.mkdir()
    subprocess.run(["git", "init", "-q", str(clean)], check=True)
    assert run(f"git -C {dirty(repo)} reset --hard", clean).returncode == 2


def test_a_cd_into_a_clean_repo_is_still_allowed(repo, tmp_path):
    clean = tmp_path / "clean3"
    clean.mkdir()
    subprocess.run(["git", "init", "-q", str(clean)], check=True)
    assert run(f"cd {clean} && git reset --hard", clean).returncode == 0


def test_heredoc_opener_may_carry_trailing_shell(repo):
    """Observed live: `git commit -F - <<'EOF' 2>&1 | tail -2` left the
    body unstripped, so a commit MESSAGE describing the command was refused."""
    command = ("git commit -q -F - <<'EOF' 2>&1 | tail -2\n"
               "fix: a message that mentions git reset --hard HEAD\n"
               "EOF")
    assert run(command, dirty(repo)).returncode == 0
