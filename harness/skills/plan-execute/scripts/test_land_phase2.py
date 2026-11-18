"""R7 §18.1, §18.4, §18.6, §18.11 — the phase-2 controller (land_phase2.py).

    pytest plan-execute/scripts/test_land_phase2.py -q

Every land case runs the public `land` command (`land.CLI["land"]`) against a real bare
origin. A competing push is real: a pre-push hook advances origin/main from a third
clone inside our push window. The same hook copies the marker and the lease as they
are at that moment, so "the marker was there while we pushed" is read, not assumed.
Every GIT_* variable is cleared before the fixture runs git; a decoy repository named
in them proves it.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land  # noqa: E402
import land_gate  # noqa: E402
import land_phase2 as lp2  # noqa: E402
import land_push as steps  # noqa: E402
import land_state as lst  # noqa: E402
import land_writers as lw  # noqa: E402
import ship_locks as sl  # noqa: E402
from _land_fixture import ack_only, build_repo, isolate, origin_main, work  # noqa: E402
from worktree import git  # noqa: E402

REVIEW = {"kind": "argv", "cwd": ".", "timeout": 60,
          "argv": ["/bin/sh", "-c", "true", "llm_review_gate.py"]}   # $0: a review gate


def _scrub_git_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(k)


def _events(plan_dir, name):
    p = Path(plan_dir) / "run.ndjson"
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
    return [r for r in rows if r.get("event") == name]


def _markers(home):
    d = Path(home) / ".claude" / lw.MARKER_DIRNAME
    return sorted(p.name for p in d.glob("*.json")) if d.exists() else []


def _gate(script):
    return {"kind": "argv", "cwd": ".", "timeout": 120, "argv": ["/bin/sh", "-c", script]}


def _setup(tmp_path, monkeypatch, mode, gates=None, plan_gates=("marker-gate", "review-gate"),
           plan_work=(("src/f.py", "F = 1\n"),)):
    """An acked land, ready for phase 2: the first `land` (no ack, phase 1) parked at
    review, and the owner's ack is recorded without the land it would start."""
    _scrub_git_env(monkeypatch)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(lp2, "PHASE2_MODE", mode)
    fx = build_repo(tmp_path, gates={"review-gate": REVIEW, **(gates or {})},
                    plan_gates=plan_gates)
    iso = isolate(fx, "plan-a")
    for rel, text in plan_work:
        work(iso["tree"], rel, text)
    assert land.CLI["land"](iso["plan_dir"], None)["action"] == "land-awaits-review"
    assert not _events(iso["plan_dir"], "land_phase2")       # §18.1 inv. 1: no ack, no window
    assert ack_only(iso["plan_dir"], "ok")[1]["action"] == "land-acked"
    fx.update(home=home, resource=f"git:{fx['root'].resolve()}", cap=tmp_path / "cap")
    fx["cap"].mkdir()
    return fx, iso


def _hook(fx, iso, *, races=0, race_dir="projects/race/memory", refuse=False, hang=False,
          extra=""):
    """pre-push: snapshot marker, lease and token; then refuse, hang once, or race."""
    lease = sl._ship_lock_path(iso["plan_dir"], fx["resource"])
    mdir = Path(fx["home"]) / ".claude" / lw.MARKER_DIRNAME
    hang_flag = fx["cap"] / "HANG"
    if hang:
        hang_flag.touch()
    hook = Path(fx["root"]) / ".git" / "hooks" / "pre-push"
    hook.write_text(f"""#!/bin/sh
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_PREFIX
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_QUARANTINE_PATH
C="{fx['cap']}"
n=$(cat "$C/count" 2>/dev/null || echo 0)
echo $((n + 1)) > "$C/count"
mkdir -p "$C/push-$n"
cp "{mdir}"/*.json "$C/push-$n/" 2>/dev/null
cp "{lease}" "$C/push-$n/lease.json" 2>/dev/null
printf %s "$PLAN_LAND_PHASE_TOKEN" > "$C/push-$n/token"
{extra}
{"exit 1" if refuse else ""}
if [ -f "{hang_flag}" ]; then rm "{hang_flag}"; exec sleep 20 >/dev/null 2>&1 </dev/null; fi
[ "$n" -ge {races} ] && exit 0
d=$(mktemp -d)
git clone -q "{fx['origin']}" "$d/c" || exit 0
cd "$d/c" || exit 0
git config user.email r@example.com && git config user.name R
mkdir -p "./{race_dir}" && echo "race $n" > "./{race_dir}/raced-$n.txt" && git add -A && git commit -q -m "race $n"
git push -q origin HEAD:main
exit 0
""")
    hook.chmod(0o755)


