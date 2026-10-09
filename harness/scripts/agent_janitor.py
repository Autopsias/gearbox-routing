#!/usr/bin/env python3
"""Reap abandoned Claude/Codex agent processes left behind by dead sessions.

Detection is VETO-FIRST, never a bare conjunction:

  1. Build a live-session registry (every running ``claude``/``codex`` process,
     its cwd, its session id, its scratchpad root, its transitive descendants).
  2. Veto every candidate that is inside a live process's descendant set, or
     whose argv references a scratchpad root owned by a live session.
  3. Only then apply PPID==1 AND agent-debris pattern AND age AND current user.

Candidates are classified into three classes and only ONE is ever killed:

  dead-session  argv points at a scratchpad session dir that EXISTS and whose
                whole-tree newest mtime is older than the 2h liveness window.
                Threshold 600s. The only class ``--apply`` kills.
  bare          pattern match, no session evidence at all. REPORT-ONLY: a
                detached ``codex exec`` or backgrounded build belonging to a
                LIVE session also double-forks to PPID 1 and carries no
                scratchpad path, so pattern alone cannot tell it from debris.
  unknown       references a session whose dir is fresh, missing, inaccessible
                or ambiguous. REPORT-ONLY, counted separately so a growing
                count is visible instead of being coerced into ``bare``.

PPID==1 buys almost nothing on macOS (measured: hundreds of user-owned
processes on a typical Mac sit at PPID 1 by design). Pattern precision plus the
liveness veto is the entire safety budget.

Tuning goes through edit -> commit -> ``gearbox deploy`` like every other
harness constant. There is deliberately NO config file: an untracked JSON in
~/.claude would be invisible to ``gearbox drift`` and unbacked-up while being
the single input deciding what gets SIGKILLed.

``prune`` reclaims disk over four ALLOWLISTED debris roots
ONLY -- it never sweeps a parent directory:

  1. scratchpad session dirs   /private/tmp/claude-<uid>/*/<session-id>/
  2. tmp cwd marker files      /private/tmp/claude-*-cwd
  3. plugin-cache temp clones  ~/.claude/plugins/cache/temp_git_*, temp_subdir_*
  4. codex session rollouts    ~/.codex/sessions/**/rollout-*.jsonl

Every candidate is checked against a hard PROTECT-LIST veto before unlink --
veto wins over any allowlist match, always. A whole-directory delete (roots 1
and 3) enumerates every descendant first: one protected or unsafe descendant
vetoes the ENTIRE directory. "Unsafe" also covers alias safety -- no-follow
traversal, a resolved-path-inside-root check before every mutation, and a
refusal to unlink anything with ``st_nlink > 1`` (never proven safe to share).
``build_live_session_registry`` (above) is imported UNCHANGED for root 1's
liveness cross-check -- reap and prune must never disagree about what is
live.
"""

from __future__ import annotations

import argparse
import calendar
import fcntl
import fnmatch
import hashlib
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Constants. The whole tuning surface lives here; see module docstring.
# --------------------------------------------------------------------------

UID = os.getuid()
from janitor_paths import _is_unsafe, _resolved_inside, owner_only_slugs  # noqa: E402,F401
HOME = os.path.expanduser("~")

SCRATCHPAD_BASE = f"/private/tmp/claude-{UID}"
SHELL_SNAPSHOT_DIR = f"{HOME}/.claude/shell-snapshots"

LOG_DIR = f"{HOME}/.claude/logs"
LOG_NAME = "agent-janitor.log"
LOG_MAX_BYTES = 1024 * 1024
REAP_LOCK_NAME = ".agent-janitor-reap.lock"
PRUNE_LOCK_NAME = ".agent-janitor-prune.lock"
LOG_LOCK_NAME = ".agent-janitor-log.lock"
PENDING_DIR_NAME = "pending-session-cleanup"

# prune roots and ages. CLAUDE_ROOT/CODEX_ROOT are the module defaults;
# every prune entry point also takes an explicit override so tests run
# against a sandbox tree and never the real one.
CLAUDE_ROOT = f"{HOME}/.claude"
CODEX_ROOT = f"{HOME}/.codex"
TMP_CWD_DIR = "/private/tmp"  # not /tmp: that's a symlink to this on macOS

AGE_SCRATCHPAD_S = 14 * 86400
AGE_TMP_CWD_S = 7 * 86400
AGE_PLUGIN_TEMP_S = 7 * 86400
AGE_ROLLOUT_S = 60 * 86400

PLUGIN_TEMP_PATTERNS = ("temp_git_*", "temp_subdir_*")

LIVENESS_WINDOW_S = 2 * 3600  # a session dir touched inside this window is "fresh"
AGE_DEAD_SESSION_S = 600  # confirmed-dead session: 10 minutes
AGE_BARE_S = 4 * 3600  # no session evidence: 4 hours before we even report it
BIRTH_MATCH_S = 120  # a session's scratchpad dir is born within seconds of it
MAX_KILLS = 20  # more than this in one run means a crashed supervisor
# Mirrors StartInterval in scripts/launchd/com.gearbox.agent-janitor.reap.plist.
# `status` warns when the newest log line is older than STALE_LOG_FACTOR x this:
# a wedged janitor and a spotless machine otherwise print exactly the same thing.
REAP_INTERVAL_S = 1800
STALE_LOG_FACTOR = 2
MAX_DELETE_BYTES = 20 * 1024 ** 3  # 20 GB: prune's blast-radius cap
MAX_DELETE_PATHS = 500  # prune's blast-radius cap on path COUNT, independent of size
TERM_GRACE_S = 3.0
KILL_PASSES = 3

# The SAME lock `scripts/gearbox deploy` takes (mkdir-based, scripts/gearbox:38).
# Peeked non-blocking before any --apply mutation: the blast-radius
# caps above bound DAMAGE from a bad run but do nothing to stop a launchd tick
# executing a half-deployed module mid-`gearbox deploy`. One attempt, no retry —
# the next scheduled tick (reap: 30 min, prune: 6 h) picks the work back up, and
# a deploy never takes anywhere near that long.
GEARBOX_LOCK_NAME = ".gearbox.lock"

# Hard excludes by RESOLVED EXECUTABLE PATH, never by a substring of the whole
# command line. Measured: several processes at
# ~/.vscode/extensions/openai.chatgpt-<ver>-darwin-arm64/bin/macos-aarch64/codex
# have argv[0] basename literally "codex" and are NOT under /Applications.
EXCLUDE_EXE_PREFIXES = (
    "/Applications/",
    "/System/",
    "/Library/",
    f"{HOME}/Library/",
    f"{HOME}/Applications/",
    f"{HOME}/.vscode/extensions/",
    f"{HOME}/.cursor/extensions/",
    f"{HOME}/.vscode-insiders/extensions/",
)
EXCLUDE_EXE_SUBSTRINGS = ("/Code Helper", "/Codex Framework.framework/")

# argv[0] basenames that identify a live agent session. Never candidates: they
# are the registry.
SESSION_BASENAMES = ("claude", "codex")

_SCRATCHPAD_PATH_RE = re.compile(
    re.escape(SCRATCHPAD_BASE) + r"/([^/\s'\"]+)/([0-9a-fA-F]{8}-[0-9a-fA-F-]{20,})"
)
_COMPANION_SESSION_RE = re.compile(
    r"CODEX_COMPANION_SESSION_ID=['\"]?([0-9a-fA-F]{8}-[0-9a-fA-F-]{20,})"
)

