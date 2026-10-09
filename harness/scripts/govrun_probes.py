#!/usr/bin/env python3
"""Rerunnable live probes for the machine governor (govrun.py + governor-hook.py).

Five probes, each carrying its own control so it cannot pass vacuously:

  1. queueing:       two govrun jobs on ONE shared slot never overlap.
                      CONTROL (known negative): 2 slots of 2 -> they DO overlap
                      -- proves the measurement can tell the two states apart.
  2. crash-release:   kill -9 the holder mid-run; the queued waiter acquires
                      fast, and --status afterwards shows no LIVE holder.
  3. budget-ceiling:  two xdist-style jobs, each spawning
                      PYTEST_XDIST_AUTO_NUM_WORKERS sleepers, on the ONE shared
                      slot read live from govrun_config.DEFAULT_SLOTS -- the
                      ceiling bounds the SUM across both jobs, not just one.
  4. cross-class:     under the shipped one-slot table, a `ci` holder
                      serialises an `interactive` job behind it; the
                      interactive job times out (exit 75, queue-timeout text).
  5. hook:            live subprocess invocation of governor-hook.py: deny
                      (unwrapped heavy), allow (wrapped), allow (non-heavy),
                      and a 20-sample p50/p95 latency measurement.

Every process is PID-scoped when checked (ps -p <pid>, or ps filtered by
ppid), never a substring grep of the whole process table -- a grep for
"sleep 6" would self-match this very script's own controlling shell, which
carries that same text on its command line.

Usage: python3 govrun_probes.py [--out DIR]
  --out defaults to the OUTER plan directory's
  _evidence/governor-probes when this runs from a plan worktree,
  because the worktree's own /_evidence/ is gitignored and a transcript
  written there is read by nobody. The resolved path is printed.

Writes probes-transcript.txt (probes 1-4) and hook-latency.txt (probe 5) into
--out, both flushed line-by-line as the run progresses. Exits 1 if any
probe's CONTROL misbehaved (the probe is broken, not the governor) -- see the
ABORT list printed at the end.
"""
import argparse
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOVRUN = str(REPO / "scripts" / "govrun")
HOOK = str(REPO / "hooks" / "governor-hook.py")
sys.path.insert(0, str(REPO / "scripts"))
import govrun_config  # noqa: E402

ABORT = []  # control failures land here -- non-empty means BLOCKED, not DONE
# Probe failures land here (distinct from an ABORT: ABORT = the probe is broken,
# FAILURE = the governor is). Both exit non-zero -- previously a `FAIL:`
# in the transcript still exited 0, so the exit code read green on a real failure.
FAILURES = []


class Tee:
    """Prints AND appends to a file, flushing every line -- the brief asks for
    continuous output and a rerunnable transcript, not a buffered dump."""

    def __init__(self, fh):
        self.fh = fh

    def __call__(self, *parts):
        line = " ".join(str(p) for p in parts)
        # BACKSTOP ONLY -- `check` below is how a verdict is meant to be
        # recorded. This catches a prose `FAIL:` that never went through `check`
        # (which once let probe 2 report its exact defect and still exit 0); it
        # cannot catch a check that never writes the word. A CONTROL FAIL is
        # skipped (that is an ABORT), and a line `check` recorded is not doubled.
        if "FAIL" in line and "CONTROL FAIL" not in line and line not in FAILURES:
            FAILURES.append(line)
        print(line, flush=True)
        self.fh.write(line + "\n")
        self.fh.flush()

    def check(self, ok, message):
        """Record a pass/fail AND write its transcript line, from the SAME
        boolean. Every probe check goes through here, so a check cannot pass by
        forgetting to spell its own failure."""
        line = f"{'PASS' if ok else 'FAIL'}: {message}"
        if not ok:
            FAILURES.append(line)
        self(line)
        return ok


def comm(pid):
    """Short exec name of a live pid ('' if it is gone). PID-scoped (`ps -p`)
    so it can never self-match this script's own command line."""
    r = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)],
                        capture_output=True, text=True)
    return r.stdout.strip()


