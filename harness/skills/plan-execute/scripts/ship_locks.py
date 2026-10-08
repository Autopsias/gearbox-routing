"""Repo- and plan-scoped shipping LEASES (split out of ship_state_io.py).

One plan's shipping run must exclude another plan's in the SAME checkout, and it
must do so across the several run.py processes one shipping session spans. That
turned out to need a different model from the pidfile lock in run_state_io, so it
lives here rather than growing ship_state_io past its size bound: what the lease
is, where its file goes, and how ownership is proved are one concern.

Imported by ship_state_io, which re-exports every public name — callers still say
``ssio.acquire_ship_lock(...)``.
"""

import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import run_state_io as rsi


def _fsync_dir(dirpath):
    """Flush a directory entry so a rename/create survives a crash. Lives here
    (rather than in ship_state_io) only to keep the import one-way; the durable
    JSON writer imports it back."""
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        dfd = os.open(str(dirpath), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dfd)
    except OSError:
        pass
    finally:
        os.close(dfd)


# --------------------------------------------------------------------------
# Resource-scoped shipping LEASES
#
# SCOPE — where the file lives is decided by what the resource names:
#   * ``git:<root>`` and ``push:<root>`` are REPO-scoped: every plan in one
#     checkout contends for the same working tree, so the file lives in the
#     repo's git COMMON dir (``<git-dir>/plan-locks/``) — never committed, never
#     deployed, shared by every linked worktree, gone when the repo is. Before
#     2026-08-20 they lived under the ACQUIRING PLAN's directory, so two plans
#     resolved the same resource NAME and wrote two different FILES — measured
#     in ``_evidence/s03b/`` as "same file? False", both holding git+push.
#   * ``deploy:<target>`` too, rooted at the plan's project root. ``gate:<id>`` is PLAN-scoped.
#
# LIVENESS is a LEASE, not a pid. ``run.py ship-begin`` returns
# ``{"action": "invoke-skill"}`` and EXITS; /commit-orchestrate runs in another
# process, so the recording pid is dead for the whole operation the lock exists
# to exclude. s03b measured it: the next contender reclaimed the lock as stale
# every time, and 4 simultaneous contenders all "acquired" it, 3 trials of 3. So:
#   * acquire with ``O_CREAT|O_EXCL`` — exactly one winner, never check-then-write;
#   * ownership is an opaque TOKEN recorded in run_state.json, never a pid;
#   * a lease dies at an explicit wall-clock ``expires_at``, not when a pid dies;
#   * taking over an EXPIRED lease is RECORDED — in the new lock file, in the
#     taker's run.ndjson and in the victim's — never a silent transfer;
#   * ``ship-record`` re-verifies the token and REFUSES (halt + decision brief)
#     when the lease was taken over or expired mid-directive.
#
# ponytail: the lease only DETECTS a mid-directive takeover, it cannot prevent
# the overlap — nothing renews a lease while run.py is not running. The defence
# is a lease long enough that expiry means "the orchestrator really is gone"
# (see _lease_seconds) plus the ship-record refusal. Renewal needs a heartbeat
# that outlives run.py; add one only if operations start outliving the lease.
# --------------------------------------------------------------------------
REPO_SCOPED_PREFIXES = ("git:", "push:", "deploy:")
REPO_LOCK_DIRNAME = "plan-locks"
# A repo-scoped lock for a root that is not a git checkout has nowhere inside
# .git to live; keep it repo-wide (still shared by every plan under that root)
# rather than silently falling back to the plan-local path this fix removes.
NON_GIT_LOCK_DIRNAME = ".plan-locks"

# Lease budget. The guarded step's own timeout is the only hard bound the system
# has on how long the operation can run; the orchestrator's turns around it
# (dispatch, the Skill tool's own reasoning, ship-record) are unbounded, hence
# the doubling plus a flat slack. The floor matches rsi.STALE_LOCK_SECONDS' hour
# so a lease is never shorter than the run lock it rides alongside.
LEASE_FACTOR = 2
LEASE_SLACK_SECONDS = 900
MIN_LEASE_SECONDS = rsi.STALE_LOCK_SECONDS
LEASE_RATIONALE = (
    f"{LEASE_FACTOR}x the longest step timeout this resource guards "
    f"+ {LEASE_SLACK_SECONDS}s of orchestrator slack, floored at "
    f"{MIN_LEASE_SECONDS}s. run.py EXITS before the guarded skill runs and "
    "nothing renews the lease, so it must outlive the whole directive plus the "
    "orchestrator turns around it; expiry therefore means the holder is gone, "
    "not merely slow."
)


