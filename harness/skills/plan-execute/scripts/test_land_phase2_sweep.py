"""Phase 2 failure sweep: fail EVERY git call in the push path, one at a time (R7 s04).

    pytest plan-execute/scripts/test_land_phase2_sweep.py -q

Reviews kept finding one more "if this git call fails here, the saved state is wrong".
This test finds them all in one run. It traces the call sites of `land_push.git` and
`land_push.rev` in three clean scenarios (a plain land, a land whose first push meets a
moved main so it re-syncs, a resume after a killed push). For each site and each fault
mode it re-runs the scenario with that one call failing, then lands again with no fault.
After EVERY run these must hold:

  1. the injected fault really fired (a case that never reaches its site is a failure);
  2. origin never holds the work twice (no second push);
  3. a push that reached origin leaves the whole landing record (sha, base, target,
     attempts) in the saved state, and no phase-2 reset;
  4. after the final land the phase-2 marker is gone and the record is whole.
"""
import subprocess
import sys

from test_land_phase2 import _events, _hook, _land, _markers, _pushed, _setup, lst, steps
from worktree import git as real_git_fn

REAL_GIT, REAL_REV = steps.git, steps.rev          # captured once: wrappers never stack
KEYS = ("landed_sha", "landed_base", "landed_on", "attempts")
RESETS = ("phase2-deadline", "gate-overrun", "phase2-retries-exhausted")
MODES = ("late-timeout", "fail")      # the call ran then raised, or it never ran and failed


def _instrument(mp, fault, seen):
    """Wrap git and rev in land_push. `fault` is ((function, line), mode) or None."""
    fired = []

    def decide(frame):
        site = (frame.f_code.co_name, frame.f_lineno)
        if site not in seen:
            seen.append(site)
        if fault and site == fault[0] and not fired:
            fired.append(site)
            return fault[1]
        return None

    def w_git(*a, **k):
        mode = decide(sys._getframe(1))
        if mode == "fail":
            return 1, "", "injected failure"
        out = REAL_GIT(*a, **k)
        if mode == "late-timeout":
            raise subprocess.TimeoutExpired("git", 1)
        return out

    def w_rev(*a, **k):
        mode = decide(sys._getframe(1))
        if mode == "fail":
            return None
        out = REAL_REV(*a, **k)
        if mode == "late-timeout":
            raise subprocess.TimeoutExpired("git", 1)
        return out

    mp.setattr(steps, "git", w_git)
    mp.setattr(steps, "rev", w_rev)
    return fired


def _commits_of_work(fx):
    return len(real_git_fn(["log", "--format=%H", "main", "--", "src/f.py"], fx["origin"])[1].split())


def _whole(st):
    return [k for k in KEYS if not st.get(k)]


def _run(tmp_path, monkeypatch, faults, seen, races=0):
    """One land per entry of `faults` (None = clean), then a fault-free land.
    Returns (violations, land_carry reasons)."""
    bad = []
    with monkeypatch.context() as mp:
        fx, iso = _setup(tmp_path, mp, "on")
        _hook(fx, iso, races=races)
        for fault in list(faults) + [None]:
            fired = _instrument(mp, fault, seen)
            try:
                _land(iso)
            except Exception as e:
                if fault is None:                            # the last land must not crash
                    bad.append(f"the final land raised {type(e).__name__}: {e}")
            if fault and not fired:
                bad.append(f"the fault at {fault[0]} never fired")
            st = lst.load(iso["plan_dir"]) or {}
            if _pushed(fx) and _whole(st):
                bad.append(f"pushed but the record lacks {_whole(st)}")
        st = lst.load(iso["plan_dir"]) or {}
        reasons = [r.get("reason") for r in _events(iso["plan_dir"], "land_carry")]
        if _commits_of_work(fx) > 1:
            bad.append("origin holds the work twice")
        if _pushed(fx) and _markers(fx["home"]):
            bad.append(f"a marker is left after the final land: {_markers(fx['home'])}")
    return bad, reasons


def _sweep(tmp_path, monkeypatch, tag, prefix, sites, races=0):
    failures = {}
    for i, site in enumerate(sites):
        for mode in MODES:
            bad, _ = _run(tmp_path / f"{tag}{i}{mode}", monkeypatch, prefix + [(site, mode)], [], races)
            if bad:
                failures[f"{tag} {site[0]}:{site[1]} {mode}"] = bad
    return failures


def test_every_failing_git_call_in_the_push_path_leaves_a_sound_land(tmp_path, monkeypatch):
    plain, race, resumed = [], [], []
    assert _run(tmp_path / "base", monkeypatch, [], plain)[0] == []
    assert _run(tmp_path / "race", monkeypatch, [], race, races=1)[0] == []
    push_site = next(s for s in plain if s[0] == "_push_once")
    kill = [(push_site, "late-timeout")]
    assert _run(tmp_path / "kill", monkeypatch, kill, resumed)[0] == []
    resume_only = [s for s in resumed if s not in plain]       # reached only by a resume
    resync_only = [s for s in race if s not in plain]          # reached only by a re-sync
    assert resume_only and resync_only, (resume_only, resync_only)   # the scenarios reach new sites
    failures = {}
    failures |= _sweep(tmp_path, monkeypatch, "plain", [], plain)
    failures |= _sweep(tmp_path, monkeypatch, "resume", kill, resume_only)
    failures |= _sweep(tmp_path, monkeypatch, "resync", [], resync_only, races=1)
    assert not failures, "\n".join(f"{k}: {v}" for k, v in failures.items())


def test_a_timeout_in_the_resync_is_a_reset_never_a_crash(tmp_path, monkeypatch):
    """The first push meets a moved main, and the re-sync's fetch outlasts the deadline.
    `run_step` catches it with the clock open: reset, then today's land from the top."""
    race = []
    assert _run(tmp_path / "base", monkeypatch, [], race, races=1)[0] == []
    fetch = next(s for s in race if s[0] == "_resync")
    bad, reasons = _run(tmp_path / "t", monkeypatch, [(fetch, "late-timeout")], [], races=1)
    assert bad == [] and "phase2-deadline" in reasons, (bad, reasons)


def test_a_failed_fetch_in_the_resync_resets_with_git_error_and_never_pushes_stale(tmp_path, monkeypatch):
    """rc 128 from the re-sync's fetch: the land restarts as phase 1 with reason git-error."""
    race = []
    assert _run(tmp_path / "base", monkeypatch, [], race, races=1)[0] == []
    fetch = next(s for s in race if s[0] == "_resync")
    bad, reasons = _run(tmp_path / "t", monkeypatch, [(fetch, "fail")], [], races=1)
    assert bad == [] and "git-error" in reasons, (bad, reasons)
