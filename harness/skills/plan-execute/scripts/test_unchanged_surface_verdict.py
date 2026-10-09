"""REGRESSION — an unchanged surface after a FAILED review deadlocked the plan.

example-finish-plan-2026-10-01 / g1-integration, max_rework 2:
  1. cross-family-review-medium failed twice with findings (rework 2/2 spent);
  2. the last round changed no file, and the gate answered INDETERMINATE
     ("nothing new to review") instead of a verdict;
  3. verify.py read that as a TRANSPORT failure and, past its budget, wrote
     outcome "blocked";
  4. plan_mutate only treats passed|halted as settled, so retire-session and
     amend-session refused, and a resume only repeated the indeterminate.

The fix: an unchanged surface keeps its verdict. Open blocking findings from
the last attempt are still open, so the gate FAILS, the rework budget halts the
session with outcome "halted", and the operator can retire it.

This drives the REAL gate (`llm_review_gate.main`) as a verify argv gate in a
subprocess; only the reviewer call is stubbed.
"""
import json
import subprocess
import sys
from pathlib import Path

import plan_mutate as pm
import run
import verifier_park as vpk
import verify as vfy
from test_verify import _begin_doing, _status, _verify_sess, make_plan, write_closeout

SCRIPTS = Path(__file__).resolve().parent

# The stub reviewer raises one HIGH finding every time it is asked. Round 3 must
# never ask: the surface is unchanged, so the verdict is the ledger's, not a new
# sample. The `calls` file proves that.
_GATE = """
import json, sys
sys.path.insert(0, {scripts!r})
import llm_review_gate as g
calls = {calls!r}
def stub(prompt, cwd, timeout):
    open(calls, "a").write("x")
    f = [{{"file": "work.py", "line": 1, "severity": "high", "summary": "unguarded read"}}]
    return {{"result": "REVIEWED_FILES: 1\\n```json\\n" + json.dumps(f) + "\\n```"}}, ""
g.run_once = stub
sys.exit(g.main(["--level", "medium", "--cwd", {repo!r}, "--base", "HEAD",
                 "--plan-dir", {plan!r}, "--session", "s01"]))
"""


def _git_repo(path):
    path.mkdir()
    def git(*a):
        subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)
    git("init", "-q")
    (path / "seed.txt").write_text("seed\n")
    git("add", "seed.txt")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed")
    (path / "work.py").write_text("v1\n")
    return path


def test_an_unchanged_surface_after_a_failed_review_halts_and_can_be_retired(tmp_path, capsys):
    repo = _git_repo(tmp_path / "repo")
    calls = tmp_path / "calls"
    plan_dir = tmp_path / "proj" / "_plans" / "fixture"     # where make_plan builds it
    gate_src = _GATE.format(scripts=str(SCRIPTS), calls=str(calls), repo=str(repo),
                            plan=str(plan_dir))
    gates = {"rev": {"kind": "argv", "indeterminate_exit": 2,
                     "argv": [sys.executable, "-c", gate_src]}}
    assert make_plan(tmp_path, [_verify_sess(["rev"], max_rework=2)], gates=gates) == plan_dir

    closeout = tmp_path / "closeout.txt"
    closeout.write_text(
        '<plan-execute-closeout>\n{"session":"s01","result":"DONE","items_completed":["it-1"],'
        '"items_blocked":[],"notes":{},"dispatch_next":false,"human_checkpoint_reason":null}\n'
        '</plan-execute-closeout>')
    _begin_doing(plan_dir, "s01")
    outs = []
    for content in ("v1\n", "v2\n", "v2\n"):          # round 3 changes nothing
        (repo / "work.py").write_text(content)
        run.cmd_apply(str(plan_dir), "s01", str(closeout))
        capsys.readouterr()
        vfy.verify_begin(plan_dir, "s01")
        outs.append(vfy.verify_run_argv(plan_dir, "s01", "gate:rev"))

    assert [o["action"] for o in outs] == ["rework", "rework", "halted"], outs
    assert not outs[-1].get("transport"), "an unchanged surface is not a transport failure"
    assert calls.read_text() == "xx", "round 3 must re-use the verdict, not re-sample"
    state = json.loads((plan_dir / "_verify_state" / "s01.json").read_text())
    assert state["outcome"] == "halted", state
    assert _status(plan_dir, "s01") == "BLOCKED"

    # Step 4 of the incident: the operator's way out is accepted.
    assert pm.in_flight_state(plan_dir)["verify"] == []
    assert pm.retire_session(plan_dir, "s01", reason="superseded")["retired"] == ["s01"]


