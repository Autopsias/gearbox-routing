"""Land's environment check, with real interpreters and real virtual environments.

The land fixtures hold no requirements-ci.txt, so every other land test runs with
whatever python3 is on PATH; test_no_requirements_file_means_no_check proves that.

    pytest skills/plan-execute/scripts/test_land_env.py -q
"""

import sys
import venv
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import land_env as lev  # noqa: E402
from _land_fixture import build_repo, isolate, origin_main, run_cli, work  # noqa: E402


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


def test_an_interpreter_outside_a_venv_parks_with_the_build_command(tree, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.base_prefix) / "bin"))
    out = _check(tree)
    assert (out["action"], out["kind"]) == ("land-parked", "land-environment"), out
    assert "not in a virtual environment" in out["problem"]
    assert f"python3.14 -m venv {lev.VENV} && {lev.VENV}/bin/python -m pip install -r " in out["brief"]
    assert str(tree / lev.REQUIREMENTS) in out["brief"]
    assert f'PATH={lev.VENV}/bin:"$PATH" run.py land /plans/p' in out["brief"]


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