def _ship_lock_dir(plan_dir):
    """The PLAN-scoped lock directory (deploy:/gate: resources)."""
    return Path(plan_dir) / "_shipping_locks"


def _resource_slug(resource):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(resource)).strip("-").lower()
    return slug or "lock"


def _is_repo_scoped(resource):
    return str(resource).startswith(REPO_SCOPED_PREFIXES)


def _git_common_dir(root):
    """The repo's shared git dir for ``root``, or None if it is not a checkout.

    ``--git-common-dir`` (not ``--git-dir``) so a linked worktree resolves to the
    MAIN repo's .git — otherwise two worktrees of one repo would lock two
    different files, which is the defect this module just fixed one level up."""
    import subprocess
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (p.stdout or "").strip()
    if p.returncode != 0 or not out:
        return None
    d = Path(out)
    return d if d.is_absolute() else (Path(root) / d)


def _repo_lock_dir(plan_dir, resource):
    import plan_scope  # lazy: plan_scope's import chain reaches back here
    root = Path(plan_scope.project_root(plan_dir) if str(resource).startswith("deploy:")
                else str(resource).split(":", 1)[1] or ".")
    common = _git_common_dir(root)
    return (common / REPO_LOCK_DIRNAME) if common else (root / NON_GIT_LOCK_DIRNAME)


def _ship_lock_path(plan_dir, resource):
    """The one place a shipping lock path is constructed — repo-scoped resources
    route out of the plan directory, plan-scoped ones stay in it."""
    d = _repo_lock_dir(plan_dir, resource) if _is_repo_scoped(resource) else _ship_lock_dir(plan_dir)
    return d / f"{_resource_slug(resource)}.lock"


def _lease_seconds(guarded_timeout):
    try:
        guarded = float(guarded_timeout or 0)
    except (TypeError, ValueError):
        guarded = 0
    return int(max(MIN_LEASE_SECONDS, LEASE_FACTOR * guarded + LEASE_SLACK_SECONDS))


def _read_lock(lp):
    try:
        info = json.loads(Path(lp).read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return info if isinstance(info, dict) else {}


def _lease_state(info):
    """``live`` | ``expired`` | ``foreign-host`` | ``unreadable``.

    ``foreign-host`` (LCK-02) outranks expiry: a lease taken on another machine
    is never reclaimed here, however old it is — the clocks, the pid table and
    the process are all somebody else's. It is reported with its host name."""
    if not info or not info.get("token"):
        return "unreadable"
    if not rsi.lock_on_this_host(info):
        return "foreign-host"
    try:
        expires = datetime.fromisoformat(info["expires_at"])
    except (KeyError, TypeError, ValueError):
        # No usable expiry (legacy pidfile-era lock, or hand-edited): fall back
        # to the run lock's staleness horizon measured from started_at.
        age = rsi.lock_age_seconds(info)
        return "expired" if age is None or age > rsi.STALE_LOCK_SECONDS else "live"
    return "live" if datetime.now(UTC) < expires else "expired"


def _locked_message(resource, lp, info, state):
    if state == "foreign-host":
        return (
            f"shipping resource {resource!r} is leased by ANOTHER machine "
            f"(host={info.get('host')}, plan={info.get('plan_dir')}, "
            f"expires={info.get('expires_at')}). A lease taken on {info.get('host')} is "
            f"never reclaimed from here — this host cannot see that process. Clear it on "
            f"{info.get('host')}, or remove {lp} once you know that run is over."
        )
    return (
        f"shipping resource {resource!r} is leased (plan={info.get('plan_dir')}, "
        f"host={info.get('host')}, pid={info.get('pid')}, started={info.get('started_at')}, "
        f"expires={info.get('expires_at')}). Wait for it, or remove {lp} once you know "
        f"that run is over."
    )


def _new_lease(plan_dir, resource, guarded_timeout, taken_over_from=None):
    now = datetime.now(UTC)
    seconds = _lease_seconds(guarded_timeout)
    info = {
        "token": uuid.uuid4().hex,
        "resource": str(resource),
        "plan_dir": str(Path(plan_dir).resolve()),
        **rsi.host_fields(),
        "pid": os.getpid(),  # informational only — NOT what liveness is keyed on
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=seconds)).isoformat(),
        "lease_seconds": seconds,
        "guarded_timeout": guarded_timeout,
        "lease_rationale": LEASE_RATIONALE,
    }
    if taken_over_from:
        info["taken_over_from"] = taken_over_from
        info["taken_over_at"] = now.isoformat()
    return info


