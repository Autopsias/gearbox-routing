"""`govrun --status` — the report that has to be able to say something is wrong.

Split out of test_govrun.py when that file passed the 800-line
test ratchet. It is a clean seam: everything here drives `scripts/govrun_status.py`
and nothing here spawns a governed job for its own sake.

WHY THIS REPORT MATTERS MORE THAN IT LOOKS. The PreToolUse hook FAILS OPEN by
design — a crashing guard must not brick every Bash call on the machine — so
this command is the only place a disarmed governor becomes visible. That makes
a traceback and a clean bill the same non-answer, and it makes every "it looks
fine" line here a liability unless the unhappy version of it is reachable too.
So each verdict below has its negative beside it: AGREE against DIVERGED and
UNKNOWN, `wired=yes` against every malformed settings shape, ARMED against a
deploy that cannot enforce.

The hook's own end of that contract is tested beside the hook, which is not
part of this export.

The fixtures and helpers are in conftest.py, shared with test_govrun.py.
"""
import json

import pytest

from conftest import BUDGET, govrun, wait_for_exec


# --- --status ---------------------------------------------------------------

def _hook_env(tmp_path, literal=None, wired=False):
    settings = tmp_path / "settings.json"
    hook = tmp_path / "governor-hook.py"
    if literal is not None:
        hook.write_text(f"#!/usr/bin/env python3\nSLOT_WORKERS = {literal}\n")
        hook.chmod(0o755)
    wiring = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": f"{hook}"}]}]}} if wired else {}
    settings.write_text(json.dumps(wiring))
    return {"GOVRUN_SETTINGS": str(settings), "GOVRUN_HOOK": str(hook)}


def test_status_shows_free_slots_a_held_slot_and_the_hook_wiring(
        state, tmp_path, jobs):
    env = _hook_env(tmp_path, literal=BUDGET, wired=True)
    idle = govrun(state, "--status", env=env)
    assert idle.returncode == 0
    assert f"slot shared workers={BUDGET} classes=ci,interactive FREE" in idle.stdout
    assert "hook: wired=yes" in idle.stdout
    assert "exists=yes executable=yes" in idle.stdout

    jobs(state, ["sleep", "5"])
    pid = wait_for_exec(state, "shared", "sleep")
    busy = govrun(state, "--status", env=env)
    assert f"slot shared workers={BUDGET} classes=ci,interactive HELD" in busy.stdout
    assert f"pid={pid} alive" in busy.stdout
    assert "cmd=sleep 5" in busy.stdout


@pytest.mark.parametrize("literal,verdict", [(BUDGET, "AGREE"), (8, "DIVERGED"),
                                             (None, "UNKNOWN")])
def test_status_compares_the_effective_budget_with_the_hooks_literal(
        state, tmp_path, literal, verdict):
    """The hook inlines the budget as a literal to stay under ~200 ms, so an
    override here can silently desynchronise enforcement from reality. The probe script's
    wiring probe asserts AGREE — which is only meaningful if DIVERGED is
    reachable, so both are exercised."""
    env = _hook_env(tmp_path, literal=literal, wired=literal is not None)
    result = govrun(state, "--status", env=env)
    assert f"| {verdict}" in result.stdout, result.stdout


@pytest.mark.parametrize("settings_body,expect", [
    ({"hooks": [1, 2, 3]}, "malformed"),
    ({"hooks": "PreToolUse"}, "malformed"),
    ({"hooks": {"PreToolUse": {"matcher": "Bash"}}}, "malformed"),
    ([1, 2, 3], "malformed"),
    ("just a string", "malformed"),
    ({}, "no"),
])
def test_status_never_crashes_on_a_malformed_settings_file(
        state, tmp_path, settings_body, expect):
    """settings.json is rewritten by ANOTHER program (the Claude Code binary),
    so bad shapes are reachable without anyone hand-editing it. `--status` is
    the only liveness check for a hook that FAILS OPEN — a traceback and a
    clean bill are the same non-answer, so it must report the problem."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps(settings_body))
    result = govrun(state, "--status", env={"GOVRUN_SETTINGS": str(settings)})
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert f"hook: wired={expect}" in result.stdout, result.stdout


def test_status_reports_a_settings_file_that_is_not_valid_json(state, tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json")
    result = govrun(state, "--status", env={"GOVRUN_SETTINGS": str(settings)})
    assert result.returncode == 0, result.stderr
    assert "hook: wired=unreadable" in result.stdout


def test_status_tails_the_hook_error_log(state, tmp_path):
    (state / "hook-errors.log").write_text("\n".join(f"line{i}" for i in range(6)))
    result = govrun(state, "--status", env=_hook_env(tmp_path))
    assert "hook-errors.log (last 3" in result.stdout
    assert "line5" in result.stdout and "line3" in result.stdout
    assert "line2" not in result.stdout




def test_status_runs_the_hook_and_reports_what_it_actually_decides(
        state, tmp_path):
    """End to end through the CLI, against a hook that carries the budget
    LITERAL and nothing else. The literal check reads it happily and says
    AGREE; running it proves it enforces nothing. Both lines print, and they
    disagree — which is the point, because previously only the first
    one existed and a hook that could not even load still reported AGREE.

    The ARMED half against the real hook is tested beside the hook.
    """
    env = _hook_env(tmp_path, literal=BUDGET, wired=True)
    result = govrun(state, "--status", env=env)
    assert result.returncode == 0, result.stderr
    assert "| AGREE" in result.stdout, result.stdout
    assert "enforcement: DISARMED" in result.stdout, result.stdout
    assert "was NOT refused" in result.stdout, result.stdout
