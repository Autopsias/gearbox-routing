"""The CONTEXT verify hands a review gate: base, scope, plan dir, session id.

Orchestrator-side. The gate-side half -- what the gate does with these -- is
`llm_review_surface.py`. Two modules because the two run in different processes:
this one has the plan state, that one has the tree.

Both answer the same question — *which changes belong to this session?* — and the
gates could not answer it at all before 2026-08-20. Measured that day, over 21
hours and two plans running in one checkout:

  * 16 of 17 verify failures were `llm-review-medium`, and rework never
    converged. The surface is `git diff HEAD` + untracked, and verify runs
    BEFORE a session commits, so every rework round re-reviewed the session's
    ENTIRE accumulated output with a fresh model sample. Round 2 raised findings
    on round-0 files that round 1 had never sampled — previously-unsampled code,
    not regressions. `max_rework` was a cutoff on a sampling process rather than
    a convergence criterion.
  * The surface is the TREE, so a concurrent session's uncommitted work is
    reviewed as this session's. Three rework attempts were spent on findings in
    files the reviewed session never touched: real defects, charged to the wrong
    budget, invisible to whoever caused them.

BASE fixes the first: `git diff <base>` covers the session's commits as well as
its uncommitted edits, so a session may commit before verify and still be
reviewed instead of handing the reviewer an empty diff that reads as green.

SCOPE fixes the second: the surface is restricted to the paths a session owns.

Both are OPTIONAL. Absent, the gate keeps its old whole-tree behaviour — this
adds a bound, it never silently narrows what an existing plan reviews.
"""
import json
import pathlib
import subprocess

import plan_scope as ps
import run_state_io as rsi

BASE_ENV = "PLAN_EXECUTE_REVIEW_BASE"
SCOPE_ENV = "PLAN_EXECUTE_REVIEW_SCOPE"
# The mirror of SCOPE: paths to REMOVE from the surface. §5.1b needs it —
# the land re-gate runs on the tree that will be pushed, which contains the
# plan's own record commit, and a scope (an INCLUDE list) cannot express
# "everything except this".
EXCLUDE_ENV = "PLAN_EXECUTE_REVIEW_EXCLUDE"
PLAN_DIR_ENV = "PLAN_EXECUTE_PLAN_DIR"    # the gate filters other plans' _plans/** on these
SESSION_ENV = "PLAN_EXECUTE_SESSION"      # and other sessions' _evidence/
# WHICH FAMILY BUILT the code under review. A cross-family gate that cannot
# answer this is not a cross-family gate: under `--harness codex` the family that
# did NOT write the code is Claude, and running the Claude reviewer there sends
# an operator's code to Anthropic when they chose Codex to keep it away.
HARNESS_ENV = "PLAN_EXECUTE_HARNESS"
CODEX, CLAUDE = "codex", "claude"
# The land re-gate's scope id. `land_gate._run_argv_gate` passes it where a
# session id goes, because §5.1b's re-gate reviews the plan's MERGED tree and
# not any one session's work -- see `land_harness`.
LAND = "land"


