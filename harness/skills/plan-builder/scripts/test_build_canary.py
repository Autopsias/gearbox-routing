#!/usr/bin/env python3
"""build_canary.apply: plan-builder writes an armed routing canary into a NEW plan's build
sessions. Each refusal sits next to a control that the same call tags, so no case can pass
by the canary never firing.

Run: pytest skills/plan-builder/scripts/test_build_canary.py -q
"""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_canary as bc  # noqa: E402
import build_plan as bp  # noqa: E402
from test_route_at_dispatch_build import _V8  # noqa: E402

PID = "sonnet-low-std-20261010"


@pytest.fixture(autouse=True)
def _canary_on(monkeypatch):
    """plan-execute's conftest turns the canary off for its tests; here it is the subject."""
    monkeypatch.delenv("PLAN_BUILDER_BUILD_CANARY", raising=False)


def _spec(**fields):
    s = copy.deepcopy(_V8)
    s01 = s["sessions"][0]  # standard_build, no model: the canary's target
    s01["verify"] = {"gates": ["code-review-gate"], "on_fail": "rework"}
    s01.update(fields)
    return s


def _ledger(stage=None):
    return lambda: ({PID: stage} if stage else {})


def _apply(spec, stage=None):
    return bc.apply(spec, ledger=_ledger(stage))


def test_a_new_build_session_is_marked_but_never_pinned():
    spec = _apply(_spec())
    s01 = spec["sessions"][0]
    assert s01["routing_experiment"] == {"kind": "canary", "proposal_id": PID}
    assert s01["verify"]["max_rework"] == 4
    # route at dispatch: the builder must leave the model to `begin`
    assert not any(s01.get(k) for k in ("model", "reasoning", "why_model"))
    assert spec["sessions"][1]["model"] == "Opus"  # the linchpin keeps its own pin
    bp.validate_spec(spec)
    m = bp.gen_manifest(spec)
    assert m["sessions"][0]["routing_experiment"]["proposal_id"] == PID
    assert m["sessions"][0]["model"] == ""


def test_an_author_choice_keeps_the_session_out():
    for fields in ({"model": "Opus", "reasoning": "high", "why_model": "x"},
                   {"peer_triggers": ["security"]}, {"escalation": False},
                   {"verify": {"gates": []}}, {"routing_experiment": {"kind": "canary", "proposal_id": "p"}}):
        s01 = _apply(_spec(**fields))["sessions"][0]
        assert s01.get("routing_experiment", {}).get("proposal_id") != PID, fields


def test_a_stopped_canary_or_unreadable_ledger_marks_nothing():
    for stage in ("smoke-failed", "adoption-ready", "adoption-evidence-insufficient"):
        assert "routing_experiment" not in _apply(_spec(), stage)["sessions"][0]
    assert "routing_experiment" not in bc.apply(_spec(), ledger=lambda: None)["sessions"][0]
    assert _apply(_spec(), "smoke-in-progress")["sessions"][0]["routing_experiment"]  # control


def test_dispatch_cell_runs_the_canary_only_while_it_runs_on_its_provider():
    tagged = {"id": "s01", "routing_experiment": {"kind": "canary", "proposal_id": PID}}
    assert bc.dispatch_cell(tagged, "anthropic", ledger=_ledger("smoke-in-progress")) == \
        ("sonnet", "low", PID)
    assert bc.dispatch_cell(tagged, "anthropic", ledger=_ledger("smoke-failed")) is None
    assert bc.dispatch_cell(tagged, "zai", ledger=_ledger()) is None
    assert bc.dispatch_cell(tagged, "anthropic", ledger=lambda: None) is None
    other = {"id": "s01", "routing_experiment": {"kind": "canary", "proposal_id": "elsewhere"}}
    assert bc.dispatch_cell(other, "anthropic", ledger=_ledger()) is None
    assert bc.dispatch_cell({"id": "s01"}, "anthropic", ledger=_ledger()) is None


def test_the_off_switch_writes_and_picks_nothing(monkeypatch):
    monkeypatch.setenv("PLAN_BUILDER_BUILD_CANARY", "off")
    assert "routing_experiment" not in _apply(_spec())["sessions"][0]
    tagged = {"id": "s01", "routing_experiment": {"kind": "canary", "proposal_id": PID}}
    assert bc.dispatch_cell(tagged, "anthropic", ledger=_ledger()) is None


def test_the_real_ledger_reader_answers():
    bc._ledger.cache_clear()
    assert isinstance(bc._ledger(), dict)
