#!/usr/bin/env python3
"""Where the compaction policy's state lives, and how it is written.

Split out of compact-policy.py so the RULES and the STATE LAYER are separate
files, and so the operator subcommands (compact_activation.py) can share the
ledger writer without importing the rules. Nothing here decides anything.

EVERYTHING IS UNDER ~/.gearbox-state/compaction/ — outside both versioned trees, on
purpose. Editing the deployed ~/.claude by hand would be a hotfix that makes
the next `gearbox deploy` refuse until it is harvested back, so the ledger and
the kill switch have to live where no deploy classifies them.
"""

from __future__ import annotations

import contextlib
import json
import os

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX; the lock degrades to a no-op
    fcntl = None

POLICY_VERSION = 1

SPINE_FIELDS = ("ts", "session", "event", "source", "policy_version", "reason")


def live_root():
    """~/.gearbox-state/compaction — the LIVE state, whatever mode this call runs in.

    Resolved per call so tests can move HOME.
    """
    return os.path.join(os.path.expanduser("~"), ".gearbox-state", "compaction")


def is_probe():
    """This invocation is a HAND PROBE, declared with GEARBOX_COMPACT_SOURCE=probe."""
    return os.environ.get("GEARBOX_COMPACT_SOURCE") == "probe"


def root():
    """Where THIS invocation writes: the probe subtree, or the live one.

    ISOLATION, NOT JUST A TAG. Tagging decisions.ndjson with source='probe' was
    never enough: the heartbeat, policy/<id>.json and VERSION.json carry no
    source field at all, and a probe run against a REAL session id wrote a
    fabricated policy that a real session would then have READ as its own. So
    a probe's whole state root moves to ~/.gearbox-state/compaction/probe/ and it cannot
    touch a live surface on any path. The `source` tag stays as well — a plan session must
    still exclude a probe row it ever meets.

    The two things that stay on the LIVE root on purpose: the kill switch
    (an operator's rollback governs probes too) and the operator's
    model-windows.json, which context_tokens.py reads directly.
    """
    return os.path.join(live_root(), "probe") if is_probe() else live_root()


def claude_dir():
    return os.environ.get("GEARBOX_CLAUDE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )


def repo_dir():
    return os.environ.get("GEARBOX_REPO_DIR") or os.getcwd()


def now_ms():
    import time

    return int(time.time() * 1000)


def iso_utc(ms=None):
    import time

    ms = now_ms() if ms is None else int(ms)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ms / 1000.0))
    return "%s.%03dZ" % (stamp, ms % 1000)


def safe_id(value):
    text = str(value or "unknown")
    kept = "".join(c if (c.isalnum() or c in "._-") else "_" for c in text)
    return kept[:128] or "unknown"


def append_line(path, obj):
    """Append one JSON line with O_APPEND in a SINGLE write() call."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None  # guard the parse AND the type


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.replace(tmp, path)


def tail_lines(path, count):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            handle.seek(max(0, size - 256 * 1024))
            raw = handle.read()
    except OSError:
        return []
    lines = [line for line in raw.decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-count:]


# --- THE ONE LEDGER WRITER -------------------------------------------------


def ledger(event, session, reason, **fields):
    """The ONLY function that appends to decisions.ndjson.

    It stamps the spine — ts / session / event / source / policy_version /
    reason — so no path, including the fail-open one, can produce a record that
    the downstream retro and arming check cannot index. `source` is 'probe' for a hand invocation and
    'live' otherwise; a plan session must exclude probe rows from every cohort.
    """
    record = {
        "ts": iso_utc(),
        "session": str(session or "unknown"),
        "event": event,
        "source": "probe" if is_probe() else "live",
        "policy_version": POLICY_VERSION,
        "reason": reason,
    }
    for key, value in fields.items():
        if key not in SPINE_FIELDS:
            record[key] = value
    try:
        append_line(os.path.join(root(), "decisions.ndjson"), record)
    except Exception:  # a ledger failure must never gate a compaction
        pass
    return record


def write_version_file():
    """a plan session's arming check reads this to know which mechanism a record belongs to."""
    try:
        write_json(
            os.path.join(root(), "policy", "VERSION.json"),
            {"policy_version": POLICY_VERSION, "updated_ms": now_ms()},
        )
    except OSError:
        pass


# --- the kill switch, written once, used by every hook subcommand ----------


def kill_switch():
    """'' when live, else the name of the switch that is on.

    Never consulted by `activation` or `status`: those are operator
    bookkeeping, and a switch that gagged `activation` would corrupt the very
    measurement it exists to keep safe.
    """
    if os.environ.get("GEARBOX_COMPACT_POLICY") == "off":
        return "env"
    # live_root(), never root(): the operator's rollback governs probe runs too.
    if os.path.exists(os.path.join(live_root(), "DISABLED")):
        return "file"
    return ""


# --- the per-session policy file -------------------------------------------


def policy_path(session):
    # ponytail: one small file per session, kept forever, plus a rare orphaned
    # `.tmp.<pid>` if a process dies mid-write. Nothing reads either after the
    # session ends — prune by mtime if the directory ever grows enough to care.
    return os.path.join(root(), "policy", safe_id(session) + ".json")


def read_policy(session):
    return read_json(policy_path(session))


def write_policy(session, policy):
    """Blind whole-file write. Use update_policy() for anything that CHANGES an
    existing policy — this one is for a caller that owns the whole document."""
    write_json(policy_path(session), policy)


