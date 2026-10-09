"""Smallest checks that fail if the quality checkers' core logic breaks."""

from __future__ import annotations

import json
import re
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
