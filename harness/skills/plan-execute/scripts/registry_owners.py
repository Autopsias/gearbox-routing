"""registry.py's ownership + classification group: mapping `plan/*` branches
and `git worktree`s to the plan that owns them, and classifying both
ACTIVE/LEFTOVER/UNKNOWN/CONFLICT.

Split out of registry.py (was 594 LOC, over the 500 file-size limit) — see
registry.py's module docstring for the full design rationale (ADR-0002:
"reap only what a live registry does not claim").
"""

from pathlib import Path

import ship_state_io as ssio
from registry_guard import _guarded

# --------------------------------------------------------------------------
# Branch ownership (ADR-0002: reap only what a live registry does not claim)
# --------------------------------------------------------------------------
def _group_state_files(plan_dir):
    d = Path(plan_dir) / "_worktrees"
    if not d.is_dir():
        return []
    return sorted(d.glob("*.json"))


def _plan_dirs_raw(plans_root):
    """Every subdirectory of `_plans/`, manifest.json or not.

    Deliberately NOT `registry_plans.discover_plans` (which requires a
    readable manifest): a `_worktrees/<group>.json` ownership record is its
    own evidence that a directory once was — or still is — a plan, and a
    corrupted or not-yet-written manifest must not make that plan's
    worktrees/branches vanish into UNKNOWN. Ownership lookup and "is this a
    buildable plan" are different questions.
    """
    d = Path(plans_root)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_dir())


def _owners_from_state_file(f, plan_dir):
    """One `_worktrees/<group>.json` -> {branch: owner record}. Raises (for
    `_guarded` to catch) on a corrupt file with no usable `.bak`
    (`ssio.ShipStateError`) or on JSON that parsed but isn't the expected
    object shape. A malformed individual MEMBER entry inside an otherwise
    good file is not an error worth flagging — it just yields no owner for
    that one branch, same as any other UNKNOWN branch (ADR-0002)."""
    state = ssio.read_json_with_bak(f)
    if state is None:
        return {}
    if not isinstance(state, dict):
        raise ValueError(f"{f} does not contain a JSON object")
    group = state.get("group") or f.stem
    members = state.get("members")
    if not isinstance(members, dict):
        members = {}
    out = {}
    for sid, entry in members.items():
        if not isinstance(entry, dict):
            continue
        branch = entry.get("branch")
        if not branch:
            continue
        out[branch] = {
            "plan": plan_dir.name, "plan_dir": str(plan_dir),
            "group": group, "session": sid, "path": entry.get("path"),
        }
    return out


def _group_owners_by_plan(pairs):
    """`pairs`: iterable of (key, owner_record). Returns key -> list of owner
    records, deduped so the SAME plan claiming a key from more than one
    source counts once — the same plan claiming a branch (or, per the
    worktree-path fix below, a resolved worktree path) via more than one
    group-state file is not a collision. More than one DISTINCT plan
    claiming the same key IS the collision, left for the caller to report
    as CONFLICT rather than resolved to a single winner here. Shared by
    `branch_owners` and `classify_worktrees`' `_owners_by_path` — one
    dedup idiom for both, not two that can drift apart."""
    by_key_plan = {}
    for key, owner in pairs:
        by_key_plan.setdefault(key, {})[owner["plan"]] = owner
    return {key: list(by_plan.values()) for key, by_plan in by_key_plan.items()}


def branch_owners(plans_root, errors=None):
    """branch name -> list of owner records, scanned across every plan's
    `_worktrees/<group>.json` (legacy parallel-group members —
    `plan/<group>/<sid>`, worktree.py's `member_branch`). Shaped so a future
    `_plan_worktree.json` source (S05's whole-plan branch) is one more
    state-file glob to add here, not a rewrite of the lookup contract — that
    file does not exist yet, so it is not read speculatively.

    Keyed by (plan, branch), NOT branch name alone (HIGH defect, verified
    against this repo: `pg-prep` is declared as a `parallel_group` in BOTH
    `_plans/gearbox-dyno-v3-2026-07-27` and `_plans/gearbox-dyno-v4-2026-07-27`,
    which both produce the branch `plan/pg-prep/<sid>` via `member_branch`).
    Two plans sharing a branch name used to merge via `owners.update(partial)`,
    so the last plan scanned silently won ownership — if the loser was LIVE
    and the winner was DONE, its branch fell through to LEFTOVER, the exact
    false-reap signal ADR-0002 exists to prevent. A branch is now returned as
    a LIST of owner records (deduped by plan via `_group_owners_by_plan`); a
    list with more than one DISTINCT plan is the collision itself, surfaced by
    `classify_branches` as a fourth `conflict` class, never resolved here.

    `errors` (optional, mutated in place): plan name -> list of "what went
    wrong" strings for any `_worktrees/*.json` this plan owns that could not
    be read at all (gate llm-review-low, attempt 2 — a single plan's
    truncated/corrupt group-state file with no `.bak` used to raise
    `ShipStateError` straight out of this function and abort `plans-status`
    for every OTHER plan too; now it degrades that one plan's ownership to
    unknown and keeps going).
    """
    if errors is None:
        errors = {}
    pairs = []
    for plan_dir in _plan_dirs_raw(plans_root):
        for f in _group_state_files(plan_dir):
            partial = _guarded(
                errors, plan_dir.name, f"_worktrees/{f.name}",
                lambda f=f, pd=plan_dir: _owners_from_state_file(f, pd),
            )
            if not partial:
                continue
            pairs.extend(partial.items())
    return _group_owners_by_plan(pairs)


