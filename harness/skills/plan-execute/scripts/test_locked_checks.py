"""EXE-02 — locked checks: the worker cannot edit the checks the author wrote.

Authority: ../references/route-at-dispatch-contract.md §5. Every refusal test
plants a real edit in a real git checkout and asserts a real gate NEVER ran (it
touches a marker file when it does); the untouched case proves the same gate
does run, so "the marker is absent" is not true of a gate that never could run.

Run: pytest skills/plan-execute/scripts/test_locked_checks.py -q
"""
import json
import shutil
import subprocess
from importlib import machinery
from pathlib import Path

import pytest

from escalation_helpers import REPO, ab, esca, make_plan, mio, rsi, run
import locked_checks as lc
import stuck_protocol as sp
import verify as vfy
from test_shipping import write_closeout
from verify_paths import GATE_FAILED, LOCKED_CHECK_EDITED

LOCKED = "tests/test_locked.py"
FORCE_PASS = ("import pytest\n\n@pytest.hookimpl(hookwrapper=True)\n"
              "def pytest_runtest_makereport(item, call):\n"
              "    out = yield\n    out.get_result().outcome = 'passed'\n")
RESTORE = "Restore it. If the check itself is wrong, return BLOCKED with a decision brief."


@pytest.fixture(autouse=True)
def _clean_git_env(monkeypatch):
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):   # a hook's GIT_DIR must not leak in
        monkeypatch.delenv(k, raising=False)


def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout


def _plan(tmp_path, *, max_rework=2, locked=(LOCKED,)):
    """A built plan in a git project with one committed locked test, stamped v8
    by hand with `verify.locked`, and an argv gate that leaves a marker when it runs."""
    marker = tmp_path / "gate-ran"
    gates = {"mark": {"kind": "argv", "argv": ["sh", "-c", f"touch '{marker}'"]}}
    sess = {"id": "s01", "title": "S1", "items": ["it-1"], "model": "Sonnet", "reasoning": "high",
            "prompt": "work", "verify": {"gates": ["mark"], "max_rework": max_rework}}
    plan_dir = make_plan(tmp_path, [sess], gates=gates)
    root = plan_dir.parents[1]
    (root / "tests").mkdir()
    (root / LOCKED).write_text("def test_it():\n    assert 1 + 1 == 2\n")
    (root / ".gitignore").write_text("ignored/\n_plans/\n")
    _git(root, "init", "-q")
    _git(root, "add", ".gitignore", LOCKED)
    _git(root, "commit", "-qm", "base")
    m = plan_dir / "manifest.json"
    man = json.loads(m.read_text())
    man["plan_schema_version"] = 8
    man["sessions"][0].update(task_class="standard_build", why_model="fixture pin")
    man["sessions"][0]["verify"]["locked"] = list(locked)
    m.write_text(json.dumps(man, indent=2))
    return plan_dir, root, marker


def _verify(plan_dir):
    write_closeout(plan_dir, "s01")
    return vfy.verify_begin(plan_dir, "s01")


def _snap(plan_dir):
    return json.loads(lc.snapshot_path(plan_dir, "s01").read_text())


def _assert_refused(plan_dir, out, marker, path, verb, kind):
    assert out["action"] == "rework" and out["refusal"] == LOCKED_CHECK_EDITED
    assert out["gate"] == lc.LOCKED_GATE and [path, verb, kind] in out["edited"]
    state = vfy._load_state(plan_dir, "s01")
    assert state["gate_status"][lc.LOCKED_GATE] == LOCKED_CHECK_EDITED != GATE_FAILED
    assert state["gate_status"]["gate:mark"] == "pending"
    # The pass is closed: the gate cannot be run on this closeout, and it never ran.
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:mark")["action"] == "stale-closeout"
    assert not marker.exists()
    fb = (plan_dir / "_verify_state" / "s01.feedback.md").read_text()
    assert f"You {verb} {path}, a {kind}." in fb
    return fb