# (name, allowed argv[0] basenames, marker regex over the full command line).
# The basename set is the anchor; the marker is an absolute path or an anchored
# token, never a bare word like "codex".
PATTERNS = (
    ("shell-snapshot", frozenset({"sh", "bash", "zsh"}),
     re.compile(re.escape(SHELL_SNAPSHOT_DIR) + r"/")),
    ("scratchpad-ref",
     frozenset({"sh", "bash", "zsh", "tail", "node", "python", "python3",
                "rsync", "git", "uv"}),
     _SCRATCHPAD_PATH_RE),
    ("codex-exec", frozenset({"codex"}), re.compile(r"(?:^|\s)exec(?:\s|$)")),
)


# --------------------------------------------------------------------------
# prune protect-list -- the hard veto, checked before every unlink.
#
# Patterns are ROOT-RELATIVE (never ~/-absolute: a --root override in a test
# sandbox could never match an absolute pattern) and matched with fnmatch,
# whose '*' already crosses '/' -- 'a/**' and 'a/*' match identically, so a
# real recursive-glob engine buys nothing here.
#
# [HARDENED:claude-HIGH] verified at .gitignore:62-65: '/projects/'
# is negated but 'projects/*/*' re-ignores everything except
# 'projects/*/memory/**' -- so transcripts are NOT git-recoverable. v1 used
# the narrow memory/-only pattern, which protected exactly the wrong half.
# 'projects/**' below is the whole tree, superseding that narrower rule.
# --------------------------------------------------------------------------

CLAUDE_PROTECT = (
    ("projects/**", "protect-projects"),
    ("_plans/**", "protect-plans"),
    ("settings*.json", "protect-settings"),
    (".credentials*", "protect-credentials"),
    ("skills/**", "protect-skills"),
    ("commands/**", "protect-commands"),
    ("agents/**", "protect-agents"),
    ("hooks/**", "protect-hooks"),
    ("scripts/**", "protect-scripts"),
    ("rules/**", "protect-rules"),
    ("CLAUDE.md", "protect-claude-md"),
    ("history.jsonl", "protect-history"),
    ("plugins/**", "protect-plugins"),
    ("shell-snapshots/**", "protect-shell-snapshots"),
    ("todos/**", "protect-todos"),
    ("logs/**", "protect-logs"),
)

# prune-02's debris root is carved OUT of protect-plugins, the same shape as
# the codex-root carve-out below for prune-04's rollout files -- a narrow,
# named exception to a broad veto, not a second allowlist.
CLAUDE_CARVEOUT = ("plugins/cache/temp_git_*", "plugins/cache/temp_subdir_*")

# Everything under the codex root is protected by default (auth.json,
# history.jsonl, state_*.sqlite, session_index.jsonl, and anything else --
# "everything under ~/.codex stays veto-protected" per PRUNE-04) except this
# one carve-out.
CODEX_DEFAULT_PROTECT_RULE = "protect-codex-default"
CODEX_CARVEOUT = "sessions/**/rollout-*.jsonl"


def is_protected(root_kind: str, rel_path: str):
    """(protected, rule_id) for a path relative to the claude or codex root.

    Veto wins over any allowlist match: a debris-root walker calls this on
    every candidate (and, for a whole-directory delete, every descendant)
    before ever proposing a deletion. ``root_kind`` is ``"claude"`` or
    ``"codex"`` -- the caller supplies whichever root the path was resolved
    against, never a guess.
    """
    rel_path = rel_path.replace(os.sep, "/")
    if root_kind == "claude":
        if any(fnmatch.fnmatch(rel_path, pat) for pat in CLAUDE_CARVEOUT):
            return False, None
        for pat, rule_id in CLAUDE_PROTECT:
            if fnmatch.fnmatch(rel_path, pat):
                return True, rule_id
        return False, None
    if root_kind == "codex":
        if fnmatch.fnmatch(rel_path, CODEX_CARVEOUT):
            return False, None
        return True, CODEX_DEFAULT_PROTECT_RULE
    raise ValueError(f"unknown root_kind {root_kind!r}")


# --------------------------------------------------------------------------
# ps parsing (macOS has no /proc)
# --------------------------------------------------------------------------

class Proc:
    """One row of ``ps -axww -o pid,ppid,uid,etime,lstart,command``."""

    __slots__ = ("pid", "ppid", "uid", "age", "lstart", "command", "exe", "base")

    def __init__(self, pid, ppid, uid, age, lstart, command):
        self.pid = pid
        self.ppid = ppid
        self.uid = uid
        self.age = age
        self.lstart = lstart
        self.command = command
        self.exe = shell_argv0(command)
        self.base = os.path.basename(self.exe)

    @property
    def identity(self):
        """(lstart, command) — what a pre-signal recheck must still match."""
        return (self.lstart, self.command)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<Proc {self.pid} ppid={self.ppid} {self.command[:60]!r}>"


def shell_argv0(command: str) -> str:
    """argv[0] of a ps command string.

    ps joins argv with spaces, so an executable path containing spaces (every
    ``/Applications/**/Code Helper (Renderer)`` and ``Codex (Renderer)``) cannot
    be recovered by splitting. Take the longest leading prefix that names an
    existing file, else the first whitespace-delimited token.
    """
    parts = command.split(" ")
    first = parts[0]
    if os.path.sep not in first or os.path.isfile(first):
        return first
    # ponytail: bounded at 8 tokens — no real executable path has more spaces,
    # and the /Applications prefix exclude already covers the GUI helpers.
    for n in range(2, min(len(parts), 8) + 1):
        cand = " ".join(parts[:n])
        if os.path.isfile(cand):
            return cand
    return first


def parse_etime(raw: str) -> int:
    """``MM:SS`` / ``HH:MM:SS`` / ``DD-HH:MM:SS`` -> seconds."""
    raw = raw.strip()
    days = 0
    if "-" in raw:
        d, raw = raw.split("-", 1)
        days = int(d)
    bits = [int(x) for x in raw.split(":")]
    while len(bits) < 3:
        bits.insert(0, 0)
    h, m, s = bits[-3:]
    return days * 86400 + h * 3600 + m * 60 + s


def ps_snapshot() -> list:
    """Every process on the machine, as Proc rows.

    ``-ww`` is mandatory: without it macOS ps truncates the command column and
    the scratchpad path we key detection on falls off the end.
    """
    out = subprocess.run(
        ["ps", "-axww", "-o", "pid,ppid,uid,etime,lstart,command"],
        capture_output=True, text=True, check=True,
    ).stdout
    procs = []
    for line in out.splitlines()[1:]:
        # pid ppid uid etime + 5 lstart tokens (Dow Mon D HH:MM:SS YYYY) + command
        f = line.split(None, 9)
        if len(f) < 10:
            continue
        try:
            procs.append(Proc(
                pid=int(f[0]), ppid=int(f[1]), uid=int(f[2]),
                age=parse_etime(f[3]), lstart=" ".join(f[4:9]), command=f[9],
            ))
        except ValueError:
            continue
    return procs


