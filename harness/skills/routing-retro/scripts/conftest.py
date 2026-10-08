"""Shared fixtures for the routing-retro suite.

`ssot_path` lives here rather than in a helper module because an IMPORTED
fixture is shadowed by the test's own parameter of the same name; conftest is
where pytest resolves it without anyone importing anything.
"""
import pytest

from retro_test_helpers import SSOT_FIXTURE


@pytest.fixture
def ssot_path(tmp_path):
    p = tmp_path / "model-routing.yaml"
    p.write_text(SSOT_FIXTURE)
    return p
