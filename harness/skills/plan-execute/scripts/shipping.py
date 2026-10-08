"""Post-session shipping state machine (the deterministic core).

The ORCHESTRATOR (Claude, following SKILL.md) drives this between Task
dispatches, exactly like the closeout pipeline. A plain Python process cannot
call the Skill tool, so for ``skill``-kind sub-steps this module returns a
DIRECTIVE ("invoke /commit-orchestrate now") and the orchestrator runs it via
the Skill tool, then records the outcome back. ``argv``-kind sub-steps
(``deploy_argv`` / argv registry targets+gates) are executed here directly.

Ordering of the safety gates (the adversarial hardenings):

  Step 0  checkpoint precedence — a human checkpoint ALWAYS outranks shipping.
  Step 1  acquire resource LEASES first (before the idempotency read — closes the
          read-then-lock TOCTOU). ``git:``/``push:`` leases are repo-scoped, so a
          second plan in the same checkout genuinely waits; ownership is a token
          with an explicit expiry, because run.py exits before the skill it
          authorised runs (ship_state_io.py has the measurements).
  Step 2  idempotency read + digest reconciliation — a manifest/closeout digest
          mismatch refuses as ``state-drift`` rather than skipping as shipped.
  Step 3  runtime guard (deploy target re-resolve) + execution-time adapter
          probe + deploy-authorization staleness gate.
  Step 4  ordered sub-steps with running->done/failed durable writes + redaction.
  Step 5  finalize (release locks).

Orchestrator protocol (run.py subcommands):
  ship-begin   -> first directive | noop | deferred | already-shipped | skipped
                  | failed | confirm-required
  ship-record  -> record a skill step's outcome, return next directive. Re-verifies
                  the lease first: a step whose lease was taken over or expired
                  mid-directive REFUSES (`reason: lease-lost`) with a decision
                  brief instead of recording anything.
  ship-run     -> run an argv step here, return next directive
  ship-finalize / ship-release -> finalize / release locks
  ship-status  -> read-only computed plan + state (no lock; dashboard/dry-run)
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import gate_expect as gx
import manifest_io as mio
import plan_scope as pscope
import plan_ship as pship
import run_state_io as rsi
import ship_state_io as ssio
import shipping_adapter as adapter

# Moved to ship_badges.py (size-ratchet extraction, s06); re-exported for callers.
from ship_badges import (  # noqa: F401, E402
    STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED,
    mark_done, shipping_badge, shipping_summary,
)


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Project-local registry resolution (deploy targets + eval gates)
# --------------------------------------------------------------------------
def find_project_root(plan_dir):
    # One definition, in plan_scope: the walk must stop at the git top-level or
    # it climbs out of a plan worktree back into the shared checkout (ISO-02).
    return pscope.project_root(plan_dir)


def _bundled_default(name):
    return Path(__file__).resolve().parent.parent / "references" / name


def _load_registry(plan_dir, project_filename, bundled_filename):
    """Project-local registry merged over skill-bundled defaults (project wins)."""
    merged = {}
    bundled = _bundled_default(bundled_filename)
    if bundled.is_file():
        try:
            merged.update(json.loads(bundled.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    local = find_project_root(plan_dir) / ".claude" / project_filename
    if local.is_file():
        try:
            merged.update(json.loads(local.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def deploy_targets(plan_dir):
    return _load_registry(plan_dir, "deploy-targets.json", "deploy-targets.default.json")


def eval_gates(plan_dir):
    return _load_registry(plan_dir, "eval-gates.json", "eval-gates.default.json")


# --------------------------------------------------------------------------
# Step planning
# --------------------------------------------------------------------------
class StepResolveError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _resolved_post_session(manifest, session_id):
    s = mio.session_by_id(manifest).get(session_id)
    if not s:
        return None
    return s.get("post_session")


def compute_steps(plan_dir, manifest, session_id):
    """Ordered step descriptors for a session's resolved ``post_session`` block.

    Each descriptor: ``{name, kind, resource, ...}``. ``name`` is the durable
    state key (``commit``/``push``/``pr``/``gate:<id>``/``deploy``). For
    ``skill`` kind: ``{skill, args, probe_flags, timeout}``. For ``argv`` kind:
    ``{argv, cwd, env_allowlist, timeout, failure_mode, fixture_fake}``.

    Raises ``adapter.AdapterError`` / ``StepResolveError`` on bad config — and on
    a vanished plan worktree — so the caller can halt with a precise reason."""
    ps = _resolved_post_session(manifest, session_id)
    if not ps:
        return []
    steps = []
    try:                        # A plan that claims a worktree which is GONE refuses —
        for sub in adapter.git_substeps(ps.get("git", "none")):    # in `git_step`, and
            steps.append(pship.git_step(plan_dir, sub,             # in `_resolve_cwd`
                                        _git_resource(sub, plan_dir), session_id))
        for gate_id in ps.get("pre_deploy_gates", []) or []:       # for gates/deploy.
            steps.append(_resolve_gate(plan_dir, gate_id, session_id))
        deploy_step = _resolve_deploy(plan_dir, ps, session_id)
    except pship.wt.WorktreeError as e:      # caller of compute_steps handles this ONE
        raise StepResolveError("plan-worktree-missing", str(e)) from e   # family, so it
    # halts here instead of surfacing as a traceback that also discards `apply`'s output.
    if deploy_step:
        steps.append(deploy_step)
    return steps


def _git_resource(sub, plan_dir):
    root = find_project_root(plan_dir)
    return f"push:{root}" if sub == "push" else f"git:{root}"


def _resolve_gate(plan_dir, gate_id, session_id=None):
    g = eval_gates(plan_dir).get(gate_id)
    if not g:
        raise StepResolveError("gate-missing", f"gate {gate_id!r} not in eval-gates registry")
    return gate_descriptor(gate_id, g, lambda: _resolve_cwd(plan_dir, g.get("cwd"), session_id))


def gate_descriptor(gate_id, g, cwd):  # cwd: a thunk, argv only. Shared with land_at_land.
    name = f"gate:{gate_id}"
    if g.get("kind") == "skill":
        return {"name": name, "kind": "skill", "resource": name, "skill": g["skill"],
                "args": g.get("args", ""), "probe_flags": g.get("probe_flags", []),
                "timeout": g.get("timeout", 1200), "fixture_fake": g.get("fixture_fake")}
    return {"name": name, "kind": "argv", "resource": name, "argv": g.get("argv", []),
            "cwd": cwd(), "env_allowlist": g.get("env_allowlist", []),
            "timeout": g.get("timeout", 1200), "failure_mode": "fail-halt",
            "fixture_fake": g.get("fixture_fake"), "indeterminate_exit": g.get("indeterminate_exit"),
            "expect": g.get("expect"), "land_covered_by": g.get("land_covered_by")}


# Public alias: session verify gates (verify.py) resolve through the SAME registry
# entry as a pre_deploy gate of the same id, so the two never diverge. Translates
# `_resolve_cwd`'s vanished-worktree refusal for callers outside compute_steps.
#
# `session_id` is REQUIRED for that promise to hold. Without it `plan_cwd` cannot
# see a member worktree or a group's merge target, so the same registry entry
# resolved by `verify.compute_gates` (which passes it) and by a `pre_deploy_gates`
# step (which did not) landed in DIFFERENT trees for a group member and for an
# integration session -- the integration gate testing the plan worktree the merge
# never reached, and passing vacuously. Defaulted rather than positional so a
# non-session caller keeps working; every in-tree caller passes it.
def resolve_gate(plan_dir, gate_id, session_id=None):
    try:
        return _resolve_gate(plan_dir, gate_id, session_id)
    except pship.wt.WorktreeError as e:
        raise StepResolveError("plan-worktree-missing", str(e)) from e


def _resolve_deploy(plan_dir, ps, session_id=None):
    failure_mode = ps.get("command_failure_mode", "fail-halt")
    argv = ps.get("deploy_argv")
    if argv:
        return {"name": "deploy", "kind": "argv", "resource": "deploy:argv", "argv": argv,
                "cwd": _resolve_cwd(plan_dir, None, session_id),
                "env_allowlist": ps.get("env_allowlist", []),
                "timeout": _deploy_timeout(failure_mode), "failure_mode": failure_mode}
    target = ps.get("deploy", "none")
    if not target or target == "none":
        return None
    t = deploy_targets(plan_dir).get(target)
    if not t:
        raise StepResolveError("deploy-target-missing",
                               f"deploy target {target!r} not in deploy-targets registry")
    if t.get("kind") == "skill":
        return {"name": "deploy", "kind": "skill", "resource": f"deploy:{target}", "target": target,
                "skill": t["skill"], "args": t.get("args", ""),
                "probe_flags": t.get("probe_flags", []), "timeout": t.get("timeout", 1800)}
    return {"name": "deploy", "kind": "argv", "resource": f"deploy:{target}", "target": target,
            "argv": t.get("argv", []), "cwd": _resolve_cwd(plan_dir, t.get("cwd"), session_id),
            "env_allowlist": t.get("env_allowlist", []),
            "timeout": t.get("timeout", _deploy_timeout(failure_mode)),
            "failure_mode": t.get("command_failure_mode", failure_mode),
            "fixture_fake": t.get("fixture_fake")}


def _deploy_timeout(failure_mode):
    return 10 if failure_mode == "best-effort" else 1800


def _resolve_cwd(plan_dir, cwd, session_id=None):
    # ISO-02: under plan isolation the root for a gate/deploy cwd is the tree
    # THAT SESSION's work is actually in — the only one holding its commits.
    # Rooted in the outer checkout, a `pre_deploy_gates` gate tests a tree the
    # work never reached and passes without seeing it.
    #
    # `session_id` is what makes that true for the two nested cases, and passing
    # it is the whole point: `plan_cwd` resolves a group MEMBER to its own
    # worktree and a group's INTEGRATION session to the tree `merge_group`
    # merged into — neither of which is the plan worktree. Resolved without it
    # (as this did until 2026-08-22) an integration session's pre_deploy gate
    # ran in the plan worktree, which never received the merge, while
    # `verify.gate_cwd` — passing session_id — ran the SAME registry entry in
    # the merge target. Same id, two trees, and the gate that saw neither the
    # merge nor the members passed anyway.
    #
    # A plan that CLAIMS a worktree which is gone refuses (`require_live`)
    # instead of silently degrading to the shared tree; compute_steps and
    # compute_gates translate that refusal into `plan-worktree-missing`.
    pscope.require_live(plan_dir)
    root = Path(pscope.plan_cwd(plan_dir, session_id) or find_project_root(plan_dir))
    if not cwd or cwd == ".":
        return str(root)
    p = Path(cwd)
    return str(p if p.is_absolute() else (root / p))


def _resolve_deploy_present(ps):
    return bool(ps.get("deploy_argv")) or (ps.get("deploy", "none") not in (None, "none"))


# --------------------------------------------------------------------------
# Auth-staleness digest (deploy authorization freshness gate)
# --------------------------------------------------------------------------
def _transitive_upstream(manifest, session_id):
    by_id = mio.session_by_id(manifest)
    seen, stack = set(), [session_id]
    while stack:
        sid = stack.pop()
        for dep in by_id.get(sid, {}).get("dispatch", {}).get("depends_on", []) or []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def runtime_auth_digest(plan_dir, manifest, session_id):
    """sha256 over the *effective* shipped item set: this session's declared
    items + each transitive-upstream session's ACTUALLY-completed items (from
    closeouts). Compared against the build-time ``deploy_auth_digest`` (declared
    items). A divergence means an intervening session shipped a different set ->
    downgrade pre-authorization to surface-and-confirm."""
    import hashlib

    by_id = mio.session_by_id(manifest)
    items = set(by_id.get(session_id, {}).get("items", []))
    for up in _transitive_upstream(manifest, session_id):
        co = cp.load_closeout(plan_dir, up)
        items.update(co.get("items_completed", []) if co else by_id.get(up, {}).get("items", []))
    blob = json.dumps(sorted(items), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Checkpoint precedence (Step 0)
# --------------------------------------------------------------------------
def checkpoint_pending(manifest, session_id, plan_dir):
    s = mio.session_by_id(manifest).get(session_id, {})
    ps = s.get("post_session") or {}
    if s.get("dispatch", {}).get("requires_human_checkpoint"):
        return "dispatch.requires_human_checkpoint"
    if ps.get("require_human_checkpoint"):
        return "phase_closer.require_human_checkpoint"
    co = cp.load_closeout(plan_dir, session_id)
    if co and co.get("human_checkpoint_reason"):
        return "closeout.human_checkpoint_reason"
    return None


# --------------------------------------------------------------------------
# State helpers
# --------------------------------------------------------------------------
def _current_closeout_digest(plan_dir, session_id):
    co = cp.load_closeout(plan_dir, session_id)
    return co.get("_closeout_digest") if co else None


def _new_state(plan_dir, manifest, session_id, steps, ps, result):
    return {
        "session_id": session_id,
        "manifest_digest": mio.manifest_digest(plan_dir),
        "closeout_digest": _current_closeout_digest(plan_dir, session_id),
        "result": result,
        "declared_steps": [s["name"] for s in steps],
        "steps": {s["name"]: STEP_PENDING for s in steps},
        "command_failure_mode": ps.get("command_failure_mode", "fail-halt"),
        "rollback_hint": ps.get("rollback_hint"),
        "started_at": _now(),
        "updated_at": _now(),
        "failures": {},
    }


def _persist_state(plan_dir, session_id, state):
    state["updated_at"] = _now()
    ssio.save_ship_state(plan_dir, session_id, state)
    # Ordering invariant: the dashboard reflects shipping state no later than the
    # corresponding run.ndjson event — update the badge right after the durable
    # write, best-effort (a stale badge must never imply "shipped").
    try:
        ab.update_shipping_badge(Path(plan_dir) / "PLAN.html", session_id, shipping_badge(state))
    except Exception as e:  # noqa: BLE001 — badge is advisory; state is source of truth
        rsi.log_event(plan_dir, "shipping_badge_failed", session_ids=[session_id], error=str(e))


def _first_unfinished(state):
    for name in state["declared_steps"]:
        if state["steps"].get(name) not in (STEP_DONE, STEP_SKIPPED):
            return name
    return None


def _all_done(state):
    return all(state["steps"].get(n) in (STEP_DONE, STEP_SKIPPED) for n in state["declared_steps"])


# --------------------------------------------------------------------------
# ship-begin (Steps 0-3)
# --------------------------------------------------------------------------
def _resume_ship_state(state, cur_manifest, cur_closeout, plan_dir, session_id,
                       dry_run, acquired):
    """Recorded shipping state, judged for resume.

    Returns an action dict when the run must stop here — the digests drifted, or
    everything is already shipped — and None to carry on with the existing state.
    """
    if (state.get("manifest_digest") != cur_manifest
            or state.get("closeout_digest") != cur_closeout):
        return _fail(plan_dir, session_id, "state-drift",
                     "manifest/closeout digest changed since shipping state was recorded; "
                     "refusing to resume (a rebuilt plan must not skip as already-shipped)",
                     dry_run, halt=True, locks=acquired)
    if _all_done(state):
        _release(plan_dir, acquired)
        return {"action": "already-shipped", "session": session_id}
    return None


def _ship_deferral(plan_dir, manifest, session_id, ps, result, resume, dry_run):
    """The two reasons shipping stands down before it starts, or None to proceed.

    A pending checkpoint always outranks shipping, and skip_if_partial skips the
    whole session when the work is not DONE.
    """
    # Step 0 — checkpoint precedence (always outranks shipping).
    cp_reason = checkpoint_pending(manifest, session_id, plan_dir)
    if cp_reason and not resume:
        if not dry_run:
            rsi.log_event(plan_dir, "post_session_deferred", session_ids=[session_id],
                          reason=cp_reason)
        return {"action": "deferred", "session": session_id, "reason": f"checkpoint:{cp_reason}"}

    # skip_if_partial: whole-session skip when the work isn't DONE.
    if ps.get("skip_if_partial") and result is not None and result != "DONE":
        if not dry_run:
            rsi.log_event(plan_dir, "post_session_skipped", session_ids=[session_id],
                          reason="skip-if-partial")
        return {"action": "skipped", "session": session_id, "reason": "skip-if-partial"}
    return None


def _lease_budget(steps):
    """``{resource: longest step timeout it guards}`` — in declared order, so the
    acquire order stays deterministic. The lease duration is derived from this
    (ship_state_io.LEASE_RATIONALE): nothing renews a lease while the guarded
    skill runs, because run.py has exited by then."""
    budget = {}
    for s in steps:
        res = s["resource"]
        budget[res] = max(budget.get(res, 0), s.get("timeout") or 0)
    return budget


def ship_begin(plan_dir, session_id, *, dry_run=False, resume=False, confirm_stale=False):
    manifest = mio.load_manifest(plan_dir)
    ps = _resolved_post_session(manifest, session_id)
    if not ps or (ps.get("git", "none") == "none" and not ps.get("pre_deploy_gates")
                  and not _resolve_deploy_present(ps)):
        return {"action": "noop", "session": session_id}

    co = cp.load_closeout(plan_dir, session_id)
    result = co.get("result") if co else None

    deferral = _ship_deferral(plan_dir, manifest, session_id, ps, result, resume, dry_run)
    if deferral is not None:
        return deferral

    try:
        steps = compute_steps(plan_dir, manifest, session_id)
    except (StepResolveError, adapter.AdapterError) as e:
        return _fail(plan_dir, session_id, getattr(e, "reason", "step-resolve-error"), str(e),
                     dry_run, halt=True)

    if dry_run:
        return {"action": "dry-run", "session": session_id, "result": result,
                "steps": [_describe(s) for s in steps]}

    # Step 1 — acquire leases FIRST (before the idempotency read; closes TOCTOU).
    acquired = []
    try:
        for res, guarded in _lease_budget(steps).items():
            ssio.acquire_ship_lock(plan_dir, res, guarded_timeout=guarded)
            acquired.append(res)
    except rsi.LockError as e:
        _release(plan_dir, acquired)
        return {"action": "locked", "session": session_id, "reason": str(e)}

    # Step 2 — idempotency read + digest reconciliation.
    try:
        state = ssio.load_ship_state(plan_dir, session_id)
    except ssio.ShipStateError as e:
        return _fail(plan_dir, session_id, "state-corrupt", str(e), dry_run, halt=True, locks=acquired)

    cur_manifest = mio.manifest_digest(plan_dir)
    cur_closeout = _current_closeout_digest(plan_dir, session_id)
    if state is not None:
        early = _resume_ship_state(state, cur_manifest, cur_closeout,
                                   plan_dir, session_id, dry_run, acquired)
        if early is not None:
            return early
    else:
        state = _new_state(plan_dir, manifest, session_id, steps, ps, result)
        _persist_state(plan_dir, session_id, state)

    # Step 3 — execution-time adapter probe + deploy guard + staleness gate.
    guard = _runtime_guard(plan_dir, manifest, session_id, steps, ps, resume, confirm_stale)
    if guard is not None:
        return _fail(plan_dir, session_id, guard["reason"], guard["message"], dry_run,
                     halt=True, locks=acquired, extra=guard.get("extra"))

    rsi.log_event(plan_dir, "post_session_started", session_ids=[session_id],
                  declared_steps=state["declared_steps"])
    return _advance(plan_dir, session_id, steps, state)


def _skill_probe_roots(plan_dir):
    """Project-local search roots so project commands (e.g. /eval, /deploy-update)
    resolve in the adapter probe, not just global ~/.claude ones."""
    root = find_project_root(plan_dir)
    return [str(root / ".claude" / "commands"), str(root / ".claude" / "skills")]


def _runtime_guard(plan_dir, manifest, session_id, steps, ps, resume, confirm_stale):
    # Execution-time adapter probe for every skill step (vendor_change premortem).
    extra_roots = _skill_probe_roots(plan_dir)
    for s in steps:
        if s["kind"] == "skill" and s.get("probe_flags"):
            res = adapter.probe_skill_flags(s["skill"], s["probe_flags"], extra_roots)
            if not res["ok"]:
                return {"reason": "adapter-contract-drift",
                        "message": f"skill {s['skill']!r} missing {res['missing']} "
                                   f"(located: {res['located']})"}
    # Deploy staleness gate (downgrade pre-authorization to confirm on drift).
    deploy = next((s for s in steps if s["name"] == "deploy"), None)
    if deploy and ps.get("deploy_auth_digest") and not (resume or confirm_stale):
        if runtime_auth_digest(plan_dir, manifest, session_id) != ps["deploy_auth_digest"]:
            return {"reason": "deploy-auth-stale",
                    "message": "deploy authorization is stale — an intervening session changed "
                               "what this deploy ships; surface and confirm before deploying",
                    "extra": {"rollback_hint": ps.get("rollback_hint")}}
    return None


# --------------------------------------------------------------------------
# Advance / record / run-argv / finalize
# --------------------------------------------------------------------------
def _describe(step):
    out = {"name": step["name"], "kind": step["kind"]}
    if step["kind"] == "skill":
        out.update({"skill": step["skill"], "args": step.get("args", "")})
    else:
        out.update({"argv": step.get("argv"), "cwd": step.get("cwd"),
                    "failure_mode": step.get("failure_mode")})
    return out


def _step_by_name(steps, name):
    return next((s for s in steps if s["name"] == name), None)


def _advance(plan_dir, session_id, steps, state):
    """Return the next directive: a skill step to invoke, an argv step to run,
    or done. Marks a skill step ``running`` before handing it to the orchestrator
    so a crash mid-skill is distinguishable from never-started."""
    nxt = _first_unfinished(state)
    if nxt is None:
        return {"action": "done", "session": session_id}
    step = _step_by_name(steps, nxt)
    if step["kind"] == "skill":
        state["steps"][nxt] = STEP_RUNNING
        _persist_state(plan_dir, session_id, state)
        out = {"action": "invoke-skill", "session": session_id, "step": nxt, "kind": "skill",
               "skill": step["skill"], "args": step.get("args", ""),
               "timeout": step.get("timeout")}
        out.update(_lease_fields(plan_dir, step))
        return out
    return {"action": "run-argv", "session": session_id, "step": nxt, "kind": "argv",
            **_lease_fields(plan_dir, step)}


def _lease_fields(plan_dir, step):
    """The lease this directive runs under, echoed to the orchestrator so it can
    hand the token straight back on ``ship-record --lease-token``. The skill
    itself (e.g. /commit-orchestrate) needs no knowledge of it: both ends of the
    round trip are run.py, and ship-record re-verifies against run_state."""
    lease = ssio.our_lease(plan_dir, step["resource"]) or {}
    return {"resource": step["resource"], "lease_token": lease.get("token"),
            "lease_expires_at": lease.get("expires_at")}


def _reload(plan_dir, session_id):
    manifest = mio.load_manifest(plan_dir)
    return manifest, compute_steps(plan_dir, manifest, session_id), \
        ssio.load_ship_state(plan_dir, session_id)


_LEASE_OK = ("held", "unrecorded")


def _reload_failed(plan_dir, session_id, e):
    """Mid-loop step resolution failed — most concretely, the plan worktree
    vanished BETWEEN ship-begin and this step. A traceback here would leave the
    lease held with no halt recorded (the finding this fixes), so: release every
    lease this plan holds (only ours — token-checked), then halt with the same
    precise reason ship_begin gives. Resume goes back through ship-begin, which
    re-acquires."""
    ssio.release_all_ship_locks(plan_dir)
    return _fail(plan_dir, session_id, getattr(e, "reason", "step-resolve-error"),
                 str(e), False, halt=True)


def _refuse_if_lease_lost(plan_dir, session_id, steps, step, lease_token=None):
    """Re-verify the lease before recording a step, or refuse and park.

    Completing a directive under a lease you no longer hold is the corruption the
    lease exists to prevent: another plan took the repo over while /commit-
    orchestrate was still running here, and recording ``done`` would write
    "shipped" over a tree somebody else was moving. So a lost/expired lease
    NEVER records an outcome — it halts with a decision brief and leaves the step
    exactly as it was, for a human to judge."""
    sd = _step_by_name(steps, step)
    if sd is None:
        return None
    lease = ssio.lease_status(plan_dir, sd["resource"])
    status = lease["status"]
    if status == "unrecorded":
        # A plan that began shipping before leases existed. The lock file is
        # still exclusive; only the token round-trip is missing, so proceed —
        # loudly, so the gap is visible in run.ndjson rather than assumed away.
        rsi.log_event(plan_dir, "shipping_lease_unrecorded", session_ids=[session_id],
                      step=step, resource=sd["resource"])
        return None
    if status == "held" and lease_token and lease_token != lease.get("our_token"):
        status = "token-mismatch"
    if status in _LEASE_OK:
        return None
    return _park_lease_lost(plan_dir, session_id, step, lease, status)


def _park_lease_lost(plan_dir, session_id, step, lease, status):
    holder = lease.get("holder") or {}
    why = {
        "taken-over": (f"another plan took this lease over after it expired "
                       f"(now held by {holder.get('plan_dir')} on {holder.get('host')})"),
        "expired": (f"this lease expired at {lease.get('expires_at')} while the step was "
                    f"running, so any other plan was free to take the repo"),
        "vanished": f"the lock file {lease.get('lock_file')} is gone — someone removed it",
        "token-mismatch": ("the token on this directive is not the lease this plan holds — "
                           "the directive being recorded is not the one that is in flight"),
    }[status]
    message = (f"refusing to record {step!r}: {why}. The step is left untouched; nothing "
               f"was marked shipped.")
    brief = {
        "situation": (f"Session {session_id} finished the {step!r} step, but this plan no "
                      f"longer holds the {lease['resource']} lease: {why}."),
        "options": [
            "Check the repository state by hand (git log/status), then re-run "
            "`run.py ship-begin --resume` to re-take the lease and redo the step.",
            "If the other plan's work already landed what this step would have done, "
            "clear the halt and mark this session's shipping complete deliberately.",
            "Raise the lease duration for this resource if the operation legitimately "
            "needs longer than its lease (ship_state_io.LEASE_* constants).",
        ],
        "recommendation": ("Look at git log first. Two plans may have written the same "
                           "checkout while this lease was expired, and only the repository "
                           "itself can say what actually landed."),
    }
    detail = "\n".join([brief["situation"], "", "Options:",
                        *(f"  {i}. {o}" for i, o in enumerate(brief["options"], 1)),
                        "", f"Recommendation: {brief['recommendation']}"])
    rsi.log_event(plan_dir, "shipping_lease_lost", session_ids=[session_id], step=step,
                  resource=lease["resource"], lease_status=status,
                  holder_plan=holder.get("plan_dir"), holder_token=holder.get("token"))
    rsi.set_halt(plan_dir, f"{session_id}: shipping lease lost at {step} ({status})",
                 session_id, detail=detail)
    return {"action": "failed", "session": session_id, "reason": "lease-lost",
            "lease_status": status, "step": step, "resource": lease["resource"],
            "message": message, "decision_brief": brief, "resumable": True}


def ship_record(plan_dir, session_id, step, status, result_file=None, lease_token=None):
    """Record a SKILL step's outcome (orchestrator already invoked it)."""
    try:
        _manifest, steps, state = _reload(plan_dir, session_id)
    except (StepResolveError, adapter.AdapterError) as e:
        return _reload_failed(plan_dir, session_id, e)
    if state is None:
        return _fail(plan_dir, session_id, "no-shipping-state",
                     "ship-record with no shipping state", False, halt=True)
    refusal = _refuse_if_lease_lost(plan_dir, session_id, steps, step, lease_token)
    if refusal is not None:
        return refusal
    if status == "done":
        mark_done(state, step)
        _persist_state(plan_dir, session_id, state)
        return _post_step(plan_dir, session_id, steps, state)
    excerpt = ""
    if result_file and Path(result_file).exists():
        excerpt = adapter.redact(Path(result_file).read_text(errors="replace"))
    return _record_failure(plan_dir, session_id, state, step, excerpt)


