#!/usr/bin/env python3
"""The explainer section ("The plan, explained") — validation + render.

The field is optional: absence must build byte-identical behaviour (no section,
no refusal). Presence must be validated (unknown session ids, unqualified
analogy, one-step chain) and rendered with the data attributes the template's
static renderExplainer() reads at load time. Every refusal is asserted in both
directions — the same spec minus the defect must pass — so a test here cannot
go green by the validator refusing everything.

Run: pytest skills/plan-builder/scripts/test_explainer.py -q
"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import explainer  # noqa: E402

_SPEC = {
    "title": "Explainer fixture",
    "categories": [{"key": "c", "label": "C"}],
    "items": [{"id": "it-1", "title": "IT-1", "category": "c"}],
    "sessions": [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["it-1"],
         "prompt": "do the thing", "dispatch": {"subagent_type": None, "depends_on": []}},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["it-1"],
         "prompt": "do the other thing",
         "dispatch": {"subagent_type": None, "depends_on": ["s01"]}},
    ],
    "infographic": {"type": "phase-journey"},
    "explainer": {
        "chain": [
            {"label": "Decide", "sessions": ["s01"],
             "caption": "Freeze the shape.", "unlocks": "building can start"},
            {"label": "Build", "sessions": ["s02"],
             "caption": "Make the thing."},
        ],
        "analogy": {"text": "Like a bridge.", "breaks": "Spans here are cheap to redo."},
        "terms": [{"name": "session", "definition": "one bounded unit of work"}],
        "recap": "Decide, then build.",
    },
}


def _spec(**mutate):
    s = copy.deepcopy(_SPEC)
    for k, v in mutate.items():
        s[k] = v
    return s


def test_absent_is_valid_and_renders_nothing():
    s = _spec()
    del s["explainer"]
    explainer.validate_explainer(s)  # must not raise
    assert explainer.render_explainer_section(s) == ""


def test_valid_spec_passes_and_renders():
    s = _spec()
    explainer.validate_explainer(s)
    html = explainer.render_explainer_section(s)
    assert '<section class="plan-explained"' in html
    assert 'data-sessions="s01"' in html
    assert 'data-label="Decide"' in html
    assert 'data-unlocks="building can start"' in html
    assert "Where the analogy stops" in html
    assert 'data-recap="Decide, then build."' in html
    # the live half's targets exist for renderExplainer()
    assert 'id="explainer-now-text"' in html


def test_unknown_session_id_refused():
    s = _spec()
    s["explainer"]["chain"][0]["sessions"] = ["s99"]
    with pytest.raises(ValueError, match="s99"):
        explainer.validate_explainer(s)


def test_unqualified_analogy_refused():
    s = _spec()
    del s["explainer"]["analogy"]["breaks"]
    with pytest.raises(ValueError, match="breaks"):
        explainer.validate_explainer(s)
    # analogy may be omitted entirely — only the half-analogy is refused
    del s["explainer"]["analogy"]
    explainer.validate_explainer(s)


def test_one_step_chain_refused():
    s = _spec()
    s["explainer"]["chain"] = s["explainer"]["chain"][:1]
    with pytest.raises(ValueError, match="at least 2"):
        explainer.validate_explainer(s)


def test_term_without_definition_refused():
    s = _spec()
    s["explainer"]["terms"] = [{"name": "session"}]
    with pytest.raises(ValueError, match="definition"):
        explainer.validate_explainer(s)


def test_markup_is_escaped():
    s = _spec()
    s["explainer"]["chain"][0]["label"] = '<img src=x onerror=1> "quoted"'
    explainer.validate_explainer(s)
    html = explainer.render_explainer_section(s)
    assert "<img" not in html
    assert "&lt;img" in html
    assert 'data-label="&lt;img src=x onerror=1&gt; &quot;quoted&quot;"' in html


def test_uncovered_sessions_warn_but_pass(capsys):
    s = _spec(sessions=_SPEC["sessions"] + [{"id": "s03", "title": "S3"}])
    explainer.validate_explainer(s)  # s03 in no chain step: advisory only
    assert "s03" in capsys.readouterr().err


def test_preserve_state_rebuild_keeps_schema_stamp(tmp_path):
    """A retrofit rebuild must not bump the plan's published contract: the
    manifest stamp gates runtime behaviour (isolation at >=7), so carrying it
    is what makes --preserve-state safe on a mid-run plan. Both directions:
    with the flag the old stamp survives; without it the rebuild upgrades."""
    import json
    import build_plan as bp

    spec = _spec()
    del spec["explainer"]  # start as a plan built before the feature
    plan_dir = tmp_path / "plan"
    bp.build(spec, plan_dir)
    m = json.loads((plan_dir / "manifest.json").read_text())
    assert m["plan_schema_version"] == bp.rad.PRE_V8_STAMP  # a spec below v8 stamps 7
    # simulate an executed plan built under an older contract
    m["plan_schema_version"] = 6
    (plan_dir / "manifest.json").write_text(json.dumps(m, indent=2))
    html = (plan_dir / "PLAN.html").read_text()
    (plan_dir / "PLAN.html").write_text(
        html.replace('data-status="TODO"', 'data-status="DONE"', 1))

    spec["explainer"] = copy.deepcopy(_SPEC["explainer"])
    bp.build(spec, plan_dir, preserve_state=True)
    m2 = json.loads((plan_dir / "manifest.json").read_text())
    assert m2["plan_schema_version"] == 6, "retrofit bumped the plan's contract"
    assert '<section class="plan-explained"' in (plan_dir / "PLAN.html").read_text()

    # a deliberate upgrade (no --preserve-state, statuses reset) still bumps
    (plan_dir / "PLAN.html").unlink()
    bp.build(spec, plan_dir)
    m3 = json.loads((plan_dir / "manifest.json").read_text())
    assert m3["plan_schema_version"] == bp.rad.PRE_V8_STAMP
