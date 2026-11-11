"""Digest page theme: neutral by default, the owner's palette only through the private pack."""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import human_gate_digest as H  # noqa: E402

RESULT = {"run_id": "fx", "findings": [
    {"id": "F1", "type": "numeric", "severity": "must-fix", "evidence": "x", "location": "s3"}]}
PRIVATE = os.path.join(HERE, "..", "references", "scripts", "provider_pack_private.py")
OWNER = ("#FF5900", "#C84600", "#FF7A33")


def _digest():
    return H.build_digest(RESULT, travel_class="board")


def test_neutral_without_pack_has_no_owner_colour(tmp_path):
    # a copy of the published scripts with no pack beside them: the registry names no private tool
    shutil.copytree(HERE, tmp_path / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(HERE, "..", "references"), tmp_path / "references",
                    ignore=shutil.ignore_patterns("__pycache__", "provider_pack_private.py",
                                                  "test_provider_pack_private*"))
    code = ("import sys; sys.path.insert(0,'scripts'); import human_gate_digest as H;"
            "d=H.build_digest({'run_id':'fx','findings':[]}); print(H.render_digest_html(d))")
    html = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True,
                          text=True, check=True).stdout
    assert "--accent:#2F5D8A" in html
    assert not any(c.lower() in html.lower() for c in OWNER)


def test_explicit_theme_overrides_and_bad_values_are_dropped():
    html = H.render_digest_html(_digest(), {"--accent": "#123456", "--accent-dark": "red;}x{"})
    assert "--accent:#123456" in html and "--accent-dark:#1F4468" in html


@pytest.mark.skipif(not os.path.exists(PRIVATE), reason="private pack not installed")
def test_private_pack_carries_the_owner_palette():
    html = H.render_digest_html(_digest())
    assert "--accent:#FF5900" in html and "--accent-dark:#C84600" in html
    assert "--accent:#FF7A33" in html
    assert "--accent:#FF5900" not in H.render_digest_html(_digest(), {})
