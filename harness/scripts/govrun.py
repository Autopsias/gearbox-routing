#!/usr/bin/env python3
"""govrun — machine-wide admission gate for heavy jobs. Python stdlib only.

Heavy jobs (a pytest run with xdist workers, a docker build) are wrapped:
``govrun -- pytest -n auto``. govrun hands out a limited number of SLOTS; a job
with no free slot QUEUES instead of failing, and on acquire it exports the
slot's worker budget as ``PYTEST_XDIST_AUTO_NUM_WORKERS`` so ``-n auto`` sizes
itself to the slot rather than to the machine.

The PreToolUse enforcement hook that refuses an unwrapped heavy command is not
part of this export, so without it ``govrun --status`` reports DISARMED.

Why a lock and not a daemon: the lock lives and dies with the process. A clean
exit, a crash, a ``kill -9`` and a CI timeout all free the slot with no cleanup
path and nothing to reap. There is deliberately no release code here — release
is process death. (Same instinct as the no-``git stash`` rule: never leave
hidden state that outlives its owner.)

WHICH process death, though, is the whole question, and it is why this uses a
POSIX record lock (``fcntl.lockf``) and not ``fcntl.flock``. A flock belongs to
the OPEN FILE DESCRIPTION, so every descendant that inherits the fd holds it
too: a payload that backgrounds anything — ``pytest &``, a test that leaves a
server running, a shell wrapper that spawns a daemon — hands the slot to a
process nobody is waiting on, and the next job queues behind a ghost. Measured
on macOS with the payload ``sh -c 'sleep 8 & exit 0'``: under ``flock`` the
next contender waited the full 8 s, i.e. for the orphan, not the payload; under
``lockf`` it acquired at once. A
record lock is owned by the PROCESS — preserved across ``execvp`` (so the
payload keeps the slot) and NOT inherited across ``fork`` (so no descendant can
keep it). Both halves are measured and both are pinned by tests.

The cost, stated plainly: the slot is bound to the process govrun exec'd into,
so a payload that forks the real work and exits immediately (a daemonising
wrapper) frees the slot while that work runs on. That direction was chosen
deliberately — a slot released early is re-admitted by the next job, while a
slot held by a ghost is lost until a human finds and kills the ghost, and on
the ci class, whose default ``--max-wait`` is 0, that is an unbounded hang.

The trade is also why nothing here forks: bounding the lock to the payload does
NOT require govrun to become a supervisor that holds the fd and waits. The
kernel already offers exactly these semantics, so ``execvp`` stays a bare
``execvp`` and govrun is gone the moment the payload starts.

ONE SHARED SLOT OF 4 WORKERS is the whole machine's budget, and both classes
queue for it. There is no per-class reservation, because 4 workers is all this
machine can actually run — see ``govrun_config.DEFAULT_SLOTS`` for the two
measurements that sized it — and with only 4 to hand out there is nothing left
to reserve. ``validate_slots`` still refuses an override that raises the total
past 4, and one that STRANDS a class: leaves a class no slot admits, so nothing
at that class could ever run.

The cost, stated plainly: an interactive job that arrives while a CI shard holds
the slot serialises behind it, reaches its 120 s ``--max-wait`` and exits 75
with "queue timeout, not a test failure". Nothing ran — the caller retries later
or passes ``--max-wait 0`` and launches in the background. That is the trade
taken deliberately: a legible refusal beats the OOM kill the earlier two-slot
table was measured to produce. Queue order is poll order with no aging (see
``acquire``), so a busy machine can make a caller wait several turns; accepted,
because the contenders here are a handful of long jobs, not a herd.

Trap, already paid for: Python file descriptors are close-on-exec by default
(PEP 446), so the lock DIES at ``execvp`` unless the fd is made inheritable
first. Measured on macOS: plain
``os.open`` -> the next contender acquired while the payload was still running;
``os.set_inheritable(fd, True)`` -> the lock survived. There is no ``os.open``
flag for this; the only one in that space, ``O_CLOEXEC``, makes it worse. This
is still needed with a record lock — the LOCK survives exec on its own, but only
for as long as the process still has an fd on the file.

Exit codes: 2 = refused (bad override, over-budget payload, usage), 75 = queue
timeout. Anything else is the payload's own exit code, because govrun is gone
by then.
"""
import math
import os
import sys

# govrun.py is split across sibling modules (govrun_config, govrun_locks,
# govrun_pytest_budget, govrun_pytest_ini, govrun_status, govrun_errors) that
# it imports below by bare name. `python3 /abs/path/govrun.py` and the
# `scripts/govrun` shim both get this for free — Python puts a script's own
# directory at sys.path[0] — but a caller that loads this file another way
# (importlib.util.spec_from_file_location from a `python3 -c` snippet, as the
# suite's `_lock_identity` ordering test does) does not: `-c` puts the
# CURRENT WORKING DIRECTORY on sys.path, not this file's directory. Measured:
# without this line that test's child process fails with
# "ModuleNotFoundError: No module named 'govrun_config'" no matter what cwd
# the parent test process itself happens to run from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from govrun_config import (
    DEFAULT_CLASS,
    DEFAULT_MAX_WAIT,
    DEFAULT_SLOTS,
    EXIT_REFUSED,
    NICE_BY_CLASS,
    load_slots,
    validate_slots,
)
from govrun_errors import Refused
from govrun_locks import _try_lock, acquire, write_holder
from govrun_pytest_budget import (
    VALUE_TAKING_SHORT_OPTS,
    check_budget,
    ini_addopts,
    pytest_option_stream,
    requested_workers,
)
from govrun_status import cmd_status

