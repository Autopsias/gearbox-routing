"""ISO-02 — WHERE an isolated plan's work happens: dispatch, gates, evidence.

Authority: ``../references/plan-isolation-contract.md`` (v1, frozen 2026-08-21).
``plan_worktree.py`` CREATES the branch and the locked checkout (s05); this module
is what makes every later stage actually use it, and ``plan_ship.py`` is the other
half — HOW the work lands on the branch. Section numbers below are the contract's.

The split is a measured constraint, not taste: this repo's ``pre-commit`` runs
``check_file_sizes.py --staged`` with a 500-LOC default, and the git mechanism
needed room the scoping half was already using.

Two surfaces, one rule each:

* **Dispatch** (§Verdict) — a session prompt carries a preamble naming the plan
  worktree and branch. ADVISORY, exactly like the parallel-group member preamble:
  s06 of the earlier plan MEASURED ``EnterWorktree(path=…)`` being refused from a
  freshly dispatched subagent, so nothing in the harness confines an agent there.
  Saying "contained" would be the lie; the preamble tells, and the ship step
  commits from the worktree either way.

* **Evidence** (§8) — SPLIT, never blanket-redirected. A declared path starting
  with ``_evidence/`` is part of the plan's RECORD and lives in the OUTER plan
  directory (§8.a). Every other relative path is repo-relative and resolves in
  the plan worktree, where the session's code actually is.

NOT OWNED HERE: creation and teardown (``plan_worktree.py``), the git operations
and the shipping step descriptors (``plan_ship.py``), the land protocol §4 (s08),
the lifecycle §15 (s09).
"""

from pathlib import Path

import findings_digest as fd
import group_scope as gs
import plan_version_gate as pvg
import plan_worktree as pwt
import run_state_io as rsi
import worktree as wt

# The plan RECORD's directory, repo-relative. Defined here because both halves of
# ISO-02 need it: `plan_ship` re-exports it for §8.b's staging pathspec.
PLANS_DIR = "_plans"

# --------------------------------------------------------------------------
# HARNESS-01 — Fable 5.1 guide lines, every Claude-harness dispatch
# --------------------------------------------------------------------------
# Anthropic's Fable 5.1 prompting guide names two instructions that change its
# behaviour (evals/routing/external-priors-fable51-astra-2026-09.md, "Fable 5.1
# prompting guide vs this harness"): state the scope and ask for the tests to
# run, and prefer targeted edits over rewrites. A third line points human-facing
# text at the operator's output style, which a dispatched subagent never sees on
# its own — output styles apply to the MAIN conversation only. Concatenated
# separately from `dispatch_preamble` at the run.py Claude-only funnel so it
# lands whether or not a preamble is empty.
CLAUDE_DISPATCH_GUIDANCE = (
    # This exact substring is grepped verbatim against the DEPLOYED file by
    # s03b's evidence gate — keep it on one source line, never re-wrap it.
    "Implement exactly what the session scope asks — no extra features and no speculative refactors. "
    "Before you close, run the tests that cover what you "
    "changed and report the command and its result; never report a test result "
    "from memory.\n\n"
    "Prefer small, targeted edits to the existing code over rewriting a file; "
    "keep the surrounding code and its style intact.\n\n"
    "Any text a person will read — closeout notes, decision briefs, reports — "
    "follows the writing rules in ~/.claude/output-styles/<your-style>.md: the "
    "outcome in the first sentence, plain words, sentences of 25 words or "
    "fewer.\n\n---\n\n"
)

