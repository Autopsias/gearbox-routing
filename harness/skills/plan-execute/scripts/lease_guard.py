"""Live-holder protection and renewal for a running land's ``git:`` lease.

R6 §17.4 / §17.5.1 (plan-isolation-contract.md). ``ship_locks`` only calls this
module; it stays under the 500-line ratchet.

  * ``mutex``: every read-check-write of a lease file runs inside one host-level
    ``flock`` on ``<lock file>.mutex``, held only for that critical section.
  * A land lease (``holder_kind: "land"``) stores ``pid`` and ``pid_start``
    (``ps -o lstart=`` read with ``TZ=UTC`` and ``LC_ALL=C``, plus `` UTC``, so
    every reader sees the same string). While that process is alive with the SAME
    start time, nobody takes the lease, however long after ``expires_at``. Only a
    start time READ and found DIFFERENT is pid reuse, i.e. a dead holder; a start
    time that cannot be read, or an old local-time one, never authorises a
    takeover while the pid lives. There is no time cap.
  * Only the holder process ON THIS HOST renews, releases or pushes under a land
    lease. A dead holder's lease is taken through the recorded takeover path.
  * A same-host lease with no ``lease_version`` (legacy) whose pid is alive is
    refused too; with a dead pid it follows today's wall-clock rule.
"""

import errno
import fcntl
import json
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import run_state_io as rsi

LEASE_VERSION = 2
LAND = "land"


_HELD = threading.local()


@contextmanager
def mutex(lp):
    """The host mutex for the lease file ``lp`` (§17.5.1). Re-entrant within one thread, so
    a reservation check and the lease acquire it wraps share ONE hold (§17.2 invariant 2)."""
    held = _HELD.__dict__.setdefault("paths", {})
    if held.get(str(lp)):
        yield
        return
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    with open(f"{lp}.mutex", "a") as f:       # closing the file releases the flock
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
        except OSError as e:                  # e.g. a share with no flock: fail closed
            raise rsi.LockError(f"cannot take the host mutex {lp}.mutex ({e}); the lease "
                                f"cannot be judged safely here.") from e
        held[str(lp)] = True
        try:
            yield
        finally:
            held.pop(str(lp), None)


_UTC = " UTC"
_OWN = {}


def pid_start(pid):
    """The process start time in UTC (``ps -o lstart=`` + `` UTC``), or None when ``ps``
    cannot answer (read twice). The same string whatever the caller's TZ or locale."""
    env = {**os.environ, "TZ": "UTC", "LC_ALL": "C"}
    for _ in range(2):
        try:
            p = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                               text=True, timeout=10, check=False, env=env)
        except (OSError, subprocess.SubprocessError):
            continue
        out = (p.stdout or "").strip()
        if p.returncode == 0 and out:
            return out + _UTC
    return None


def own_start():
    """This process's start time, read once and kept (it never changes)."""
    if _OWN.get(os.getpid()) is None:
        _OWN[os.getpid()] = pid_start(os.getpid())
    return _OWN[os.getpid()]


def holder_fields(kind):
    """The identity fields a new lease carries (``{}`` kind -> a plain v2 lease). A
    start time ``ps`` could not read is stored as None: that lease is judged by pid
    alone, so nobody takes it while the pid lives (§17.6)."""
    return {"lease_version": LEASE_VERSION,
            **({"holder_kind": kind, "pid_start": own_start()} if kind else {})}


def _pid(info):
    pid = info.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None


def _comparable(start):
    return isinstance(start, str) and start.endswith(_UTC)


def is_holder(info):
    """True when THIS process on THIS host is the holder the lease file names
    (pid alone when its start time was unreadable at creation)."""
    return (rsi.lock_on_this_host(info) and _pid(info) == os.getpid()
            and info.get("pid_start") in (None, own_start()))


def identity(info):
    """``dead`` | ``same`` | ``reused`` | ``unknown`` for the recorded holder. Only a
    start time READ now and DIFFERENT from a comparable recorded one is ``reused``."""
    pid = _pid(info)
    if pid is None or not rsi._pid_alive(pid):
        return "dead"
    if not _comparable(info.get("pid_start")):
        return "unknown"
    now = own_start() if pid == os.getpid() else pid_start(pid)
    if now is None:
        return "unknown"
    return "same" if now == info["pid_start"] else "reused"


def holder_alive(info):
    """The holder may still be running: not provably dead or replaced (fail closed)."""
    return identity(info) in ("same", "unknown")


def may_take_over(info):
    """``(allowed, cause, why)`` for a same-host lease (§17.4, §17.6)."""
    land = info.get("holder_kind") == LAND
    if not land and info.get("lease_version") is not None:
        return True, "expired", None          # push:, deploy:, ship-begin, finish: today's rule
    ident, known = identity(info), land and _comparable(info.get("pid_start"))
    if ident == "dead":
        return True, ("pid-dead" if known else "expired"), None
    if ident == "reused":
        return True, "pid-reused", None
    return False, None, ("live-holder" if known else "legacy-live-pid")


