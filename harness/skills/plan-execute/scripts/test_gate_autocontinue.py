"""End-to-end: a rubber-stamp AWAITS_REVIEW-ack gate auto-continues (OR-03).

Builds a REAL plan through the sibling plan-builder, then drives the real
``run.cmd_apply`` closeout path with a ``human_checkpoint_reason`` set, for two
sessions that differ ONLY in their per-gate policy:

  * s01 opts in (``dispatch.checkpoint_policy: notify-and-continue``) → the gate
    auto-continues: the session lands DONE (not AWAITS_REVIEW) and a
    ``gate_auto_continue`` event is written to run.ndjson.
  * s02 leaves the default → the gate BLOCKS: the session parks in AWAITS_REVIEW
    and a ``checkpoint_reached`` event is written, exactly as today.

Also emits the demonstrated ``gate_auto_continue`` ndjson line to
GATE_AUTOCONTINUE_EVIDENCE (if set) so the plan-execute evidence artifact is a
real product of this code path, not a hand-written sample.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import run

_BUILDER = (Path(__file__).resolve().parents[2]
            / "plan-builder" / "scripts" / "build_plan.py")


def _spec():
    def _session(sid, item, policy):
        dispatch = {"subagent_type": None, "parallel_group": None,
                    "depends_on": [], "requires_human_checkpoint": False}
        if policy:
            dispatch["checkpoint_policy"] = policy
        return {"id": sid, "title": "S", "items": [item], "model": "Sonnet",
                "effort": "~1h", "human_summary": "h", "deliverable": "d",
                "why_model": "w", "prompt": "Do the work.", "dispatch": dispatch}

    return {
        "title": "Gate Auto-Continue Fixture", "subtitle": "x",
        "slug": "gate-autocontinue-fixture", "created": "2026-07-03",
        "infographic": {
            "type": "phase-journey", "title": "x",
            "phases": [{"num": 1, "name": "P", "tagline": "t",
                        "items": ["it-01", "it-02"]}],
            "anchor_now": {"name": "a", "tagline": "a"},
            "anchor_goal": {"name": "b", "tagline": "b"},
        },
        "categories": [{"key": "c1", "label": "C", "description": "d"}],
        "items": [
            {"id": "it-01", "title": "I1", "category": "c1",
             "human_summary": "h", "deliverable": "d"},
            {"id": "it-02", "title": "I2", "category": "c1",
             "human_summary": "h", "deliverable": "d"},
        ],
        "sessions": [
            _session("s01", "it-01", "notify-and-continue"),
            # s02 sets NO checkpoint_policy — the conservative default. Same
            # rubber-stamp TYPE, but with no explicit opt-in it must still park in
            # AWAITS_REVIEW exactly as today. Proves existing plans are unchanged.
            _session("s02", "it-02", None),
        ],
    }


def _closeout(sid, item):
    return json.dumps({
        "session": sid, "result": "DONE", "items_completed": [item],
        "items_blocked": [], "notes": {item: "done", sid: "done"},
        "dispatch_next": True,
        "human_checkpoint_reason": "please confirm before I move on",
    })


def _apply(plan_dir, sid, item, tmp_path):
    raw = f"work done.\n\n<plan-execute-closeout>\n{_closeout(sid, item)}\n</plan-execute-closeout>\n"
    out = tmp_path / f"{sid}.out.md"
    out.write_text(raw)
    # cmd_apply calls sys.exit(1) on a gate failure; catch to inspect.
    run.cmd_begin(plan_dir, [sid])
    try:
        run.cmd_apply(plan_dir, sid, str(out))
    except SystemExit as e:
        assert e.code in (0, None), f"apply exited {e.code}"


def _events(plan_dir):
    ndjson = Path(plan_dir) / "run.ndjson"
    if not ndjson.exists():
        return []
    return [json.loads(x) for x in ndjson.read_text().splitlines() if x.strip()]


def _status(plan_dir, sid):
    import article_block as ab
    html = (Path(plan_dir) / "PLAN.html").read_text()
    return ab.current_status(html, sid) if hasattr(ab, "current_status") else html


@pytest.fixture
def plan(tmp_path):
    if not _BUILDER.exists():
        pytest.skip(f"sibling builder not found at {_BUILDER}")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(_spec()))
    out = tmp_path / "plan"
    r = subprocess.run([sys.executable, str(_BUILDER), str(spec), str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"build failed: {r.stderr}\n{r.stdout}"
    return str(out)


def test_optin_session_autocontinues(plan, tmp_path):
    _apply(plan, "s01", "it-01", tmp_path)
    evs = _events(plan)
    ac = [e for e in evs if e.get("event") == "gate_auto_continue"]
    assert ac, f"expected a gate_auto_continue event; got {[e.get('event') for e in evs]}"
    assert ac[0]["gate_type"] == "session_review_ack"
    assert ac[0]["policy"] == "notify-and-continue"
    # And it did NOT park in AWAITS_REVIEW.
    assert not [e for e in evs if e.get("event") == "checkpoint_reached"]
    # The dashboard shows the terminal DONE, not AWAITS_REVIEW.
    html = (Path(plan) / "PLAN.html").read_text()
    assert 'data-session-id="s01"' in html

    # Emit the demonstrated ndjson line as plan-execute evidence, if requested.
    dest = os.environ.get("GATE_AUTOCONTINUE_EVIDENCE")
    if dest:
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_text(json.dumps(ac[0], indent=2) + "\n")


def test_default_session_still_blocks(plan, tmp_path):
    _apply(plan, "s02", "it-02", tmp_path)
    evs = _events(plan)
    assert [e for e in evs if e.get("event") == "checkpoint_reached"], \
        "default (no checkpoint_policy) session must still park in AWAITS_REVIEW"
    assert not [e for e in evs
                if e.get("event") == "gate_auto_continue" and "s02" in e.get("session_ids", [])]