def head_sha(cwd):
    """The current commit, or None in a repo with no commits / no git."""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd,
                              capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def first_dispatch_at(plan_dir, session_id):
    """Timestamp of this session's FIRST `dispatch_started`, or None.

    Read from run.ndjson rather than stamped at dispatch ON PURPOSE: run.py sits
    at exactly its size baseline, and buying a line there by trimming another
    session's load-bearing comments is a worse trade than deriving a fact the log
    already records. It also makes the base available RETROACTIVELY, for sessions
    dispatched before any of this existed.
    """
    try:
        lines = (pathlib.Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return None
    for ln in lines:                       # first match wins: FIRST dispatch
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if (rec.get("event") == "dispatch_started"
                and session_id in (rec.get("session_ids") or [])):
            return rec.get("ts")
    return None


def harness(plan_dir, session_id):
    """`"codex"` or `"claude"` — which family BUILT the code this gate reviews.

    Read from the session's own durable dispatch record in run.ndjson, the
    derivation source the SSOT already names (`verification.verifier_selection`).
    NOT from a runner argument: `verify.declared_env` calls `gate_env` without a
    harness and neither has ever had such a parameter, so that path does not
    exist to read from.

    TWO events, because they answer at two different scopes and either alone is
    wrong:
      * `dispatch_started.harness == "codex"` — the WHOLE RUN is on the Codex
        harness, so every session it dispatches was built by Codex.
      * `dispatch_families.families[<sid>] == "openai"` — this ONE session
        resolved to a Codex executor under the Claude harness (the
        `executor_policy` dial). `begin` writes it immediately after
        `dispatch_started`, so the more specific record wins within a batch.
    Either one means codex. A session with no dispatch record at all reads
    `claude`, which is also what every plan built before this got.

    THE LAST pair, never ANY: run.ndjson is append-only, and a rework
    re-dispatch (or a harness change mid-plan) appends a fresh pair. Keying off
    the mere PRESENCE of a codex record pinned the lane to codex forever once a
    session had run there even once — measured, and fixed the same way, in
    run.py `_dispatch_lane`.

    PER SESSION, and only that: the land re-gate's scope is the whole plan and is
    rolled up by `land_harness` instead.
    """
    try:
        lines = (pathlib.Path(plan_dir) / "run.ndjson").read_text().splitlines()
    except OSError:
        return CLAUDE
    seen = CLAUDE
    for ln in lines:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if session_id not in (rec.get("session_ids") or []):
            continue
        if rec.get("event") == "dispatch_started":
            seen = CODEX if rec.get("harness") == CODEX else CLAUDE
        elif rec.get("event") == "dispatch_families":
            fam = (rec.get("families") or {}).get(session_id)
            if fam:
                seen = CODEX if fam == "openai" else CLAUDE
    return seen


def land_harness(plan_dir):
    """`"codex"` if ANY session in this plan was built under Codex, else `"claude"`.

    THE LAND SCOPE IS NOT A SESSION and must not be resolved like one. §5.1b's
    re-gate reviews the plan's MERGED tree — every session's work at once — so
    the family that did NOT write that tree is Claude the moment ONE session was
    Codex-built. `harness(plan_dir, "land")` answered `claude` for every plan
    (nothing is ever CALLED "land", so the lookup found no record and fell
    through to its default), the land re-gate ran `llm_review_gate.py --harness
    claude`, `refusal()` returned None, and the on-box `claude -p` reviewer read
    Codex-built code — the egress the opt-in exists to prevent, at the one moment
    the tree under review is everything the plan will push.

    ANY, unlike the LAST-pair rule above — and the two are not in tension: each
    session is still resolved by `harness`, so a session re-dispatched onto the
    Claude harness stops counting; only the ROLL-UP across sessions is a
    disjunction. The plan's own sessions are the unit, never "is there a codex
    record anywhere in run.ndjson", which is the pinning defect `harness`'s
    docstring names.

    Session ids come from the manifest — a superset of what was dispatched, and
    the extras are neutral: an undispatched session has no record and reads
    `claude`. Unreadable manifest reads `claude`, the same answer as an
    unreadable run.ndjson.
    """
    try:
        import manifest_io as mio          # noqa: PLC0415 — as in `session_spec`
        sids = mio.all_session_ids(mio.load_manifest(plan_dir))
    except Exception:                      # noqa: BLE001 — no manifest, no plan
        return CLAUDE
    # One run.ndjson read per session. ~20 sessions x a few hundred lines, once
    # per land re-gate; a single-pass map is the upgrade if a plan ever dwarfs
    # that.
    return CODEX if any(harness(plan_dir, s) == CODEX for s in sids) else CLAUDE


def derive_base(plan_dir, session_id, cwd):
    """The commit that was HEAD when this session was first dispatched.

    `git rev-list -1 --before=<ts> HEAD`. Returns None when the session was never
    dispatched, git cannot answer, or no commit predates the dispatch — and None
    means the gate keeps its old `git diff HEAD` surface rather than failing.
    """
    ts = first_dispatch_at(plan_dir, session_id)
    if not ts:
        return None
    try:
        proc = subprocess.run(["git", "rev-list", "-1", f"--before={ts}", "HEAD"],
                              cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (proc.stdout.strip() or None) if proc.returncode == 0 else None


def record_base(plan_dir, session_id, base_ref):
    """Cache a derived base — FIRST DISPATCH WINS.

    A rework must review everything the session has done, so the base must not
    move forward each round; that would hide earlier attempts' work once it is
    A REDISPATCH KEEPS THE ORIGINAL BASE, deliberately. It restarts the session,
    but the session's work is still sitting uncommitted in the tree, so reviewing
    from the first dispatch is the only base that covers all of it. `derive_base`
    reads the FIRST `dispatch_started`, so this needs no special case.
    """
    if not base_ref:
        return None
    state = rsi.load_state(plan_dir)
    bases = state.setdefault("session_base", {})
    if bases.get(session_id):
        return bases[session_id].get("base_ref")
    bases[session_id] = {"base_ref": base_ref}
    rsi.save_state(plan_dir, state)
    rsi.log_event(plan_dir, "session_base_recorded", session_ids=[session_id],
                  base_ref=base_ref)
    return base_ref


def recorded_base(plan_dir, session_id):
    """The cached session base, READ ONLY — no derive, no write, no fallback.

    `get_base` is the gate's accessor and has side effects (it derives and CACHES
    on a miss, and falls back to the plan's pinned base). A caller that only wants
    to know what was already recorded — `evidence_proof` — must not trigger either:
    caching a base derived in ITS cwd would move the reviewer's window, and the
    pinned fallback is an ancestor of every tree by construction, which makes any
    ancestry guard downstream unreachable.
    """
    rec = (rsi.load_state(plan_dir).get("session_base") or {}).get(session_id) or {}
    return rec.get("base_ref")


def usable_base(base, cwd):
    """Can ``base`` bound a diff taken in ``cwd``? Only if it is an ANCESTOR of
    that tree's HEAD.

    `git diff <base>` against a commit HEAD cannot reach shows the symmetric
    difference of two unrelated lines of history — under ISO-02 the gate's cwd is
    the plan WORKTREE, so a base recorded from a call made in the operator's
    checkout (a commit on `main` made after the branch was cut) would put every
    unrelated change between the two into this session's review surface.

    False on any git failure: an unanswerable question is not a yes.
    """
    if not base:
        return False
    try:
        proc = subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"],
                              cwd=cwd, capture_output=True, text=True,
                              check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def get_base(plan_dir, session_id, cwd=None):
    """The session's starting commit: cached, else derived and cached, else the
    plan's pinned base, else None.

    None is not an error — it is every session dispatched before this existed,
    and the gate falls back to its old whole-tree `git diff HEAD` surface.

    THE PER-SESSION BASE COMES FIRST, and an isolated plan's PINNED base (§1.3)
    is its FALLBACK — never an override. Under isolation every session commits to
    the same plan branch, so a pinned base that won unconditionally would make
    session sNN's gate diff the branch CUT POINT to HEAD and re-review every
    earlier session's commits: the surface grows with each session, which is the
    shape that already cost this repo 16 failed gate rounds over 21 hours
    (2026-08-20) and the reason this plan is split into small sessions at all.
    Derived in the gate's own cwd — the plan worktree — `derive_base` answers
    "what was the plan branch's tip when this session was dispatched", which is
    exactly the bound that keeps sNN's surface to sNN's own work.

    The pin is for the case the derived base cannot serve: a fresh plan worktree
    with no `dispatch_started` to derive from, or a base recorded by an earlier
    call made somewhere else. `usable_base` is what tells those apart — the pin is
    an ancestor of the plan branch by construction, a drifted base is not.
    """
    cwd = cwd or "."
    base = (recorded_base(plan_dir, session_id)
            or record_base(plan_dir, session_id,
                           derive_base(plan_dir, session_id, cwd)))
    pinned = ps.pinned_base(plan_dir)
    if pinned and not usable_base(base, cwd):
        return pinned
    return net_of_default_merges(base, cwd, ps.claim(plan_dir).get("default_branch") or "main")


def net_of_default_merges(base, cwd, branch):
    """``base`` with every default-branch commit merged in since then folded into it.

    A session that merges ``origin/<branch>`` into the plan branch after it was
    dispatched (the build rule "merge main before any build") otherwise hands its
    review gate all of main's changes since dispatch: on 2026-10-04 s01 of
    example-project-agentic-edge put 290 files of already-reviewed main code in front of
    ``llm-review-low`` for 3 files of its own, and both reviewer attempts timed out.
    The returned value is the TREE of ``merge(base, merge-base(HEAD, origin/<branch>))``,
    so ``git diff <tree>`` is the session's own work and nothing main brought.
    Unchanged when main merged nothing new, on a merge conflict, or on any git
    failure -- the wider surface is the safe fallback, never a narrower guess.
    """
    if not base:
        return base

    def git(*args):
        try:
            p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                               check=False, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""
        return p.returncode, p.stdout.strip()

    rc, mb = git("merge-base", "HEAD", f"origin/{branch}")
    if rc or not mb or git("merge-base", "--is-ancestor", mb, base)[0] == 0:
        return base  # main merged nothing that base does not already hold
    # The local origin/<branch> ref is writable by the session under review: moved
    # to its own HEAD, it would fold the session's work out of its own review. Fold
    # only commits the REMOTE's branch really holds; unconfirmable -> wide surface.
    rc, remote = git("ls-remote", "origin", f"refs/heads/{branch}")
    tip = remote.split()[0] if rc == 0 and remote else ""
    if not tip or git("merge-base", "--is-ancestor", mb, tip)[0] != 0:
        return base
    rc, out = git("merge-tree", "--write-tree", base, mb)
    return out.splitlines()[0] if rc == 0 and out else base


def get_scope(session):
    """The path prefixes a session owns, from its spec's optional `review_scope`.

    A list of repo-relative directories. Absent or empty means the whole tree,
    which is what every plan built before this did.
    """
    raw = (session or {}).get("review_scope") or []
    if isinstance(raw, str):
        raw = [raw]
    import re
    return [re.sub(r"^(\./)+", "", str(x).strip()) for x in raw if str(x).strip()]


def session_spec(plan_dir, session_id):
    """This session's manifest entry, or {} if the manifest cannot be read."""
    try:
        import manifest_io as mio
        return mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id) or {}
    except Exception:                      # noqa: BLE001 — no manifest, no scope
        return {}


def gate_env(plan_dir, session_id, cwd=None):
    """`{BASE_ENV: ..., SCOPE_ENV: ...}` for the review gates — only the keys
    that actually have a value, so an unset one never becomes an empty string
    the gate would have to distinguish from absent.

    Loads the session spec itself so the call site stays one line: verify.py sits
    5 lines under its size baseline, and the wiring must fit inside that.
    """
    # RESOLVED here, in the orchestrator's cwd: the gate runs with cwd = a member or
    # plan worktree, where a relative plan dir names the FROZEN `_plans/` copy. The
    # ledger then landed in the worktree, `apply` committed it, and §8.b refused
    # every later ship of the plan (2026-09-24). Same rule as `plan_ship.git_step`.
    env = {PLAN_DIR_ENV: str(pathlib.Path(plan_dir).resolve()), SESSION_ENV: str(session_id),
           HARNESS_ENV: (land_harness(plan_dir) if session_id == LAND
                         else harness(plan_dir, session_id))}
    base = get_base(plan_dir, session_id, cwd)
    if base:
        env[BASE_ENV] = base
    scope = get_scope(session_spec(plan_dir, session_id))
    if scope:
        env[SCOPE_ENV] = ",".join(scope)
    return env


def declared_env(gate, plan_dir, session_id, cwd=None):
    """`gate_env`, restricted to the keys this gate's registry entry DECLARES in
    `env_allowlist`. The allowlist is a gate's statement of what it is willing to
    receive; the computed values fill those keys and never add new ones.

    Why: handing the review base/scope to EVERY argv gate leaked them into
    `code-review-gate`, whose pytest children call the review gate's main() in
    temp repos -- argparse read the ambient PLAN_EXECUTE_REVIEW_BASE and diffed a
    commit those repos do not have. Green four times run by hand, INDETERMINATE
    on its first run THROUGH verify (2026-08-20).
    """
    allow = set((gate or {}).get("env_allowlist") or [])
    return {k: v for k, v in gate_env(plan_dir, session_id, cwd).items() if k in allow}