def ship_run_argv(plan_dir, session_id, step):
    """Run an ARGV step here (deploy_argv / argv registry target or gate)."""
    try:
        _manifest, steps, state = _reload(plan_dir, session_id)
    except (StepResolveError, adapter.AdapterError) as e:
        return _reload_failed(plan_dir, session_id, e)
    if state is None:
        return _fail(plan_dir, session_id, "no-shipping-state",
                     "ship-run with no shipping state", False, halt=True)
    sd = _step_by_name(steps, step)
    if sd is None or sd["kind"] != "argv":
        return _fail(plan_dir, session_id, "not-argv-step", f"{step!r} is not an argv step",
                     False, halt=True)
    refusal = _refuse_if_lease_lost(plan_dir, session_id, steps, step)
    if refusal is not None:
        return refusal

    state["steps"][step] = STEP_RUNNING
    _persist_state(plan_dir, session_id, state)

    fake = sd.get("fixture_fake")
    if fake is not None:
        res = {"returncode": fake.get("returncode", 0), "stdout": fake.get("stdout", ""),
               "stderr": fake.get("stderr", ""), "timed_out": False}
    else:
        res = ssio.run_deploy_argv(sd["argv"], cwd=sd["cwd"],
                                   env_allowlist=sd.get("env_allowlist"),
                                   timeout=sd.get("timeout", 1800))

    failure_mode = sd.get("failure_mode", "fail-halt")
    miss = gx.gate_miss(sd, res)
    ok = (res.get("returncode") == 0 and miss is None) or failure_mode == "best-effort"
    if ok:
        mark_done(state, step)
        _persist_state(plan_dir, session_id, state)
        return _post_step(plan_dir, session_id, steps, state)
    excerpt = gx.expect_excerpt(step, miss, adapter.redact(
        (res.get("stderr") or "") + "\n" + (res.get("stdout") or "")))
    return _record_failure(plan_dir, session_id, state, step, excerpt)


