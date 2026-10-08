"""FIN-12 / FIN-15 / FIN-16 / FIN-17 — finish saves its CI result only when nothing moved:
superseded and busy outcomes, the per-invocation lock, the reset on reopen.
Split out of test_finish.py (file-size bound); it reuses that file's fixtures.

    python3 -m pytest -q test_finish_settle.py
"""
# ruff: noqa: F811  (ci and fx are pytest fixtures imported from test_finish)

import sys
import threading
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import finish  # noqa: E402
import finish_ci  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import origin_main, run_cli  # noqa: E402
from test_finish import _arm, _dirty_record, _kill_ci, _pending, _state, ci, fx  # noqa: E402,F401


def _during_poll(monkeypatch, change):
    """The next CI poll runs `change` first: something else moves the state meanwhile."""
    stub = finish_ci.ci_verdict

    def slow(*a, **kw):
        monkeypatch.setattr(finish_ci, "ci_verdict", stub)
        change()
        return stub(*a, **kw)
    monkeypatch.setattr(finish_ci, "ci_verdict", slow)


def test_a_repoll_whose_generation_moved_is_superseded_not_finished(fx, ci, monkeypatch):
    ctx = _pending(fx, ci)
    _during_poll(monkeypatch, lambda: finish._save(ctx, {**_state(fx), "generation": "newer"}))
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded" and "newer finish run" in out["brief"]
    assert _state(fx)["ci"]["verdict"] == "pending"


def test_a_repoll_refuses_when_the_stored_checked_at_moved(fx, ci, monkeypatch):
    ctx = _pending(fx, ci)
    moved = "2099-01-01T00:00:00Z"
    _during_poll(monkeypatch, lambda: finish._save(ctx, {**_state(fx), "checked_at": moved}))
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded", out["brief"]
    st = _state(fx)
    assert st["checked_at"] == moved and st["ci"]["verdict"] == "pending"


def test_two_polls_of_one_generation_never_let_the_older_overwrite(fx, ci, monkeypatch):
    _pending(fx, ci)
    ci[1].append("red")                    # the poll that writes first sees red
    inner = {}
    _during_poll(monkeypatch, lambda: inner.update(finish.finish(fx["plan"], timeout_s=0)))
    out = finish.finish(fx["plan"], timeout_s=0)     # its own poll then reads green
    assert inner["action"] == "finished" and inner["ci"]["verdict"] == "red"
    assert out["action"] == "finish-superseded" and out["ci"]["verdict"] == "green"
    # the saved state is finished: a --no-wait re-run reads it
    assert f"run.py finish {fx['plan']} --no-wait" in out["brief"], out["brief"]
    assert _state(fx)["ci"]["verdict"] == "red"


