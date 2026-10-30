"""Sensitive-path list: a session whose change touches one of these paths gets
the medium review instead of the low one, plus the adversarial review.

The list is edited BY HAND. It is not derived: s02's count (evals/review/COUNTS.md,
`sensitive: BUILD`) is what justified it. `verify.compute_gates` calls `raise_gates`.

THE GATE ID CHANGES, not a level field: the review ledger keys on the id, and a
shared level once let a raised gate reuse the low gate's record (fixed in 969feba,
see `llm_review_surface.prepare_surface`).

Unreadable -> fail closed (raised): an unreadable file set (None) is a hit; an
empty list, or a tree with no `.git` at all (nothing can ship), is not.
"""
from fnmatch import fnmatchcase
from pathlib import Path

#: Repo-root-anchored globs. `*` crosses `/`, so `hooks/*` covers nested files.
SENSITIVE = ("hooks/*", "settings.json", "githooks/*", "scripts/gearbox*",
             "model-routing.yaml", ".github/workflows/*")

#: Low review gate id -> its medium id.
RAISE = {"llm-review-low": "llm-review-medium",
         "llm-review-low-canary": "llm-review-medium-canary",
         "cross-family-review-low": "cross-family-review-medium",
         "cross-family-review-low-canary": "cross-family-review-medium-canary"}

ADVERSARIAL = "adversarial-review"


def hits(paths, patterns=SENSITIVE):
    """The changed paths that match a sensitive pattern, in input order."""
    return [p for p in paths or () if any(fnmatchcase(p, pat) for pat in patterns)]


def raise_gates(paths, gate_ids, patterns=SENSITIVE):
    """`gate_ids` unchanged on no hit (or an empty list); else each low review id
    swapped for its medium id and `adversarial-review` appended. No duplicates.
    Unreadable (`paths is None`) -> fail closed (raised)."""
    if paths is not None and not hits(paths, patterns):
        return list(gate_ids)
    out = []
    for gid in list(gate_ids) + [ADVERSARIAL]:
        gid = RAISE.get(gid, gid)
        if gid not in out:
            out.append(gid)
    return out


def session_paths(plan_dir, session_id):
    """The session's changed files, read the way its review gates read them: in
    the gate's tree, from the session's review base, inside its `review_scope`.
    None if unreadable (git failed, worktree gone): fail closed (raised). [] if
    no `.git` sits at or above the tree (checked on disk, not via git)."""
    import gate_files as gf
    import llm_review_surface as surf
    import review_context as rvs
    import shipping as shp
    try:
        cwd = shp._resolve_cwd(plan_dir, None, session_id)
    except shp.pship.wt.WorktreeError:
        return None
    here = Path(cwd).resolve()
    if not any((d / ".git").exists() for d in (here, *here.parents)):
        return []
    files = gf.changed_files(cwd, rvs.get_base(plan_dir, session_id, cwd))
    scope = rvs.get_scope(rvs.session_spec(plan_dir, session_id))
    return files and [p for p in files if surf.in_scope(p, scope)]


def effective_verify(plan_dir, session_id, vb):
    """`vb` unchanged when declared; else, on a sensitive hit, a synthesized block
    so a session with NO verify block still gets the adversarial review (apply
    marks it verify-pending). Unreadable -> fail closed (raised): None is a hit."""
    if vb:
        return vb
    paths = session_paths(plan_dir, session_id)
    if paths is not None and not hits(paths):
        return vb
    return {"gates": [ADVERSARIAL], "max_rework": 1}