def verify_identity(pid: int, identity) -> bool:
    """True if pid still has the start time AND command it had at discovery.

    macOS recycles PIDs and the TERM->KILL grace is long enough for a fresh
    process to inherit a just-freed one.
    """
    r = subprocess.run(["ps", "-ww", "-o", "lstart=,command=", "-p", str(pid)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return False
    f = r.stdout.rstrip("\n").split(None, 5)
    if len(f) < 6:
        return False
    return (" ".join(f[:5]), f[5]) == tuple(identity)


def children_map(procs) -> dict:
    kids = {}
    for p in procs:
        kids.setdefault(p.ppid, []).append(p.pid)
    return kids


def descendants(pid: int, kids: dict) -> list:
    """Transitive descendants, breadth-first, parents before children."""
    out, queue, seen = [], list(kids.get(pid, ())), {pid}
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        out.append(cur)
        queue.extend(kids.get(cur, ()))
    return out


# --------------------------------------------------------------------------
# Live-session registry — CROSS-SESSION CONTRACT (prune imports this)
# --------------------------------------------------------------------------

def _is_excluded(proc) -> bool:
    """True for app bundles and editor extensions, by executable PATH.

    Checked against the raw command line as well as the resolved argv[0]:
    ``shell_argv0`` can only rejoin a space-bearing path that still exists on
    disk, and an exclude that silently stops working when a helper is upgraded
    out from under a running process is worse than no exclude.
    """
    for text in (proc.exe, proc.command):
        if text.startswith(EXCLUDE_EXE_PREFIXES):
            return True
        if any(s in text for s in EXCLUDE_EXE_SUBSTRINGS):
            return True
    return False


def _cwds(pids) -> dict:
    """pid -> cwd via one batched lsof call (macOS has no /proc)."""
    if not pids:
        return {}
    r = subprocess.run(
        ["/usr/sbin/lsof", "-a", "-d", "cwd", "-Fn", "-p",
         ",".join(str(p) for p in pids)],
        capture_output=True, text=True,
    )
    cwds, cur = {}, None
    for line in r.stdout.splitlines():
        if line.startswith("p"):
            cur = int(line[1:])
        elif line.startswith("n") and cur is not None:
            cwds.setdefault(cur, line[1:])
    return cwds


def cwd_slug(cwd: str) -> str:
    """/Users/x/repo -> -Users-x-repo (how the scratchpad root is named)."""
    return cwd.replace("/", "-")


def session_dir_by_birth(slug: str, start: float):
    """(session_id, dir) for the scratchpad dir born when this session started.

    A live claude that has no bash child at snapshot time carries no scratchpad
    path anywhere in its subtree argv, so argv alone resolves the session id only
    intermittently (measured: a minority of live sessions). Directory
    creation time is durable and exact: measured, the owning dir was born within
    seconds of the process, and the next-nearest dir under the same slug many
    minutes away. An idle session must stay identifiable, otherwise its
    own stale scratchpad reads as a dead session.
    """
    base = f"{SCRATCHPAD_BASE}/{slug}"
    best = None
    try:
        names = os.listdir(base)
    except OSError:
        return None
    for name in names:
        try:
            born = os.stat(os.path.join(base, name)).st_birthtime
        except (OSError, AttributeError):
            continue
        delta = abs(born - start)
        if delta <= BIRTH_MATCH_S and (best is None or delta < best[0]):
            best = (delta, name.lower(), os.path.join(base, name))
    return None if best is None else (best[1], best[2])


def build_live_session_registry(procs=None, now=None) -> list:
    """Every running claude/codex process and what it owns.

    Returns a list of dicts::

        {pid, cwd, session_id, scratchpad_root, session_roots, descendants,
         kind, incomplete, command}

    ``descendants`` is the full transitive PID set. ``session_roots`` is every
    scratchpad session directory referenced anywhere in that process's subtree
    argv — the union of these across the registry is the veto set. ``kind`` is
    ``session`` for a real CLI session or ``app`` for a GUI/editor helper
    (ChatGPT.app, the VS Code codex extension), which owns no session metadata
    but whose descendants are still vetoed.

    ``incomplete`` marks a real session whose cwd could not be read: the veto
    would be weaker than the one the tests exercise, so ``--apply`` aborts
    non-zero rather than degrading to pattern-only detection.

    prune imports and calls this UNCHANGED. Do not inline it into the
    reap path — if prune rebuilds its own view, reap and prune will disagree
    about which sessions are live.
    """
    procs = ps_snapshot() if procs is None else procs
    now = time.time() if now is None else now
    by_pid = {p.pid: p for p in procs}
    kids = children_map(procs)

    roots = [p for p in procs
             if p.uid == UID and p.base in SESSION_BASENAMES]
    real = [p for p in roots if not _is_excluded(p)]
    cwds = _cwds([p.pid for p in real])

    registry = []
    for p in roots:
        app = _is_excluded(p)
        desc = descendants(p.pid, kids)
        blob = "\n".join([p.command] + [by_pid[d].command for d in desc if d in by_pid])

        found = {}  # uuid -> scratchpad session dir
        for slug_, uuid in _SCRATCHPAD_PATH_RE.findall(blob):
            found[uuid.lower()] = f"{SCRATCHPAD_BASE}/{slug_}/{uuid}"
        cwd = cwds.get(p.pid)
        slug = cwd_slug(cwd) if cwd else None
        for uuid in _COMPANION_SESSION_RE.findall(blob):
            if slug:
                found.setdefault(uuid.lower(), f"{SCRATCHPAD_BASE}/{slug}/{uuid}")

        argv_ids = set(found)
        born = session_dir_by_birth(slug, now - p.age) if slug else None
        if born:
            found[born[0]] = born[1]

        session_id, scratchpad_root = None, None
        # Prefer a session id this process actually named in its own subtree.
        for uuid in sorted(argv_ids):
            if slug and found[uuid].startswith(f"{SCRATCHPAD_BASE}/{slug}/"):
                session_id, scratchpad_root = uuid, found[uuid]
                break
        if session_id is None and born:
            session_id, scratchpad_root = born
        elif session_id is None and found:
            session_id, scratchpad_root = sorted(found.items())[0]

        registry.append({
            "pid": p.pid,
            "cwd": cwd,
            "session_id": session_id,
            "scratchpad_root": scratchpad_root,
            "session_roots": set(found.values()),
            "descendants": set(desc),
            "kind": "app" if app else "session",
            "incomplete": (not app) and cwd is None,
            "command": p.command,
        })
    return registry


def veto_sets(registry):
    """(pids never touched, scratchpad roots owned by a live session)."""
    pids, roots = set(), set()
    for e in registry:
        pids.add(e["pid"])
        pids |= e["descendants"]
        roots |= e["session_roots"]
    return pids, roots


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

def match_pattern(proc: Proc):
    for name, bases, marker in PATTERNS:
        if proc.base in bases and marker.search(proc.command):
            return name
    return None


def session_ref(command: str):
    """The scratchpad session dir this command claims, or None."""
    m = _SCRATCHPAD_PATH_RE.search(command)
    if not m:
        return None
    return f"{SCRATCHPAD_BASE}/{m.group(1)}/{m.group(2)}"


def newest_mtime(path: str):
    """Newest mtime in the whole tree, or None if unreadable.

    The session directory's OWN mtime is useless: measured, a live
    session's dir mtime was a day stale while a file two levels down had been
    written seconds earlier.
    """
    try:
        newest = os.stat(path).st_mtime
    except OSError:
        return None
    for root, dirs, files in os.walk(path, onerror=lambda e: None):
        for name in dirs + files:
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
    return newest


class Candidate:
    __slots__ = ("proc", "pattern", "cls", "session_root", "reason")

    def __init__(self, proc, pattern, cls, session_root, reason):
        self.proc = proc
        self.pattern = pattern
        self.cls = cls
        self.session_root = session_root
        self.reason = reason


def classify(proc: Proc, pattern: str, now: float):
    """-> Candidate, or None if too young / not yet worth reporting."""
    root = session_ref(proc.command)
    if root is None:
        cls, reason, threshold = "bare", "no-session-evidence", AGE_BARE_S
    else:
        newest = newest_mtime(root)
        if newest is None:
            cls, reason, threshold = "unknown", "session-dir-missing", AGE_BARE_S
        elif now - newest < LIVENESS_WINDOW_S:
            cls, reason, threshold = "unknown", "session-dir-fresh", AGE_BARE_S
        else:
            cls, reason, threshold = "dead-session", "session-dir-dead", AGE_DEAD_SESSION_S
    if proc.age < threshold:
        return None
    return Candidate(proc, pattern, cls, root, reason)


def detect(procs=None, registry=None, now=None):
    """(candidates, registry, vetoed_count) — the veto runs FIRST."""
    procs = ps_snapshot() if procs is None else procs
    now = time.time() if now is None else now
    if registry is None:
        registry = build_live_session_registry(procs, now)
    veto_pids, live_roots = veto_sets(registry)

    candidates, vetoed = [], 0
    for p in procs:
        if p.uid != UID or p.ppid != 1:
            continue
        if _is_excluded(p):
            continue
        pattern = match_pattern(p)
        if pattern is None:
            continue
        if p.pid in veto_pids:
            vetoed += 1
            continue
        root = session_ref(p.command)
        if root and root in live_roots:
            vetoed += 1
            continue
        cand = classify(p, pattern, now)
        if cand is not None:
            candidates.append(cand)
    return candidates, registry, vetoed


# --------------------------------------------------------------------------
# Logging (rotation takes its own short lock: both subcommands share the file)
# --------------------------------------------------------------------------

def module_hash() -> str:
    with open(os.path.abspath(__file__), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:12]


def _log_path():
    return os.path.join(LOG_DIR, LOG_NAME)


def log(level: str, action: str, **fields):
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [ts, level, action]
    for k, v in fields.items():
        if v is None:
            continue
        if k == "cmd":
            v = shlex.quote(str(v)[:120])
        parts.append(f"{k}={v}")
    line = " ".join(str(p) for p in parts) + "\n"
    path = _log_path()
    # ponytail: rotate+append under one short dedicated lock; the per-subcommand
    # reap/prune locks cannot cover a file both subcommands write.
    with open(os.path.join(LOG_DIR, LOG_LOCK_NAME), "a+") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            if os.path.exists(path) and os.path.getsize(path) + len(line) > LOG_MAX_BYTES:
                os.replace(path, path + ".old")
            with open(path, "a") as fh:
                fh.write(line)
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)
    return line


def _tail_log(n: int) -> list:
    """Last `n` lines of the janitor log, or [] if it does not exist yet."""
    try:
        with open(_log_path(), "r", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-n:]]


LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z")


def newest_log_time(now=None):
    """UTC epoch seconds of the newest parseable janitor log line, or None.

    Reads the newest line, not the file mtime: rotation and an interrupted
    write both move mtime without a run having happened.
    """
    for line in reversed(_tail_log(200)):
        m = LOG_TS_RE.match(line)
        if m:
            return calendar.timegm(
                time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))
    return None


def log_freshness(now=None):
    """(verdict, human detail) for `status`.

    WHY THIS EXISTS: without it, a janitor that has silently stopped running
    and a machine with genuinely nothing to clean produce an IDENTICAL status
    page — 0 candidates, 0 prunable bytes, state OK. The absence of work and
    the absence of the worker must not look the same.

    STALE is deliberately not an error exit: `status` is read-only and is also
    the tool you run to DIAGNOSE a stale janitor. It reports; it does not fail.
    """
    now = time.time() if now is None else now
    newest = newest_log_time()
    if newest is None:
        return "NEVER RUN", ("no janitor log yet — nothing has run, so a clean "
                             "report here means untested, not clean")
    age = int(now - newest)
    limit = REAP_INTERVAL_S * STALE_LOG_FACTOR
    mins = age // 60
    if age > limit:
        return "STALE", (
            f"newest log line is {mins} min old, over the {limit // 60} min "
            f"limit ({STALE_LOG_FACTOR}x the {REAP_INTERVAL_S // 60} min reap "
            "interval) — the janitor may have stopped running; check "
            "`launchctl print gui/$UID/com.gearbox.agent-janitor.reap`")
    return "FRESH", (f"newest log line is {mins} min old, within the "
                     f"{limit // 60} min limit")


def notify(message: str):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json_str(message)} with title "agent-janitor"'],
            capture_output=True, timeout=10,
        )
    except Exception:  # notification failure must never block the reaper
        pass


def json_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Lock:
    """Non-blocking exclusive lock. A second instance exits, never queues."""

    def __init__(self, name):
        self.path = os.path.join(LOG_DIR, name)
        self.fh = None

    def __enter__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return None
        return self

    def __exit__(self, *exc):
        if self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()


class GearboxLock:
    """Non-blocking peek at ``<claude_root>/.gearbox.lock`` -- the SAME
    mkdir-based lock ``scripts/gearbox`` (line ~38) takes for the duration of a
    deploy. One ``os.mkdir`` attempt, no retry, no reclaim-if-stale logic (that
    is gearbox's own job): if it is held, ``.acquired`` is False and the caller
    must skip its mutation for this tick rather than wait.
    """

    def __init__(self, claude_root=None):
        self.dir = os.path.join(claude_root or CLAUDE_ROOT, GEARBOX_LOCK_NAME)
        self.acquired = False

    def __enter__(self):
        try:
            os.mkdir(self.dir)
        except OSError:
            self.acquired = False
            return self
        try:
            with open(os.path.join(self.dir, "pid"), "w") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass
        self.acquired = True
        return self

    def __exit__(self, *exc):
        if self.acquired:
            shutil.rmtree(self.dir, ignore_errors=True)


def _resolve_session_dir(session_id, scratchpad_base=None):
    """Resolve a hook-supplied session id to EXACTLY ONE scratchpad session
    directory. -> (status, path), one of three outcomes:

      ("one", path)      exactly one match; reap scoped to that directory.
      ("none", None)     the id is well formed and the scratchpad root is
                         readable, but this session never created a directory
                         of its own. There is provably nothing of its own to
                         reap, so the caller NO-OPs cleanly at exit 0.
      ("unresolved", None)  genuinely ambiguous: a malformed id, an unreadable
                         scratchpad root, or MORE THAN ONE matching directory.
                         The caller refuses non-zero.

    [HARDENED:codex-HIGH] The scratchpad root is SHARED across every session
    on the machine, so a glob hit alone is not ownership -- this only narrows
    the search to a single directory path; callers must still require a
    candidate's OWN ``session_root`` to equal this path exactly (never a
    prefix) before treating it as in scope. No outcome ever falls back to
    machine-wide reaping: "none" reaps nothing at all.
    """
    if not session_id or not re.fullmatch(r"[A-Za-z0-9._-]+", session_id):
        return "unresolved", None
    base = scratchpad_base or SCRATCHPAD_BASE
    matches = []
    try:
        slugs = os.listdir(base)
    except OSError:
        return "unresolved", None
    for slug in slugs:
        cand = os.path.join(base, slug, session_id)
        if os.path.isdir(cand) and not os.path.islink(cand):
            matches.append(cand)
    if len(matches) == 1:
        return "one", matches[0]
    return ("none", None) if not matches else ("unresolved", None)


# --------------------------------------------------------------------------
# Kill path
# --------------------------------------------------------------------------

