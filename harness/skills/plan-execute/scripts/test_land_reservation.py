"""R6 §17.2 — an owner stop leaves a reservation, never a lease.

    pytest plan-execute/scripts/test_land_reservation.py -q
"""

import itertools
import json
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land  # noqa: E402
import land_reservation as lr  # noqa: E402
import lease_guard as lg  # noqa: E402
import plan_worktree as pwt  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import build_repo, isolate, work  # noqa: E402


@pytest.fixture
def fx2(tmp_path):
    fx = build_repo(tmp_path, slugs=("plan-a", "plan-b"))
    a, b = isolate(fx, "plan-a"), isolate(fx, "plan-b")
    work(a["tree"], "src/fa.py", "A = 1\n")
    work(b["tree"], "src/fb.py", "B = 1\n")
    return fx, a["plan_dir"], b["plan_dir"], f"git:{fx['root'].resolve()}"


def _age(plan_dir, res, seconds):
    rp = lr._path(plan_dir, res)
    rec = json.loads(rp.read_text())
    rec["expires_at"] = (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()
    rp.write_text(json.dumps(rec))


@pytest.mark.parametrize("stop,window,who", list(itertools.product(
    ["invoke-skill", "gate-on-box-verification", "land-awaits-review", None],
    ["inside", "after-expiry"], ["same", "other"])))
def test_matrix_owner_stop_vs_real_park(fx2, stop, window, who):
    _fx, a, b, res = fx2
    sl.acquire_ship_lock(a, res, guarded_timeout=60, holder_kind=lg.LAND)
    lr.release(a, res, stop)
    assert not sl._ship_lock_path(a, res).exists()           # the lease is always released
    assert lr._path(a, res).exists() == (stop is not None)   # a real park holds nothing
    if stop and window == "after-expiry":
        _age(a, res, -5)
    asker = a if who == "same" else b
    if stop and window == "inside" and who == "other":
        with pytest.raises(lr.Reserved) as e:
            lr.acquire(asker, res, guarded_timeout=60, holder_kind=lg.LAND)
        park = lr.parked(e.value.rec)
        assert (park["kind"], park["reserved_by"], park["reason"]) == (
            "land-reserved", "plan-a", stop)
        assert park["retry_after_seconds"] > 3000 and park["expires_at"]
        assert "plan-a" in park["brief"] and stop in park["brief"]
        assert not sl._ship_lock_path(b, res).exists()      # took nothing
    else:
        lr.acquire(asker, res, guarded_timeout=60, holder_kind=lg.LAND)
        assert sl._ship_lock_path(asker, res).exists()


def test_own_land_ends_the_reservation_and_expiry_is_logged(fx2):
    _fx, a, _b, res = fx2
    sl.acquire_ship_lock(a, res, guarded_timeout=60, holder_kind=lg.LAND)
    lr.release(a, res, "land-awaits-review")
    lr.acquire(a, res, guarded_timeout=60, holder_kind=lg.LAND)
    assert not lr._path(a, res).exists()
    assert '"why": "own-land"' in (Path(a) / "run.ndjson").read_text()


def test_crash_in_the_handoff_is_safe_either_way(fx2):
    _fx, a, b, res = fx2
    # reservation without a lease: it blocks b, then expires on its own
    lr.reserve(a, res, "land-awaits-review")
    with pytest.raises(lr.Reserved):
        lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
    _age(a, res, -1)
    lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
    assert '"why": "expired"' in (Path(a) / "run.ndjson").read_text()
    sl.release_ship_lock(b, res)
    # lease without a reservation: today's rule, a plain lock refusal and not Reserved
    sl.acquire_ship_lock(a, res, guarded_timeout=60, holder_kind=lg.LAND)
    with pytest.raises(rsi.LockError) as e:
        lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
    assert not isinstance(e.value, lr.Reserved)


def test_an_unreadable_reservation_is_ignored(fx2):
    _fx, a, b, res = fx2
    rp = lr._path(a, res)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("{not json")
    lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
    assert "land_reservation_ignored" in (Path(b) / "run.ndjson").read_text()


def test_only_lands_lease_acquisition_is_blocked(fx2):
    fx, a, b, res = fx2
    lr.reserve(a, res, "invoke-skill")
    # worktree setup, ship-begin, push: and deploy: leases take the lease directly
    pwt.ensure_plan_worktree(b, project_root=fx["root"])
    sl.acquire_ship_lock(b, res, guarded_timeout=60)
    sl.release_ship_lock(b, res)
    with pytest.raises(lr.Reserved):
        lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)


