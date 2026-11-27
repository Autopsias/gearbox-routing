"""Land's environment check, with real interpreters and real virtual environments.

The land fixtures hold no requirements-ci.txt, so every other land test runs with
whatever python3 is on PATH; test_no_requirements_file_means_no_check proves that.

    pytest skills/plan-execute/scripts/test_land_env.py -q
"""

import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_env as lev  # noqa: E402
from _land_fixture import build_repo, isolate, origin_main, run_cli, work  # noqa: E402


@pytest.fixture(autouse=True)
def no_ci_venv(tmp_path, monkeypatch):
    """The real /tmp/ci-venv on this machine would turn every park below into a pass."""
    monkeypatch.setattr(lev, "VENV", str(tmp_path / "no-ci-venv"))
    monkeypatch.setenv("LAND_CI_VENV", lev.VENV)    # and for run.py subprocesses
    monkeypatch.setenv(lev.NO_BUILD_ENV, "1")       # no test builds a real venv (pip, network)
    monkeypatch.setenv("PYRES_VENV", "")            # record pre-test state so teardown undoes land_env writes
    monkeypatch.delenv("PYRES_VENV", raising=False)
    monkeypatch.setattr(lev, "_ATTEMPTED", {})


@pytest.fixture
def tree(tmp_path):
    (tmp_path / lev.REQUIREMENTS).write_text("pytest\n")
    return tmp_path


def _check(tree):
    return lev.check("/plans/p", {"plan_tree": str(tree)})


def _venv(path, pytest_file=True):
    """A real venv without pip; ``pytest_file`` puts a stand-in pytest in its site-packages."""
    venv.create(path, with_pip=False)
    site = next(Path(path).glob("lib/python*/site-packages"))
    if pytest_file:
        (site / "pytest.py").write_text("")
    return Path(path), site


def test_no_requirements_file_means_no_check(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))       # no python3 at all: a probe would park
    assert _check(tmp_path) is None


def test_a_venv_that_resolves_pytest_passes(tree, tmp_path, monkeypatch):
    env, _ = _venv(tmp_path / "v")
    monkeypatch.setenv("PATH", str(env / "bin"))
    assert _check(tree) is None


def test_an_interpreter_outside_a_venv_parks_or_switches_to_the_ci_venv(tree, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.base_prefix) / "bin"))
    out = _check(tree)
    assert (out["action"], out["kind"]) == ("land-parked", "land-environment"), out
    assert "not in a virtual environment" in out["problem"]
    assert f"python3.14 -m venv {lev.VENV} && {lev.VENV}/bin/python -m pip install -r " in out["brief"]
    assert str(tree / lev.REQUIREMENTS) in out["brief"]
    assert f'PATH={lev.VENV}/bin:"$PATH" run.py land /plans/p' in out["brief"]
    bare = str(Path(sys.base_prefix) / "bin")
    _venv(lev.VENV, pytest_file=False)               # a CI venv that cannot run the gates
    assert "not in a virtual environment" in _check(tree)["problem"]
    assert os.environ["PATH"] == bare               # a failed switch leaves PATH as it was
    site = next(Path(lev.VENV).glob("lib/python*/site-packages"))
    (site / "pytest.py").write_text("")
    site.chmod(0o777)                               # another user could plant code here
    assert "not in a virtual environment" in _check(tree)["problem"]
    assert os.environ["PATH"] == bare
    site.chmod(0o755)
    assert _check(tree) is None                     # a working, private CI venv: no park
    assert os.environ["PATH"] == f"{lev.VENV}/bin{os.pathsep}{bare}"


def test_a_venv_without_pytest_parks(tree, tmp_path, monkeypatch):
    env, _ = _venv(tmp_path / "v", pytest_file=False)
    monkeypatch.setenv("PATH", str(env / "bin"))
    assert "cannot import pytest" in _check(tree)["problem"]


def test_a_pytest_from_outside_the_venv_parks(tree, tmp_path, monkeypatch):
    env, site = _venv(tmp_path / "v", pytest_file=False)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pytest.py").write_text("")
    (site / "out.pth").write_text(f"{outside}\n")
    monkeypatch.setenv("PATH", str(env / "bin"))
    assert "outside the virtual environment" in _check(tree)["problem"]


def test_no_python3_parks(tree, tmp_path, monkeypatch):
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert "did not start" in _check(tree)["problem"]


def test_a_python3_that_hangs_parks_at_the_time_limit(tree, tmp_path, monkeypatch):
    stub = tmp_path / "bin" / "python3"
    stub.parent.mkdir()
    stub.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(stub.parent))
    monkeypatch.setattr(lev, "TIMEOUT_S", 1)        # the real limit is 30 s; the stub sleeps 30
    assert _check(tree)["problem"] == "python3 did not answer in 1 s"