def test_a_busy_lease_at_the_resume_write_parks_and_the_cli_exits_nonzero(fx, ci, monkeypatch):
    _arm(fx)
    _dirty_record(fx)
    _kill_ci(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        finish.finish(fx["plan"], timeout_s=0)
    ctx = finish.context(fx["plan"])
    finish._save(ctx, {**_state(fx), "ci": {"verdict": "green"}})   # nothing left to poll
    other = fx["plans"]["other"]                  # another plan holds the shared lease
    sl.acquire_ship_lock(other, ctx["resource"], guarded_timeout=60)
    try:
        rc, out = run_cli("finish", fx["plan"], "--no-wait")
    finally:
        sl.release_ship_lock(other, ctx["resource"])
    assert rc == 1 and out["action"] == "finish-parked", out
    assert out["park_reason"] == "ci-save-busy" and out["preflight"]["park_reason"] is None
    assert "only the CI result was not saved" in out["brief"].splitlines()[0], out["brief"]
    assert "lease was busy" in out["brief"] and f"held by the run of {other}" in out["brief"]
    assert "git steps (record, leftovers, checkout) had already run" in out["brief"]
    assert f"run.py finish {fx['plan']} --no-wait" in out["brief"], out["brief"]
    assert not _state(fx).get("finished_at")
    assert finish.finish(fx["plan"], timeout_s=0)["action"] == "finished"   # retryable
    assert _state(fx)["finished_at"]


def test_a_late_busy_lease_parks_after_the_git_steps_and_the_rerun_skips_them(fx, ci, monkeypatch):
    calls, _ = ci
    _arm(fx)
    _dirty_record(fx)
    real, seen = sl.acquire_ship_lock, []

    def busy_on_the_save(*a, **kw):       # the steps' lease is free; the save's is not
        seen.append(a)
        if len(seen) > 1:
            raise finish.rsi.LockError("held by another plan")
        return real(*a, **kw)
    monkeypatch.setattr(sl, "acquire_ship_lock", busy_on_the_save)
    out = finish.finish(fx["plan"], timeout_s=0)
    monkeypatch.setattr(sl, "acquire_ship_lock", real)
    assert out["action"] == "finish-parked" and out["park_reason"] == "ci-save-busy"
    assert out["preflight"]["park_reason"] is None
    assert out["record"]["status"] == "pushed" and "had already run" in out["brief"]
    assert "held by another run" in out["brief"] and "--no-wait" in out["brief"]
    st = _state(fx)
    assert st["steps_done_at"] and not st.get("finished_at") and "ci" not in st
    pushed = origin_main(fx)
    again = finish.finish(fx["plan"], timeout_s=0)
    assert again["action"] == "finished" and again["record"]["status"] == "pushed"
    assert origin_main(fx) == pushed                         # the git steps did not run again
    assert len(calls) == 2 and calls[1]["sha"] == st["ci_sha"]   # only CI was looked at again
    assert _state(fx)["finished_at"] and "only CI was looked at again" in again["brief"]


# --------------------------------------------------------------------- FIN-16


def test_two_same_plan_saves_never_both_win(fx, ci, monkeypatch):
    """Both callers read before either writes (the barrier), unless the
    per-invocation lock holds the second one back: then exactly one writes."""
    ctx = _pending(fx, ci)
    st = _state(fx)
    seen = {k: st.get(k) for k in finish.SEEN}
    barrier, real = threading.Barrier(2), finish._load

    def read_then_wait(c):
        got = real(c)
        try:
            barrier.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass                           # the other caller is held at the lock
        return got
    monkeypatch.setattr(finish, "_load", read_then_wait)
    results = []

    def save(verdict):
        results.append(finish._settle(fx["plan"], ctx, st["generation"], seen,
                                      {"ci": {"verdict": verdict}, "checked_at": verdict}))
    threads = [threading.Thread(target=save, args=(v,)) for v in ("red", "green")]
    # this plan already holds the lease, as a second finish run of it would find:
    # each caller re-enters it (ship_locks._renew_own_lease), so the lease
    # alone excludes neither
    sl.acquire_ship_lock(fx["plan"], ctx["resource"], guarded_timeout=60)
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        sl.release_ship_lock(fx["plan"], ctx["resource"])
    assert sorted(r[0] for r in results) == ["superseded", "written"], results
    assert dict(results)["superseded"] == "finished"


def test_a_reopened_plan_after_a_stopped_finish_runs_the_git_steps_again(fx, ci, monkeypatch):
    """`begin` on a plan whose finish stopped during its CI poll starts a new
    cycle: the old poll is superseded and the next finish repeats steps 1-3."""
    stub = finish_ci.ci_verdict
    _arm(fx)
    _dirty_record(fx)
    _kill_ci(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        finish.finish(fx["plan"], timeout_s=0)
    old = _state(fx)
    assert old["steps_done_at"] and not old.get("finished_at")
    finish.arm(fx["plan"])                 # what `run.py begin` calls
    st = _state(fx)
    assert set(st) == {"armed_at", "generation"} and st["generation"] != old["generation"]
    ctx = finish.context(fx["plan"])
    late = finish._settle(fx["plan"], ctx, old["generation"],
                          {k: old.get(k) for k in finish.SEEN}, {"finished_at": "late"})
    assert late == ("superseded", "restarted") and not _state(fx).get("finished_at")
    monkeypatch.setattr(finish_ci, "ci_verdict", stub)
    (fx["plan"] / "sessions" / "s02.md").write_text("reopened work\n")
    before = origin_main(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finished" and out["record"]["status"] == "pushed", out["brief"]
    assert origin_main(fx) != before and "only CI was looked at again" not in out["brief"]
    assert _state(fx)["ci_sha"] and _state(fx)["finished_at"]


def test_a_begin_during_the_ci_poll_supersedes_it_and_points_to_plan(fx, ci, monkeypatch):
    _arm(fx)
    _dirty_record(fx)
    _during_poll(monkeypatch, lambda: finish.arm(fx["plan"]))
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded", out["brief"]
    assert "The plan was reopened" in out["brief"] and "--no-wait" not in out["brief"]
    assert f"run.py plan {fx['plan']}" in out["brief"] and "re-run of finish is safe" in out["brief"]
    assert set(_state(fx)) == {"armed_at", "generation"}


# --------------------------------------------------------------------- FIN-17


def test_a_cycle_that_moved_after_admission_is_never_reset(fx, ci, monkeypatch):
    """Another run of this plan checkpoints between this run's admission read and
    its reset (the lease lets it in): nothing is reset and no git step runs."""
    calls, _ = ci
    ctx = _arm(fx)
    _dirty_record(fx)
    theirs = {"armed_at": "a", "generation": "theirs", "mode": "apply",
              "steps_done_at": "2026-10-04T00:00:00Z", "steps": {"record": {"status": "pushed"}}}
    real = finish._preflight

    def another_run_checkpoints(*a):
        got = real(*a)
        finish._save(ctx, theirs)
        return got
    monkeypatch.setattr(finish, "_preflight", another_run_checkpoints)
    monkeypatch.setattr(finish.fr, "record", lambda *a, **k: pytest.fail("git steps ran"))
    before = origin_main(fx)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded", out["brief"]
    assert _state(fx) == theirs and origin_main(fx) == before and not calls
    assert "Wait for the finish run you started" in out["brief"], out["brief"]
    assert f"it died: run:\n  run.py finish {fx['plan']}" in out["brief"]
    assert sl.lease_status(fx["plan"], ctx["resource"])["status"] != "held"


def test_a_run_superseded_during_its_git_steps_skips_the_ci_poll(fx, ci, monkeypatch):
    calls, _ = ci
    ctx = _arm(fx)
    _dirty_record(fx)
    newer, real = {"armed_at": "a", "generation": "newer"}, finish.fr.checkout

    def reopened_meanwhile(*a, **k):
        got = real(*a, **k)
        finish._save(ctx, newer)
        return got
    monkeypatch.setattr(finish.fr, "checkout", reopened_meanwhile)
    out = finish.finish(fx["plan"], timeout_s=0)
    assert out["action"] == "finish-superseded" and not calls, out["brief"]
    assert out["record"]["status"] == "pushed" and "The plan was reopened" in out["brief"]
    assert _state(fx) == newer


# --------------------------------------------------------------------- FIN-18


def test_a_second_run_while_another_holds_the_run_lock_parks_and_does_nothing(fx, ci, monkeypatch):
    import fcntl
    calls, _ = ci
    ctx = _arm(fx)
    _dirty_record(fx)
    monkeypatch.setattr(finish.fr, "record", lambda *a, **k: pytest.fail("git steps ran"))
    monkeypatch.setattr(finish, "_preflight", lambda *a: pytest.fail("preflight ran"))
    before, state = origin_main(fx), _state(fx)
    lock = finish.state_path(ctx["root"], ctx["slug"]).with_name("finish.run.lock")
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        out = finish.finish(fx["plan"], timeout_s=0, apply=True)
    assert (out["action"], out["park_reason"]) == ("finish-parked", "finish-running"), out
    assert f"run.py finish {fx['plan']} --no-wait" in out["brief"], out["brief"]
    assert _state(fx) == state and origin_main(fx) == before and not calls
    monkeypatch.undo()
    assert finish.finish(fx["plan"], timeout_s=0)["action"] == "finished"   # lock released
