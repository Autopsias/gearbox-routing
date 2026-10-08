"""Tests for proc_group.py — a timeout must kill the child's WHOLE tree.

The defect this module exists for is not the child, it is the GRANDCHILD:
`subprocess.run(..., timeout=N)` kills the direct child and reparents whatever
it spawned to init. Every test below therefore asserts on a grandchild, because
a test that only checks the child passes against the broken implementation.

Run: pytest skills/plan-execute/scripts/test_proc_group.py -q
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import proc_group as pg  # noqa: E402

#: A shell that spawns a long-lived grandchild, prints its pid, then sleeps.
#: The grandchild outlives the child on purpose — killing the child alone leaks it.
_SPAWNER = (
    'sleep 120 & echo $! ; '
    'exec sleep 120'
)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                      # exists, owned by someone else
        return True
    return True


def _wait_gone(pid, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


#: How long the spawner gets to start, fork its grandchild and write the pid.
#: The timeout under test KILLS the shell when it fires, so a shell that has not
#: written the pid by then never will — polling afterwards cannot recover it.
#: 1.0s was too tight: the gate runs 62 pytest shards at once and this file failed
#: there while passing alone (measured 2026-09-20, both tests, on an untouched
#: checkout of the default branch). Each test therefore costs this many seconds.
_SPAWN_WINDOW = 5.0


def _read_pid(out, seconds=10.0):
    """Wait for the spawner's pid file, then return the grandchild pid.

    Covers the half of the race that is still recoverable: the shell created the
    file but has not finished writing it. The unrecoverable half — the shell was
    killed before it created the file at all — is what `_SPAWN_WINDOW` buys room
    for.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            text = out.read_text().split()
        except FileNotFoundError:
            text = []
        if text:
            return int(text[0])
        time.sleep(0.05)
    raise AssertionError(f"spawner never wrote a pid to {out} within {seconds}s")


def test_timeout_kills_the_grandchild(tmp_path):
    """The whole point. A plain subprocess.run leaves this pid alive."""
    out = tmp_path / "pid"
    script = tmp_path / "spawn.sh"
    script.write_text(f'#!/bin/sh\n{{ {_SPAWNER} ; }} > "{out}"\n')
    script.chmod(0o755)

    with pytest.raises(subprocess.TimeoutExpired):
        pg.run(["/bin/sh", str(script)], timeout=_SPAWN_WINDOW)

    grandchild = _read_pid(out)
    assert _wait_gone(grandchild), (
        f"grandchild {grandchild} survived the timeout — the process group was not killed")


def test_plain_subprocess_run_leaks_it(tmp_path):
    """The known positive: prove the test above can fail, and why it exists.

    If this ever stops leaking, CPython changed and proc_group may be redundant.
    """
    out = tmp_path / "pid"
    script = tmp_path / "spawn.sh"
    script.write_text(f'#!/bin/sh\n{{ {_SPAWNER} ; }} > "{out}"\n')
    script.chmod(0o755)

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(["/bin/sh", str(script)], timeout=_SPAWN_WINDOW, capture_output=True)

    grandchild = _read_pid(out)
    try:
        assert _alive(grandchild), "subprocess.run no longer leaks the grandchild"
    finally:
        try:
            os.kill(grandchild, 9)
        except ProcessLookupError:
            pass


def test_normal_exit_returns_a_completed_process():
    res = pg.run([sys.executable, "-c", "print('hi')"], timeout=30)
    assert res.returncode == 0
    assert res.stdout.strip() == "hi"
    assert isinstance(res, subprocess.CompletedProcess)


def test_nonzero_exit_is_returned_not_raised():
    res = pg.run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30)
    assert res.returncode == 3


def test_stderr_is_captured_separately():
    res = pg.run([sys.executable, "-c", "import sys; sys.stderr.write('bad')"], timeout=30)
    assert res.stderr == "bad"
    assert res.stdout == ""


def test_signal_handlers_are_restored():
    """A gate runs several reviewer attempts; the handlers must not accumulate."""
    import signal
    before = signal.getsignal(signal.SIGTERM)
    pg.run([sys.executable, "-c", "pass"], timeout=30)
    assert signal.getsignal(signal.SIGTERM) is before


def test_kill_group_on_a_dead_process_never_raises():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()
    pg.kill_group(proc)          # must not raise


def test_kill_group_never_kills_its_own_group(tmp_path):
    """A same-group child must not take the caller down with it.

    Reachable whenever kill_group is handed a process that was NOT started with
    start_new_session=True. Measured: killpg on our own group exits the caller 137.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    assert os.getpgid(proc.pid) == os.getpgid(0), "fixture assumption: same group"
    pg.kill_group(proc)                 # we must still be here afterwards
    proc.wait(timeout=5)
    assert _wait_gone(proc.pid), "the same-group child was not killed at all"
