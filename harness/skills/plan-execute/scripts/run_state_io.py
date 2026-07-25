"""Mutable runtime state (run_state.json) + lock + NDJSON event log.

run_state.json holds: halt flag, last_batch summary, lock token.
PLAN.html remains the canonical source for per-session/item status — run_state
never duplicates it.

Lock model (v1, single-user): a pidfile `.lock` carrying {pid, started_at,
host}. Best-effort across separate process invocations (the orchestrator calls
this CLI multiple times within one turn). True fcntl.flock and shared-FS
detection are v1.5/v2 refinements — see references/failure-modes.md.
"""

import json
import os
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path

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
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return _default_state()


def save_state(plan_dir, state):
    _atomic_write(Path(plan_dir) / "run_state.json", json.dumps(state, indent=2))


def set_halt(plan_dir, reason, by_session):
    state = load_state(plan_dir)
    state["halt"] = {"set": True, "reason": reason, "by_session": by_session, "at": _now()}
    save_state(plan_dir, state)
    log_event(plan_dir, "halt_set", reason=reason, session_ids=[by_session] if by_session else [])
    _write_halt_notice(plan_dir, reason, by_session)
    _run_halt_notify(plan_dir, reason, by_session)
    return state


def clear_halt(plan_dir):
    state = load_state(plan_dir)
    state["halt"] = {"set": False, "reason": None, "by_session": None, "at": None}
    save_state(plan_dir, state)
    notice = Path(plan_dir) / "HALT_NOTICE.txt"
    if notice.exists():
        notice.unlink()
    log_event(plan_dir, "halt_cleared")
    return state


def is_halted(plan_dir):
    return bool(load_state(plan_dir).get("halt", {}).get("set"))


def record_batch(plan_dir, session_ids, result):
    state = load_state(plan_dir)
    state["last_batch"] = {"at": _now(), "session_ids": list(session_ids), "result": result}
    save_state(plan_dir, state)


def _write_halt_notice(plan_dir, reason, by_session):
    body = (
        f"PLAN HALTED at {_now()}\n"
        f"Session: {by_session or '(unknown)'}\n"
        f"Reason: {reason}\n\n"
        f"Inspect with: /plan-execute {plan_dir} --status\n"
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
# Filesystem lock-semantics guard (P5)
# --------------------------------------------------------------------------
# Pidfile / flock advisory locks are reliable only on a local POSIX filesystem.
# On networked or cloud-sync filesystems two runs can each believe they hold the
# lock and race PLAN.html into corruption. We detect the common cases and refuse
# by default (override: --unsafe-lock).

# Path-prefix heuristics: sync-folder roots under $HOME (cheap, no external deps).
_SYNC_FS_PREFIXES = {
    "iCloud Drive": ["Library/Mobile Documents"],
    "CloudStorage (Google Drive / OneDrive / Dropbox via Finder)": ["Library/CloudStorage"],
    "Dropbox": ["Dropbox"],
    "Google Drive": ["Google Drive"],
    "OneDrive": ["OneDrive"],
}

# fstype values that indicate a networked filesystem where POSIX locks misbehave.
_NETWORK_FSTYPES = {"nfs", "smbfs", "cifs", "afpfs", "fuse", "fuseblk", "webdav", "ftp"}


def check_lock_fs(plan_dir):
    """Return a human-readable FS-class name if ``plan_dir`` resolves onto a
    networked/sync filesystem where the lock is unreliable, else ``None``.

    Detection is best-effort: any error returns ``None`` (never block on a
    detection failure — the caller decides whether to refuse).
    """
    try:
        resolved = Path(plan_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        return None

    # 1. Path-prefix match against known sync-folder roots under $HOME.
    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = None
    if home is not None and resolved.is_relative_to(home):
        rel = str(resolved.relative_to(home))
        for fs_class, prefixes in _SYNC_FS_PREFIXES.items():
            for pre in prefixes:
                if rel == pre or rel.startswith(pre + "/"):
                    return fs_class

    # 2. Mount-type probe (best-effort, guarded).
    fstype = _probe_fstype(resolved)
    if fstype and fstype.lower() in _NETWORK_FSTYPES:
        return f"network filesystem ({fstype})"
    return None


def _probe_fstype(path):
    """Best-effort fstype for ``path``'s mount point. ``None`` on any failure.

    NB: BSD/macOS ``stat -f %T`` reports the *file* type (ls -F style), not the
    fstype, so we parse ``mount`` output (macOS) / ``/proc/mounts`` (Linux) and
    match the longest mount point that is a prefix of ``path``.
    """
    import subprocess
    import sys

    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["/sbin/mount"], capture_output=True, text=True, timeout=5, check=False
            )
            if out.returncode != 0:
                return None
            mounts = _parse_macos_mount(out.stdout)
        elif sys.platform.startswith("linux"):
            mounts = _parse_proc_mounts(Path("/proc/mounts").read_text())
        else:
            return None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return _fstype_for_path(str(path), mounts)


def _parse_macos_mount(text):
    """macOS ``mount`` lines: ``<dev> on <mountpoint> (<fstype>, <opts>)``."""
    import re

    mounts = []
    for line in text.splitlines():
        m = re.match(r"^.*? on (.+?) \(([^,)]+)", line)
        if m:
            mounts.append((m.group(1), m.group(2).strip()))
    return mounts


def _parse_proc_mounts(text):
    """Linux ``/proc/mounts`` lines: ``<dev> <mountpoint> <fstype> <opts> 0 0``.
    Mount points escape spaces as ``\\040``."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            mountpoint = parts[1].replace("\\040", " ")
            mounts.append((mountpoint, parts[2]))
    return mounts


def _fstype_for_path(path, mounts):
    """Return the fstype of the longest mount point that is a prefix of ``path``."""
    best_fstype, best_len = None, -1
    for mountpoint, fstype in mounts:
        if not mountpoint:
            continue
        norm = mountpoint.rstrip("/")
        matches = path == mountpoint or norm == "" or path.startswith(norm + "/")
        if matches and len(mountpoint) > best_len:
            best_fstype, best_len = fstype, len(mountpoint)
    return best_fstype


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
    return True


def acquire_lock(plan_dir):
    lp = _lock_path(plan_dir)
    if lp.exists():
        try:
            info = json.loads(lp.read_text())
        except (json.JSONDecodeError, OSError):
            info = {}
        started = info.get("started_at")
        age = None
        if started:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
            except ValueError:
                age = None
        pid = info.get("pid")
        alive = _pid_alive(pid) if isinstance(pid, int) else False
        stale = (age is not None and age > STALE_LOCK_SECONDS) or not alive
        if not stale:
            raise LockError(
                f"another /plan-execute appears to be running (pid={pid}, host={info.get('host')}, "
                f"started={started}). If it is stale, remove {lp} and retry."
            )
        # stale -> overwrite
    token = _now()
    _atomic_write(
        lp, json.dumps({"pid": os.getpid(), "started_at": token, "host": socket.gethostname()})
    )
    log_event(plan_dir, "lock_acquired")
    return token


def release_lock(plan_dir):
    lp = _lock_path(plan_dir)
    if lp.exists():
        lp.unlink()
        log_event(plan_dir, "lock_released")


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
