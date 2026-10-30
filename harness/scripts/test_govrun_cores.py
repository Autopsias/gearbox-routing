"""govrun's core-token budget (govrun_cores.py): one CPU pool for every heavy job.

Each test runs real govrun processes against a throwaway state dir and a small
GOVRUN_CORE_BUDGET, so "the pool is full" is reached with a few tokens.
"""
import os
import subprocess
import sys
import time

from conftest import GOVRUN, govrun

HOLD = ["sh", "-c", "echo started; sleep 30"]


def _env(budget):
    return {"GOVRUN_CORE_BUDGET": str(budget)}


def _start(state, argv, budget):
    environ = dict(os.environ, GOVRUN_STATE_DIR=str(state), **_env(budget))
    environ.pop("GOVRUN_SLOTS", None)
    environ.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.Popen([sys.executable, str(GOVRUN), *argv], env=environ,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None and proc.stderr is not None
    assert proc.stdout.readline().strip() == "started", proc.stderr.read()
    return proc


def _stop(*procs):
    for proc in procs:
        proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def test_cores_job_sees_its_count(state):
    r = govrun(state, "--cores", "3", "--", "sh", "-c", "echo $GOVRUN_CORES",
               env=_env(10))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "3"


def test_two_jobs_that_fit_run_together(state):
    first = _start(state, ["--cores", "4", "--", *HOLD], budget=10)
    try:
        r = govrun(state, "--cores", "4", "--max-wait", "1", "--", "true",
                   env=_env(10))
        assert r.returncode == 0, r.stderr
    finally:
        _stop(first)


def test_a_job_that_does_not_fit_queues_then_times_out(state):
    first = _start(state, ["--cores", "8", "--", *HOLD], budget=10)
    try:
        r = govrun(state, "--cores", "4", "--max-wait", "1", "--", "true",
                   env=_env(10))
        assert r.returncode == 75, r.stderr
        assert "8 of 10 cores are held" in r.stderr
    finally:
        _stop(first)


def test_tokens_free_when_the_holder_dies(state):
    first = _start(state, ["--cores", "8", "--", *HOLD], budget=10)
    _stop(first)
    r = govrun(state, "--cores", "8", "--max-wait", "2", "--", "true", env=_env(10))
    assert r.returncode == 0, r.stderr


def test_slot_job_also_takes_one_core_per_worker(state):
    """The worker slot (4 workers) and a video job share ONE pool: with 8 of 10
    cores held, a slot job (needs 4) must wait even though the slot is free."""
    first = _start(state, ["--cores", "8", "--", *HOLD], budget=10)
    try:
        r = govrun(state, "--max-wait", "1", "--", "true", env=_env(10))
        assert r.returncode == 75, r.stderr
        assert "cores" in r.stderr
    finally:
        _stop(first)


def test_slot_holder_counts_against_cores_jobs(state):
    slot = _start(state, ["--class", "ci", "--", *HOLD], budget=6)
    try:
        start = time.monotonic()
        r = govrun(state, "--cores", "3", "--max-wait", "1", "--", "true", env=_env(6))
        assert r.returncode == 75, r.stderr
        assert time.monotonic() - start < 10
        ok = govrun(state, "--cores", "2", "--max-wait", "1", "--", "true", env=_env(6))
        assert ok.returncode == 0, ok.stderr
    finally:
        _stop(slot)


def test_more_cores_than_the_budget_is_refused(state):
    r = govrun(state, "--cores", "11", "--", "true", env=_env(10))
    assert r.returncode == 2
    assert "budget is 10" in r.stderr


def test_bad_cores_value_is_refused(state):
    r = govrun(state, "--cores", "0", "--", "true", env=_env(10))
    assert r.returncode == 2
    assert "--cores" in r.stderr


def test_status_reports_held_cores(state):
    first = _start(state, ["--cores", "3", "--", *HOLD], budget=10)
    try:
        r = govrun(state, "--status", env=_env(10))
        assert "cores: 3 of 10 held" in r.stdout, r.stdout
    finally:
        _stop(first)
