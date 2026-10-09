"""ISO-03 — WHERE a parallel group's work happens when its plan is isolated.

Authority: ``../references/parallel-group-contract.md`` §9 (contract v3,
2026-08-21) and ``../references/plan-isolation-contract.md`` §1.1/§1.1a (v1,
frozen). ``worktree.py`` is the mechanism; this module is the small set of
answers that mechanism needs once a plan-level worktree exists above it.

Split out of ``worktree.py`` rather than added to it, for the measured reason
``plan_version_gate.py`` and ``plan_hooks.py`` were split out of
``plan_worktree.py``: this repo's ratchet pins ``worktree.py`` at its exact LOC,
and bumping a baseline to make room is the answer that has twice been wrong here.

Below the version gate NOTHING here changes anything: ``plan_isolation`` returns
``(None, None)`` for every plan on disk today, and each helper then returns the
value ``worktree.py`` computed before this module existed.

THE MEMBER BRANCH NAME — flat, not nested (operator decision)
--------------------------------------------------------------------------
V3-1 first fixed an isolated plan's member branch at ``plan/<plan-slug>/<group>/
<sid>`` while the plan's own branch is ``plan/<plan-slug>``. **Git cannot hold
both.** Measured in a scratch repo, git 2.x, loose refs AND after
``git pack-refs --all``::

    $ git branch plan/myslug
    $ git branch plan/myslug/grp/s01
    fatal: cannot lock ref 'refs/heads/plan/myslug/grp/s01':
           'refs/heads/plan/myslug' exists; cannot create '...'
    rc=128
    $ git worktree add -b plan/myslug/grp/s02 <path> main
    fatal: cannot lock ref 'refs/heads/plan/myslug/grp/s02': ... rc=255

Both contracts named this ref pair a REFUSAL but scoped it to a *legacy* group
that happens to be named after a plan slug; under the nested name it was the
shape of every group of every isolated plan, so the rule refused the design it
belonged to. The operator amended V3-1 to the ``__`` separator §1.1a already
uses for the member worktree path: ``member_dirname`` is therefore the ONE
derivation behind both the checkout directory and the branch
(``worktree.member_branch`` prefixes it), so the two can never drift.

``plan_scope.member_branch`` is the resolver every caller outside ``worktree.py``
goes through. It reads the slug from the group's RECORDED state, so a group
pinned before its plan was isolated keeps the name its branch actually has.
"""

from pathlib import Path

import ship_locks as sl


def common_dir(root):
    """``$GIT_COMMON_DIR`` for ``root`` — the SHARED git admin directory.

    ``<root>/.git`` is right only for a primary checkout. In a linked worktree
    ``.git`` is a gitdir POINTER FILE (plan-isolation §12.1), so every path built
    as ``<root>/.git/<x>`` either lands nowhere or raises ``NotADirectoryError``.
    Under plan isolation that rare shape becomes the DEFAULT — the plan worktree
    IS a linked worktree — which is why this is resolved rather than assumed.
    Falls back to ``<root>/.git`` only when git cannot answer at all.
    """
    return sl._git_common_dir(root) or (Path(root) / ".git")


def exclude_worktree_dir(root, dirname):
    """Keep ``dirname/`` out of ``git status`` without touching a tracked file.

    Writes ``$GIT_COMMON_DIR/info/exclude``, which every linked worktree of the
    repository shares — so one write covers the primary checkout, the plan
    worktree and every member checkout. Before this resolved the common dir the
    write raised ``OSError`` inside any worktree and was swallowed, leaving every
    member and land checkout visible as untracked to the tree above it.
    """
    exclude = Path(common_dir(root)) / "info" / "exclude"
    entry = f"/{dirname}/"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text() if exclude.exists() else ""
        if entry not in current.splitlines():
            exclude.write_text(current + ("" if current.endswith("\n") or not current else "\n")
                               + entry + "\n")
    except OSError:
        # A repo whose admin dir is read-only. The directory then shows up as
        # untracked, which the group's baseline records — degraded, not wrong.
        pass


def worktree_admin_dir(root, path):
    """The ``$GIT_COMMON_DIR/worktrees/<name>`` registration for ``path``, matched
    by its own ``gitdir`` file CONTENT — never assumed from ``path``'s basename,
    which git de-duplicates on a name collision. None when none is registered."""
    admin_root = Path(common_dir(root)) / "worktrees"
    if not admin_root.is_dir():
        return None
    want = f"{path}/.git"
    for d in admin_root.iterdir():
        f = d / "gitdir"
        try:
            if f.is_file() and f.read_text().strip() == want:
                return d
        except OSError:
            continue
    return None


