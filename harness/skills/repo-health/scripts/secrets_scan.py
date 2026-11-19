#!/usr/bin/env python3
"""sec.secrets-history: scan only commits since the last completed scan, under a cap.

`health.py scan-secrets --repo R` runs gitleaks and records the check itself.
The full-history scan took 8m36s on 415 commits and later passed 15 minutes, so
the weekly run always timed out and never reached `render` (its only history
writer). Two rules fix that:

  * the history line records `secrets_scanned` (a full commit id). The next run
    scans `<marker>..HEAD` only. The marker moves ONLY after a scan that
    completed (read every
    commit) and found NO leaks (exit 0). A scan that found leaks (exit 1),
    timed out or crashed leaves the old marker, so a leak keeps failing until
    it is purged (a rewrite is not an ancestor: full scan). A marker
    that is not an ancestor of HEAD (rebase, force push) means a full scan.
  * the scan has a cap, REPO_HEALTH_SECRETS_CAP_S seconds (default 900). Past
    it the scan is killed and the check is recorded `pending` with the reason,
    so the run goes on to `render` and writes its history line.

REPO_HEALTH_GITLEAKS names the scanner command (default `gitleaks`; split like a
shell line); tests point it at `sh <blocking script>` to exercise the real timeout path.
"""
import contextlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from card import card_lock, health_dir, load_card, save_card
from common import parse_json, run

CAP_ENV, DEFAULT_CAP_S = "REPO_HEALTH_SECRETS_CAP_S", 900
BIN_ENV = "REPO_HEALTH_GITLEAKS"
# history.jsonl is repo content: only a full commit id is trusted as a marker
# (a ref such as "HEAD" would make the range empty and skip every commit).
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


def cap_seconds():
    try:
        cap = int(os.environ.get(CAP_ENV, DEFAULT_CAP_S))
    except ValueError:
        return DEFAULT_CAP_S
    return cap if cap > 0 else DEFAULT_CAP_S


def local_marker_file(repo):
    """Where this clone records its own completed scan: inside .git, so no commit
    can write it. history.jsonl is committed, so a commit can forge its marker."""
    rc, out = run(["git", "rev-parse", "--git-path", "repo-health-secrets-marker"], repo)[:2]
    return Path(repo) / out.strip() if rc == 0 and out.strip() else None


def last_marker(repo):
    """The marker of this clone's own last completed scan, else (no local record yet,
    e.g. a fresh clone) the newest `secrets_scanned` in history.jsonl, else "" (first run)."""
    lf = local_marker_file(repo)
    if lf and lf.is_file():
        m = lf.read_text().strip()
        if COMMIT_RE.fullmatch(m):
            return m
    f = health_dir(repo) / "history.jsonl"
    if not f.exists():
        return ""
    marker = ""
    for ln in f.read_text().splitlines():
        h = parse_json(ln) if ln.strip() else None
        m = h.get("secrets_scanned") if h else None
        if isinstance(m, str) and COMMIT_RE.fullmatch(m):
            marker = m
    return marker


def scan_command(repo, marker, head="HEAD"):
    """(argv, mode): a ranged scan when the marker is an ancestor of head, else full.

    head is the commit id scan() records, so a commit made mid-scan is never
    marked scanned."""
    cmd = shlex.split(os.environ.get(BIN_ENV, "gitleaks")) + ["git", ".", "--no-banner", "--redact"]
    if marker and run(["git", "merge-base", "--is-ancestor", marker, head], repo)[0] == 0:
        return cmd + ["--log-opts", f"{marker}..{head}"], "ranged"
    return cmd, "full"


def execute(cmd, repo, cap):
    """Return exit code, or None when the cap killed it (whole process group)."""
    try:
        p = subprocess.Popen(cmd, cwd=repo, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return f"cannot start {cmd[0]}: {e.strerror}"
    try:
        return p.wait(timeout=cap)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # it exited between the timeout and the kill
        p.wait()
        return None


def scan(repo, cap=None):
    """(status, detail, scanned_commit). scanned_commit is "" unless the scan completed."""
    cap = cap or cap_seconds()
    rc, out = run(["git", "rev-parse", "HEAD"], repo)[:2]
    head = out.strip()
    if rc != 0 or not head:
        return "pending", "not scanned: git rev-parse HEAD failed (not a git repository?)", ""
    cmd, mode = scan_command(repo, last_marker(repo), head)
    rc = execute(cmd, repo, cap)
    if rc is None:
        return "pending", f"timed out in the weekly run: {mode} secrets scan passed its {cap}s cap", ""
    if isinstance(rc, str):
        return "pending", rc, ""
    if rc == 0:
        return "pass", f"gitleaks {mode} scan: no leaks", head
    if rc == 1:
        return "fail", f"gitleaks {mode} scan found leaks (run it by hand; never paste a value)", ""
    return "pending", f"gitleaks {mode} scan failed with exit {rc}", ""


def record(repo, set_result):
    """Scan, record the check, and carry the marker in the card for `render`.

    set_result must be the UNLOCKED writer: this holds card_lock across both
    writes. Its status line goes to stderr, so stdout is the one JSON line."""
    status, detail, scanned = scan(repo)
    with card_lock(repo), contextlib.redirect_stdout(sys.stderr):
        set_result(repo, "sec.secrets-history", status, detail)
        if scanned:
            card = load_card(repo)
            card["secrets_scanned"] = scanned
            save_card(repo, card)
            if lf := local_marker_file(repo):
                lf.write_text(scanned + "\n")
    print(json.dumps({"status": status, "detail": detail}))
