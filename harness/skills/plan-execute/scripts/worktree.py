"""Orchestrator-managed git worktree isolation for a `parallel_group`.

The contract this implements: ``../references/parallel-group-contract.md``
(contract v2, amended). Read it first — this module is its
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

import group_scope as gs
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

    A `git` binary missing from ``PATH`` degrades to rc 127 (the shell's own
    "command not found" convention) rather than letting ``subprocess.run``'s
    ``FileNotFoundError`` propagate — every caller here already handles a
    nonzero rc through its normal refuse/degrade path (``check=True`` raises
    ``WorktreeError`` the same as any other git failure; ``repo_root`` and
    friends just return None/False), so a test that strips ``PATH`` to
    isolate an UNRELATED dependency check (REG-02's registry scan runs during
    every `begin`, git-repo or not) must not crash on this instead.
    """
    try:
        p = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as e:
        if check:
            raise WorktreeError(f"git {' '.join(args)} failed in {cwd}: {e}") from e
        return 127, "", str(e)
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
    """Keep `.plan-worktrees/` out of `git status`. See `group_scope`."""
    gs.exclude_worktree_dir(root, WORKTREE_DIRNAME)


def ensure_group(plan_dir, group, *, project_root):
    """Pin the group's base ref and baseline on first touch; return the state.

    The base ref is captured ONCE, when the group starts, and every member
    branches from that exact sha. Contract §3 rule 7's baseline (HEAD, branch,
    pre-existing dirty state) is recorded here too — before any member runs, so
    it genuinely describes what was in the tree beforehand.

    UNDER PLAN ISOLATION all three are read in the PLAN WORKTREE (V3-1/V3-2): the
    base is the plan branch's tip, and the baseline is the plan's tree, so a
    NEIGHBOUR plan's dirty files can no longer surface as this group's strays.
    `repo_root` stays the OUTER checkout — see `group_scope.group_roots`.
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
    root, merge_root, slug = gs.group_roots(plan_dir, root)
    _, head, _ = git(["rev-parse", "HEAD"], merge_root, check=True)
    _, branch, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], merge_root, check=True)
    _exclude_worktree_dir(root)
    state = {
        "group": group,
        "repo_root": str(root),
        "merge_root": str(merge_root),
        "plan_slug": slug,
        # PINNED. S02 §4: never assume a worktree's base — pass it and verify it.
        "base_ref": head,
        "base_branch": branch,
        # Contract §3 rule 7 — what was already dirty BEFORE the group ran, so
        # integration can tell a member's stray write from a pre-existing edit
        # and never sweep an unrelated change into the group's commit.
        "baseline_dirty": _status_paths(_porcelain(merge_root)),
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
def member_branch(group, session_id, plan_slug=None):
    """``plan/<group>/<sid>``; ``plan/<slug>__<group>__<sid>`` under isolation
    (operator decision — ``group_scope`` holds the measurement)."""
    return f"{BRANCH_PREFIX}/{gs.member_dirname(group, session_id, plan_slug)}"


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
        branch = member_branch(group, sid, state.get("plan_slug"))
        path = root / WORKTREE_DIRNAME / gs.member_dirname(group, sid, state.get("plan_slug"))
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


def resolve_evidence_path(plan_dir, session_id, raw):
    """Where a declared evidence path actually lives.

    For an ISOLATED PARALLEL MEMBER the artifacts are written inside that
    member's own worktree and are NOT in the shared checkout until the
    integration merge — so the member's worktree is searched FIRST (its copy of
    the plan directory, then its repo root), exactly as ``gate_cwd`` does for
    argv gates. Otherwise, and as the fallback, the pre-existing order holds:
    the plan directory, then cwd.
    """
    path = Path(raw)
    if path.is_absolute():
        return path
    roots = []
    member = member_path(plan_dir, session_id)
    if member:
        root = repo_root(plan_dir)
        if root:
            try:
                rel = Path(plan_dir).resolve().relative_to(Path(root).resolve())
                roots.append(Path(member) / rel)
            except ValueError:
                pass
        roots.append(Path(member))
    roots += [Path(plan_dir), Path.cwd()]
    for r in roots:
        cand = r / path
        if cand.exists():
            return cand
    return Path(plan_dir) / path


def _hooks_rewrote_the_tree(path):
    """True when a failed commit left UNSTAGED edits behind — a formatting hook
    rewriting what it was given (all was staged a moment ago, so a dirty worktree
    column is the hook's doing). A real refusal — ratchet, lint, rejected message
    — leaves the tree as staged, stays False, and surfaces. ``strip=False`` and
    counting ``??`` are load-bearing: see test_worktree.py."""
    rc, out, _ = git(["status", "--porcelain"], path, strip=False)
    if rc != 0:
        return False
    return any(ln[1] != " " for ln in out.splitlines() if len(ln) > 1)


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
    import plan_ship    # lazy (cycle). Never stages the FROZEN `_plans/` copy: §8.b
    if not _porcelain(path) or not plan_ship.stage(path):     # refuses ships over it
        return None
    rc, out, err = git(["commit", "-m", message], path)
    if rc != 0 and _hooks_rewrote_the_tree(path):
        # A formatting pre-commit hook (trailing-whitespace, end-of-file-fixer,
        # a formatter) fails the FIRST commit and fixes the files in place; the
        # standard response is to re-stage and commit again. Without this the
        # orchestrator halts the whole plan on a hook that already did its job.
        plan_ship.stage(path)
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

    "The shared tree" is the group's MERGE ROOT — the plan worktree under
    isolation (V3-2 §3 rules 6-7), so a neighbour plan's dirty file in the
    operator's checkout is not in this report's input at all.
    """
    state = load_state(plan_dir, group) or {}
    root = gs.merge_root(state, plan_dir)
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
    """Producer-first merge of every member branch into the group's MERGE ROOT —
    the PLAN WORKTREE under isolation (V3-2 §3 rule 5), never the primary checkout.

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
    root = gs.merge_root(state, plan_dir)
    slug = state.get("plan_slug")
    order = [s["id"] for s in group_members(manifest, group)]
    contain = containment_report(plan_dir, manifest, group)
    if contain["stray"]:
        return {"status": "containment", "group": group, "order": order, "merge_root": str(root),
                "containment": contain, "merged": [], "base_ref": state["base_ref"],
                "branches": {sid: member_branch(group, sid, slug) for sid in order}}

    # The integration session's review base is the tree BEFORE these merges; derived
    # at its later first dispatch it is the merged tip and the gates review nothing.
    import review_context as rvc  # noqa: PLC0415 — lazy: it imports plan_scope → worktree
    pre_merge = rvc.head_sha(root)  # THIS round's start: the only safe reset target
    rvc.record_base(plan_dir, integration_session, pre_merge)
    # Skip only the tip already merged (old state re-merges once: "Already up to date").
    merged, tips = [], state.get("merged_tips") or {}
    for sid in order:
        entry = (state.get("members") or {}).get(sid)
        if not entry:
            return {"status": "missing-member", "group": group, "session": sid,
                    "order": order, "merged": merged, "containment": contain,
                    "base_ref": state["base_ref"], "merge_root": str(root)}
        branch = entry["branch"]
        tip = git(["rev-parse", branch], root)[1]
        if tips.get(sid) == tip:
            continue
        rc, out, err = git(
            ["merge", "--no-ff", "--no-edit", "-m",
             f"chore(plan-{group}): merge {sid} for {integration_session}", branch],
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
                "base_ref": state["base_ref"], "merge_root": str(root), "pre_merge": pre_merge,
                "files": [f for f in conflicted.splitlines() if f.strip()],
                "branches": {m: member_branch(group, m, slug) for m in order},
                "detail": (err or out).strip(),
            }
        merged.append(sid)
        state["merged"] = sorted(set(state.get("merged") or []) | {sid})
        state.setdefault("merged_tips", {})[sid] = tip
        _save(plan_dir, group, state)
        rsi.log_event(plan_dir, "worktree_merged", session_ids=[integration_session],
                      group=group, member=sid, branch=branch)
    return {"status": "merged", "group": group, "order": order, "merged": merged,
            "containment": contain, "base_ref": state["base_ref"],
            "branches": {sid: member_branch(group, sid, slug) for sid in order}}


# --------------------------------------------------------------------------
# Cleanup — explicit, last, and never the S02 §6 shape
# --------------------------------------------------------------------------
def _unlock_worktree(root, path):
    """``git worktree unlock`` if ``path`` is registered locked, else a
    no-op. Returns a note ONLY when the documented LAST RESORT fired.

    Skipping this before ``remove``/``prune`` is the documented dead end:
    ``prune`` refuses a locked worktree BY DESIGN, so the registration
    survives forever and a later ``git worktree add`` at this path fails
    with "missing but locked working tree". ``unlock`` on an unlocked (or
    unregistered) path just errors "not locked" — ignored, not a problem.
    When ``unlock`` itself fails for some OTHER reason while the entry is
    still locked (rare git-admin corruption), the last resort is removing
    ``.git/worktrees/<name>/locked`` by hand — reported here so it is never
    silent."""
    rc, _, err = git(["worktree", "unlock", str(path)], root)
    if rc == 0 or "not locked" in err.lower():
        return None
    admin = gs.worktree_admin_dir(root, path)  # via the SHARED git dir
    marker = admin / "locked" if admin else None
    if marker and marker.exists():
        try:
            marker.unlink()
            return f"`git worktree unlock` failed ({err}); hand-removed {marker}"
        except OSError as e:
            return f"could not unlock or hand-remove the lock for {path}: {err} / {e}"
    return f"could not unlock {path}: {err}"


def teardown_worktree(root, path, force):
    """One member's worktree teardown. Fixed order: repair (a harmless no-op
    unless the worktree's git-admin links drifted) -> the dirty-content
    refusal -> unlock -> remove. Never raises: any git failure here is
    reported as ``"preserved"`` rather than propagated, so one bad entry
    never aborts the rest of a cleanup/sweep pass.

    Returns ``(status, extra)``:
      * ``"preserved"``, ``{...}``  — refused, left in place, reported.
      * ``"removed"``,   note|None  — gone, normal path.
      * ``"recovered"``, note|None  — the directory was already gone; the
        dead git-admin registration was reclaimed via unlock + an immediate
        ``prune``, since there was nothing left to ``remove``. Pruning HERE
        (not only in the caller's final pass) matters: git still treats the
        branch as "used by worktree at <gone path>" — and refuses `branch
        -d` — until the dead registration is actually pruned away, and the
        caller's branch-deletion attempt for THIS member runs immediately
        after this call returns.

    ``repair`` runs BEFORE the dirty-content check on purpose: a worktree
    whose whole project root moved has a git-admin link broken in BOTH
    directions (git 2.48.1, measured) — including the worktree's OWN
    ``.git`` file — so ``git status`` inside it would raise before repair
    ever ran.
    """
    try:
        if path.is_dir():
            git(["worktree", "repair", str(path)], root)
            all_leftovers = _porcelain(path, ignored=True)
            leftovers = [ln for ln, p in zip(all_leftovers, _status_paths(all_leftovers))
                         if not _is_cache(p)]
            if leftovers and not force:
                return "preserved", {"path": str(path), "content": leftovers[:20],
                                      "caches_ignored": len(all_leftovers) - len(leftovers)}
            note = _unlock_worktree(root, path)
            rc, _, err = git(
                ["worktree", "remove", *(["--force"] if force else []), str(path)], root
            )
            if rc != 0:
                return "preserved", {"path": str(path), "error": err}
            return "removed", note
        note = _unlock_worktree(root, path)
        git(["worktree", "prune"], root)
        return "recovered", note
    except WorktreeError as e:
        return "preserved", {"path": str(path), "error": str(e)}


def cleanup_group(plan_dir, group, *, force=False):
    """Remove member worktrees and branches. Contract §4: explicit and LAST.

    A worktree holding uncommitted, untracked, or ignored-but-locally-created
    content is PRESERVED and reported, not pruned — that content is exactly what
    S02 §6 watched the barred mechanism destroy. ``force`` overrides the worktree
    check on an explicit operator decision; nothing overrides the branch check,
    which uses ``git branch -d`` (never ``-D``), so a branch carrying unmerged
    commits always survives.

    Teardown order per member is FIXED and non-negotiable: ``unlock`` ->
    ``remove`` -> ``branch -d`` -> the final ``worktree prune`` (see
    ``teardown_worktree``/``_unlock_worktree`` for the two recoveries
    — a locked-and-hand-deleted directory, and a whole-project move — that
    order exists to close.

    The member's path is recomputed STRUCTURALLY (``root / WORKTREE_DIRNAME /
    group_scope.member_dirname(...)`` — the same derivation ``prepare_members``
    used to create it, reading the ``plan_slug`` the state file recorded)
    rather than trusted from the recorded ``entry["path"]``: after a
    whole-project move, ``root`` (re-resolved fresh from ``plan_dir``'s
    CURRENT location) is correct while the recorded absolute strings are not.
    """
    state = load_state(plan_dir, group)
    if not state:
        return {"status": "noop", "group": group, "reason": "no worktree state"}
    root = repo_root(plan_dir) or Path(state["repo_root"])
    # `branch -d` is judged against the CURRENT branch, so it asks the MERGE ROOT:
    # under isolation a member merges into the plan branch, and the outer checkout
    # (on `main`) would call every one unmerged and keep every ref forever.
    broot = gs.merge_root(state, plan_dir, fallback=root)
    removed, recovered, preserved, branches_kept, notes = [], [], [], [], []
    for sid, entry in sorted((state.get("members") or {}).items()):
        path = root / WORKTREE_DIRNAME / gs.member_dirname(group, sid, state.get("plan_slug"))
        branch = entry["branch"]
        status, extra = teardown_worktree(root, path, force)
        if status == "preserved":
            preserved.append({"session": sid, **extra})
            continue
        if extra:
            notes.append(extra)
        if status == "recovered":
            recovered.append(sid)
        # Only report a branch as KEPT when it is actually still there — a second
        # cleanup pass would otherwise list every branch the first pass deleted,
        # which reads as "unmerged work survived" when nothing survived.
        if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], broot)[0] == 0:
            rc_b, _, err_b = git(["branch", "-d", branch], broot)
            if rc_b != 0:
                branches_kept.append({"session": sid, "branch": branch, "reason": err_b})
        removed.append(sid)
    git(["worktree", "prune"], root)
    state["cleaned_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save(plan_dir, group, state)
    rsi.log_event(plan_dir, "worktree_cleanup", session_ids=[], group=group,
                  removed=removed, recovered=recovered,
                  preserved=[p["session"] for p in preserved],
                  branches_kept=[b["branch"] for b in branches_kept], notes=notes)
    return {"status": "cleaned" if not preserved else "partial", "group": group,
            "removed": removed, "recovered": recovered, "preserved": preserved,
            "branches_kept": branches_kept, "notes": notes}


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
    return {"group": group, "base_ref": state["base_ref"], "members": members,
            "base_branch": state.get("base_branch"), "repo_root": state["repo_root"],
            "merge_root": state.get("merge_root"), "merged": state.get("merged") or [],
            "containment": containment_report(plan_dir, manifest, group)}


def json_out(obj):
    return json.dumps(obj, indent=2, sort_keys=False)
