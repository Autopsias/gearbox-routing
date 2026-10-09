"""REG-01 — `plans-status`: which plans are live in this repo right now, which
are dead, and what stale branches/worktrees they left behind.

Read-only. Everything here comes from git's own bookkeeping (`git worktree
list --porcelain`, local `plan/` branches) plus the plan-side state this repo
already writes (`.lock` pidfiles via run_state_io, `_worktrees/<group>.json`
via worktree.py). Nothing is stored here that could drift, and nothing here
reclaims or deletes anything — see `cleanup_group` in worktree.py for that.

This module is the ENTRY POINT (assembly + output: `collect`, `render_table`,
the CLI-facing `cmd_plans_status`); everything else is split into three
sibling modules along the seams this file used to have as comment banners
(was 594 LOC, over the file-size limit) and re-exported here so `reg.X` still
resolves for every existing caller:

  * `registry_git.py`    — git bookkeeping: worktree porcelain parsing,
                           `plan/*` branch listing.
  * `registry_plans.py`  — plan discovery, `.lock` reads, lifecycle
                           classification.
  * `registry_owners.py` — branch/worktree ownership + classification.
  * `registry_guard.py`  — the `_guarded`/`_DEGRADE_ERRORS` degrade-and-record
                           helper shared by `registry_plans` and
                           `registry_owners` (kept out of both to avoid a
                           circular import with this file).

Two measured constraints shape the design (see the s01 session brief for the
full derivation):

1. **`prunable` is structurally blind to the worktrees this plan machinery
   owns.** `git worktree prune` skips locked worktrees by design, and every
   worktree the S05 whole-plan mechanism creates is `--lock`ed. So `prunable`
   is trustworthy only for FOREIGN (non-plan) worktrees; an owned worktree's
   staleness is derived from its owning plan's lock record instead.
2. **PID liveness is not plan liveness.** `run.py` is a short-lived CLI —
   `begin` acquires the lock with its own pid and exits while the dispatched
   session runs for minutes, so `lock_holder` reads a dead pid for the entire
   wall-clock duration of a live session. A plan is ACTIVE when its recorded
   lifecycle (session statuses via `dispatch.next_action`) is non-terminal,
   regardless of pid. STALE-LOCK is a narrower, additional signal: non-terminal
   AND the lock is both dead-pid AND older than the stale threshold — reported,
   never reclaimed.

Branch AND worktree ownership (ADR-0002 precedent — "reap only what a live
registry does not claim"): the `plan/` branch namespace is shared between
legacy parallel-group members (`plan/<group>/<sid>`, worktree.py's
BRANCH_PREFIX) and any future whole-plan branch (`plan/<slug>`, S05); the
`.plan-worktrees/<group>/<sid>` path shape carries the same ambiguity — name
(or path) shape never tells owners apart — every branch and every worktree is
mapped to an owner by reading the plan-side state files, and anything
unmappable is reported UNKNOWN, never guessed at. Ownership itself is keyed by
(plan, branch) or (plan, path), not name/path alone: two plans can
legitimately declare the same `parallel_group` (verified in this repo —
`pg-prep` in both `example-plan-a-2026-07-27` and `example-plan-b-2026-07-27`),
which collides on both the same `plan/<group>/<sid>` branch name AND the same
`.plan-worktrees/<group>/<sid>` path. A branch or worktree claimed by more
than one distinct plan is reported CONFLICT — never resolved to a single
owner, never reapable — see `registry_owners.branch_owners`/`classify_branches`
and `classify_worktrees`.
"""

import json
from pathlib import Path

from registry_git import (  # noqa: F401 — re-exported for existing callers
    BRANCH_PREFIX,
    list_plan_branches,
    list_worktrees,
    parse_worktree_porcelain,
)
from registry_owners import (  # noqa: F401 — re-exported for existing callers
    branch_owners,
    classify_branches,
    classify_worktrees,
)
from registry_plans import (  # noqa: F401 — re-exported for existing callers
    classify_plan,
    discover_plans,
    lock_info,
    plan_lifecycle_active,
)
from registry_sweep import (  # noqa: F401 — re-exported for existing callers
    apply_sweep,
    collect_sweep,
    render_apply_result,
    render_sweep_table,
)
import worktree as wt