def test_barrier_b_cannot_slip_between_a_reservation_and_its_release(fx2):
    _fx, a, b, res = fx2
    sl.acquire_ship_lock(a, res, guarded_timeout=60, holder_kind=lg.LAND)
    paused, go, got = threading.Event(), threading.Event(), {}
    real = sl.release_ship_lock

    def slow_release(*args, **kw):
        paused.set()
        assert go.wait(20)
        return real(*args, **kw)

    def b_lands():
        try:
            lr.acquire(b, res, guarded_timeout=60, holder_kind=lg.LAND)
            got["b"] = "acquired"
        except lr.Reserved:
            got["b"] = "land-reserved"

    setattr(sl, "release_ship_lock", slow_release)
    try:
        ta = threading.Thread(target=lr.release, args=(a, res, "land-awaits-review"))
        ta.start()
        assert paused.wait(20)           # A: reservation written, lease not yet released
        tb = threading.Thread(target=b_lands)
        tb.start()
        tb.join(1.0)
        assert tb.is_alive()             # B waits on the mutex, it cannot slip in
        go.set()
        ta.join(20)
        tb.join(20)
    finally:
        sl.release_ship_lock = real
    assert got["b"] == "land-reserved"


def test_real_two_plan_land_b_parks_then_lands_after_a_answers(fx2):
    _fx, a, b, res = fx2
    out = land.land(a)
    assert out["action"] == "land-awaits-review"
    assert not sl._ship_lock_path(a, res).exists()                # lease released
    assert json.loads(lr._path(a, res).read_text())["reason"] == "land-awaits-review"
    parked = land.land(b)                                         # B takes nothing
    assert (parked["action"], parked["kind"], parked["reserved_by"]) == (
        "land-parked", "land-reserved", "plan-a")
    assert not sl._ship_lock_path(b, res).exists()
    _age(a, res, 300)       # R6: the answer ends it at any minute, there is no refresh
    assert land.land_ack(a, "ok")["action"] == "land-acked"
    assert lr._path(a, res).exists()                 # the answer leaves the reservation alone
    assert land.land(b)["kind"] == "land-reserved"   # B still parks after A's answer
    assert land.land(a)["action"] == "landed"
    assert land.land(b)["action"] == "land-awaits-review"         # B lands after A


def test_repeated_owner_stop_keeps_expires_at_and_token(fx2):
    _fx, a, _b, res = fx2
    first = lr.reserve(a, res, "land-awaits-review")
    _age(a, res, 600)
    kept = json.loads(lr._path(a, res).read_text())
    again = lr.reserve(a, res, "invoke-skill")
    after = json.loads(lr._path(a, res).read_text())
    assert again["expires_at"] == after["expires_at"] == kept["expires_at"] != first["expires_at"]
    assert after["token"] == first["token"]


def test_retry_fields_for_an_unreadable_lease_fall_back_to_a_minute(fx2, monkeypatch):
    _fx, a, _b, res = fx2

    def unreadable(_lp):
        raise rsi.LockError("cannot read the lease file")

    monkeypatch.setattr(sl, "_read_lock", unreadable)
    assert lr.retry_fields(a, res) == {"expires_at": None, "retry_after_seconds": 60}


def _set_origin(a, url):
    subprocess.run(["git", "-C", a, "remote", "set-url", "origin", url], check=True)


@pytest.mark.parametrize("url", ["https://github.com/Own-er/re.po.git", "git@github.com:Own-er/re.po.git"])
def test_take_lease_publishes_the_pointer_for_https_and_ssh(fx2, tmp_path, monkeypatch, url):
    _fx, a, _b, res = fx2
    monkeypatch.setenv("LAND_HOLD_DIR", str(tmp_path / "holds"))
    _set_origin(a, url)
    assert land._take_lease(a, {"resource": res}) is None
    pf = tmp_path / "holds" / "Own-er__re.po.json"
    assert [p.name for p in pf.parent.iterdir()] == [pf.name]
    rec = json.loads(pf.read_text())
    assert sorted(rec) == ["hold_version", "locks_dir", "repo", "resource", "written_at"]
    assert (rec["hold_version"], rec["repo"], rec["resource"]) == (1, "Own-er/re.po", res)
    assert rec["locks_dir"] == str(sl._ship_lock_path(a, res).parent.resolve())
    assert (Path(rec["locks_dir"]) / sl._ship_lock_path(a, res).name).exists()
    before = pf.stat().st_mtime_ns
    lr.publish_pointer(a, res)                    # same content: untouched
    assert pf.stat().st_mtime_ns == before


@pytest.mark.parametrize("broken", ["no-origin", "unwritable"])
def test_a_failing_pointer_never_fails_the_land(fx2, tmp_path, monkeypatch, broken):
    _fx, a, _b, res = fx2
    hold = tmp_path / "holds"
    if broken == "unwritable":
        hold.write_text("a file, not a folder")
        _set_origin(a, "https://github.com/o/r.git")
    else:
        subprocess.run(["git", "-C", a, "remote", "remove", "origin"], check=True)
    monkeypatch.setenv("LAND_HOLD_DIR", str(hold))
    assert land._take_lease(a, {"resource": res}) is None
    assert "land_pointer_failed" in (Path(a) / "run.ndjson").read_text()
