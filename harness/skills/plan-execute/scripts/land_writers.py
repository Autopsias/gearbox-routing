"""§18.5 — a local push to main waits, bounded, while a land pushes main.

``githooks/pre-push`` runs ``land_writers.py hook <remote> <url>`` with git's ref
lines on stdin. For a push to the default branch it waits while the host-wide
marker (§18.6, written by ``land_phase2``) is live, at most ``WAIT_CAP_S``.

It fails open: a missing, expired or unreadable marker, any error, or the cap
lets the push go on. It never takes or touches the lease. Every pre-push appends
one ``land_writer_wait`` line to ``~/.claude/.land-phase/writers.ndjson``.

Standard library plus the sibling ``safe_json``, and Python 3.9-safe: the hook runs
whatever ``python3`` is first on PATH, and it must stay cheap (<200 ms) when no marker exists.
"""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import safe_json as sj

WAIT_CAP_S = 300            # §18.6: the hook wait cap, kept apart from the deadline and expiry
DEADLINE_GRACE_S = 1.0      # the hook's own timer fires this long after the cap
POLL_S = 0.5
TOKEN_ENV = "PLAN_LAND_PHASE_TOKEN"
MARKER_DIRNAME = ".land-phase"
LOG_NAME = "writers.ndjson"


def marker_dir():
    return Path.home() / ".claude" / MARKER_DIRNAME


def remote_hash(remote_url):
    """First 16 hex of SHA-256 of the URL with any trailing ``/`` and ``.git`` removed."""
    u = str(remote_url).strip().rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    return hashlib.sha256(u.encode()).hexdigest()[:16]


def marker_path(remote_url):
    return marker_dir() / f"{remote_hash(remote_url)}.json"


def _read_marker(path):
    """None when absent; the dict otherwise. Anything unreadable raises (-> ``error``)."""
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    m = sj.load_dict(raw)
    if m is None:
        raise ValueError("marker is not a readable JSON object")
    return m


def _live(m):
    """A marker is live while it is a push-phase marker and its expiry is ahead."""
    if m.get("phase", "push") != "push":   # a non-string phase is not "push": no wait
        return False
    raw = str(sj.get_str(m, "expires_at") or sj.get_str(m, "phase_expires_at"))
    if raw.endswith("Z"):   # Python 3.9's fromisoformat refuses a trailing Z
        raw = raw[:-1] + "+00:00"
    exp = datetime.fromisoformat(raw)
    if exp.tzinfo is None:
        raise ValueError("marker expiry has no timezone")
    return datetime.now(timezone.utc) < exp


def _log(remote_url, outcome, waited_s, token=None, **extra):
    rec = {"event": "land_writer_wait", "remote_hash": remote_hash(remote_url),
           "outcome": outcome, "waited_s": round(waited_s, 3),
           "ts": datetime.now(timezone.utc).isoformat(), **extra}
    if token is not None:
        rec["marker_token"] = token
    try:
        marker_dir().mkdir(parents=True, exist_ok=True)
        with open(marker_dir() / LOG_NAME, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError:
        pass                # a log we cannot write never holds a push


def _wait(path, cap_s, start):
    token, waited = None, False
    while True:
        m = _read_marker(path)
        if m is None:
            return ("clear" if waited else "no-marker"), token
        token = sj.get_str(m, "token")
        own = os.environ.get(TOKEN_ENV)
        if own and token and own == token:
            return "own-token", token
        if not _live(m):
            return "expired", token
        left = cap_s - (time.monotonic() - start)
        if left <= 0:
            return "cap", token
        waited = True
        time.sleep(min(POLL_S, left))


def wait_for_land(remote_url, cap_s=WAIT_CAP_S):
    """Return ``clear``, ``expired``, ``cap``, ``no-marker``, ``own-token`` or ``error``;
    logs one ``land_writer_wait`` line. Never raises, never waits past ``cap_s``."""
    start = time.monotonic()
    token, extra = None, {}
    try:
        outcome, token = _wait(marker_path(remote_url), cap_s, start)
    except Exception as e:  # noqa: BLE001 — §18.5 invariant 2: any failure lets the push go
        outcome, extra = "error", {"error": f"{type(e).__name__}: {e}"[:300]}
    _log(remote_url, outcome, time.monotonic() - start, token, **extra)
    return outcome


class _Deadline(BaseException):
    """Raised by the hook's timer; a BaseException so no inner handler swallows it."""


def _on_alarm(signum, frame):
    raise _Deadline("hook passed its own deadline")


def _arm(seconds):
    """Start a timer that raises ``_Deadline`` even inside a read that never returns
    (a named pipe, a stalled filesystem). Returns the old handler, or None off the
    main thread, where signals cannot be set and only the poll cap holds."""
    try:
        old = signal.signal(signal.SIGALRM, _on_alarm)
    except ValueError:
        return None
    signal.setitimer(signal.ITIMER_REAL, seconds)
    return old


def _disarm(old):
    if old is not None:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _default_branch(remote):
    """``main`` unless ``refs/remotes/<remote>/HEAD`` names another branch."""
    try:
        p = subprocess.run(["git", "symbolic-ref", "--quiet", "--short",
                            f"refs/remotes/{remote}/HEAD"],
                           capture_output=True, text=True, timeout=10, check=False)
        name = p.stdout.strip()
        if p.returncode == 0 and name.startswith(f"{remote}/"):
            return name[len(remote) + 1:]
    except (OSError, subprocess.SubprocessError):
        pass
    return "main"


def hook(argv, stdin):
    """The pre-push entry: ``argv`` is ``[remote, url]``, ``stdin`` git's ref lines.
    Always returns 0."""
    url = argv[1] if len(argv) > 1 else (argv[0] if argv else "")
    start = time.monotonic()
    old = _arm(WAIT_CAP_S + DEADLINE_GRACE_S)   # §18.5: never blocks past the cap
    try:
        try:
            refs = [p[2] for p in (ln.split() for ln in stdin.read().splitlines()) if len(p) == 4]
            if f"refs/heads/{_default_branch(argv[0])}" in refs:
                wait_for_land(url, WAIT_CAP_S)
            else:
                _log(url, "other-branch", 0.0)
        except (Exception, _Deadline) as e:  # noqa: BLE001 — fail open
            if old is not None:     # the error log gets its own short timer
                signal.setitimer(signal.ITIMER_REAL, DEADLINE_GRACE_S)
            _log(url, "error", time.monotonic() - start, error=f"{type(e).__name__}: {e}"[:300])
    except _Deadline:
        pass                        # the error log itself blocked: the push still goes on
    finally:
        _disarm(old)
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:
        sys.exit(hook(sys.argv[2:], sys.stdin))
    sys.exit(f"usage: {Path(sys.argv[0]).name} hook <remote> <url> < ref-lines")
