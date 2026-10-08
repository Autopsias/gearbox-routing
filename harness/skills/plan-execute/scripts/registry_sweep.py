"""registry.py's plans-sweep group (SWP-01): report — and, on `--apply`, tear
down — stale plan-lifecycle state left behind in a repo: dead `.lock` plans,
leftover plan worktrees, and orphaned `plan/*` branches.

Read-only by default, same discipline `registry.py` itself already has.
`--apply` acts ONLY on entries `registry_owners.py` classifies LEFTOVER (a
plan whose lifecycle is definitively stale-lock/done owns it) — an UNKNOWN or
CONFLICT entry is reported and never touched (ADR-0002: "reap only what a
live registry does not claim", never guessed at, never resolved to a
winner).

Teardown reuses `worktree.py`'s `cleanup_group` directly — the SAME refusal
semantics (a worktree holding uncommitted/untracked/ignored-created content
is preserved and reported, never force-deleted; a branch carrying unmerged
commits is never `-D`'d) apply here because this calls that function rather
than reimplementing it. `cleanup_group` itself was hardened (see
worktree.py) to close two dead ends this sweep exists to catch:

  * a `--lock`ed worktree whose directory was deleted by hand — `git worktree
    prune` refuses a locked entry BY DESIGN, so without an `unlock` first the
    registration survives forever and a later `git worktree add` at that
    path fails with "missing but locked working tree".
  * a worktree that physically MOVED along with its whole project root (a
    relocated checkout, a restored backup) — git's own admin files record
    ABSOLUTE paths on both sides, so both go stale together even though the
    worktree is intact at its unchanged RELATIVE position (measured against
    git 2.48.1); `git worktree repair` re-links it before removal.

Stale worktrees are read from the REGISTRY's own `stale` field, never a raw
`prunable` check — that field already folds in both cases: a foreign
worktree trusts git's `prunable`, an OWNED one derives staleness from the
owning plan's lifecycle, because `prunable` is structurally blind to a
locked, owned worktree (registry.py's module docstring, constraint 1). Every
worktree is reported regardless of branch prefix (a `lane/*` foreign
leftover is exactly the kind of thing an operator wants to SEE), but
`--apply` only ever calls `cleanup_group` for a `plan/*`-owned LEFTOVER
group.
"""
from pathlib import Path

import run_state_io as rsi
import worktree as wt
from registry_git import list_plan_branches, list_worktrees
from registry_owners import branch_owners, classify_branches, classify_worktrees
from registry_plans import classify_plan, discover_plans


def collect_sweep(repo_root, plans_root):
    """Read-only snapshot: stale-lock plans, stale worktrees (any branch
    prefix), and every non-active `plan/*` branch (leftover/unknown/conflict
    — an ACTIVE branch is never sweep-worthy, so it is left out of the
    report entirely rather than shown and never actioned)."""
    errors = {}
    plans = [classify_plan(d, errors) for d in discover_plans(plans_root)]
    lifecycle_by_name = {p["plan"]: p["lifecycle"] for p in plans}
    owners = branch_owners(plans_root, errors)
    branches = classify_branches(list_plan_branches(repo_root), owners, lifecycle_by_name)
    worktrees = classify_worktrees(list_worktrees(repo_root), owners, lifecycle_by_name)
    return {
        "repo_root": str(repo_root),
        "stale_lock_plans": [p for p in plans if p["lifecycle"] == "stale-lock"],
        "worktrees": [w for w in worktrees if w["stale"]],
        "branches": [b for b in branches if b["class"] != "active"],
        "errors": errors,
    }


def _leftover_groups(data):
    """(plan_dir, group) pairs owning at least one LEFTOVER branch or
    worktree — the exact unit `wt.cleanup_group` tears down together. UNKNOWN
    and CONFLICT entries never contribute a pair: nothing here ever resolves
    a CONFLICT to a winner or acts on a branch/worktree with no identifiable
    owner."""
    pairs = set()
    for b in data["branches"]:
        if b["class"] == "leftover":
            o = b["owner"]
            pairs.add((o["plan_dir"], o["group"]))
    for w in data["worktrees"]:
        if w.get("class") == "leftover" and w.get("owner"):
            o = w["owner"]
            pairs.add((o["plan_dir"], o["group"]))
    return pairs


