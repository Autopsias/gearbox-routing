"""Orchestrator-managed git worktree isolation for a `parallel_group`.

The contract this implements: ``../references/parallel-group-contract.md``
(contract v1, FROZEN by plan session S06). Read it first — this module is its
mechanism, not its source of truth. The verdict fixes the mechanism by name:

    "A parallel group MAY declare worktree isolation. The mechanism is
    orchestrator-managed `git worktree add` — never the Agent tool's
    `isolation: "worktree"`, which is barred by name."

S02 measured the barred mechanism silently destroying real on-disk agent output
on its first zero-contention attempt. Nothing here ever passes ``isolation`` to
the Agent tool, and nothing here ever removes a worktree implicitly.

WHAT THIS MODULE GUARANTEES, and what it does not:

* Each member gets its own linked worktree, branched from ONE PINNED base sha
  captured when the group starts — never from whatever ``HEAD`` happens to be
  later. S02 §4 measured ``git worktree add -b`` with no start-point basing on
  local ``HEAD`` including unpushed commits, so the start-point is always passed
  explicitly and then VERIFIED with ``rev-parse`` before the worktree is used.
* Worktree creation is STAGGERED: members are created one at a time in a loop
  with an explicit inter-creation delay, never fired simultaneously.
* NO MEMBER EVER COMMITS (contract M1). The orchestrator commits each member's
  worktree at ``apply`` time, one session at a time, so the concurrent-committer
  contention the capability ledger leaves OPEN is never entered.
  ponytail: serial because `apply` is serial; if closeouts are ever applied
  concurrently this needs a real lock around `commit_member`.
* Cleanup is EXPLICIT and never implicit. ``cleanup_group`` refuses any worktree
  holding uncommitted, untracked or ignored-but-locally-created content, and
  never force-deletes a branch (``git branch -d``, never ``-D``), because
  imitating the S02 §6 shape is the one thing orchestrator-managed cleanup must
  not do.
* Containment stays ADVISORY (contract §Verdict): a member is TOLD its worktree
  in its prompt; nothing in the harness confines it there. ``containment_report``
  is how that assumption gets checked instead of trusted.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import parallel_contract as pc
import run_state_io as rsi
import ship_state_io as ssio

# Contract §4 "Retry is per member, in its own worktree" — the worktree and
# branch are addressed by (group, session), so a re-dispatch reuses both.
WORKTREE_DIRNAME = ".plan-worktrees"
BRANCH_PREFIX = "plan"

# Inter-creation delay. Creation is already serial (one `git worktree add` at a
# time); this is the explicit stagger on top, so two creations never land in the
# same git config/index window even on a slow filesystem.
STAGGER_MS_ENV = "PLAN_WORKTREE_STAGGER_MS"
DEFAULT_STAGGER_MS = 250

GIT_TIMEOUT = 300

# Tool caches that do NOT count as "content worth preserving" at cleanup time.
#
# Deliberately tiny and closed. The hazard cleanup exists to avoid is S02 §6 —
# real agent OUTPUT living in a git-invisible path and being destroyed — and a
# `__pycache__` a verify gate just created is not that. Without this list every
# cleanup on every Python project refuses, which trains an operator to reach for
# `--force` reflexively, and a reflexive `--force` destroys the real thing the
# check was built for. Nothing here is a dependency tree or a build output
# directory: `node_modules/`, `dist/`, `build/` and friends stay on the
# preserve-and-report side on purpose.
CACHE_DIRS = frozenset((
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
))
CACHE_SUFFIXES = (".pyc", ".pyo")
CACHE_NAMES = frozenset((".DS_Store",))


class WorktreeError(RuntimeError):
    """A git operation the caller must surface, never swallow."""


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------
def git(args, cwd, *, check=False, strip=True, timeout=GIT_TIMEOUT):
    """Run one git command, ``shell=False``. Returns (rc, stdout, stderr).

    ``strip=False`` matters for ``status --porcelain``, whose first two columns
    are the status code and may legitimately be blank (`` M path``). Stripping
    stdout shifts that line left by one and every path parsed out of it loses its
    first character — which is exactly how the containment check silently stopped
    matching declared paths the first time this was written.
    """
    p = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )
    if check and p.returncode != 0:
        raise WorktreeError(
            f"git {' '.join(args)} failed in {cwd} (rc={p.returncode}): "
            f"{(p.stderr or p.stdout).strip()}"
        )
    out = p.stdout.strip() if strip else p.stdout
    return p.returncode, out, p.stderr.strip()


def repo_root(start):
    """The git top-level containing ``start``, or None when there is no repo."""
    rc, out, _ = git(["rev-parse", "--show-toplevel"], Path(start))
    return Path(out) if rc == 0 and out else None


def _porcelain(cwd, *, ignored=False):
    """``git status --porcelain`` lines. ``ignored`` adds files a .gitignore hides.

    A fresh worktree is a tracked-files-only checkout (S02 §5), so ANY ignored
    file inside one was produced during the session — which is precisely the
    git-invisible output S02 §6 watched get destroyed. That is why the cleanup
    safety check looks at ignored files and the ordinary dirty check does not.
    """
    args = ["status", "--porcelain"]
    if ignored:
        args.append("--ignored=matching")
    rc, out, err = git(args, cwd, strip=False)
    if rc != 0:
        raise WorktreeError(f"git status failed in {cwd}: {err}")
    return [ln for ln in out.splitlines() if ln.strip()]


def _is_cache(path):
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if any(p in CACHE_DIRS for p in parts):
        return True
    name = parts[-1] if parts else ""
    return name in CACHE_NAMES or name.endswith(CACHE_SUFFIXES)


def _status_paths(lines):
    """Paths out of porcelain lines, rename arrows resolved to the destination."""
    out = []
    for ln in lines:
        path = ln[3:].strip() if len(ln) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        # `git status` reports a wholly-untracked directory as `dir/`. Drop the
        # trailing slash so a directory compares as a prefix of the paths under
        # it (`src` vs `src/alpha.py`) rather than missing them entirely.
        out.append(path.strip('"').rstrip("/"))
    return [p for p in out if p]


# --------------------------------------------------------------------------
# Manifest reading (isolation is a GROUP property — contract §1)
# --------------------------------------------------------------------------
def group_of(session):
    pg = (session.get("dispatch") or {}).get("parallel_group")
    return pg if isinstance(pg, str) and pg.strip() else None


def is_isolated_member(session):
    d = session.get("dispatch") or {}
    return bool(group_of(session)) and d.get("isolation") == pc.ISOLATION_WORKTREE


def integrates_group(session):
    ig = (session.get("dispatch") or {}).get("integrates_group")
    return ig if isinstance(ig, str) and ig.strip() else None


def group_members(manifest, group):
    """Members in MANIFEST DOCUMENT ORDER — the producer-first merge order.

    R1 forbids asymmetric deps inside a group, so members carry no dependency
    relation to each other and document order is the only ordering the plan
    author actually declares. Recorded into the state file at merge time so the
    order that ran is auditable rather than re-derived later.
    """
    return [s for s in manifest.get("sessions") or [] if group_of(s) == group]


def isolated_group(manifest, group):
    members = group_members(manifest, group)
    return bool(members) and all(is_isolated_member(s) for s in members)


def member_touches(manifest, session_id):
    """Declared written paths for one member (contract M2a makes this non-empty)."""
    by_item = {
        it.get("id"): it.get("touches") for it in (manifest.get("items") or []) if it.get("id")
    }
    for s in manifest.get("sessions") or []:
        if s["id"] == session_id:
            return sorted(
                {p for i in (s.get("items") or []) for p in pc._touch_paths(by_item.get(i))}
            )
    return []


# --------------------------------------------------------------------------
# Group state
# --------------------------------------------------------------------------
def state_path(plan_dir, group):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in group)
    return Path(plan_dir) / "_worktrees" / f"{safe}.json"


def load_state(plan_dir, group):
    return ssio.read_json_with_bak(state_path(plan_dir, group))


def _save(plan_dir, group, state):
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ssio.durable_write_json(state_path(plan_dir, group), state)
    return state


def _exclude_worktree_dir(root):
    """Keep `.plan-worktrees/` out of `git status` without touching a tracked file."""
    exclude = Path(root) / ".git" / "info" / "exclude"
    entry = f"/{WORKTREE_DIRNAME}/"
    try:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text() if exclude.exists() else ""
        if entry not in current.splitlines():
            exclude.write_text(current + ("" if current.endswith("\n") or not current else "\n")
                               + entry + "\n")
    except OSError:
        # A worktree-of-a-worktree has `.git` as a file, not a directory. Not
        # fatal: the dir just shows up as untracked, which the baseline records.
        pass


def ensure_group(plan_dir, group, *, project_root):
    """Pin the group's base ref and baseline on first touch; return the state.

    The base ref is captured ONCE, when the group starts, and every member
    branches from that exact sha. Contract §3 rule 7's baseline (HEAD, branch,
    pre-existing dirty state) is recorded here too — before any member runs, so
    it genuinely describes what was in the tree beforehand.
    """
    state = load_state(plan_dir, group)
    if state:
        return state
    root = repo_root(project_root)
    if root is None:
        raise WorktreeError(
            f"dispatch.isolation={pc.ISOLATION_WORKTREE!r} needs a git repository, but "
            f"{project_root} is not inside one. Remove the isolation declaration to run "
            "the group in a shared tree."
        )
    _, head, _ = git(["rev-parse", "HEAD"], root, check=True)
    _, branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], root, check=True)
    _exclude_worktree_dir(root)
    state = {
        "group": group,
        "repo_root": str(root),
        # PINNED. S02 §4: never assume a worktree's base — pass it and verify it.
        "base_ref": head,
        "base_branch": branch,
        # Contract §3 rule 7 — what was already dirty BEFORE the group ran, so
        # integration can tell a member's stray write from a pre-existing edit
        # and never sweep an unrelated change into the group's commit.
        "baseline_dirty": _status_paths(_porcelain(root)),
        "members": {},
        "merged": [],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save(plan_dir, group, state)
    rsi.log_event(plan_dir, "worktree_group_started", session_ids=[],
                  group=group, base_ref=head, base_branch=branch,
                  baseline_dirty=len(state["baseline_dirty"]))
    return state


# --------------------------------------------------------------------------
# Member worktrees
# --------------------------------------------------------------------------
def member_branch(group, session_id):
    return f"{BRANCH_PREFIX}/{group}/{session_id}"


def member_path(plan_dir, session_id):
    """The worktree recorded for a session, or None. Cheap enough to call often."""
    d = Path(plan_dir) / "_worktrees"
    if not d.is_dir():
        return None
    for f in sorted(d.glob("*.json")):
        st = ssio.read_json_with_bak(f)
        entry = (st or {}).get("members", {}).get(session_id)
        if entry and Path(entry["path"]).is_dir():
            return entry["path"]
    return None


def _stagger():
    try:
        ms = int(os.environ.get(STAGGER_MS_ENV, DEFAULT_STAGGER_MS))
    except ValueError:
        ms = DEFAULT_STAGGER_MS
    if ms > 0:
        time.sleep(ms / 1000.0)


def prepare_members(plan_dir, manifest, session_ids, *, project_root):
    """Create (or reuse) one worktree per isolated member. Returns {sid: path}.

    Creation is SERIAL with an explicit inter-creation delay — the "staggered,
    not simultaneous" requirement. Each branch is created AT THE PINNED BASE SHA
    and the result is then verified, because S02 §4 measured the default
    start-point silently being local HEAD.
    """
    by_id = {s["id"]: s for s in manifest.get("sessions") or []}
    todo = [sid for sid in session_ids if is_isolated_member(by_id.get(sid, {}))]
    if not todo:
        return {}
    out = {}
    for n, sid in enumerate(todo):
        group = group_of(by_id[sid])
        state = ensure_group(plan_dir, group, project_root=project_root)
        root = Path(state["repo_root"])
        base = state["base_ref"]
        branch = member_branch(group, sid)
        path = root / WORKTREE_DIRNAME / group / sid
        if n:
            _stagger()
        if not path.is_dir():
            path.parent.mkdir(parents=True, exist_ok=True)
            rc_b, _, _ = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)
            if rc_b == 0:
                # Retry of a member whose worktree was removed but whose branch
                # survived: re-attach, never re-create at base (that would throw
                # away everything the previous attempt committed).
                git(["worktree", "add", str(path), branch], root, check=True)
            else:
                git(["worktree", "add", "-b", branch, str(path), base], root, check=True)
        # VERIFY the base rather than trust it (S02 §4's actionable conclusion).
        rc_a, _, _ = git(["merge-base", "--is-ancestor", base, "HEAD"], path)
        if rc_a != 0:
            raise WorktreeError(
                f"worktree {path} for {sid} is NOT based on the group's pinned base ref "
                f"{base[:12]} — refusing to dispatch into it. `git worktree add` bases on "
                "local HEAD when no start-point is given, so a worktree that drifted here "
                "would merge unrelated commits into the integration tree."
            )
        entry = state["members"].get(sid) or {}
        entry.update({
            "path": str(path),
            "branch": branch,
            "base_ref": base,
            "created_at": entry.get("created_at")
            or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        # A re-dispatched member keeps its `commits` list — retry is per member,
        # in its own worktree (contract §4), not a fresh start that forgets what
        # the previous attempt already landed on the branch.
        state["members"][sid] = entry
        _save(plan_dir, group, state)
        rsi.log_event(plan_dir, "worktree_created", session_ids=[sid],
                      group=group, path=str(path), branch=branch, base_ref=base)
        out[sid] = str(path)
    return out


def prompt_preamble(path, branch, base_ref):
    """The advisory-containment instruction prepended to a member's prompt.

    Advisory is the honest word: S06 measured `EnterWorktree(path=…)` being
    REFUSED from a freshly dispatched subagent, so nothing in the harness
    confines the member here. The integration session checks whether it obeyed.
    """
    return (
        "## WORKTREE ISOLATION — do all of your work in this checkout\n\n"
        f"Working copy: `{path}`\n"
        f"Branch: `{branch}` (based on `{base_ref[:12]}`)\n\n"
        "`cd` there first and use ABSOLUTE paths under it. Do NOT edit the shared "
        "repository checkout — a peer session is editing those files right now.\n\n"
        "**Do NOT run `git commit`, `git push`, `git merge` or `git worktree` yourself.** "
        "The orchestrator commits this worktree for you when your closeout is accepted, "
        "and a separate integration session merges every member's branch afterwards. "
        "A commit made here mid-flight races your peers on `.git/index.lock`.\n"
    )


def commit_member(plan_dir, manifest, session_id, message):
    """Commit an isolated member's worktree ON ITS OWN BRANCH. Orchestrator-only.

    Called from `apply`, one session at a time — contract M1 bars the MEMBER from
    committing, not the orchestrator, and a serial committer never enters the
    contention mode the capability ledger leaves open. Returns None when the
    session is not an isolated member or produced nothing.
    """
    by_id = {s["id"]: s for s in manifest.get("sessions") or []}
    s = by_id.get(session_id)
    if not s or not is_isolated_member(s):
        return None
    group = group_of(s)
    state = load_state(plan_dir, group)
    entry = (state or {}).get("members", {}).get(session_id)
    if not entry:
        return None
    path = Path(entry["path"])
    if not path.is_dir():
        raise WorktreeError(
            f"worktree {path} for {session_id} is gone — refusing to record a result for "
            "work that has no checkout. Nothing here removes a worktree implicitly, so "
            "this was removed out of band."
        )
    if not _porcelain(path):
        return None
    git(["add", "-A"], path, check=True)
    rc, out, err = git(["commit", "-m", message], path)
    if rc != 0:
        # `git add -A` respects .gitignore, so an all-ignored worktree can stage
        # nothing. Say so instead of failing opaquely — the content is still on
        # disk and cleanup will refuse to remove it.
        if "nothing to commit" in (out + err):
            return None
        raise WorktreeError(f"commit failed in {path}: {(err or out).strip()}")
    _, sha, _ = git(["rev-parse", "HEAD"], path, check=True)
    entry.setdefault("commits", []).append(sha)
    _save(plan_dir, group, state)
    rsi.log_event(plan_dir, "worktree_committed", session_ids=[session_id],
                  group=group, branch=entry["branch"], commit=sha)
    return sha


# --------------------------------------------------------------------------
# Integration (contract §3 rules 5-7)
# --------------------------------------------------------------------------
def _under_plan_dir(plan_dir, root, path):
    """Is this shared-tree path the plan's own directory?

    Overlap in EITHER direction: the orchestrator writes PLAN.html and
    run.ndjson inside the plan dir while the group runs, and `git status`
    collapses an untracked tree to its top directory (``_plans/``), which is a
    PREFIX of the plan dir rather than a child of it.
    """
    try:
        rel = Path(plan_dir).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return False
    return pc._overlaps(path.rstrip("/"), rel)


def _overlaps_any(path, declared):
    return any(pc._overlaps(path, d) for d in declared)


def containment_report(plan_dir, manifest, group):
    """Contract §3 rule 6 — did each member actually stay in its own worktree?

    Advisory containment is only worth something if it is CHECKED. Two findings:

      * ``stray`` (REFUSAL): a path a member declared it writes is dirty in the
        SHARED tree and was not dirty in the baseline. The member wrote outside
        its worktree, so merging its branch would land a partial result while the
        stray edit sits loose in the integration commit's path.
      * ``empty`` (WARNING): a member's branch has no commits and its worktree is
        clean. Legitimate for a session that had nothing to write, and the
        signature of one that worked somewhere else entirely.
    """
    state = load_state(plan_dir, group) or {}
    root = Path(state.get("repo_root") or plan_dir)
    baseline = set(state.get("baseline_dirty") or [])
    dirty_now = [p for p in _status_paths(_porcelain(root)) if p not in baseline]
    dirty_now = [p for p in dirty_now if not _under_plan_dir(plan_dir, root, p)
                 and not p.startswith(WORKTREE_DIRNAME + "/")]
    stray, empty = [], []
    for s in group_members(manifest, group):
        sid = s["id"]
        declared = member_touches(manifest, sid)
        hits = sorted({p for p in dirty_now if _overlaps_any(p, declared)})
        if hits:
            stray.append({"session": sid, "paths": hits})
        entry = (state.get("members") or {}).get(sid)
        if entry and Path(entry["path"]).is_dir():
            _, ahead, _ = git(["rev-list", "--count", f"{state['base_ref']}..HEAD"],
                              entry["path"])
            if (ahead or "0") == "0" and not _porcelain(Path(entry["path"])):
                empty.append(sid)
    return {"stray": stray, "empty": empty,
            "shared_tree_dirty": dirty_now, "baseline_dirty": sorted(baseline)}


def merge_group(plan_dir, manifest, group, *, integration_session):
    """Producer-first merge of every member branch into the shared tree.

    Order is manifest document order (see ``group_members``). A conflict ABORTS
    the merge and returns ``status: "conflict"`` with every branch intact — never
    an automated resolution, because an agent "resolving" a merge is how work
    disappears (contract §3 rule 5; S02 §7 measured the loud failure).

    A clean merge here does NOT mean a working tree. The integration session's
    own gates — which the contract forces to be a superset of the members' —
    are what catch the semantic break that merges without complaint.
    """
    state = load_state(plan_dir, group)
    if not state:
        raise WorktreeError(f"no worktree state for parallel_group {group!r}")
    root = Path(state["repo_root"])
    order = [s["id"] for s in group_members(manifest, group)]
    contain = containment_report(plan_dir, manifest, group)
    if contain["stray"]:
        return {"status": "containment", "group": group, "order": order,
                "containment": contain, "merged": [], "base_ref": state["base_ref"],
                "branches": {sid: member_branch(group, sid) for sid in order}}

    merged, already = [], set(state.get("merged") or [])
    for sid in order:
        entry = (state.get("members") or {}).get(sid)
        if not entry:
            return {"status": "missing-member", "group": group, "session": sid,
                    "order": order, "merged": merged, "containment": contain,
                    "base_ref": state["base_ref"]}
        branch = entry["branch"]
        if sid in already:
            continue
        rc, out, err = git(
            ["merge", "--no-ff", "--no-edit", "-m",
             f"plan({group}): merge {sid} for {integration_session}", branch],
            root,
        )
        if rc != 0:
            _, conflicted, _ = git(["diff", "--name-only", "--diff-filter=U"], root)
            git(["merge", "--abort"], root)
            rsi.log_event(plan_dir, "worktree_merge_conflict",
                          session_ids=[integration_session], group=group, member=sid,
                          files=[f for f in conflicted.splitlines() if f.strip()])
            return {
                "status": "conflict", "group": group, "session": sid, "branch": branch,
                "order": order, "merged": merged, "containment": contain,
                "base_ref": state["base_ref"],
                "files": [f for f in conflicted.splitlines() if f.strip()],
                "branches": {m: member_branch(group, m) for m in order},
                "detail": (err or out).strip(),
            }
        merged.append(sid)
        state["merged"] = sorted(set(state.get("merged") or []) | {sid})
        _save(plan_dir, group, state)
        rsi.log_event(plan_dir, "worktree_merged", session_ids=[integration_session],
                      group=group, member=sid, branch=branch)
    return {"status": "merged", "group": group, "order": order, "merged": merged,
            "containment": contain, "base_ref": state["base_ref"],
            "branches": {sid: member_branch(group, sid) for sid in order}}


# --------------------------------------------------------------------------
# Cleanup — explicit, last, and never the S02 §6 shape
# --------------------------------------------------------------------------
def cleanup_group(plan_dir, group, *, force=False):
    """Remove member worktrees and branches. Contract §4: explicit and LAST.

    A worktree holding uncommitted, untracked, or ignored-but-locally-created
    content is PRESERVED and reported, not pruned — that content is exactly what
    S02 §6 watched the barred mechanism destroy. ``force`` overrides the worktree
    check on an explicit operator decision; nothing overrides the branch check,
    which uses ``git branch -d`` (never ``-D``), so a branch carrying unmerged
    commits always survives.
    """
    state = load_state(plan_dir, group)
    if not state:
        return {"status": "noop", "group": group, "reason": "no worktree state"}
    root = Path(state["repo_root"])
    removed, preserved, branches_kept = [], [], []
    for sid, entry in sorted((state.get("members") or {}).items()):
        path = Path(entry["path"])
        if path.is_dir():
            all_leftovers = _porcelain(path, ignored=True)
            leftovers = [ln for ln, p in zip(all_leftovers, _status_paths(all_leftovers))
                         if not _is_cache(p)]
            if leftovers and not force:
                preserved.append({"session": sid, "path": str(path),
                                  "content": leftovers[:20],
                                  "caches_ignored": len(all_leftovers) - len(leftovers)})
                continue
            rc, _, err = git(["worktree", "remove", *(["--force"] if force else []), str(path)],
                             root)
            if rc != 0:
                preserved.append({"session": sid, "path": str(path), "error": err})
                continue
        # Only report a branch as KEPT when it is actually still there — a second
        # cleanup pass would otherwise list every branch the first pass deleted,
        # which reads as "unmerged work survived" when nothing survived.
        if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{entry['branch']}"], root)[0] == 0:
            rc_b, _, err_b = git(["branch", "-d", entry["branch"]], root)
            if rc_b != 0:
                branches_kept.append({"session": sid, "branch": entry["branch"],
                                      "reason": err_b})
        removed.append(sid)
    git(["worktree", "prune"], root)
    state["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save(plan_dir, group, state)
    rsi.log_event(plan_dir, "worktree_cleanup", session_ids=[], group=group,
                  removed=removed, preserved=[p["session"] for p in preserved],
                  branches_kept=[b["branch"] for b in branches_kept])
    return {"status": "cleaned" if not preserved else "partial", "group": group,
            "removed": removed, "preserved": preserved, "branches_kept": branches_kept}


def group_status(plan_dir, manifest, group):
    """Read-only summary for `run.py worktree-status`."""
    state = load_state(plan_dir, group)
    if not state:
        return {"group": group, "status": "not-started"}
    members = {}
    for sid, entry in sorted((state.get("members") or {}).items()):
        path = Path(entry["path"])
        alive = path.is_dir()
        _, ahead, _ = git(["rev-list", "--count", f"{state['base_ref']}..HEAD"], path) \
            if alive else (1, "0", "")
        members[sid] = {
            "path": entry["path"], "branch": entry["branch"], "exists": alive,
            "commits_ahead_of_base": int(ahead or 0),
            "dirty": _porcelain(path) if alive else [],
        }
    return {"group": group, "base_ref": state["base_ref"],
            "base_branch": state.get("base_branch"), "repo_root": state["repo_root"],
            "merged": state.get("merged") or [], "members": members,
            "containment": containment_report(plan_dir, manifest, group)}


def json_out(obj):
    return json.dumps(obj, indent=2, sort_keys=False)