# --- the pending pre-compact record: how ctx_before reaches post-compact ----


def pending_path(session):
    return os.path.join(root(), "pending", safe_id(session) + ".json")


def write_pending(session, data):
    """Where THIS session's ctx from its own last PreCompact call waits for
    the matching PostCompact call. Overwritten on every PreCompact call, so a
    compaction blocked N times and then allowed still leaves the ctx from the
    call that actually preceded the real compaction."""
    write_json(pending_path(session), data)


def take_pending(session):
    """Read and DELETE this session's own pending pre-compact record, or None.

    SESSION-KEYED ON PURPOSE. Several sessions append to ONE shared
    decisions.ndjson and can interleave between one session's PreCompact and
    its own PostCompact call — reading "the last pre-compact line in the
    ledger" would then silently attach another session's ctx to this one.
    """
    path = pending_path(session)
    data = read_json(path)
    try:
        os.unlink(path)
    except OSError:
        pass
    return data


# --- the deferred post-compact measurement ---------------------------------
#
# ctx_after does not exist when PostCompact runs: the compact_boundary record
# lands AFTER that hook, so nothing in the transcript is post-compaction yet.
# See context_tokens.context_after_compaction(). PostCompact parks what it DOES
# know here, and a later UserPromptSubmit reads the number and ledgers it.


def deferred_path(session):
    return os.path.join(root(), "deferred", safe_id(session) + ".json")


def write_deferred(session, data):
    """Park this session's unmeasured compaction. Overwritten on every
    compaction, so only the most recent one is ever owed a number."""
    write_json(deferred_path(session), data)


def read_deferred(session):
    """The parked record, or None. Does NOT delete it: the first prompt after a
    compaction is normally too early to measure, and the record must survive
    that attempt to be measurable on the next one."""
    return read_json(deferred_path(session))


def clear_deferred(session):
    try:
        os.unlink(deferred_path(session))
    except OSError:
        pass


@contextlib.contextmanager
def policy_lock(session):
    """Serialise one session's policy file across processes.

    flock is stdlib and the kernel releases it on close OR on process death, so
    there is no stale lock to reap and no timeout to tune. Two fds in one
    process conflict too, which is what makes the race testable.

    A lock we cannot take must NEVER gate a compaction: on any failure we fall
    through unlocked, which is exactly the last-writer-wins behaviour this
    replaces.
    """
    fd = None
    try:
        path = policy_path(session) + ".lock"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        pass
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)  # releases the flock
            except OSError:
                pass


def update_policy(session, mutate):
    """READ-MODIFY-WRITE under the lock. The ONE way to change a policy file.

    Every writer used to do read_policy() ... write_policy(), two separate
    operations over a whole-file replace: a `safe-point --state hold` running
    alongside the UserPromptSubmit hook for the same session dropped whichever
    side wrote first. The loss was fail-open — a dropped hold means the
    compaction proceeds — but it silently discarded a hold a skill had asked
    for, and the skill had no way to know.

    `mutate(current)` returns the document to write, or None to write nothing.
    Returns the policy as it now stands on disk.
    """
    with policy_lock(session):
        current = read_policy(session)
        updated = mutate(current)
        if updated is None:
            return current
        write_json(policy_path(session), updated)
        return updated


# --- whose session is this? ------------------------------------------------

# A worker's environment carries these. MEASURED by
# dumping the environment of a plan-execute subagent: CLAUDE_CODE_CHILD_SESSION=1
# and CLAUDE_CODE_FORK_SUBAGENT=1 were both set, alongside a CLAUDE_CODE_SESSION_ID
# that belongs to the worker, not to the conversation that dispatched it.
WORKER_MARKERS = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_FORK_SUBAGENT")


def env_session():
    """(session_id, refusal) for a WRITE that would default to $CLAUDE_CODE_SESSION_ID.

    NEVER GUESS A SESSION ID for a write. The caller passes --session
    explicitly or nothing is written.

    THE ORIGINAL RATIONALE WAS WRONG, AND THE REFUSAL IS KEPT ANYWAY. It said a
    subagent "sees a well-formed session id that belongs to the subagent", so a
    hold/ok would land on a different policy file. MEASURED later, both halves
    are false: the two markers read '1' inside the
    ORCHESTRATOR's own tool subprocess as well as a worker's, so they separate
    nothing; and a dispatched subagent reports the SAME session id as the
    orchestrator and resolves to the SAME transcript. A safe point written from
    a worker would therefore land on the RIGHT file, not the wrong one.

    The refusal stays because it is the conservative side of a WRITE, it costs
    only an explicit --session, and it may be correct on a Claude Code version
    not yet measured. What must not happen again is a READER inheriting it:
    skills/plan-execute/scripts/orchestrator_ctx.py did, and every dispatch
    recorded a null context while its reader worked fine. A refusal that is
    right for a write is wrong for a read.
    """
    worker = [marker for marker in WORKER_MARKERS if os.environ.get(marker)]
    if worker:
        return "", (
            "refusing to guess: this is a worker (%s set), where "
            "$CLAUDE_CODE_SESSION_ID is the worker's own id and the write would "
            "land on the wrong session — pass --session" % ", ".join(worker)
        )
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        return "", "no session id — pass --session or set CLAUDE_CODE_SESSION_ID"
    return session, ""
