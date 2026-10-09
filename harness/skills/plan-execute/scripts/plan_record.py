"""ISO-02 §8.c — WHO commits the plan record, and WHEN.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).
``plan_ship.py`` is HOW a SESSION's work lands on the plan branch; this module is
the other, separate job: copying the LIVE outer plan directory into a target
checkout and committing just it. s08 calls it inside the LAND worktree.

It exists at all because §8.b removes ``git add -A`` from the job of committing
the plan record, and a rule that removes the only writer without naming the
replacement leaves ``PLAN.html``, ``run_state.json`` and every closeout
permanently uncommitted.

Split out of ``plan_ship.py`` 2026-08-26 for the same measured reason
``plan_scope.py``'s own docstring records: this repo's ``pre-commit`` runs
``check_file_sizes.py --staged`` with a 500-LOC default, and the two jobs were
sharing one file. ``plan_ship`` re-exports ``record_plan``, so every existing
caller (``land_push``, ``land_steps``, the CLI) is unchanged.
"""

import shutil
from pathlib import Path

import plan_scope as ps
import worktree as wt

RUNTIME_LOCAL = ("LAND_NOTICE.txt", "_worktrees/")
PLANS_DIR = ps.PLANS_DIR              # ONE definition, in plan_scope
GIT_TIMEOUT = 900


def _git(args, cwd, **kw):
    return wt.git(args, cwd, timeout=GIT_TIMEOUT, **kw)

def _names(args, cwd, what):
    """Path list out of a `--name-only`-style git command, `-z` and NUL-split.

    `-z` is not decoration. Under the default `core.quotePath`, git renders a
    non-ASCII path as an escaped, DOUBLE-QUOTED token — measured:
    `_plans/plâno-2026/a.txt` comes back as `"_plans/pl\\303\\242no-2026/a.txt"`.
    Every consumer below compares against a plain `_plans/` prefix, so the
    quoting silently flipped both of them the wrong way: §8.b's refusal could
    never fire for a plan directory with a non-ASCII name, and `_assert_only`
    called a legitimate staged path a stray. `-z` disables the quoting outright
    and is the only form that survives a path containing a space or a newline.
    A git failure RAISES — an unreadable list must not read as an all-clear.
    """
    rc, out, err = _git([*args, "-z"], cwd, strip=False)
    if rc != 0:
        raise wt.WorktreeError(f"cannot read {what} in {cwd}: {err.strip()}")
    return [p for p in out.split("\0") if p]


# --------------------------------------------------------------------------
# §8.c — WHO commits the plan record, and WHEN
# --------------------------------------------------------------------------
def record_plan(target, plan_dir, message=None):
    """Copy the LIVE outer plan directory into ``target`` and commit just it.

    §8.b removes ``/commit-orchestrate``'s ``git add -A`` from this job, and a
    rule that removes the only writer of ``PLAN.html``/``run_state.json``/every
    closeout without naming a replacement leaves the plan record permanently
    uncommitted — which in THIS repo is what ``gearbox drift`` reports and what
    ``gearbox deploy`` then refuses on.

    Four properties, each load-bearing and each asserted by the tests:
      * copies the live OUTER directory, so the record is current, not frozen;
      * stages with an explicit pathspec, never ``-A``;
      * stages only the plan's OWN directory — never ``_plans_index.md``, never a
        sibling plan's (probe 3 scenario 3 measured a regenerated index resolving
        ``--theirs`` and silently deleting another plan's entry, exit 0);
      * tolerates finding the directory ALREADY committed — §8.d admits a
        schema-6 neighbour's repo-wide staging can get there first, so this is a
        no-op then, not an error.

    ``target`` is the LAND worktree in the finished protocol (§8.c, s08). It is a
    plain argument here because this function must not know which worktree it is
    in — the same call records the plan from a plan worktree in a test.
    """
    plan_dir = Path(plan_dir).resolve()
    root = wt.repo_root(plan_dir)
    if root is None:
        raise wt.WorktreeError(f"{plan_dir} is not inside a git checkout")
    rel = plan_dir.relative_to(Path(root).resolve())
    dest = Path(target) / rel
    if dest.resolve() == plan_dir:
        raise wt.WorktreeError(
            f"record-plan target {target} IS the primary checkout holding {plan_dir} — "
            "copying a directory onto itself would destroy it.")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(plan_dir, dest)
    pathspec = rel.as_posix()
    _keep_runtime_local(target, dest, pathspec)
    dropped = _drop_ignored(target, dest, pathspec)
    _git(["add", "--", pathspec], target, check=True)
    _assert_only(target, pathspec)
    if _git(["diff", "--cached", "--quiet", "--", pathspec], target)[0] == 0:
        return {"status": "already-recorded", "pathspec": pathspec, "sha": None,
                "dropped_ignored": dropped}
    msg = message or f"plan({plan_dir.name}): record"
    rc, out, err = _git(["commit", "-m", msg], target)
    if rc != 0 and wt._hooks_rewrote_the_tree(target):
        _git(["add", "--", pathspec], target, check=True)
        _assert_only(target, pathspec)
        # The rewrite can put back exactly what HEAD holds (a final newline the
        # end-of-file fixer restores): nothing is left to record, not a failure.
        if _git(["diff", "--cached", "--quiet", "--", pathspec], target)[0] == 0:
            return {"status": "already-recorded", "pathspec": pathspec, "sha": None,
                    "dropped_ignored": dropped}
        rc, out, err = _git(["commit", "-m", msg], target)
    if rc != 0:
        raise wt.WorktreeError(f"record-plan commit failed in {target}: {(err or out).strip()}")
    _, sha, _ = _git(["rev-parse", "HEAD"], target, check=True)
    names = _names(["show", "--name-only", "--format=", sha], target, sha)
    return {"status": "recorded", "pathspec": pathspec, "sha": sha, "files": names,
            "dropped_ignored": dropped}


