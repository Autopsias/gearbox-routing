"""Run the tests and checks that came WITH main first: a stale one fails in seconds.

A land re-gate once failed after 26 minutes on one test main had added
(test_safe_json_adoption.py), which re-ran alone in 0.13 s. Before the long gates,
`precheck` runs the test files and quality checks main changed since the previous
candidate, in one short pytest call on the merged tree. Red parks the land like a
failed gate does (kind `gate-failed`, gate name `main-new-tests`); anything else, even
a timeout or an unreadable range, goes on to the gates unchanged, because the gates
decide and this only saves their time.

"Since" is the main head the plan was last gated or merged on (`carry_basis`),
joined to the plan by merge base; with no earlier record it is the parent of the
plan's first own commit.

Run with the `python3` land_env put first on PATH, an empty HOME and CI=true, as
`ci-check` runs. Of the quality checks only the three that take `--project` run;
`check_subprocess_results.py` and the rest need a base this step does not have, so
a changed one is named in the record and left to `ci-check`.
"""

import re
import shutil
import tarfile
import tempfile
from pathlib import Path

import land_state as lst
import run_state_io as rsi
import ship_state_io as ssio
from land_state import park, save
from worktree import git

TIMEOUT_S = 600
RUNNABLE_CHECKS = ("check_file_sizes", "check_function_lengths", "check_complexity")
GATE = "main-new-tests"
_SKIP_DIRS = {"fixtures", "codex", "assets", "vendor"}      # pytest.ini norecursedirs
_SKIP_PREFIXES = ("evals/routing/tasks/", "skills/state-machine-audit/evals/")
_OK_PYTEST = (0, 5)                                         # 5: nothing collected


def _base(ctx, st):
    """The commit main's changes are counted from, or None when git cannot say."""
    root, prev = ctx["root"], (st.get("carry_basis") or {}).get("main_head")
    if isinstance(prev, str) and prev:
        rc, out, _ = git(["merge-base", st["plan_head"], prev], root)
        return out if rc == 0 and out else None
    rc, out, _ = git(["rev-list", "--no-merges", "--reverse", st["plan_head"],
                      f"^{st['expected']}"], root)
    first = out.split()[0] if rc == 0 and out.split() else None
    rc, out, _ = git(["rev-parse", f"{first}^"], root) if first else (1, "", "")
    return out if rc == 0 and out else None


def changed_files(ctx, st):
    """The paths main changed since the base ([] when none, None when unreadable)."""
    base = _base(ctx, st)
    if base is None:
        return None
    rc, out, _ = git(["diff", "--no-renames", "--name-only", "-z", f"{base}..{st['expected']}"],
                     ctx["root"], strip=False)
    return [p for p in out.split("\0") if p] if rc == 0 else None


def _is_test(p):
    parts = Path(p).parts
    return (Path(p).name.startswith("test_") and p.endswith(".py")
            and not _SKIP_DIRS.intersection(parts) and not p.startswith(_SKIP_PREFIXES))


def select(land_path, files):
    """(test files that still exist on the merged tree, changed check scripts that do)."""
    live = [p for p in files if (Path(land_path) / p).is_file()]
    tests = [p for p in live if _is_test(p)]
    checks = [p for p in live if p.startswith("scripts/quality/check_") and p.endswith(".py")]
    return sorted(tests), sorted(checks)


def _tail(res):
    return ((res.get("stdout") or "") + (res.get("stderr") or "")).strip()[-3000:]


def _by_folder(tests):
    """{folder: [files]}: two folders may each hold a conftest.py, and one pytest process mixes them up."""
    groups = {}
    for t in tests:
        groups.setdefault(str(Path(t).parent), []).append(t)
    return groups


def _pytest_argv(files):
    return ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfE", *files]


def _red_ids(res):
    """The failing test ids a pytest run printed; the exit code when it printed none."""
    text = (res.get("stdout") or "") + (res.get("stderr") or "")
    return set(re.findall(r"^(?:FAILED|ERROR) (\S+)", text, re.M)) or {f"rc={res.get('returncode')}"}


