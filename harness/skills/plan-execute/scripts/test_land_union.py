"""union_gates — the land re-gate's gate set (§4.4 "the plan's union of verify
gates"). Pure-dict tests, no git fixture; extracted from test_land.py at its
size bound (2026-08-27).

    pytest plan-execute/scripts/test_land_union.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import land  # noqa: E402


def test_union_gates_dedupes_across_sessions():
    m = {"sessions": [{"id": "s01", "verify": {"gates": ["b", "a"]}},
                      {"id": "s02", "verify": {"gates": ["a", "c"]}},
                      {"id": "s03"}]}
    assert land.union_gates(m) == ["a", "b", "c"]
    assert land.union_gates({"sessions": []}) == []


def test_union_gates_keeps_only_the_highest_level_per_reviewer_family():
    m = {"sessions": [
        {"id": "s01", "verify": {"gates": ["code-review-gate", "llm-review-medium"]}},
        {"id": "s02", "verify": {"gates": ["code-review-gate", "llm-review-low"]}},
        {"id": "s03", "verify": {"gates": ["cross-family-review-low"]}},
    ]}
    # medium subsumes low within llm-review; cross-family is its OWN family and
    # its sole declared level survives; the deterministic gate is untouched.
    assert land.union_gates(m) == [
        "code-review-gate", "cross-family-review-low", "llm-review-medium"]
    # KNOWN NEGATIVE — a lone level is never dropped.
    solo = {"sessions": [{"id": "s01", "verify": {"gates": ["llm-review-low"]}}]}
    assert land.union_gates(solo) == ["llm-review-low"]
