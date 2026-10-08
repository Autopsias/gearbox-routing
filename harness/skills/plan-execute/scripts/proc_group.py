"""Run a child in its OWN process group, and kill the whole group.

WHY THIS EXISTS (measured 2026-08-21, profile-a-brain self-healing-vault s10).

`subprocess.run(..., timeout=N)` kills its DIRECT child only. `claude -p` — the
reviewer `llm_review_gate.run_once` launches — spawns helpers of its own, so a
timeout killed the launcher and REPARENTED the reviewer to init: one orphan was
observed at 34 minutes (`ppid=1`) after the gate had already reported
`timeout after 1800s`, still holding a live model connection with nobody reading
its output. Three of that session's five gate runs timed out while an orphan
from the previous run was still alive, so raising the timeout makes it WORSE — a
longer window breeds a longer-lived orphan.

Two halves, and BOTH are needed:

  * `start_new_session=True` puts the child and everything it spawns in one
    process group this module can kill by group id.
  * signal handlers that kill that group before this process dies. Without them
    the first half makes things worse, not better: a new session is deliberately
    OUTSIDE the group our own parent kills, so a caller that reaps us by group
    (profile-a-brain's `tools/llm_review_scoped.py` does exactly that) would no
    longer reach the reviewer at all.

`scripts/codex_supervised.py` solves the same problem for `codex exec`, but is
built around supervising a JSONL event stream for stalls; this is the plain
run-with-a-timeout case.
"""
import os
import signal
import subprocess

#: Signals whose default action ends this process. Each is intercepted only for
#: as long as a child is running, then restored.
_FATAL = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

#: Seconds to wait for a KILLED child's pipes to close before giving up on them.
REAP_S = 5


def kill_group(proc):
    """SIGKILL the child's whole process group. Never raises.

    REFUSES to killpg our OWN group. A child started without
    `start_new_session=True` shares the caller's group, so the obvious
    `killpg(getpgid(child))` sends SIGKILL to the caller as well — measured
    while neuter-probing this module: the probe exited 137 and took the test
    runner with it. Never act on a derived target that can be yourself.
    """
    try:
        pgid = os.getpgid(proc.pid)
        if pgid != os.getpgid(0):
            os.killpg(pgid, signal.SIGKILL)
            return
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run(argv, cwd=None, timeout=None, env=None, input=None):  # noqa: A002 — subprocess.run's name
    """`subprocess.run`-shaped, but the TIMEOUT kills the whole process tree.

    Returns a CompletedProcess. Raises `subprocess.TimeoutExpired` on timeout,
    exactly like `subprocess.run`, so a caller's existing `except` clause keeps
    working — the difference is that by the time it is raised, nothing the child
    spawned is still alive.

    `input` is text written to the child's stdin and is named after
    `subprocess.run`'s own parameter, so this stays a drop-in replacement. Left out,
    stdin is /dev/null exactly as before: a child that reads a closed stdin gets EOF
    rather than blocking on a terminal nobody is watching.
    """
    proc = subprocess.Popen(argv, cwd=cwd, env=env,
                            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)

    def _reap(signum, _frame):
        kill_group(proc)
        signal.signal(signum, signal.SIG_DFL)   # re-raise as the default action,
        os.kill(os.getpid(), signum)            # so the caller sees a normal death

    previous = {}
    for sig in _FATAL:
        try:
            previous[sig] = signal.signal(sig, _reap)
        except (ValueError, OSError):           # not the main thread, or unsupported
            pass
    try:
        try:
            stdout, stderr = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc)
            # BOUNDED. An unbounded reap here hangs FOREVER whenever the group
            # kill misses something holding the stdout pipe open — measured: a
            # neuter probe of this module blocked 120s on a surviving grandchild
            # that had inherited the pipe. That is a worse failure than the
            # orphan this module exists to prevent, so the reap gets a deadline
            # and the pipes are dropped if it expires.
            try:
                proc.communicate(timeout=REAP_S)
            except subprocess.TimeoutExpired:
                pass
            raise
        except BaseException:                   # noqa: BLE001 — KeyboardInterrupt too
            kill_group(proc)
            raise
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
    return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
