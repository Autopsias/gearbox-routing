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
  * **Resource-scoped shipping leases** (Codex MEDIUM — a single global lock
    defeats parallel groups). The resource names shipping.py actually keys on
    are ``git:<project-root>``, ``push:<project-root>`` (both REPO-scoped — one
    file per checkout, in the repo's git common dir, shared by every plan and
    every linked worktree), and ``deploy:<target>`` / ``deploy:argv`` /
    ``gate:<id>`` (PLAN-scoped, under the plan directory). Per-worktree and
    per-remote/branch granularity was advertised here for two months and never
    implemented; ``push:`` is keyed on the project root, not on a remote or a
    branch, so a push to a different remote from the same checkout still
    serialises. Ownership is a token+expiry LEASE, not a pid — see the section
    comment above ``acquire_ship_lock``.
  * **``run_deploy_argv``** for ``deploy_argv`` / argv-kind registry steps —
    ``shell=False``, explicit cwd, **allow-listed env** (NOT the full-``os.environ``
    inheritance ``_run_halt_notify`` uses; that is the secret-leak anti-pattern
    we must not copy).
"""

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ship_locks import (  # noqa: F401  (re-exported: callers use ssio.<name>)
    LEASE_RATIONALE,
    REPO_LOCK_DIRNAME,
    REPO_SCOPED_PREFIXES,
    _fsync_dir,
    _lease_state,
    _read_lock,
    _resource_slug,
    _ship_lock_dir,
    _ship_lock_path,
    acquire_ship_lock,
    held_ship_locks,
    lease_status,
    our_lease,
    release_all_ship_locks,
    release_ship_lock,
)


class ShipStateError(Exception):
    """Shipping-state record unreadable/corrupt on both primary and ``.bak``.
    The orchestrator must halt."""


def _now():
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------
# Durable JSON writer
# --------------------------------------------------------------------------
def durable_write_json(path, obj):
    """Atomically + durably write ``obj`` as JSON, keeping the prior file as
    ``<path>.bak``. Validates the written bytes parse before returning."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Final newline: repo end-of-file hooks rewrite a file without one, which
    # empties the plan-record commit at land time.
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"

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


def run_deploy_argv(argv, *, cwd, env_allowlist=None, timeout=1800, env_extra=None):
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
    # `env_extra` is computed by the orchestrator, not inherited from the ambient
    # environment, so it is passed EXPLICITLY rather than allow-listed through:
    # a value the caller derived (a session's review base and scope) has no
    # business depending on what happened to be exported into this process.
    env.update({k: v for k, v in (env_extra or {}).items() if v})
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


def argv_outcome(res, indeterminate_exit=None):
    """Classify a ``run_deploy_argv`` result: ``pass`` / ``fail`` / ``indeterminate``.

    A TIMEOUT or an exec error is INDETERMINATE, never a fail: ``returncode`` is
    None, the gate never ANSWERED, and charging "I could not run" as "I found
    something" was measured twice — a timed-out llm-review charged as a rework
    failure in `verify`, then the land re-gate inheriting the identical shape.
    Lives beside ``run_deploy_argv`` because every caller interprets the same
    result dict, and a fix applied to only one of them left the sibling broken.
    """
    rc = res.get("returncode")
    if res.get("timed_out") or res.get("error") or rc is None:
        return "indeterminate"
    if indeterminate_exit is not None and rc == indeterminate_exit:
        return "indeterminate"
    return "pass" if rc == 0 else "fail"
