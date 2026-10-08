"""ISO-01 / ISO-04 — one branch and one LOCKED worktree per PLAN, at `begin`.

The authority is ``../references/plan-isolation-contract.md`` (contract v1,
frozen 2026-08-21). Read it first; this module is its mechanism, not its source
of truth. Section numbers below refer to it.

WHY THIS LIVES BESIDE ``worktree.py`` RATHER THAN INSIDE IT. Every piece of git
plumbing here is IMPORTED from it — ``git()``, ``repo_root()``, the dirname and
branch-prefix constants, ``_exclude_worktree_dir`` and the whole
``teardown_worktree`` order — so this is a caller, not a fork. The split is a
measured constraint, not taste: ``worktree.py`` is pinned at exactly 790 LOC in
``.file-size-exceptions`` and this repo's ``pre-commit`` runs
``check_file_sizes.py --staged``, which BLOCKS any commit growing a file past its
baseline. §9's hooks live in ``plan_hooks.py`` for the same reason.

OWNED HERE (s05): the version gate (§6 — ``plan_schema_version >= 7`` or an
explicit ``--isolate``, with ``--no-isolate`` beating both); branch
``plan/<plan-slug>`` at a PINNED base plus a ``--lock``ed worktree at
``<repo>/.plan-worktrees/<plan-slug>/`` (§1.1-§1.3); the ownership refusals (§1.4,
§1.5 — a legacy nested ref, or a branch/path matching the name but mapping to no
state file, which is UNKNOWN and untouchable); the hook gate (§9) in a UNIQUE
TEMP hooks directory, never the shared ``$GIT_COMMON_DIR/hooks``; the optional
``worktree_setup`` argv (ISO-04), run once, ``shell=False``; and teardown's
``unlock`` -> ``remove`` -> ``prune`` with its two recoveries (§12.3).

NOT OWNED HERE: dispatch cwd scoping (s06), groups nested under the plan branch
(s07), the land protocol §4 (s08). NOT OWNED HERE EITHER (moved out, s09): both
teardown halves of §15 — ``remove_plan_worktree`` (§12.3's unlock/remove/prune)
and ``retire_plan`` (the `abandon` row, wired to ``run.py retire-plan``) now
live in ``plan_teardown.py`` beside the land-success cleanup that already
called the former; the move was a straight relocation to clear this file's
500-LOC bound, not a redesign — see that module's docstring for the full
picture. §6.4's stamp — ``build_plan.PLAN_SCHEMA_VERSION`` — was held at 6 while
this was built and was FLIPPED TO 7 BY s10 (2026-08-23) after the two-plan
end-to-end proof and the real-plan canary. Isolation is therefore ON by default
for every plan built from that point; every plan already on disk stays below the
gate and is unaffected (measured: ``_evidence/s10/activation-sweep.json``,
29 manifests scanned, zero crossings).
"""

import json
import subprocess
import time
from pathlib import Path

import plan_hooks as ph
import run_state_io as rsi
import ship_locks as sl
import ship_state_io as ssio
import worktree as wt
# §6 lives in plan_version_gate.py (split for the same file-size reason as
# plan_hooks.py); re-exported so run.py and the tests keep one import surface.
from plan_version_gate import (ISOLATION_MIN_SCHEMA,  # noqa: F401
                               isolates_the_tree, isolation_enabled,
                               record_isolation_gate, sweep_schema_versions)
from worktree import WorktreeError, git, repo_root

STATE_FILENAME = "_plan_worktree.json"
# §8.d/§8.e — RUNTIME state lives in the git common dir, outside every working
# tree, so no neighbouring plan's `git add -A` can ever sweep it into a commit.
PLAN_STATE_DIRNAME = "plan-state"

SETUP_TIMEOUT = 1800


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _real(p):
    """Compare paths as `git worktree list` prints them (macOS /var ->
    /private/var), so a registration lookup never misses on a symlink."""
    return str(Path(p).resolve())


# ---- Names, state, and the RUNTIME claim --------------------------------
def plan_slug(plan_dir):
    return Path(plan_dir).resolve().name


def plan_branch(slug):
    return f"{wt.BRANCH_PREFIX}/{slug}"


def plan_worktree_path(root, slug):
    return Path(root) / wt.WORKTREE_DIRNAME / slug


def state_path(plan_dir):
    return Path(plan_dir) / STATE_FILENAME


