"""R6 §17.4: a running land renews its lease; a live holder is never taken over.

The first test is the purpose, end to end through the real ``run.py``; the rest
are the edge cases one rule each."""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import land_load as lld  # noqa: E402
import lease_guard as lg  # noqa: E402
import run_state_io as rsi  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import ack_only  # noqa: E402
from _land_fixture import build_repo, isolate, json_tail, origin_main, run_cli, work  # noqa: E402


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("event") == name]


def _backdate(lp, **over):
    with lg.mutex(lp):                                    # like every other lease writer
        d = json.loads(lp.read_text())
        d["expires_at"] = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
        d.update(over)
        lp.write_text(json.dumps(d))
    return d


LIVE = {"expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}


# --------------------------------------------------------------------------
# The purpose: a land whose gate outlasts its lease keeps it and pushes; a
# second plan's land is refused meanwhile.
# --------------------------------------------------------------------------
def test_a_land_whose_gate_outlasts_its_lease_keeps_it_and_pushes(tmp_path):
    fx = build_repo(tmp_path, slugs=("plan-a", "plan-b"), gates={"marker-gate": {
        "kind": "argv", "cwd": ".", "timeout": 60,
        "argv": ["/bin/sh", "-c", f"if [ -f '{tmp_path}/SLOW' ]; then touch '{tmp_path}/RUNNING'; "
                                  f"i=0; while [ ! -f '{tmp_path}/GO' ] && [ $i -lt 500 ]; do sleep 0.1; i=$((i+1)); "
                                  f"done; fi; test ! -f projects/p/memory/STOP"]}})
    a, b = isolate(fx, "plan-a"), isolate(fx, "plan-b")
    work(a["tree"], "src/f.py", "F = 1\n")
    work(b["tree"], "src/g.py", "G = 1\n")
    assert run_cli("land", a["plan_dir"])[1]["action"] == "land-awaits-review"
    assert ack_only(a["plan_dir"], "ok")[1]["action"] == "land-acked"
    # A harvest on main makes the re-gate run again, now slowly.
    from _land_fixture import main_side  # noqa: PLC0415
    moved = main_side(fx, "projects/p/memory/note.md", "harvested\n", "harvest: 1 live-state")
    (tmp_path / "SLOW").touch()
    holder = subprocess.Popen([sys.executable, str(SCRIPTS / "run.py"), "land", str(a["plan_dir"])],
                              stdout=subprocess.PIPE, text=True, cwd=str(SCRIPTS))
    deadline = time.time() + 120
    while not (tmp_path / "RUNNING").exists() and time.time() < deadline:
        time.sleep(0.1)
    assert (tmp_path / "RUNNING").exists(), "the slow gate never started"
    lp = sl._ship_lock_path(a["plan_dir"], f"git:{fx['root']}")
    before = _backdate(lp)                            # the lease lapses while the gate runs
    rc, out = run_cli("land", b["plan_dir"])          # plan B tries to take it
    assert out["action"] == "land-locked", out
    assert json.loads(lp.read_text())["token"] == before["token"]   # still plan A's
    assert _events(b["plan_dir"], "shipping_lease_takeover_refused")
    (tmp_path / "GO").touch()                         # the slow gate ends
    text, _ = holder.communicate(timeout=300)
    assert json_tail(text)["action"] == "landed"
    assert origin_main(fx) != moved                   # A pushed
    assert not lp.exists()                            # and released it


# --------------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path):
    root = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    a, b = root / "_plans" / "a", root / "_plans" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield {"a": str(a), "b": str(b), "res": f"git:{root}", "child": child, "root": str(root)}
    child.kill()
    child.wait()


def _take(w, kind="land", **over):
    """Plan A takes the lease in this process, then it is made to look as if ``child`` held it."""
    sl.acquire_ship_lock(w["a"], w["res"], guarded_timeout=1800, holder_kind=kind)
    lp = sl._ship_lock_path(w["a"], w["res"])
    if "pid" not in over:
        over = {"pid": w["child"].pid, "pid_start": lg.pid_start(w["child"].pid) if kind else None,
                **over}
    return lp, _backdate(lp, **over)


def test_renewal_extends_expiry_and_keeps_identity(world):
    sl.acquire_ship_lock(world["a"], world["res"], guarded_timeout=1800, holder_kind="land")
    lp = sl._ship_lock_path(world["a"], world["res"])
    old = _backdate(lp)                                   # expired, but we are the live holder
    assert lg.holds_lease(world["a"], world["res"])
    assert lg.renew(world["a"], world["res"]) is True
    new = json.loads(lp.read_text())
    assert new["expires_at"] > datetime.now(UTC).isoformat()
    assert (new["token"], new["pid"], new["pid_start"], new["renewals"]) == (
        old["token"], old["pid"], old["pid_start"], 1)


def test_a_foreign_token_and_another_process_cannot_renew(world):
    _take(world)                                          # child is the holder
    assert lg.renew(world["a"], world["res"]) is False    # same token, other process
    assert _events(world["a"], "shipping_lease_renew_refused")
    assert lg.renew(world["b"], world["res"]) is False    # plan B holds no record


@pytest.mark.parametrize("over, takes, cause", [
    ({}, False, None),                                    # live holder, same start
    ({"pid": 999999}, True, "pid-dead"),
    ({"pid_start": "Thu Jan  1 00:00:00 1970 UTC"}, True, "pid-reused"),
    ({"pid_start": "Thu Jan  1 00:00:00 1970"}, False, None),   # old local-time start: refused
    ({"host": "other-box.local", "machine_id": 1}, False, None),   # another host: never
])
def test_takeover_of_an_expired_land_lease(world, over, takes, cause):
    _take(world, **over)
    if takes:
        sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
        assert _events(world["b"], "shipping_lease_taken_over")[-1]["cause"] == cause
    else:
        with pytest.raises(rsi.LockError):
            sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
    refused = _events(world["b"], "shipping_lease_takeover_refused")
    assert bool(refused) is (not takes and "host" not in over)
    if refused:
        assert refused[-1]["why"] == ("live-holder" if not over else "legacy-live-pid")


def test_a_push_lease_keeps_todays_wall_clock_rule(world):
    _take(world, kind=None, lease_version=2)              # v2, no holder_kind: live pid, expired
    sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
    assert _events(world["b"], "shipping_lease_taken_over")[-1]["cause"] == "expired"


@pytest.mark.parametrize("alive", [True, False])
def test_a_legacy_lease_is_refused_only_while_its_pid_lives(world, alive):
    _take(world, kind=None, lease_version=None, pid=world["child"].pid if alive else 999999)
    if alive:
        with pytest.raises(rsi.LockError):
            sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
        assert _events(world["b"], "shipping_lease_takeover_refused")[-1]["why"] == "legacy-live-pid"
    else:
        sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)


