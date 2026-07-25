"""Shipping-state durability, resource locks, and argv execution.

Split out of ``run_state_io.py`` to keep that module focused (and under the
500-LOC ceiling). Reuses ``run_state_io``'s pidfile-lock shape and event log.

Three concerns:

  * **Durable JSON writer** (Codex HIGH — durability gap). ``_shipping_state``
    records gate unattended deploys, so a torn write that looks like
    ``deploy: done`` must never survive a crash. Adds: keep a prior ``.bak``,
    fsync the parent directory after rename, and validate-on-read. A corrupt
    read raises ``ShipStateError`` so the caller halts rather than skipping a
    step as already-done. (The plain ``write_text`` in ``_closeouts`` is not
    strong enough to copy here.)
  * **Resource-scoped shipping locks** (Codex MEDIUM — a single global lock
    defeats parallel groups). Granularity: ``git:<worktree>``,
    ``push:<remote>/<branch>``, ``deploy:<target>``, ``gate:<id>``.
  * **``run_deploy_argv``** for ``deploy_argv`` / argv-kind registry steps —
    ``shell=False``, explicit cwd, **allow-listed env** (NOT the full-``os.environ``
    inheritance ``_run_halt_notify`` uses; that is the secret-leak anti-pattern
    we must not copy).
"""

import json
import os
import re
import shutil
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import run_state_io as rsi


class ShipStateError(Exception):
    """Shipping-state record unreadable/corrupt on both primary and ``.bak``.
    The orchestrator must halt."""


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Durable JSON writer
# --------------------------------------------------------------------------
def _fsync_dir(dirpath):
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        dfd = os.open(str(dirpath), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


def durable_write_json(path, obj):
    """Atomically + durably write ``obj`` as JSON, keeping the prior file as
    ``<path>.bak``. Validates the written bytes parse before returning."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2, ensure_ascii=False)

    if path.exists():
        try:
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    try:
        json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ShipStateError(f"durable write of {path} did not validate: {e}") from e
    return path


def read_json_with_bak(path):
    """Read JSON, falling back to ``<path>.bak`` on corruption. ``None`` if the
    file is absent. Raises ``ShipStateError`` if both are corrupt."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        bak = path.with_name(path.name + ".bak")
        if bak.exists():
            try:
                return json.loads(bak.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        raise ShipStateError(f"shipping-state file {path} is corrupt and has no usable .bak")


# --------------------------------------------------------------------------
# _shipping_state/<sid>.json IO
# --------------------------------------------------------------------------
def ship_state_path(plan_dir, session_id):
    return Path(plan_dir) / "_shipping_state" / f"{session_id}.json"


def load_ship_state(plan_dir, session_id):
    return read_json_with_bak(ship_state_path(plan_dir, session_id))


def save_ship_state(plan_dir, session_id, state):
    return durable_write_json(ship_state_path(plan_dir, session_id), state)


# --------------------------------------------------------------------------
# Resource-scoped shipping locks (pidfile model mirrors run_state_io's run lock
# so the lock holds across the multiple run.py invocations one shipping session
# spans).
# --------------------------------------------------------------------------
def _ship_lock_dir(plan_dir):
    return Path(plan_dir) / "_shipping_locks"


def _resource_slug(resource):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(resource)).strip("-").lower()
    return slug or "lock"


def _ship_lock_path(plan_dir, resource):
    return _ship_lock_dir(plan_dir) / f"{_resource_slug(resource)}.lock"


def _lock_is_stale(info):
    started = info.get("started_at")
    age = None
    if started:
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(started)).total_seconds()
        except ValueError:
            age = None
    pid = info.get("pid")
    alive = rsi._pid_alive(pid) if isinstance(pid, int) else False
    return (age is not None and age > rsi.STALE_LOCK_SECONDS) or not alive