def apply_sweep(repo_root, plans_root, data=None):
    """Tear down every LEFTOVER-owned group and every genuinely dead lock.

    Re-verifies each stale-lock plan's lifecycle immediately before deleting
    its `.lock` — closes the window between `collect_sweep`'s snapshot and
    this call, in case a resumed `begin` acquired it in between (abort
    condition: never force a mutation past a check that has since gone
    stale)."""
    data = data or collect_sweep(repo_root, plans_root)
    locks_removed, lock_errors = [], []
    for p in data["stale_lock_plans"]:
        plan_dir = Path(p["plan_dir"])
        fresh = classify_plan(plan_dir)
        if fresh["lifecycle"] != "stale-lock":
            continue
        lock_path = plan_dir / ".lock"
        try:
            lock_path.unlink()
        except OSError as e:
            lock_errors.append({"plan_dir": str(plan_dir), "error": str(e)})
            continue
        locks_removed.append(str(plan_dir))
        rsi.log_event(plan_dir, "sweep_lock_removed",
                      pid=(p["lock"] or {}).get("pid"), age_s=(p["lock"] or {}).get("age_s"))

    groups_cleaned = []
    for plan_dir, group in sorted(_leftover_groups(data)):
        res = wt.cleanup_group(Path(plan_dir), group)
        groups_cleaned.append({"plan_dir": plan_dir, "group": group, "result": res})

    return {
        "repo_root": str(repo_root),
        "locks_removed": locks_removed,
        "lock_errors": lock_errors,
        "groups_cleaned": groups_cleaned,
        "skipped_unknown": [b["branch"] for b in data["branches"] if b["class"] == "unknown"],
        "skipped_conflict": [b["branch"] for b in data["branches"] if b["class"] == "conflict"],
    }


def render_sweep_table(data):
    lines = [f"repo: {data['repo_root']}", "", "STALE-LOCK PLANS"]
    lines += [
        f"  {p['plan_dir']:<60} pid={(p['lock'] or {}).get('pid')} "
        f"age={int(a) if (a := (p['lock'] or {}).get('age_s')) is not None else '?'}s"
        for p in data["stale_lock_plans"]
    ] or ["  (none)"]

    lines += ["", "STALE WORKTREES (any branch prefix — --apply only ever acts on plan/*)"]
    for w in data["worktrees"]:
        owner = w.get("owner")
        owner_str = f"{owner['plan']}/{owner['group']}" if owner else "foreign"
        lines.append(f"  {w['path']:<60} class={(w.get('class') or '-'):<10} owner={owner_str}")
    if not data["worktrees"]:
        lines.append("  (none)")

    lines += ["", "ORPHAN plan/* BRANCHES (leftover -> --apply removes; unknown/conflict -> never)"]
    lines += [f"  {b['branch']:<40} class={b['class']}" for b in data["branches"]] or ["  (none)"]
    return "\n".join(lines)


def render_apply_result(result):
    lines = [f"repo: {result['repo_root']}", "", "LOCKS REMOVED"]
    lines += [f"  {p}" for p in result["locks_removed"]] or ["  (none)"]
    if result["lock_errors"]:
        lines += ["", "LOCK ERRORS"]
        lines += [f"  {e['plan_dir']}: {e['error']}" for e in result["lock_errors"]]

    lines += ["", "GROUPS CLEANED"]
    for g in result["groups_cleaned"]:
        r = g["result"]
        lines.append(
            f"  {g['plan_dir']} / {g['group']}: removed={r.get('removed')} "
            f"recovered={r.get('recovered')} "
            f"preserved={[p['session'] for p in r.get('preserved', [])]} "
            f"branches_kept={[b['branch'] for b in r.get('branches_kept', [])]}"
        )
    if not result["groups_cleaned"]:
        lines.append("  (none)")

    lines += ["", "SKIPPED (unknown — never guessed at)"]
    lines += [f"  {b}" for b in result["skipped_unknown"]] or ["  (none)"]
    lines += ["", "SKIPPED (conflict — never resolved to a winner)"]
    lines += [f"  {b}" for b in result["skipped_conflict"]] or ["  (none)"]
    return "\n".join(lines)
