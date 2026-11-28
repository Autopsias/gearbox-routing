"""Smallest checks that fail if the quality checkers' core logic breaks."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent
REPO_ROOT = SCRIPTS.parent.parent


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        capture_output=True, text=True,
    )


def make_fixture(tmp_path: Path) -> None:
    (tmp_path / "big_one.py").write_text("\n".join(f"x{i} = {i}" for i in range(520)) + "\n")
    (tmp_path / "big_two.py").write_text("\n".join(f"y{i} = {i}" for i in range(510)) + "\n")
    body = "\n".join(f"    a{i} = {i}" for i in range(120))
    (tmp_path / "longfunc.py").write_text(f"def sprawler():\n{body}\n    return a0\n")


def test_file_size_baseline_reports_stale_removal(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    proj = str(tmp_path)

    first = run("check_file_sizes.py", "--project", proj, "--generate-baseline")
    assert "2 exceptions" in first.stdout

    (tmp_path / "big_two.py").unlink()
    second = run("check_file_sizes.py", "--project", proj, "--generate-baseline")
    assert "1 exceptions" in second.stdout
    assert "1 stale entries removed" in second.stdout

    data = json.loads((tmp_path / ".file-size-exceptions").read_text())
    assert [e["file"] for e in data["exceptions"]] == ["big_one.py"]


def test_function_length_blocking_detected_and_no_hardcoded_literal(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    proc = run("check_function_lengths.py", "--project", str(tmp_path))
    assert proc.returncode == 1
    assert "sprawler" in proc.stdout
    assert "BLOCKING" in proc.stdout  # the marker the skill greps for
    assert ">100 lines" not in proc.stdout  # removed hardcoded literal must stay gone


def test_grandfathered_function_survives_a_line_shift(tmp_path: Path) -> None:
    """The ratchet must forgive by identity, not by position.

    Keying an exception on the line number meant one inserted line above a
    forgiven function re-blocked it, which made the baseline useless the moment
    anyone edited the file.
    """
    make_fixture(tmp_path)
    proj = str(tmp_path)

    run("check_function_lengths.py", "--project", proj, "--generate-baseline")
    assert run("check_function_lengths.py", "--project", proj).returncode == 0

    longfunc = tmp_path / "longfunc.py"
    longfunc.write_text("import sys  # one line, pushes sprawler down\n" + longfunc.read_text())

    after = run("check_function_lengths.py", "--project", proj)
    assert after.returncode == 0, f"line shift re-blocked a grandfathered function:\n{after.stdout}"
    assert "0 BLOCKING violation(s)" in after.stdout


def test_function_length_baseline_reads_legacy_lineno_keys(tmp_path: Path) -> None:
    """A baseline written under the old file:lineno:name scheme still applies."""
    make_fixture(tmp_path)
    proj = str(tmp_path)
    run("check_function_lengths.py", "--project", proj, "--generate-baseline")

    exc_file = tmp_path / ".function-length-exceptions"
    data = json.loads(exc_file.read_text())
    legacy = {
        f"{e['file']}:{e['lineno']}:{e['name']}": e
        for e in data["exceptions"].values()
    }
    exc_file.write_text(json.dumps({"exceptions": legacy}, indent=2))

    assert run("check_function_lengths.py", "--project", proj).returncode == 0


def test_file_size_blocking_exit_code(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    proc = run("check_file_sizes.py", "--project", str(tmp_path))
    assert proc.returncode == 1
    assert "BLOCKING" in proc.stdout


# --- staged mode -------------------------------------------------------------

def git(proj: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=proj, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")


def test_staged_ignores_unstaged_debt_and_blocks_the_staged_plant(tmp_path: Path) -> None:
    """Hole 1: a one-file commit must never be judged on files it never touched.

    Plant: a NEW staged over-limit file blocks. Control: pre-existing debt in
    an untouched, unbaselined file does not.
    """
    make_repo(tmp_path)
    make_fixture(tmp_path)  # big_one/big_two over limit, never baselined
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "debt exists")

    (tmp_path / "small.py").write_text("x = 1\n")
    git(tmp_path, "add", "small.py")
    for script in ("check_file_sizes.py", "check_function_lengths.py"):
        proc = run(script, "--project", str(tmp_path), "--staged")
        assert proc.returncode == 0, f"{script} judged untouched files:\n{proc.stdout}"

    plant = "\n".join(f"p{i} = {i}" for i in range(520)) + "\n"
    (tmp_path / "plant.py").write_text(plant)
    git(tmp_path, "add", "plant.py")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1, "a new staged over-limit file must block"
    assert "plant.py" in proc.stdout and "BLOCKING" in proc.stdout


@pytest.mark.skipif(
    not (REPO_ROOT / ".file-size-exceptions").exists(),
    reason="this layout has no .file-size-exceptions baseline (public export)")
def test_staged_blocking_message_says_the_fix_and_names_real_paths(tmp_path: Path) -> None:
    """TELL-01: the blocking message says why, says the fix, and never lies.

    Every file or command the message names must exist in THIS tree (not the
    throwaway tmp_path fixture) -- a message that points at a file that is not
    there is the exact defect this item closes.
    """
    make_repo(tmp_path)
    plant = "\n".join(f"p{i} = {i}" for i in range(520)) + "\n"
    (tmp_path / "plant.py").write_text(plant)
    git(tmp_path, "add", "plant.py")

    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1, "the exit code must be unchanged by the message rewrite"

    out = proc.stdout
    assert "ratchet" in out, "why before what: the bound is a ratchet"
    assert "extract" in out.lower() and "module" in out.lower(), "must name the fix"
    assert ".file-size-exceptions" in out, "must name the baseline file by name"
    assert "not the fix" in out, "must say raising the baseline is not the fix"

    (tmp_path / "_evidence_message.txt").write_text(out)

    # Pull every file path and python command the FIX MESSAGE names (not the
    # violation listing above it, which names the planted fixture file) and
    # prove each one is real -- in this repo, not the tmp_path fixture.
    fix_message = out[out.index("is a ratchet") :]
    py_paths = re.findall(r"[\w./-]+\.py\b", fix_message)
    dotfiles = re.findall(r"\.[a-zA-Z][\w-]*-exceptions\b", fix_message)
    assert py_paths, "message should name at least one script"
    assert dotfiles, "message should name the baseline file"
    for rel in {*py_paths, *dotfiles}:
        assert (REPO_ROOT / rel).exists(), f"message names {rel}, which does not exist"


def test_staged_inherited_debt_warns_instead_of_blocking(tmp_path: Path) -> None:
    """Hole 3: debt this commit did not make worse warns; shrinking it passes."""
    make_repo(tmp_path)
    make_fixture(tmp_path)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "debt exists")  # big_one committed at 520, no baseline

    # Shrink big_one to 510 — still over limit, but better than HEAD.
    (tmp_path / "big_one.py").write_text("\n".join(f"x{i} = {i}" for i in range(510)) + "\n")
    git(tmp_path, "add", "big_one.py")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"an improvement was blocked:\n{proc.stdout}"
    assert "Inherited debt" in proc.stdout and "re-record" in proc.stdout.lower()

    # Growing it past HEAD blocks again.
    (tmp_path / "big_one.py").write_text("\n".join(f"x{i} = {i}" for i in range(560)) + "\n")
    git(tmp_path, "add", "big_one.py")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1


def test_staged_function_growth_past_baseline_blocks(tmp_path: Path) -> None:
    make_repo(tmp_path)
    make_fixture(tmp_path)
    run("check_function_lengths.py", "--project", str(tmp_path), "--generate-baseline")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "baselined")

    body = "\n".join(f"    a{i} = {i}" for i in range(140))  # grew 120 -> 142
    (tmp_path / "longfunc.py").write_text(f"def sprawler():\n{body}\n    return a0\n")
    git(tmp_path, "add", "longfunc.py")
    proc = run("check_function_lengths.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1
    assert "sprawler" in proc.stdout


def test_staged_complexity_blocks_new_and_forgives_inherited(tmp_path: Path) -> None:
    import shutil as _shutil
    if _shutil.which("ruff") is None:
        return  # environment without ruff: the checker itself exits 2 there
    make_repo(tmp_path)
    cond = "\n".join(f"    if n == {i}: return {i}" for i in range(20))
    (tmp_path / "twisty.py").write_text(f"def twisty(n):\n{cond}\n    return 0\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "complex already")

    # Touched but not made worse -> inherited warning, exit 0.
    (tmp_path / "twisty.py").write_text(f"# comment\ndef twisty(n):\n{cond}\n    return 0\n")
    git(tmp_path, "add", "twisty.py")
    proc = run("check_complexity.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"inherited complexity blocked:\n{proc.stdout}{proc.stderr}"
    assert "Inherited debt" in proc.stdout

    # A NEW over-limit function blocks.
    (tmp_path / "fresh.py").write_text(f"def fresh(n):\n{cond}\n    return 0\n")
    git(tmp_path, "add", "fresh.py")
    proc = run("check_complexity.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1
    assert "fresh" in proc.stdout


def test_merge_landing_branch_debt_warns_not_blocks(tmp_path: Path) -> None:
    """Hole 3 verbatim: a branch grew an over-limit file the baseline never saw.

    At merge time the file is judged against MERGE_HEAD, where it already
    existed at that size — so the merge commit warns (re-record the baseline)
    instead of blocking with no local way forward.
    """
    make_repo(tmp_path)
    (tmp_path / "base.py").write_text("x = 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")

    git(tmp_path, "checkout", "-qb", "feature")
    (tmp_path / "grown.py").write_text("\n".join(f"g{i} = {i}" for i in range(520)) + "\n")
    git(tmp_path, "add", "grown.py")
    git(tmp_path, "commit", "-qm", "branch debt")

    git(tmp_path, "checkout", "-q", "-")
    merge = git(tmp_path, "merge", "--no-commit", "--no-ff", "feature")
    assert merge.returncode == 0, merge.stderr
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"merge-inherited debt blocked the merge:\n{proc.stdout}"
    assert "Inherited debt" in proc.stdout


def test_a_commit_cannot_write_the_bar_it_is_judged_against(tmp_path: Path) -> None:
    """Hole 5: re-recording the baseline in the same commit disabled the gate.

    Measured on the real checkers, before this was closed: a file
    grown 100 -> 600 LOC against a 500 limit exited 0 and printed "within size
    limits" when the same commit re-recorded its baseline at 600. `loc <= bound`
    was satisfied by the number the commit had just written, so the parent
    comparison under it never ran. The control below -- identical growth, no
    re-record -- blocked, which is what proves the re-record was doing it.

    This is not a hypothetical shape. It is what the documented merge procedure
    ("re-record the baselines IN the merge commit") asks for, and a real repo
    took several files back over the limit through it in one merge.
    """
    make_repo(tmp_path)
    (tmp_path / "mod.py").write_text("x = 1\n" * 100)
    (tmp_path / ".file-size-exceptions").write_text('{"exceptions": []}')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "compliant parent")

    # CONTROL: the growth alone blocks, so the plant below is isolating the
    # re-record and nothing else.
    (tmp_path / "mod.py").write_text("x = 1\n" * 600)
    git(tmp_path, "add", "-A")
    control = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert control.returncode == 1, f"the control must block:\n{control.stdout}"

    # PLANT: same growth, plus a baseline this commit writes for itself.
    (tmp_path / ".file-size-exceptions").write_text(
        '{"exceptions": [{"file": "mod.py", "loc": 600}]}'
    )
    git(tmp_path, "add", "-A")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1, (
        f"a commit wrote its own bar and passed:\n{proc.stdout}"
    )
    assert "mod.py" in proc.stdout and "BLOCKING" in proc.stdout
    assert "within size limits" not in proc.stdout


def test_a_raised_bar_is_judged_even_when_no_source_file_is_staged(tmp_path: Path) -> None:
    """The re-record commit shape: it stages the baseline and no .py at all.

    Judging only `staged_py_files` missed this entirely, and it is the shape
    that historically moved a baseline the most ("re-record the baselines after
    the merge" nearly tripled one repo's function baseline).
    """
    make_repo(tmp_path)
    (tmp_path / "mod.py").write_text("x = 1\n" * 100)
    (tmp_path / ".file-size-exceptions").write_text('{"exceptions": []}')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "compliant parent")

    # Nothing but the bar moves. The file on disk is untouched and compliant.
    (tmp_path / ".file-size-exceptions").write_text(
        '{"exceptions": [{"file": "mod.py", "loc": 900}]}'
    )
    git(tmp_path, "add", "-A")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1, (
        f"a bar raised with no source staged went unjudged:\n{proc.stdout}"
    )
    assert "900" in proc.stdout


def test_recording_debt_a_parent_already_carried_warns_and_never_passes_silently(
    tmp_path: Path,
) -> None:
    """The legitimate case must still land -- but visibly.

    A merge landing a branch that genuinely carried an over-limit file needs to
    record it, and blocking that would recreate the "three bypasses and the
    ratchet is dead" failure. So it warns, exit 0. What it must never do again
    is print the all-clear, because that is indistinguishable from a commit
    that authored the debt.
    """
    make_repo(tmp_path)
    (tmp_path / "big.py").write_text("x = 1\n" * 600)
    (tmp_path / ".file-size-exceptions").write_text('{"exceptions": []}')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "the file arrives already big")

    (tmp_path / ".file-size-exceptions").write_text(
        '{"exceptions": [{"file": "big.py", "loc": 600}]}'
    )
    git(tmp_path, "add", "-A")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"recording carried debt must not block:\n{proc.stdout}"
    assert "WARNING" in proc.stdout
    assert "a parent had 600" in proc.stdout
    assert "within size limits" not in proc.stdout, "a raised bar is never a silent ✓"


def test_lowering_a_baseline_stays_silent(tmp_path: Path) -> None:
    """Shrinking is the whole point of the ratchet and must cost nothing."""
    make_repo(tmp_path)
    (tmp_path / "big.py").write_text("x = 1\n" * 600)
    (tmp_path / ".file-size-exceptions").write_text(
        '{"exceptions": [{"file": "big.py", "loc": 600}]}'
    )
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "baselined at 600")

    (tmp_path / "big.py").write_text("x = 1\n" * 550)
    (tmp_path / ".file-size-exceptions").write_text(
        '{"exceptions": [{"file": "big.py", "loc": 550}]}'
    )
    git(tmp_path, "add", "-A")
    proc = run("check_file_sizes.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"a real shrink must pass:\n{proc.stdout}"
    assert "within size limits" in proc.stdout


def test_function_length_bar_cannot_be_self_written_either(tmp_path: Path) -> None:
    """The same hole, the same close, in the sibling checker."""
    make_repo(tmp_path)
    short = "\n".join(f"    x{i} = {i}" for i in range(30))
    (tmp_path / "m.py").write_text(f"def f():\n{short}\n")
    (tmp_path / ".function-length-exceptions").write_text('{"exceptions": {}}')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "compliant parent")

    long_body = "\n".join(f"    x{i} = {i}" for i in range(150))
    (tmp_path / "m.py").write_text(f"def f():\n{long_body}\n")
    (tmp_path / ".function-length-exceptions").write_text(
        '{"exceptions": {"m.py:f": {"file": "m.py", "name": "f",'
        ' "lineno": 1, "lines": 151}}}'
    )
    git(tmp_path, "add", "-A")
    proc = run("check_function_lengths.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1, (
        f"a commit wrote its own function bar and passed:\n{proc.stdout}"
    )
    assert "m.py" in proc.stdout and "BLOCKING" in proc.stdout


# --- check_types: the type-error ratchet -------------------------------------

def _load_check_types():
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_types", SCRIPTS / "check_types.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TY_CONCISE = (
    "hooks/a.py:10:5: error[invalid-assignment] Object of type `str` is not assignable to `int`\n"
    "hooks/a.py:12:5: error[invalid-assignment] Object of type `str` is not assignable to `int`\n"
    "hooks/a.py:20:1: warning[redundant-condition] Condition is always true\n"
    "scripts/b.py:3:8: error[unresolved-import] Cannot resolve imported module `nope`\n"
    "Found 4 diagnostics\n"
)


def test_check_types_counts_per_file_and_rule() -> None:
    ct = _load_check_types()
    counts = ct.count_diagnostics(TY_CONCISE)
    assert counts == {
        ("hooks/a.py", "invalid-assignment"): 2,
        ("hooks/a.py", "redundant-condition"): 1,
        ("scripts/b.py", "unresolved-import"): 1,
    }


def test_check_types_blocks_only_a_rising_count() -> None:
    ct = _load_check_types()
    baseline = ct.count_diagnostics(TY_CONCISE)
    same = ct.judge(ct.count_diagnostics(TY_CONCISE), baseline)
    assert same == ([], [])
    # One more invalid-assignment in a.py, one fewer in b.py: block the rise,
    # report the fall, never net them against each other.
    moved = TY_CONCISE.replace("scripts/b.py:3:8: error[unresolved-import] Cannot resolve imported module `nope`\n", "") \
        + "hooks/a.py:30:5: error[invalid-assignment] again\n"
    blocking, stale = ct.judge(ct.count_diagnostics(moved), baseline)
    assert blocking == [("hooks/a.py", "invalid-assignment", 3, 2)]
    assert stale == [("scripts/b.py", "unresolved-import", 1, 0)]
    # A file the baseline never saw blocks on its first diagnostic.
    fresh = TY_CONCISE + "lib/new.py:1:1: error[unresolved-import] Cannot resolve imported module `x`\n"
    blocking, _ = ct.judge(ct.count_diagnostics(fresh), baseline)
    assert blocking == [("lib/new.py", "unresolved-import", 1, 0)]


@pytest.mark.skipif(
    _load_check_types().find_ty() is None, reason="ty not installed",
)
def test_check_types_end_to_end_against_a_planted_error(tmp_path: Path) -> None:
    """Positive control first: a gate that is green on an empty input proves nothing."""
    (tmp_path / "pyproject.toml").write_text('[tool.ty.src]\ninclude = ["src"]\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    bad = tmp_path / "src" / "bad.py"
    bad.write_text('x: int = "not an int"\nimport nope_not_installed\n', encoding="utf-8")
    proj = str(tmp_path)

    # --staged: nothing staged passes; a staged file blocks on its own error, never on the import
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert run("check_types.py", "--project", proj, "--staged").returncode == 0
    subprocess.run(["git", "add", "src/bad.py"], cwd=tmp_path, check=True)
    staged = run("check_types.py", "--project", proj, "--staged")
    assert staged.returncode == 1 and "invalid-assignment" in staged.stdout
    assert "unresolved-import" not in staged.stdout
    bad.write_text('x: int = "not an int"\n', encoding="utf-8")

    first = run("check_types.py", "--project", proj)
    assert first.returncode == 1, first.stdout + first.stderr
    assert "BLOCK src/bad.py: 1 x invalid-assignment" in first.stdout

    assert run("check_types.py", "--project", proj, "--generate-baseline").returncode == 0
    recorded = json.loads((tmp_path / ".type-exceptions").read_text())
    assert recorded == {"exceptions": [{"file": "src/bad.py", "rule": "invalid-assignment", "count": 1}]}
    assert run("check_types.py", "--project", proj).returncode == 0

    bad.write_text('x: int = "not an int"\ny: int = "nor this"\n', encoding="utf-8")
    assert run("check_types.py", "--project", proj).returncode == 1

    bad.write_text("x: int = 1\n", encoding="utf-8")
    under = run("check_types.py", "--project", proj)
    assert under.returncode == 0
    assert "below baseline src/bad.py" in under.stdout


# --- check_subprocess_results -------------------------------------------------

def _load_subprocess_check():
    import importlib.util
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("check_subprocess_results", SCRIPTS / "check_subprocess_results.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BARE = "import subprocess\n\ndef f():\n    subprocess.run(['x'])\n"


@pytest.mark.parametrize("body,flagged", [
    ("    subprocess.run(['x'])\n", 1),                                   # plant: bare call
    ("    r = subprocess.run(['x'], capture_output=True)\n    return r.stdout\n", 1),   # .stdout only
    ("    r = subprocess.run(['x'])\n    if r:\n        pass\n", 1),      # always truthy
    ("    subprocess.run(['x'], check=False)\n", 1),
    ("    subprocess.run(['x'], check=True)\n", 0),                       # allow
    ("    r = subprocess.run(['x'])\n    return r.returncode == 0\n", 0),
    ("    r = subprocess.run(['x'])\n    return r\n", 0),                 # wrapper returns the result
    ("    r = subprocess.run(['x'])\n    g(r)\n", 0),                     # passed on whole
    ("    subprocess.run(['x'], check=strict)\n", 0),                     # non-literal check
    ("    subprocess.run(['x'], **kw)\n", 0),                             # check via **kwargs
    ("    a = b = subprocess.run(['x'])\n", 1),                           # chained: neither name is read
    ("    a = b = subprocess.run(['x'])\n    return b.stdout\n", 1),      # .stdout only, on either name
    ("    a = b = subprocess.run(['x'])\n    return b\n", 0),              # one name passed on whole
    ("    r = subprocess.run(['x'])\n    assert r\n", 1),                  # an assert on the object is always true
    ("    assert subprocess.run(['x'])\n", 1),
])
def test_subprocess_check_plant_and_allow(body: str, flagged: int) -> None:
    mod = _load_subprocess_check()
    assert len(mod.bare_calls(f"import subprocess\n\ndef f():\n{body}")) == flagged


def test_subprocess_check_sees_aliases() -> None:
    mod = _load_subprocess_check()
    assert len(mod.bare_calls("import subprocess as sp\n\ndef f():\n    sp.run(['x'])\n")) == 1
    assert len(mod.bare_calls("from subprocess import run\n\ndef f():\n    run(['x'])\n")) == 1
    assert mod.bare_calls("def f():\n    run(['x'])\n") == []   # no import of subprocess


def test_subprocess_check_skips_a_dangling_symlink(tmp_path: Path) -> None:
    """A whole-project scan reports the real files even when one `.py` entry is a symlink
    to a path that no longer exists (a stale plugin-cache link): the scan used
    to stop on it with FileNotFoundError and report nothing."""
    sub_repo(tmp_path, {"a.py": "import subprocess\n\ndef f():\n    subprocess.run(['x'])\n"})
    (tmp_path / "gone.py").symlink_to(tmp_path / "no-such-dir" / "__init__.py")
    res = sub_check(tmp_path)
    assert res.returncode == 0, res.stderr
    assert "a.py:" in res.stdout and "Summary: 1 subprocess.run call(s)" in res.stdout


def sub_check(proj: Path, *args: str) -> subprocess.CompletedProcess:
    return run("check_subprocess_results.py", "--project", str(proj), *args)


def sub_repo(tmp_path: Path, files: dict[str, str]) -> str:
    """A repo whose first commit holds `files`; returns that commit's sha."""
    make_repo(tmp_path)
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    return git(tmp_path, "rev-parse", "HEAD").stdout.strip()


def sub_change(tmp_path: Path, base: str, mode: str, edit_tree, check=None) -> subprocess.CompletedProcess:
    """Apply `edit_tree`, put the result in the state `mode` judges, run the check."""
    check = check or sub_check
    edit_tree(tmp_path)
    if mode == "staged":
        git(tmp_path, "add", "-A")
        return check(tmp_path, "--staged")
    if mode == "base":
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-qm", "change")
        return check(tmp_path, "--base", base)
    return check(tmp_path, "--base", base, "--worktree")   # nothing staged, untracked kept


MODES = ["staged", "base", "worktree"]


@pytest.mark.parametrize("mode", MODES)
def test_subprocess_new_bare_call_fails_and_inherited_passes(tmp_path: Path, mode: str) -> None:
    base = sub_repo(tmp_path, {"old.py": BARE})
    ok = sub_change(tmp_path, base, mode, lambda p: (p / "new.py").write_text("x = 1\n"))
    assert ok.returncode == 0, ok.stdout + ok.stderr   # the inherited call alone is not new
    bad = sub_change(tmp_path, base, mode, lambda p: (p / "new.py").write_text(BARE.replace("'x'", "'y'")))
    assert bad.returncode == 1 and "new.py:4" in bad.stdout, bad.stdout + bad.stderr


@pytest.mark.parametrize("mode", MODES)
def test_subprocess_function_rename_passes(tmp_path: Path, mode: str) -> None:
    base = sub_repo(tmp_path, {"a.py": BARE})
    proc = sub_change(tmp_path, base, mode, lambda p: (p / "a.py").write_text(BARE.replace("def f", "def renamed")))
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("mode", MODES)
def test_subprocess_file_move_passes(tmp_path: Path, mode: str) -> None:
    base = sub_repo(tmp_path, {"a.py": BARE})

    def move(p: Path) -> None:
        (p / "sub").mkdir()
        (p / "sub" / "b.py").write_text(BARE)   # untracked destination in worktree mode
        (p / "a.py").unlink()
    proc = sub_change(tmp_path, base, mode, move)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("mode", MODES)
def test_subprocess_moved_file_with_new_call_fails(tmp_path: Path, mode: str) -> None:
    base = sub_repo(tmp_path, {"a.py": BARE})

    def move_and_add(p: Path) -> None:
        (p / "b.py").write_text(BARE + "\ndef g():\n    subprocess.run(['new'])\n")
        (p / "a.py").unlink()
    proc = sub_change(tmp_path, base, mode, move_and_add)
    assert proc.returncode == 1 and "['new']" in proc.stdout, proc.stdout + proc.stderr


def test_subprocess_ratchet_rejects_equal_count_replacement(tmp_path: Path) -> None:
    base = sub_repo(tmp_path, {"a.py": BARE})
    proc = sub_change(tmp_path, base, "staged", lambda p: (p / "a.py").write_text(BARE.replace("'x'", "'other'")))
    assert proc.returncode == 1 and "'other'" in proc.stdout   # one removed, one added: still new


def test_subprocess_second_identical_call_fails(tmp_path: Path) -> None:
    base = sub_repo(tmp_path, {"a.py": BARE})
    proc = sub_change(tmp_path, base, "base", lambda p: (p / "b.py").write_text(BARE))
    assert proc.returncode == 1 and "b.py:4" in proc.stdout   # one more call than the base had


def test_subprocess_staged_reads_index(tmp_path: Path) -> None:
    sub_repo(tmp_path, {"a.py": "x = 1\n"})
    (tmp_path / "a.py").write_text(BARE)
    git(tmp_path, "add", "a.py")
    (tmp_path / "a.py").write_text("x = 1\n")   # fixed on disk, bad version still staged
    proc = sub_check(tmp_path, "--staged")
    assert proc.returncode == 1 and "a.py:4" in proc.stdout


def test_subprocess_ci_mode_fails_new_call(tmp_path: Path) -> None:
    base = sub_repo(tmp_path, {"a.py": "x = 1\n"})
    proc = sub_change(tmp_path, base, "base", lambda p: (p / "a.py").write_text(BARE))
    assert proc.returncode == 1
    unreachable = sub_check(tmp_path, "--base", "0" * 40)
    assert unreachable.returncode == 2   # an unknown base is never a pass
    assert sub_check(tmp_path, "--base", "").returncode == 2


def test_subprocess_staged_listing_failure_is_exit_2(tmp_path: Path) -> None:
    proc = sub_check(tmp_path, "--staged")   # not a git repo: no listing, no pass
    assert proc.returncode == 2


def test_subprocess_worktree_mode_flags_unstaged_call(tmp_path: Path) -> None:
    base = sub_repo(tmp_path, {"old.py": BARE})
    assert sub_check(tmp_path, "--base", base, "--worktree").returncode == 0   # inherited call passes
    (tmp_path / "old.py").write_text(BARE + "\ndef g():\n    subprocess.run(['new'])\n")
    proc = sub_check(tmp_path, "--base", base, "--worktree")
    assert proc.returncode == 1 and "['new']" in proc.stdout


def test_subprocess_ratchet_blocks_commit(tmp_path: Path) -> None:
    """The real githooks/pre-commit refuses a staged bare call, and not a clean one."""
    sub_repo(tmp_path, {"a.py": "x = 1\n"})
    (tmp_path / "scripts").mkdir()
    shutil.copytree(SCRIPTS, tmp_path / "scripts" / "quality", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "scripts" / "verify-routing.sh").write_text("exit 0\n")   # the routing guard is not under test
    hook = tmp_path / "pre-commit"
    shutil.copy(REPO_ROOT / "githooks" / "pre-commit", hook)
    git(tmp_path, "add", "-A")   # the scaffold is part of the base, not of the commit under test
    git(tmp_path, "commit", "-qm", "scaffold")

    def commit_hook() -> subprocess.CompletedProcess:
        git(tmp_path, "add", "-A")
        return subprocess.run(["bash", str(hook)], cwd=tmp_path, capture_output=True, text=True)

    (tmp_path / "a.py").write_text(BARE)
    bad = commit_hook()
    assert bad.returncode == 1 and "COMMIT BLOCKED: check_subprocess_results" in bad.stderr
    (tmp_path / "a.py").write_text(BARE.replace("['x'])", "['x'], check=True)"))
    assert "check_subprocess_results" not in commit_hook().stderr
    # The shared-append ratchet runs in the same loop: a new plain append in hooks/ blocks.
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "h.py").write_text(APPEND)
    appended = commit_hook()
    assert appended.returncode == 1 and "COMMIT BLOCKED: check_shared_append" in appended.stderr
    (tmp_path / "hooks" / "h.py").unlink()
    # A tracked checker that is missing from disk is a broken tree: the hook blocks, it does not skip.
    checker = tmp_path / "scripts" / "quality" / "check_subprocess_results.py"
    checker.unlink()
    (tmp_path / "a.py").write_text(BARE)
    broken = commit_hook()
    assert broken.returncode == 1 and "tracked by git but missing from disk" in broken.stderr
    # A worktree that predates the checker never tracked it: the hook skips it instead of failing the commit.
    # Such a tree has no check_shared_append either (it imports check_subprocess_results).
    (tmp_path / "scripts" / "quality" / "check_shared_append.py").unlink()
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "tree without the checker", "--no-verify")
    (tmp_path / "a.py").write_text(BARE.replace("['x'])", "['x'], check=True)"))
    old_tree = commit_hook()
    assert old_tree.returncode == 0 and "can't open file" not in old_tree.stderr


