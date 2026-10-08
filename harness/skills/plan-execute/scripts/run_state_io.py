"""Mutable runtime state (run_state.json) + lock + NDJSON event log.

run_state.json holds: halt flag, last_batch summary, lock token.
PLAN.html remains the canonical source for per-session/item status — run_state
never duplicates it.

Lock model (v1, single-user): a pidfile `.lock` carrying {pid, started_at,
host}. Best-effort across separate process invocations (the orchestrator calls
this CLI multiple times within one turn). True fcntl.flock and shared-FS
detection are v1.5/v2 refinements — see references/failure-modes.md.

Staleness is HOST-AWARE (`lock_stale` / `lock_on_this_host`, LCK-02): a pid only
means something on the machine that recorded it, so a lock carrying another
host's name is reported with that name and never reclaimed here. The shipping
LEASES in ship_state_io.py are a different, stronger model (token + explicit
expiry, atomic O_EXCL create) because they must survive run.py exiting mid-
operation; this pidfile lock is only held inside one run.py process tree.
"""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from host_id import host_fields, same_host  # noqa: F401  (host_fields re-exported for ship_locks)

STALE_LOCK_SECONDS = 3600  # 1 hour


def _now():
    return datetime.now(UTC).isoformat()


def _atomic_write(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --------------------------------------------------------------------------
# run_state.json
# --------------------------------------------------------------------------
def _default_state():
    return {
        "schema_version": 2,
        "halt": {"set": False, "reason": None, "by_session": None, "at": None},
        "last_batch": None,
        "lock_token": None,
    }


def load_state(plan_dir):
    p = Path(plan_dir) / "run_state.json"
    if not p.exists():
        return _default_state()
    try:
        state = json.loads(p.read_text())
    except json.JSONDecodeError:
        return _default_state()
    # Guard the parsed VALUE, not just the parse: valid JSON that is a list or a
    # scalar would make every `state.get(...)` below raise AttributeError.
    return state if isinstance(state, dict) else _default_state()


def save_state(plan_dir, state):
    _atomic_write(Path(plan_dir) / "run_state.json", json.dumps(state, indent=2))


def set_halt(plan_dir, reason, by_session, kind=None, detail=None):
    """Stop the plan. ``kind`` names WHY, for the guards that must treat one
    flavor differently: a ``"replan"`` halt (S05) is resolved by restructuring
    the plan, so the mutation commands are allowed through it while `plan` /
    `begin` still refuse. ``None`` (the default) is the ordinary failure halt
    that refuses everything until `clear-halt`.

    ``detail`` is optional pre-rendered plain text spliced into HALT_NOTICE.txt
    under the one-line reason — used by RS-04 to put a BLOCKED session's decision
    brief (options + recommendation) in front of the operator without them having
    to open `_closeouts/<sid>.json`. It never enters run_state.json, so it cannot
    affect any digest.
    """
    state = load_state(plan_dir)
    state["halt"] = {
        "set": True, "reason": reason, "by_session": by_session,
        "at": _now(), "kind": kind,
    }
    save_state(plan_dir, state)
    log_event(plan_dir, "halt_set", reason=reason, session_ids=[by_session] if by_session else [])
    _write_halt_notice(plan_dir, reason, by_session, detail)
    _run_halt_notify(plan_dir, reason, by_session)
    return state


def rewrite_halt_notice(plan_dir, detail):
    """Re-render HALT_NOTICE.txt for the CURRENT halt with fresh ``detail``.

    The halt itself is unchanged — same reason, same session, same timestamp —
    so this deliberately writes no state, logs no `halt_set`, and never re-fires
    the notify hook. Used by RP-08 when a recommendation is recorded against an
    already-parked REPLAN: the operator's notice must show it, but nothing about
    the halt has happened twice. Returns False when the plan is not halted.
    """
    halt = load_state(plan_dir).get("halt", {})
    if not halt.get("set"):
        return False
    _write_halt_notice(plan_dir, halt.get("reason"), halt.get("by_session"), detail,
                       at=halt.get("at"))
    return True


from halt_clears import HaltClearRefused, clear_halt, clear_halt_cli  # noqa: E402,F401


def is_halted(plan_dir):
    return bool(load_state(plan_dir).get("halt", {}).get("set"))


def record_batch(plan_dir, session_ids, result):
    state = load_state(plan_dir)
    # orchestrator_session_id lets hooks/turnend-guard.py scope "no turn ends
    # blind" to the Claude session that actually dispatched this batch.
    state["last_batch"] = {"at": _now(), "session_ids": list(session_ids), "result": result,
                           "orchestrator_session_id": os.environ.get("CLAUDE_CODE_SESSION_ID")}
    save_state(plan_dir, state)


def _write_halt_notice(plan_dir, reason, by_session, detail=None, at=None):
    body = (
        f"PLAN HALTED at {at or _now()}\n"
        f"Session: {by_session or '(unknown)'}\n"
        f"Reason: {reason}\n\n"
        + (f"{detail.rstrip()}\n\n" if detail and str(detail).strip() else "")
        + f"Inspect with: /plan-execute {plan_dir} --status\n"
        f"After fixing, clear with: /plan-execute {plan_dir} --clear-halt\n"
    )
    _atomic_write(Path(plan_dir) / "HALT_NOTICE.txt", body)


def _run_halt_notify(plan_dir, reason, by_session):
    """Opt-in, best-effort halt notification (generic command hook).

    If manifest.json carries ``notify_on_halt.command`` (user-authored config in
    the user's own project, like a git hook), run it ONCE, synchronously, with
    ``shell=False``, a ~10s timeout, and PLAN_* env vars. Any failure — non-zero
    exit, timeout, bad parse, missing manifest — is swallowed and logged as a
    ``notify_failed`` event. Notification must NEVER block or crash the halt path.
    """
    import shlex
    import subprocess

    try:
        import manifest_io as mio
    except ImportError:
        return
    try:
        manifest = mio.load_manifest(plan_dir)
    except (mio.ManifestError, OSError):
        return

    notify = manifest.get("notify_on_halt")
    if not isinstance(notify, dict):
        return
    command = notify.get("command")
    if not isinstance(command, str) or not command.strip():
        return

    try:
        argv = shlex.split(command)
    except ValueError as e:
        log_event(plan_dir, "notify_failed", error=f"command parse error: {e}")
        return
    if not argv:
        return

    env = dict(os.environ)
    env.update(
        {
            "PLAN_DIR": str(Path(plan_dir).resolve()),
            "PLAN_TITLE": str(manifest.get("title", "")),
            "HALT_SESSION": str(by_session or ""),
            "HALT_REASON": str(reason or ""),
        }
    )
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired:
        log_event(plan_dir, "notify_failed", error="timeout (10s)", command=argv[0])
        return
    except (OSError, subprocess.SubprocessError) as e:
        log_event(plan_dir, "notify_failed", error=str(e), command=argv[0])
        return

    if proc.returncode != 0:
        log_event(
            plan_dir,
            "notify_failed",
            returncode=proc.returncode,
            stderr=(proc.stderr or "")[-500:],
            command=argv[0],
        )
    else:
        log_event(plan_dir, "notify_sent", command=argv[0])


def notify_complete(plan_dir):
    """Opt-in, best-effort plan-COMPLETE notification — the symmetric companion to
    the halt notify, for ``--auto`` runs you walked away from. Fires ONCE (guarded
    by a ``complete_notified`` run-state flag so a repeated ``--status`` read never
    re-fires) when the loop reports the plan complete. Same safety envelope as the
    halt notify: ``shell=False``, ~10s timeout, swallowed failures. Returns the
    action taken for the caller to surface."""
    import shlex
    import subprocess

    state = load_state(plan_dir)
    if state.get("complete_notified"):
        return {"action": "already-notified"}

    try:
        import manifest_io as mio

        manifest = mio.load_manifest(plan_dir)
    except Exception:  # noqa: BLE001 — never block completion on a notify hook
        return {"action": "noop"}

    notify = manifest.get("notify_on_complete")
    command = notify.get("command") if isinstance(notify, dict) else None
    if not isinstance(command, str) or not command.strip():
        # Mark notified anyway so we don't re-check every loop; nothing to run.
        state["complete_notified"] = True
        save_state(plan_dir, state)
        return {"action": "noop"}

    try:
        argv = shlex.split(command)
    except ValueError as e:
        log_event(plan_dir, "notify_failed", error=f"command parse error: {e}")
        return {"action": "failed", "error": str(e)}
    if not argv:
        return {"action": "noop"}

    env = dict(os.environ)
    env.update({"PLAN_DIR": str(Path(plan_dir).resolve()),
                "PLAN_TITLE": str(manifest.get("title", "")), "PLAN_STATUS": "complete"})
    result = {"action": "sent", "command": argv[0]}
    try:
        proc = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode != 0:
            log_event(plan_dir, "notify_failed", returncode=proc.returncode,
                      stderr=(proc.stderr or "")[-500:], command=argv[0])
            result = {"action": "failed", "returncode": proc.returncode}
        else:
            log_event(plan_dir, "notify_sent", command=argv[0], kind="complete")
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError) as e:
        log_event(plan_dir, "notify_failed", error=str(e), command=argv[0])
        result = {"action": "failed", "error": str(e)}

    state = load_state(plan_dir)
    state["complete_notified"] = True
    save_state(plan_dir, state)
    return result


