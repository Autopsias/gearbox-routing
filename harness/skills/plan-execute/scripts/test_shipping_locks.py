"""Verification suite for the shipping LEASES (plan §Verification, LCK-01/02).

Split out of test_shipping.py, which owns the state machine. Everything here is
about exclusion: which file a resource locks, who owns it, and what happens when
that ownership lapses mid-directive.

  7   a live foreign holder blocks (serialization)
  7b  repo-scoped leases two plans in ONE checkout really contend on — driven
      through real run.py SUBPROCESSES, because an in-process fixture keeps a
      live pid and passes even where production does not (s03b measured that)
  7c  a lock taken on ANOTHER machine is reported, never reclaimed

Run: pytest plan-execute/scripts/test_shipping_locks.py -q
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import run_state_io as rsi  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import shipping as shp  # noqa: E402
from test_shipping import make_plan, write_closeout  # noqa: E402



def git_init(root):
    """A real git repo, so repo-scoped leases resolve to <root>/.git/plan-locks."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True,
                   capture_output=True)
    return root


def _plant_lease(plan_dir, resource, *, token, lease_seconds=3600, host=None, pid=1,
                 plan_dir_field="/somewhere/else"):
    """Write a lock file directly, as a foreign holder would. ``lease_seconds``
    may be negative to plant an already-EXPIRED lease."""
    import socket as _socket
    from datetime import UTC, datetime, timedelta
    lp = ssio._ship_lock_path(plan_dir, resource)
    lp.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    lp.write_text(json.dumps({
        "token": token, "resource": resource, "plan_dir": plan_dir_field,
        "host": host or _socket.gethostname(), "pid": pid,
        "started_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=lease_seconds)).isoformat(),
        "lease_seconds": lease_seconds, "lease_rationale": "planted by a test",
    }))
    return lp


