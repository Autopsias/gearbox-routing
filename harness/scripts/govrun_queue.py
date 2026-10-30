"""govrun's first-come-first-served queue: one ticket per waiting job.

WHY. `acquire` used to admit whichever waiter polled first after the slot
freed. With six CI runners plus local suites all wrapped, a job could lose that
race for ever: an example_app test job queued 45 min (2703 s logged)
while later arrivals took the slot one after another, and its job timeout
cancelled it before it ever ran. A ticket queue admits only the oldest live
waiter, so a job waits for the jobs ahead of it and no longer.

HOW. A waiter creates `<state dir>/queue-<name>/<time_ns>-<pid>.ticket` and
holds a lockf lock on it while it waits — the same per-process record lock the
slot uses, so a waiter that dies (kill -9, CI timeout) drops its lock with no
cleanup path. Only the waiter with no LIVE older ticket may try the resource. A
ticket whose lock is free belongs to a dead process: the next prober deletes it.

The file is created under a `.tmp-` name, locked, then renamed into place, so no
prober can ever see a ticket that exists but is not yet locked (it would read
as dead and be deleted under its owner).

ponytail: one queue per resource ("slot", "cores"), not per class or per slot.
A slot job leaves the slot queue once it holds the slot and then queues for its
cores; a cores-only job (video) never waits behind a pytest job that is still
waiting for the slot. Ordering is by wall-clock time_ns, then pid — a clock step
back can reorder two arrivals in the same instant; fine for jobs minutes long.
"""
import errno
import fcntl
import os
import time

from govrun_config import state_dir


def _queue_dir(queue):
    d = state_dir() / f"queue-{queue}"
    d.mkdir(exist_ok=True)
    return d


def take_ticket(queue):
    """Join the queue. Returns (fd, path); the lock lives as long as the fd."""
    d = _queue_dir(queue)
    name = f"{time.time_ns():020d}-{os.getpid():07d}"
    tmp = d / f".tmp-{name}"
    fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a fresh file: never busy
    path = d / f"{name}.ticket"
    os.rename(tmp, path)  # same inode, so the lock comes along
    return fd, path


def drop_ticket(fd, path):
    """Leave the queue: the next waiter becomes the head at its next poll."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    os.close(fd)


def _alive(path):
    """True while the ticket's owner holds its lock. Probes with lockf."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EACCES):
            return True
        raise
    finally:
        os.close(fd)  # drops the probe's own lock if it got one
    return False


def ahead(path):
    """How many live waiters joined before this ticket. Deletes dead ones."""
    count = 0
    for other in sorted(path.parent.glob("*.ticket")):
        if other.name >= path.name:
            break
        if _alive(other):
            count += 1
        else:
            try:
                other.unlink()
            except FileNotFoundError:
                pass
    return count


def waiting(queue):
    """Live tickets in a queue, for --status. Read-only: deletes nothing."""
    d = state_dir() / f"queue-{queue}"
    return sum(1 for p in d.glob("*.ticket") if _alive(p)) if d.is_dir() else 0