def notify_gate_continue(plan_dir, session_id, gate_type, reason):
    """Best-effort push notification for a notify-and-continue gate auto-continue
    (OR-03). The symmetric companion to :func:`notify_complete` / the halt notify:
    when a rubber-stamp gate is passed WITHOUT waiting for the operator, the
    operator still gets a ping instead of silently losing visibility. Fires the
    optional ``notify_on_gate`` manifest hook (falls back to ``notify_on_complete``
    if only that is configured, so a single hook covers both events). Same safety
    envelope: ``shell=False``, ~10s timeout, failures swallowed and logged; NEVER
    blocks or crashes the auto-continue path. Returns the action taken.

    The authoritative record of the auto-continue is the ``gate_auto_continue``
    run.ndjson event (logged by the caller) — this hook is only the outbound ping.
    """
    import shlex
    import subprocess

    try:
        import manifest_io as mio

        manifest = mio.load_manifest(plan_dir)
    except Exception:  # noqa: BLE001 — never block the auto-continue on a hook
        return {"action": "noop"}

    notify = manifest.get("notify_on_gate")
    if not isinstance(notify, dict):
        notify = manifest.get("notify_on_complete")
    command = notify.get("command") if isinstance(notify, dict) else None
    if not isinstance(command, str) or not command.strip():
        return {"action": "noop"}

    try:
        argv = shlex.split(command)
    except ValueError as e:
        log_event(plan_dir, "notify_failed", error=f"command parse error: {e}", kind="gate")
        return {"action": "failed", "error": str(e)}
    if not argv:
        return {"action": "noop"}

    env = dict(os.environ)
    env.update(
        {
            "PLAN_DIR": str(Path(plan_dir).resolve()),
            "PLAN_TITLE": str(manifest.get("title", "")),
            "PLAN_STATUS": "gate-auto-continue",
            "GATE_SESSION": str(session_id),
            "GATE_TYPE": str(gate_type),
            "GATE_REASON": str(reason or ""),
        }
    )
    result = {"action": "sent", "command": argv[0]}
    try:
        proc = subprocess.run(
            argv, env=env, capture_output=True, text=True, timeout=10, check=False
        )
        if proc.returncode != 0:
            log_event(
                plan_dir, "notify_failed", returncode=proc.returncode,
                stderr=(proc.stderr or "")[-500:], command=argv[0], kind="gate",
            )
            result = {"action": "failed", "returncode": proc.returncode}
        else:
            log_event(plan_dir, "notify_sent", command=argv[0], kind="gate")
    except (subprocess.TimeoutExpired, OSError, subprocess.SubprocessError) as e:
        log_event(plan_dir, "notify_failed", error=str(e), command=argv[0], kind="gate")
        result = {"action": "failed", "error": str(e)}
    return result