def test_release_by_a_non_holder_keeps_the_file_and_the_record(world):
    lp, _ = _take(world)
    assert sl.release_ship_lock(world["a"], world["res"]) is False
    assert lp.exists() and sl.our_lease(world["a"], world["res"])
    assert _events(world["a"], "shipping_lock_not_ours")


def test_renewal_first_makes_the_waiting_takeover_refuse(world):
    sl.acquire_ship_lock(world["a"], world["res"], guarded_timeout=1800, holder_kind="land")
    lp = sl._ship_lock_path(world["a"], world["res"])
    _backdate(lp)
    result = {}

    def contend():
        try:
            sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
            result["r"] = "taken"
        except rsi.LockError:
            result["r"] = "refused"
    with lg.mutex(lp):                                    # the renewal holds the mutex
        t = threading.Thread(target=contend)
        t.start()
        time.sleep(0.5)
        assert t.is_alive()                               # the contender waits
        cur = sl._read_lock(lp)
        lg.renew_locked(world["a"], world["res"], lp, cur)
    t.join(10)
    assert result["r"] == "refused"


def test_takeover_first_makes_the_late_renewal_fail_and_leave_the_new_lease(world):
    lp, _ = _take(world, pid=999999, pid_start="x")       # dead holder, expired
    sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
    taken = json.loads(lp.read_text())
    assert lg.renew(world["a"], world["res"]) is False
    assert json.loads(lp.read_text()) == taken
    assert lg.holds_lease(world["a"], world["res"]) is False   # the push guard reads the file


# --------------------------------------------------------------------------
# Rework round 1: each test failed on the first build (evidence after.txt)
# --------------------------------------------------------------------------
def test_h1_a_contender_in_another_time_zone_does_not_take_a_live_holder(world, monkeypatch):
    holder = subprocess.Popen([sys.executable, "-c", (
        f"import sys, time; sys.path[:0] = [{str(SCRIPTS)!r}, "
        f"{str(SCRIPTS.parent.parent / 'plan-builder' / 'scripts')!r}]\n"
        "import ship_locks as sl\n"
        f"sl.acquire_ship_lock({world['a']!r}, {world['res']!r}, guarded_timeout=1800, "
        "holder_kind='land')\nprint('ready', flush=True)\ntime.sleep(120)")],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "TZ": "Asia/Tokyo"})
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        lp = sl._ship_lock_path(world["a"], world["res"])
        before = _backdate(lp)                            # expired; the holder still runs
        monkeypatch.setenv("TZ", "America/New_York")      # the contender reads in another TZ
        with pytest.raises(rsi.LockError):
            sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)
        assert json.loads(lp.read_text())["token"] == before["token"]
        assert _events(world["b"], "shipping_lease_takeover_refused")[-1]["why"] == "live-holder"
    finally:
        holder.kill()
        holder.wait()