def test_a_human_BLOCKED_disposition_settles_the_verify_cycle(tmp_path):
    """The same lock, one writer over: an on-box BLOCKED disposition is final, but
    it wrote outcome `blocked`, so plan_mutate read the session as in flight and
    refused retire/amend. It now writes `halted`, like every other final stop."""
    gates = {"rev": {"kind": "argv", "argv": ["false"]}}
    plan_dir = make_plan(tmp_path, [_verify_sess(["rev"], max_rework=2)], gates=gates)
    _begin_doing(plan_dir, "s01")
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    state_file = plan_dir / "_verify_state" / "s01.json"
    assert pm.in_flight_state(plan_dir)["verify"] == ["s01"]       # control: open cycle

    vpk._resolve_blocked(plan_dir, "s01", json.loads(state_file.read_text()),
                         "gate:rev", vpk.BLOCKED, "cannot verify this by hand")
    assert json.loads(state_file.read_text())["outcome"] == "halted"
    assert pm.in_flight_state(plan_dir)["verify"] == []


# REGRESSION — the PASS twin. example-improve-plan-2026-10-06: both
# land review gates PASSED on attempt 1, the owner acked, main moved, and the
# re-gate saw the same 7 byte-identical files and answered INDETERMINATE. Every
# retry gave the same answer, and an argv gate cannot be waived, so the plan
# could never land. A completed review of THESE bytes that left nothing blocking
# open is still a pass.
_LAND_GATE = """
import sys
sys.path.insert(0, {scripts!r})
import llm_review_gate as g
calls = {calls!r}
def stub(prompt, cwd, timeout):
    open(calls, "a").write("x")
    return {{"result": {result!r}}}, ""
g.run_once = stub
sys.exit(g.main(["--level", "low", "--cwd", {repo!r}, "--base", "HEAD",
                 "--plan-dir", {plan!r}, "--session", "land"]))
"""


def _land_gate(tmp_path, result):
    repo = _git_repo(tmp_path / "repo")
    calls, plan = tmp_path / "calls", tmp_path / "plan"
    calls.write_text("")
    src = _LAND_GATE.format(scripts=str(SCRIPTS), calls=str(calls), repo=str(repo),
                            plan=str(plan), result=result)
    def run_gate():
        return subprocess.run([sys.executable, "-c", src], capture_output=True, text=True)
    return repo, calls, plan, run_gate


def test_an_unchanged_surface_after_a_PASSED_review_keeps_its_pass(tmp_path):
    repo, calls, plan, run_gate = _land_gate(tmp_path, "REVIEWED_FILES: 1\n```json\n[]\n```")
    first = run_gate()
    assert first.returncode == 0, first.stderr
    second = run_gate()                               # main moved; the surface did not
    assert second.returncode == 0, second.stdout + second.stderr
    assert calls.read_text() == "x", "the unchanged re-gate must re-use the verdict, not re-sample"
    assert "PASSED" in second.stdout + second.stderr

    # Control: one changed byte is new code again and IS reviewed.
    (repo / "work.py").write_text("v2\n")
    assert run_gate().returncode == 0 and calls.read_text() == "xx"


def test_a_deleted_surface_file_is_not_an_unchanged_surface(tmp_path):
    """Deleting a file leaves every REMAINING hash equal, so the delta is empty --
    but the surface did change. That must never inherit the old pass."""
    repo, calls, plan, run_gate = _land_gate(tmp_path, "REVIEWED_FILES: 2\n```json\n[]\n```")
    (repo / "extra.py").write_text("x\n")
    assert run_gate().returncode == 0
    (repo / "extra.py").unlink()
    assert run_gate().returncode != 0


def test_a_transport_failed_review_never_becomes_a_pass(tmp_path):
    """The known negative. A reviewer that never answers parseably leaves no
    attempt in the ledger, so the unchanged re-run is attempt 1 again: it asks
    the reviewer again and stays INDETERMINATE."""
    repo, calls, plan, run_gate = _land_gate(tmp_path, "I looked and it seems fine.")
    first = run_gate()
    assert first.returncode == 2, first.stdout + first.stderr
    second = run_gate()
    assert second.returncode == 2, second.stdout + second.stderr
    assert calls.read_text() == "xxxx", "both runs must ask the reviewer (2 tries each)"
