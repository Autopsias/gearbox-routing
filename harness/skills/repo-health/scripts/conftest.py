"""Fixtures for the exposure.py and check-run tests (see exposure_testlib.py)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import exposure  # noqa: E402
from exposure_testlib import (  # noqa: E402
    EXPOSED, GIT, make_repo)

# These files drive check-run against the vendored Cloudflare copy. The public
# export does not ship that copy, so there they skip instead of failing.
_NEEDS_CLOUDFLARE = {"test_check_run.py", "test_exposure.py", "test_invariant.py",
                     "test_open_set.py", "test_open_set_matrix.py", "test_reader.py"}


def pytest_collection_modifyitems(config, items):
    if exposure.VENDOR.is_dir():
        return
    skip = pytest.mark.skip(reason=f"{exposure.VENDOR} is absent (not shipped in this copy)")
    for item in items:
        if item.path.name in _NEEDS_CLOUDFLARE:
            item.add_marker(skip)


@pytest.fixture
def exposure_home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def exposure_node(tmp_path, monkeypatch):
    """PATH with git and a stub node: exit code and output chosen per test.

    The on-disk vendor tree hash is stubbed to the contract for speed; the
    real hash has its own test below."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "git").symlink_to(GIT)

    def make(code=0, msg="PASS", pre=""):
        node = bin_ / "node"
        node.write_text(f"#!/bin/sh\n{pre}\necho '{msg}' >&2\nexit {code}\n")
        node.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_))
    monkeypatch.setattr(exposure, "_vendored_tree", lambda: exposure.CONTRACT_TREE)
    make()
    return make


@pytest.fixture
def exposure_repo(tmp_path, exposure_home):
    return make_repo(tmp_path / "r", EXPOSED)
