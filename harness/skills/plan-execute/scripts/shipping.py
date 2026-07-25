"""Post-session shipping state machine (the deterministic core).

The ORCHESTRATOR (Claude, following SKILL.md) drives this between Task
dispatches, exactly like the closeout pipeline. A plain Python process cannot
call the Skill tool, so for ``skill``-kind sub-steps this module returns a
DIRECTIVE ("invoke /commit-orchestrate now") and the orchestrator runs it via
the Skill tool, then records the outcome back. ``argv``-kind sub-steps
(``deploy_argv`` / argv registry targets+gates) are executed here directly.

Ordering of the safety gates (the adversarial hardenings):

  Step 0  checkpoint precedence — a human checkpoint ALWAYS outranks shipping.
  Step 1  acquire resource locks FIRST (before the idempotency read — closes the
          read-then-lock TOCTOU).
  Step 2  idempotency read + digest reconciliation — a manifest/closeout digest
          mismatch refuses as ``state-drift`` rather than skipping as shipped.
  Step 3  runtime guard (deploy target re-resolve) + execution-time adapter
          probe + deploy-authorization staleness gate.
  Step 4  ordered sub-steps with running->done/failed durable writes + redaction.
  Step 5  finalize (release locks).

Orchestrator protocol (run.py subcommands):
  ship-begin   -> first directive | noop | deferred | already-shipped | skipped
                  | failed | confirm-required
  ship-record  -> record a skill step's outcome, return next directive
  ship-run     -> run an argv step here, return next directive
  ship-finalize / ship-release -> finalize / release locks
  ship-status  -> read-only computed plan + state (no lock; dashboard/dry-run)
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import article_block as ab
import closeout_pipeline as cp
import manifest_io as mio
import run_state_io as rsi
import ship_state_io as ssio
import shipping_adapter as adapter

STEP_PENDING, STEP_RUNNING, STEP_DONE, STEP_FAILED, STEP_SKIPPED = (
    "pending", "running", "done", "failed", "skipped",
)


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Project-local registry resolution (deploy targets + eval gates)
# --------------------------------------------------------------------------
def find_project_root(plan_dir):
    plan_dir = Path(plan_dir).resolve()
    for up in [plan_dir, *plan_dir.parents]:
        if (up / ".claude").is_dir():
            return up
    if plan_dir.parent.name == "_plans":
        return plan_dir.parent.parent
    return plan_dir.parent


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

    Raises ``adapter.AdapterError`` / ``StepResolveError`` on bad config so the
    caller can halt with a precise reason."""
    ps = _resolved_post_session(manifest, session_id)
    if not ps:
        return []
    steps = []
    for sub in adapter.git_substeps(ps.get("git", "none")):
        entry = adapter.git_adapter(sub)
        steps.append({"name": sub, "kind": "skill", "resource": _git_resource(sub, plan_dir),
                      **{k: entry[k] for k in ("skill", "args", "probe_flags", "timeout")}})
    for gate_id in ps.get("pre_deploy_gates", []) or []:
        steps.append(_resolve_gate(plan_dir, gate_id))
    deploy_step = _resolve_deploy(plan_dir, ps)
    if deploy_step:
        steps.append(deploy_step)
    return steps


def _git_resource(sub, plan_dir):
    root = find_project_root(plan_dir)
    return f"push:{root}" if sub == "push" else f"git:{root}"


def _resolve_gate(plan_dir, gate_id):
    g = eval_gates(plan_dir).get(gate_id)
    if not g:
        raise StepResolveError("gate-missing",
                               f"gate {gate_id!r} not in eval-gates registry")
    name = f"gate:{gate_id}"
    if g.get("kind") == "skill":
        return {"name": name, "kind": "skill", "resource": name, "skill": g["skill"],
                "args": g.get("args", ""), "probe_flags": g.get("probe_flags", []),
                "timeout": g.get("timeout", 1200), "fixture_fake": g.get("fixture_fake")}
    return {"name": name, "kind": "argv", "resource": name, "argv": g.get("argv", []),
            "cwd": _resolve_cwd(plan_dir, g.get("cwd")), "env_allowlist": g.get("env_allowlist", []),
            "timeout": g.get("timeout", 1200), "failure_mode": "fail-halt",
            "fixture_fake": g.get("fixture_fake")}


# Public alias: session verify gates (verify.py) resolve through the SAME registry
# entry as a pre_deploy gate of the same id, so the two never diverge.
def resolve_gate(plan_dir, gate_id):
    return _resolve_gate(plan_dir, gate_id)


def _resolve_deploy(plan_dir, ps):
    failure_mode = ps.get("command_failure_mode", "fail-halt")
    argv = ps.get("deploy_argv")
    if argv:
        return {"name": "deploy", "kind": "argv", "resource": "deploy:argv", "argv": argv,
                "cwd": _resolve_cwd(plan_dir, None), "env_allowlist": ps.get("env_allowlist", []),
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
            "argv": t.get("argv", []), "cwd": _resolve_cwd(plan_dir, t.get("cwd")),
            "env_allowlist": t.get("env_allowlist", []),
            "timeout": t.get("timeout", _deploy_timeout(failure_mode)),
            "failure_mode": t.get("command_failure_mode", failure_mode),
            "fixture_fake": t.get("fixture_fake")}


def _deploy_timeout(failure_mode):
    return 10 if failure_mode == "best-effort" else 1800


def _resolve_cwd(plan_dir, cwd):
    root = find_project_root(plan_dir)
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


