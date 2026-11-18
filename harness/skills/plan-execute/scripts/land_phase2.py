"""R7 §18.1, §18.4, §18.6, §18.10, §18.11 — the phase-2 controller: the short push
window after the owner's ack (plan-isolation-contract.md §18).

It owns no loop. ``land_push.step_push`` stays the ONE retry loop; this module only
opens and closes the window, holds its one deadline, and turns a phase-2 limit into
a reset to phase 1:

* ``enter`` (``land.land``, right after the lease) opens a window when the run starts
  with an ack. In ``on`` it writes the lease's ``phase`` fields and the host-wide
  marker in ONE host-mutex hold and starts the deadline clock (``land_state``).
* ``leave`` (after a verified push in ``step_push``, on a reset, and in
  ``land.land``'s ``finally`` before the lease release) removes the marker and the
  lease fields and logs ONE ``land_phase2`` event. It is idempotent and needs no lease
  to remove a marker whose token is ours.
* ``reset`` ends the window on ``phase2-deadline``, ``gate-overrun`` or
  ``phase2-retries-exhausted``: the ack moves to ``invalidated_from``, every gate
  table is emptied so every gate re-runs, and the land goes on as phase 1.

``PHASE2_MODE`` is the rollout switch (§18.11). ``off``: nothing here runs. ``shadow``
(the default): the window is observed and logged, nothing is written for writers,
no clock is started, and every outcome is today's. ``on``: the rules above.
"""

import json
import math
import os
import socket
import subprocess
import time
from datetime import UTC, datetime, timedelta

import land_carry as lc
import land_state as lst
import land_writers as lw
import lease_guard as lg
import run_state_io as rsi
import safe_json as sj
import ship_locks as sl
import shipping as shp
import verify_pass as vp
from worktree import git

PHASE2_MODE = "shadow"      # §18.11: off | shadow | on; anything else reads as off
DEADLINE_S = 240            # §18.4: the one phase-2 deadline
MARKER_GRACE_S = 10         # §18.6: the marker expires this long after the deadline
MARKER_VERSION = 1
CARRY_STREAK_MAX = 4        # §18.10: carry ci-check at most 4 times in a row
CARRY_STALE_S = 24 * 3600   # §18.10: never on a real pass older than 24 h

#: This process's open window: t0 (monotonic), and in `on` the marker path, lease
#: file and token. Process state on purpose: a crashed land leaves only an expired
#: marker (§18.6 invariant 1), never a clock another process could read.
_OPEN = {}

remaining = lst.remaining
timeout_for = lst.timeout_for


def mode():
    return PHASE2_MODE if PHASE2_MODE in ("off", "shadow", "on") else "off"


def is_open(st):
    """True while an `on` window holds the clock: the limits below apply."""
    return st.get("phase") == "2" and lst.remaining() is not None


def out_of_time(st):
    return is_open(st) and lst.remaining() <= 0


def _klass(plan_dir, gid):
    """§18.3: `review` (always carries), `carry` or `fast` (the default)."""
    g = shp.resolve_gate(plan_dir, gid, None)
    if vp.is_review_gate(g):
        return "review"
    return "carry" if g.get("phase2") == "carry" else "fast"


def budget(plan_dir, st):
    """§18.6 — the sum of every fast gate's last `wall_s`, its registry timeout when it
    has none. An estimate only; any error is infinite, so the run stays in phase 1."""
    total = 0.0
    try:
        for rec in st.get("gates") or []:
            g = shp.resolve_gate(plan_dir, rec["gate_id"], None)
            if vp.is_review_gate(g) or g.get("phase2") == "carry":
                continue
            wall = rec.get("wall_s")
            total += wall if isinstance(wall, (int, float)) else float(g.get("timeout", 1200))
    except Exception:  # noqa: BLE001 — an unknown budget never opens the window
        return math.inf
    return total


def streak_ok(st, gid, now=None):
    """§18.10 — (True, None) when a carry gate may be copied once more, else
    (False, "carry-streak" | "carry-stale"). No recorded real pass is stale."""
    rec = (st.get("carry_streaks") or {}).get(gid) or {}
    if int(rec.get("streak") or 0) >= CARRY_STREAK_MAX:
        return False, "carry-streak"
    try:
        ran = datetime.fromisoformat(str(rec["last_run_pass_at"]).replace("Z", "+00:00"))
        age = ((now or datetime.now(UTC)) - ran).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False, "carry-stale"
    return (True, None) if age <= CARRY_STALE_S else (False, "carry-stale")


def _remote_url(ctx):
    rc, out, _ = git(["remote", "get-url", ctx["remote"]], ctx["root"])
    return out if rc == 0 and out else None


def _foreign_live(marker, token):
    """Another land's marker (another token) that has not expired. An unreadable one is
    not live: the hook fails open on it (§18.5), so it holds no writer to protect."""
    try:
        m = lw._read_marker(marker)
        return bool(m) and sj.get_str(m, "token") != token and lw._live(m)
    except (OSError, ValueError, TypeError):
        return False