# --------------------------------------------------------------------------
# Where this session's work lives
# --------------------------------------------------------------------------
def project_root(start):
    """The project root containing ``start``, BOUNDED AT THE GIT TOP-LEVEL.

    The unbounded form — walk up until a directory holds ``.claude/`` — is the
    classic worktree trap. A linked worktree's ``.git`` is a gitdir POINTER FILE
    (§12.1), and a plan worktree lives at ``<repo>/.plan-worktrees/<slug>/``,
    i.e. UNDER the primary checkout. So whenever ``.claude/`` is untracked in the
    target repo (it is untracked in most), the walk from a path inside the plan
    worktree finds nothing until it reaches the OUTER checkout, and every
    registry lookup and every relative cwd derived from it silently points back
    at the shared tree the plan exists to stay out of. ``git rev-parse
    --show-toplevel`` reads the pointer file correctly (measured), so
    the top-level is the ceiling.
    """
    start = Path(start).resolve()
    top = wt.repo_root(start)
    ceiling = Path(top).resolve() if top else None
    for up in [start, *start.parents]:
        if (up / ".claude").is_dir():
            return up
        if up == ceiling:
            return ceiling
    if start.parent.name == "_plans":
        return start.parent.parent
    return start.parent


def isolation_honoured(plan_dir):
    """§6.2 — may this plan's worktree be USED, not merely does one exist?

    `--no-isolate` is a flag on `begin` alone; `apply`, verify and shipping never
    see it, so a decision that "wins over everything" won only until the next
    command: an existing worktree still took dispatch, every gate cwd and the
    whole ship step. `begin` now RECORDS the gate's answer (§6, `plan_isolation`
    in run_state), and that record is what every later stage reads.

    With nothing recorded the manifest's own version answers, so a plan
    downgraded below the gate stops routing into a worktree an earlier run made.
    An unreadable manifest leaves the claim honoured — the pre-existing
    behaviour, and the safe direction: the alternative silently dispatches into
    the operator's checkout.
    """
    recorded = (rsi.load_state(plan_dir).get("plan_isolation") or {})
    if isinstance(recorded.get("enabled"), bool):
        return recorded["enabled"]
    try:
        import manifest_io as mio
        return bool(pvg.isolation_enabled(mio.load_manifest(plan_dir))[0])
    except Exception:                      # noqa: BLE001 — no manifest, no gate
        return True


def claim(plan_dir):
    """What this plan says about its own isolation — {} when it says nothing.

    RUNTIME-first (§8.d/§8.e): the claim under ``$GIT_COMMON_DIR/plan-state/`` is
    outside every working tree, so a neighbouring schema-6 plan's repo-wide
    ``git add -A`` cannot sweep a half-written copy of it into a commit, and a
    merge resolving ``--theirs`` cannot delete it. The in-tree
    ``_plan_worktree.json`` is a MIRROR and only the fallback.

    A mirror stamped with a SUCCESSFUL teardown is a RECORD, not a live claim
    (§8.e: "a mirror never decides anything"): ``remove_plan_worktree`` clears
    the runtime claim but keeps the mirror as history, so without this reading
    the fallback would resurrect the claim forever and ``require_live`` could
    never be cleared by the one legitimate action that resolves it. This is
    git's own model — a clean ``worktree remove`` leaves no registration; only
    an OUT-OF-BAND vanishing (no teardown stamp) leaves the claim standing for
    ``require_live`` to refuse on. ``"preserved"`` means the teardown REFUSED
    and the worktree still stands, so the claim stays live.

    §6.2's gate is consulted FIRST: a claim RECORDS that a worktree exists; it is
    not on its own a decision to route work into it.
    """
    if not isolation_honoured(plan_dir):
        return {}
    root = wt.repo_root(plan_dir)
    live = (pwt.read_claim(root, pwt.plan_slug(plan_dir)) if root else None) or {}
    if live:
        return live
    state = pwt.load_state(plan_dir) or {}
    td = state.get("teardown")
    if td and td.get("status") != "preserved":
        return {}
    return state


def plan_worktree(plan_dir):
    """This plan's worktree path, or None when it is not isolated OR the checkout
    is gone. A recorded path whose directory was removed out of band must never
    silently become a cwd — see ``require_live``, which is what turns that into a
    refusal at the two places where it would otherwise write to the shared tree.
    """
    path = claim(plan_dir).get("path")
    return str(path) if path and Path(path).is_dir() else None


