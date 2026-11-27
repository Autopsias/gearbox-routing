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
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import land_start as lsg
import ship_state_io as ssio

REQUIREMENTS = "requirements-ci.txt"
# Under ~/.gearbox-state, not /tmp: /tmp/ci-venv lost its pyvenv.cfg twice and
# nothing built it again. LAND_CI_VENV moves it (tests point a run.py subprocess away from this
# machine's real venv; a sandbox that denies reads under HOME needs a path outside it).
VENV = os.environ.get("LAND_CI_VENV") or os.path.expanduser("~/.gearbox-state/ci-venv")
NO_BUILD_ENV = "LAND_CI_VENV_NO_BUILD"      # set (any value): park, never build
BUILD_TIMEOUT_S = 900
_ATTEMPTED = {}     # (venv, requirements) -> the build's failure text, or None: one build a process
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
print(sys.prefix)
"""


def _problem(requirements=None):
    """Why the gates' ``python3`` is wrong, or None. When it is wrong and the CI venv at
    ``VENV`` exists and passes the same probe, put ``VENV/bin`` first on this process's
    PATH and answer None: every later gate reads PATH from ``os.environ``. Also sets
    ``PYRES_VENV`` to the checked venv so ``scripts/pyres`` inside linked worktrees uses
    the Python that passed the probe. A bare ``python3`` parked a land once per land until
    then. When the venv is missing or broken and ``requirements`` names a
    file, build it once and probe again; a build that fails parks, with its output."""
    problem, prefix = _probe()
    if problem is None:
        if prefix:
            os.environ["PYRES_VENV"] = prefix
        return None
    if _adopt(problem):
        return None
    if requirements is None or os.environ.get(NO_BUILD_ENV):
        return problem
    failed = _build(VENV, requirements)
    if failed is None and _adopt(problem):
        return None
    return f"{problem}; building {VENV} failed: {failed or 'it still fails the probe'}"


def _adopt(problem):
    """True once ``VENV/bin`` is first on PATH and the probe passes; PATH is left as it was
    on False."""
    if not _trusted_venv(VENV):
        return False
    before = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{VENV}/bin{os.pathsep}{before}"
    err, prefix = _probe()
    if err is None:
        os.environ["PYRES_VENV"] = prefix or VENV
        print(f"land_env: {problem}; using {VENV} first on PATH", file=sys.stderr)
        return True
    os.environ["PATH"] = before
    return False


def _build(root, requirements):
    """None when the venv at ``root`` was built, else why not. Once per process and path."""
    key = (root, str(requirements))
    if key not in _ATTEMPTED:
        _ATTEMPTED[key] = _build_once(Path(root), requirements)
    return _ATTEMPTED[key]


def _build_once(root, requirements):
    """``python3.14 -m venv root`` then ``pip install -r requirements`` (CI builds from 3.14;
    a bare python3 may be newer than the pins). A broken venv already there is moved aside, never
    deleted: it may hold what someone was debugging."""
    py = shutil.which("python3.14") or "python3"
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        if root.exists() or root.is_symlink():
            root.rename(root.with_name(f"{root.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}"))
        steps = ([py, "-m", "venv", str(root)],
                 [str(root / "bin" / "python"), "-m", "pip", "install", "--disable-pip-version-check",
                  "-r", str(requirements)])
        for argv in steps:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=BUILD_TIMEOUT_S,
                                 env=ssio.build_allowlisted_env())
            if res.returncode != 0:
                return f"{' '.join(argv[:3])} exited {res.returncode}: {(res.stderr or res.stdout).strip()[-600:]}"
    except subprocess.TimeoutExpired:
        return f"timed out after {BUILD_TIMEOUT_S} s"
    except OSError as e:
        return str(e)
    return None


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
    """``(problem, prefix)`` for ``python3`` on PATH: ``(None, sys.prefix)`` when it can
    run the gates, else ``(reason, None)``. No answer in time is a problem: a re-run
    costs seconds, and a wrong environment costs a whole land."""
    try:
        res = subprocess.run(["python3", "-I", "-c", _PROBE], capture_output=True, text=True,
                             env=ssio.build_allowlisted_env(), timeout=TIMEOUT_S)
    except OSError as e:
        return f"python3 did not start: {e}", None
    except subprocess.TimeoutExpired:
        return f"python3 did not answer in {TIMEOUT_S} s", None
    if res.returncode == 0:
        lines = res.stdout.strip().splitlines()
        return None, (lines[-1] if lines else None)
    return (res.stderr.strip().splitlines() or [f"python3 exited {res.returncode}"])[-1], None


def gate_problem(cwd, argv):
    """For one SESSION verify gate: why its ``python3`` cannot run it, or None. Land
    checks once before its gates; verify-run had no check, so a bare ``python3`` failed
    the gate with "No module named pytest" and charged a rework for it.
    Same probe, only for a gate whose argv runs python3 in a repo with the requirements."""
    if not (Path(cwd) / REQUIREMENTS).is_file() or not any("python3" in str(a) for a in argv):
        return None
    problem = _problem(Path(cwd) / REQUIREMENTS)
    if problem is None:
        return None
    return (f"GATE NOT RUN, not charged: this python3 cannot run it ({problem}). Build the CI "
            f"environment outside HOME (python3.14 -m venv {VENV} && {VENV}/bin/python -m pip "
            f"install -r {shlex.quote(str(Path(cwd) / REQUIREMENTS))}), then re-run verify-run "
            f"with PATH={VENV}/bin:\"$PATH\".")


def check(plan_dir, ctx):
    """A ``land-parked`` result when the gates' interpreter cannot run them, else None."""
    requirements = Path(ctx["plan_tree"]) / REQUIREMENTS
    problem = _problem(requirements) if requirements.is_file() else None
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