def load_state(plan_dir):
    return ssio.read_json_with_bak(state_path(plan_dir))


def _save(plan_dir, state):
    state["updated_at"] = _now()
    ssio.durable_write_json(state_path(plan_dir), state)
    return state


def plan_state_dir(root, slug):
    """``$GIT_COMMON_DIR/plan-state/<slug>/`` — §8.d's RUNTIME home."""
    common = sl._git_common_dir(root)
    return (common / PLAN_STATE_DIRNAME / slug) if common \
        else (Path(root) / f".{PLAN_STATE_DIRNAME}" / slug)


def read_claim(root, slug):
    return ssio.read_json_with_bak(plan_state_dir(root, slug) / "worktree.json")


def _write_claim(root, slug, claim):
    ssio.durable_write_json(plan_state_dir(root, slug) / "worktree.json", claim)


def _clear_claim(root, slug):
    for name in ("worktree.json", "worktree.json.bak"):
        (plan_state_dir(root, slug) / name).unlink(missing_ok=True)


def _clear_claim_path(root, slug):
    """After a teardown that actually removed the worktree (status != ``"preserved"``),
    drop the claim's ``path`` — never the whole claim. ``require_live`` reads a
    missing ``path`` as "nothing to check"; a STALE one (directory gone) is what it
    refuses on. Used by ``plan_teardown.step_cleanup`` directly; ``retire_plan``
    applies the same "no dead path" rule inline, since it rebuilds the whole claim
    dict itself (the `abandon` marker) rather than editing this one in place.
    ``branch``/``default_branch``/etc. survive, because ``land_state.context``
    reads ``branch`` from this claim on every call, including a landed plan's
    idempotent retry — a full ``_clear_claim`` here would make that call return
    None and misreport a landed plan as "not isolated"."""
    claim = read_claim(root, slug)
    if not claim:
        return
    claim.pop("path", None)
    _write_claim(root, slug, claim)


# ---- Worktree registration (read-only helpers) --------------------------
def list_worktrees(root):
    """``git worktree list --porcelain`` as dicts. ``locked`` is present as a
    key on a locked entry (its value is the reason, or True when empty)."""
    rc, out, err = git(["worktree", "list", "--porcelain"], root, strip=False)
    if rc != 0:
        raise WorktreeError(f"git worktree list failed in {root}: {err}")
    entries, cur = [], {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        cur[key] = value or True
    if cur:
        entries.append(cur)
    return entries


def worktree_entry(root, path):
    want = _real(path)
    for e in list_worktrees(root):
        if isinstance(e.get("worktree"), str) and _real(e["worktree"]) == want:
            return e
    return None


def is_locked(root, path):
    entry = worktree_entry(root, path)
    return bool(entry) and "locked" in entry


# ---- §1.3 base pinning, §7 refusals, §1.1b ignore check -----------------
def _pin_base(root):
    """``(default_branch, base_expr, base_sha, source)`` — §1.3 / §4.0.
    ``origin/<default>`` when there is a remote; the local ``HEAD`` only in a
    repo without one, and the caller records WHICH. No network call: a repo whose
    ``refs/remotes/origin/HEAD`` is unset falls back to the local branch name."""
    rc, out, _ = git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], root)
    default = out.split("/", 1)[1] if rc == 0 and out.startswith("origin/") else None
    if not default:
        rc, out, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], root)
        default = out if rc == 0 and out and out != "HEAD" else None
    if not default:
        raise WorktreeError(
            f"cannot resolve a default branch in {root}: origin/HEAD is unset and HEAD is "
            "detached. Check out a branch, or set refs/remotes/origin/HEAD, before "
            "isolating a plan here."
        )
    rc, sha, _ = git(["rev-parse", "--verify", "--quiet",
                      f"refs/remotes/origin/{default}"], root)
    if rc == 0 and sha:
        return default, f"origin/{default}", sha, "origin"
    rc, sha, _ = git(["rev-parse", "--verify", "--quiet", "HEAD"], root)
    if rc != 0 or not sha:
        raise WorktreeError(f"{root} has no commits — nothing to base a plan branch on.")
    return default, "HEAD", sha, "local-head"