def _mark(plan_dir, ctx, url, started):
    """Write the lease's phase fields and the marker in ONE host-mutex hold (§18.6).
    (token, None), or (None, why) with nothing written: `lease-not-held`, or
    `marker-held` when another clone's land holds a live marker for this remote.
    The marker's own mutex serialises the lands of every clone that share the remote."""
    lp, rec = sl._ship_lock_path(plan_dir, ctx["resource"]), sl.our_lease(plan_dir, ctx["resource"])
    exp = (started + timedelta(seconds=DEADLINE_S + MARKER_GRACE_S)).isoformat()
    marker = lw.marker_path(url)
    with lg.mutex(lp), lg.mutex(marker):
        cur = sl._read_lock(lp)
        if not rec or not cur.get("token") or cur.get("token") != rec.get("token"):
            return None, "lease-not-held"
        if _foreign_live(marker, cur["token"]):
            return None, "marker-held"     # §18.1: another land's window; never replaced
        _OPEN.update(marker=marker, lease=lp, token=cur["token"])   # before any write: leave() can undo
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            rsi._atomic_write(marker, json.dumps({
                "marker_version": MARKER_VERSION, "remote_hash": lw.remote_hash(url),
                "plan_slug": ctx["slug"], "token": cur["token"], "pid": os.getpid(),
                "host": socket.gethostname(), "started_at": started.isoformat(),
                "expires_at": exp}, indent=2))
            rsi._atomic_write(lp, json.dumps({**cur, "phase": "push", "phase_expires_at": exp},
                                             indent=2))
        except BaseException:
            _rollback(marker, lp, cur)
            raise
    return cur["token"], None


def _drop_ours(marker, token):
    """Delete the marker only when its token is `token` (§18.1: a new holder's marker,
    or another clone's, is never touched)."""
    try:
        if sj.get_str(sj.load_dict(marker.read_bytes()) or {}, "token") == token:
            marker.unlink()
    except (OSError, ValueError):
        pass                          # gone already, or not ours to judge: expiry covers it


def _rollback(marker, lp, cur):
    """A half-done `_mark`, still inside its mutex hold: no marker of ours, the lease as
    it was read. A step that fails here is left to `_unmark` and the marker's expiry."""
    for undo in (lambda: _drop_ours(marker, cur["token"]),
                 lambda: rsi._atomic_write(lp, json.dumps(cur, indent=2))):
        try:
            undo()
        except OSError:
            pass


def _log(plan_dir, ctx, st, w, outcome, reason, **extra):
    rsi.log_event(plan_dir, "land_phase2", session_ids=[], slug=ctx["slug"],
                  mode=w.get("mode"), outcome=outcome, reason=reason,
                  from_main=w.get("from_main"), to_main=st.get("expected"),
                  elapsed_s=round(time.monotonic() - _OPEN.get("t0", time.monotonic()), 1),
                  attempts=w.get("attempts", 0), **extra)


def enter(plan_dir, ctx, st):
    """Open the window. None when phase 2 starts (`on`), else why the run stays in
    phase 1. A `shadow` window is still observed, so `leave` can log it."""
    _OPEN.clear()
    lst._PHASE2_DEADLINE.clear()
    stale = st.pop("phase", None), st.pop("phase2", None)   # a crashed window; its marker expires
    m = mode()
    if m == "off" or not st.get("ack") or st.get("landed_sha"):
        if any(stale):
            lst.save(plan_dir, ctx, st)
        return "off" if m == "off" else "no-ack" if not st.get("ack") else "landed"
    url = ctx["remote"] and _remote_url(ctx)
    _OPEN["t0"] = time.monotonic()
    w = {"started_at": lst.now(), "deadline_s": DEADLINE_S, "from_main": st.get("expected"),
         "attempts": 0, "mode": m}
    if budget(plan_dir, st) > DEADLINE_S:
        _log(plan_dir, ctx, st, w, "stayed-phase1", "phase2-budget")
        lst.save(plan_dir, ctx, st)
        return "phase2-budget"
    if m == "on":
        token, why = _mark(plan_dir, ctx, url, datetime.now(UTC)) if url else (None, "no-remote")
        if not token:                  # nothing to hold writers with: today's land
            _log(plan_dir, ctx, st, w, "stayed-phase1", why)
            lst.save(plan_dir, ctx, st)
            return why
        st["phase"] = "2"
        lst._PHASE2_DEADLINE["at"] = _OPEN["t0"] + DEADLINE_S
        os.environ[lw.TOKEN_ENV] = token          # §18.5: our own push never waits on us
    st["phase2"] = w
    lst.save(plan_dir, ctx, st)
    return None if m == "on" else "shadow"


