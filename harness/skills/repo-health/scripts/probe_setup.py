#!/usr/bin/env python3
"""Fixture setup steps that GIT, not a file, has to make true.

Split from probe_cases.py, which is the corpus itself — the file bodies a
fixture repo is built from. Everything here mutates a repo AFTER make_repo has
committed it: a remote, an old committer date, an unmerged branch, a stash, a
tracked path that will not open. That is a different kind of thing from a
dictionary of file contents, and probe_cases.py sat at the 500-LOC blocking
limit, so the split is the same one card.py and quality_checks.py already made
out of health.py.

Imported by probe_cases.py, never the other way round.
"""
import os
import re
import subprocess

from test_health_shape import git


def add_remote(repo):
    git(repo, "remote", "add", "origin", "https://example.invalid/x.git")


def age_commit(repo):
    """Move the COMMITTER date back a year — %ct is what hyg.activity reads."""
    old = "2020-01-01T00:00:00+00:00"
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "--amend", "--no-edit", "--date", old],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": old})


def unmerged_branch(repo):
    head = subprocess.run(["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    git(repo, "checkout", "-qb", "feature")
    (repo / "src" / "feature.py").write_text("y = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "wip")
    git(repo, "checkout", "-q", head)


def leave_stash(repo):
    (repo / "src" / "app.py").write_text("def main():\n    return 99\n")
    git(repo, "stash", "push", "-qm", "probe")


def hide_from_worktree(rel):
    """Leave a file TRACKED but unopenable — git ls-files still lists it.

    The state a sparse checkout, an interrupted rebase or a mid-`git rm` leaves
    behind, and the one that made `read()`'s "" indistinguishable from an empty
    file. Deliberately not chmod 000: a suite running as root reads that fine.
    """
    def setup(repo):
        (repo / rel).unlink()
    setup.__name__ = "hide_" + re.sub(r"[^a-z]+", "_", rel)
    return setup


def dangling_symlink(repo):
    """A tracked symlink whose target does not exist — `stat()` raises OSError.

    The cheapest real file the size check cannot measure. It used to be swallowed
    by a bare `except OSError: pass`, and the scorecard then said "no tracked file
    over 5 MB" over a file nothing had looked at.
    """
    os.symlink("no-such-target", repo / "dangling")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "dangling")