def classify_branches(branches, owners, plan_lifecycle_by_name):
    """FOUR classes, never three (ADR-0002 plus the cross-plan collision
    defect this repo already has — see `branch_owners`): ACTIVE (owner's
    plan is active, OR the owner's plan lifecycle could not be determined),
    LEFTOVER (owner found, owner's plan lifecycle is definitively
    stale-lock/done), UNKNOWN (no owner record anywhere — a question, never
    acted on), CONFLICT (more than one DISTINCT plan claims this branch name
    — e.g. two plans that both declared the same `parallel_group`). CONFLICT
    is never reapable and is never silently resolved to a single owner,
    regardless of what either claimant's lifecycle is — that resolution is
    exactly the bug (last plan scanned wins) this class exists to stop.

    LEFTOVER requires a POSITIVE, definitive stale-lock/done determination —
    the same safe default `classify_worktrees` uses for its `stale` flag.
    An owner whose plan lifecycle is "unknown" (missing/corrupt manifest.json,
    see `registry_plans.plan_lifecycle_active`) must never fall through to
    LEFTOVER: that would make a plan's branches reapable exactly when we are
    least sure about them.
    """
    out = []
    for name in branches:
        owner_list = owners.get(name) or []
        if not owner_list:
            out.append({"branch": name, "class": "unknown", "owner": None})
            continue
        distinct_plans = {o["plan"] for o in owner_list}
        if len(distinct_plans) > 1:
            out.append({"branch": name, "class": "conflict", "owner": None, "owners": owner_list})
            continue
        owner = owner_list[0]
        cls = "leftover" if plan_lifecycle_by_name.get(owner["plan"]) in ("stale-lock", "done") else "active"
        out.append({"branch": name, "class": cls, "owner": owner})
    return out


# --------------------------------------------------------------------------
# Worktree staleness (constraint 1 — prunable is blind to owned worktrees)
# --------------------------------------------------------------------------
def _resolved(path):
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


def _owners_by_path(owners):
    """The same (plan, path) collision guard `branch_owners` already has for
    branch names, applied to resolved worktree paths.

    Rework fix: `worktree.py`'s `.plan-worktrees/<group>/<sid>` path shape
    carries no plan identity, exactly like the `plan/<group>/<sid>` branch
    name — verified live in this repo: `pg-prep`/s01/s02 are declared as a
    `parallel_group` in BOTH `_plans/gearbox-dyno-v3-2026-07-27` and
    `_plans/gearbox-dyno-v4-2026-07-27`, sharing project_root, group, AND
    session id, which resolves to the identical worktree path under both. A
    dict comprehension used to collapse that straight to one owner
    (last-plan-scanned wins), so a DONE plan could mark a LIVE plan's
    worktree stale. `_group_owners_by_plan` — the same dedup-by-plan idiom
    `branch_owners` already uses — surfaces the collision as more than one
    distinct plan per path instead, for `_classify_one_worktree` to report
    as CONFLICT rather than resolve to a winner."""
    pairs = (
        (_resolved(o["path"]), o)
        for owner_list in owners.values() for o in owner_list if o.get("path")
    )
    return _group_owners_by_plan(pairs)


def _classify_one_worktree(wtree, owners_by_path, plan_lifecycle_by_name):
    row = dict(wtree)
    owner_list = owners_by_path.get(_resolved(wtree["path"])) if wtree["path"] else None
    if not owner_list:
        row["owned"] = False
        row["owner"] = None
        row["class"] = "foreign"
        # Foreign (non-plan) worktree: git's own `prunable` is trustworthy here.
        row["stale"] = wtree["prunable"]
        return row
    distinct_plans = {o["plan"] for o in owner_list}
    if len(distinct_plans) > 1:
        # More than one plan claims this resolved path — the worktree-side
        # mirror of a branch CONFLICT. Never reapable, never resolved to a
        # single winner (see `_owners_by_path`).
        row["owned"] = True
        row["owner"] = None
        row["owners"] = owner_list
        row["class"] = "conflict"
        row["stale"] = False
        return row
    owner = owner_list[0]
    # Owned worktree: `prunable` is structurally blind once it is locked
    # (constraint 1), so staleness comes from the owning plan's record.
    stale = plan_lifecycle_by_name.get(owner["plan"]) in ("stale-lock", "done")
    row["owned"] = True
    row["owner"] = owner
    row["class"] = "leftover" if stale else "active"
    row["stale"] = stale
    return row


def classify_worktrees(worktrees, owners, plan_lifecycle_by_name):
    owners_by_path = _owners_by_path(owners)
    return [_classify_one_worktree(w, owners_by_path, plan_lifecycle_by_name) for w in worktrees]