def _unmark():
    """Remove our marker, then our lease's phase fields, in one mutex hold. The marker
    always goes when its token is ours; the lease part only while we still hold it."""
    marker, lp, token = _OPEN.get("marker"), _OPEN.get("lease"), _OPEN.get("token")
    if marker is None:
        return

    try:
        with lg.mutex(lp), lg.mutex(marker):
            _drop_ours(marker, token)
            cur = sl._read_lock(lp)
            if cur.get("token") == token and "phase" in cur:
                cur.pop("phase")
                cur.pop("phase_expires_at", None)
                rsi._atomic_write(lp, json.dumps(cur, indent=2))
    except (rsi.LockError, OSError):
        try:                          # §18.1: the marker must always go, still under its mutex
            with lg.mutex(marker):
                _drop_ours(marker, token)
        except (rsi.LockError, OSError):
            pass                      # not even that mutex: the marker's expiry covers it


def _outcome(result, w, reason):
    """(outcome, reason) for the event, from what ended the window."""
    if result in ("pushed", "reset"):
        out = result
    else:
        res = result or {}
        out = "pushed" if res.get("action") == "landed" else "parked"
        reason = reason or res.get("kind") or res.get("action") or "error"
    if w.get("mode") == "shadow" and out != "parked":
        out = "shadow"
    return out, reason


def _carry_view(plan_dir, st, w):
    """(moved paths, carried, re-run) for the window: only a main move inside it
    built a new candidate. In shadow, `carried` is what `on` would have copied."""
    if st.get("expected") == w.get("from_main"):
        return [], [], []
    last = st.get("last_carry") or {}
    verdict = (last.get("shadow") or last) if w.get("mode") == "shadow" else last
    ids = sorted(r["gate_id"] for r in st.get("gates") or [])
    if verdict.get("decision") != "carry":
        return last.get("moved_paths") or [], [], ids
    if w.get("mode") != "shadow" and "carried_gates" in st:   # what `apply` really copied (a 5th carry runs)
        carried = list(st["carried_gates"])
        return last.get("moved_paths") or [], carried, sorted(set(ids) - set(carried))
    try:
        carried = [g for g in ids if _klass(plan_dir, g) != "fast"]
    except Exception:  # noqa: BLE001 — unresolvable now: report it as re-run
        carried = []
    return last.get("moved_paths") or [], carried, sorted(set(ids) - set(carried))


def leave(plan_dir, ctx, st, result, reason=None):
    """Close the window: marker, lease fields, clock, token, then ONE `land_phase2`
    event. `result` is "pushed", "reset" or the land's result dict. Idempotent."""
    _unmark()
    lst._PHASE2_DEADLINE.clear()
    os.environ.pop(lw.TOKEN_ENV, None)
    w = st.pop("phase2", None)
    st.pop("phase", None)
    if w:
        outcome, why = _outcome(result, w, reason)
        moved, carried, rerun = _carry_view(plan_dir, st, w)
        _log(plan_dir, ctx, st, w, outcome, why, moved_paths=moved, carried=carried,
             rerun=rerun, **({"shadow": (st.get("last_carry") or {}).get("shadow")}
                             if w.get("mode") == "shadow" else {}))
        lst.save(plan_dir, ctx, st)
    _OPEN.clear()


def count_attempt(st, attempt):
    if st.get("phase2"):
        st["phase2"]["attempts"] = attempt


def reset(plan_dir, ctx, st, reason, **fields):
    """§18.4 — a phase-2 limit: log the `land_carry` reset, close the window, move the
    ack to `invalidated_from` and empty every gate table so every gate re-runs. The
    caller goes on as phase 1 in this process."""
    if st.get("ack"):                  # the fact first: logging and `leave` can fail after it is saved
        st["invalidated_from"], st["ack"] = st["ack"], None
    st.update(skill_gates={}, argv_gates={}, on_box_gates={})
    st.pop("moved_since_ack", None)
    lst.save(plan_dir, ctx, st)
    lc.log_reset(plan_dir, ctx, reason, to_main=st.get("expected"), **fields)
    leave(plan_dir, ctx, st, "reset", reason)


def run_step(plan_dir, ctx, st, step, restart):
    """One land step under the clock. In `on`, no time left before the step, a git call
    the deadline killed, or a stop returned after the deadline is a reset: `restart`
    (`land_push._reset`) resets and walks today's land again from the top, so every
    gate re-runs. Outside an `on` window this is `step(...)` and nothing else."""
    if not is_open(st):
        return step(plan_dir, ctx, st)
    why = "gate-overrun" if step.__name__ == "step_gate" else "phase2-deadline"
    if out_of_time(st):
        return restart(plan_dir, ctx, st, "phase2-deadline", step=step.__name__)
    try:
        res = step(plan_dir, ctx, st)
    except subprocess.TimeoutExpired as e:
        if not is_open(st):            # the push exited 0 and stopped the clock: today's error
            raise
        return restart(plan_dir, ctx, st, why, step=step.__name__, killed=str(e)[:200])
    if out_of_time(st):                # a step that succeeded late counts too
        return restart(plan_dir, ctx, st, why, step=step.__name__)
    return res
