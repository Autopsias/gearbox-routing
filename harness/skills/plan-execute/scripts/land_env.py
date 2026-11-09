"""Land's environment check: a wrong interpreter parks in seconds, not after the gates.

Gates run ``python3`` from PATH. In a repo with ``requirements-ci.txt`` they need the
environment that file builds: ``make types`` reads packages from ``sys.prefix``, so a
Homebrew Python reports false over-baseline pairs, and a bare ``python3`` has no
pytest. Each such failure cost a land run of about 50 minutes.

A repo without that file declares no such need, so the check does not apply there:
other projects' gates bring their own interpreters. The park writes no land state.
"""

import shlex
import subprocess
from pathlib import Path

import land_start as lsg
import ship_state_io as ssio

REQUIREMENTS = "requirements-ci.txt"
VENV = "/tmp/ci-venv"       # outside HOME: the grader sandbox denies reads under HOME
# A cold probe took 0.15-0.87 s at load 22-43, yet 2 s timed out once on
# the shared CI runner. 30 s still parks a hung python3 long before a 50-minute land.
TIMEOUT_S = 30
_PROBE = """
import sys
from pathlib import Path
if sys.prefix == sys.base_prefix:
    sys.exit(f"{sys.executable} is not in a virtual environment (sys.prefix is {sys.prefix})")
try:
    import pytest
except Exception as e:
    sys.exit(f"{sys.executable} cannot import pytest: {e}")
if not Path(pytest.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
    sys.exit(f"pytest comes from {pytest.__file__}, outside the virtual environment {sys.prefix}")
"""


def _problem():
    """Why the gates' ``python3`` is wrong, or None. No answer in time is a problem:
    a re-run costs seconds, and a wrong environment costs a whole land."""
    try:
        res = subprocess.run(["python3", "-I", "-c", _PROBE], capture_output=True, text=True,
                             env=ssio.build_allowlisted_env(), timeout=TIMEOUT_S)
    except OSError as e:
        return f"python3 did not start: {e}"
    except subprocess.TimeoutExpired:
        return f"python3 did not answer in {TIMEOUT_S} s"
    if res.returncode == 0:
        return None
    return (res.stderr.strip().splitlines() or [f"python3 exited {res.returncode}"])[-1]


def check(plan_dir, ctx):
    """A ``land-parked`` result when the gates' interpreter cannot run them, else None."""
    requirements = Path(ctx["plan_tree"]) / REQUIREMENTS
    problem = _problem() if requirements.is_file() else None
    if problem is None:
        return None
    # python3.14 is what .github/workflows builds its venv from; .type-exceptions matches it.
    return {"action": "land-parked", "kind": "land-environment", "problem": problem,
            "brief": (f"LAND PARKED — the gates cannot run with this python3. {problem}.\n\n"
                      "No gate ran and nothing was pushed. Build the CI virtual environment "
                      "outside HOME, then land with it first on PATH:\n\n"
                      f"  python3.14 -m venv {VENV} && {VENV}/bin/python -m pip install -r "
                      f"{shlex.quote(str(requirements))}\n"
                      f"  PATH={VENV}/bin:\"$PATH\" {lsg.land_cmd(plan_dir)}")}
