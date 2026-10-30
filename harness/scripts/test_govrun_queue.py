"""govrun's first-come-first-served ticket queue (govrun_queue.py).

Real govrun processes against a throwaway state dir. The order tests pause the
first waiter with SIGSTOP, so they do not depend on poll timing: a working
queue holds the second waiter back, and poll order lets it straight in.
"""
import os
import signal
import subprocess
import sys
import time

from conftest import GOVRUN, govrun
from govrun_config import POLL_SECONDS

HOLD = ["sh", "-c", "echo started; sleep 30"]


def _spawn(state, argv, budget=10):
    environ = dict(os.environ, GOVRUN_STATE_DIR=str(state),
                   GOVRUN_CORE_BUDGET=str(budget))
    environ.pop("GOVRUN_SLOTS", None)
    environ.pop("PYTEST_ADDOPTS", None)
    return subprocess.Popen([sys.executable, str(GOVRUN), *argv], env=environ,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _wait_started(proc):
    assert proc.stdout is not None and proc.stderr is not None
    assert proc.stdout.readline().strip() == "started", proc.stderr.read()


def _tickets(state, queue):
    d = state / f"queue-{queue}"
    return len(list(d.glob("*.ticket"))) if d.is_dir() else 0


def _wait_tickets(state, queue, n, timeout=10.0):
    deadline = time.monotonic() + timeout
    while _tickets(state, queue) < n:
        assert time.monotonic() < deadline, f"{queue}: never saw {n} tickets"
        time.sleep(0.05)


def _reap(*procs):
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def _order_test(state, tmp_path, holder_argv, waiter_argv, queue):
    """A waiter that is alive but cannot poll (SIGSTOP) keeps its place: the
    later waiter must NOT run past it. Poll order would let it straight in."""
    order = tmp_path / "order.txt"
    holder = _spawn(state, [*holder_argv, "--", *HOLD])
    _wait_started(holder)
    first = second = None
    try:
        first = _spawn(state, [*waiter_argv, "--", "sh", "-c", f"echo first >> {order}"])
        _wait_tickets(state, queue, 1)
        second = _spawn(state, [*waiter_argv, "--", "sh", "-c", f"echo second >> {order}"])
        _wait_tickets(state, queue, 2)
        first.send_signal(signal.SIGSTOP)
        holder.kill()
        holder.wait()
        time.sleep(POLL_SECONDS * 6)
        assert not order.exists(), "a later waiter ran while an earlier one waited"
        first.send_signal(signal.SIGCONT)
        for proc in (first, second):
            assert proc.wait(timeout=30) == 0, proc.stderr.read() if proc.stderr else ""
        assert order.read_text().split() == ["first", "second"]
    finally:
        for proc in (first, second):
            if proc is not None and proc.poll() is None:
                proc.send_signal(signal.SIGCONT)
        _reap(*[p for p in (holder, first, second) if p is not None])


def test_slot_waiters_run_in_arrival_order(state, tmp_path):
    _order_test(state, tmp_path, ["--class", "ci"], ["--class", "ci"], "slot")


def test_cores_waiters_run_in_arrival_order(state, tmp_path):
    _order_test(state, tmp_path, ["--cores", "10"], ["--cores", "6"], "cores")


def test_a_killed_waiter_does_not_block_the_queue(state):
    holder = _spawn(state, ["--class", "ci", "--", *HOLD])
    _wait_started(holder)
    dead = _spawn(state, ["--class", "ci", "--", "true"])
    try:
        _wait_tickets(state, "slot", 1)
        dead.send_signal(signal.SIGKILL)  # no cleanup runs: its ticket file stays
        dead.wait()
        assert _tickets(state, "slot") == 1
        holder.kill()
        holder.wait()
        r = govrun(state, "--class", "ci", "--max-wait", "5", "--", "true")
        assert r.returncode == 0, r.stderr
        assert _tickets(state, "slot") == 0  # the dead ticket was swept
    finally:
        _reap(holder, dead)


def test_a_timed_out_waiter_leaves_the_queue(state):
    holder = _spawn(state, ["--class", "ci", "--", *HOLD])
    _wait_started(holder)
    try:
        r = govrun(state, "--max-wait", "1", "--", "true")
        assert r.returncode == 75, r.stderr
        holder.kill()
        holder.wait()
        ok = govrun(state, "--max-wait", "5", "--", "true")
        assert ok.returncode == 0, ok.stderr
    finally:
        _reap(holder)


def test_queue_note_says_how_many_are_ahead(state):
    holder = _spawn(state, ["--class", "ci", "--", *HOLD])
    _wait_started(holder)
    first = _spawn(state, ["--class", "ci", "--", "true"])
    try:
        _wait_tickets(state, "slot", 1)
        r = govrun(state, "--max-wait", "1", "--", "true")
        assert "1 job(s) ahead" in r.stderr, r.stderr
        status = govrun(state, "--status")
        assert "queue: 1 waiting for the slot" in status.stdout, status.stdout
    finally:
        _reap(holder, first)