def _steal_expired(lp, resource):
    """Atomically take an expired/unreadable lease aside so exactly one contender
    can replace it. Returns the displaced record (for the takeover receipt), or
    None when the file vanished under us. Raises ``rsi.LockError`` if the lease
    is live or foreign."""
    cur = _read_lock(lp)
    state = _lease_state(cur)
    if state in ("live", "foreign-host"):
        raise rsi.LockError(_locked_message(resource, lp, cur, state))
    if state == "unreadable" and _unreadable_is_young(lp):
        raise rsi.LockError(
            f"shipping resource {resource!r}: {lp} exists but carries no readable "
            f"lease and was written under {UNREADABLE_GRACE_SECONDS}s ago — another "
            f"contender is writing it right now. Retry; do not remove it.")
    aside = lp.with_name(f"{lp.name}.superseded-{uuid.uuid4().hex[:8]}")
    try:
        os.rename(str(lp), str(aside))
    except OSError:
        return None  # another contender got there first; re-read and re-judge
    displaced = _read_lock(aside)
    try:
        aside.unlink()
    except OSError:
        pass
    return {k: displaced.get(k) for k in
            ("token", "plan_dir", "host", "pid", "started_at", "expires_at")}


def _lease_record_key(resource):
    return str(resource)


def _record_lease(plan_dir, resource, info, lp):
    """Persist OUR side of the lease in run_state.json, so a later run.py process
    (ship-record/ship-finalize) can prove the lock on disk is still the one we
    took — and so the orchestrating conversation can carry the token."""
    state = rsi.load_state(plan_dir)
    leases = state.setdefault("ship_leases", {})
    leases[_lease_record_key(resource)] = {
        "token": info["token"], "lock_file": str(lp), "acquired_at": info["started_at"],
        "expires_at": info["expires_at"], "lease_seconds": info["lease_seconds"],
    }
    rsi.save_state(plan_dir, state)


def _forget_lease(plan_dir, resource):
    state = rsi.load_state(plan_dir)
    leases = state.get("ship_leases") or {}
    if leases.pop(_lease_record_key(resource), None) is not None:
        state["ship_leases"] = leases
        rsi.save_state(plan_dir, state)


def our_lease(plan_dir, resource):
    """Our recorded lease record for ``resource``, or None."""
    return (rsi.load_state(plan_dir).get("ship_leases") or {}).get(_lease_record_key(resource))


# A lock file must NEVER be visible EMPTY. `O_EXCL` alone creates the entry
# first and writes the JSON after it; a contender reading that window classes
# the lease `unreadable` and steals it, and two plans hold one resource.
# Reproduced by review 2026-08-21 -- the 4-contender barrier test missed it
# because three trials rarely land in a sub-millisecond window. So: write the
# content to a private temp file, then `os.link` it in. `link` is atomic and
# fails EEXIST, so the lock path only ever appears fully-formed.
UNREADABLE_GRACE_SECONDS = 30