def dead_own_land(plan_dir, resource, cur):
    """An unexpired land lease of THIS plan on this host whose holder died: it is taken
    over at once through the recorded takeover path, never adopted in place."""
    import ship_locks as sl  # noqa: PLC0415 — ship_locks imports this module
    rec = sl.our_lease(plan_dir, resource)
    return bool(cur.get("holder_kind") == LAND and rec and cur.get("token") == rec.get("token")
                and rsi.lock_on_this_host(cur) and not holder_alive(cur))


def check_takeover(plan_dir, resource, lp, cur, message):
    """The cause of an allowed takeover, or raise ``rsi.LockError`` and log the refusal."""
    ok, cause, why = may_take_over(cur or {})
    if ok:
        return cause
    rsi.log_event(plan_dir, "shipping_lease_takeover_refused", resource=str(resource),
                  lock_file=str(lp), holder_pid=cur.get("pid"), holder_host=cur.get("host"),
                  holder_plan=cur.get("plan_dir"), why=why)
    raise rsi.LockError(message)


def renew_locked(plan_dir, resource, lp, cur):
    """Extend ``cur`` (already read inside the mutex). Token, pid and start stay."""
    import ship_locks as sl  # noqa: PLC0415 — ship_locks imports this module
    now = datetime.now(UTC)
    seconds = sl._lease_seconds(cur.get("guarded_timeout"))
    cur.update(expires_at=(now + timedelta(seconds=seconds)).isoformat(),
               lease_seconds=seconds, renewed_at=now.isoformat(),
               renewals=int(cur.get("renewals") or 0) + 1)
    rsi._atomic_write(lp, json.dumps(cur, indent=2))
    sl._record_lease(plan_dir, resource, cur, lp)
    rsi.log_event(plan_dir, "shipping_lease_renewed", resource=str(resource),
                  token=cur["token"], lock_file=str(lp), expires_at=cur["expires_at"])
    return True


def _read_or_empty(sl, lp):
    """The lease file, or {} when it cannot be read: the holder then cannot prove it holds
    the lease (renew False, holds_lease False), and the file is left untouched."""
    try:
        return sl._read_lock(lp)
    except rsi.LockError:
        return {}


def renew(plan_dir, resource):
    """The holder process extends its land lease, even after expiry. False (and a
    ``shipping_lease_renew_refused`` event) for another token or another process."""
    import ship_locks as sl  # noqa: PLC0415
    lp = sl._ship_lock_path(plan_dir, resource)
    if not lp.exists():
        return False
    with mutex(lp):
        cur, rec = _read_or_empty(sl, lp), sl.our_lease(plan_dir, resource)
        if rec and cur.get("token") == rec.get("token") and cur.get("holder_kind") == LAND \
                and is_holder(cur):
            renew_locked(plan_dir, resource, lp, cur)
            return True
    rsi.log_event(plan_dir, "shipping_lease_renew_refused", resource=str(resource),
                  lock_file=str(lp), holder_token=cur.get("token"), holder_pid=cur.get("pid"))
    return False


def holds_lease(plan_dir, resource):
    """Reads the ACTUAL lock file inside the mutex: our token, and a valid lease. A
    land lease is valid only for its holder process on this host (expired or not);
    any other lease while it is live."""
    import ship_locks as sl  # noqa: PLC0415
    lp = sl._ship_lock_path(plan_dir, resource)
    if not lp.exists():
        return False
    with mutex(lp):
        cur, rec = _read_or_empty(sl, lp), sl.our_lease(plan_dir, resource)
        return bool(rec and cur.get("token") == rec.get("token")
                    and (is_holder(cur) if cur.get("holder_kind") == LAND
                         else sl._lease_state(cur) == "live"))


def renew_or_park(plan_dir, ctx, st, when):
    """Renew before a land step, a gate or a push attempt. None when renewed;
    a ``lease-lost`` park (writing nothing else) when the lease is no longer ours."""
    import land_state as lst  # noqa: PLC0415
    if renew(plan_dir, ctx["resource"]):
        return None
    return lst.park(plan_dir, ctx, st, "lease-lost-renew",
                    f"LAND PARKED — this process could not renew {ctx['resource']} ({when}): "
                    "the lease is no longer ours (another token, or another process). §3.5: "
                    "nothing more is written under it. Re-run `run.py land "
                    f"{plan_dir}` once the other run is over.")


class RunLocked(rsi.LockError):
    """Another process of this plan holds the land run lock (``land-running``)."""


@contextmanager
def run_lock(plan_dir):
    """R6 §17.5.2 — the per-plan land run lock: ``flock(LOCK_EX | LOCK_NB)`` on
    ``<plan_dir>/.land-run.lock``, held by this process for the whole ``with`` and released
    by the OS when it exits. Taken ONCE per process, at the CLI entry of land, land-resume,
    land-ack, land-record and land-verify: a second ``flock`` on the same file in the same
    process is refused (errno 35 on macOS), so nothing below this may take it again.
    Not ``rsi.acquire_lock``: that checks, then overwrites, so two callers both get a token."""
    with open(Path(plan_dir) / ".land-run.lock", "a") as f:    # closing releases the flock
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES):
                raise RunLocked(f"another process of {plan_dir} is running land") from e
            raise rsi.LockError(f"cannot take the land run lock for {plan_dir} ({e}); fail "
                                "closed.") from e
        yield