def children_named(ppids, name):
    """Count of live processes whose ppid is in `ppids` and comm == name."""
    r = subprocess.run(["ps", "-eo", "pid,ppid,comm"],
                        capture_output=True, text=True)
    n = 0
    for line in r.stdout.splitlines()[1:]:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            ppid = int(parts[1])
        except ValueError:
            continue
        if ppid in ppids and parts[2] == name:
            n += 1
    return n


def env_with(state_dir, extra=None):
    e = dict(os.environ, GOVRUN_STATE_DIR=str(state_dir))
    e.pop("GOVRUN_SLOTS", None)
    if extra:
        e.update(extra)
    return e


def kill_tree_safe(*procs):
    """Never leave a process behind, whatever a probe's outcome was."""
    for p in procs:
        if p is None:
            continue
        if p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
            try:
                p.wait(timeout=5)
            except Exception:
                pass


# --- probe 1: queueing known positive + 2-slot negative control ------------

def probe1(w, tmp_root):
    w("=== PROBE 1: queueing known positive + 2-slot negative control ===")
    state = tmp_root / "p1-positive"
    state.mkdir()
    env = env_with(state, {"GOVRUN_SLOTS": json.dumps(
        [{"name": "a", "workers": 4, "classes": ["ci", "interactive"]}])})
    w(f"GOVRUN_STATE_DIR={state}")
    w(f"GOVRUN_SLOTS={env['GOVRUN_SLOTS']}")
    j1 = subprocess.Popen([GOVRUN, "sleep", "6"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    j2 = subprocess.Popen([GOVRUN, "sleep", "6"], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    both_ever = seen1 = seen2 = False
    for t in range(14):
        time.sleep(1)
        c1, c2 = comm(j1.pid), comm(j2.pid)
        w(f"t={t + 1}s job1.pid={j1.pid} comm={c1!r} job2.pid={j2.pid} comm={c2!r}")
        seen1, seen2 = seen1 or c1 == "sleep", seen2 or c2 == "sleep"
        if c1 == "sleep" and c2 == "sleep":
            both_ever = True
    _, err1 = j1.communicate(timeout=10)
    _, err2 = j2.communicate(timeout=10)
    w(f"job1 exit={j1.returncode} stderr={err1.strip()!r}")
    w(f"job2 exit={j2.returncode} stderr={err2.strip()!r}")
    # A crash never overlaps either, so absence of overlap is NOT enough: a real
    # PASS needs both payloads to have RUN -- each seen as sleep, each exit 0 --
    # not merely to have missed each other (found in review).
    w.check(seen1 and seen2 and j1.returncode == 0 and j2.returncode == 0
            and not both_ever, "both payloads ran and were serialised, never the "
                               "same sample tick")
    kill_tree_safe(j1, j2)

    w("--- CONTROL (known negative): 2 slots of 2 -- expect BOTH concurrent ---")
    state2 = tmp_root / "p1-control"
    state2.mkdir()
    env2 = env_with(state2, {"GOVRUN_SLOTS": json.dumps([
        {"name": "a", "workers": 2, "classes": ["ci", "interactive"]},
        {"name": "b", "workers": 2, "classes": ["ci", "interactive"]},
    ])})
    w(f"GOVRUN_STATE_DIR={state2}")
    w(f"GOVRUN_SLOTS={env2['GOVRUN_SLOTS']}")
    k1 = subprocess.Popen([GOVRUN, "sleep", "3"], env=env2,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    k2 = subprocess.Popen([GOVRUN, "sleep", "3"], env=env2,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(1)
    cc1, cc2 = comm(k1.pid), comm(k2.pid)
    w(f"t=1s (control) job1 comm={cc1!r} job2 comm={cc2!r}")
    control_ok = cc1 == "sleep" and cc2 == "sleep"
    w("CONTROL PASS: both ran concurrently" if control_ok else
      "CONTROL FAIL: the 2-slot table did not show concurrency -- "
      "the PROBE, not the governor, is broken")
    k1.communicate(timeout=10)
    k2.communicate(timeout=10)
    kill_tree_safe(k1, k2)
    if not control_ok:
        ABORT.append("probe1 control: 2-slot table did not run concurrently")


# --- probe 2: crash release --------------------------------------------------

def probe2(w, tmp_root):
    w("=== PROBE 2: crash release (kill -9 the exec'd payload mid-run) ===")
    state = tmp_root / "p2"
    state.mkdir()
    env = env_with(state)  # shipped default table, no override
    w(f"GOVRUN_STATE_DIR={state} (shipped default table, no GOVRUN_SLOTS override)")
    holder = subprocess.Popen([GOVRUN, "sleep", "30"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    acquired = False
    for _ in range(50):
        if comm(holder.pid) == "sleep":
            acquired = True
            break
        time.sleep(0.1)
    w(f"holder pid={holder.pid} acquired={acquired}")
    if not acquired:
        ABORT.append("probe2: holder never became the exec'd payload -- probe setup broken")
        kill_tree_safe(holder)
        return

    waiter = subprocess.Popen([GOVRUN, "sleep", "3"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.3)
    before = comm(waiter.pid)
    w(f"waiter pid={waiter.pid} comm before kill={before!r} (expect queued, not 'sleep')")
    if before == "sleep":
        ABORT.append("probe2 control: waiter acquired before the holder was killed -- "
                      "probe timing is broken")
        kill_tree_safe(holder, waiter)
        return

    t_kill = time.monotonic()
    os.kill(holder.pid, signal.SIGKILL)
    w(f"kill -9 sent to holder pid={holder.pid} (t=0.00s reference)")
    t_acquired = None
    for _ in range(40):
        if comm(waiter.pid) == "sleep":
            t_acquired = time.monotonic()
            break
        time.sleep(0.05)
    delta = None if t_acquired is None else t_acquired - t_kill
    w.check(delta is not None and delta < 2,
            f"waiter acquired within 2s of the kill -9 (measured: "
            f"{'never, in 4s' if delta is None else format(delta, '.3f') + 's'})")

    holder.communicate(timeout=10)
    w(f"holder exit={holder.returncode} (expect a negative signal code, killed)")
    _, err_w = waiter.communicate(timeout=10)
    w(f"waiter exit={waiter.returncode} stderr={err_w.strip()!r}")

    status = subprocess.run([GOVRUN, "--status"], env=env, capture_output=True, text=True)
    w("--- govrun --status after crash + waiter finished ---")
    for line in status.stdout.splitlines():
        w(f"  {line}")
    slot_lines = [ln for ln in status.stdout.splitlines() if ln.startswith("slot ")]
    live_held = [ln for ln in slot_lines if " HELD " in ln and " dead " not in ln]
    # THE check this probe exists for. It used to be written as an expectation
    # with no verdict, so a slot still held by a live process after the crash
    # left FAILURES empty and the script exited 0.
    w.check(not live_held,
            f"no slot shows a LIVE holder after the crash: {live_held!r}")
    kill_tree_safe(holder, waiter)


# --- probe 3: budget ceiling --------------------------------------------------

def probe3(w, tmp_root, ceiling):
    w("=== PROBE 3: budget ceiling (two xdist-style jobs, ONE shared slot) ===")
    w(f"ceiling read live from govrun_config.DEFAULT_SLOTS = {govrun_config.DEFAULT_SLOTS} "
      f"-> total workers = {ceiling}")
    state = tmp_root / "p3"
    state.mkdir()
    env = env_with(state)  # shipped default table
    w(f"GOVRUN_STATE_DIR={state} (shipped default table, no GOVRUN_SLOTS override)")
    script = ('n="$PYTEST_XDIST_AUTO_NUM_WORKERS"; i=0; '
              'while [ "$i" -lt "$n" ]; do sleep 5 & i=$((i+1)); done; wait')
    j1 = subprocess.Popen([GOVRUN, "sh", "-c", script], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    j2 = subprocess.Popen([GOVRUN, "sh", "-c", script], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    max_seen = 0
    for t in range(13):
        time.sleep(1)
        n = children_named({j1.pid, j2.pid}, "sleep")
        max_seen = max(max_seen, n)
        w(f"t={t + 1}s concurrent sleep children of job1({j1.pid})/job2({j2.pid}): "
          f"{n} (running max so far: {max_seen})")
    _, err1 = j1.communicate(timeout=10)
    _, err2 = j2.communicate(timeout=10)
    w(f"job1 exit={j1.returncode} stderr={err1.strip()!r}")
    w(f"job2 exit={j2.returncode} stderr={err2.strip()!r}")
    kill_tree_safe(j1, j2)
    w(f"max concurrent sleepers observed across BOTH jobs: {max_seen} (ceiling={ceiling})")
    if max_seen == 0:
        ABORT.append("probe3 control: never observed a single spawned sleeper -- "
                      "probe payload is broken")
    else:
        w.check(max_seen <= ceiling,
                f"never exceeded the shipped ceiling ({max_seen} vs {ceiling})")


# --- probe 4: cross-class serialisation --------------------------------------

def probe4(w, tmp_root):
    w("=== PROBE 4: cross-class serialisation (shipped 1-slot table) ===")
    state = tmp_root / "p4"
    state.mkdir()
    env = env_with(state)  # shipped default table
    w(f"GOVRUN_STATE_DIR={state} (shipped default table, no GOVRUN_SLOTS override)")
    ci_job = subprocess.Popen([GOVRUN, "--class", "ci", "--", "sleep", "5"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    acquired = False
    for _ in range(50):
        if comm(ci_job.pid) == "sleep":
            acquired = True
            break
        time.sleep(0.1)
    w(f"ci holder pid={ci_job.pid} acquired={acquired}")
    if not acquired:
        ABORT.append("probe4: ci holder never acquired -- probe setup broken")
        kill_tree_safe(ci_job)
        return

    t0 = time.monotonic()
    interactive = subprocess.run(
        [GOVRUN, "--class", "interactive", "--max-wait", "1", "--", "sleep", "1"],
        env=env, capture_output=True, text=True, timeout=15)
    elapsed = time.monotonic() - t0
    w(f"interactive job exit={interactive.returncode} elapsed={elapsed:.2f}s")
    w(f"interactive stderr={interactive.stderr.strip()!r}")
    w(f"interactive stdout={interactive.stdout.strip()!r}")
    ok = (interactive.returncode == 75
          and "queue timeout" in interactive.stderr
          and "not a test failure" in interactive.stderr)
    w.check(ok, "interactive job serialised behind the ci holder: exit 75 with "
                "queue-timeout text")
    ci_job.communicate(timeout=10)
    w(f"ci holder exit={ci_job.returncode}")
    kill_tree_safe(ci_job)


# --- probe 5: hook live invocation + latency ---------------------------------

def _hook_payload(command):
    return json.dumps({
        "session_id": "probe", "cwd": str(REPO), "permission_mode": "default",
        "hook_event_name": "PreToolUse", "tool_name": "Bash",
        "tool_input": {"command": command, "description": "probe",
                       "timeout": 120000, "run_in_background": False},
        "tool_use_id": "toolu_probe",
    })


def probe5(w, tmp_root):
    w("=== PROBE 5: hook live invocation -- deny / wrapped-allow / non-heavy-allow ===")
    state = tmp_root / "p5"
    state.mkdir()
    env = dict(os.environ, GOVRUN_STATE_DIR=str(state))
    env.pop("GOVRUN_SLOTS", None)

    def run_hook(command):
        return subprocess.run([sys.executable, HOOK], input=_hook_payload(command),
                              env=env, capture_output=True, text=True, timeout=10)

    cases = [
        ("deny (unwrapped heavy)", "pytest -n 8", "deny"),
        ("allow (wrapped, auto workers)", "govrun -- pytest -n auto", "allow"),
        ("allow (non-heavy)", "echo hello", "allow"),
    ]
    for label, cmd, expect in cases:
        r = run_hook(cmd)
        stdout = r.stdout.strip()
        if stdout:
            hso = json.loads(stdout).get("hookSpecificOutput", {})
            got, reason = hso.get("permissionDecision", "?"), hso.get("permissionDecisionReason", "")
        else:
            got, reason = "allow (silent)", ""
        # An allow is silent stdout AND a clean exit: a hook that CRASHES (exit
        # non-zero, empty stdout) is not a silent-allow, and the settings.json
        # wrapper turns that into a deny. Checking only the empty stdout would
        # pass a crash as an allow.
        ok = (got == "deny") if expect == "deny" else (stdout == "" and r.returncode == 0)
        w.check(ok, f"{label}: cmd={cmd!r} exit={r.returncode} decision={got!r} "
                    f"reason={reason!r}")
        if expect == "deny" and ok:
            w.check("govrun" in reason and "not wrapped" in reason,
                    f"the deny names the remedy: reason={reason!r}")

    w("--- latency: 20 live subprocess runs of the worst case ('pytest -n 8') ---")
    timings = []
    for i in range(20):
        started = time.perf_counter()
        run_hook("pytest -n 8")
        ms = (time.perf_counter() - started) * 1000
        timings.append(ms)
        w(f"run {i + 1:2d}: {ms:.1f} ms")
    p50 = statistics.median(timings)
    p95 = sorted(timings)[int(20 * 0.95) - 1]
    w(f"p50={p50:.1f}ms p95={p95:.1f}ms (budget: p95 < 200ms) "
      f"min={min(timings):.1f}ms max={max(timings):.1f}ms")
    w.check(p95 < 200, f"hook p95 under the 200ms budget (p95={p95:.1f}ms)")


# --- entry point --------------------------------------------------------------

def _default_out():
    """The plan's evidence lives in the OUTER plan directory, never in here.

    `REPO / "_evidence"` is the obvious default and it is WRONG when this file
    runs from a plan worktree: .gitignore swallows `/_evidence/`, so a rerun
    writes a full transcript that no gate and no reviewer will ever read, and
    nothing says so. Resolve the outer plan directory instead, and fall back to
    the in-repo path only when this is not a worktree at all.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"], cwd=REPO, text=True,
            capture_output=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        common = ""
    # A plan worktree lives at <main repo>/.plan-worktrees/<plan slug>, and its
    # own directory name IS the slug. Do NOT try to infer the slug from the
    # worktree's `_plans/` -- that is a full copy of every plan in the repo, not a single frozen one.
    if common and REPO.parent.name == ".plan-worktrees":
        main_root = (REPO / common).resolve().parent
        outer = main_root / "_plans" / REPO.name
        if outer.is_dir():
            return outer / "_evidence" / "governor-probes"
    return REPO / "_evidence" / "governor-probes"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_dir = Path(args.out) if args.out else _default_out()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"govrun_probes.py: writing evidence to {out_dir.resolve()}", flush=True)

    tmp_root = Path(tempfile.mkdtemp(prefix="govrun-probes-"))
    ceiling = sum(s["workers"] for s in govrun_config.DEFAULT_SLOTS)

    try:
        with open(out_dir / "probes-transcript.txt", "w") as tf:
            w = Tee(tf)
            w(f"govrun_probes.py -- run started {time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
            w(f"repo: {REPO}")
            w(f"evidence dir: {out_dir.resolve()}")
            w(f"shipped ceiling read from govrun_config.DEFAULT_SLOTS = "
              f"{govrun_config.DEFAULT_SLOTS} (total={ceiling} workers). The evidence "
              f"contract's descriptive text says '8-worker ceiling' -- that is STALE "
              f"(pre-dates the collapse to one shared 4-worker slot). "
              f"This probe checks {ceiling}, not 8.")
            w("")
            probe1(w, tmp_root)
            w("")
            probe2(w, tmp_root)
            w("")
            probe3(w, tmp_root, ceiling)
            w("")
            probe4(w, tmp_root)
            w("")
            w(f"=== END TRANSCRIPT === ABORT conditions hit so far: {ABORT!r}")

        with open(out_dir / "hook-latency.txt", "w") as lf:
            w2 = Tee(lf)
            w2(f"governor-hook.py live-invocation + latency probe -- run started "
               f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}")
            probe5(w2, tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    if ABORT:
        print("\nABORT: probe control(s) misbehaved -- the PROBE, not the governor, is broken:")
        for a in ABORT:
            print(f"  - {a}")
    if FAILURES:
        print("\nFAIL: the governor did not do what the probe requires:")
        for failure in FAILURES:
            print(f"  - {failure}")
    return exit_code()


def exit_code():
    """0 only when nothing aborted AND nothing failed. Its own function so the
    suite can assert the rule without running the probes, which sleep."""
    return 1 if ABORT or FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
