"""Self-check for agent-janitor-sessionend.py.

Exercises the hook wrapper's stdin-JSON contract and its "never raise" rule
in isolation from agent_janitor.py itself -- exact-mapping session scoping is
already covered end-to-end in scripts/test_agent_janitor.py.
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "agent-janitor-sessionend.py"


def run(payload) -> subprocess.CompletedProcess:
    stdin = json.dumps(payload) if payload is not None else ""
    return subprocess.run([sys.executable, str(HOOK)], input=stdin,
                          capture_output=True, text=True, timeout=15)


def test_missing_session_id_is_a_clean_noop():
    r = run({})
    assert r.returncode == 0


def test_malformed_json_is_a_clean_noop_never_raises():
    r = run(None)  # empty stdin -> "{}" default inside the hook
    assert r.returncode == 0
    r2 = subprocess.run([sys.executable, str(HOOK)], input="not json{{{",
                        capture_output=True, text=True, timeout=15)
    assert r2.returncode == 0


def test_non_string_session_id_is_a_clean_noop():
    r = run({"session_id": 12345})
    assert r.returncode == 0


def test_valid_session_id_spawns_the_janitor_and_exits_zero():
    """Uses a real (never-existing) id -- agent_janitor.py itself refuses it
    non-zero internally, but this wrapper never propagates that; a
    SessionEnd hook must never fail session shutdown."""
    r = run({"session_id": "sessionend-hook-selfcheck-does-not-exist"})
    assert r.returncode == 0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