def _post_step(plan_dir, session_id, steps, state):
    if _all_done(state):
        return ship_finalize(plan_dir, session_id, _state=state)
    return _advance(plan_dir, session_id, steps, state)


def _record_failure(plan_dir, session_id, state, step, excerpt):
    state["steps"][step] = STEP_FAILED
    state["failures"][step] = {"stderr_excerpt": excerpt, "at": _now()}
    _persist_state(plan_dir, session_id, state)
    rsi.log_event(plan_dir, "post_session_failed", session_ids=[session_id], failed_step=step,
                  stderr_excerpt=excerpt, resumable=True)
    rsi.set_halt(plan_dir, f"{session_id}: shipping failed at {step}", session_id)
    return {"action": "failed", "session": session_id, "failed_step": step,
            "stderr_excerpt": excerpt, "resumable": True}


def ship_finalize(plan_dir, session_id, _state=None):
    state = _state or ssio.load_ship_state(plan_dir, session_id)
    durations = None
    if state and state.get("started_at"):
        try:
            started = datetime.fromisoformat(state["started_at"])
            durations = int((datetime.now(UTC) - started).total_seconds() * 1000)
        except ValueError:
            durations = None
    if state:
        state["replayed_at"] = _now()
        _persist_state(plan_dir, session_id, state)
    rsi.log_event(plan_dir, "post_session_completed", session_ids=[session_id],
                  durations_ms=durations)
    ssio.release_all_ship_locks(plan_dir)
    return {"action": "done", "session": session_id, "durations_ms": durations}