def expire_lease(plan_dir, resource, *, seconds_ago=60):
    """Backdate a real lease's expiry — the mid-directive expiry the contract
    is written for, without sleeping through a whole lease."""
    from datetime import UTC, datetime, timedelta
    lp = ssio._ship_lock_path(plan_dir, resource)
    info = json.loads(lp.read_text())
    info["expires_at"] = (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()
    lp.write_text(json.dumps(info))
    return lp


def run_py(*argv):
    """Invoke run.py as a REAL separate process (the production shape: it exits
    before the skill it authorises ever runs)."""
    proc = subprocess.run([sys.executable, str(SCRIPTS / "run.py"), *[str(a) for a in argv]],
                          capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:  # pragma: no cover - surfaced only on failure
        raise AssertionError(f"run.py {argv} produced no JSON\n"
                             f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}") from None


# --------------------------------------------------------------------------
# 7 — resource lock blocks a foreign holder (serialization)
# --------------------------------------------------------------------------
def test_resource_lock_blocks_foreign_holder(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit-push"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    # Pre-plant a FOREIGN, live lease on the git resource — a lease held by a
    # process that never existed here, whose expiry is still in the future.
    root = shp.find_project_root(plan_dir)
    lp = _plant_lease(plan_dir, f"git:{root}", token="foreign-token", lease_seconds=3600)
    assert lp.exists()
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "locked"


# --------------------------------------------------------------------------
# 7b — LCK-01/LCK-02: repo-scoped LEASES that two plans in one checkout really
# contend on. Every contention assertion below runs the production shape: real
# `run.py` SUBPROCESSES that EXIT before the skill they authorise would run
# (s03b measured that an in-process fixture keeps a live pid and passes even
# where production does not — the known-positive trap that hid this defect).
# --------------------------------------------------------------------------
COMMIT_ONLY = {"git": "commit"}


def _two_plans(tmp_path):
    """One git repo, two plans, both ready to ship a commit."""
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"],
                 "prompt": "do", "post_session": dict(COMMIT_ONLY)}]
    plan_a = make_plan(tmp_path, sessions, name="plan-a")
    plan_b = make_plan(tmp_path, sessions, name="plan-b")
    git_init(shp.find_project_root(plan_a))
    write_closeout(plan_a, "s01")
    write_closeout(plan_b, "s01")
    return plan_a, plan_b, shp.find_project_root(plan_a)


def test_repo_scoped_lease_file_lives_in_the_git_dir_not_the_plan_dir(tmp_path):
    plan_a, plan_b, root = _two_plans(tmp_path)
    git_lock = ssio._ship_lock_path(plan_a, f"git:{root}")
    assert git_lock.parent == root / ".git" / "plan-locks"
    # ...and BOTH plans resolve the identical file (s03b: "same file? False").
    assert git_lock == ssio._ship_lock_path(plan_b, f"git:{root}")
    # deploy:<target> names no root, yet both plans must still resolve ONE file:
    # they deploy the same container (2026-09-19, two plans, one fastapi).
    deploy_lock = ssio._ship_lock_path(plan_a, "deploy:test-ec2")
    assert deploy_lock.parent == root / ".git" / "plan-locks"
    assert deploy_lock == ssio._ship_lock_path(plan_b, "deploy:test-ec2")
    assert deploy_lock != ssio._ship_lock_path(plan_a, "deploy:other-target")
    # Gates stay plan-local.
    assert ssio._ship_lock_path(plan_a, "gate:smoke").parent == plan_a / "_shipping_locks"

    out = run_py("ship-begin", plan_a, "--session", "s01")
    assert out["action"] == "invoke-skill"
    assert git_lock.exists(), "the git lease must be written into the repo's git dir"
    assert not (plan_a / "_shipping_locks").exists(), "no repo lease under the plan dir"


def test_second_plan_blocks_on_first_plans_git_lease_across_processes(tmp_path):
    plan_a, plan_b, root = _two_plans(tmp_path)

    a = run_py("ship-begin", plan_a, "--session", "s01")
    assert a["action"] == "invoke-skill" and a["step"] == "commit"
    assert a["lease_token"], "the directive must carry the lease token"
    # run.py has EXITED — the pid on that lease is dead RIGHT NOW, which is
    # exactly the state that used to make every contender reclaim it.
    holder = json.loads(ssio._ship_lock_path(plan_a, f"git:{root}").read_text())
    assert rsi._pid_alive(holder["pid"]) is False
    assert holder["token"] == a["lease_token"]
    assert holder["lease_seconds"] >= 1800 and holder["lease_rationale"]

    b = run_py("ship-begin", plan_b, "--session", "s01")
    assert b["action"] == "locked", f"plan B was not excluded: {b}"
    assert f"git:{root}" in b["reason"]

    # NEUTER-ONCE: release A's lease and B must proceed — proof this check can
    # fail, rather than being a `locked` that would come back whatever we did.
    assert run_py("ship-release", plan_a)["action"] == "released"
    b2 = run_py("ship-begin", plan_b, "--session", "s01")
    assert b2["action"] == "invoke-skill", f"B still blocked after release: {b2}"


_BARRIER_WORKER = """
import json, os, sys, time
sys.path.insert(0, {scripts!r})
import run_state_io as rsi, ship_state_io as ssio
plan_dir, resource, release_at = sys.argv[1], sys.argv[2], float(sys.argv[3])
while time.time() < release_at:
    time.sleep(0.001)
try:
    ssio.acquire_ship_lock(plan_dir, resource, guarded_timeout=600)
    out = {{"result": "ACQUIRED", "error": ""}}
except rsi.LockError as e:
    out = {{"result": "EXCLUDED", "error": str(e)}}
print(json.dumps({{"pid": os.getpid(), **out}}))
"""


def test_simultaneous_acquirers_produce_exactly_one_winner(tmp_path):
    """s03b measured 4 of 4 contenders 'acquiring' the same check-then-write
    lock, three trials of three. With an O_CREAT|O_EXCL lease: exactly one."""
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"],
                 "prompt": "do", "post_session": dict(COMMIT_ONLY)}]
    plans = [make_plan(tmp_path, sessions, name=f"plan-{i}") for i in range(4)]
    root = git_init(shp.find_project_root(plans[0]))
    worker = tmp_path / "contend.py"
    worker.write_text(_BARRIER_WORKER.format(scripts=str(SCRIPTS)))

    release_at = time.time() + 1.5
    procs = [subprocess.Popen(
        [sys.executable, str(worker), str(p), f"git:{root}", str(release_at)],
        stdout=subprocess.PIPE, text=True) for p in plans]
    results = [json.loads(p.communicate()[0]) for p in procs]

    winners = [r for r in results if r["result"] == "ACQUIRED"]
    assert len(winners) == 1, f"expected exactly one winner, got {results}"
    assert all("is leased" in r["error"] for r in results if r["result"] == "EXCLUDED")


