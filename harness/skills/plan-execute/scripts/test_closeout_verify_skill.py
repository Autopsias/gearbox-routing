"""verify_skill closeout field: no invalid shape accepted, no valid shape refused (I3)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import closeout_pipeline as cp  # noqa: E402

BASE = {"session": "s02", "result": "DONE", "items_completed": ["w-01"], "items_blocked": [],
        "notes": {"w-01": "x"}}


def verify(**extra):
    return cp.semantic_verify({**BASE, **extra}, "s02", ["w-01"])["status"]


@pytest.mark.parametrize("vs", [
    {"result": "pass"},
    {"result": "pass", "reason": "ran clean", "evidence": "out.txt"},
    {"result": "fail", "reason": "10 tests failed"},
    {"result": "not_run", "reason": "diff is docs only"},
])
def test_valid_shapes_are_accepted(vs):
    assert verify(verify_skill=vs) == "ok"


def test_missing_field_is_accepted():
    assert verify() == "ok"


@pytest.mark.parametrize("vs", [
    "pass", ["pass"], None, {}, {"result": "PASS"}, {"result": "maybe"},
    {"result": "not_run"}, {"result": "not_run", "reason": "  "}, {"result": "not_run", "reason": 3},
    {"result": "fail"}, {"result": "fail", "reason": ""}, {"result": "pass", "reason": 5},
])
def test_invalid_shapes_are_violations(vs):
    got = cp.semantic_verify({**BASE, "verify_skill": vs}, "s02", ["w-01"])
    assert got["status"] == "semantic_error"
    assert any("verify_skill" in m for m in got["violations"])
