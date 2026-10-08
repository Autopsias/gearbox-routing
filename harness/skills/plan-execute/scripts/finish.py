"""FIN-01 / FIN-02 — ``run.py finish <dir> [--no-wait] [--apply]``, the last step of
every plan.

Authority: ``../references/finish-contract.md`` (frozen 2026-10-01). Where this
module and that page disagree, the page wins. In order:

  preflight  remote, fetch + pin (``origin_sha``, ``ci_sha``, ``base_sha``),
             ancestry, then ONE fresh ``git:<root>`` lease
  1 record   the plan's leftover record files, pushed (``finish_record.record``)
  2 leftovers unpushed commits, this plan's worktree and branch; a preserved
             cleanup retried once through ``plan_teardown.teardown_plan_tree``
  3 checkout the owner of the default branch restored + fast-forwarded only when
             provably safe (``finish_record.checkout``)
  4 CI       ``finish_ci.ci_verdict`` — a report, never a repair

Lock-ownership rule: the lease is taken in preflight and released in this
module's own ``finally`` right after step 3, before the CI poll (which may wait
540 s). No callee releases it: step 2 never calls ``step_cleanup`` or
``retire_plan``.
The per-plan run lock (``finish.run.lock``, FIN-18) is taken before finish.json
is read and dropped in that same ``finally``: a second run of the plan parks.

State lives at ``$GIT_COMMON_DIR/plan-state/<slug>/finish.json``, never under
``_plans/``, so finish never dirties the checkout it is cleaning.
"""

import contextlib
import fcntl
import json
import sys
import uuid
from pathlib import Path

import finish_ci
import finish_notes as fn
from finish_leftovers import leftovers
import finish_record as fr
import land_state as lst
import plan_scope as ps
import plan_teardown as pt
import plan_worktree as pwt
import run_state_io as rsi
import ship_locks as sl
import ship_state_io as ssio
import worktree as wt
from land_state import now, rev
from worktree import git

STATE_FILE = "finish.json"
DEFAULT_TIMEOUT_S = 540               # under the 600 s Bash tool cap
LEASE_TIMEOUT = 1800
REPOLL = fn.REPOLL


def state_path(root, slug):
    return pwt.plan_state_dir(root, slug) / STATE_FILE


def _save(ctx, st):
    ssio.durable_write_json(state_path(ctx["root"], ctx["slug"]), st)


def _load(ctx):
    return ssio.read_json_with_bak(state_path(ctx["root"], ctx["slug"])) or {}