def _tree_pids(root_pid, procs):
    return [root_pid] + descendants(root_pid, children_map(procs))


def kill_tree(root: Candidate, hashval: str) -> tuple:
    """SIGTERM -> grace -> SIGKILL over a REDISCOVERED tree. -> (killed, survivors)

    One snapshot cannot see children forked after it, so the tree is rediscovered
    on every pass and each PID is re-verified immediately before EVERY signal —
    not only before SIGKILL. Bounded at KILL_PASSES; anything still alive after
    that is reported, never escalated blindly.
    """
    known = {root.proc.pid: root.proc.identity}
    killed = set()
    for attempt in range(KILL_PASSES):
        procs = ps_snapshot()
        by_pid = {p.pid: p for p in procs}
        pids = [p for p in _tree_pids(root.proc.pid, procs) if p in by_pid]
        if not pids:
            return killed, []
        sig = signal.SIGTERM if attempt == 0 else signal.SIGKILL
        for pid in reversed(pids):  # children before their parent
            ident = known.setdefault(pid, by_pid[pid].identity)
            if not verify_identity(pid, ident):
                log("WARN", "pid-recycled", pid=pid, root=root.proc.pid, hash=hashval)
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                continue
            except PermissionError:
                log("WARN", "kill-denied", pid=pid, root=root.proc.pid, hash=hashval)
                continue
            killed.add(pid)
            log("INFO", "killed", pid=pid, root=root.proc.pid,
                sig=sig.name, age=by_pid[pid].age, cls=root.cls,
                pattern=root.pattern, hash=hashval, cmd=by_pid[pid].command)
        time.sleep(TERM_GRACE_S if attempt == 0 else 0.5)

    procs = ps_snapshot()
    alive = {p.pid for p in procs}
    return killed, [p for p in _tree_pids(root.proc.pid, procs) if p in alive]


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def _pending_dir():
    return os.path.join(LOG_DIR, PENDING_DIR_NAME)


def _defer_session(session_id, hashval):
    """A SessionEnd run that loses the lock must not vanish."""
    os.makedirs(_pending_dir(), exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)[:80]
    with open(os.path.join(_pending_dir(), safe), "w") as fh:
        fh.write(datetime.now(timezone.utc).isoformat() + "\n")
    log("WARN", "deferred-session", session=safe, hash=hashval)


def _consume_pending(hashval):
    try:
        names = os.listdir(_pending_dir())
    except OSError:
        return 0
    for name in names:
        try:
            os.unlink(os.path.join(_pending_dir(), name))
        except OSError:
            continue
        log("INFO", "consumed-pending", session=name, hash=hashval)
    return len(names)


def _counts(candidates):
    c = {"dead-session": 0, "bare": 0, "unknown": 0}
    for cand in candidates:
        c[cand.cls] += 1
    return c


def _report(candidates, registry, vetoed, counts, killed, out):
    live = [e for e in registry if e["kind"] == "session"]
    print(f"live sessions: {len(live)} (+{len(registry) - len(live)} app helpers), "
          f"vetoed candidates: {vetoed}", file=out)
    for e in live:
        print(f"  live pid={e['pid']} session={e['session_id']} cwd={e['cwd']} "
              f"descendants={len(e['descendants'])}"
              + ("  INCOMPLETE" if e["incomplete"] else ""), file=out)
    for cls in ("dead-session", "bare", "unknown"):
        rows = [c for c in candidates if c.cls == cls]
        note = "KILLABLE" if cls == "dead-session" else "report-only"
        print(f"\n{cls} ({len(rows)}, {note}):", file=out)
        for c in rows:
            print(f"  pid={c.proc.pid} age={c.proc.age}s pattern={c.pattern} "
                  f"why={c.reason}\n    {c.proc.command[:160]}", file=out)
    print(f"\ncounts: {counts} killed={killed}", file=out)


def _reap_under_lock(args, hashval, lock, out, procs, scoped_dir):
    """The reap itself, run while the lock is held: classify, veto, kill.

    Every exit is a return value the caller hands straight back, so the lock
    releases on the way out no matter which branch fired.
    """
    if lock is None:
        if args.session_id:
            _defer_session(args.session_id, hashval)
        log("INFO", "lock-busy", hash=hashval)
        return 0
    _consume_pending(hashval)
    candidates, registry, vetoed = detect(procs)
    if scoped_dir is not None:
        # bare/unknown candidates carry no session_root (or a different
        # one) and are dropped here too -- they are never in scope for a
        # session-scoped hook invocation, not just never killable.
        candidates = [c for c in candidates if c.session_root == scoped_dir]
    counts = _counts(candidates)
    killable = [c for c in candidates if c.cls == "dead-session"]
    incomplete = [e for e in registry if e["incomplete"]]
    killed = 0

    if args.apply:
        glock = GearboxLock()
        with glock:
            if not glock.acquired:
                log("INFO", "gearbox-deploy-lock-busy", hash=hashval)
                print("gearbox deploy holds .gearbox.lock; skipping --apply "
                      "this tick (next tick picks it up)", file=out)
                return 0
            if incomplete:
                log("ERROR", "registry-incomplete",
                    pids=",".join(str(e["pid"]) for e in incomplete), hash=hashval)
                print("registry incomplete (cwd unreadable for a live session); "
                      "refusing to --apply", file=sys.stderr)
                return 2
            live = ps_snapshot()
            planned = sum(len(_tree_pids(c.proc.pid, live)) for c in killable)
            if planned > MAX_KILLS:
                log("ERROR", "refused-max-kills", planned=planned,
                    max=MAX_KILLS, hash=hashval)
                notify(f"agent-janitor REFUSED: {planned} processes exceed "
                       f"MAX_KILLS={MAX_KILLS}. Check for a crashed supervisor.")
                print(f"refusing: would kill {planned} processes (MAX_KILLS="
                      f"{MAX_KILLS})", file=sys.stderr)
                return 3
            for c in killable:
                done, survivors = kill_tree(c, hashval)
                killed += len(done)
                for pid in survivors:
                    log("WARN", "survivor", pid=pid, root=c.proc.pid, hash=hashval)

    log("INFO", "heartbeat", mode="apply" if args.apply else "dry-run",
        session=args.session_id, dead_session=counts["dead-session"],
        bare=counts["bare"], unknown=counts["unknown"], killed=killed,
        vetoed=vetoed, live=len(registry), hash=hashval)
    deferred = counts["bare"] + counts["unknown"] > 0 and killed == 0
    if deferred:
        log("WARN", "deferred", bare=counts["bare"],
            unknown=counts["unknown"], hash=hashval)
    _report(candidates, registry, vetoed, counts, killed, out)
    return 0


def cmd_reap(args, out=None, procs=None) -> int:
    out = sys.stdout if out is None else out
    hashval = module_hash()

    # [HARDENED:codex-HIGH] --session-id (the SessionEnd hook's own caller)
    # must resolve to an EXACT scratchpad directory before anything else runs.
    # A missing/malformed/ambiguous id is refused non-zero here -- never
    # silently widened to a machine-wide reap.
    scoped_dir = None
    if args.session_id is not None:
        status, scoped_dir = _resolve_session_dir(args.session_id)
        if status == "none":
            # A session whose cwd never created a scratchpad directory owns no
            # debris at all, so there is provably nothing of its own to reap:
            # a clean no-op, not an error. Distinct from "unresolved" below,
            # which is genuine ambiguity. Neither ever widens to machine-wide.
            log("INFO", "session-scope-empty", session=args.session_id,
                hash=hashval)
            return 0
        if status != "one":
            log("ERROR", "session-scope-unresolved", session=args.session_id,
                hash=hashval)
            print(f"--session-id {args.session_id!r} does not resolve to exactly "
                  "one scratchpad directory; refusing (never falling back to "
                  "machine-wide reaping)", file=sys.stderr)
            return 4

    with Lock(REAP_LOCK_NAME) as lock:
        return _reap_under_lock(args, hashval, lock, out, procs, scoped_dir)