def require_live(plan_dir):
    """Refuse when this plan CLAIMS a worktree that is not there.

    Every reader above degrades to "not isolated" on a missing directory, which is
    right for a plan that was never isolated and WRONG for one that was: the
    caller would quietly fall back to the shared checkout — dispatching a session
    into the operator's tree, or committing there. The claim is cleared by
    teardown, so its presence with no directory means the checkout vanished
    between `begin` and now, and that is a stop.
    """
    c = claim(plan_dir)
    if c.get("path") and not Path(c["path"]).is_dir():
        raise wt.WorktreeError(
            f"plan {pwt.plan_slug(plan_dir)} claims the worktree {c['path']} on branch "
            f"{c.get('branch')!r} and that directory is gone. Refusing to fall back to "
            "the shared checkout — that is the collision this isolation exists to "
            "prevent. Re-run `begin` (which re-attaches a vanished worktree), or "
            "retire the plan's isolation state deliberately.")


def plan_branch(plan_dir):
    return claim(plan_dir).get("branch")


def member_branch(plan_dir, group, session_id):
    """The branch a parallel-group MEMBER of this plan works on.

    The sibling of ``plan_branch`` — one resolution every caller shares, so the
    name a session is told, the name that is committed and the name reported
    afterwards cannot drift apart. The slug comes from the group's RECORDED
    state, never from live isolation, so a group pinned before the plan was
    isolated (or after it landed) keeps the name its branch actually has;
    only a group with no state yet falls back to what the plan claims now.
    """
    state = wt.load_state(plan_dir, group)
    slug = state.get("plan_slug") if state else gs.plan_isolation(plan_dir)[1]
    return wt.member_branch(group, session_id, slug)


def pinned_base(plan_dir):
    """The plan branch's PINNED base sha (§1.3), or None. RUNTIME-first, as above."""
    return claim(plan_dir).get("base_ref")


def integration_root(plan_dir, session_id):
    """The tree a group's INTEGRATION session must be gated in, or None.

    ``worktree.merge_group`` merges every member branch into the group state's
    recorded MERGE ROOT. Gating an integration session anywhere else tests a tree
    that never received the merge, which is the M4 carve-out ("the integration
    session, whose gates test the MERGED tree") read from the mechanism instead of
    from prose. Reading the recorded root rather than hard-coding one is what let
    the merge target MOVE — under isolation it is now the plan worktree (V3-2 §3
    rule 5), and this answer followed it with no change here.

    None when the session integrates nothing, or its group was never worktree-
    isolated (then there is no merge, the members worked in the plan worktree, and
    the caller's plan-level answer is the right one).
    """
    try:
        import manifest_io as mio
        spec = mio.session_by_id(mio.load_manifest(plan_dir)).get(session_id) or {}
    except Exception:                      # noqa: BLE001 — no manifest, no carve-out
        return None
    group = wt.integrates_group(spec)
    state = (wt.load_state(plan_dir, group) or {}) if group else None
    root = gs.merge_root(state, plan_dir) if state else None
    return str(root) if root and Path(root).is_dir() else None


def plan_cwd(plan_dir, session_id=None):
    """Where this session's tooling runs: its own member worktree if it has one,
    else the tree its group's merge landed in if it is an integration session,
    else the plan worktree, else None (no isolation — every caller keeps its
    pre-existing behaviour unchanged).

    Member FIRST is deliberate and is the nesting rule of §1.1a read from the
    other end: a group member under an isolated plan has a checkout of its own
    (s07), and its gates must see that, not the plan-level tree its branch was
    cut from. INTEGRATION second, for the mirror-image reason — see
    ``integration_root``.
    """
    if session_id is not None:
        member = wt.member_path(plan_dir, session_id)
        if member:
            return member
        merged = integration_root(plan_dir, session_id)
        if merged:
            return merged
    return plan_worktree(plan_dir)