def _refuse_submodules(root, base_sha):
    """§7.1 — a hard stop, not a warning. Submodule state lives in the shared
    ``.git/modules``, so two worktrees at different submodule revisions fight
    over one gitdir (``git-worktree(1)``, BUGS)."""
    if git(["cat-file", "-e", f"{base_sha}:.gitmodules"], root)[0] == 0:
        raise WorktreeError(
            f"refusing to isolate a plan in {root}: .gitmodules exists at the pinned base "
            f"{base_sha[:12]}. git-worktree(1) records that multiple superproject "
            "checkouts of a submodule-bearing repo are not recommended — a partially "
            "checked-out submodule looks exactly like a clean tree (contract §7.1)."
        )


def _ensure_ignored(root):
    """§1.1b — VERIFY that `.plan-worktrees/` is ignored rather than assume it.
    Escalating, cheapest first: already ignored -> `.git/info/exclude` (repo-local,
    dirties no tracked file) -> appending to the tracked `.gitignore`. Returns
    which rung fired, and the caller logs it."""
    probe = f"{wt.WORKTREE_DIRNAME}/probe"
    if git(["check-ignore", "-q", probe], root)[0] == 0:
        return "already-ignored"
    wt._exclude_worktree_dir(root)
    if git(["check-ignore", "-q", probe], root)[0] == 0:
        return "git-info-exclude"
    gitignore = Path(root) / ".gitignore"
    current = gitignore.read_text() if gitignore.exists() else ""
    sep = "" if (not current or current.endswith("\n")) else "\n"
    gitignore.write_text(f"{current}{sep}/{wt.WORKTREE_DIRNAME}/\n")
    return "gitignore"


def _check_namespace(root, branch, path, state):
    """§1.4 collisions and §1.5's UNKNOWN class, BEFORE anything is written.
    Ownership is read only from this plan's own state file. A ref or a worktree
    that matches the naming scheme but maps to no state is UNKNOWN: reported,
    never adopted, never reused, never force-updated."""
    rc, out, _ = git(["for-each-ref", "--format=%(refname)", f"refs/heads/{branch}/**"], root)
    nested = [line for line in out.splitlines() if line.strip()]
    if nested:
        raise WorktreeError(
            f"refusing to create {branch}: git cannot hold both refs/heads/{branch} and the "
            f"existing nested ref(s) {nested}. These are legacy parallel-group member "
            "branches (`plan/<group>/<sid>`) whose group name equals this plan's slug. "
            "Rename the plan directory or retire the legacy group (contract §1.4)."
        )
    ours = bool(state) and state.get("branch") == branch
    if not ours and git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] == 0:
        raise WorktreeError(
            f"refusing to use {branch}: the ref already exists but this plan has no "
            f"{STATE_FILENAME} claiming it. A name is a claim anybody can make, so an "
            "unmapped ref is UNKNOWN and untouchable — never adopted, never reused "
            "(contract §1.5). Delete it deliberately, or run `plan-adopt`."
        )
    if not ours and worktree_entry(root, path) is not None:
        raise WorktreeError(
            f"refusing to use {path}: a worktree is already registered there and this plan "
            f"has no {STATE_FILENAME} claiming it (contract §1.4/§1.5)."
        )


# ---- ISO-04 — the optional per-plan setup command -----------------------
def _run_setup(argv, path):
    """One declared setup command, ONCE, ``shell=False``, in the fresh worktree.
    An argv ARRAY is required and a string refused: a fresh worktree holds tracked
    files only, so this routinely installs dependencies, and `shell=True` on a
    manifest-supplied string is a command-injection surface for anyone who can
    edit a manifest."""
    if not isinstance(argv, (list, tuple)) or not argv \
            or not all(isinstance(a, str) and a for a in argv):
        raise WorktreeError(
            f"manifest worktree_setup must be a non-empty ARGV ARRAY of strings, got "
            f"{argv!r}. It runs with shell=False, so a single string cannot be executed "
            "(and would be a command-injection surface if it were)."
        )
    started = _now()
    try:
        p = subprocess.run(list(argv), cwd=str(path), capture_output=True,
                           text=True, timeout=SETUP_TIMEOUT, shell=False)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        rc, out, err = 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        rc, out, err = 124, "", f"timed out after {SETUP_TIMEOUT}s: {e}"
    return {"argv": list(argv), "rc": rc, "ok": rc == 0, "ran_at": started,
            "stdout": out[-4000:], "stderr": err[-4000:]}


