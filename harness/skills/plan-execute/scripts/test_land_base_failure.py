"""LND-03 — a gate red on the base too: the park says the plan is innocent only when
the failures match. On 2026-11-06 a plan's own lint error got that verdict because
its base run failed for another cause.

    pytest skills/plan-execute/scripts/test_land_base_failure.py -q
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_repair as lrp  # noqa: E402
import land_state as lst  # noqa: E402
from _land_fixture import build_repo, isolate, run_cli, work  # noqa: E402


def _gate(tmp, body):
    """An at_land argv gate that logs its directory, so base runs can be counted."""
    return {"kind": "argv", "cwd": ".", "timeout": 60, "at_land": True,
            "argv": ["/bin/sh", "-c", f'echo "$PWD" >> {tmp}/runs.txt; {body}']}


def _base_runs(tmp):
    return sum("__base-" in ln for ln in (Path(tmp) / "runs.txt").read_text().splitlines())


def _land(iso):
    return run_cli("land", iso["plan_dir"])


def _state(iso):
    return lst.load(iso["plan_dir"])


def _differs(tmp_path):
    """The 2026-11-06 shape: the plan's file fails lint; the base fails for its own cause."""
    body = ('if [ -f src/plan_file.py ]; then echo "src/plan_file.py:1:1: F401 unused import"; '
            'else echo "ModuleNotFoundError: env is broken"; fi; exit 1')
    fx = build_repo(tmp_path, gates={"ci-gate": _gate(tmp_path, body)}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/plan_file.py", "import os\n")
    return iso


def test_a_base_failure_with_another_cause_never_calls_the_plan_innocent(tmp_path):
    iso = _differs(tmp_path)
    for _ in range(2):                      # the cached base run says the same
        rc, out = _land(iso)
        assert (rc, out["action"], out["kind"]) == (1, "land-parked", "gate-base-differs"), out
        assert out["inherited"] == [] and out["base_differs"] == ["ci-gate"]
        assert "did not break" not in out["brief"] and "repair" not in _state(iso)
        assert "F401 unused import" in out["brief"]
        assert "env is broken" in out["brief"]      # the base's own excerpt
        assert _base_runs(tmp_path) == 1


def test_a_base_fail_cached_without_its_output_runs_again(tmp_path):
    iso = _differs(tmp_path)
    _land(iso)
    st = _state(iso)
    for rec in st["base_gate_cache"].values():
        del rec["failure"]                  # a land.json from before the output was kept
    lst.save(iso["plan_dir"], lst.context(iso["plan_dir"]), st)
    out = _land(iso)[1]
    assert out["kind"] == "gate-base-differs" and "env is broken" in out["brief"], out
    assert _base_runs(tmp_path) == 2


def test_silent_failures_on_both_sides_prove_nothing(tmp_path):
    fx = build_repo(tmp_path, gates={"ci-gate": _gate(tmp_path, "exit 1")}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    out = _land(iso)[1]
    assert out["kind"] == "gate-base-differs" and "did not break" not in out["brief"], out


def test_same_failures_ignores_noise_and_allows_a_subset():
    same = lrp._same_failures
    assert same({"tests": ["a"], "excerpt": "x", "fingerprint": "1"},
                {"tests": ["b", "a"], "excerpt": "y", "fingerprint": "2"})
    assert not same({"tests": ["a", "c"], "excerpt": "x", "fingerprint": "1"},
                    {"tests": ["a"], "excerpt": "x", "fingerprint": "1"})
    assert not same({"tests": [], "excerpt": "x", "fingerprint": "1"},
                    {"tests": ["a"], "excerpt": "x", "fingerprint": "2"})
    assert lrp._failing({"gate_id": "g", "outcome": "fail", "excerpt": "/land/x: E1 at 1.2 s",
                         "cwd": "/land"}, None)["fingerprint"] == lrp._failing(
        {"gate_id": "g", "outcome": "fail", "excerpt": "/base/x: E1 at 3.4 s",
         "cwd": "/base"}, None)["fingerprint"]