def default_repo_root(start=None):
    return wt.repo_root(start or Path.cwd())


def default_plans_root(repo_root):
    return Path(repo_root) / "_plans"


def collect(repo_root, plans_root):
    """The full registry: plans, branches, worktrees — each already classified.

    `errors`: plan name -> list of degraded-read messages (see
    `registry_guard._guarded`). Never empty-vs-missing ambiguity for a plan
    already in `plans` — every plan row also carries its own `errors` list
    (usually `[]`) so a degradation is visible right next to the row it
    affects, not only in a side table an operator has to cross-reference.
    """
    errors = {}
    plans = [classify_plan(d, errors) for d in discover_plans(plans_root)]
    plan_lifecycle_by_name = {p["plan"]: p["lifecycle"] for p in plans}
    owners = branch_owners(plans_root, errors)
    branches = classify_branches(list_plan_branches(repo_root), owners, plan_lifecycle_by_name)
    worktrees = classify_worktrees(list_worktrees(repo_root), owners, plan_lifecycle_by_name)
    for p in plans:
        p["errors"] = errors.get(p["plan"], [])
    return {"repo_root": str(repo_root), "plans": plans, "branches": branches,
            "worktrees": worktrees, "errors": errors}


def other_active_plans(plan_dir, repo_root=None, plans_root=None):
    """REG-02 — registry-ACTIVE plans in `plan_dir`'s repo, other than
    `plan_dir` itself.

    `[]` when `plan_dir` is not (or no longer) inside a resolvable git repo:
    there is no registry to scan, and `begin`'s guard must stay a no-op for
    the many existing test fixtures (and any real use) that build a plan
    under a bare, non-`git init`ed directory. `active` (not `stale-lock`) is
    the bar: a stale-lock plan's own `begin` already exited, so a second
    `begin` there is exactly the resume path, not a collision.

    A plan that has NEVER BEGUN is excluded too (`has_begun` — see
    `registry_plans`). The subject here is "who else is dispatching into this
    tree right now", and a plan nobody has started is not dispatching into
    anything. Counting it made every drafted-but-unrun plan a permanent
    blocker: seven had accumulated by 2026-08-23, so every new plan needed
    `--concurrent`, which is how a real control becomes a reflex.

    This does NOT open the race it guards. Becoming active requires a `begin`,
    and `begin` runs this same check — so of two never-begun plans, whichever
    starts first writes `run.ndjson` and blocks the second. The protection is
    preserved in both orders; only the phantom is dropped.
    """
    root = repo_root or default_repo_root(plan_dir)
    if root is None:
        return []
    proot = plans_root or default_plans_root(root)
    data = collect(root, proot)
    this = str(Path(plan_dir).resolve())
    return [
        p for p in data["plans"]
        if p["lifecycle"] == "active" and p.get("has_begun", True)
        and str(Path(p["plan_dir"]).resolve()) != this
    ]


# --------------------------------------------------------------------------
# render_table row helpers — extracted (gate: complexity 16, LIMIT 12): each
# helper below owns exactly one row's marker/flag assembly, so render_table
# itself is left as a plain "for row: build one line" loop per section.
# --------------------------------------------------------------------------
def _plan_lock_str(lock):
    if not lock:
        return "no-lock"
    if lock.get("age_s") is not None:
        return f"pid={lock['pid']} alive={lock['alive']} age={int(lock['age_s'])}s"
    return f"pid={lock['pid']} alive={lock['alive']}"


def _plan_marker(p):
    marker = "  [UNREADABLE — see ERRORS]" if p.get("errors") else ""
    marker += "  [BLOCKED — needs attention]" if p.get("needs_attention") else ""
    return marker


def _branch_owner_str(b):
    if b["class"] == "conflict":
        claimants = ", ".join(sorted({o["plan"] for o in b.get("owners", [])}))
        return f"CONFLICT: {claimants}"
    owner = b["owner"]
    return "-" if not owner else f"{owner['plan']} ({owner.get('group') or owner.get('session')})"


def _worktree_owner_str(w):
    if w.get("class") == "conflict":
        claimants = ", ".join(sorted({o["plan"] for o in w.get("owners", [])}))
        return f"CONFLICT: {claimants}"
    if w["owner"]:
        return w["owner"]["plan"]
    return "foreign" if not w["owned"] else "-"


