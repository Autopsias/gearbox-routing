"""LND-12 fix round — every failure in the land tree is held the moment it
happens (``land_flaky.record``), and "the plan changed" means the plan's OWN
patch changed, not that its head moved.

Real git, real `run.py`, on the shared `_land_fixture`; the gate helpers come
from test_land_repair.py.

    pytest skills/plan-execute/scripts/test_land_flaky.py -q
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import plan_mutate as pm  # noqa: E402
from _land_fixture import build_repo, isolate, main_side, run_cli, work  # noqa: E402
from test_land_repair import _flaky, _gate, _land, _seq, _state  # noqa: E402
from worktree import git  # noqa: E402

SKILL = {"kind": "skill", "skill": "some-review", "at_land": True}
WHOLE = 'echo "error: boom"; exit 1'      # a failure that names no test and no file


def _add(iso, d):
    pm.add_session(iso["plan_dir"], **{**d["add_session"], "new_items": [
        json.dumps(i) for i in d["add_session"]["new_items"]]})


def test_a_first_failure_before_a_skill_round_trip_is_held(tmp_path):
    """Codex HIGH 1: red, then a skill verdict, then green on the unchanged tree."""
    fx = build_repo(tmp_path, gates={"ci-gate": _seq(tmp_path, "t.py::test_a", "pass"),
                                     "z-skill": SKILL}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "tests/t.py", "def test_a():\n    pass\n")
    rc, out = _land(iso)                       # run 1 red, then the skill asks
    assert out["action"] == "invoke-skill", out
    assert run_cli("land-record", iso["plan_dir"], "--gate", "z-skill",
                   "--status", "passed")[0] == 0
    rc, out = _land(iso)                       # run 2 green: nothing changed since run 1
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    assert out["tests"] == ["tests/t.py::test_a"] and "run.py land" not in out["brief"], out


def test_a_failure_only_the_re_run_saw_is_held(tmp_path):
    """Codex HIGH 2: run 1 fails A (untouched), its re-run fails B (plan-touched)."""
    fx = build_repo(tmp_path, gates={"ci-gate": _seq(tmp_path, "a.py::test_a",
                                                     "b.py::test_b")}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "tests/b.py", "def test_b():\n    pass\n")
    rc, d = _land(iso)                         # A, then B, then the base is green
    assert d["action"] == "land-repair", d
    rc, out = _land(iso)                       # green on the unchanged tree
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    assert out["tests"] == ["tests/b.py::test_b"], out
    assert {"tests/a.py::test_a", "tests/b.py::test_b"} <= set(_state(iso)["red_then_green"])


def test_a_held_test_file_moved_to_quarantine_clears_its_hold(tmp_path):
    """Claude HIGH 3: a rename shows the old path too (``--no-renames``)."""
    fx = build_repo(tmp_path, gates={"ci-gate": _flaky(tmp_path)}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "tests/t.py", "def test_flaky():\n    pass\n")
    assert _land(iso)[1]["kind"] == "gate-flaky"
    (iso["tree"] / "tests" / "quarantine").mkdir()
    git(["mv", "tests/t.py", "tests/quarantine/t.py"], iso["tree"], check=True)
    git(["commit", "-q", "-m", "quarantine the flaky test"], iso["tree"], check=True)
    rc, out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    assert not _state(iso)["plan_touched_flakes"]


def test_the_fast_forward_alone_leaves_a_round_gone_green_flaky(tmp_path):
    """Claude MEDIUM 4a: the directive's own fast-forward changes no plan code."""
    fx = build_repo(tmp_path, gates={"ci-gate": _seq(tmp_path, "t.py::test_a",
                                                     "t.py::test_a", "pass")}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "X = 1\n")
    rc, d = _land(iso)
    assert d["action"] == "land-repair", d
    _add(iso, d)
    subprocess.run(shlex.split(d["start"]["command"]), check=True, capture_output=True)
    rc, out = _land(iso)
    assert out.get("kind") == "repair-not-needed", out
    assert "Nothing is left for it to repair" not in out["brief"], out
    assert "tests/t.py::test_a failed, then passed: flaky" in out["brief"], out
    assert "tests/t.py::test_a" in _state(iso)["red_then_green"]