@contextlib.contextmanager
def _exclusive(ctx):
    """FIN-16: one finish invocation at a time between reading finish.json and
    writing it back. The ship lease cannot do this: a second finish run of the
    SAME plan re-enters it (``ship_locks._renew_own_lease``). An OS lock on a
    sidecar file is owned by this open file, so it excludes every other caller,
    another thread included. Held for milliseconds, never across a git step or
    a CI poll; the kernel drops it if the process dies."""
    path = state_path(ctx["root"], ctx["slug"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(f"{path}.lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield


@contextlib.contextmanager
def _run_lock(ctx):
    """FIN-18: one finish RUN of this plan at a time, from its admission read until
    its steps checkpoint is written. ``_exclusive`` guards one read-write only, and
    a same-plan run re-enters the lease. Yields a release callable, or None when
    another run holds the lock (never waits). The kernel drops it if we die."""
    path = state_path(ctx["root"], ctx["slug"]).with_name("finish.run.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
        yield None if fh.closed else fh.close


def _default_branch(root, remote):
    if remote:
        rc, out, _ = git(["symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD"], root)
        if rc == 0 and "/" in out:
            return out.split("/", 1)[1]
    rc, out, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return out if rc == 0 and out and out != "HEAD" else "main"


class NotAPlan(ValueError):
    """The folder finish was given is not ``<repo root>/_plans/<slug>``."""


def context(plan_dir):
    """``land_state.context`` for an isolated plan; the same shape, built here, for
    a non-isolated one. None for a non-git target. Raises ``NotAPlan`` unless the
    folder is exactly ``<root>/_plans/<slug>`` (resolved, so no symlink escape) and
    holds a ``manifest.json``: the record boundary is derived from this folder, so
    any other folder would push files that are not a plan's record."""
    plan_dir = Path(plan_dir).resolve()
    root = wt.repo_root(plan_dir)
    if root is None:
        return None
    if plan_dir.parent != Path(root).resolve() / "_plans" \
            or not (plan_dir / "manifest.json").is_file():
        raise NotAPlan(f"{plan_dir} is not a plan folder: finish needs "
                       f"{Path(root).resolve() / '_plans'}/<slug> with a manifest.json")
    iso = lst.context(plan_dir)
    if iso is not None:
        return {**iso, "isolated": True,
                "default": iso["default"] or _default_branch(root, iso["remote"])}
    remotes = git(["remote"], root)[1].split()
    remote = "origin" if "origin" in remotes else (remotes[0] if len(remotes) == 1 else None)
    default = ps.claim(plan_dir).get("default_branch") or _default_branch(root, remote)
    return {"plan_dir": str(plan_dir), "root": root, "slug": pwt.plan_slug(plan_dir),
            "branch": None, "default": default, "remote": remote,
            "resource": f"git:{root}", "isolated": False,
            "remote_ambiguous": remotes if remote is None and len(remotes) > 1 else None}


def arm(plan_dir):
    """``run.py begin`` calls this: an armed plan finishes in apply mode. A state
    that exists is left alone, except one whose git steps already ran (finished,
    or stopped during its CI poll): that plan is being reopened, so a fresh cycle
    starts (``armed_at`` and a new ``generation``, every result of the previous
    finish dropped). The new generation makes a poll still running from the old
    cycle end as ``finish-superseded``. A non-git target or a non-plan folder has
    nothing to arm."""
    try:
        ctx = context(plan_dir)
    except NotAPlan:
        return
    if ctx is None:
        return
    with _exclusive(ctx):
        st = _load(ctx)
        if not st or st.get("finished_at") or st.get("steps_done_at"):
            _save(ctx, {"armed_at": now(), "generation": uuid.uuid4().hex})


def finish_action(plan_dir, action):
    """Insert ``finish`` between ``complete`` and the end of the plan (contract,
    "When ``plan`` returns ``finish``"). Writes nothing: the action is resolved
    from ``finish.json`` and handed back; only ``run.py finish`` performs a step.
    A scoped answer (``scope: session``) now appears only while other sessions
    are open, so it is not the plan's end and passes through unchanged."""
    if action.get("action") != "complete" or action.get("scope") == "session":
        return action
    try:
        ctx = context(plan_dir)
    except NotAPlan as e:
        return {**action, "finish": f"skipped — {e}"}
    if ctx is None:
        return {**action, "finish": f"skipped — {plan_dir} is not in a git checkout"}
    st = ssio.read_json_with_bak(state_path(ctx["root"], ctx["slug"])) or {}
    if st.get("finished_at"):
        return action
    mode = "apply" if st.get("armed_at") else "report-only"
    return {**action, "action": "finish", "finish_mode": mode,
            "hint": f"the plan is done but not finished — run `run.py finish {plan_dir}` "
                    "(report CI's verdict in plain words), then `plan` again"}


def _skipped():
    return {"record": {"status": "skipped", "park_reason": None, "sha": None, "paths": [],
                       "outside_record": []},
            "leftovers": None,
            "checkout": {"owner": None, "status": "skipped", "target_sha": None,
                         "restored": [], "blocking": [], "command": None},
            "ci": None}


# --------------------------------------------------------------------------
# Preflight 1-3 (4, the lease, is taken by `finish`)
# --------------------------------------------------------------------------
def _preflight(plan_dir, ctx):
    """``(preflight, park_brief)``; ``park_brief`` is None when nothing parked."""
    root, remote, default = ctx["root"], ctx["remote"], ctx["default"]
    pre = {"remote": remote, "origin_sha": None, "ci_sha": None, "base_sha": None,
           "park_reason": None}

    def park(reason, brief):
        pre["park_reason"] = reason
        return pre, brief

    if remote is None and ctx.get("remote_ambiguous"):
        return park("remote-ambiguous", f"FINISH PARKED — several remotes "
                    f"{ctx['remote_ambiguous']} and none is `origin`. Guessing a push "
                    "target is worse than refusing one.")
    land = (lst.load(plan_dir, ctx) or {}) if ctx["isolated"] else {}
    if ctx["isolated"]:
        pre["ci_sha"], pre["base_sha"] = land.get("landed_sha"), land.get("landed_base")
        if not pre["ci_sha"]:
            return park("not-ancestor", "FINISH PARKED — this isolated plan has no "
                        "`landed_sha` in land.json, so there is no landed commit to "
                        f"finish. Run `run.py land {plan_dir}` first.")
    else:
        pre["ci_sha"] = rev(root, f"refs/heads/{default}")
    if remote is None:
        return pre, None
    rc, out, err = git(["fetch", remote, default], root, timeout=600)
    pre["origin_sha"] = rev(root, f"refs/remotes/{remote}/{default}") if rc == 0 else None
    if not pre["origin_sha"]:
        return park("fetch-failed", f"FINISH PARKED — `git fetch {remote} {default}` "
                    f"failed: {(err or out).strip()[:500]}")
    origin = pre["origin_sha"]
    if ctx["isolated"]:
        for key in ("landed_sha", "final_record_sha"):
            sha = land.get(key)
            if sha and not fr.ancestor(root, sha, origin):
                return park("not-ancestor", f"FINISH PARKED — {key} {sha[:12]} is not on "
                            f"{remote}/{default} ({origin[:12]}). Origin was rewritten; "
                            "finish pushes nothing onto a history it cannot prove.")
    elif not pre["ci_sha"] or not fr.ancestor(root, pre["ci_sha"], origin):
        return park("local-ahead", f"FINISH PARKED — your local `{default}` is ahead of "
                    f"or diverged from {remote}/{default}. A record built on origin's "
                    "tip would split the two histories. Push first, then re-run:\n"
                    f"  git -C {fr.lb.owner_of_default(git, root, default) or root} "
                    f"push {remote} {default}\n  run.py finish {plan_dir}")
    return pre, None


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def _done(plan_dir, ctx, out, notes, event):
    if ctx["isolated"] and out["action"] in ("finished", "finish-superseded") \
            and out["mode"] == "apply":
        manual = pt.manual_remote_delete(plan_dir, ctx, lst.load(plan_dir, ctx))
        notes = [*notes, manual and f"Remote plan branch kept: {manual}"]
    out["brief"] = fn.brief(out, notes)
    plan_dir = out.pop("plan_dir")
    rsi.log_event(plan_dir, event, session_ids=[], slug=ctx["slug"], mode=out["mode"],
                  action=out["action"],
                  record=(out.get("record") or {}).get("status"),
                  checkout=(out.get("checkout") or {}).get("status"),
                  ci=(out.get("ci") or {}).get("verdict"))
    return out


SEEN = ("finished_at", "checked_at", "ci")     # what a poll compares before it writes
UNSEEN = dict.fromkeys(SEEN)                  # a new cycle has none of them
def _outcome(plan_dir, settled, action, note):
    """Map a ``_settle`` result to (action, park_reason, note); ``written`` keeps
    the caller's own action and note."""
    status, detail = settled
    if status == "superseded":
        return "finish-superseded", None, fn.superseded(plan_dir, detail)
    if status == "busy":
        return "finish-parked", "ci-save-busy", fn.busy(plan_dir, detail)
    return action, None, note


def _write_if(ctx, gen, seen, fields):
    """Under ``_exclusive``: re-read finish.json and write ``fields`` into it only
    if the ``generation`` and the ``SEEN`` values are still what this run saw.
    ``(written, None)``, or ``(superseded, fn.case(saved state))``."""
    with _exclusive(ctx):
        cur = _load(ctx)
        if cur.get("generation") != gen or any(cur.get(k) != seen.get(k) for k in SEEN):
            return "superseded", fn.case(cur)
        cur.update(fields)
        _save(ctx, cur)
        return "written", None


def _settle(plan_dir, ctx, gen, seen, fields):
    """Write ``fields`` into finish.json only if nothing moved since this run read
    it: the same ``generation`` (no newer finish run) and the same ``SEEN`` values
    (no other poll of this generation wrote first). Takes the lease briefly, then
    ``_write_if`` re-reads under the per-invocation lock: the in-memory copy is
    stale after a long CI poll, and the lease alone lets a same-plan run in.
    Returns ``_write_if``'s pair, or ``(busy, <lock error>)``: the lease was held."""
    try:
        sl.acquire_ship_lock(plan_dir, ctx["resource"], guarded_timeout=LEASE_TIMEOUT)
    except rsi.LockError as e:
        return "busy", str(e)
    try:
        return _write_if(ctx, gen, seen, fields)
    finally:
        sl.release_ship_lock(plan_dir, ctx["resource"])


ADMIT = ("finished_at", "steps_done_at", "generation")   # what admission decided on


def _new_cycle(ctx, pre, gen, read):
    """A new cycle, under ``_exclusive``: drop the last cycle's results and take a
    fresh ``generation``, so any older poll still running is superseded. FIN-17:
    only if the ``ADMIT`` values are still what ``finish`` read before the lease
    (which a second run of this plan re-enters); otherwise reset nothing and
    return ``fn.case`` of the saved state."""
    with _exclusive(ctx):
        st = _load(ctx)
        if any(st.get(k) != read.get(k) for k in ADMIT):
            return fn.case(st)
        for k in ("finished_at", "steps_done_at", "steps", "ci", "reported_at", "checked_at"):
            st.pop(k, None)
        st.update({k: pre[k] for k in ("origin_sha", "ci_sha", "base_sha")}, generation=gen)
        _save(ctx, st)
        return None


def _repoll(plan_dir, ctx, st, timeout_s):
    """The re-run rule: steps 1-3 are left alone. A finished state re-polls only a
    pending/unknown verdict; a state killed before its poll (``steps_done_at``, no
    ``finished_at``) polls the pinned ``ci_sha`` when no verdict was saved, then
    stamps ``finished_at``. Written only while this run's generation still owns it."""
    ci, finishing = st.get("ci"), not st.get("finished_at")
    fields = {}
    if st.get("ci_sha") and ctx["remote"] and (
            (ci and ci.get("verdict") in REPOLL) or (finishing and not ci)):
        ci = finish_ci.ci_verdict(ctx["root"], st["ci_sha"], branch=ctx["default"],
                                  timeout_s=timeout_s, base_sha=st.get("base_sha"))
        fields.update(ci=ci, checked_at=now())
    if finishing:
        stamp = now()
        fields.update(finished_at=stamp, ci=ci, checked_at=stamp)
        if st.get("mode") == "report-only":
            fields["reported_at"] = stamp
    steps = st.get("steps") if finishing else None
    note = (f"Steps 1-3 already ran at {st.get('steps_done_at') or st.get('finished_at')}; "
            "only CI was looked at again.")
    action, park = "finished", None
    if fields:
        action, park, note = _outcome(plan_dir, _settle(plan_dir, ctx, st.get("generation"),
                                                        {k: st.get(k) for k in SEEN}, fields),
                                      action, note)
    steps = steps or {}
    out = {"action": action, "plan": ctx["slug"], "plan_dir": str(plan_dir),
           "mode": st.get("mode"), "default_branch": ctx["default"], "park_reason": park,
           "preflight": {"remote": ctx["remote"], "origin_sha": st.get("origin_sha"),
                         "ci_sha": st.get("ci_sha"), "base_sha": st.get("base_sha"),
                         "park_reason": None},
           "record": steps.get("record"), "leftovers": steps.get("leftovers"),
           "checkout": steps.get("checkout"), "ci": ci}
    return _done(plan_dir, ctx, out, [note], "plan_finish_ci")


def finish(plan_dir, *, timeout_s=DEFAULT_TIMEOUT_S, apply=False):
    plan_dir = Path(plan_dir).resolve()
    try:
        ctx = context(plan_dir)
    except NotAPlan as e:                  # nothing is read, written or pushed
        return {"action": "finish-parked", "plan": plan_dir.name, "mode": None,
                "preflight": {"park_reason": "not-a-plan"},
                "brief": f"FINISH PARKED — {e}. Nothing was pushed."}
    if ctx is None:
        return {"action": "finish-skipped", "plan": plan_dir.name, "mode": None,
                "brief": f"FINISH SKIPPED — {plan_dir} is not in a git checkout."}
    with _run_lock(ctx) as release:
        if release is None:                # nothing is read, written or pushed
            rsi.log_event(str(plan_dir), "plan_finish_parked", session_ids=[],
                          slug=ctx["slug"], action="finish-parked", park_reason="finish-running")
            return {"action": "finish-parked", "plan": ctx["slug"], "mode": None,
                    "default_branch": ctx["default"], "park_reason": "finish-running",
                    "preflight": None, **_skipped(), "brief": fn.running(plan_dir)}
        return _run(plan_dir, ctx, timeout_s, apply, release)


def _run(plan_dir, ctx, timeout_s, apply, release):
    """``finish`` under the run lock; ``release`` drops it before any CI poll."""
    st = ssio.read_json_with_bak(state_path(ctx["root"], ctx["slug"])) or {}
    if (st.get("finished_at") or st.get("steps_done_at")) \
            and not (apply and st.get("mode") == "report-only"):
        release()
        return _repoll(plan_dir, ctx, st, timeout_s)
    mode = "apply" if apply or st.get("armed_at") else "report-only"
    writes = mode == "apply"
    out = {"action": "finished", "plan": ctx["slug"], "plan_dir": str(plan_dir),
           "mode": mode, "default_branch": ctx["default"], "park_reason": None,
           "preflight": None, **_skipped()}
    pre, brief = _preflight(plan_dir, ctx)
    out["preflight"] = pre
    if brief:
        out["action"] = "finish-parked"
        return _done(plan_dir, ctx, out, [brief], "plan_finish_parked")
    try:
        sl.acquire_ship_lock(plan_dir, ctx["resource"], guarded_timeout=LEASE_TIMEOUT)
    except rsi.LockError as e:
        pre["park_reason"], out["action"] = "lease-busy", "finish-parked"
        return _done(plan_dir, ctx, out, [f"FINISH PARKED — {e}"], "plan_finish_parked")
    notes = []
    gen = uuid.uuid4().hex

    def lease_ok():
        return sl.lease_status(plan_dir, ctx["resource"])["status"] == "held"

    try:                                   # every exit after acquire releases the lease
        moved = _new_cycle(ctx, pre, gen, st)
        if moved:                          # another run of this plan got there first
            out["action"] = "finish-superseded"
            return _done(plan_dir, ctx, out, [fn.superseded(plan_dir, moved)], "plan_finish")
        rec, target, staged = fr.record(plan_dir, ctx, pre["origin_sha"],
                                        apply=writes, lease_ok=lease_ok)
        notes.append(rec.pop("detail", None))
        out["record"] = rec
        if rec["status"] == "parked":
            out["action"] = "finish-parked"
            return _done(plan_dir, ctx, out, notes, "plan_finish_parked")
        out["leftovers"] = leftovers(plan_dir, ctx, pre["origin_sha"], apply=writes,
                                     lease_ok=lease_ok)
        rel = plan_dir.relative_to(Path(ctx["root"]).resolve()).as_posix()
        co = fr.checkout(ctx, rel, target,
                         lambda p: staged[p] if p in staged else fr.blob(ctx["root"], target, p),
                         apply=writes, lease_ok=lease_ok)
        would = co.pop("would_restore", None)
        notes += [f"Would restore: {would}" if would else None,
                  f"git said: {co.pop('git')}" if "git" in co else None]
        out["checkout"] = co
        # checkpoint BEFORE the lease goes and the CI poll starts: a run stopped
        # during the poll must not repeat steps 1-3. Written only while this cycle
        # still owns the state; if it does not (a `begin` re-armed it, or another
        # run took it), the CI poll is skipped and this run is finish-superseded.
        saved = _write_if(ctx, gen, UNSEEN, {
            "mode": mode, "target_sha": target, "steps_done_at": now(),
            "steps": {"record": rec, "leftovers": out["leftovers"], "checkout": co}})
    finally:
        sl.release_ship_lock(plan_dir, ctx["resource"])
        release()                          # FIN-18: the run lock goes before the CI poll
    if saved[0] == "superseded":
        out["action"], _, note = _outcome(plan_dir, saved, None, None)
        return _done(plan_dir, ctx, out, [*notes, note], "plan_finish")
    if ctx["remote"]:
        out["ci"] = finish_ci.ci_verdict(ctx["root"], pre["ci_sha"], branch=ctx["default"],
                                         timeout_s=timeout_s, base_sha=pre["base_sha"])
    stamp = now()
    fields = {"finished_at": stamp, "ci": out["ci"], "checked_at": stamp}
    if mode == "report-only":
        fields["reported_at"] = stamp
    out["action"], out["park_reason"], note = _outcome(
        plan_dir, _settle(plan_dir, ctx, gen, UNSEEN, fields), out["action"], None)
    notes.append(note)                     # _brief drops a None note
    return _done(plan_dir, ctx, out, notes, "plan_finish")


def cli(plan_dir, args):
    payload = finish(plan_dir, timeout_s=0 if args.no_wait else DEFAULT_TIMEOUT_S,
                     apply=args.apply)
    if payload.get("brief"):
        print("=" * 72)
        print(payload["brief"].rstrip())
        print("=" * 72)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if payload.get("action") == "finish-parked":
        sys.exit(1)