def _create_with_content(lp, info):
    """Create `lp` holding `info`, atomically. True if we won, False if it exists."""
    tmp = lp.with_name(f"{lp.name}.new-{uuid.uuid4().hex[:8]}")
    try:
        # ONE guard at the BOUNDARY, not one per call site: a first pass
        # guarded `os.link` alone and left os.open/write/fsync bare, so an
        # ENOSPC there escaped ship_begin's `except rsi.LockError` just the
        # same. Every OSError here means one thing to the caller.
        try:
            fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(json.dumps(info, indent=2))
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(str(tmp), str(lp))
            except FileExistsError:
                return False          # a rival published first; not an error
            _fsync_dir(lp.parent)
            return True
        except OSError as e:
            # No hard links (exFAT/FAT32, some FUSE/SMB -- the shared-checkout
            # case this module targets), EPERM, ENOSPC, EIO. LockError ON
            # PURPOSE: ship_begin catches it and releases the leases it holds.
            # A bare OSError escapes and strands a repo-wide `git:` lease.
            raise rsi.LockError(
                f"cannot publish the lease file at {lp}: {type(e).__name__}: {e}. "
                f"The filesystem may not support hard links, or may be full or "
                f"read-only; a lock cannot be created atomically here.") from e
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _unreadable_is_young(lp):
    """An `unreadable` lease younger than the grace is a WRITER, not a corpse.

    Defence in depth: with `_create_with_content` the path is never published
    empty, so this covers a truncated file from a crashed writer or an older
    build's lock. Stealing one instantly let two plans hold one lease."""
    try:
        age = datetime.now(UTC).timestamp() - lp.stat().st_mtime
    except OSError:
        return False
    return 0 <= age < UNREADABLE_GRACE_SECONDS


def acquire_ship_lock(plan_dir, resource, *, guarded_timeout=None):
    """Take a lease on ``resource``. Atomic exclusive create — exactly one of N
    simultaneous contenders wins. Raises ``rsi.LockError`` when a live (or
    foreign-host) lease already exists. An EXPIRED lease is taken over, and the
    takeover is recorded on both sides.

    ``guarded_timeout`` is the longest step timeout this resource guards; it sets
    the lease duration (see LEASE_RATIONALE, which is written into the file)."""
    lp = _ship_lock_path(plan_dir, resource)
    lp.parent.mkdir(parents=True, exist_ok=True)
    takeover = None
    for _attempt in range(4):
        info = _new_lease(plan_dir, resource, guarded_timeout, takeover)
        if not _create_with_content(lp, info):
            renewed = _renew_own_lease(plan_dir, resource, lp, guarded_timeout)
            if renewed is not None:
                return renewed
            takeover = _steal_expired(lp, resource) or takeover
            continue
        _record_lease(plan_dir, resource, info, lp)
        rsi.log_event(plan_dir, "shipping_lock_acquired", resource=str(resource),
                      token=info["token"], lock_file=str(lp),
                      expires_at=info["expires_at"], lease_seconds=info["lease_seconds"])
        if takeover:
            _record_takeover(plan_dir, resource, info, takeover, lp)
        return True
    raise rsi.LockError(
        f"shipping resource {resource!r}: lost the acquire race four times at {lp}; "
        f"another plan is contending for it right now — retry."
    )


def _renew_own_lease(plan_dir, resource, lp, guarded_timeout):
    """Extend a lease this plan already holds (a resumed ship-begin re-acquiring
    its own resource) and return True, or None when the file is not ours to
    renew. Renewal keeps the TOKEN — the directive already in the orchestrator's
    hands stays valid — and only pushes the expiry out.

    Only a LIVE lease of ours is renewed. An expired one goes through the normal
    takeover path even when the token is our own, because by then another plan
    was entitled to take it and the takeover must be recorded either way."""
    rec = our_lease(plan_dir, resource)
    cur = _read_lock(lp)
    if not rec or not cur.get("token") or cur.get("token") != rec.get("token"):
        return None
    if _lease_state(cur) != "live":
        return None
    now = datetime.now(UTC)
    seconds = _lease_seconds(guarded_timeout)
    cur["expires_at"] = (now + timedelta(seconds=seconds)).isoformat()
    cur["lease_seconds"] = seconds
    cur["renewed_at"] = now.isoformat()
    cur["renewals"] = int(cur.get("renewals") or 0) + 1
    cur["pid"] = os.getpid()
    rsi._atomic_write(lp, json.dumps(cur, indent=2))
    _record_lease(plan_dir, resource, cur, lp)
    rsi.log_event(plan_dir, "shipping_lease_renewed", resource=str(resource),
                  token=cur["token"], lock_file=str(lp), expires_at=cur["expires_at"])
    return True