# --- check_shared_append ------------------------------------------------------

def _load_append_check():
    import importlib.util
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("check_shared_append", SCRIPTS / "check_shared_append.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APPEND = "def f(p):\n    with open(p, 'a') as fh:\n        fh.write('row')\n"


@pytest.mark.parametrize("body,flagged", [
    ("    open(p, 'a').write('x')\n", 1),                                # plant: plain append
    ("    p.open('ab').write(b'x')\n", 1),                               # pathlib, binary
    ("    open(p, mode='a+').write('x')\n", 1),                          # mode keyword
    ("    io.open(p, 'at').write('x')\n", 1),                            # module.open, mode is arg 2
    ("    open(p, 'a').write('x')  # single-writer:\n", 1),              # marker with no reason
    ("    open(p, 'a').write('x')  # single-writer: only this cron job writes it\n", 0),   # allow: marked
    ("    import shared_file\n    shared_file.append_capped(p, 'x', 1024)\n", 0),   # allow: helper
    ("    with open(p, 'a') as fh:\n        fcntl.flock(fh, fcntl.LOCK_EX)\n", 0),  # allow: locked
    ("    open(p, 'w').write('x')\n", 0),
    ("    open(p).read()\n", 0),
    ("    os.open(p, os.O_WRONLY | os.O_APPEND)\n", 0),                  # flags, not a mode (known limit)
    ("    open(p, m).write('x')\n", 0),                                  # non-literal mode (known limit)
])
def test_shared_append_plant_and_allow(body: str, flagged: int) -> None:
    mod = _load_append_check()
    assert len(mod.plain_appends(f"import io, os, fcntl\n\ndef f(p, m):\n{body}")) == flagged


@pytest.mark.parametrize("rel,inside", [
    ("hooks/a.py", True), ("scripts/x/b.py", True), ("skills/s/scripts/c.py", True),
    ("skills/s/scripts/test_c.py", False), ("hooks/c_test.py", False),
    ("skills/s/vendor/c.py", False), ("evals/c.py", False), ("c.py", False),
])
def test_shared_append_scope(rel: str, inside: bool) -> None:
    assert _load_append_check().in_scope(rel) is inside


def append_check(proj: Path, *args: str) -> subprocess.CompletedProcess:
    return run("check_shared_append.py", "--project", str(proj), *args)


@pytest.mark.parametrize("mode", MODES)
def test_shared_append_new_site_fails_and_old_site_passes(tmp_path: Path, mode: str) -> None:
    base = sub_repo(tmp_path, {"hooks/old.py": APPEND})
    untouched = sub_change(tmp_path, base, mode, lambda p: (p / "hooks" / "new.py").write_text("x = 1\n"),
                           append_check)
    assert untouched.returncode == 0, untouched.stdout + untouched.stderr   # the inherited append is not new
    marked = APPEND.replace("as fh:", "as fh:  # single-writer: one test process")
    near = sub_change(tmp_path, base, mode, lambda p: (p / "hooks" / "new.py").write_text(marked), append_check)
    assert near.returncode == 0, near.stdout + near.stderr
    bad = sub_change(tmp_path, base, mode,
                     lambda p: (p / "hooks" / "new.py").write_text(APPEND.replace("'row'", "'other'")
                                                                   .replace("(p, 'a')", "(q, 'a')")),
                     append_check)
    assert bad.returncode == 1 and "hooks/new.py:2" in bad.stdout and "single-writer:" in bad.stdout, \
        bad.stdout + bad.stderr


def test_shared_append_out_of_scope_file_passes(tmp_path: Path) -> None:
    base = sub_repo(tmp_path, {"x.py": "x = 1\n"})
    proc = sub_change(tmp_path, base, "staged",
                      lambda p: (p / "hooks").mkdir() or (p / "hooks" / "test_h.py").write_text(APPEND),
                      append_check)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_shared_append_unknown_base_is_exit_2(tmp_path: Path) -> None:
    sub_repo(tmp_path, {"x.py": "x = 1\n"})
    assert append_check(tmp_path, "--base", "0" * 40).returncode == 2


def test_context_size_blocks_growth_past_the_bound_and_allows_pruning(tmp_path: Path) -> None:
    """Plant: an over-limit CLAUDE.md that grows blocks; a file crossing the bound blocks.
    Control: the same over-limit file shrinking passes; a path-scoped rule is not counted."""
    make_repo(tmp_path)
    (tmp_path / "rules").mkdir()
    (tmp_path / "CLAUDE.md").write_text("x " * 60 + "\n" + "rule\n" * 250)  # 251 lines
    (tmp_path / "rules" / "small.md").write_text("rule\n" * 190)
    (tmp_path / "rules" / "scoped.md").write_text("---\npaths:\n  - src/**\n---\n" + "r\n" * 400)
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "debt exists")

    (tmp_path / "CLAUDE.md").write_text("x " * 60 + "\n" + "rule\n" * 249)
    git(tmp_path, "add", "-A")
    proc = run("check_context_size.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 0, f"pruning an over-limit file must pass:\n{proc.stdout}"

    (tmp_path / "CLAUDE.md").write_text("x " * 60 + "\n" + "rule\n" * 251)
    (tmp_path / "rules" / "scoped.md").write_text("---\npaths:\n  - src/**\n---\n" + "r\n" * 500)
    git(tmp_path, "add", "-A")
    proc = run("check_context_size.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1 and "BLOCK   CLAUDE.md" in proc.stdout
    assert "scoped.md" not in proc.stdout, "a path-scoped rule loads on demand and is not counted"

    git(tmp_path, "reset", "-q", "--hard")
    (tmp_path / "rules" / "small.md").write_text("rule\n" * 210)
    git(tmp_path, "add", "-A")
    proc = run("check_context_size.py", "--project", str(tmp_path), "--staged")
    assert proc.returncode == 1 and "rules/small.md" in proc.stdout, "crossing the bound must block"
