"""Shared git plumbing for the three quality-ratchet checkers' --staged mode.

Source of truth: gearbox scripts/quality/ (deployed to ~/.claude/scripts/quality/).
Adopting repos vendor this file into tools/ next to the checkers via
vendor_quality.py — never edit a vendored copy; re-sync instead.

NEVER RUN --generate-baseline IN A TREE OTHER SESSIONS ARE EDITING.
It measures the WORKING TREE, so every file that happens to be dirty at that
instant becomes the repo's official baseline — including a peer's half-written
function and any prose they were mid-way through adding. Nobody decides that,
nobody reviews it, and it looks identical to a deliberate re-record afterwards.
Hand-edit the specific entries you mean instead, and name the before/after in
the commit message ("a.py 795 -> 799, +4 lines of wiring at two call sites").
Hand-edits also COMPOSE: 685 -> 795 -> 799 on one file landed cleanly across
three concurrent sessions precisely because each moved one entry;
one regeneration would have flattened the other two into whatever the tree
looked like that second.

When a bump IS right, check that the growth is the CODE and not the comment you
wrote about the code — re-recording is the easy move and it silently buys
headroom for prose.

Derived independently by several sessions and stated nowhere else, which is
the argument for it living here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True,
    )


def staged_py_files(project: Path) -> list[str]:
    """Relative paths of staged .py files (added/copied/modified/renamed).

    Empty list when not in a git repo — staged mode then has nothing to judge.
    """
    result = _git(project, "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.splitlines() if f.endswith(".py")]


def parent_sources(project: Path, rel: str) -> list[str]:
    """The file's content at each commit parent (HEAD, plus MERGE_HEAD mid-merge).

    A version the file does not exist in is omitted; a brand-new file returns [].
    The MERGE_HEAD side matters: a merge that lands a long-lived branch must be
    judged against what the branch already carried, not blocked as if the merge
    commit had authored that debt.
    """
    refs = ["HEAD"]
    # rev-parse, not a .git/MERGE_HEAD stat: .git is a file in worktrees.
    if _git(project, "rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
        refs.append("MERGE_HEAD")
    sources = []
    for ref in refs:
        result = _git(project, "show", f"{ref}:{rel}")
        if result.returncode == 0:
            sources.append(result.stdout)
    return sources


def self_recorded_bounds(
    project: Path, exc_rel: str, parse, current: dict,
) -> dict:
    """Baseline entries THIS commit introduces or raises, keyed as `parse` keys.

    A ratchet whose bar can be rewritten by the same commit it judges is not a
    ratchet. Measured on the real checkers: a file grown 100 -> 600
    LOC against a 500 limit exits 0 and prints "within size limits" when the
    commit also re-records its baseline at 600 -- the `loc <= bound` test is
    satisfied by the number the commit just wrote, so the parent comparison
    below it never runs. The control (same growth, no re-record) blocks, which
    is what proves the re-record is doing it.

    Returns {key: previous_bound_or_None} for every entry whose bound is new or
    higher than in EVERY parent. An entry that is unchanged, or lowered, is not
    returned -- shrinking a baseline is always allowed and is the point.
    """
    parents = [parse(src) for src in parent_sources(project, exc_rel)]
    if not parents:
        # No parent version: a brand-new baseline file. Every entry is
        # self-recorded, and `previous` is None for all of them.
        return {k: None for k in current}
    raised = {}
    for key, bound in current.items():
        befores = [p[key] for p in parents if key in p]
        if not befores:
            raised[key] = None
        elif bound > max(befores):
            raised[key] = max(befores)
    return raised
