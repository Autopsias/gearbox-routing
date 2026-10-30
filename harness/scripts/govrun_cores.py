"""govrun's machine-wide CPU budget: core tokens.

WHY. The worker slot only ever saw pytest and docker. On 2026-10-19 the load
average on this 12-core Mac was 80-107 while the slot sat FREE: two video
render/QA steps used ~3.7 cores each and six GitHub runners ran suites outside
govrun. Every heavy job now takes core tokens from ONE pool, so a video step and
a test suite count against the same budget instead of each lock seeing only
its own jobs.

HOW. The pool is `CORE_BUDGET` lock files, `core-<i>.lock` in the state dir,
taken with the same `_try_lock` the slot uses (lockf, per-process, survives
exec, released by process death — see govrun.py's docstring). A job takes ALL
the tokens it asks for or none: it never sits holding a partial set, so two
jobs can never deadlock each other on half a budget.

ponytail: tokens are accounting, not a cgroup. A job that asks for 4 and runs
12 threads still uses 12 — the payload must size itself from GOVRUN_CORES (or
-n auto, which reads PYTEST_XDIST_AUTO_NUM_WORKERS). Polling with all-or-nothing
is first come, first served through the "cores" ticket queue (govrun_queue), so
a big request is never overtaken by later small ones; it can wait behind an
earlier big one while small ones would fit. Fine while the callers are 4-core
video steps and 4-worker suites.
"""
import os
import sys
import time

from govrun_config import EXIT_QUEUE_TIMEOUT, POLL_SECONDS
from govrun_errors import Refused
from govrun_locks import NOTE_SECONDS, _try_lock
from govrun_queue import ahead, drop_ticket, take_ticket

# 12 cores minus 2 for the desktop, the Claude sessions and the antivirus, which
# together used 2-3 cores in the 2026-10-19 samples and are never wrapped.
DEFAULT_CORE_BUDGET = 10


def core_budget():
    raw = os.environ.get("GOVRUN_CORE_BUDGET", "").strip()
    if not raw:
        return DEFAULT_CORE_BUDGET
    try:
        value = int(raw)
    except ValueError:
        raise Refused(f"GOVRUN_CORE_BUDGET must be a whole number, got {raw!r}")
    if value < 1:
        raise Refused(f"GOVRUN_CORE_BUDGET must be >= 1, got {value}")
    return value


def _token(i):
    return {"name": f"core-{i}"}


def _take(want, budget):
    """`want` locked fds, or [] — never a partial set."""
    fds = []
    for i in range(budget):
        fd = _try_lock(_token(i))
        if fd is not None:
            fds.append(fd)
            if len(fds) == want:
                return fds
    for fd in fds:
        os.close(fd)
    return []


def acquire_cores(want, max_wait, waited_before=0.0):
    """Poll until `want` tokens are free. Returns (fds, waited_seconds).

    `waited_before` is time already spent queueing for the worker slot, so one
    `--max-wait` bounds the whole wait, not each half of it.
    """
    budget = core_budget()
    if want > budget:
        raise Refused(f"asks for {want} cores but the machine budget is {budget}. "
                      "Nothing ran.")
    start = time.monotonic() - waited_before
    next_note = 0.0
    ticket_fd, ticket = take_ticket("cores")
    while True:
        fds = _take(want, budget) if ahead(ticket) == 0 else []
        if fds:
            drop_ticket(ticket_fd, ticket)
            return fds, time.monotonic() - start
        waited = time.monotonic() - start
        if max_wait and waited >= max_wait:
            sys.stderr.write(
                f"govrun: queue timeout, not a test failure — waited {waited:.0f}s "
                f"for {want} free cores of {budget} and gave up (--max-wait "
                f"{max_wait:g}). The machine is busy; nothing ran. "
                f"{budget - free_cores(budget)} of {budget} cores are held. "
                "Retry later, or pass --max-wait 0 and run in the background.\n")
            sys.exit(EXIT_QUEUE_TIMEOUT)
        if waited >= next_note:
            sys.stderr.write(f"govrun: queued {waited:.0f}s for {want} free cores, "
                             f"{ahead(ticket)} job(s) ahead; "
                             f"{budget - free_cores(budget)} of {budget} held.\n")
            sys.stderr.flush()
            next_note = waited + NOTE_SECONDS
        time.sleep(POLL_SECONDS)


def free_cores(budget=None):
    """How many tokens nobody holds. Asks with lockf and lets go at once."""
    budget = budget or core_budget()
    free = 0
    for i in range(budget):
        fd = _try_lock(_token(i))
        if fd is not None:
            free += 1
            os.close(fd)
    return free
