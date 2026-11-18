"""Processes whose working folder is a DELETED copy of the land worktree.

`run.py land` deletes and recreates the `__land-*` worktree at the same path on every
run. A codex-companion broker started by an earlier review keeps the old folder as its
cwd, so the next review from that path fails "failed to load configuration" (2026-09-26,
2026-11-11). Same path, different inode = stale. This only reports: the orchestrator
stops the brokers ITS review started, never another session's.
"""

import os
import subprocess


def stale_cwd_pids(path):
    """PIDs whose cwd is `path` by name but not the folder now at `path`. [] when lsof is
    missing or slow: the review still runs, and a stale broker fails it loudly."""
    try:
        now = os.stat(path).st_ino
        out = subprocess.run(["lsof", "-d", "cwd", "-Fpin"], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    want, pids, pid, ino = os.path.realpath(path), [], None, None
    for line in out.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            pid, ino = int(val), None
        elif tag == "i":
            ino = int(val)
        elif tag == "n" and ino is not None and ino != now and os.path.realpath(val) == want:
            pids.append(pid)
    return sorted(set(pids))
