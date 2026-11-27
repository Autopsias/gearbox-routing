"""The ~/.gearbox-state leak guard in conftest.py: a plant that must fail, a control that must pass,
and the invariants, each checked by an inner pytest run whose HOME is a temp folder."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import dyno_guard

HERE = Path(__file__).resolve().parent
INNER = """
import json, os
from pathlib import Path

def test_inner(tmp_path, monkeypatch):
    for path, content in json.loads(os.environ["INNER_WRITES"]):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content)
    if os.environ.get("INNER_SETHOME"):
        monkeypatch.setenv("HOME", os.environ["INNER_SETHOME"])
"""
TEMP_PTR = json.dumps({"locks_dir": "/private/var/folders/xx/T/pytest-1/locks"})
REAL_PTR = json.dumps({"locks_dir": "/Users/someone/repo/.git/locks"})


ROOT = HERE.parents[2]


def run_inner(tmp_path, writes, *, pre=(), xdist=False, sethome=None):
    # Root-style: the repo-root conftest.py (plus the guard) is copied above the test file, so
    # pytest loads it as an initial conftest exactly as a bare `pytest` run does. No `-p conftest`.
    shutil.copy(ROOT / "conftest.py", tmp_path / "conftest.py")
    guard = tmp_path / "skills/plan-execute/scripts"
    guard.mkdir(parents=True)
    for name in ("dyno_guard.py", "safe_json.py"):
        shutil.copy(HERE / name, guard / name)
    home = tmp_path / "home"
    (home / ".gearbox-state").mkdir(parents=True)
    for rel, content in pre:
        (home / ".gearbox-state" / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / ".gearbox-state" / rel).write_text(content)
    (tmp_path / "test_inner.py").write_text(INNER)
    cmd = [sys.executable, "-m", "pytest", str(tmp_path / "test_inner.py"), "-q", "-p", "no:cacheprovider",
           "--rootdir", str(tmp_path)]
    if xdist:
        cmd += ["-p", "xdist", "-n", "2"]
    env = {**os.environ, "HOME": str(home), "INNER_WRITES": json.dumps([[str(home / ".gearbox-state" / r), c] for r, c in writes]),
           "INNER_SETHOME": str(sethome or ""),
           # pin plugin loading as `make test` does, so the inner run is the same with autoload on or off
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=tmp_path)
    return r.returncode, r.stdout + r.stderr, home


LEAK = [("land-holds/test_x__origin.json", TEMP_PTR)]
BACKGROUND = [("agent-nag/s.bash.json", "{}"), ("compaction/hb/s", ""), ("land-holds/real__origin.json", REAL_PTR),
              ("land-holds/note.txt", TEMP_PTR)]


def test_plant_a_matching_file_fails_the_run(tmp_path):
    code, out, home = run_inner(tmp_path, LEAK)
    assert code != 0, out
    assert f"GEARBOX_STATE-LEAK: {home}/.gearbox-state/land-holds/test_x__origin.json" in out
    assert (home / ".gearbox-state/land-holds/test_x__origin.json").exists()  # the guard never deletes it


def test_control_background_writes_pass(tmp_path):
    code, out, _ = run_inner(tmp_path, BACKGROUND)
    assert code == 0 and "GEARBOX_STATE-LEAK" not in out, out


@pytest.mark.parametrize("xdist", [False, True])
@pytest.mark.parametrize("case", ["preexisting", "nonmatching", "matching", "matching_home_changed"])
def test_matrix(tmp_path, case, xdist):
    pre = LEAK if case == "preexisting" else ()
    writes = {"preexisting": [], "nonmatching": BACKGROUND}.get(case, LEAK)
    sethome = tmp_path / "elsewhere" if case.endswith("home_changed") else None
    code, out, _ = run_inner(tmp_path, writes, pre=pre, xdist=xdist, sethome=sethome)
    if case.startswith("matching"):
        assert code != 0 and "GEARBOX_STATE-LEAK" in out, out  # fails although every test passed
        assert "1 passed" in out
    else:
        assert code == 0 and "GEARBOX_STATE-LEAK" not in out, out


def test_guard_functions_never_write(tmp_path):
    d = tmp_path / ".gearbox-state/land-holds"
    d.mkdir(parents=True)
    f = d / "a__origin.json"
    f.write_text(TEMP_PTR)
    before = (f.read_bytes(), f.stat().st_mtime_ns, sorted(p.name for p in tmp_path.rglob("*")))
    snap = dyno_guard.snapshot(tmp_path)
    (d / "b__origin.json").write_text(TEMP_PTR)
    assert [p.name for p in dyno_guard.new_leaks(snap, tmp_path)] == ["b__origin.json"]
    dyno_guard.scan_since(tmp_path, 0)
    assert (f.read_bytes(), f.stat().st_mtime_ns) == before[:2]
    assert sorted(p.name for p in tmp_path.rglob("*")) == sorted(before[2] + ["b__origin.json"])


def test_scan_cli_lists_matching_files(tmp_path):
    d = tmp_path / ".gearbox-state/land-holds"
    d.mkdir(parents=True)
    (d / "a__origin.json").write_text(TEMP_PTR)
    (d / "b__origin.json").write_text(REAL_PTR)
    r = subprocess.run([sys.executable, str(HERE / "dyno_guard.py"), "--scan", "--since", "2000-01-01"],
                       env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True)
    assert r.returncode == 0 and "a__origin.json" in r.stdout and "b__origin.json" not in r.stdout


def test_a_rewrite_of_an_existing_leak_name_is_caught(tmp_path):
    d = tmp_path / ".gearbox-state/land-holds"
    d.mkdir(parents=True)
    (d / "a__origin.json").write_text(TEMP_PTR)
    os.utime(d / "a__origin.json", ns=(10**9, 10**9))  # an old mtime: coarse-clock filesystems can give two quick writes one mtime
    snap = dyno_guard.snapshot(tmp_path)
    (d / "a__origin.json").write_text(TEMP_PTR + " ")
    assert [p.name for p in dyno_guard.new_leaks(snap, tmp_path)] == ["a__origin.json"]