def _base_tree(root, commit, dest):
    """Unpack the main commit the land merged onto, or None when git cannot say."""
    tar = Path(dest) / "base.tar"
    res = ssio.run_deploy_argv(["git", "archive", "--format=tar", "-o", str(tar), commit], cwd=root, timeout=120)
    if res.get("returncode") != 0 or not tar.is_file():
        return None
    out = Path(dest) / "base"
    out.mkdir()
    try:
        with tarfile.open(tar) as t:
            t.extractall(out, filter="tar")     # our own commit; it holds absolute symlinks (a test tree)
    except (tarfile.TarError, OSError):
        return None                             # no base to compare: the red parks, as before
    return str(out)


def _failures(land_path, tests, checks, home, root=None, base=None):
    """([(name, output)] that came back red, [names] that could not decide, [names] not run).

    A test folder that is red is run again on `base` (main as the land found it). A red that
    main has too is not the plan's, so it does not park: the gates go on and decide.
    """
    env = {"HOME": home, "CI": "true", "GITHUB_ACTIONS": "true", "PYTHONDONTWRITEBYTECODE": "1"}
    red, skipped = [], []
    undecided = []
    runs = [(f"pytest {folder}", _pytest_argv(files)) for folder, files in _by_folder(tests).items()]
    if runs and ssio.run_deploy_argv(["python3", "-c", "import pytest"], cwd=land_path, timeout=60,
                                     env_extra=env).get("returncode") != 0:
        undecided, runs = ["pytest"], []        # a python3 without pytest cannot say; the gates will
    for c in checks:
        stem = Path(c).stem
        if stem in RUNNABLE_CHECKS:
            runs.append((stem, ["python3", c, "--project", land_path]))
        else:
            skipped.append(stem)
    base_dir = None
    for name, argv in runs:
        res = ssio.run_deploy_argv(argv, cwd=land_path, timeout=lst.timeout_for(TIMEOUT_S),
                                   env_extra=env)
        rc = res.get("returncode")
        if rc is None:
            undecided.append(name)
        elif rc not in (_OK_PYTEST if name.startswith("pytest") else (0,)):
            if name.startswith("pytest") and base and root:
                base_dir = base_dir or _base_tree(root, base, home) or ""
                if base_dir:
                    old = ssio.run_deploy_argv(argv, cwd=base_dir, timeout=lst.timeout_for(TIMEOUT_S),
                                               env_extra=env)
                    if old.get("returncode") is not None and _red_ids(res) <= _red_ids(old):
                        continue                # main is red the same way
            red.append((name, _tail(res)))
    return red, undecided, skipped


def _brief(plan_dir, st, tests, red):
    shown = "\n\n".join(f"--- {n} ---\n{out}" for n, out in red)
    return (f"LAND PARKED — the re-gate FAILED on the merged tree: ['{GATE}']. Nothing was pushed.\n\n"
            f"Main changed {len(tests)} test file(s) since this plan last met it, and they fail "
            "on the merged tree. They ran FIRST, so the long gates did not run: a red here is "
            "a red there, in seconds instead of an hour.\n\n"
            f"  cd {st['land_path']}\n" + "".join(f"  python3 -m pytest -q {' '.join(f)}\n" for f in _by_folder(tests).values())
            + "\n"
            f"Fix it on the plan branch, then run `run.py land {plan_dir}` again.\n\n{shown}")


def precheck(plan_dir, ctx, st, key):
    """None to go on to the gates, or the park. Settled once per candidate `key`."""
    if st.get("main_tests_key") == key:
        return None
    files = changed_files(ctx, st)
    tests, checks = select(st["land_path"], files or [])
    if not (tests or checks):
        return None
    home = tempfile.mkdtemp(prefix="land-main-tests-")
    try:
        red, undecided, skipped = _failures(st["land_path"], tests, checks, home,
                                            root=ctx["root"], base=st["expected"])
    finally:
        shutil.rmtree(home, ignore_errors=True)
    st["main_tests"] = {"tests": tests, "checks": checks, "undecided": undecided,
                        "not_run": skipped, "red": [n for n, _ in red]}
    rsi.log_event(plan_dir, "plan_land_main_tests", session_ids=[], slug=ctx["slug"],
                  tests=len(tests), checks=len(checks), red=[n for n, _ in red],
                  undecided=undecided, not_run=skipped)
    if red:
        return park(plan_dir, ctx, st, "gate-failed", _brief(plan_dir, st, tests, red),
                    failed=[GATE], undecided=[])
    st["main_tests_key"] = key
    save(plan_dir, ctx, st)
    return None
