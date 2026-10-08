"""registry.py's git-bookkeeping group: `git worktree list --porcelain -z` and
local `plan/*` branch listing. Read-only, no plan-side state.

Split out of registry.py (was 594 LOC, over the 500 file-size limit) — see
registry.py's module docstring for the full design rationale (constraint 1:
`prunable` is structurally blind to locked, plan-owned worktrees).
"""

import worktree as wt

BRANCH_PREFIX = wt.BRANCH_PREFIX  # "plan" — shared with worktree.py, not re-pinned here.


def _apply_porcelain_field(entry, key, val):
    """Mutate `entry` for one `key val` line of a worktree stanza. Extracted
    from `parse_worktree_porcelain` (gate: complexity 13, LIMIT 12) — the
    per-attribute dispatch is the branchy part; the stanza/loop shell around
    it is not."""
    if key == "worktree":
        entry["path"] = val
    elif key == "HEAD":
        entry["head"] = val
    elif key == "branch":
        entry["branch"] = val[len("refs/heads/"):] if val.startswith("refs/heads/") else val
    elif key == "detached":
        entry["detached"] = True
    elif key == "bare":
        entry["bare"] = True
    elif key == "locked":
        entry["locked"] = True
        entry["locked_reason"] = val or None
    elif key == "prunable":
        entry["prunable"] = True
        entry["prunable_reason"] = val or None


def parse_worktree_porcelain(raw):
    """Parse `git worktree list --porcelain -z` into one dict per worktree.

    NUL-separated fields, stanzas terminated by an empty field (a trailing
    double-NUL). Measured against git 2.48.1 (this repo) plus a fixture with a
    `--lock`ed and a genuinely prunable worktree: fields seen are `worktree`,
    `HEAD`, one of `branch <ref>` / `detached` / `bare`, and the optional
    boolean-with-reason attributes `locked [reason]` / `prunable [reason]`.

    Raises WorktreeError on a stanza with no `worktree` line — a shape this
    git version's porcelain format does not document, and guessing at a parse
    here is exactly the failure mode `run.py plans-status` must not have.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    out = []
    for stanza in raw.split("\x00\x00"):
        lines = [ln for ln in stanza.split("\x00") if ln]
        if not lines:
            continue
        entry = {
            "path": None, "head": None, "branch": None, "detached": False,
            "bare": False, "locked": False, "locked_reason": None,
            "prunable": False, "prunable_reason": None,
        }
        for line in lines:
            key, _, val = line.partition(" ")
            _apply_porcelain_field(entry, key, val)
        if entry["path"] is None:
            raise wt.WorktreeError(
                f"git worktree list --porcelain stanza has no `worktree` line: {lines!r} — "
                "this git version's porcelain shape deviates from the documented one; "
                "refusing to guess a parse."
            )
        out.append(entry)
    return out


def list_worktrees(root):
    rc, out, err = wt.git(["worktree", "list", "--porcelain", "-z"], root, strip=False)
    if rc != 0:
        raise wt.WorktreeError(f"git worktree list failed in {root}: {err}")
    return parse_worktree_porcelain(out)


def list_plan_branches(root):
    """Local branch names under `plan/` — the namespace shared by legacy group
    members and any future whole-plan branch. Ownership, not name shape, is
    what tells them apart (see registry.py's module docstring)."""
    rc, out, err = wt.git(
        ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{BRANCH_PREFIX}/"], root
    )
    if rc != 0:
        raise wt.WorktreeError(f"git for-each-ref failed in {root}: {err}")
    return [ln for ln in out.splitlines() if ln.strip()]