def acquire_ship_lock(plan_dir, resource):
    """Acquire a resource-scoped shipping lock. Raises ``rsi.LockError`` if a
    live, non-stale holder (other than this process) exists; a dead/stale holder
    is reclaimed."""
    lp = _ship_lock_path(plan_dir, resource)
    lp.parent.mkdir(parents=True, exist_ok=True)
    if lp.exists():
        try:
            info = json.loads(lp.read_text())
        except (json.JSONDecodeError, OSError):
            info = {}
        if not _lock_is_stale(info) and info.get("pid") != os.getpid():
            raise rsi.LockError(
                f"shipping resource {resource!r} is locked (pid={info.get('pid')}, "
                f"host={info.get('host')}, started={info.get('started_at')}). "
                f"If stale, remove {lp} and retry."
            )
    rsi._atomic_write(
        lp,
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": _now(),
                "host": socket.gethostname(),
                "resource": str(resource),
            }
        ),
    )
    rsi.log_event(plan_dir, "shipping_lock_acquired", resource=str(resource))
    return True


def release_ship_lock(plan_dir, resource):
    lp = _ship_lock_path(plan_dir, resource)
    if lp.exists():
        lp.unlink()
        rsi.log_event(plan_dir, "shipping_lock_released", resource=str(resource))


def release_all_ship_locks(plan_dir):
    """Release every shipping lock we (or a dead holder) own — cleanup on
    abort/finalize."""
    d = _ship_lock_dir(plan_dir)
    if not d.is_dir():
        return
    for lp in d.glob("*.lock"):
        try:
            info = json.loads(lp.read_text())
        except (json.JSONDecodeError, OSError):
            info = {}
        pid = info.get("pid")
        if pid == os.getpid() or not (isinstance(pid, int) and rsi._pid_alive(pid)):
            lp.unlink()
            rsi.log_event(plan_dir, "shipping_lock_released", resource=info.get("resource", lp.stem))


def held_ship_locks(plan_dir):
    """List currently-present shipping lock resources (for status/tests)."""
    d = _ship_lock_dir(plan_dir)
    if not d.is_dir():
        return []
    out = []
    for lp in sorted(d.glob("*.lock")):
        try:
            out.append(json.loads(lp.read_text()).get("resource", lp.stem))
        except (json.JSONDecodeError, OSError):
            out.append(lp.stem)
    return out


# --------------------------------------------------------------------------
# argv-kind step execution
# --------------------------------------------------------------------------
_DEFAULT_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SHELL")


def build_allowlisted_env(extra_allow=None):
    """An env dict restricted to a safe allowlist, NOT inherited ``os.environ``.

    ``extra_allow`` names additional pass-through vars the registry/target
    explicitly opts into (e.g. ``AWS_PROFILE``)."""
    allow = set(_DEFAULT_ENV_ALLOWLIST)
    if extra_allow:
        allow.update(extra_allow)
    return {k: v for k, v in os.environ.items() if k in allow}


def run_deploy_argv(argv, *, cwd, env_allowlist=None, timeout=1800):
    """Execute an argv-kind shipping step. ``shell=False`` always; explicit cwd;
    allow-listed env (never the full inherited environment); relative-path
    executables rejected (the registry must declare an absolute path or a bare
    program name resolved via PATH).

    Returns ``{returncode, stdout, stderr, timed_out[, error]}``. The caller
    redacts before logging."""
    import subprocess

    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return {"returncode": None, "stdout": "", "timed_out": False, "error": "bad-argv",
                "stderr": "deploy_argv must be a non-empty list of strings"}
    exe = argv[0]
    if (os.sep in exe or (os.altsep and os.altsep in exe)) and not os.path.isabs(exe):
        return {"returncode": None, "stdout": "", "timed_out": False, "error": "relative-exe",
                "stderr": f"relative executable rejected: {exe!r}"}

    env = build_allowlisted_env(env_allowlist)
    try:
        proc = subprocess.run(
            argv, cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"returncode": None, "stdout": "", "stderr": f"timeout after {timeout}s",
                "timed_out": True, "error": "timeout"}
    except (OSError, subprocess.SubprocessError) as e:
        return {"returncode": None, "stdout": "", "stderr": str(e), "timed_out": False,
                "error": "exec-error"}
    return {"returncode": proc.returncode, "stdout": proc.stdout or "",
            "stderr": proc.stderr or "", "timed_out": False}