def test_a_session_gate_with_a_bare_python3_is_not_run_and_not_charged(tree, tmp_path, monkeypatch):
    import plan_scope
    import verify_pass
    ran = tmp_path / "ran.txt"
    gate = {"name": "g", "argv": ["/bin/sh", "-c", f"python3 -c 0; touch {ran}"]}
    monkeypatch.setattr(plan_scope, "gate_cwd", lambda *a: str(tree))
    monkeypatch.setenv("PATH", f"{Path(sys.base_prefix) / 'bin'}:/usr/bin:/bin")
    res = verify_pass.run_argv(str(tmp_path), "s01", gate, dry_run=False)
    assert res["returncode"] is None and "GATE NOT RUN" in res["stderr"], res
    assert f"python3.14 -m venv {lev.VENV}" in res["stderr"] and not ran.exists()
    (tmp_path / "plain").mkdir()
    assert lev.gate_problem(tmp_path / "plain", gate["argv"]) is None  # no requirements file
    assert lev.gate_problem(tree, ["/bin/sh", "-c", "true"]) is None  # gate runs no python3

class _Build:
    """The subprocess edge of a venv build: `-m venv` makes a real venv (a stand-in pytest in
    it, no pip), `pip install` is recorded, answers with `pip_rc` and `pip_err`; every other call
    (the probe) runs for real."""

    def __init__(self, monkeypatch, pip_rc=0, pip_err=""):
        self.calls, self.pip_rc, self.pip_err, self.real = [], pip_rc, pip_err, subprocess.run
        monkeypatch.delenv(lev.NO_BUILD_ENV)
        monkeypatch.setattr(lev.subprocess, "run", self)

    def __call__(self, argv, *a, **kw):
        if argv[1:3] == ["-m", "venv"]:
            self.calls.append(argv)
            _venv(argv[3], pytest_file=self.pip_rc == 0)    # a failed pip leaves no pytest
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["-m", "pip"]:
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, self.pip_rc, "", self.pip_err)
        return self.real(argv, *a, **kw)


def test_the_default_venv_is_under_dyno_not_tmp():
    env = {k: v for k, v in os.environ.items() if k != "LAND_CI_VENV"}
    out = subprocess.run([sys.executable, "-c", "import land_env; print(land_env.VENV)"],
                         env=env, cwd=SCRIPTS, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == os.path.expanduser("~/.gearbox-state/ci-venv")


def test_a_broken_venv_is_moved_aside_and_rebuilt_from_the_requirements(tree, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.base_prefix) / "bin"))
    _venv(lev.VENV, pytest_file=False)                  # a venv that cannot run the gates
    (Path(lev.VENV) / "pyvenv.cfg").unlink()            # what /tmp/ci-venv lost twice
    build = _Build(monkeypatch)
    assert _check(tree) is None
    assert [c[1:3] for c in build.calls] == [["-m", "venv"], ["-m", "pip"]]
    assert build.calls[1][-2:] == ["-r", str(tree / lev.REQUIREMENTS)]
    assert os.environ["PATH"].startswith(f"{lev.VENV}/bin{os.pathsep}")
    assert len(list(Path(lev.VENV).parent.glob("no-ci-venv.broken-*"))) == 1   # kept, not deleted
    assert _check(tree) is None and len(build.calls) == 2                      # a working venv: no second build


def test_a_good_venv_is_adopted_without_a_build(tree, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.base_prefix) / "bin"))
    _venv(lev.VENV)
    build = _Build(monkeypatch)
    assert _check(tree) is None
    assert build.calls == [] and os.environ["PATH"].startswith(f"{lev.VENV}/bin{os.pathsep}")


def test_a_failed_build_parks_with_its_output_and_is_not_retried(tree, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.base_prefix) / "bin"))
    build = _Build(monkeypatch, pip_rc=1, pip_err="ERROR: no matching distribution for pytest==9.1.1")
    out = _check(tree)
    assert (out["action"], out["kind"]) == ("land-parked", "land-environment"), out
    assert f"building {lev.VENV} failed" in out["problem"] and "no matching distribution" in out["problem"]
    assert "not in a virtual environment" in out["problem"] and len(build.calls) == 2
    assert _check(tree)["problem"] == out["problem"] and len(build.calls) == 2   # once per process
    assert os.environ["PATH"] == str(Path(sys.base_prefix) / "bin")


def test_land_parks_before_any_gate_runs(tmp_path, monkeypatch):
    ran = tmp_path / "ran.txt"
    gate = {"kind": "argv", "cwd": ".", "timeout": 60, "at_land": True,
            "argv": ["/bin/sh", "-c", f"touch {ran}"]}
    fx = build_repo(tmp_path, gates={"ci-gate": gate}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], lev.REQUIREMENTS, "pytest\n")
    env, _ = _venv(tmp_path / "bare", pytest_file=False)
    before = origin_main(fx)
    monkeypatch.setenv("PATH", f"{env / 'bin'}:{Path(sys.base_prefix) / 'bin'}:/usr/bin:/bin")
    rc, out = run_cli("land", iso["plan_dir"])
    assert (rc, out["action"], out["kind"]) == (1, "land-parked", "land-environment"), out
    assert not ran.exists() and origin_main(fx) == before
    monkeypatch.setenv("PATH", f"{_venv(tmp_path / 'good')[0] / 'bin'}:/usr/bin:/bin")
    rc, out = run_cli("land", iso["plan_dir"])
    assert out["action"] == "land-awaits-review" and ran.exists(), out
