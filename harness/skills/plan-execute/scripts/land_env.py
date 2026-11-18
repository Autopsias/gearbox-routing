"""Land's environment check: a wrong interpreter parks in seconds, not after the gates.

Gates run ``python3`` from PATH. In a repo with ``requirements-ci.txt`` they need the
environment that file builds: ``make types`` reads packages from ``sys.prefix``, so a
Homebrew Python reports false over-baseline pairs, and a bare ``python3`` has no
pytest. Each such failure cost a land run of about 50 minutes.

A repo without that file declares no such need, so the check does not apply there:
other projects' gates bring their own interpreters. The park writes no land state.
"""

import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path

import land_start as lsg
import ship_state_io as ssio

REQUIREMENTS = "requirements-ci.txt"
# outside HOME: the grader sandbox denies reads under HOME. LAND_CI_VENV lets tests point
# a run.py subprocess away from this machine's real venv.
VENV = os.environ.get("LAND_CI_VENV", "/tmp/ci-venv")
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
    """Why the gates' ``python3`` is wrong, or None. When it is wrong and the CI venv at
    ``VENV`` exists and passes the same probe, put ``VENV/bin`` first on this process's
    PATH and answer None: every later gate reads PATH from ``os.environ``. A bare
    ``python3`` parked a land once per land until then."""
    problem = _probe()
    if problem is None or not _trusted_venv(VENV):
        return problem
    before = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{VENV}/bin{os.pathsep}{before}"
    if _probe() is None:
        print(f"land_env: {problem}; using {VENV} first on PATH", file=sys.stderr)
        return None
    os.environ["PATH"] = before
    return problem


def _trusted_venv(root):
    """True when ``root`` is a real directory with ``bin/python3``, and this user owns
    every entry under it and no entry is group- or world-writable. /tmp is shared, so
    another account could plant a venv there; adopting it would run its code."""
    uid = os.getuid()
    try:
        top = os.lstat(root)
        if not stat.S_ISDIR(top.st_mode) or not (Path(root) / "bin" / "python3").exists():
            return False
        for d, dirs, files in os.walk(root):
            for p in [d] + [os.path.join(d, n) for n in dirs + files]:
                st = os.lstat(p)
                if st.st_uid != uid or (not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022):
                    return False
    except OSError:
        return False
    return True


def _probe():
    """Why ``python3`` on PATH cannot run the gates, or None. No answer in time is a
    problem: a re-run costs seconds, and a wrong environment costs a whole land."""
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


def gate_problem(cwd, argv):
    """For one SESSION verify gate: why its ``python3`` cannot run it, or None. Land
    checks once before its gates; verify-run had no check, so a bare ``python3`` failed
    the gate with "No module named pytest" and charged a rework for it.
    Same probe, only for a gate whose argv runs python3 in a repo with the requirements."""
    if not (Path(cwd) / REQUIREMENTS).is_file() or not any("python3" in str(a) for a in argv):
        return None
    problem = _problem()
    if problem is None:
        return None
    return (f"GATE NOT RUN, not charged: this python3 cannot run it ({problem}). Build the CI "
            f"environment outside HOME (python3.14 -m venv {VENV} && {VENV}/bin/python -m pip "
            f"install -r {shlex.quote(str(Path(cwd) / REQUIREMENTS))}), then re-run verify-run "
            f"with PATH={VENV}/bin:\"$PATH\".")


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