# --------------------------------------------------------------------------
# Lock (best-effort pidfile)
# --------------------------------------------------------------------------
class LockError(Exception):
    pass


def _lock_path(plan_dir):
    return Path(plan_dir) / ".lock"


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError):
        # A pid outside the platform's valid range (hand-edited/truncated
        # pidfile) raises from os.kill itself — degrade to "not alive".
        return False
    return True


def lock_age_seconds(info):
    """Age of a lock record in seconds, or None when ``started_at`` is missing
    or unparseable (a naive timestamp cannot be subtracted from an aware now)."""
    started = (info or {}).get("started_at")
    if not started:
        return None
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
    except (ValueError, TypeError):
        return None


def lock_on_this_host(info):
    """True when the lock record was written on THIS machine (or names no host).

    LCK-02: the single definition of "may this machine judge that lock at all".
    A pid is only meaningful on the host that recorded it — on a shared checkout
    (network share, synced folder) this machine's pid table says nothing about a
    process on another one, so a foreign-host lock must be REPORTED with its host
    name, never reclaimed because the number happens to be free here."""
    return same_host(info)


def lock_stale(info):
    """True when a `.lock` record may be reclaimed. Host-aware: a lock recorded
    on another machine is NEVER stale here, whatever its pid or age."""
    if not isinstance(info, dict):
        return True
    if not lock_on_this_host(info):
        return False
    age = lock_age_seconds(info)
    pid = info.get("pid")
    alive = _pid_alive(pid) if isinstance(pid, int) else False
    return (age is not None and age > STALE_LOCK_SECONDS) or not alive