# --------------------------------------------------------------------------
# Dispatch (§Verdict — ADVISORY containment, said out loud)
# --------------------------------------------------------------------------
def plan_preamble(path, branch, base_ref, plan_dir):
    """The instruction prepended to a session prompt under PLAN isolation.

    Names the checkout and the branch (so an agent can `cd` there) AND the outer
    plan directory (so the plan's RECORD — evidence, closeouts — is written where
    §8.a says the orchestrator reads it). Both halves matter: an agent told only
    about the worktree writes its evidence into the frozen `_plans/` copy, where
    the evidence gate does not look and no commit may stage it (§8.b).
    """
    return (
        "## PLAN ISOLATION — this plan has its own checkout and its own branch\n\n"
        f"Working copy: `{path}`\n"
        f"Branch: `{branch}` (based on `{(base_ref or '')[:12]}`)\n"
        f"Plan directory (OUTER, not the copy inside the worktree): `{plan_dir}`\n\n"
        "`cd` into the working copy first and use ABSOLUTE paths under it. Do NOT edit "
        "the shared repository checkout — the operator and other plans are using it.\n\n"
        "**Write repo files in the worktree; write the plan's own record OUTSIDE it.** "
        f"Evidence paths you declare as `_evidence/...` resolve in `{plan_dir}` and must "
        "be written there. Everything else you declare is repo-relative and resolves in "
        "the working copy. The `_plans/` directory inside the worktree is a FROZEN copy — "
        "never write to it; no commit from here may stage it.\n\n"
        "**Do NOT run `git commit`, `git push`, `git merge` or `git worktree` yourself.** "
        "The orchestrator's shipping step commits this worktree on its own branch once "
        "your closeout is accepted.\n"
    )


def dispatch_preamble(plan_dir, session, session_id, member_path):
    """The preamble for one dispatched session, or "" when nothing is isolated.

    One funnel for both isolation levels so a session can never be told about a
    worktree it is not in: a parallel-group member gets the member preamble it
    has always had; a plain session under an isolated plan gets the plan one.

    A plan that claims a worktree which is gone REFUSES here rather than
    returning "" — an empty preamble would dispatch the session straight at the
    operator's shared checkout with nothing said about it.
    """
    if member_path:
        group = wt.group_of(session)
        state = wt.load_state(plan_dir, group) or {}
        return wt.prompt_preamble(member_path,
                                  wt.member_branch(group, session_id,
                                                   state.get("plan_slug")),
                                  state.get("base_ref", "")) + "\n---\n\n"
    require_live(plan_dir)
    path = plan_worktree(plan_dir)
    if not path:
        return ""
    return plan_preamble(path, plan_branch(plan_dir), pinned_base(plan_dir),
                         Path(plan_dir).resolve()) + "\n---\n\n"


def dispatch_preamble_with_digest(plan_dir, session, session_id, member_path):
    """`dispatch_preamble`, with the cross-session findings digest
    (`findings_digest.digest`) prepended and the standing dispatch guidance
    appended -- the exact prefix run.py's Claude funnel puts in front of every
    dispatched prompt. Lives here (not in run.py) so that file, which sits on a
    hard LOC ratchet, never needs its own `findings_digest` import."""
    return (fd.digest(plan_dir, session_id)
            + dispatch_preamble(plan_dir, session, session_id, member_path)
            + CLAUDE_DISPATCH_GUIDANCE)