def _worktree_flags(w):
    flags = []
    if w["locked"]:
        flags.append("locked")
    if w["prunable"]:
        flags.append("prunable")
    if w.get("class") == "conflict":
        flags.append("CONFLICT")
    elif w["stale"]:
        flags.append("STALE")
    return flags


def render_table(data):
    lines = [f"repo: {data['repo_root']}", "", "PLANS"]
    if not data["plans"]:
        lines.append("  (none found)")
    for p in data["plans"]:
        lock_str = _plan_lock_str(p["lock"])
        lines.append(f"  {p['plan']:<40} {p['lifecycle']:<12} {lock_str}{_plan_marker(p)}")

    lines += ["", f"BRANCHES ({BRANCH_PREFIX}/*)"]
    if not data["branches"]:
        lines.append("  (none found)")
    for b in data["branches"]:
        lines.append(f"  {b['branch']:<40} {b['class']:<10} owner={_branch_owner_str(b)}")

    lines += ["", "WORKTREES"]
    if not data["worktrees"]:
        lines.append("  (none found)")
    for w in data["worktrees"]:
        branch_str = w["branch"] or ("(bare)" if w["bare"] else "(detached)")
        lines.append(
            f"  {w['path']:<60} {branch_str:<30} "
            f"{','.join(_worktree_flags(w)) or '-':<20} owner={_worktree_owner_str(w)}"
        )

    errors = data.get("errors") or {}
    if errors:
        lines += ["", "ERRORS (degraded to unknown — never treated as absence)"]
        for name, msgs in errors.items():
            for msg in msgs:
                lines.append(f"  {name}: {msg}")
    return "\n".join(lines)


def cmd_plans_status(as_json=False):
    """REG-01 — read-only registry across every plan under this repo's
    `_plans/`. See this module's docstring for the classification rules.
    Never mutates anything. The CLI-facing body of run.py's `plans-status`
    subcommand — kept here (not run.py) so a growing feature doesn't grow
    run.py's own file-size ratchet."""
    root = default_repo_root()
    if root is None:
        raise SystemExit("plans-status: not inside a git repository")
    data = collect(root, default_plans_root(root))
    print(json.dumps(data, indent=2, ensure_ascii=False) if as_json else render_table(data))


def add_plans_status_parser(sub):
    """Register run.py's `plans-status` subparser. Kept here alongside
    `cmd_plans_status` for the same reason."""
    s = sub.add_parser(
        "plans-status",
        help="REG-01: read-only registry of every plan's lifecycle + its "
        "plan/* branches + git worktrees, joined and classified. No plan_dir "
        "— scans this repo's whole _plans/.",
    )
    s.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def cmd_plans_sweep(apply_changes=False, as_json=False):
    """SWP-01 — `plans-sweep`: report (and, with `--apply`, remove) stale
    plan-lifecycle state across this repo's whole `_plans/`. See
    `registry_sweep.py` for the classification/teardown rules. Read-only by
    default. Never mutates on a plain run — same discipline `plans-status`
    already has."""
    root = default_repo_root()
    if root is None:
        raise SystemExit("plans-sweep: not inside a git repository")
    plans_root = default_plans_root(root)
    data = collect_sweep(root, plans_root)
    if apply_changes:
        result = apply_sweep(root, plans_root, data=data)
        print(json.dumps(result, indent=2, ensure_ascii=False) if as_json
              else render_apply_result(result))
    else:
        print(json.dumps(data, indent=2, ensure_ascii=False) if as_json
              else render_sweep_table(data))


def add_plans_sweep_parser(sub):
    """Register run.py's `plans-sweep` subparser. Kept here alongside
    `cmd_plans_sweep` for the same reason `plans-status` is."""
    s = sub.add_parser(
        "plans-sweep",
        help="SWP-01: report (read-only by default) dead .lock plans, stale plan "
        "worktrees (any branch prefix) and orphan plan/* branches across this "
        "repo's whole _plans/. --apply removes only LEFTOVER-classified state; "
        "UNKNOWN and CONFLICT entries are always printed and never touched.",
    )
    s.add_argument(
        "--apply", action="store_true",
        help="Remove dead locks and LEFTOVER worktrees/branches. Without it, "
        "plans-sweep only reports.",
    )
    s.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
