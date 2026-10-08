"""`land_covered_by` — a plan gate an at_land gate covers does not run twice at land.

Real git, real `run.py land`, on the shared `_land_fixture`. The plan-declared
`marker-gate` FAILS here, so a land that still ran it could never reach review.

    pytest skills/plan-execute/scripts/test_land_covered.py -q
"""

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_state as lst  # noqa: E402
from _land_fixture import build_repo, isolate, run_cli, work  # noqa: E402

REG = ".claude/eval-gates.json"
CI = {"kind": "argv", "argv": ["/bin/sh", "-c", "true"], "cwd": ".", "timeout": 60,
      "at_land": True}


@pytest.fixture
def fx(tmp_path):
    return build_repo(tmp_path)


def _land(fx, covered_by, candidate, outer_cover=CI):
    """The outer (trusted) registry marks the plan's failing marker-gate as covered by
    ``covered_by`` and holds ``outer_cover`` under that id; the candidate tree's
    registry is ``candidate``."""
    (Path(fx["root"]) / REG).write_text(json.dumps(
        {"marker-gate": {"kind": "argv", "argv": ["/bin/sh", "-c", "exit 1"], "cwd": ".",
                         "timeout": 60, "land_covered_by": covered_by},
         covered_by: outer_cover}))
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    work(iso["tree"], REG, json.dumps(candidate))
    rc, out = run_cli("land", iso["plan_dir"])
    st = lst.load(iso["plan_dir"])
    st["_plan_dir"] = iso["plan_dir"]
    return out, {g["gate_id"]: g for g in st.get("gates") or []}, st


def test_a_gate_covered_by_an_at_land_gate_does_not_run(fx):
    out, gates, st = _land(fx, "ci-gate", {"ci-gate": CI})
    assert out["action"] == "land-awaits-review", out
    assert "marker-gate" not in gates and gates["ci-gate"]["outcome"] == "pass"
    assert st["covered"] == {"marker-gate": "ci-gate"}
    import gate_durations as gd                  # the land logs each gate's run time
    assert len(gd.runs(Path(st["_plan_dir"]).parent.parent)["ci-gate"]) == 1


def test_a_cover_that_is_not_an_at_land_gate_is_ignored(fx):
    # ci-gate exists but is not at_land: it covers nothing, so marker-gate runs and fails.
    out, gates, st = _land(fx, "ci-gate", {"ci-gate": {**CI, "at_land": False}})
    assert out["action"] != "land-awaits-review", out
    assert gates["marker-gate"]["outcome"] == "fail" and st["covered"] == {}


def test_a_plan_cannot_rewrite_the_cover_to_skip_a_gate(fx):
    # The plan's own tree swaps ci-gate's command for a different one. The trusted
    # registry still holds the real command, so the cover does not count.
    hijack = {**CI, "argv": ["/bin/sh", "-c", "echo skipped"]}
    out, gates, st = _land(fx, "ci-gate", {"ci-gate": hijack})
    assert out["action"] != "land-awaits-review", out
    assert gates["marker-gate"]["outcome"] == "fail" and st["covered"] == {}


def test_a_plan_cannot_change_another_field_of_the_cover(fx):
    # Same argv, but the plan's tree changes another field of the covering gate.
    out, gates, st = _land(fx, "ci-gate", {"ci-gate": {**CI, "indeterminate_exit": 3}})
    assert out["action"] != "land-awaits-review", out
    assert gates["marker-gate"]["outcome"] == "fail" and st["covered"] == {}