def test_lease_that_expires_mid_directive_makes_ship_record_refuse(tmp_path):
    """The case the expiry rule exists for: A's lease expires while
    /commit-orchestrate is still running, B takes the repo over, and A must
    NEVER complete against a lease it no longer holds."""
    plan_a, plan_b, root = _two_plans(tmp_path)
    resource = f"git:{root}"

    a = run_py("ship-begin", plan_a, "--session", "s01")
    assert a["action"] == "invoke-skill"
    a_token = a["lease_token"]

    expire_lease(plan_a, resource)                       # ...directive still in flight
    b = run_py("ship-begin", plan_b, "--session", "s01")
    assert b["action"] == "invoke-skill", f"B should take over an expired lease: {b}"

    # The takeover is RECORDED, on both sides — never a silent transfer.
    holder = json.loads(ssio._ship_lock_path(plan_b, resource).read_text())
    assert holder["taken_over_from"]["token"] == a_token
    assert holder["token"] == b["lease_token"] != a_token
    b_events = [json.loads(x) for x in (plan_b / "run.ndjson").read_text().splitlines()]
    a_events = [json.loads(x) for x in (plan_a / "run.ndjson").read_text().splitlines()]
    assert any(e["event"] == "shipping_lease_taken_over" for e in b_events)
    assert any(e["event"] == "shipping_lease_lost" for e in a_events)

    # A comes back to record its commit: REFUSED and parked, with a brief.
    rec = run_py("ship-record", plan_a, "--session", "s01", "--step", "commit",
                 "--status", "done", "--lease-token", a_token)
    assert rec["action"] == "failed" and rec["reason"] == "lease-lost"
    assert rec["lease_status"] == "taken-over"
    assert rec["decision_brief"]["options"] and rec["decision_brief"]["recommendation"]
    # Nothing was marked shipped, and the plan is halted for a human.
    state = ssio.load_ship_state(plan_a, "s01")
    assert state["steps"]["commit"] == "running"
    assert rsi.is_halted(plan_a)


def test_ship_record_refuses_when_our_lease_merely_expired(tmp_path):
    """No contender needed: an expired lease is not ours to complete against."""
    plan_a, _plan_b, root = _two_plans(tmp_path)
    a = run_py("ship-begin", plan_a, "--session", "s01")
    expire_lease(plan_a, f"git:{root}")
    rec = run_py("ship-record", plan_a, "--session", "s01", "--step", "commit",
                 "--status", "done", "--lease-token", a["lease_token"])
    assert rec["reason"] == "lease-lost" and rec["lease_status"] == "expired"
    assert ssio.load_ship_state(plan_a, "s01")["steps"]["commit"] == "running"


def test_ship_record_completes_normally_while_the_lease_is_held(tmp_path):
    """Neuter-once for the refusal itself: with the lease intact the SAME call
    records the step, so `lease-lost` is a real judgement, not a constant."""
    plan_a, _plan_b, _root = _two_plans(tmp_path)
    a = run_py("ship-begin", plan_a, "--session", "s01")
    rec = run_py("ship-record", plan_a, "--session", "s01", "--step", "commit",
                 "--status", "done", "--lease-token", a["lease_token"])
    assert rec["action"] == "done", rec
    assert ssio.load_ship_state(plan_a, "s01")["steps"]["commit"] == "done"