def test_h2_an_unreadable_start_time_never_authorises_a_takeover(world, tmp_path, monkeypatch):
    lp, _ = _take(world)                                  # child holds it, expired
    before = lp.read_text()
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ps").write_text("#!/bin/sh\nexit 1\n")    # ps cannot answer (load, fork failure)
    (fake / "ps").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake}:{os.environ['PATH']}")
    for plan in ("b", "a"):                               # another plan; our plan, other process
        with pytest.raises(rsi.LockError):
            sl.acquire_ship_lock(world[plan], world["res"], guarded_timeout=1800)
        assert lp.read_text() == before
    assert _events(world["b"], "shipping_lease_takeover_refused")[-1]["why"] == "live-holder"


@pytest.mark.parametrize("expired", [True, False])
def test_h3_another_hosts_land_lease_is_never_taken_by_its_own_plan(world, expired):
    lp, _ = _take(world, pid=999999, host="other-box.local", machine_id=1,
                  **({} if expired else LIVE))            # pid 999999 is dead HERE, not there
    before = lp.read_text()
    with pytest.raises(rsi.LockError, match="ANOTHER machine"):
        sl.acquire_ship_lock(world["a"], world["res"], guarded_timeout=1800, holder_kind="land")
    assert lp.read_text() == before
    assert lg.renew(world["a"], world["res"]) is False
    assert sl.release_ship_lock(world["a"], world["res"]) is False and lp.read_text() == before


def test_h3_our_own_dead_land_holder_is_taken_over_and_recorded_not_adopted(world):
    lp, old = _take(world, pid=999999, **LIVE)            # unexpired, holder dead
    sl.acquire_ship_lock(world["a"], world["res"], guarded_timeout=1800)   # finish, same plan
    new = json.loads(lp.read_text())
    assert new["token"] != old["token"] and "holder_kind" not in new   # not a land lease
    assert new["taken_over_from"]["cause"] == "pid-dead"
    assert _events(world["a"], "shipping_lease_taken_over")[-1]["cause"] == "pid-dead"


def test_m1_a_lost_lease_parks_before_the_targeted_re_run(world, tmp_path):
    sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800)   # plan B holds it
    ran = tmp_path / "RAN"
    g = {"rerun_tests": {"argv": ["/bin/sh", "-c", f"touch '{ran}'"], "timeout": 30}}
    rec = {"outcome": "fail", "cwd": str(tmp_path),
           "excerpt": "FAILED tests/t.py::test_a - slow\n1 failed, 3 passed in 1.0s"}
    ctx = {"plan_dir": world["a"], "resource": world["res"], "root": world["root"], "slug": "a"}
    out = lld.retry(ctx, {}, "ci-gate", g, rec)
    assert (out.get("action"), out.get("kind")) == ("land-parked", "lease-lost-renew"), out
    assert not ran.exists()                               # the re-run never ran


def test_m2_the_push_guard_refuses_a_same_plan_process_that_is_not_the_holder(world):
    _take(world, **LIVE)                                  # live lease, our token, child holds it
    assert lg.holds_lease(world["a"], world["res"]) is False
    lp = sl._ship_lock_path(world["a"], world["res"])
    _backdate(lp, pid=os.getpid(), pid_start=lg.own_start())   # now WE are the holder, expired
    assert lg.holds_lease(world["a"], world["res"]) is True


def test_l_a_mutex_that_cannot_lock_raises_lock_error(tmp_path, monkeypatch):
    def no_flock(*_a):
        raise OSError(45, "Operation not supported")
    monkeypatch.setattr(lg.fcntl, "flock", no_flock)
    with pytest.raises(rsi.LockError, match="host mutex"), lg.mutex(tmp_path / "x.lock"):
        pass


def test_a_read_error_on_an_old_live_lease_keeps_its_file_and_token(world, monkeypatch):
    """s07 finding A: an OSError reading the lease is not an empty record to take over."""
    lp, before = _take(world, **LIVE)                     # child holds a live land lease
    os.utime(lp, (time.time() - 600, time.time() - 600))  # past the unreadable grace
    real = Path.read_bytes

    def flaky(self):
        if self == lp:
            raise OSError(5, "Input/output error")
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", flaky)
    with pytest.raises(rsi.LockError, match="cannot read"):
        sl.acquire_ship_lock(world["b"], world["res"], guarded_timeout=1800, holder_kind="land")
    assert lg.renew(world["a"], world["res"]) is False    # the holder check fails closed too
    monkeypatch.undo()
    assert json.loads(lp.read_text())["token"] == before["token"]