def cmd_status(args, out=None) -> int:
    """Reports current would-kill list, prunable bytes, this module's own
    content hash (the identity stamp checked by a human), and the
    last 10 janitor log lines. Read-only: never takes --apply."""
    out = sys.stdout if out is None else out
    # FIRST, before anything below writes to the log: detect() and the prune
    # dry-run each emit a heartbeat, so reading freshness later would always
    # measure THIS run and report FRESH no matter how long the janitor had
    # been wedged. The check has to happen before the checker leaves a trace.
    freshness, freshness_detail = log_freshness()
    candidates, registry, vetoed = detect()
    counts = _counts(candidates)
    state = "DEFERRED" if counts["bare"] + counts["unknown"] > 0 else "OK"
    _report(candidates, registry, vetoed, counts, 0, out)
    print(f"state: {state}", file=out)

    prune_dry = run_prune(apply=False)
    mb = prune_dry["bytes_reclaimed"] / (1024 * 1024)
    print(f"\nprunable: {len(prune_dry['deleted'])} paths, {mb:.1f} MB "
          f"(dry-run; `agent_janitor.py prune --apply` reclaims it)", file=out)

    print(f"\nmodule hash: {module_hash()}", file=out)

    print(f"\nheartbeat: {freshness} — {freshness_detail}", file=out)

    print("\nlast 10 janitor log lines:", file=out)
    tail = _tail_log(10)
    if not tail:
        print("  (no log yet)", file=out)
    for line in tail:
        print(f"  {line}", file=out)
    return 0


# --------------------------------------------------------------------------
# prune -- alias safety
# --------------------------------------------------------------------------

def dir_size(path: str) -> int:
    """Total bytes of file CONTENT in a file or directory tree -- directory
    entries themselves are not counted, so the number matches what a human
    reading a reclaim total expects (bytes of data freed, not inode bookkeeping).
    No-follow: a symlinked subtree must never inflate the reported total."""
    try:
        st_ = os.lstat(path)
        total = st_.st_size if stat.S_ISREG(st_.st_mode) else 0
    except OSError:
        return 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _scan_dir_veto(top: str, root_for_protect=None, root_kind=None):
    """First alias-safety or protect-list rule fired by `top` or ANY
    descendant, or None if the whole tree is clear.

    Enumerates every descendant before any deletion decision is made --
    [HARDENED:codex-HIGH] a whole-directory delete must never rmtree a
    candidate whose contents were not individually checked. A symlinked
    descendant is listed (it appears in `dirs`/`files` from the parent's own
    listing) but never traversed into (`followlinks=False`), so its veto is
    recorded without ever resolving what it points at.
    """
    paths = [top]
    for dirpath, dirnames, filenames in os.walk(top, onerror=lambda e: None, followlinks=False):
        paths.extend(os.path.join(dirpath, n) for n in dirnames)
        paths.extend(os.path.join(dirpath, n) for n in filenames)
    for full in paths:
        unsafe = _is_unsafe(full)
        if unsafe:
            return unsafe
        if root_kind:
            rel = os.path.relpath(full, root_for_protect)
            protected, rule_id = is_protected(root_kind, rel)
            if protected:
                return rule_id
    return None


class PruneItem:
    """One deletion candidate that has already cleared every veto."""

    __slots__ = ("path", "is_dir", "nbytes", "root")

    def __init__(self, path, is_dir, nbytes, root):
        self.path = path
        self.is_dir = is_dir
        self.nbytes = nbytes
        self.root = root  # re-checked with _resolved_inside right before mutation


# --------------------------------------------------------------------------
# prune -- the four allowlisted debris-root scanners
#
# Each returns (deleted: [PruneItem], vetoed: [(path, rule_id)], fresh: [path]).
# Pure: no scanner mutates anything. Every scanner independently re-verifies
# `_resolved_inside` and `_is_unsafe` on its own root-relative walk -- a
# candidate is never trusted just because it matched a glob.
# --------------------------------------------------------------------------

def scan_scratchpad(now, scratchpad_base=None, registry=None):
    """Whole session dirs under /private/tmp/claude-<uid>/*/<session-id>/.

    Staleness is the newest mtime anywhere in the tree (a dir with one fresh
    file inside is LIVE). Staleness alone is not sufficient: cross-check
    every stale candidate against the live-session registry (imported
    UNCHANGED from reap) and veto any dir whose session id or path is
    claimed by a running claude/codex process -- a session paused for days on
    a human checkpoint is idle, not dead.
    """
    base = scratchpad_base or SCRATCHPAD_BASE
    registry = build_live_session_registry() if registry is None else registry
    _, live_roots = veto_sets(registry)
    live_ids = {e["session_id"] for e in registry if e["session_id"]}

    deleted, fresh = [], []
    slugs, vetoed = owner_only_slugs(base)  # see janitor_paths.owner_only_dir
    for slug in slugs:
        slug_dir = os.path.join(base, slug)
        if not os.path.isdir(slug_dir) or os.path.islink(slug_dir):
            continue
        try:
            sessions = os.listdir(slug_dir)
        except OSError:
            continue
        for sid in sessions:
            path = os.path.join(slug_dir, sid)
            if not _resolved_inside(path, base):
                vetoed.append((path, "veto-outside-root"))
                continue
            unsafe = _is_unsafe(path)
            if unsafe:
                vetoed.append((path, unsafe))
                continue
            newest = newest_mtime(path)
            if newest is None:
                continue
            if now - newest < AGE_SCRATCHPAD_S:
                fresh.append(path)
                continue
            if path in live_roots or sid in live_ids:
                vetoed.append((path, "veto-live-session"))
                continue
            rule = _scan_dir_veto(path)
            if rule:
                vetoed.append((path, rule))
                continue
            deleted.append(PruneItem(path, True, dir_size(path), base))
    return deleted, vetoed, fresh


def scan_tmp_cwd(now, tmp_dir=None):
    """/private/tmp/claude-*-cwd marker files older than 7 days."""
    base = tmp_dir or TMP_CWD_DIR
    deleted, vetoed, fresh = [], [], []
    try:
        names = fnmatch.filter(os.listdir(base), "claude-*-cwd")
    except OSError:
        return deleted, vetoed, fresh
    for name in names:
        path = os.path.join(base, name)
        if not _resolved_inside(path, base):
            vetoed.append((path, "veto-outside-root"))
            continue
        unsafe = _is_unsafe(path)
        if unsafe:
            vetoed.append((path, unsafe))
            continue
        try:
            st_ = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st_.st_mode):
            continue
        if now - st_.st_mtime < AGE_TMP_CWD_S:
            fresh.append(path)
            continue
        deleted.append(PruneItem(path, False, st_.st_size, base))
    return deleted, vetoed, fresh