# --------------------------------------------------------------------------
# 7c — LCK-02: a lock taken on ANOTHER machine is reported, never reclaimed.
# --------------------------------------------------------------------------
def test_foreign_host_lease_is_reported_not_reclaimed(tmp_path):
    plan_a, _plan_b, root = _two_plans(tmp_path)
    # Long expired, and holding a pid that is certainly dead on this machine —
    # everything the old rule needed to call it stale. It is on another host.
    _plant_lease(plan_a, f"git:{root}", token="tok-elsewhere", lease_seconds=-99999,
                 host="some-other-machine", pid=999999)
    out = run_py("ship-begin", plan_a, "--session", "s01")
    assert out["action"] == "locked"
    assert "some-other-machine" in out["reason"]
    assert "ANOTHER machine" in out["reason"]


def test_foreign_host_run_lock_is_never_stale(tmp_path):
    """The same rule in run_state_io's dispatch pidfile."""
    import socket
    from datetime import UTC, datetime, timedelta

    import run_state_io as rsi
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    old = (datetime.now(UTC) - timedelta(seconds=rsi.STALE_LOCK_SECONDS * 3)).isoformat()
    (plan_dir / ".lock").write_text(json.dumps(
        {"pid": 999999, "started_at": old, "host": "some-other-machine"}))
    with pytest.raises(rsi.LockError) as e:
        rsi.acquire_lock(plan_dir)
    assert "some-other-machine" in str(e.value)
    assert rsi.lock_holder(plan_dir) is not None      # reported, not treated as free
    assert rsi.lock_stale({"pid": 999999, "started_at": old, "host": "some-other-machine"}) is False
    # Neuter-once: the identical record on THIS host is stale and reclaimable.
    same_host = {"pid": 999999, "started_at": old, "host": socket.gethostname()}
    assert rsi.lock_stale(same_host) is True




# --------------------------------------------------------------------------
# 7d — THE MID-WRITE WINDOW. `O_EXCL` publishes the lock path EMPTY and the
# JSON lands after it. A contender reading that window classed the lease
# `unreadable`, stole it, and BOTH plans held the same repo resource. Found by
# review 2026-08-21 and reproduced; the four-contender barrier test above misses
# it because three trials rarely land inside a sub-millisecond window — a
# known-positive that could not fail for the case that mattered.
# --------------------------------------------------------------------------
def test_an_empty_lock_file_is_a_writer_not_a_corpse(tmp_path):
    """The exact reproduction: the lock path exists and is EMPTY. A contender
    must REFUSE, never steal it. Neuter `_unreadable_is_young` (or revert the
    `os.link` publish) and this test fails with the second plan acquiring."""
    import ship_locks as sl
    root = git_init(tmp_path / "proj")
    plan_dir = root / "_plans" / "p-a"
    plan_dir.mkdir(parents=True)
    resource = f"git:{root}"

    lp = sl._ship_lock_path(plan_dir, resource)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text("")                      # the mid-write window, exactly
    assert lp.exists() and lp.read_text() == ""

    with pytest.raises(rsi.LockError) as exc:
        sl.acquire_ship_lock(plan_dir, resource)
    assert "no readable lease" in str(exc.value)
    assert lp.exists(), "the writer's file must survive; stealing it is the bug"


def test_the_lock_path_is_never_published_empty(tmp_path):
    """The root fix: content is linked into place, so the path never appears
    without its JSON. Proven by reading every byte back immediately."""
    import ship_locks as sl
    root = git_init(tmp_path / "proj")
    plan_dir = root / "_plans" / "p-a"
    plan_dir.mkdir(parents=True)
    resource = f"git:{root}"

    assert sl.acquire_ship_lock(plan_dir, resource) is True
    lp = sl._ship_lock_path(plan_dir, resource)
    body = json.loads(lp.read_text())      # never empty, never partial
    assert body["token"] and body["expires_at"]
    # and no temp file was left behind
    assert not list(lp.parent.glob(f"{lp.name}.new-*"))