# --------------------------------------------------------------------------
# Locked paths
# --------------------------------------------------------------------------
def test_untouched_locked_file_passes_and_the_gate_runs(tmp_path):
    plan_dir, root, marker = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    snap = _snap(plan_dir)
    assert snap["begin_sha"] == _git(root, "rev-parse", "HEAD").strip()
    assert snap["repo_root"] == str(root) and list(snap["locked"]) == [LOCKED]
    out = _verify(plan_dir)
    assert out["action"] == "run-argv"
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:mark")["action"] == "passed"
    assert marker.exists()                       # the positive control: this gate does run
    import gate_durations as gd                  # and its run time is logged (gate_run)
    assert len(gd.runs(Path(plan_dir).parent.parent)["gate:mark"]) == 1


def test_one_byte_edit_fails_before_any_gate_runs(tmp_path):
    plan_dir, root, marker = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    f = root / LOCKED
    f.write_text(f.read_text().replace("2\n", "3\n"))       # one byte
    fb = _assert_refused(plan_dir, _verify(plan_dir), marker, LOCKED, "changed", "locked check")
    assert f"You changed {LOCKED}, a locked check. {RESTORE}" in fb


def test_deleting_a_locked_file_fails_the_same_way(tmp_path):
    plan_dir, root, marker = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    (root / LOCKED).unlink()
    _assert_refused(plan_dir, _verify(plan_dir), marker, LOCKED, "deleted", "locked check")


def test_a_missing_locked_path_at_begin_refuses_the_session(tmp_path):
    plan_dir, root, _ = _plan(tmp_path, locked=(LOCKED, "tests/absent.py"))
    with pytest.raises(SystemExit, match="tests/absent.py"):
        lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    assert not lc.snapshot_path(plan_dir, "s01").exists()


def test_a_rework_keeps_its_generations_snapshot(tmp_path):
    plan_dir, root, _ = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    first = _snap(plan_dir)
    (root / LOCKED).write_text("def test_it():\n    pass\n")
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])   # a rework re-dispatch
    assert _snap(plan_dir) == first
    assert lc.check(plan_dir, "s01") == [(LOCKED, "changed", "locked check")]


def test_a_new_escalation_generation_takes_a_new_snapshot(tmp_path):
    plan_dir, root, _ = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    first = _snap(plan_dir)
    (root / LOCKED).write_text("def test_it():\n    assert True\n")   # the operator's correction
    esca.reset(plan_dir, "s01", why="redispatch")
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    second = _snap(plan_dir)
    assert second["generation"] == first["generation"] + 1
    assert second["locked"][LOCKED] != first["locked"][LOCKED]
    assert lc.check(plan_dir, "s01") == []


# --------------------------------------------------------------------------
# Test configuration: tracked, untracked or ignored
# --------------------------------------------------------------------------
def test_a_planted_untracked_conftest_fails(tmp_path):
    plan_dir, root, marker = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    (root / "tests" / "conftest.py").write_text(FORCE_PASS)
    assert "?? tests/conftest.py" in _git(root, "status", "--porcelain", "-uall")   # never staged
    fb = _assert_refused(plan_dir, _verify(plan_dir), marker, "tests/conftest.py", "added",
                         "test-configuration file")
    assert "Remove it." in fb


def test_a_planted_ignored_conftest_fails(tmp_path):
    plan_dir, root, marker = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    (root / "ignored").mkdir()
    (root / "ignored" / "conftest.py").write_text(FORCE_PASS)
    assert _git(root, "check-ignore", "ignored/conftest.py").strip() == "ignored/conftest.py"
    _assert_refused(plan_dir, _verify(plan_dir), marker, "ignored/conftest.py", "added",
                    "test-configuration file")


@pytest.mark.parametrize("where", ["tests", "ignored"])
def test_editing_a_conftest_that_was_untracked_or_ignored_at_begin_fails(tmp_path, where):
    plan_dir, root, marker = _plan(tmp_path)
    (root / where).mkdir(exist_ok=True)
    conftest = root / where / "conftest.py"
    conftest.write_text("# harmless\n")
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    assert f"{where}/conftest.py" in _snap(plan_dir)["test_config"]
    conftest.write_text(FORCE_PASS)
    _assert_refused(plan_dir, _verify(plan_dir), marker, f"{where}/conftest.py", "changed",
                    "test-configuration file")


