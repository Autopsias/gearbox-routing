"""R6 §17.2 — an owner stop leaves a RESERVATION, never a lease.

A land that stops to ask the owner releases its ``git:`` lease but leaves
``<plan-locks>/<resource-slug>.reservation.json`` (default 60 minutes). Another plan's
LAND lease acquisition parks ``land-reserved`` while it is live. Nothing else reads it:
worktree setup, ship-begin, ``push:``/``deploy:`` and ``finish`` leases ignore it.
Every read-check-write runs inside the lease file's host mutex (``lease_guard.mutex``,
re-entrant per thread), so no plan can acquire between A's reservation and A's release.
Nothing ever extends ``expires_at``.

The owner's answer (``land-ack``/``land-record``/``land-verify``) ends it in the same mutex
hold that takes the lease, in the same process that goes on with the land (``acquire`` with
``why="answered"``); any other land run ends it when it takes the lease (``own-land``).
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import lease_guard as lg
import run_state_io as rsi
import safe_json as sj
import ship_locks as sl

SECONDS = 3600
STOPS = ("invoke-skill", "gate-on-box-verification", "land-awaits-review")


class Reserved(rsi.LockError):
    """Another plan's live reservation holds this land's turn."""

    def __init__(self, rec):
        self.rec = rec
        super().__init__(f"reserved by {rec.get('plan_slug')} ({rec.get('reason')}) until "
                         f"{rec.get('expires_at')}")


def _path(plan_dir, resource):
    lp = sl._ship_lock_path(plan_dir, resource)
    return lp.with_name(lp.name[:-len(".lock")] + ".reservation.json")


def _same(rec, plan_dir):
    return rec.get("plan_dir") == str(Path(plan_dir).resolve())


def _read(plan_dir, rp):
    """The record, or None (absent, or unreadable: ignored and logged, §17.2 inv. 10)."""
    if not rp.exists():
        return None
    try:
        rec = sj.load_dict(rp.read_bytes())
    except OSError:
        rec = None
    if rec and rec.get("plan_dir") and rec.get("expires_at"):
        return rec
    rsi.log_event(plan_dir, "land_reservation_ignored", file=str(rp), why="unreadable")
    return None


def _remaining(rec):
    try:
        return (datetime.fromisoformat(rec["expires_at"]) - datetime.now(UTC)).total_seconds()
    except (KeyError, TypeError, ValueError):
        return 0.0


def _end_locked(plan_dir, rp, rec, why):
    rp.unlink(missing_ok=True)
    rsi.log_event(rec.get("plan_dir") or plan_dir, "land_reservation_ended", why=why,
                  token=rec.get("token"), resource=rec.get("resource"))


def end(plan_dir, resource, why):
    """End THIS plan's reservation (no-op for another plan's). True when one was removed."""
    rp = _path(plan_dir, resource)
    with lg.mutex(sl._ship_lock_path(plan_dir, resource)):
        rec = _read(plan_dir, rp)
        if rec and _same(rec, plan_dir):
            _end_locked(plan_dir, rp, rec, why)
            return True
    return False


def blocking(plan_dir, resource):
    """The live reservation of ANOTHER plan, or None. An expired one is removed here."""
    rp = _path(plan_dir, resource)
    with lg.mutex(sl._ship_lock_path(plan_dir, resource)):
        rec = _read(plan_dir, rp)
        if rec is None:
            return None
        if _remaining(rec) <= 0:
            _end_locked(plan_dir, rp, rec, "expired")
            return None
        return None if _same(rec, plan_dir) else rec


def acquire(plan_dir, resource, why="own-land", **kw):
    """``sl.acquire_ship_lock`` for LAND only: check the reservation and take the lease in one
    mutex hold. Raises ``Reserved`` (taking nothing); the reserving plan passes its own.
    ``why="answered"`` (the owner's answer, §17.2 inv. 5) ends this plan's reservation in that
    same hold whatever the lease attempt returns; ``own-land`` ends it only on success."""
    with lg.mutex(sl._ship_lock_path(plan_dir, resource)):
        rec = blocking(plan_dir, resource)
        if rec:
            rsi.log_event(plan_dir, "land_reservation_blocked", resource=str(resource),
                          reserved_by=rec.get("plan_slug"), reason=rec.get("reason"),
                          expires_at=rec.get("expires_at"))
            raise Reserved(rec)
        try:
            sl.acquire_ship_lock(plan_dir, resource, **kw)
        except rsi.LockError:
            if why == "answered":
                end(plan_dir, resource, why)
            raise
        end(plan_dir, resource, why)


def release(plan_dir, resource, stop=None, why="real-park"):
    """Release the land lease. At an owner ``stop`` the reservation is written first, inside
    the same mutex hold (§17.2 inv. 1); any other exit ends this plan's own reservation."""
    with lg.mutex(sl._ship_lock_path(plan_dir, resource)):
        if stop:
            reserve(plan_dir, resource, stop)
        else:
            end(plan_dir, resource, why)
        sl.release_ship_lock(plan_dir, resource)


def reserve(plan_dir, resource, reason, seconds=SECONDS):
    """Write this plan's reservation. None (and a log line) while another plan's is live."""
    rp = _path(plan_dir, resource)
    with lg.mutex(sl._ship_lock_path(plan_dir, resource)):
        own = _read(plan_dir, rp)
        if own and _same(own, plan_dir) and _remaining(own) > 0:
            return own     # a repeated stop never moves expires_at or the token
        if blocking(plan_dir, resource):
            rsi.log_event(plan_dir, "land_reservation_skipped", resource=str(resource),
                          why="another plan's reservation is live")
            return None
        now = datetime.now(UTC)
        rec = {"reservation_version": 1, "plan_dir": str(Path(plan_dir).resolve()),
               "plan_slug": Path(plan_dir).name, "token": uuid.uuid4().hex,
               "resource": str(resource), "reason": reason, **rsi.host_fields(),
               "created_at": now.isoformat(), "reservation_seconds": seconds,
               "expires_at": (now + timedelta(seconds=seconds)).isoformat()}
        rsi._atomic_write(rp, json.dumps(rec, indent=2))
        rsi.log_event(plan_dir, "land_reserved", reason=reason, token=rec["token"],
                      expires_at=rec["expires_at"])
        return rec


def owner_stop(out):
    """The owner-stop name of a land result, or None for a real park, a success or an error."""
    kind = out.get("kind") if out.get("action") == "land-parked" else out.get("action")
    return kind if kind in STOPS else None


def parked(rec):
    """The ``land-reserved`` park for a blocked land. It takes nothing and writes nothing."""
    from land_brief import reserved  # noqa: PLC0415
    wait = max(1, int(_remaining(rec)))
    return {"action": "land-parked", "kind": "land-reserved", "reserved_by": rec.get("plan_slug"),
            "reason": rec.get("reason"), "expires_at": rec.get("expires_at"),
            "retry_after_seconds": wait, "brief": reserved(rec, wait)}


def retry_fields(plan_dir, resource):
    """``expires_at`` and ``retry_after_seconds`` of the lease that blocks a land (§17.2 inv. 11)."""
    try:
        exp = sl._read_lock(sl._ship_lock_path(plan_dir, resource)).get("expires_at")
    except rsi.LockError:      # an unreadable lease still answers land-locked, with the fallback wait
        exp = None
    wait = _remaining({"expires_at": exp}) or 60   # shortcut: no usable expiry, poll every minute
    return {"expires_at": exp, "retry_after_seconds": min(max(1, int(wait)), 3600)}