def plan_isolation(plan_dir):
    """``(plan_worktree_path, plan_slug)`` when this plan is isolated, else
    ``(None, None)``.

    Lazy import: ``plan_scope`` imports ``worktree``, which imports this module,
    so a module-level import is a cycle. ``plan_scope.plan_worktree`` is the one
    reader of the §8.d runtime claim and is version-gated, so a sub-7 plan — every
    plan on disk today — answers ``(None, None)`` here and nothing below changes.
    """
    try:
        import plan_scope as ps
        import plan_worktree as pwt
    except ImportError:                       # pragma: no cover - stdlib-only fallback
        return None, None
    tree = ps.plan_worktree(plan_dir)
    return (tree, pwt.plan_slug(plan_dir)) if tree else (None, None)


def group_roots(plan_dir, repo):
    """``(repo_root, merge_root, plan_slug)`` for a group about to start.

    * ``repo_root`` — the OUTER checkout, ALWAYS. Every ``git worktree add`` runs
      here with absolute paths: run from inside the plan worktree instead, the
      new checkout registers against the shared repo but its own ``.git`` pointer
      and the admin ``gitdir`` are written relative to a different tree.
    * ``merge_root`` — where member branches MERGE and where containment is
      measured: the PLAN WORKTREE under isolation (V3-2 §3 rules 5-7), the outer
      checkout otherwise. This is the value that stops a neighbouring plan's
      dirty files from surfacing as this group's stray writes.
    * ``plan_slug`` — None below the version gate; the member checkout's
      directory name carries it under isolation (§1.1a).
    """
    tree, slug = plan_isolation(plan_dir)
    return repo, Path(tree) if tree else Path(repo), slug


def merge_root(state, plan_dir, fallback=None):
    """The tree a group's merges land in, from its recorded state.

    ``repo_root`` is the fallback key so a group state file written before this
    existed keeps merging exactly where it always did.

    With no ``fallback`` a recorded-but-VANISHED tree is returned as it is, so
    the git command that follows fails loudly rather than quietly operating on
    the operator's checkout — the collision plan isolation exists to prevent.
    ``fallback`` is for teardown alone, which must still work once the tree is
    gone: that is the one caller for which "the worktree is missing" is the
    normal case rather than the alarming one.
    """
    root = Path(state.get("merge_root") or state.get("repo_root") or plan_dir)
    return root if (fallback is None or root.is_dir()) else Path(fallback)


def member_dirname(group, session_id, plan_slug=None):
    """The member checkout's directory name under ``<repo>/.plan-worktrees/``.

    Sub-7 (``plan_slug`` None): ``<group>/<sid>`` — byte-identical to what every
    manifest on disk uses today. Isolated: ``<slug>__<group>__<sid>``, ONE
    directory, a SIBLING of the plan worktree and never inside it (§1.1a). The
    ``__`` separators are not cosmetic: ``git worktree add`` into a path inside
    another worktree SUCCEEDS, the parent then reports the child as untracked,
    and ``git worktree remove`` on the parent fails exit 128 until someone
    reaches for ``--force`` — which §12.4 forbids.
    """
    if not plan_slug:
        return f"{group}/{session_id}"
    return f"{plan_slug}__{group}__{session_id}"


def merge_recovery(res, integration_session):
    """The recovery note for a refused integration merge. It names a
    ``reset --hard`` only when a merge may have landed, and only against the
    result's own ``merge_root``: a containment or missing-member refusal merged
    nothing, and falling back to the primary checkout would aim the reset at the
    operator's uncommitted work. The target is ``pre_merge``, the head THIS round
    started from: ``base_ref`` would also undo merges an earlier begin landed."""
    note = (f"every member branch is intact ({sorted((res.get('branches') or {}).values())}). "
            f"Resolve by hand, then re-dispatch {integration_session}.")
    if res.get("merge_root") and (res.get("merged") or res.get("status") == "conflict"):
        note += (f" To undo the merges that DID land: git -C {res['merge_root']} "
                 f"reset --hard {(res.get('pre_merge') or res.get('base_ref') or '')[:12]}")
    return note