def scan_plugin_cache(now, claude_root=None):
    """~/.claude/plugins/cache/temp_git_* and temp_subdir_* older than 7 days.

    Only temp_*-prefixed entries are debris; installed plugin caches are
    never even listed as candidates (the walk is scoped to this one glob,
    not a sweep of plugins/cache/).
    """
    root = claude_root or CLAUDE_ROOT
    cache_dir = os.path.join(root, "plugins", "cache")
    deleted, vetoed, fresh = [], [], []
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return deleted, vetoed, fresh
    names = [n for n in names
             if any(fnmatch.fnmatch(n, pat) for pat in PLUGIN_TEMP_PATTERNS)]
    for name in names:
        path = os.path.join(cache_dir, name)
        if not _resolved_inside(path, root):
            vetoed.append((path, "veto-outside-root"))
            continue
        unsafe = _is_unsafe(path)
        if unsafe:
            vetoed.append((path, unsafe))
            continue
        is_dir = stat.S_ISDIR(os.lstat(path).st_mode)
        newest = newest_mtime(path) if is_dir else os.lstat(path).st_mtime
        if newest is None:
            continue
        if now - newest < AGE_PLUGIN_TEMP_S:
            fresh.append(path)
            continue
        if is_dir:
            rule = _scan_dir_veto(path, root_for_protect=root, root_kind="claude")
            if rule:
                vetoed.append((path, rule))
                continue
        size = dir_size(path) if is_dir else os.lstat(path).st_size
        deleted.append(PruneItem(path, is_dir, size, root))
    return deleted, vetoed, fresh


def scan_codex(now, codex_root=None):
    """~/.codex/sessions/**/rollout-*.jsonl older than 60 days.

    The walk is scoped to sessions/ -- codex-root files that live elsewhere
    (auth.json, history.jsonl, state_*.sqlite, session_index.jsonl) are
    outside the only root ever walked, so they are never a candidate at all.
    Every OTHER file actually found under sessions/ (i.e. anything that is
    not a rollout, which in practice never happens, but IS exercised by the
    protect-list tests) is vetoed via protect-codex-default rather than
    silently ignored -- the boundary is a rule_id in code, not a comment.
    A symlinked date directory is listed by its parent but never descended
    into (`followlinks=False`) and is recorded in `vetoed`; a symlink named
    rollout-*.jsonl is caught by `_is_unsafe` before the carve-out is even
    consulted.
    """
    root = codex_root or CODEX_ROOT
    sessions_dir = os.path.join(root, "sessions")
    deleted, vetoed, fresh = [], [], []
    if not os.path.isdir(sessions_dir):
        return deleted, vetoed, fresh
    for dirpath, dirnames, filenames in os.walk(sessions_dir, onerror=lambda e: None,
                                                followlinks=False):
        kept = []
        for d in dirnames:
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                vetoed.append((full, "veto-symlink"))
                continue
            kept.append(d)
        dirnames[:] = kept

        for name in filenames:
            full = os.path.join(dirpath, name)
            unsafe = _is_unsafe(full)
            if unsafe:
                vetoed.append((full, unsafe))
                continue
            if not _resolved_inside(full, root):
                vetoed.append((full, "veto-outside-root"))
                continue
            rel = os.path.relpath(full, root)
            protected, rule_id = is_protected("codex", rel)
            if protected:
                vetoed.append((full, rule_id))
                continue
            st_ = os.lstat(full)
            if now - st_.st_mtime < AGE_ROLLOUT_S:
                fresh.append(full)
                continue
            deleted.append(PruneItem(full, False, st_.st_size, root))
    return deleted, vetoed, fresh


def _prune_empty_dirs(sessions_dir: str):
    """After rollout removal, rmdir now-empty YYYY/MM/DD dirs, bottom-up."""
    removed = []
    if not os.path.isdir(sessions_dir):
        return removed
    for dirpath, dirnames, filenames in os.walk(sessions_dir, topdown=False, followlinks=False):
        if dirpath == sessions_dir:
            continue
        try:
            if os.path.islink(dirpath) or os.listdir(dirpath):
                continue
            if not _resolved_inside(dirpath, sessions_dir):
                continue
            os.rmdir(dirpath)
            removed.append(dirpath)
        except OSError:
            continue
    return removed


def _apply_delete(item: PruneItem, hashval: str) -> bool:
    """Delete one already-vetted item, re-verifying safety immediately before
    the mutation -- the scan and the delete are not the same instant."""
    if not _resolved_inside(item.path, item.root):
        log("ERROR", "prune-refused-outside-root", path=item.path, hash=hashval)
        return False
    unsafe = _is_unsafe(item.path)
    if unsafe:
        log("ERROR", "prune-refused-unsafe", path=item.path, rule=unsafe, hash=hashval)
        return False
    try:
        if item.is_dir:
            shutil.rmtree(item.path)
        else:
            os.remove(item.path)
    except OSError as e:
        log("WARN", "prune-delete-failed", path=item.path, error=str(e)[:200], hash=hashval)
        return False
    log("INFO", "pruned", path=item.path, bytes=item.nbytes, hash=hashval)
    return True


def run_prune(apply: bool, *, now=None, claude_root=None, codex_root=None,
              scratchpad_base=None, tmp_cwd_dir=None, registry=None) -> dict:
    """The pruner. Always scans; mutates only when apply=True.

    -> {"deleted": [(path, nbytes)], "vetoed": [(path, rule_id)],
        "skipped_fresh": [path], "bytes_reclaimed": int, "lock_busy": bool}

    Takes the same non-blocking exclusive lock as reap (its own file,
    .agent-janitor-prune.lock) so a 6-hourly launchd prune can never overlap
    a manual one mid-rmtree; a second instance exits immediately rather than
    queuing, exactly like reap.
    """
    now = time.time() if now is None else now
    claude_root = claude_root or CLAUDE_ROOT
    codex_root = codex_root or CODEX_ROOT
    scratchpad_base = scratchpad_base or SCRATCHPAD_BASE
    tmp_cwd_dir = tmp_cwd_dir or TMP_CWD_DIR
    hashval = module_hash()
    empty = {"deleted": [], "vetoed": [], "skipped_fresh": [],
             "bytes_reclaimed": 0, "lock_busy": False, "refused": False}

    with Lock(PRUNE_LOCK_NAME) as lock:
        if lock is None:
            log("INFO", "prune-lock-busy", hash=hashval)
            return dict(empty, lock_busy=True)

        items, vetoed, fresh = [], [], []
        for got_items, got_vetoed, got_fresh in (
            scan_scratchpad(now, scratchpad_base, registry),
            scan_tmp_cwd(now, tmp_cwd_dir),
            scan_plugin_cache(now, claude_root),
            scan_codex(now, codex_root),
        ):
            items.extend(got_items)
            vetoed.extend(got_vetoed)
            fresh.extend(got_fresh)

        if apply:
            planned_bytes = sum(i.nbytes for i in items)
            if len(items) > MAX_DELETE_PATHS or planned_bytes > MAX_DELETE_BYTES:
                log("ERROR", "refused-max-delete", planned_paths=len(items),
                    planned_bytes=planned_bytes, max_paths=MAX_DELETE_PATHS,
                    max_bytes=MAX_DELETE_BYTES, hash=hashval)
                notify(f"agent-janitor prune REFUSED: {len(items)} paths / "
                       f"{planned_bytes} bytes exceed the blast-radius cap.")
                return dict(empty, vetoed=vetoed, skipped_fresh=fresh, refused=True)

            glock = GearboxLock(claude_root)
            with glock:
                if not glock.acquired:
                    log("INFO", "prune-gearbox-deploy-lock-busy", hash=hashval)
                    return dict(empty, vetoed=vetoed, skipped_fresh=fresh, lock_busy=True)

                deleted, bytes_reclaimed = [], 0
                for item in items:
                    if not _apply_delete(item, hashval):
                        continue
                    deleted.append((item.path, item.nbytes))
                    bytes_reclaimed += item.nbytes
                removed_dirs = _prune_empty_dirs(os.path.join(codex_root, "sessions"))
        else:
            deleted, bytes_reclaimed, removed_dirs = [], 0, []
            for item in items:
                deleted.append((item.path, item.nbytes))
                bytes_reclaimed += item.nbytes

        log("INFO", "prune-heartbeat", mode="apply" if apply else "dry-run",
            deleted=len(deleted), vetoed=len(vetoed), skipped_fresh=len(fresh),
            bytes_reclaimed=bytes_reclaimed, empty_dirs=len(removed_dirs), hash=hashval)

        return {"deleted": deleted, "vetoed": vetoed, "skipped_fresh": fresh,
                "bytes_reclaimed": bytes_reclaimed, "lock_busy": False, "refused": False}