@pytest.mark.parametrize("move", ["none", "edit-target", "retarget"])
def test_a_symlinked_conftest_is_fingerprinted_through_its_target(tmp_path, move):
    """The target lives outside the repo, so git never lists it: only the link's
    fingerprint can see an edit to it."""
    plan_dir, root, marker = _plan(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.py").write_text("# harmless\n")
    (outside / "b.py").write_text(FORCE_PASS)
    link = root / "tests" / "conftest.py"
    link.symlink_to(outside / "a.py")
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    if move == "none":
        assert _verify(plan_dir)["action"] == "run-argv"      # the control: nothing moved
        return
    if move == "edit-target":
        (outside / "a.py").write_text(FORCE_PASS)              # the link text never changes
    else:
        link.unlink()
        link.symlink_to(outside / "b.py")
    _assert_refused(plan_dir, _verify(plan_dir), marker, "tests/conftest.py", "changed",
                    "test-configuration file")


@pytest.mark.parametrize("edit", [False, True])
def test_a_conftest_inside_a_symlinked_folder_is_fingerprinted(tmp_path, edit):
    """git lists a symlinked folder as one entry and never looks inside it."""
    plan_dir, root, marker = _plan(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "conftest.py").write_text("# harmless\n")
    (outside / "loop").symlink_to(outside)                     # a cycle the walk must survive
    (root / "linked").symlink_to(outside)
    (root / "self").symlink_to(root)                           # and one back into the repo
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    assert "linked/conftest.py" in _snap(plan_dir)["test_config"]
    if not edit:
        assert _verify(plan_dir)["action"] == "run-argv"      # the control: nothing moved
        return
    (outside / "conftest.py").write_text(FORCE_PASS)
    _assert_refused(plan_dir, _verify(plan_dir), marker, "linked/conftest.py", "changed",
                    "test-configuration file")


def test_a_dangling_or_unreadable_symlink_has_a_stable_distinct_fingerprint(tmp_path):
    link, target = tmp_path / "conftest.py", tmp_path / "t.py"
    link.symlink_to(target)
    dangling = lc._sha(link)
    assert dangling == lc._sha(link) == f"symlink:{target}:missing"
    target.mkdir()                                             # a target that cannot be read
    assert lc._sha(link) == dangling
    target.rmdir()
    target.write_text("x")
    assert lc._sha(link) not in (dangling, None)


# --------------------------------------------------------------------------
# Package initializers of a locked test: bounded to its own folder and above
# --------------------------------------------------------------------------
PKG = "pkg/sub/test_x.py"
PKG_KIND = "package initializer of a locked check"


def _pkg_plan(tmp_path, inits):
    """A locked test at pkg/sub/test_x.py with the given __init__ files, plus an
    unrelated other/__init__.py, all committed before begin."""
    plan_dir, root, marker = _plan(tmp_path, locked=(PKG,))
    for rel in (PKG, *inits, "other/__init__.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("def test_it():\n    assert True\n" if rel == PKG else "# pkg\n")
    _git(root, "add", PKG, *inits, "other/__init__.py")
    _git(root, "commit", "-qm", "pkg")
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    return plan_dir, root, marker


@pytest.mark.parametrize("edited, inits", [
    ("pkg/__init__.py", ("pkg/__init__.py", "pkg/sub/__init__.py")),
    ("pkg/sub/__init__.py", ("pkg/__init__.py", "pkg/sub/__init__.py")),
    # A gap in the chain: pytest still runs pkg/__init__.py, at Package setup.
    ("pkg/__init__.py", ("pkg/__init__.py",)),
])
def test_editing_a_package_init_above_a_locked_test_fails(tmp_path, edited, inits):
    plan_dir, root, marker = _pkg_plan(tmp_path, inits)
    (root / edited).write_text("import _pytest.python\n_pytest.python.Function.runtest = "
                               "lambda self: None\n")                  # every test passes
    _assert_refused(plan_dir, _verify(plan_dir), marker, edited, "changed", PKG_KIND)


@pytest.mark.parametrize("added", ["pkg/sub/__init__.py",
                                   "pkg/sub/__init__" + machinery.EXTENSION_SUFFIXES[0]])
def test_adding_a_package_init_above_a_locked_test_fails(tmp_path, added):
    """An extension module beside __init__.py is imported first, so every import
    form of __init__ counts, not only the .py."""
    plan_dir, root, marker = _pkg_plan(tmp_path, ("pkg/__init__.py",))
    (root / added).write_text("# planted\n")
    fb = _assert_refused(plan_dir, _verify(plan_dir), marker, added, "added", PKG_KIND)
    assert "Remove it." in fb


def test_an_unrelated_package_init_is_code_under_test(tmp_path):
    """The bound: an __init__.py outside the locked test's folder chain is not
    fingerprinted (contract §5, out of scope; the review gate covers it)."""
    plan_dir, root, marker = _pkg_plan(tmp_path, ("pkg/__init__.py", "pkg/sub/__init__.py"))
    (root / "other" / "__init__.py").write_text("X = 1\n")
    (root / "pkg" / "sub" / "helpers.py").write_text("X = 1\n")   # a sibling module too
    assert _verify(plan_dir)["action"] == "run-argv"
    assert vfy.verify_run_argv(plan_dir, "s01", "gate:mark")["action"] == "passed"
    assert marker.exists()


@pytest.mark.parametrize("rel", ["pytest.ini", "a/b/sitecustomize.py", "x.pth",
                                 "pkg.dist-info/entry_points.txt", "usercustomize/__init__.py",
                                 "sitecustomize.abi3.so", "venv/pyvenv.cfg", "Conftest.py"])
def test_the_protected_set_is_a_class(rel):
    assert lc.protected(rel)


@pytest.mark.parametrize("rel", ["tests/test_x.py", "entry_points.txt",
                                 "__pycache__/conftest.cpython-314.pyc", "sitecustomize_x.py"])
def test_ordinary_files_are_not_protected(rel):
    assert not lc.protected(rel)


# --------------------------------------------------------------------------
# Stuck protocol, bytecode caches, the begin wiring
# --------------------------------------------------------------------------
def test_the_refusal_is_a_stuck_failure_with_its_own_signature(tmp_path):
    plan_dir, root, _ = _plan(tmp_path)
    lc.begin(plan_dir, mio.load_manifest(plan_dir), ["s01"])
    (root / LOCKED).write_text("")
    _verify(plan_dir)
    rec = sp.last_failure(plan_dir, "s01")
    assert rec["class"] == "LockedCheckError"
    assert rec["sig"] != sp.signature("AssertionError: gate failed")["sig"]
    out = _verify(plan_dir)                      # the next attempt left it edited too
    assert out["refusal"] == LOCKED_CHECK_EDITED and out["stuck_protocol"]["triggered"]


def test_a_locked_sessions_gate_gets_a_fresh_empty_bytecode_cache(tmp_path):
    plan_dir, _, _ = _plan(tmp_path)
    first = lc.gate_env(plan_dir, "s01")["PYTHONPYCACHEPREFIX"]
    (tmp_path / "planted.pyc").write_bytes(b"x")
    shutil.copy(tmp_path / "planted.pyc", first)
    second = lc.gate_env(plan_dir, "s01")["PYTHONPYCACHEPREFIX"]
    assert second != first and not list(Path(second).iterdir())
    assert not Path(first).exists()


@pytest.fixture
def routing(tmp_path, monkeypatch):
    path = tmp_path / "model-routing.yaml"
    shutil.copy(REPO / "model-routing.yaml", path)
    monkeypatch.setenv(run._SSOT_ENV, str(path))
    for k in ("PLAN_EXECUTE_ROUTING_PROVIDER", "CLAUDE_CONFIG_DIR", "PLAN_EXECUTE_ROUTE_AT_DISPATCH"):
        monkeypatch.delenv(k, raising=False)


def test_begin_snapshots_and_refuses_through_cmd_begin(tmp_path, routing, capsys):
    plan_dir, root, _ = _plan(tmp_path)
    run.cmd_begin(plan_dir, ["s01"], isolate=False)
    capsys.readouterr()
    assert _snap(plan_dir)["locked"] and _snap(plan_dir)["repo_root"] == str(root)
    rsi.release_lock(plan_dir)

    (tmp_path / "two").mkdir()
    plan2, _, _ = _plan(tmp_path / "two", locked=("tests/absent.py",))
    with pytest.raises(SystemExit, match="locked checks"):
        run.cmd_begin(plan2, ["s01"], isolate=False)
    assert not lc.snapshot_path(plan2, "s01").exists()
    assert ab.read_all_statuses((plan2 / "PLAN.html").read_text())["s01"] == "TODO"