# --------------------------------------------------------------------------
# 7e — the two defects the FIX introduced, found by review 2026-08-21.
# A fix is a change, and a change gets reviewed like any other.
# --------------------------------------------------------------------------
def test_a_future_mtime_does_not_lock_the_resource_forever(tmp_path):
    """`_unreadable_is_young` had no LOWER bound: a lock file whose mtime is in
    the future yields a NEGATIVE age, which is < the grace, so it read as a live
    writer forever — `_steal_expired` raised on every attempt and the message
    told the operator not to remove it. A future mtime is a clock anomaly, never
    a writer. Neuter the `0 <=` and this test hangs the resource permanently."""
    import os as _os
    import ship_locks as sl
    root = git_init(tmp_path / "proj")
    plan_dir = root / "_plans" / "p-a"
    plan_dir.mkdir(parents=True)
    resource = f"git:{root}"

    lp = sl._ship_lock_path(plan_dir, resource)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text("")                                   # unreadable
    future = time.time() + 86400                        # mtime one day AHEAD
    _os.utime(lp, (future, future))

    assert sl._unreadable_is_young(lp) is False, "a future mtime is not a writer"
    # and the resource is therefore acquirable again rather than wedged for good
    assert sl.acquire_ship_lock(plan_dir, resource) is True


def test_a_filesystem_without_hard_links_raises_LockError_not_OSError(tmp_path,
                                                                     monkeypatch):
    """`_create_with_content` caught only FileExistsError from `os.link`. Any
    OTHER OSError — no hard-link support on exFAT/FAT32 or an SMB/FUSE mount,
    which is exactly the shared-checkout case this module targets — escaped
    `ship_begin`'s `except rsi.LockError` and stranded an already-taken
    repo-wide lease for the whole lease duration.

    monkeypatch, not a file copy: the module is already in sys.modules, so a
    copied tree would never execute (a passing neuter is a broken probe)."""
    import ship_locks as sl
    root = git_init(tmp_path / "proj")
    plan_dir = root / "_plans" / "p-a"
    plan_dir.mkdir(parents=True)

    def _no_hardlinks(src, dst):
        raise OSError(1, "Operation not permitted")
    monkeypatch.setattr(sl.os, "link", _no_hardlinks)

    with pytest.raises(rsi.LockError) as exc:
        sl.acquire_ship_lock(plan_dir, f"git:{root}")
    assert "hard links" in str(exc.value)
    # and no debris is left behind for the next contender to misread
    lp = sl._ship_lock_path(plan_dir, f"git:{root}")
    assert not lp.exists()
    assert not list(lp.parent.glob(f"{lp.name}.new-*"))


def test_an_OSError_anywhere_in_the_publish_raises_LockError(tmp_path, monkeypatch):
    """The FIRST fix guarded `os.link` alone — the call the review named — and
    left `os.open`, the write and the fsync of the temp file bare. An ENOSPC
    there escaped `ship_begin`'s `except rsi.LockError` exactly as the link
    failure had, and stranded the repo-wide lease just the same. One guard at
    the BOUNDARY, not one per call site; a per-call-site guard is how the first
    sibling was missed. Reproduced by review 2026-08-21."""
    import ship_locks as sl
    root = git_init(tmp_path / "proj")
    plan_dir = root / "_plans" / "p-a"
    plan_dir.mkdir(parents=True)
    real_open = sl.os.open

    def _enospc(path, *a, **k):
        if ".new-" in str(path):                 # only the temp file
            raise OSError(28, "No space left on device")
        return real_open(path, *a, **k)
    monkeypatch.setattr(sl.os, "open", _enospc)

    with pytest.raises(rsi.LockError) as exc:    # NOT a raw OSError
        sl.acquire_ship_lock(plan_dir, f"git:{root}")
    assert "cannot publish the lease file" in str(exc.value)


def test_lock_on_this_host_survives_hostname_flip_and_machine_id():
    import socket as _s
    import uuid as _uuid
    flipped = _s.gethostname().split(".")[0].upper() + ".lan"
    assert rsi.lock_on_this_host({"host": flipped})
    assert rsi.lock_on_this_host({"host": "other-box.local", "machine_id": _uuid.getnode()})
    assert not rsi.lock_on_this_host({"host": "other-box.local", "machine_id": 1})
