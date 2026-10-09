"""Row builders and CLI-arg shims shared by the agent-janitor suites.

Extracted when test_agent_janitor.py was split at the prune boundary. Nothing
here is mocked: `aged` and `only` rewrite ONLY the age field of a real ``ps``
snapshot, so the parsing, the argv, the PPID and the process all stay real.
"""
import argparse
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_janitor as aj  # noqa: E402

HOUR = 3600
TEST_SLUG = f"-agent-janitor-test-{os.getpid()}"
TEST_SLUG_DIR = f"{aj.SCRATCHPAD_BASE}/{TEST_SLUG}"



def aged(procs, pids, seconds):
    """Rewrite only the age of real Proc rows — everything else stays real."""
    for p in procs:
        if p.pid in pids:
            p.age = seconds
    return procs


def only(procs, pids, seconds):
    """Age `pids` and make every OTHER PPID-1 process too young to be a candidate.

    An --apply test must not reap this machine's genuine debris as a side effect;
    zeroing the age of unrelated PPID-1 processes drops them at the age gate
    without touching any of the logic under test.

    Other claude/codex sessions on the machine are dropped from the snapshot:
    one that exits before the registry's lsof call reads as cwd=None, the
    registry turns `incomplete`, and --apply exits 2 (seen in a `make check`
    gate). Dropping them weakens no safety the test relies on —
    a candidate needs PPID 1, and every unrelated PPID-1 process is already
    aged to 0 above.
    """
    procs = [p for p in procs
             if p.pid in pids or p.base not in aj.SESSION_BASENAMES]
    for p in procs:
        if p.pid not in pids and p.ppid == 1:
            p.age = 0
    return aged(procs, pids, seconds)


def fake(command, pid=999001, ppid=1, uid=None, age=99999):
    return aj.Proc(pid, ppid, aj.UID if uid is None else uid, age,
                   "Mon Aug  3 00:00:00 2026", command)


def reap_args(apply=False, session_id=None):
    return argparse.Namespace(apply=apply, dry_run=not apply, session_id=session_id)


def logged(text=None):
    path = os.path.join(aj.LOG_DIR, aj.LOG_NAME)
    if not os.path.exists(path):
        return []
    lines = Path(path).read_text().splitlines()
    return [ln for ln in lines if text is None or text in ln]


# --------------------------------------------------------------------------
