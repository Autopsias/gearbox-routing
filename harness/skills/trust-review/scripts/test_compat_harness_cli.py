"""compat_harness.py CLI: both documented flag spellings reach run_compat.

Run as a subprocess so the harness's sys.path insert never leaks into the suite
(the same isolation test_canonical_adapters.py keeps).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent / "references" / "scripts" / "compat_harness.py"


@pytest.mark.parametrize("form", ["space", "equals"])
def test_trust_root_flag_is_honoured(tmp_path: Path, form: str) -> None:
    flag = ["--trust-root", str(tmp_path)] if form == "space" else [f"--trust-root={tmp_path}"]
    proc = subprocess.run(
        [sys.executable, str(HARNESS), *flag], capture_output=True, text=True, timeout=60
    )
    # An empty root is a FAIL verdict (exit 1), not a crash (a traceback, exit 1 too).
    assert "Traceback" not in proc.stderr, proc.stderr
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f'"trust_root": "{tmp_path}"' in proc.stdout
    assert "COMPAT GATE: FAIL (0 runs" in proc.stdout