def test_an_unrelated_default_branch_commit_keeps_a_whole_gate_hold(tmp_path):
    """Claude MEDIUM 4b: the base moving is not the plan changing."""
    gate = _gate(tmp_path, f'if [ ! -f {tmp_path}/flaked ]; then touch {tmp_path}/flaked; '
                           f'{WHOLE}; fi')
    fx = build_repo(tmp_path, gates={"ci-gate": gate}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert _land(iso)[1]["kind"] == "gate-flaky"
    main_side(fx, "src/other.py", "O = 1\n")
    rc, out = _land(iso)
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    work(iso["tree"], "src/g.py", "G = 1\n")   # a change in the plan's own patch
    assert _land(iso)[1]["action"] == "land-awaits-review"


def test_a_failure_the_base_then_fixed_does_not_hold_the_land(tmp_path):
    """A failure never seen to pass on its own tree is not a known flake: once the
    tree changes (here the base fixes it, the gate-inherited way out), it ends."""
    gate = _gate(tmp_path, f'if [ ! -f MAIN_FIX ]; then {WHOLE}; fi')
    fx = build_repo(tmp_path, gates={"ci-gate": gate}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "src/f.py", "F = 1\n")
    assert _land(iso)[1]["kind"] == "gate-inherited"
    main_side(fx, "MAIN_FIX", "x\n")
    rc, out = _land(iso)
    assert out["action"] == "land-awaits-review", out


def test_a_repair_that_fixes_the_code_not_the_test_file_lands(tmp_path):
    """A plan-touched test that failed twice, repaired in another file: a repair."""
    gate = _gate(tmp_path, 'if [ -f BROKEN ]; then echo "FAILED tests/t.py::test_a - boom"; '
                           'exit 1; fi')
    fx = build_repo(tmp_path, gates={"ci-gate": gate}, built=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "tests/t.py", "def test_a():\n    pass\n")
    work(iso["tree"], "BROKEN", "x\n")
    rc, d = _land(iso)
    assert d["action"] == "land-repair", d
    subprocess.run(shlex.split(d["start"]["command"]), check=True, capture_output=True)
    git(["rm", "-q", "BROKEN"], iso["tree"], check=True)
    git(["commit", "-q", "-m", "repair"], iso["tree"], check=True)
    rc, out = _land(iso)
    assert out["action"] == "land-awaits-review", out


_T = [f"# line {i}\n" for i in range(1, 10)]   # a held file far apart from main's edit


def _both_edit_t(tmp_path):
    """tests/t.py on the base; the plan edits line 1, the flaky gate parks it, then
    main edits line 9 of the same file."""
    fx = build_repo(tmp_path, gates={"ci-gate": _flaky(tmp_path)}, built=True)
    work(fx["root"], "tests/t.py", "".join(_T), "base test file")
    git(["push", "-q", "origin", "main"], fx["root"], check=True)
    iso = isolate(fx, "plan-a")
    work(iso["tree"], "tests/t.py", "".join(["# plan\n", *_T[1:]]))
    assert _land(iso)[1]["kind"] == "gate-flaky"
    main_side(fx, "tests/t.py", "".join([*_T[:-1], "# main\n"]))
    return iso


def test_a_plan_repair_of_a_file_main_also_changed_clears_its_hold(tmp_path):
    """Both reviewers HIGH: main editing the held file must not hide the plan's repair."""
    iso = _both_edit_t(tmp_path)
    work(iso["tree"], "tests/t.py", "".join(["# plan\n", _T[1], "# repaired\n", *_T[3:]]))
    rc, out = _land(iso)
    assert out["action"] == "land-awaits-review", out
    assert not _state(iso)["plan_touched_flakes"]


def test_only_main_changing_the_held_file_keeps_its_hold(tmp_path):
    iso = _both_edit_t(tmp_path)
    rc, out = _land(iso)
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    assert out["tests"] == ["tests/t.py::test_flaky"], out


def _pkg_flaky(tmp_path, pkg):
    """Fails once, then passes, run from ``pkg/``: prints the same id as the other."""
    flag = tmp_path / f"flaked-{pkg}"
    return _gate(tmp_path, f'if [ ! -f {flag} ]; then touch {flag}; '
                           'echo "FAILED tests/t.py::test_x - boom"; exit 1; fi', cwd=pkg)


def test_one_test_id_in_two_gates_is_held_per_gate(tmp_path):
    """FIN-16: the same pytest id from two packages (two gates, two working
    directories) is two holds; changing only package A's file keeps B's."""
    fx = build_repo(tmp_path, gates={"gate-a": _pkg_flaky(tmp_path, "pkgA"),
                                     "gate-b": _pkg_flaky(tmp_path, "pkgB")}, built=True)
    iso = isolate(fx, "plan-a")
    for pkg in ("pkgA", "pkgB"):
        work(iso["tree"], f"{pkg}/tests/t.py", "def test_x():\n    pass\n")
    rc, out = _land(iso)
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    assert out["tests"] == ["gate-a: tests/t.py::test_x", "gate-b: tests/t.py::test_x"], out
    held = _state(iso)["plan_touched_flakes"]
    assert {k: e["file"] for k, e in held.items()} == {
        "gate-a: tests/t.py::test_x": "pkgA/tests/t.py",
        "gate-b: tests/t.py::test_x": "pkgB/tests/t.py"}, held
    work(iso["tree"], "pkgA/tests/t.py", "def test_x():\n    assert 1\n", msg="fix A")
    rc, out = _land(iso)
    assert (rc, out.get("kind")) == (1, "gate-flaky"), out
    assert out["tests"] == ["tests/t.py::test_x"] and out["failed"] == ["gate-b"], out
    assert list(_state(iso)["plan_touched_flakes"]) == ["gate-b: tests/t.py::test_x"]
    assert "pkgB/tests/t.py, which this plan changed" in out["brief"], out


def test_a_name_only_record_from_an_older_land_is_kept_under_its_gate(monkeypatch):
    """A land.json written before FIN-16 keyed holds by name; it is re-keyed by
    its recorded gate, and another gate's same id gets a record of its own."""
    import land_flaky as lfk  # noqa: PLC0415
    import land_state as lst  # noqa: PLC0415
    monkeypatch.setattr(lst, "range_files", lambda *a, **k: None)
    old = {"gate": "gate-a", "file": "pkgA/tests/t.py", "flaky": True, "plan_head": "h0"}
    st = {"land_path": "/land", "expected": "e", "plan_head": "h1",
          "plan_touched_flakes": {"tests/t.py::test_x": dict(old)}}
    lfk.hold(st, "gate-b", [("tests/t.py::test_x", "tests/t.py")], cwd="/land/pkgB")
    held = st["plan_touched_flakes"]
    assert held["gate-a: tests/t.py::test_x"] == {**old, "name": "tests/t.py::test_x"}
    assert held["gate-b: tests/t.py::test_x"]["file"] == "pkgB/tests/t.py"
    assert held["gate-b: tests/t.py::test_x"]["gate"] == "gate-b"


def test_a_hold_anchors_to_the_gate_relative_path_when_both_are_changed(monkeypatch):
    """FIN-17: a relative id from a gate run in ``zz/`` (or ``pkgB/``) holds
    ``<cwd>/tests/t.py`` even when the root ``tests/t.py`` changed too; the raw
    id is used only when the gate-relative path is not in the plan's diff."""
    import land_flaky as lfk  # noqa: PLC0415
    import land_state as lst  # noqa: PLC0415
    for pkg in ("pkgB", "zz"):
        diff = {"tests/t.py", f"{pkg}/tests/t.py"}
        monkeypatch.setattr(lst, "range_files", lambda *a, **k: diff)
        st = {"land_path": "/land", "expected": "e", "plan_head": "h1"}
        lfk.hold(st, "gate-b", [("tests/t.py::test_x", "tests/t.py")], cwd=f"/land/{pkg}")
        assert st["plan_touched_flakes"]["gate-b: tests/t.py::test_x"]["file"] \
            == f"{pkg}/tests/t.py", st
        diff.discard(f"{pkg}/tests/t.py")
        st = {"land_path": "/land", "expected": "e", "plan_head": "h1"}
        lfk.hold(st, "gate-b", [("tests/t.py::test_x", "tests/t.py")], cwd=f"/land/{pkg}")
        assert st["plan_touched_flakes"]["gate-b: tests/t.py::test_x"]["file"] == "tests/t.py"
