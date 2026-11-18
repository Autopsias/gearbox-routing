"""Phase 2 writes a fact down BEFORE the step that can fail after it (R7 review rounds 5-6)."""
import json
import os
import subprocess

import pytest

from test_land_phase2 import _events, _hook, _land, _markers, _pushed, _setup, lst, steps, lp2
from test_land_phase2_sweep import RESETS, _commits_of_work, _whole


def test_a_failed_check_after_the_push_keeps_the_push(tmp_path, monkeypatch):
    """The push reached main, then the check that confirms it raises: the record already
    holds the sha, so a resume verifies it instead of pushing again."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso)

    def boom(*a, **k):
        raise RuntimeError("fetch failed after the push")

    monkeypatch.setattr(steps, "_verify_landed", boom)
    with pytest.raises(RuntimeError):
        _land(iso)
    assert _pushed(fx)
    keys = ("landed_sha", "landed_base", "landed_on", "attempts")
    assert all(lst.load(iso["plan_dir"]).get(k) for k in keys)     # one whole record, not a sha alone


def test_a_resume_after_a_push_completes_a_partial_record(tmp_path, monkeypatch):
    """An old or half-written record holds only the sha. The resume verifies it against git
    and fills the rest, so the final record and the CI lookup never read a missing field."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso)
    real = steps._verify_landed

    def boom(*a, **k):
        raise RuntimeError("fetch failed after the push")

    monkeypatch.setattr(steps, "_verify_landed", boom)
    with pytest.raises(RuntimeError):
        _land(iso)
    monkeypatch.setattr(steps, "_verify_landed", real)
    st = lst.load(iso["plan_dir"])
    for k in ("landed_base", "landed_on", "attempts"):
        st.pop(k)                                   # the half record a resume can meet
    lst.save(iso["plan_dir"], lst.context(iso["plan_dir"]), st)
    _land(iso)
    st = lst.load(iso["plan_dir"])
    assert all(st.get(k) for k in ("landed_sha", "landed_base", "landed_on", "attempts"))
    assert "phase" not in st and "phase2" not in st     # the resume leaves no window open
    assert not steps.lp2.is_open(st) and _markers(fx["home"]) == []


def test_a_failed_log_during_a_reset_still_drops_the_ack(tmp_path, monkeypatch):
    """The reset's logging raises: the old approval is already gone from the saved state."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso, races=9)                         # every push meets a moved main: exhaustion reset

    def boom(*a, **k):
        raise RuntimeError("log failed")

    monkeypatch.setattr(lp2.lc, "log_reset", boom)
    with pytest.raises(RuntimeError):
        _land(iso)
    st = lst.load(iso["plan_dir"])
    assert st["ack"] is None and st["invalidated_from"]


def test_a_failed_read_after_a_killed_push_is_never_a_reset(tmp_path, monkeypatch):
    """The deadline kills the push, then any further git read times out too. The clock has
    already stopped and the sha was read before the push, so there is no reset (§18.4)."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    monkeypatch.setattr(lp2, "DEADLINE_S", 8)
    _hook(fx, iso, hang=True)
    real_rev, killed = steps.rev, []

    def late_rev(*a, **k):
        if killed:
            raise subprocess.TimeoutExpired("git", 1)
        return real_rev(*a, **k)

    def kill_then_flag(*a, **k):
        try:
            return real_push(*a, **k)
        finally:
            killed.append(1)

    real_push = steps._push_once
    monkeypatch.setattr(steps, "rev", late_rev)
    monkeypatch.setattr(steps, "_push_once", kill_then_flag)
    with pytest.raises(subprocess.TimeoutExpired):
        _land(iso)
    assert not [r for r in _events(iso["plan_dir"], "land_carry") if r["reason"] in (
        "phase2-deadline", "gate-overrun", "phase2-retries-exhausted")]
    st = lst.load(iso["plan_dir"])
    assert st["ack"] and all(st.get(k) for k in ("landed_sha", "landed_base", "landed_on", "attempts"))

