"""trust_ladder.py counts what it claims to count, on a repo whose counts are known.

Every section of the report is fed a planted fixture with a known answer, and
the script is RUN as a subprocess, the way the review runs it. Hermetic: HOME
points at a temporary folder, the memory folder is passed explicitly, and the
fixture commits carry their own identity and dates, so a CI runner with an
empty HOME and no git identity gets the same numbers.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import trust_ladder  # noqa: E402

SCRIPT = Path(__file__).parent / "trust_ladder.py"
DAY = 86400

FILES = {
    "CLAUDE.md": "# Rules\n"
                 "- NEVER run `git stash` without a cleanup path.\n"   # candidate: command
                 "- Never guess a cause from memory.\n"                # no token: not a candidate
                 "- The guide is `docs/missing.md`.\n"                 # broken doc path
                 "- The checker is `scripts/check_x.py`.\n"            # resolves
                 "- Never call `Task` from a subagent.\n",             # bare word: not grep-able
    "AGENTS.md": "Always pass `--json` to the collector.\n",           # candidate: flag
    ".claude/rules/small.md": "ALWAYS keep changes small.\n",           # no token
    ".claude/hooks/guard.sh": "#!/bin/sh\nexit 0\n",
    ".claude/launch.json": "{}\n",
    "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
    ".github/workflows/ci.yml": "on: push\n",
    "Makefile": "check:\n\tpytest\n",
    "scripts/check_x.py": "print('ok')\n",
    "tests/test_a.py": "def test_a():\n    pass\n",
    "tests/e2e/test_flow.py": "def test_flow():\n    pass\n",
}
MEMORY_NOTE = "Never read `~/.ssh` from a probe.\n"                    # candidate: path


def _env(home):
    return {**os.environ, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}


def _git(repo, home, *args, age_days=None):
    env = _env(home)
    if age_days is not None:
        stamp = f"{int(time.time() - age_days * DAY)} +0000"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, env=env, check=True, capture_output=True)


def _commit(repo, home, subject, age_days):
    _git(repo, home, "commit", "-q", "--allow-empty", "-m", subject, age_days=age_days)


def _init(path, home):
    path.mkdir()
    _git(path, home, "-c", "init.defaultBranch=main", "init", "-q")
    return path


def _run(repo, home, *extra):
    p = subprocess.run([sys.executable, str(SCRIPT), str(repo), "--json", *extra],
                       env=_env(home), capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout), p.stderr


def _fixture(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _init(tmp_path / "repo", home)
    for rel, text in FILES.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, home, "add", "-A")
    _commit(repo, home, "fix: an old bug", age_days=200)          # outside 90 days
    _commit(repo, home, "fix: a new bug", age_days=10)
    _commit(repo, home, 'Revert "a change"', age_days=9)
    _commit(repo, home, "hotfix: the release", age_days=8)
    _commit(repo, home, "add fixture data", age_days=7)          # "fixture" is not a fix
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "note.md").write_text(MEMORY_NOTE)
    return repo, home, memory


def test_every_section_counts_the_planted_fixture(tmp_path):
    repo, home, memory = _fixture(tmp_path)
    r, _ = _run(repo, home, "--memory-dir", str(memory))

    assert r["prose"]["CLAUDE.md"] == {"files": 1, "bytes": len(FILES["CLAUDE.md"])}
    assert r["prose"]["AGENTS.md"]["files"] == 1
    assert r["prose"]["rules"]["files"] == 1
    assert r["prose"]["memory"] == {"files": 1, "bytes": len(MEMORY_NOTE)}

    m = r["mechanical"]["configs"]
    assert m["ruff"] == ["pyproject.toml [tool.ruff]"]
    assert m["ci"] == [".github/workflows/ci.yml"]
    assert m["claude-hooks"] == [".claude/hooks/guard.sh"]
    assert m["custom-checks"] == ["scripts/check_x.py"]
    assert m["eslint"] == m["mypy"] == m["tsc"] == m["pre-commit"] == []
    assert r["mechanical"]["test_files"] == 2

    where = sorted((c["file"], c["line"]) for c in r["candidates"])
    assert where == [("AGENTS.md", 1), ("CLAUDE.md", 2), ("memory/note.md", 1)], where

    assert r["history"] == {"window_days": 90, "commits": 4, "fix": 1,
                            "revert": 1, "hotfix": 1}

    assert r["docs_drift"]["checked"] == 2
    assert r["docs_drift"]["paths"] == [{"file": "CLAUDE.md", "line": 4,
                                         "path": "docs/missing.md"}]

    assert r["verification"] == {"launch_json": True, "makefile_check_target": True,
                                 "e2e_or_integration_dirs": ["tests/e2e"]}
    assert set(r["see_also"]) == {"hyg.todo-density", "ai.verify-command"}


def test_default_memory_dir_follows_the_slug_and_absent_is_null(tmp_path):
    repo, home, _ = _fixture(tmp_path)
    r, err = _run(repo, home)
    assert r["memory_dir_found"] is False
    assert r["prose"]["memory"] == {"files": None, "bytes": None}
    assert "memory dir not found:" in err

    derived = home / ".claude" / "projects" / trust_ladder.memory_slug(repo) / "memory"
    derived.mkdir(parents=True)
    (derived / "n.md").write_text(MEMORY_NOTE)
    r, _ = _run(repo, home)
    assert r["memory_dir"] == str(derived)
    assert r["prose"]["memory"]["files"] == 1


def test_slug_turns_every_non_alphanumeric_into_a_dash():
    assert trust_ladder.memory_slug("/Users/x/.claude") == "-Users-x--claude"
    assert trust_ladder.memory_slug("/Users/x/my_proj") == "-Users-x-my-proj"


def test_an_empty_repo_yields_zeros_not_an_exception(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _init(tmp_path / "empty", home)
    memory = tmp_path / "memory"
    memory.mkdir()
    r, _ = _run(repo, home, "--memory-dir", str(memory))
    assert all(v == {"files": 0, "bytes": 0} for v in r["prose"].values()), r["prose"]
    assert all(v == [] for v in r["mechanical"]["configs"].values())
    assert r["mechanical"]["test_files"] == 0
    assert r["candidates"] == []
    assert r["history"] == {"window_days": 90, "commits": 0, "fix": 0,
                            "revert": 0, "hotfix": 0}
    assert r["docs_drift"] == {"checked": 0, "unresolved": 0, "paths": []}
    assert r["verification"] == {"launch_json": False, "makefile_check_target": False,
                                 "e2e_or_integration_dirs": []}


def test_the_text_report_names_every_section(tmp_path):
    repo, home, memory = _fixture(tmp_path)
    p = subprocess.run([sys.executable, str(SCRIPT), str(repo), "--memory-dir", str(memory)],
                       env=_env(home), capture_output=True, text=True, check=True)
    for header in ("## Prose layer", "## Mechanical layer", "## Graduation candidates: 3",
                   "## Fix and revert history", "## Docs drift: 1 of 2",
                   "## Verification base", "hyg.todo-density", "ai.verify-command"):
        assert header in p.stdout, header


def test_a_hooks_path_outside_the_repo_is_listed_not_a_crash(tmp_path):
    # A plan worktree shares the main clone's hooks: core.hooksPath is absolute
    # and outside the worktree. The report must list it, not raise ValueError.
    repo, home, memory = _fixture(tmp_path)
    hooks = tmp_path / "shared-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    _git(repo, home, "config", "core.hooksPath", str(hooks))
    r, _ = _run(repo, home, "--memory-dir", str(memory))
    assert r["mechanical"]["configs"]["pre-commit"] == [str(hooks / "pre-commit")]


def test_a_path_with_a_line_suffix_resolves(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _init(tmp_path / "cite", home)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("x = 1\n")
    (repo / "CLAUDE.md").write_text("See `src/app.py:12` and `src/app.py:3-5`; "
                                    "not `src/gone.py:7`.\n")
    _git(repo, home, "add", "-A")
    memory = tmp_path / "memory"
    memory.mkdir()
    r, _ = _run(repo, home, "--memory-dir", str(memory))
    assert r["docs_drift"] == {"checked": 3, "unresolved": 1, "paths": [
        {"file": "CLAUDE.md", "line": 1, "path": "src/gone.py"}]}


def test_first_real_run_nested_configs_plan_probes_and_memory_pointers(tmp_path):
    # Found on a real run: lint configs beside the app they lint read as
    # "ruff: 0"; plan probes counted as standing checks;
    # CLAUDE.md pointers to memory notes by bare name read as broken paths.
    home = tmp_path / "home"
    home.mkdir()
    repo = _init(tmp_path / "mono", home)
    for rel, text in {
        "apps/api/ruff.toml": "line-length = 100\n",
        "apps/api/mypy.ini": "[mypy]\n",
        "apps/api/pyproject.toml": "# [tool.mypy] was removed; see mypy.ini\n[tool.ruff]\n",
        "scripts/check-size.py": "",
        "_plans/p/_evidence/s01/probe_check.py": "",
        "_evidence/x/family_check.py": "",
        "CLAUDE.md": "See `feedback_x.md` and `gone_note.md`.\n",
    }.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, home, "add", "-A")
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "feedback_x.md").write_text("x\n")
    r, _ = _run(repo, home, "--memory-dir", str(memory))
    m = r["mechanical"]["configs"]
    assert m["ruff"] == ["apps/api/ruff.toml", "apps/api/pyproject.toml [tool.ruff]"]
    assert m["mypy"] == ["apps/api/mypy.ini"]
    assert m["custom-checks"] == ["scripts/check-size.py"]
    assert r["docs_drift"] == {"checked": 2, "unresolved": 1, "paths": [
        {"file": "CLAUDE.md", "line": 1, "path": "gone_note.md"}]}