def acquire_lock(plan_dir):
    lp = _lock_path(plan_dir)
    if lp.exists():
        try:
            info = json.loads(lp.read_text())
        except (json.JSONDecodeError, OSError):
            info = {}
        if not isinstance(info, dict):
            info = {}
        if not lock_stale(info):
            foreign = ("" if lock_on_this_host(info) else
                       f" This lock was taken on ANOTHER machine ({info.get('host')}); this host "
                       "cannot tell whether that process is alive, so it is never reclaimed here.")
            raise LockError(
                f"another /plan-execute appears to be running (pid={info.get('pid')}, "
                f"host={info.get('host')}, started={info.get('started_at')}). "
                f"If it is stale, remove {lp} and retry.{foreign}"
            )
        # stale -> overwrite
    token = _now()
    _atomic_write(
        lp, json.dumps({"pid": os.getpid(), "started_at": token, **host_fields()})
    )
    log_event(plan_dir, "lock_acquired")
    return token


def lock_holder(plan_dir):
    """The LIVE dispatch-lock holder (`{pid, started_at, host}`), or None when
    the plan is unlocked or the lock is stale. Read-only — the mutation commands
    use it to refuse restructuring a plan while a batch is in flight."""
    lp = _lock_path(plan_dir)
    if not lp.exists():
        return None
    try:
        info = json.loads(lp.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(info, dict) or lock_stale(info):
        return None
    return info


def release_lock(plan_dir):
    lp = _lock_path(plan_dir)
    if lp.exists():
        lp.unlink()
        log_event(plan_dir, "lock_released")


# --------------------------------------------------------------------------
# Dispatch receipts (TEL-01 attestation handoff) — a background Agent-tool
# completion carries no usage telemetry of its own, so "which model actually
# served this session" is only knowable through an explicit correlation step:
# the orchestrator calls `run.py record-receipt <sid> --agent-id <id>
# [--transcript <path>]` AFTER the Agent call returns and BEFORE apply/verify
# resolution. Landing it in run_state.json (rather than a separate file) means
# it survives crash/compaction/resume exactly like the halt flag and the lock
# token already do.
# --------------------------------------------------------------------------
def record_receipt(plan_dir, session_id, *, agent_id, transcript=None, backend="claude",
                   attested=None, generation=None, attempt=None, usage=None):
    """Persist one dispatch's correlation receipt. `usage` is the rule-6 token
    and cost block read off `transcript` (None without one). `attested` is
    `{"model": ..., "reasoning": ...}` when served-model evidence was found in
    `transcript`, else None (the receipt still proves an Agent ID was
    correlated — `outcomes.py` reads that as `model_ran_source="requested"`).

    `generation`/`attempt` key the receipt to the DISPATCH it was recorded
    for (TEL-01 finding 1) — the caller passes the same escalation generation
    and dispatch-attempt ordinal a resolution recorded right now would carry.
    `outcomes._fresh_receipt` compares them against the resolution it is actually
    composing and refuses to trust a receipt that does not match: this
    receipt does not get cleared on re-dispatch (see `get_receipt`), so
    without this check a skipped `record-receipt` on a later attempt would
    silently reattribute an EARLIER attempt's served model to this one."""
    state = load_state(plan_dir)
    receipts = state.setdefault("dispatch_receipts", {})
    rec = {
        "agent_id": agent_id,
        "transcript": transcript,
        "backend": backend,
        "attested": attested,
        "usage": usage,
        "generation": generation,
        "attempt": attempt,
        "recorded_at": _now(),
    }
    receipts[session_id] = rec
    save_state(plan_dir, state)
    log_event(plan_dir, "receipt_recorded", session_ids=[session_id], agent_id=agent_id,
              backend=backend, attested=bool(attested), generation=generation, attempt=attempt)
    return rec


def get_receipt(plan_dir, session_id):
    """This session's most recently recorded dispatch receipt, or None. A
    redispatch/amend's `escalation.reset` does not clear it — a NEW `begin` is
    expected to overwrite it with a fresh `record-receipt` call before the next
    resolution moment; a stale receipt read before that call simply describes
    the previous attempt, same as any other pre-dispatch state. Callers that
    attribute a served model to a SPECIFIC resolution must check the receipt's
    `generation`/`attempt` against that resolution's own — see
    `outcomes._fresh_receipt`, which is the only place that does."""
    return (load_state(plan_dir).get("dispatch_receipts") or {}).get(session_id)


# --------------------------------------------------------------------------
# NDJSON event log
# --------------------------------------------------------------------------
def log_event(plan_dir, event_type, **fields):
    rec = {"ts": _now(), "event": event_type}
    rec.update(fields)
    line = json.dumps(rec, ensure_ascii=False)
    with open(Path(plan_dir) / "run.ndjson", "a") as f:
        f.write(line + "\n")


# --------------------------------------------------------------------------
# OR-01 (S05 2026-07-03): transport-retry + commit-boundary + subagent-death
# checkpoint logging. Thin convenience wrappers over log_event so every
# retry/surface/checkpoint decision lands in run.ndjson with a stable event
# name the orchestrator (and evidence greps) can rely on. The decision logic
# itself lives in transport.py — this module only persists it.
# --------------------------------------------------------------------------
def log_retry_attempt(plan_dir, session_id, decision, **extra):
    """Log one transport-retry decision (a `transport.RetryDecision`, or any
    object/dict with action/reason/delay_seconds/error_class/attempt)."""
    def _get(k):
        return decision.get(k) if isinstance(decision, dict) else getattr(decision, k, None)

    rec = {
        "session_ids": [session_id],
        "action": _get("action"),
        "reason": _get("reason"),
        "delay_seconds": _get("delay_seconds"),
        "error_class": _get("error_class"),
        "attempt": _get("attempt"),
    }
    rec.update(extra)
    log_event(plan_dir, "transport_retry_decision", **rec)


def log_commit_boundary(plan_dir, session_id, **fields):
    """Mark that a session's dispatch has crossed its commit boundary (a
    PLAN.html apply, a post_session git commit, an MCP write, a
    notification, or a file write outside its own scratch). Any transport
    failure logged AFTER this marker for the same session must be surfaced,
    never auto-retried — see transport.decide(crossed_commit_boundary=True)."""
    log_event(plan_dir, "commit_boundary_crossed", session_ids=[session_id], **fields)


def log_subagent_death_checkpoint(plan_dir, session_id, checkpoint, **fields):
    """A dispatched session died mid-flight (Task tool reported the subagent
    lost/errored with no closeout). Persist whatever partial state exists
    (partial closeout text, last known progress note, files it touched) so
    the re-dispatch prompt can attach it — the 30-minute work product is not
    silently lost. `checkpoint` is a free-form string/dict; kept small
    (callers should summarize, not dump full transcripts, into run.ndjson)."""
    log_event(
        plan_dir,
        "subagent_death_checkpoint",
        session_ids=[session_id],
        checkpoint=checkpoint,
        **fields,
    )