def _keep_runtime_local(target, dest, pathspec):
    """Put every ``RUNTIME_LOCAL`` path in the copy back to ``target``'s ``HEAD``.

    R4 / finish-contract decision (a): ``LAND_NOTICE.txt`` and ``_worktrees/`` are
    local runtime, kept out of every record BY NAME. A ``.gitignore`` line is not
    enough: ``_drop_ignored`` asks the land worktree, built from origin's tree,
    which may lack it. ``rmtree`` + ``copytree`` would stage a delete, a change or
    a new file, so each path is removed from the copy and then restored from
    ``HEAD`` when ``HEAD`` tracks it. A tracked copy keeps that commit's bytes;
    an untracked one never enters the record.
    """
    for name in RUNTIME_LOCAL:
        victim = Path(dest) / name
        if victim.is_dir() and not victim.is_symlink():
            shutil.rmtree(victim)
        elif victim.exists() or victim.is_symlink():
            victim.unlink()
        rel = f"{pathspec}/{name}".rstrip("/")
        if _names(["ls-tree", "-r", "--name-only", "HEAD", "--", rel], target, rel):
            _git(["checkout", "HEAD", "--", rel], target, check=True)


def _drop_ignored(target, dest, pathspec):
    """Delete, from the copy just made, everything git IGNORES under ``pathspec``.

    §8.e's RUNTIME class — ``.lock``, ``run.ndjson``, ``run_state.json``,
    ``_closeouts/``, ``HALT_NOTICE.txt``, the five lines plan-builder writes into
    the project ``.gitignore`` — is copied by ``copytree`` and then never staged,
    so it settles in the LAND worktree as ignored content that nothing removes.
    §12.4's dirty-content refusal preserves a worktree holding any, so EVERY real
    land parked at the teardown: measured end to end 2026-08-23 with a plan that
    had actually been ``begin``-run, push and final record both green,
    ``step_finish`` returning ``land-worktree-preserved`` over four files whose
    authoritative copy was sitting in the primary checkout the copy came from.
    The fixtures missed it because a hand-built plan directory has no runtime
    files in it at all.

    Git's own ignore evaluation answers — never a re-typed copy of the pattern
    list, which would drift the moment a project adds a line. ``-z`` for the
    quoting reason ``_names`` documents. A path outside ``dest`` is refused
    rather than deleted: this removes a directory tree, and the one thing it may
    never do is walk out of the copy it just made.
    """
    rc, out, err = _git(["status", "--porcelain=v1", "-z", "-uall", "--ignored=matching",
                         "--", pathspec], target, strip=False)
    if rc != 0:
        # An empty list is what "no ignored files" returns, so returning it here
        # made a FAILED enumeration indistinguishable from a clean one: the record
        # committed with the runtime files still in it and reported
        # `dropped_ignored: []` as an all-clear, which is precisely the §12.4
        # teardown park this function exists to prevent.
        raise wt.WorktreeError(
            f"could not enumerate ignored files under {pathspec} in {target}: "
            f"git status exited {rc}. {(err or out).strip()}")
    dest = Path(dest).resolve()
    dropped = []
    for entry in out.split("\0"):
        if not entry.startswith("!! "):
            continue
        victim = (Path(target) / entry[3:]).resolve()
        if victim == dest:
            # `--ignored=matching` reports a directory that itself matches a
            # pattern INSTEAD of recursing into it, so an ignore rule covering the
            # plan's own directory arrives here as the record root. Deleting it
            # left `git add` with nothing to stage and returned `already-recorded`
            # with `sha: None` — the whole record silently gone. Refuse: the
            # pattern, not the record, is the thing to fix.
            raise wt.WorktreeError(
                f"the plan record {pathspec} is itself git-ignored in {target}, so it "
                "can never be committed. Remove the .gitignore pattern that matches it.")
        if dest not in victim.parents:
            continue
        if victim.is_dir():
            shutil.rmtree(victim)
        else:
            victim.unlink(missing_ok=True)
        dropped.append(entry[3:].rstrip("/"))
    return sorted(dropped)


def _assert_only(target, pathspec):
    """Nothing but ``pathspec`` may be STAGED when the record commit is made.

    Asserted on the INDEX, before the commit, and re-asserted after the hook
    retry re-stages. Checking the finished commit instead would be a refusal that
    fires exactly once: the retry re-copies, stages the same pathspec, finds
    nothing new to commit and returns ``already-recorded`` — success, with the
    bad commit standing. The index is rebuilt by every call, so this answer is
    the same every time until the stray staging is actually cleared.
    """
    staged = _names(["diff", "--cached", "--name-only"], target, "the index")
    stray = [n for n in staged
             if not n.startswith(pathspec + "/") and n != pathspec]
    if stray:
        raise wt.WorktreeError(
            f"record-plan found {len(stray)} staged path(s) outside {pathspec} in "
            f"{target}: {stray[:5]}. §8.c stages ONLY the plan's own directory — never "
            "`_plans_index.md`, never a sibling plan's (probe 3 scenario 3 measured a "
            "regenerated index resolving `--theirs` and silently deleting another "
            "plan's entry, exit 0). Unstage them and re-run.")
