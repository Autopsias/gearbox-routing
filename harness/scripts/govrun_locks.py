"""govrun's POSIX record-lock slot acquisition.

Split out of govrun.py — see its module docstring for the full rationale
behind ``fcntl.lockf`` (not ``flock``) and ``os.set_inheritable``, which this
module implements. It owns taking a slot's lock, polling until one is free,
and writing the best-effort holder metadata ``--status`` reads.

``_lock_path``/``_lock_identity`` live in govrun_config.py, not here, even
though ``_try_lock`` is their other caller: ``validate_slots`` needs them too,
and this module already imports ``state_dir`` from govrun_config, so keeping
them there is what keeps the two modules from importing each other.
"""
import errno
import fcntl
import json
import os
import shlex
import sys
import time
from datetime import datetime

from govrun_config import EXIT_QUEUE_TIMEOUT, POLL_SECONDS, _lock_path, state_dir
from govrun_errors import Refused


def _try_lock(slot):
    """An open, LOCKED fd, or None. Never leaks the fd on failure.

    ``lockf`` (a POSIX record lock), never ``flock`` — see the module docstring
    for why and for the measurement. Anything else that asks "is this slot free"
    must ask with ``lockf`` too (the suite's `lock_is_free` helper does): the two
    are separate interfaces and are not guaranteed to block each other, so an
    asker in the wrong family can get "free" for a slot that is held. On macOS
    they do happen to conflict — measured: all four holder/asker combinations of flock and
    lockf blocked — but that is a Darwin
    property, not a portable one, and the suite also runs on Linux.

    ponytail: whole-file lock, byte range 0. Record locks can lock ranges; a slot
    is not divisible, so there is nothing to range over.

    Ceiling to know about: a record lock is dropped when the OWNING PROCESS
    closes ANY fd on that file, so nothing may open-and-close a lock file this
    process has locked. `_lock_identity` is the one place that was tempted to;
    it uses `os.stat` instead, which opens no descriptor. Anything new that
    touches a lock file has the same obligation — including the guard below,
    which is why it asks with `os.fstat`/`os.stat` and never re-opens the path.

    THE GUARD: a lock on an unlinked inode guards nothing. If the state
    directory is deleted in the window between the `os.open` and the `lockf`,
    this process ends up holding an exclusive lock on a file no name reaches,
    while the next job creates a fresh file at the same path and locks that one
    at once — two jobs, one slot, 8 workers, the configuration one project recorded
    as fatal. So after the lock is taken, ask whether the
    descriptor is still the file the path names, and REFUSE if it is not.
    `os.fstat(fd)` answers for the descriptor, `os.stat(path)` for the name; a
    `(st_dev, st_ino)` mismatch means the file was replaced, `st_nlink == 0`
    means it was unlinked. `path` is the one computed above, never a fresh
    `_lock_path` call — `state_dir()` re-creates the directory on every call, so
    re-deriving it here would silently mkdir the very deletion we are testing for.

    ponytail: this closes the window it can see — an acquisition racing the
    delete. It cannot close the whole hazard, and does not pretend to: a job
    that STARTS after the directory is gone gets a re-created directory and a
    brand-new lock file that is self-consistent, so it acquires normally while
    an older job still holds the unlinked inode. Measured, both ways.
    Nothing reachable from
    here can see that older holder — after the delete there is no shared kernel
    object left to ask. The upgrade path, if the directory ever has to be
    deletable: put the rendezvous somewhere `rm -rf` of the state dir cannot
    reach, or make the lock files an explicit install step so a missing one is
    an error rather than a fresh start. Until then the rule stands: do not delete the state directory.
    """
    path = _lock_path(slot)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        # BUSY, and only busy. POSIX says a non-blocking record lock that
        # someone else holds fails with EACCES or EAGAIN (and the two are the
        # same value on Linux, different on Darwin, which is why both are
        # named). EVERY OTHER errno is a broken lock, not a queue: EBADF,
        # ENOLCK (the kernel table is full), EDEADLK, EINVAL, EOPNOTSUPP on a
        # filesystem with no record locking. Swallowing those returned None,
        # which the caller reads as "slot held" — so the job polls until
        # --max-wait and exits 75 with a queue-timeout message, and the real
        # fault never surfaces. Raise instead: a governor that cannot lock must
        # say so.
        if exc.errno not in (errno.EAGAIN, errno.EACCES):
            raise Refused(
                f"slot {slot['name']!r}: locking {path} failed with "
                f"{errno.errorcode.get(exc.errno, exc.errno)} ({exc.strerror}). "
                "That is not a busy slot — it is a lock that does not work, so "
                "govrun cannot tell whether anything else is running. Nothing "
                "ran. Check the state directory's filesystem (record locking is "
                "not supported everywhere)."
            ) from exc
        return None
    held = os.fstat(fd)
    try:
        named = os.stat(path)
        replaced = (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
    except OSError:
        replaced = True  # the name is gone entirely
    if replaced or held.st_nlink == 0:
        # Closing drops the lock we just took, which is the point: it is a lock
        # on a file nothing can reach, and holding it would only hide the fault.
        os.close(fd)
        raise Refused(
            f"slot {slot['name']!r}: the lock file {path} was deleted or "
            "replaced while this job was locking it, so the lock guards "
            "NOTHING and another job could hold the same slot at the same "
            f"time. Something removed govrun's state directory ({path.parent}) "
            "— "
            "deleting it is never a valid step. Nothing ran. Let the jobs that "
            "are running finish, then retry.")
    return fd


def acquire(slots, klass, max_wait):
    """Poll eligible slots until one locks. Returns (slot, fd, waited_seconds).

    ponytail: queue order is POLL ORDER, not FIFO — a late arrival can win a
    slot ahead of a job that has waited longer, and there is no aging. Accepted:
    the contenders here are a handful of long jobs, not a thundering herd.
    Upgrade path if it ever bites: a ticket file per waiter (monotonic counter
    in the state dir) and admit only the lowest outstanding ticket.
    """
    eligible = [s for s in slots if klass in s["classes"]]
    if not eligible:
        raise Refused(f"no slot admits class {klass!r}; slots admit "
                      f"{sorted({c for s in slots for c in s['classes']})}")
    start = time.monotonic()
    while True:
        for slot in eligible:
            fd = _try_lock(slot)
            if fd is not None:
                return slot, fd, time.monotonic() - start
        waited = time.monotonic() - start
        if max_wait and waited >= max_wait:
            sys.stderr.write(
                f"govrun: queue timeout, not a test failure — waited "
                f"{waited:.0f}s for a free {klass!r} slot and gave up "
                f"(--max-wait {max_wait:g}). The machine is busy; nothing ran. "
                "Retry later, or pass --max-wait 0 and run in the background.\n")
            sys.exit(EXIT_QUEUE_TIMEOUT)
        time.sleep(POLL_SECONDS)


def write_holder(slot, klass, cmd, waited):
    """Best-effort provenance for humans. Never fatal, never cleaned up — the
    next acquirer overwrites it, and a dead pid here is stale, not a leak."""
    payload = {
        "pid": os.getpid(),
        "command": " ".join(shlex.quote(c) for c in cmd),
        "class": klass,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "queued_seconds": round(waited, 1),
    }
    try:
        (state_dir() / f"{slot['name']}.holder.json").write_text(
            json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        sys.stderr.write(f"govrun: could not write holder metadata: {exc}\n")