def _snap(fx, n=0):
    d = fx["cap"] / f"push-{n}"
    markers = [json.loads(p.read_text()) for p in d.glob("*.json") if p.name != "lease.json"]
    lease = json.loads((d / "lease.json").read_text())
    return markers, lease, (d / "token").read_text()


def _window(iso):
    rows = _events(iso["plan_dir"], "land_phase2")
    assert len(rows) == 1, rows                                 # ONE event per window
    return rows[0]


def _pushed(fx):
    """Is the plan's work on origin/main?"""
    return git(["cat-file", "-e", "refs/heads/main:src/f.py"], fx["origin"])[0] == 0


def _land(iso):
    return land.CLI["land"](iso["plan_dir"], None)


# --------------------------------------------------------------------------
# The marker: written for the push, gone after every exit (§18.6)
# --------------------------------------------------------------------------
def test_on_pushes_with_the_marker_set_and_removes_it(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso)
    out = _land(iso)
    assert out["action"] == "landed", out
    (marker,), lease, token = _snap(fx)
    assert marker["token"] == lease["token"] == token           # our push never waits on us
    assert lease["phase"] == "push" and lease["phase_expires_at"] == marker["expires_at"]
    assert marker["remote_hash"] == lw.remote_hash(str(fx["origin"]))
    span = (datetime.fromisoformat(marker["expires_at"])
            - datetime.fromisoformat(marker["started_at"])).total_seconds()
    assert span == lp2.DEADLINE_S + lp2.MARKER_GRACE_S          # a crash leaves it expired soon
    assert _markers(fx["home"]) == [] and lw.TOKEN_ENV not in os.environ
    ev = _window(iso)
    assert (ev["mode"], ev["outcome"], ev["attempts"]) == ("on", "pushed", 1)
    assert "phase" not in lst.load(iso["plan_dir"]) and "phase2" not in lst.load(iso["plan_dir"])


def test_marker_is_gone_after_a_park(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso, refuse=True)                                 # not stale: today's park
    out = _land(iso)
    assert (out["action"], out["kind"]) == ("land-parked", "push-rejected"), out
    assert len(_snap(fx)[0]) == 1 and _markers(fx["home"]) == []
    ev = _window(iso)
    assert (ev["outcome"], ev["reason"]) == ("parked", "push-rejected")
    assert lst.load(iso["plan_dir"])["ack"]                      # a park keeps the ack