def test_a_hard_kill_between_the_push_and_the_record_resumes_sound(tmp_path, monkeypatch):
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso, extra='cat > "$C/refs-$n"')
    pid = os.fork()
    if pid == 0:                                   # the child: push, then die like a SIGKILL
        try:
            real = steps._push_once

            def push_then_die(ctx, st, path):
                out = real(ctx, st, path)
                os._exit(137 if out[0] == 0 else 3)

            monkeypatch.setattr(steps, "_push_once", push_then_die)
            _land(iso)
        finally:
            os._exit(5)
    assert os.WEXITSTATUS(os.waitpid(pid, 0)[1]) == 137

    st = lst.load(iso["plan_dir"]) or {}
    # The kill came after the remote took the push. On the code before the fix no record
    # says so here (measured: record_after_kill false, and the resume parked).
    assert _pushed(fx)
    after_kill = {"markers": _markers(fx["home"]), "phase": st.get("phase"),
                  "record_after_kill": bool(st.get("landed_sha"))}

    out = _land(iso)                               # the resume, no fault
    st = lst.load(iso["plan_dir"]) or {}
    resets = [r.get("reason") for r in _events(iso["plan_dir"], "land_carry")
              if r.get("reason") in RESETS]
    print("S04PROOF " + json.dumps({
        "after_kill": after_kill, "resume_action": out.get("action"), "kind": out.get("kind"),
        "commits_of_work": _commits_of_work(fx), "record_missing": _whole(st),
        "resets": resets, "markers_after": _markers(fx["home"]),
        "push_attempts": st.get("push_attempts"), "attempts": st.get("attempts"),
        "hook_pushes": (fx["cap"] / "count").read_text().strip(),
        "refs": [x.read_text().split()[2] if x.read_text().split() else "(none)"
                 for x in sorted(fx["cap"].glob("refs-*"))]}))
    assert _commits_of_work(fx) == 1               # (a) origin holds the work once
    assert resets == []                            # (a) a land that pushed is never reset
    assert _whole(st) == []                        # (b) sha, base, target, attempts all saved
    assert out["action"] == "landed", out


def test_control_a_land_with_no_fault(tmp_path, monkeypatch):
    """Control: which pushes does a land with no fault make? The proof reads against it."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    _hook(fx, iso, extra='cat > "$C/refs-$n"')
    out = _land(iso)
    print("S04CONTROL " + json.dumps({
        "action": out.get("action"), "hook_pushes": (fx["cap"] / "count").read_text().strip(),
        "refs": [x.read_text().split()[2] if x.read_text().split() else "(none)"
                 for x in sorted(fx["cap"].glob("refs-*"))]}))
    assert out["action"] == "landed"


_REAL_PUSH = steps._push_once


@pytest.mark.parametrize("remote_took_it", [True, False])
def test_a_failed_push_keeps_the_record_unless_the_remote_said_stale(tmp_path, monkeypatch,
                                                                     remote_took_it):
    """I3: a non-stale failure keeps the record (the remote may have taken the push); the resume
    asks git: landed once if it did, record dropped and pushed if it did not."""
    fx, iso = _setup(tmp_path, monkeypatch, "on")
    if remote_took_it:
        _hook(fx, iso)
        real = steps._push_once

        def push_then_drop_connection(*a, **k):
            real(*a, **k)
            return 1, "", "connection reset"

        monkeypatch.setattr(steps, "_push_once", push_then_drop_connection)
    else:
        _hook(fx, iso, refuse=True)
    _land(iso)
    st = lst.load(iso["plan_dir"]) or {}
    assert remote_took_it == _pushed(fx)
    assert all(st.get(k) for k in ("landed_sha", "landed_base", "landed_on", "attempts"))
    monkeypatch.setattr(steps, "_push_once", _REAL_PUSH)   # the resume: no fault, hook allows
    _hook(fx, iso)
    pushes = int((fx["cap"] / "count").read_text())
    assert _land(iso)["action"] == "landed"
    assert _commits_of_work(fx) == 1
    if remote_took_it:
        assert int((fx["cap"] / "count").read_text()) <= pushes + 1