# The names above that govrun.py does not itself call are a RE-EXPORT, not dead
# imports: the suite loads govrun.py as one module (`govrun_module()` in
# conftest.py) and reads `mod.requested_workers`, `mod.pytest_option_stream`,
# `mod.ini_addopts`, `mod.VALUE_TAKING_SHORT_OPTS`, `mod.DEFAULT_SLOTS` and
# `mod.validate_slots` off it, because that is the argv this binary really runs
# with. `__all__` says so to a reader and to ruff, which otherwise reports each
# one as unused and a `--fix` would delete the surface the tests stand on.
__all__ = [
    "DEFAULT_CLASS", "DEFAULT_MAX_WAIT", "DEFAULT_SLOTS", "EXIT_REFUSED",
    "NICE_BY_CLASS", "Refused", "VALUE_TAKING_SHORT_OPTS", "_try_lock",
    "acquire", "check_budget", "cmd_status", "ini_addopts", "load_slots",
    "pytest_option_stream", "requested_workers", "validate_slots",
    "write_holder",
]


USAGE = """usage: govrun [--class ci|interactive] [--max-wait SECONDS] [--] COMMAND...
       govrun --status

  --class      which slot class to queue for (default: interactive)
  --max-wait   seconds to wait for a slot; 0 waits forever
               (default: interactive 120, ci 0)
  --status     print slot holders, hook wiring and budget agreement"""


# --- entry point ------------------------------------------------------------

def _opt_value(argv, i, name):
    if i + 1 >= len(argv):
        raise Refused(f"{name} needs a value\n\n{USAGE}")
    return argv[i + 1]


def _parse_max_wait(raw):
    try:
        value = float(raw)
    except ValueError:
        raise Refused(f"--max-wait must be a number of seconds, got {raw!r}")
    # `float()` happily accepts 'nan' and 'inf'. Both then defeat the very
    # bound this flag exists to impose: `waited >= nan` is ALWAYS false and
    # `waited >= inf` never becomes true, so the caller gets the unbounded
    # wait the defaults were chosen to prevent. Say so, and name `0`.
    if not math.isfinite(value):
        raise Refused(f"--max-wait must be a finite number of seconds, got "
                      f"{value!r} — it would never time out. Pass "
                      "--max-wait 0 if you really mean wait forever.")
    if value < 0:
        raise Refused("--max-wait must be >= 0 (0 waits forever)")
    return value


def parse_args(argv):
    """Hand-rolled so the PAYLOAD's own flags are never eaten. Stops at the
    first token that is not a govrun option (or at an explicit `--`)."""
    klass, max_wait, status, i = DEFAULT_CLASS, None, False, 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--status":
            status, i = True, i + 1
        elif arg in ("-h", "--help"):
            print(USAGE)
            sys.exit(0)
        elif arg == "--class":
            klass, i = _opt_value(argv, i, "--class"), i + 2
        elif arg.startswith("--class="):
            klass, i = arg.split("=", 1)[1], i + 1
        elif arg in ("--max-wait", "--max_wait"):
            max_wait, i = _opt_value(argv, i, "--max-wait"), i + 2
        elif arg.startswith("--max-wait="):
            max_wait, i = arg.split("=", 1)[1], i + 1
        elif arg == "--":
            i += 1
            break
        else:
            break
    if max_wait is not None:
        max_wait = _parse_max_wait(max_wait)
    return klass, max_wait, status, argv[i:]


def main(argv):
    klass, max_wait, status, cmd = parse_args(argv)
    slots = load_slots()
    if status:
        return cmd_status(slots)
    if not cmd:
        raise Refused(f"nothing to run\n\n{USAGE}")
    if max_wait is None:
        max_wait = DEFAULT_MAX_WAIT.get(klass, DEFAULT_MAX_WAIT[DEFAULT_CLASS])

    slot, fd, waited = acquire(slots, klass, max_wait)
    check_budget(cmd, slot["workers"])  # on acquire, BEFORE exec
    write_holder(slot, klass, cmd, waited)
    sys.stderr.write(f"govrun: acquired {slot['name']} after {waited:.0f}s\n")
    sys.stderr.flush()

    os.environ["PYTEST_XDIST_AUTO_NUM_WORKERS"] = str(slot["workers"])
    if klass in NICE_BY_CLASS:
        os.nice(NICE_BY_CLASS[klass])
    # The lock must OUTLIVE this process image. Without this the fd is
    # close-on-exec (PEP 446) and the slot frees itself the instant the payload
    # starts — measured, see the module docstring.
    os.set_inheritable(fd, True)
    try:
        os.execvp(cmd[0], cmd)
    except OSError as exc:
        raise Refused(f"cannot exec {cmd[0]!r}: {exc}")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refused as error:
        sys.stderr.write(f"govrun: {error}\n")
        sys.exit(EXIT_REFUSED)
    except KeyboardInterrupt:
        sys.exit(130)
