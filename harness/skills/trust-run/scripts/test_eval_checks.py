import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval_checks as ec  # noqa: E402

CLAIM = {"file": "deck.pptx", "locator": "slide 4", "source": "budget table"}


@pytest.mark.parametrize("summary,passes", [
    ({"verdict": "ready"}, False),                                   # list absent
    ({"verdict": "ready", "not_reviewed_claims": []}, True),
    ({"verdict": "ready", "not_reviewed_claims": [CLAIM]}, False),   # unreviewed but ready
    ({"verdict": "blocked", "not_reviewed_claims": [CLAIM]}, True),
    ({"verdict": "blocked", "not_reviewed_claims": [{"file": "deck.pptx"}]}, False),
    ({"verdict": "blocked", "not_reviewed_claims": ["slide 4"]}, False),
])
def test_e5_fails_a_run_that_hides_unreviewed_claims(summary, passes):
    assert ec.check_e5(summary, None)[0] is passes