def test_marker_is_gone_after_an_exception(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    seen = {}

    def boom(plan_dir, ctx, st):
        seen["markers"] = _markers(fx["home"])
        raise RuntimeError("crash inside the window")

    monkeypatch.setattr(steps, "STEPS", (*steps.STEPS[:-2], boom, *steps.STEPS[-2:]))
    with pytest.raises(RuntimeError):
        _land(iso)
    assert len(seen["markers"]) == 1 and _markers(fx["home"]) == []
    assert not sl._ship_lock_path(iso["plan_dir"], fx["resource"]).exists()   # released
    assert (_window(iso)["outcome"], _window(iso)["reason"]) == ("parked", "error")


def test_a_failed_lease_write_leaves_no_marker(tmp_path, monkeypatch):
    """§18.6: the marker lands, the lease write fails — both are clear before the error
    leaves `_mark`, and after the land."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    real_write, real_mark, calls, seen = lp2.rsi._atomic_write, lp2._mark, [], {}
    lease = sl._ship_lock_path(iso["plan_dir"], fx["resource"])

    def write(path, text):
        if seen.get("armed"):
            calls.append(path)
        if len(calls) == 2 and path == lease:            # marker written, lease write fails
            raise OSError("disk full on the lease write")
        real_write(path, text)

    def mark(*a):
        seen["armed"] = True
        try:
            return real_mark(*a)
        except OSError:
            seen.update(markers=_markers(fx["home"]), lease=json.loads(lease.read_text()))
            raise

    monkeypatch.setattr(lp2.rsi, "_atomic_write", write)
    monkeypatch.setattr(lp2, "_mark", mark)
    with pytest.raises(OSError, match="disk full"):
        _land(iso)
    assert seen["markers"] == [] and "phase" not in seen["lease"], seen
    assert _markers(fx["home"]) == [] and not lease.exists()


def _foreign(iso, seconds):
    """Another clone's marker (another token) at our path, expiring in `seconds`."""
    marker = lw.marker_path(git(["remote", "get-url", "origin"], iso["tree"])[1])
    marker.parent.mkdir(parents=True)
    text = json.dumps({"marker_version": 1, "token": "another-clone", "expires_at":
                       (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()})
    marker.write_text(text)
    return marker, text


def test_a_live_foreign_marker_is_never_replaced(tmp_path, monkeypatch):
    """§18.1: another clone's land holds a live marker for the same remote. Ours stays in
    phase 1 (`marker-held`), writes no lease phase, and leaves their file as it was."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    marker, foreign = _foreign(iso, 60)
    _hook(fx, iso)
    assert _land(iso)["action"] == "landed"
    _markers_seen, lease, token = _snap(fx)
    assert "phase" not in lease and token == ""                 # no window was opened
    assert marker.read_text() == foreign
    ev = _window(iso)
    assert (ev["outcome"], ev["reason"]) == ("stayed-phase1", "marker-held")


def test_a_failed_marker_write_leaves_a_foreign_marker_alone(tmp_path, monkeypatch):
    """§18.1: an EXPIRED foreign marker may be replaced; our marker write fails before it
    replaces the file, and the rollback must not delete theirs."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    marker, foreign = _foreign(iso, -1)
    real_write = lp2.rsi._atomic_write

    def write(path, text):
        if Path(path) == marker:
            raise OSError("disk full on the marker write")
        real_write(path, text)

    monkeypatch.setattr(lp2.rsi, "_atomic_write", write)
    with pytest.raises(OSError, match="disk full"):
        _land(iso)
    assert marker.read_text() == foreign


def test_a_lease_mutex_failure_still_drops_the_marker_under_its_mutex(tmp_path, monkeypatch):
    """§18.6 inv. 3: `leave` cannot take the lease mutex; the marker still goes, and only
    while the marker's own mutex is held."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    lease, guard, real_drop, held = (sl._ship_lock_path(iso["plan_dir"], fx["resource"]),
                                     lp2.lg, lp2._drop_ours, [])

    def mutex(path):
        if Path(path) == lease and "marker" in lp2._OPEN:       # after `_mark`: `_unmark` only
            raise lp2.rsi.LockError("lease mutex unavailable")
        return guard.mutex(path)

    def drop(marker, token):
        held.append(bool(guard._HELD.paths.get(str(marker))))
        real_drop(marker, token)

    monkeypatch.setattr(lp2, "lg", type("LG", (), {"mutex": staticmethod(mutex)}))
    monkeypatch.setattr(lp2, "_drop_ours", drop)
    _hook(fx, iso)
    assert _land(iso)["action"] == "landed"
    assert held == [True] and _markers(fx["home"]) == []


def test_an_expired_marker_holds_no_writer(tmp_path):
    """§18.6 inv. 1: a crashed land's marker past its expiry is ignored by the hook."""
    m = {"token": "t", "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
    assert lw._live(m) is False


# --------------------------------------------------------------------------
# One deadline (§18.4)
# --------------------------------------------------------------------------
def test_an_underestimated_gate_hits_the_deadline_and_resets(tmp_path, monkeypatch):
    slow = tmp_path / "SLOW"
    fx, iso = _setup(tmp_path, monkeypatch, "on", gates={"marker-gate": _gate(
        f"[ -f '{slow}' ] && rm '{slow}' && exec sleep 40; test ! -f GATE_FAIL")})
    assert lst.load(iso["plan_dir"])["gates"][0]["wall_s"] < 5    # the budget sees a fast gate
    real, took = land_gate.step_gate, []

    def gate_near_the_deadline(*a, **k):
        # the deadline falls 5 s into the phase-2 gate, wherever the steps before it end
        # (2026-11-17: a fixed 10 s window fired on the merge under machine load)
        if lst._PHASE2_DEADLINE.get("at") is not None:
            lst._PHASE2_DEADLINE["at"] = lst.time.monotonic() + 5
        t = lst.time.monotonic()
        try:
            return real(*a, **k)
        finally:
            took.append(lst.time.monotonic() - t)

    monkeypatch.setattr(land_gate, "step_gate", gate_near_the_deadline)
    slow.touch()
    _hook(fx, iso, races=1)                     # main moves once: the gate re-runs in phase 2
    out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    ev = _window(iso)
    assert (ev["outcome"], ev["reason"]) == ("reset", "gate-overrun")
    assert took and took[0] < 30                                # killed, not 40 s of sleep
    resets = [r for r in _events(iso["plan_dir"], "land_carry") if r["reason"] == "gate-overrun"]
    assert len(resets) == 1
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["invalidated_from"]["note"] == "ok"
    assert {g["gate_id"]: g["outcome"] for g in st["gates"]}["marker-gate"] == "pass"   # phase 1 re-ran it
    assert not _pushed(fx) and _markers(fx["home"]) == []


def test_a_step_that_outlasts_the_deadline_resets_and_re_gates(tmp_path, monkeypatch):
    """A step with no timeout of its own runs past the deadline: no push follows, the
    land walks phase 1 again from the top and every gate re-runs."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    ran = []

    def slow(plan_dir, ctx, st):
        # the step ends past the deadline, wherever the steps before it end
        # (2026-11-17: a fixed 10 s window fired before this step under machine load)
        ran.append(1)
        lst._PHASE2_DEADLINE["at"] = lst.time.monotonic() - 1

    monkeypatch.setattr(steps, "STEPS", (*steps.STEPS[:-2], slow, *steps.STEPS[-2:]))
    _hook(fx, iso)
    before = origin_main(fx)
    out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    ev = _window(iso)
    assert (ev["outcome"], ev["reason"]) == ("reset", "phase2-deadline")
    assert ran == [1] and not (fx["cap"] / "count").exists()   # no push was attempted
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["argv_gates"]              # the gate tables re-filled
    assert origin_main(fx) == before and _markers(fx["home"]) == []


def test_a_push_the_deadline_killed_is_never_reset(tmp_path, monkeypatch):
    """The push hangs and the deadline kills it. The remote may have taken it, so the land
    does not reset (§18.4): the timeout is today's error, the ack stays, the marker goes."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    monkeypatch.setattr(lp2, "DEADLINE_S", 8)
    _hook(fx, iso, hang=True)
    before = origin_main(fx)
    t0 = lst.time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        _land(iso)
    assert lst.time.time() - t0 < 18                            # killed, not the hook's 20 s
    assert not [r for r in _events(iso["plan_dir"], "land_carry") if r["reason"] in (
        "phase2-deadline", "gate-overrun", "phase2-retries-exhausted")]
    assert origin_main(fx) == before and _markers(fx["home"]) == []
    st = lst.load(iso["plan_dir"])
    assert st["ack"] and st.get("landed_sha")                   # a resume can ask git about it
    assert all(st.get(k) for k in ("landed_base", "landed_on", "attempts"))   # the record is whole


def test_a_push_that_landed_is_never_reset_by_a_later_timeout(tmp_path, monkeypatch):
    """The push exits 0, then the verification fetch outlasts the deadline: the land is
    on main, so it stays landed (§18.4: the clock stops at the push)."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    monkeypatch.setattr(lp2, "DEADLINE_S", 12)
    flag, slow = fx["cap"] / "SLOW_FETCH", fx["cap"] / "upload-pack"
    slow.write_text(f"#!/bin/sh\n[ -f '{flag}' ] && rm '{flag}' && sleep 14\n"
                    'exec git upload-pack "$@"\n')
    slow.chmod(0o755)
    git(["config", "remote.origin.uploadpack", str(slow)], fx["root"])
    _hook(fx, iso, extra=f"[ \"$n\" = 0 ] && touch '{flag}'")   # only fetches after our push
    out = _land(iso)
    assert out["action"] == "landed", out
    assert _pushed(fx) and not flag.exists()                    # the slow fetch did run
    ev = _window(iso)
    assert (ev["outcome"], ev["attempts"]) == ("pushed", 1)
    assert not [r for r in _events(iso["plan_dir"], "land_carry") if r["reason"] in (
        "phase2-deadline", "gate-overrun", "phase2-retries-exhausted")]
    assert lst.load(iso["plan_dir"])["ack"] and _markers(fx["home"]) == []


def test_a_lease_lost_after_the_push_still_records_the_push(tmp_path, monkeypatch):
    """The push reached main, then the lease check fails: the park keeps `landed_sha`,
    so a resume knows the land is on main and never pushes again."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso)
    monkeypatch.setattr(steps.lg, "holds_lease", lambda *a, **k: not _pushed(fx))
    out = _land(iso)
    assert (out["action"], out["kind"]) == ("land-parked", "lease-lost-after"), out
    assert _pushed(fx) and lst.load(iso["plan_dir"]).get("landed_sha")


@pytest.mark.parametrize("mode", ["on", "shadow", "off"])
def test_retries_share_one_budget_of_three(tmp_path, monkeypatch, mode):
    """Three stale rejections: `on` resets (phase2-retries-exhausted); `shadow` and
    `off` park push-rejected exactly as before R7."""
    fx, iso = _setup(tmp_path, monkeypatch, mode)
    _hook(fx, iso, races=9)                                     # every push meets a moved main
    out = _land(iso)
    assert int((fx["cap"] / "count").read_text()) == 3          # 3 pushes in total, never more
    st = lst.load(iso["plan_dir"])
    assert not st.get("landed_sha")                             # a stale rejection drops the tentative record
    if mode == "on":
        assert out["action"] == "land-awaits-review", out
        ev = _window(iso)
        assert (ev["outcome"], ev["reason"], ev["attempts"]) == (
            "reset", "phase2-retries-exhausted", 3)
        assert st["ack"] is None and st["invalidated_from"]
        # the owner is asked about the tree on the NEW main, not the stale candidate
        assert st["expected"] == origin_main(fx) == st["candidate"]["main_head"]
    else:
        assert (out["action"], out["kind"]) == ("land-parked", "push-rejected"), out
        assert st["ack"]                                         # today's park keeps the ack
    if mode == "shadow":
        assert (_window(iso)["mode"], _window(iso)["outcome"]) == ("shadow", "parked")
    if mode == "off":
        assert not _events(iso["plan_dir"], "land_phase2")
    assert _markers(fx["home"]) == []


# --------------------------------------------------------------------------
# The switch (§18.11) and the lease without a phase (§18.6 inv. 2)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["shadow", "on"])
def test_shadow_never_carries_a_code_move(tmp_path, monkeypatch, mode):
    """A path-disjoint code move in the push window: `on` carries it and pushes;
    `shadow` resets as today and only logs what `on` would have carried."""
    fx, iso = _setup(tmp_path, monkeypatch, mode)
    _hook(fx, iso, races=1, race_dir="lib")                     # harness code, not a harvest
    out = _land(iso)
    carry = _events(iso["plan_dir"], "land_carry")[-1]
    ev = _window(iso)
    assert ev["carried"] == ["review-gate"] and ev["rerun"] == ["marker-gate"]
    assert ev["moved_paths"] == ["lib/raced-0.txt"]
    if mode == "shadow":
        assert out["action"] == "land-awaits-review", out
        assert (carry["decision"], carry["reason"]) == ("reset", "harness-class")
        assert carry["shadow"]["decision"] == "carry"
        assert (ev["mode"], ev["outcome"]) == ("shadow", "parked")
        assert lst.load(iso["plan_dir"])["ack"] is None
        assert not _snap(fx)[0] and _snap(fx)[2] == ""          # shadow writes no marker
    else:
        assert out["action"] == "landed", out
        assert (carry["decision"], carry["route"]) == ("carry", "code")
        assert (ev["outcome"], ev["attempts"]) == ("pushed", 2)
        assert all(len(_snap(fx, n)[0]) == 1 for n in (0, 1))    # held through the re-sync
    assert _markers(fx["home"]) == []


def test_off_and_a_lease_without_a_phase_behave_as_today(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "off")
    _hook(fx, iso)
    out = _land(iso)
    assert out["action"] == "landed", out
    markers, lease, token = _snap(fx)
    assert markers == [] and token == ""                         # off never writes a marker
    assert "phase" not in lease and "phase_expires_at" not in lease
    assert not (Path(fx["home"]) / ".claude" / lw.MARKER_DIRNAME).exists()
    assert not _events(iso["plan_dir"], "land_phase2")


def test_a_budget_past_the_deadline_stays_in_phase_1(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on",
                     gates={"marker-gate": _gate("sleep 1; test ! -f GATE_FAIL")})
    monkeypatch.setattr(lp2, "DEADLINE_S", 0.5)            # the gate's last wall_s is ~1 s
    _hook(fx, iso)
    assert _land(iso)["action"] == "landed"
    assert not _snap(fx)[0] and _snap(fx)[2] == ""          # no marker, no token
    ev = _window(iso)
    assert (ev["outcome"], ev["reason"]) == ("stayed-phase1", "phase2-budget")


@pytest.mark.parametrize("value,expected", [("off", "off"), ("shadow", "shadow"),
                                            ("on", "on"), ("ON", "off"), ("", "off")])
def test_unknown_mode_reads_as_off(monkeypatch, value, expected):
    monkeypatch.setattr(lp2, "PHASE2_MODE", value)
    assert lp2.mode() == expected


def test_streak_ok_checks_both_limits():
    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    fresh = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=25)).isoformat()

    def st(streak, at):
        return {"carry_streaks": {"ci-check": {"streak": streak, "last_run_pass_at": at}}}
    assert lp2.streak_ok(st(3, fresh), "ci-check", now) == (True, None)
    assert lp2.streak_ok(st(4, fresh), "ci-check", now) == (False, "carry-streak")
    assert lp2.streak_ok(st(0, old), "ci-check", now) == (False, "carry-stale")
    assert lp2.streak_ok({}, "ci-check", now) == (False, "carry-stale")


def test_git_env_cleared_decoy_untouched(tmp_path, monkeypatch):
    """GIT_DIR and friends name a decoy repository; a whole `on` land runs; the decoy
    is byte-for-byte unchanged, so every git call went to the fixture's repos."""
    decoy = tmp_path / "decoy"
    subprocess.run(["git", "init", "-q", str(decoy)], check=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    before = {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()}
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
    fx, iso = _setup(tmp_path / "w", monkeypatch, "on")
    assert _land(iso)["action"] == "landed"
    after = {p: p.read_bytes() for p in (decoy / ".git").rglob("*") if p.is_file()}
    assert after == before