def _report_prune(result, out):
    mb = result["bytes_reclaimed"] / (1024 * 1024)
    print(f"deleted: {len(result['deleted'])} ({mb:.1f} MB), "
          f"vetoed: {len(result['vetoed'])}, skipped_fresh: {len(result['skipped_fresh'])}",
          file=out)
    for path, rule_id in result["vetoed"][:20]:
        print(f"  vetoed  {rule_id:<24s} {path}", file=out)
    for path, nbytes in result["deleted"][:20]:
        print(f"  deleted {nbytes:>10d}B {path}", file=out)


PRUNE_STAMP_NAME = ".agent-janitor-prune-stamp"
PRUNE_STAMP_INTERVAL_S = 24 * 3600  # the plist wakes every 6h (sleep-safe
# catch-up), this stamp caps actual deletion work at once per real day.


def _prune_due(now=None) -> bool:
    now = time.time() if now is None else now
    try:
        mtime = os.stat(os.path.join(LOG_DIR, PRUNE_STAMP_NAME)).st_mtime
    except OSError:
        return True
    return now - mtime >= PRUNE_STAMP_INTERVAL_S


def _touch_prune_stamp():
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, PRUNE_STAMP_NAME)
    with open(path, "a"):
        pass
    os.utime(path, None)


def cmd_prune(args, out=None) -> int:
    out = sys.stdout if out is None else out
    if args.apply and not getattr(args, "force", False) and not _prune_due():
        log("INFO", "prune-stamp-not-due", hash=module_hash())
        print("prune already ran within the last 24h; skipping this tick "
              "(pass --force to bypass)", file=out)
        return 0
    result = run_prune(apply=args.apply,
                       claude_root=getattr(args, "claude_root", None),
                       codex_root=getattr(args, "codex_root", None))
    if result["lock_busy"]:
        print("another prune (or a gearbox deploy) is running; exiting", file=out)
        return 0
    if result.get("refused"):
        print("refusing: this run exceeds the blast-radius cap "
              f"(MAX_DELETE_PATHS={MAX_DELETE_PATHS}, MAX_DELETE_BYTES={MAX_DELETE_BYTES})",
              file=sys.stderr)
        _report_prune(result, out)
        return 3
    if args.apply:
        _touch_prune_stamp()
    _report_prune(result, out)
    return 0


# --------------------------------------------------------------------------
# launchd install/uninstall
#
# The janitor PREPARES and installs its own scheduled jobs when the OWNER
# runs `install`/`uninstall` -- no plan session ever invokes either. Both are
# idempotent: `bootout` first (swallowing the harmless "no such service" on a
# first run -- measured: exit 3 "No such process"), THEN
# `bootstrap`, which is documented as not idempotent on an already-loaded
# label. `launchctl load` is legacy and deliberately never used.
# --------------------------------------------------------------------------

LAUNCHD_DIR = os.path.join(HOME, "Library", "LaunchAgents")
LAUNCHD_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launchd")
LAUNCHD_LABELS = {
    "reap": "com.gearbox.agent-janitor.reap",
    "prune": "com.gearbox.agent-janitor.prune",
}


def _render_plist(name: str) -> str:
    src = os.path.join(LAUNCHD_SRC_DIR, f"{LAUNCHD_LABELS[name]}.plist")
    with open(src) as fh:
        return fh.read().replace("__HOME__", HOME)


def _install_one(name: str, out) -> bool:
    label = LAUNCHD_LABELS[name]
    dest = os.path.join(LAUNCHD_DIR, f"{label}.plist")
    target = f"gui/{UID}/{label}"
    os.makedirs(LAUNCHD_DIR, exist_ok=True)
    with open(dest, "w") as fh:
        fh.write(_render_plist(name))
    subprocess.run(["launchctl", "bootout", target], capture_output=True)  # first-run "no such service" swallowed
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", dest],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"bootstrap FAILED for {label}: {r.stderr.strip()}", file=sys.stderr)
        return False
    v = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
    print(f"installed {label} -> {dest}\n{v.stdout.strip()}", file=out)
    return True


def _uninstall_one(name: str, out):
    label = LAUNCHD_LABELS[name]
    target = f"gui/{UID}/{label}"
    subprocess.run(["launchctl", "bootout", target], capture_output=True)
    dest = os.path.join(LAUNCHD_DIR, f"{label}.plist")
    try:
        os.remove(dest)
    except OSError:
        pass
    print(f"uninstalled {label}", file=out)


def cmd_install(args, out=None) -> int:
    """OWNER-run only -- see module docstring. Prints the paired uninstall
    command so a start is never handed over without its matching stop."""
    out = sys.stdout if out is None else out
    ok = all([_install_one("reap", out), _install_one("prune", out)])
    print(f"\nto reverse: python3 {os.path.abspath(__file__)} uninstall", file=out)
    return 0 if ok else 1


def cmd_uninstall(args, out=None) -> int:
    out = sys.stdout if out is None else out
    _uninstall_one("reap", out)
    _uninstall_one("prune", out)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    reap = sub.add_parser("reap", help="find and (with --apply) kill orphaned agent processes")
    g = reap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="the default; list candidates and touch nothing")
    g.add_argument("--apply", action="store_true",
                   help="kill dead-session candidates (bare/unknown are never killed)")
    reap.add_argument("--session-id", default=None,
                      help="SessionEnd caller: defer to the next pass if the lock is busy")
    reap.set_defaults(func=cmd_reap)

    st = sub.add_parser("status", help="report per-class counts without touching anything")
    st.set_defaults(func=cmd_status)

    prune = sub.add_parser("prune", help="reclaim disk from the 4 allowlisted debris roots")
    g2 = prune.add_mutually_exclusive_group()
    g2.add_argument("--dry-run", action="store_true",
                    help="the default; list candidates and touch nothing")
    g2.add_argument("--apply", action="store_true",
                    help="delete unprotected stale debris (vetoed paths are never touched)")
    prune.add_argument("--claude-root", default=None, help="test-only override of ~/.claude")
    prune.add_argument("--codex-root", default=None, help="test-only override of ~/.codex")
    prune.add_argument("--force", action="store_true",
                       help="bypass the internal 24h stamp gate (manual runs only; "
                            "launchd never passes this)")
    prune.set_defaults(func=cmd_prune)

    install = sub.add_parser("install", help="OWNER-run only: install both launchd jobs "
                             "(never invoked by an agent session)")
    install.set_defaults(func=cmd_install)

    uninstall = sub.add_parser("uninstall", help="remove both launchd jobs")
    uninstall.set_defaults(func=cmd_uninstall)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