# ---- Creation -----------------------------------------------------------
def _create(root, branch, path, base, reason):
    """§1.2 — created LOCKED. A branch that already exists is RE-ATTACHED, never
    re-created at base: re-creating would throw away everything the previous
    attempt committed (the same retry rule ``prepare_members`` already follows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = ["--lock", "--reason", reason]
    if git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] == 0:
        git(["worktree", "add", *lock, str(path), branch], root, check=True)
    elif base is None:
        # Backstop for this helper's own contract — `None` must never reach
        # git's argv (a TypeError, no refusal, no rollback). The DURABLE refusal
        # for this shape lives in `_ensure_under_lease` (`branch_created`),
        # which fires before `_reattach`'s prune can destroy the reflog; this
        # catches any future caller that arrives without that check.
        raise WorktreeError(
            f"refusing to re-attach {path}: its worktree registration survives but "
            f"refs/heads/{branch} does not — the branch was deleted out of band. "
            "Recover the tip via `git fsck --lost-found`, re-create the branch, "
            "or retire this plan's state deliberately."
        )
    else:
        git(["worktree", "add", *lock, "-b", branch, str(path), base], root, check=True)


def _rollback(root, branch, path, slug, *, created_branch):
    """§4.1a — a half-built plan is torn down while the lease is still held,
    never left to expire. Creating the ref but failing the worktree add is the
    exact shape that would otherwise leave a permanently untouchable UNKNOWN.

    ONLY what THIS call created. On the re-attach path the branch predates the
    call and holds committed session work, so `branch -D` there would destroy the
    thing the retry exists to recover — reflog included, since the worktree that
    referenced it is gone too. The claim is scoped the same way: it still maps a
    branch that still exists."""
    if worktree_entry(root, path) is not None or path.is_dir():
        wt.teardown_worktree(root, path, True)
    git(["worktree", "prune"], root)
    if created_branch:
        git(["branch", "-D", branch], root)
        _clear_claim(root, slug)


def _reattach(root, path, branch, reason):
    """The registration survived but the directory did not (deleted by hand).
    A ``--lock``ed worktree is never ``prunable``, so "just prune it" is a dead
    end that strands the registration permanently: ``unlock`` first, then
    ``prune``, then re-add attached to the branch (§12.3)."""
    wt._unlock_worktree(root, path)
    git(["worktree", "prune"], root)
    _create(root, branch, path, None, reason)


def ensure_plan_worktree(plan_dir, manifest=None, *, project_root=None, hook_body=None):
    """Create (or re-attach) this plan's branch and locked worktree. Idempotent.
    Returns the recorded state, or None when the target is not a git checkout —
    §7.2 keeps non-git targets running exactly as they do today, unchanged and
    unwarned. Everything runs under the repo-scoped ``git:`` lease, because
    worktree administration, shared config and pruning are all repo-coordinated
    (§4.1a, §11)."""
    plan_dir = Path(plan_dir)
    root = repo_root(project_root or plan_dir)
    if root is None:
        return None
    resource = f"git:{root}"
    sl.acquire_ship_lock(plan_dir, resource, guarded_timeout=SETUP_TIMEOUT)
    try:
        return _ensure_under_lease(plan_dir, manifest or {}, root, hook_body)
    finally:
        sl.release_ship_lock(plan_dir, resource)


def _ensure_under_lease(plan_dir, manifest, root, hook_body):
    slug = plan_slug(plan_dir)
    branch, path = plan_branch(slug), plan_worktree_path(root, slug)
    state = load_state(plan_dir) or {}
    # §4.1a step 2 — classify ownership BEFORE anything is written.
    _check_namespace(root, branch, path, state)
    exists = git(["rev-parse", "--verify", "--quiet",
                  f"refs/heads/{branch}"], root)[0] == 0
    if state.get("branch_created") and not exists:
        # Fires BEFORE `_reattach` can `worktree prune` away the per-worktree
        # reflog (.git/worktrees/<slug>/logs/HEAD) the recovery below names,
        # and it writes nothing — so EVERY call refuses, not only the first.
        # A one-shot refusal is not a control: the retry after it used to find
        # no registration left and silently re-create the branch at the pinned
        # base, resetting committed session work.
        raise WorktreeError(
            f"refusing to touch {path}: this plan created refs/heads/{branch} and the "
            "ref no longer exists — deleted out of band. Re-creating it at the "
            "recorded base would silently reset committed session work. Recover the "
            f"tip from the surviving worktree reflog (worktrees/{slug}/logs/HEAD "
            "under the git common dir), or `git fsck --lost-found` if that is gone; "
            "re-create the branch there, then re-run."
        )
    if not state:
        default, base_expr, base_sha, source = _pin_base(root)
        _refuse_submodules(root, base_sha)
        ignore_mode = _ensure_ignored(root)
        state = {
            "plan_slug": slug, "repo_root": str(root), "branch": branch,
            "path": str(path), "base_ref": base_sha, "base_ref_expr": base_expr,
            "base_branch": default, "default_branch": default, "base_source": source,
            "locked_reason": f"{slug} started_at={_now()}",
            "gitignore_mode": ignore_mode, "created_at": _now(),
        }
        _save(plan_dir, state)
        rsi.log_event(plan_dir, "plan_isolation_base_pinned", session_ids=[], slug=slug,
                      base_ref=base_sha, base_ref_expr=base_expr, base_source=source,
                      default_branch=default, gitignore_mode=ignore_mode)
    # §4.1a step 3 — the CLAIM precedes the ref/worktree on EVERY call, not only
    # the first: a created-branch rollback clears it, so a retry that then
    # succeeds must re-map the branch and LOCKED worktree it (re)creates, or the
    # sweep meets exactly the untouchable UNKNOWN this ordering exists to
    # prevent (§1.5). A crash after this leaves only a claim with no resource,
    # which a sweep reports as owned — the safe direction.
    _write_claim(root, slug, {k: state[k] for k in
                              ("plan_slug", "branch", "path", "base_ref",
                               "default_branch", "created_at")}
                 | {"plan_dir": str(plan_dir)})
    registered = worktree_entry(root, path) is not None
    if not registered or not path.is_dir():
        try:
            if registered:
                _reattach(root, path, branch, state["locked_reason"])
            else:
                _create(root, branch, path, state["base_ref"], state["locked_reason"])
        except WorktreeError:
            # `exists` was sampled above under the same lease, with the SAME
            # probe `_create` branches on: rollback may delete the ref only
            # when THIS call created it (§4.1a).
            _rollback(root, branch, path, slug, created_branch=not exists)
            raise
        rsi.log_event(plan_dir, "plan_worktree_created", session_ids=[], slug=slug,
                      path=str(path), branch=branch, base_ref=state["base_ref"],
                      locked=is_locked(root, path))
    if not state.get("branch_created") and \
            git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], root)[0] == 0:
        # Recorded only once the ref provably exists: this flag is what
        # separates "deleted out of band" (the refusal above) from "never
        # created, or rolled back", where a retry is safe.
        state["branch_created"] = _now()
        _save(plan_dir, state)
    # VERIFY the base rather than trust it: `git worktree add` bases on local
    # HEAD when no start-point is given (S02 §4), and a recorded-but-unverified
    # base is the "stale artifact as proof" shape §1.3 names.
    if git(["merge-base", "--is-ancestor", state["base_ref"], "HEAD"], path)[0] != 0:
        raise WorktreeError(
            f"plan worktree {path} is NOT based on the pinned base "
            f"{state['base_ref'][:12]} — refusing to dispatch into it (contract §1.3)."
        )
    if not is_locked(root, path):
        git(["worktree", "lock", "--reason", state["locked_reason"], str(path)], root)
    if not (state.get("hook_canary") or {}).get("ok"):
        state["hook_canary"] = ph.hook_gate(root, path, hook_body=hook_body)
        _save(plan_dir, state)
    setup_argv = manifest.get("worktree_setup")
    if setup_argv is not None and not (state.get("setup") or {}).get("ok"):
        state["setup"] = record = _run_setup(setup_argv, path)
        _save(plan_dir, state)
        rsi.log_event(plan_dir, "plan_worktree_setup", session_ids=[], slug=slug,
                      argv=record["argv"], rc=record["rc"], ok=record["ok"])
        if not record["ok"]:
            raise WorktreeError(
                f"worktree_setup {record['argv']} failed in {path} (rc={record['rc']}).\n"
                f"--- stdout ---\n{record['stdout']}\n--- stderr ---\n{record['stderr']}"
            )
    return state


if __name__ == "__main__":  # pragma: no cover - operator/evidence entry point
    import sys
    if len(sys.argv) < 2 or sys.argv[1] != "sweep":
        sys.exit(f"usage: {Path(__file__).name} sweep <plans-root>")
    print(json.dumps(sweep_schema_versions(sys.argv[2] if len(sys.argv) > 2
                                           else "_plans"), indent=2))
