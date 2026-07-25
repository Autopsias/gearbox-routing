"""Vista ③ — evidence gate. A session whose verify block sets ``require_evidence``
may not finalize DONE until its closeout carries an ``evidence`` list whose every
path exists and is non-empty. This is the structural form of the project's
"verify mechanism engagement" rule: a count==0 grep is a no-op no matter what the
metrics say, so the proof artifact must be on disk before DONE is granted.

The fixture builds a real plan through the sibling ``plan-builder`` so the
test exercises the whole chain (build → manifest resolve → verify-begin →
verify-finalize), not a hand-stubbed manifest.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import closeout_pipeline as cp
import verify

_BUILDER = (Path(__file__).resolve().parents[2]
            / "plan-builder" / "scripts" / "build_plan.py")

_SPEC = {
    "title": "Evidence Gate Fixture",
    "subtitle": "x",
    "slug": "evidence-fixture",
    "created": "2026-06-22",
    "infographic": {
        "type": "phase-journey", "title": "x",
        "phases": [{"num": 1, "name": "P", "tagline": "t", "items": ["it-01"]}],
        "anchor_now": {"name": "a", "tagline": "a"},
        "anchor_goal": {"name": "b", "tagline": "b"},
    },
    "categories": [{"key": "c1", "label": "C", "description": "d"}],
    "items": [{"id": "it-01", "title": "I", "category": "c1",
               "human_summary": "h", "deliverable": "d"}],
    "sessions": [{
        "id": "s01", "title": "S", "items": ["it-01"], "model": "Sonnet",
        "effort": "~1h", "human_summary": "h", "deliverable": "d", "why_model": "w",
        "prompt": "Do the work and provide proof-of-engagement.",
        "verify": {"require_evidence": True},
        "dispatch": {"subagent_type": None, "parallel_group": None,
                     "depends_on": [], "requires_human_checkpoint": False},
    }],
}

_DONE = {"session": "s01", "result": "DONE", "items_completed": ["it-01"],
         "items_blocked": [], "notes": {"it-01": "done"}, "dispatch_next": True,
         "human_checkpoint_reason": None}


@pytest.fixture
def plan(tmp_path):
    if not _BUILDER.exists():
        pytest.skip(f"sibling builder not found at {_BUILDER}")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(_SPEC))
    out = tmp_path / "plan"
    r = subprocess.run([sys.executable, str(_BUILDER), str(spec), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"build failed: {r.stderr}\n{r.stdout}"
    return str(out)


def _begin_finalize(plan_dir):
    verify.verify_begin(plan_dir, "s01")
    return verify.verify_finalize(plan_dir, "s01")


def test_require_evidence_resolves_into_manifest(plan):
    m = json.loads(Path(plan, "manifest.json").read_text())
    s01 = next(s for s in m["sessions"] if s["id"] == "s01")
    assert s01["verify"]["require_evidence"] is True


def test_done_without_evidence_is_refused(plan):
    cp.persist(plan, "s01", _DONE)  # DONE claimed, no evidence array
    out = _begin_finalize(plan)
    assert out["action"] == "rework"
    assert out["gate"] == "evidence"


def test_done_with_present_nonempty_evidence_passes(plan, tmp_path):
    ev = tmp_path / "proof.txt"
    ev.write_text("grep -c [retrieval.engaged] => 3\n")
    cp.persist(plan, "s01", {**_DONE, "evidence": [str(ev)]})
    out = _begin_finalize(plan)
    assert out["action"] == "done"
    assert out["final_status"] == "DONE"


def test_empty_evidence_file_is_refused(plan, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.touch()
    cp.persist(plan, "s01", {**_DONE, "evidence": [str(empty)]})
    out = _begin_finalize(plan)
    assert out["action"] in ("rework", "halted")


def test_missing_evidence_path_is_refused(plan, tmp_path):
    cp.persist(plan, "s01", {**_DONE, "evidence": [str(tmp_path / "nope.txt")]})
    out = _begin_finalize(plan)
    assert out["action"] in ("rework", "halted")


def test_simulate_skips_evidence(plan):
    # CI smoke (verify-simulate) auto-passes every gate AND the evidence check,
    # so a plan with require_evidence can still be dry-run without real artifacts.
    cp.persist(plan, "s01", _DONE)
    out = verify.verify_simulate(plan, "s01")
    assert out["action"] == "done"