# --------------------------------------------------------------------------
# Evidence + closeout paths (§8 — SPLIT, not redirected)
# --------------------------------------------------------------------------
def resolve_evidence_path(plan_dir, session_id, raw):
    """Where a declared evidence path actually lives, under plan isolation.

    Absolute paths and parallel-group members keep ``worktree.resolve_evidence_path``
    unchanged — that surface was patched (7d5632a) and this must not
    regress it. For a plain session under an isolated plan, §8 splits:

      * ``_evidence/...``      -> the OUTER plan directory (§8.a: RECORD)
      * ``_plans/<any>/...``   -> the OUTER checkout (§8.a: the same RECORD, spelled
        repo-relative — which is how a manifest actually declares it: this plan's
        own manifest says ``_plans/example-isolation-plan-2026-08-20/_evidence/s02``)
      * anything else          -> repo-relative, inside the plan worktree

    Both RECORD spellings are resolved in the OUTER checkout and NOWHERE ELSE —
    no worktree fallback. That is a rule, not a preference, and the
    difference is measurable: with the worktree merely searched second, a
    "first existing root wins" chain returns the outer copy whether the split is
    implemented or not, so disabling the split changes no observable behaviour
    and the check for it cannot fail. It is also the safer semantics — a copy
    found inside the worktree is by construction the FROZEN one checked out at
    the pinned base (§8.a), and accepting it would be an artifact from an earlier
    state accepted as proof about the current one.

    A repo-relative path is resolved in the worktree first; the outer roots stay
    as fallbacks, because a plan may legitimately declare an artifact the
    orchestrator wrote (a registry file, a doc under the primary checkout).
    """
    path = Path(raw)
    if path.is_absolute() or wt.member_path(plan_dir, session_id):
        return wt.resolve_evidence_path(plan_dir, session_id, raw)
    tree = plan_worktree(plan_dir)
    if not tree:
        return wt.resolve_evidence_path(plan_dir, session_id, raw)
    if path.parts[:1] == ("_evidence",):
        return Path(plan_dir) / path
    if path.parts[:1] == (PLANS_DIR,):
        return (wt.repo_root(plan_dir) or Path(plan_dir).parents[1]) / path
    roots = [Path(tree), Path(plan_dir), wt.repo_root(plan_dir) or Path.cwd(), Path.cwd()]
    for r in roots:
        if r and (r / path).exists():
            return r / path
    return roots[0] / path


def already_under(declared, path):
    """Is `declared` already inside `path`?

    Absolute paths only: a relative cwd is repo-relative by definition and has
    not been mapped into a worktree yet. Lives here rather than in `verify.py`
    because it answers a question about the tree `plan_cwd` just chose, and
    because both callers of one gate registry entry must agree on it — mapping a
    cwd is NOT idempotent, so the second caller has to be able to tell that the
    first already did it.
    """
    p = Path(declared)
    if not p.is_absolute():
        return False
    try:
        p.resolve().relative_to(Path(path).resolve())
    except ValueError:
        return False
    return True


def gate_cwd(plan_dir, session_id, gate):
    """Where an argv gate runs, from ``plan_scope.plan_cwd``: the session's member
    worktree, else — for a group's integration session — the tree that group's
    merge actually landed in, else its PLAN's worktree (ISO-02), else project cwd.

    Contract M4: a per-member gate runs INSIDE that member's worktree — deriving a
    file set from the working tree would otherwise test its peers' half-finished
    edits — and the integration session's gates test the MERGED tree, which plan
    isolation must not pull into a worktree the merge never reached. The gate's
    relative position is preserved: ``cwd: "backend"`` -> ``<worktree>/backend``.
    """
    declared = gate.get("cwd")
    path = plan_cwd(plan_dir, session_id)
    if not path:
        return declared
    if not declared:
        # A skill-kind gate carries no `cwd` (``shipping._resolve_gate``), so its
        # implicit location is the project root — whose counterpart under isolation
        # is the worktree root. None here would send every skill gate at the shared
        # tree, the one thing M4 forbids.
        return path
    if already_under(declared, path):
        # `resolve_gate` maps the cwd itself now, so a gate from `compute_gates`
        # is ALREADY inside `path` — and mapping twice DOUBLES the prefix when
        # the sub-directory is missing in the worktree (`repo_root` fails, the
        # fallback is the outer root). Checked before `repo_root` for that reason.
        return declared
    root = wt.repo_root(declared) or wt.repo_root(plan_dir)
    if root is None:
        return declared
    try:
        rel = Path(declared).resolve().relative_to(Path(root).resolve())
    except ValueError:
        # Gate cwd lives outside the repository — a worktree has no counterpart
        # for it, so leave it alone rather than invent one.
        return declared
    return str(Path(path) / rel)
