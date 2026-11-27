"""Regression tests wiring claude/fixtures/routing-guard/ into pytest."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parents[2]
VERIFY_SH = REPO_ROOT / "claude" / "scripts" / "verify-routing.sh"
RENDER_PY = REPO_ROOT / "claude" / "scripts" / "render-routing-digest.py"
RENDERER_FIXTURES = FIXTURE_DIR / "renderer-fixtures"


@pytest.mark.parametrize(
    ("fixture_name", "expected_rc"),
    [
        ("control.yaml", 0),
        ("bare-integer-version.yaml", 2),
        ("invalid-active-provider.yaml", 1),
        ("missing-tier-in-provider.yaml", 1),
        ("unresearched-active-provider.yaml", 1),
    ],
)
def test_verify_routing_yaml_fixtures(fixture_name, expected_rc):
    env = {**os.environ, "SSOT": str(FIXTURE_DIR / fixture_name)}
    proc = subprocess.run(
        ["bash", str(VERIFY_SH), "--core"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == expected_rc, (
        f"{fixture_name}: expected rc={expected_rc}, got {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_renderer_refuses_corrupt_or_legacy_markers(tmp_path):
    # missing-markers.md without --install => exit 2
    missing = tmp_path / "missing-markers.md"
    shutil.copy2(RENDERER_FIXTURES / "missing-markers.md", missing)
    r1 = subprocess.run(
        [sys.executable, str(RENDER_PY), "--variant", "v0", "--target", str(missing)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert r1.returncode == 2, r1.stdout + r1.stderr

    # duplicate-markers.md with and without --install => exit 2
    dup = tmp_path / "duplicate-markers.md"
    shutil.copy2(RENDERER_FIXTURES / "duplicate-markers.md", dup)
    for extra in ([], ["--install"]):
        r2 = subprocess.run(
            [sys.executable, str(RENDER_PY), "--variant", "v0", "--target", str(dup), *extra],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert r2.returncode == 2, r2.stdout + r2.stderr

    # old-integer-marker.md even with --install => exit 2
    old = tmp_path / "old-integer-marker.md"
    shutil.copy2(RENDERER_FIXTURES / "old-integer-marker.md", old)
    r3 = subprocess.run(
        [sys.executable, str(RENDER_PY), "--variant", "v0", "--target", str(old), "--install"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert r3.returncode == 2, r3.stdout + r3.stderr


def test_renderer_prose_mention_is_drift_not_corruption():
    proc = subprocess.run(
        [
            sys.executable,
            str(RENDER_PY),
            "--variant", "v0",
            "--target", str(RENDERER_FIXTURES / "prose-marker-mention.md"),
            "--check",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_renderer_refuses_oversized_source_template(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(RENDER_PY),
            "--variant", "v0",
            "--source", str(RENDERER_FIXTURES / "oversized-source-template.md"),
            "--target", str(tmp_path / "fresh.md"),
            "--install",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert not (tmp_path / "fresh.md").exists()
