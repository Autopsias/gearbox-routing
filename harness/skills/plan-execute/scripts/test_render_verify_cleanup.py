"""render_verify must never leave a Chrome process behind.

2026-09-27: a headless Chrome launched by test_flag_off_actually_runs_the_real_check
was found reparented to PID 1, still running 23 h later. subprocess.run(timeout=)
kills only the direct child, never the helpers Chrome forks. This fake chrome
forks a sleeping child and then never exits, so it exercises both the timeout
and the orphaned-child path at once.
"""
import os
import signal
import subprocess
import time

import render_verify as rv


def _alive(pid):
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


def test_no_chrome_child_survives_the_render_call(tmp_path, monkeypatch):
    (tmp_path / "PLAN.html").write_text("<html></html>")
    pidfile = tmp_path / "child.pid"
    fake = tmp_path / "fake-chrome"
    fake.write_text(f'#!/bin/sh\nsleep 300 &\necho $! > "{pidfile}"\nexec sleep 300\n')
    fake.chmod(0o755)
    monkeypatch.delenv(rv.SKIP_ENV, raising=False)
    monkeypatch.setattr(rv, "_find_chrome", lambda: str(fake))

    out = rv.check(tmp_path, timeout=1)
    child = int(pidfile.read_text())
    try:
        assert out["status"] == "unavailable", out
        deadline = time.monotonic() + 3
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(child), f"fake chrome's child {child} outlived render_verify.check"
    finally:
        # Only the sleep this test itself forked, and only if the fix failed.
        if _alive(child):
            os.kill(child, signal.SIGKILL)


def test_killpg_permission_error_does_not_escape(tmp_path, monkeypatch):
    # macOS killpg returns EPERM for a zombie-only group; under 65 gate shards
    # that PermissionError escaped render_verify's finally block.
    (tmp_path / "PLAN.html").write_text("<html></html>")
    fake = tmp_path / "fake-chrome"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.delenv(rv.SKIP_ENV, raising=False)
    monkeypatch.setattr(rv, "_find_chrome", lambda: str(fake))

    def eperm(*_a):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", eperm)
    assert rv.check(tmp_path)["status"] == "not_applicable"