def shipping_badge(state):
    """Human-facing badge: committed/pushed/PR-open/deployed, or
    SHIP-FAILED@<step> / SHIP-PENDING / ship-skipped."""
    steps = state.get("steps", {})
    for name, st in steps.items():
        if st == STEP_FAILED:
            return f"SHIP-FAILED@{name}"
    for name, label in (("deploy", "deployed"), ("pr", "PR-open"),
                        ("push", "pushed"), ("commit", "committed")):
        if steps.get(name) == STEP_DONE:
            return label
    if any(st == STEP_RUNNING for st in steps.values()):
        return "SHIP-PENDING"
    if steps and all(st == STEP_SKIPPED for st in steps.values()):
        return "ship-skipped"
    return "ship-pending"


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
def ship_begin(plan_dir, session_id, *, dry_run=False, resume=False, confirm_stale=False):
    manifest = mio.load_manifest(plan_dir)
    ps = _resolved_post_session(manifest, session_id)
    if not ps or (ps.get("git", "none") == "none" and not ps.get("pre_deploy_gates")
                  and not _resolve_deploy_present(ps)):
        return {"action": "noop", "session": session_id}

    co = cp.load_closeout(plan_dir, session_id)
    result = co.get("result") if co else None

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

    try:
        steps = compute_steps(plan_dir, manifest, session_id)
    except (StepResolveError, adapter.AdapterError) as e:
        return _fail(plan_dir, session_id, getattr(e, "reason", "step-resolve-error"), str(e),
                     dry_run, halt=True)

    if dry_run:
        return {"action": "dry-run", "session": session_id, "result": result,
                "steps": [_describe(s) for s in steps]}

    # Step 1 — acquire locks FIRST (before the idempotency read; closes TOCTOU).
    acquired = []
    try:
        for res in dict.fromkeys(s["resource"] for s in steps):
            ssio.acquire_ship_lock(plan_dir, res)
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
        if (state.get("manifest_digest") != cur_manifest
                or state.get("closeout_digest") != cur_closeout):
            return _fail(plan_dir, session_id, "state-drift",
                         "manifest/closeout digest changed since shipping state was recorded; "
                         "refusing to resume (a rebuilt plan must not skip as already-shipped)",
                         dry_run, halt=True, locks=acquired)
        if _all_done(state):
            _release(plan_dir, acquired)
            return {"action": "already-shipped", "session": session_id}
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
        return {"action": "invoke-skill", "session": session_id, "step": nxt, "kind": "skill",
                "skill": step["skill"], "args": step.get("args", ""), "timeout": step.get("timeout")}
    return {"action": "run-argv", "session": session_id, "step": nxt, "kind": "argv"}


def _reload(plan_dir, session_id):
    manifest = mio.load_manifest(plan_dir)
    return manifest, compute_steps(plan_dir, manifest, session_id), \
        ssio.load_ship_state(plan_dir, session_id)


def ship_record(plan_dir, session_id, step, status, result_file=None):
    """Record a SKILL step's outcome (orchestrator already invoked it)."""
    _manifest, steps, state = _reload(plan_dir, session_id)
    if state is None:
        return _fail(plan_dir, session_id, "no-shipping-state",
                     "ship-record with no shipping state", False, halt=True)
    if status == "done":
        state["steps"][step] = STEP_DONE
        _persist_state(plan_dir, session_id, state)
        return _post_step(plan_dir, session_id, steps, state)
    excerpt = ""
    if result_file and Path(result_file).exists():
        excerpt = adapter.redact(Path(result_file).read_text(errors="replace"))
    return _record_failure(plan_dir, session_id, state, step, excerpt)


def ship_run_argv(plan_dir, session_id, step):
    """Run an ARGV step here (deploy_argv / argv registry target or gate)."""
    _manifest, steps, state = _reload(plan_dir, session_id)
    if state is None:
        return _fail(plan_dir, session_id, "no-shipping-state",
                     "ship-run with no shipping state", False, halt=True)
    sd = _step_by_name(steps, step)
    if sd is None or sd["kind"] != "argv":
        return _fail(plan_dir, session_id, "not-argv-step", f"{step!r} is not an argv step",
                     False, halt=True)

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
    ok = (res.get("returncode") == 0) or failure_mode == "best-effort"
    if ok:
        state["steps"][step] = STEP_DONE
        _persist_state(plan_dir, session_id, state)
        return _post_step(plan_dir, session_id, steps, state)
    excerpt = adapter.redact((res.get("stderr") or "") + "\n" + (res.get("stdout") or ""))
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


# --------------------------------------------------------------------------
# Aggregate shipping summary (monitoring — Q10 monitoring_blind_spot mitigation).
# Surfaced by `run.py status` so a silent skip/halt is visible in the default
# output, not only in retrospective run.ndjson archaeology.
# --------------------------------------------------------------------------
def shipping_summary(plan_dir, manifest, *, tail=8):
    sessions = {}
    for s in manifest.get("sessions", []):
        if not s.get("post_session"):
            continue
        sid = s["id"]
        try:
            state = ssio.load_ship_state(plan_dir, sid)
        except ssio.ShipStateError:
            sessions[sid] = "STATE-CORRUPT"
            continue
        sessions[sid] = shipping_badge(state) if state else "ship-pending"
    if not sessions:
        return None
    return {"sessions": sessions, "recent_events": _recent_shipping_events(plan_dir, tail)}


def _recent_shipping_events(plan_dir, tail):
    p = Path(plan_dir) / "run.ndjson"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(rec.get("event", "")).startswith("post_session_"):
            ev = {"event": rec["event"], "ts": rec.get("ts"),
                  "session": (rec.get("session_ids") or [None])[0]}
            for k in ("reason", "failed_step", "declared_steps"):
                if k in rec:
                    ev[k] = rec[k]
            out.append(ev)
    return out[-tail:]