def _record_takeover(plan_dir, resource, info, takeover, lp):
    """An expired lease NEVER transfers silently: log it in the taker's run.ndjson
    and, when the displaced holder is a plan we can see, in the victim's too."""
    rsi.log_event(plan_dir, "shipping_lease_taken_over", resource=str(resource),
                  token=info["token"], lock_file=str(lp),
                  from_plan=takeover.get("plan_dir"), from_token=takeover.get("token"),
                  from_host=takeover.get("host"), from_expires_at=takeover.get("expires_at"))
    victim = takeover.get("plan_dir")
    if not victim or str(Path(plan_dir).resolve()) == str(victim):
        return
    try:
        if (Path(victim) / "run.ndjson").exists():
            rsi.log_event(victim, "shipping_lease_lost", resource=str(resource),
                          lost_token=takeover.get("token"),
                          taken_by_plan=str(Path(plan_dir).resolve()),
                          taken_by_token=info["token"], expired_at=takeover.get("expires_at"))
    except OSError:
        pass  # best-effort: the receipt in OUR log and the lock file still stand


def lease_status(plan_dir, resource):
    """Is the lease we recorded for ``resource`` still ours, right now?

    ``held`` (same token, not expired) | ``taken-over`` (a different token owns
    the file) | ``expired`` (still our token, but past its expiry — nobody has
    claimed it yet, and we may no longer act under it) | ``vanished`` (the file
    is gone) | ``unrecorded`` (we never recorded one: a plan that began shipping
    before this code, or a plan-scoped step acquired elsewhere)."""
    rec = our_lease(plan_dir, resource)
    if not rec:
        return {"status": "unrecorded", "resource": str(resource)}
    lp = Path(rec.get("lock_file") or _ship_lock_path(plan_dir, resource))
    cur = _read_lock(lp)
    out = {"resource": str(resource), "lock_file": str(lp), "our_token": rec.get("token"),
           "expires_at": rec.get("expires_at"), "holder": cur or None}
    if not cur:
        return {**out, "status": "vanished"}
    if cur.get("token") != rec.get("token"):
        return {**out, "status": "taken-over"}
    return {**out, "status": "held" if _lease_state(cur) == "live" else "expired"}


def release_ship_lock(plan_dir, resource):
    """Release ``resource`` — but only if the file on disk is still OUR lease.

    Deleting a lock we no longer own is how one plan's finalize would free the
    lock another plan is actively holding, so a token mismatch logs and keeps
    its hands off. Plan-scoped locks live in a directory only this plan writes,
    so an unrecorded one there is still ours to clear."""
    lp = _ship_lock_path(plan_dir, resource)
    if not lp.exists():
        _forget_lease(plan_dir, resource)
        return False
    rec = our_lease(plan_dir, resource)
    cur = _read_lock(lp)
    ours = (rec and cur.get("token") == rec.get("token")) or \
           (rec is None and not _is_repo_scoped(resource))
    if not ours:
        rsi.log_event(plan_dir, "shipping_lock_not_ours", resource=str(resource),
                      lock_file=str(lp), holder_token=cur.get("token"),
                      holder_plan=cur.get("plan_dir"))
        _forget_lease(plan_dir, resource)
        return False
    lp.unlink(missing_ok=True)
    _forget_lease(plan_dir, resource)
    rsi.log_event(plan_dir, "shipping_lock_released", resource=str(resource))
    return True


def release_all_ship_locks(plan_dir):
    """Release every lease this plan holds — cleanup on abort/finalize.

    Plan-scoped: everything in our own ``_shipping_locks/``. Repo-scoped: only
    the leases recorded in our run_state whose token still matches on disk."""
    for resource in list((rsi.load_state(plan_dir).get("ship_leases") or {})):
        release_ship_lock(plan_dir, resource)
    d = _ship_lock_dir(plan_dir)
    if not d.is_dir():
        return
    for lp in d.glob("*.lock"):
        info = _read_lock(lp)
        lp.unlink(missing_ok=True)
        rsi.log_event(plan_dir, "shipping_lock_released",
                      resource=info.get("resource", lp.stem))


def held_ship_locks(plan_dir):
    """Shipping resources this plan currently holds (status/tests): its own
    plan-scoped lock files plus every recorded lease still owned by our token."""
    out = []
    for resource in (rsi.load_state(plan_dir).get("ship_leases") or {}):
        if lease_status(plan_dir, resource)["status"] in ("held", "expired"):
            out.append(str(resource))
    d = _ship_lock_dir(plan_dir)
    if d.is_dir():
        for lp in sorted(d.glob("*.lock")):
            out.append(_read_lock(lp).get("resource", lp.stem))
    return sorted(dict.fromkeys(out))