def ship_release(plan_dir):
    ssio.release_all_ship_locks(plan_dir)
    return {"action": "released"}


def _release(plan_dir, locks):
    for res in locks or []:
        ssio.release_ship_lock(plan_dir, res)


def _fail(plan_dir, session_id, reason, message, dry_run, *, halt=False, locks=None, extra=None):
    _release(plan_dir, locks)
    if not dry_run:
        if reason in ("deploy-target-missing", "gate-missing", "skip-if-partial"):
            rsi.log_event(plan_dir, "post_session_skipped", session_ids=[session_id], reason=reason)
        else:
            rsi.log_event(plan_dir, "post_session_failed", session_ids=[session_id],
                          failed_step=reason, resumable=True)
        if halt:
            rsi.set_halt(plan_dir, f"{session_id}: shipping {reason} — {message}", session_id)
    action = "failed"
    if reason in ("deploy-target-missing", "gate-missing"):
        action = "skipped"
    elif reason == "deploy-auth-stale":
        action = "confirm-required"
    out = {"action": action, "session": session_id, "reason": reason, "message": message}
    if extra:
        out.update(extra)
    return out


# --------------------------------------------------------------------------
# Read-only status (no lock) — dashboard / dry-run discovery
# --------------------------------------------------------------------------
def ship_status(plan_dir, session_id):
    manifest = mio.load_manifest(plan_dir)
    ps = _resolved_post_session(manifest, session_id)
    if not ps:
        return {"session": session_id, "declared": None}
    err = None
    try:
        steps = [_describe(s) for s in compute_steps(plan_dir, manifest, session_id)]
    except (StepResolveError, adapter.AdapterError) as e:
        steps, err = [], str(e)
    state = None
    try:
        state = ssio.load_ship_state(plan_dir, session_id)
    except ssio.ShipStateError as e:
        err = err or str(e)
    return {"session": session_id, "declared": ps, "steps": steps, "state": state,
            "badge": shipping_badge(state) if state else "ship-pending", "error": err}


# --------------------------------------------------------------------------
# Simulate (CI / smoke) — run the whole pipeline producing real events + state
# but WITHOUT invoking destructive skills or real commands. Every sub-step is
# auto-succeeded. Honors checkpoint precedence, skip_if_partial, state-drift and
# deploy-target-missing (so those paths are exercisable in CI too).
# --------------------------------------------------------------------------
def ship_simulate(plan_dir, session_id):
    out = ship_begin(plan_dir, session_id, confirm_stale=True)
    walked = []
    while out.get("action") in ("invoke-skill", "run-argv"):
        walked.append(out["step"])
        out = ship_record(plan_dir, session_id, out["step"], "done")
    out["_simulated_steps"] = walked
    out["_simulated"] = True
    return out


# `shipping_summary` (the aggregate monitoring readout) lives in ship_badges.py
# and is re-exported above.
