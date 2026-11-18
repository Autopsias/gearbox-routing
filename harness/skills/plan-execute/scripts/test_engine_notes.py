"""ENG-05 — seven old memory notes about the plan engine, each probed against today's code.

One test per note. A passing test means the note's warning no longer holds; a strict xfail
means the fault is still open (the reason names what a fix needs).

Run: pytest skills/plan-execute/scripts/test_engine_notes.py -q
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILDER = SCRIPTS.parents[1] / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILDER))

import article_block as ab  # noqa: E402
import build_canary as bc  # noqa: E402
import build_plan  # noqa: E402
import dispatch as dsp  # noqa: E402
import manifest_io as mio  # noqa: E402
import run  # noqa: E402
import run_state_io as rsi  # noqa: E402
import shipping as shp  # noqa: E402
import verify as vfy  # noqa: E402
from test_route_at_dispatch_build import _V8  # noqa: E402
from test_shipping import make_plan, write_closeout  # noqa: E402

S1 = {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"}
S2 = {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
      "dispatch": {"depends_on": ["s01"]}}
PID = "sonnet-low-std-20261010"


def _statuses(plan_dir):
    return ab.read_all_statuses((Path(plan_dir) / "PLAN.html").read_text())


def _halted(plan_dir):
    return rsi.load_state(plan_dir)["halt"]["set"]


# Note 1 — reference_depends_on_policy_is_inert_20260803 (example-project)
# Claim: depends_on_policy is read by nothing; only DONE/WONTFIX/DEFERRED release a dependency.
def test_n1_depends_on_policy_is_read_by_dispatch():
    def manifest(policy):
        d = {"depends_on": ["s01"]}
        if policy:
            d["depends_on_policy"] = policy
        return {"sessions": [{"id": "s01", "dispatch": {}}, {"id": "s02", "dispatch": d}]}

    blocked = {"s01": "BLOCKED", "s02": "TODO"}
    assert [s["id"] for s in dsp.ready_sessions(manifest("all"), blocked)] == []
    assert [s["id"] for s in dsp.ready_sessions(manifest("completed_or_terminal"), blocked)] == ["s02"]


# Note 2 — plan-rebuild-wipes-session-status (Gearbox)
# Claim: build_plan --rebuild --preserve-state resets every session and item to TODO.
def test_n2_rebuild_preserve_state_keeps_status(tmp_path):
    plan_dir = make_plan(tmp_path, [S1, S2])
    ab.apply_mutation(plan_dir / "PLAN.html", "s01", status="DONE", note="n")
    ab.apply_mutation(plan_dir / "PLAN.html", "w-01", status="DONE", note="n")
    spec = json.loads((plan_dir / "spec.json").read_text())
    build_plan.build(spec, plan_dir, preserve_state=True)
    got = _statuses(plan_dir)
    assert got["s01"] == "DONE" and got["w-01"] == "DONE" and got["s02"] == "TODO"


# Note 3 — feedback_plan_builder_rebuild_drops_canary_20261011 (example-project)
# Claim: --rebuild drops the routing_experiment tags and resets max_rework 4 -> 2.
def test_n3_rebuild_from_the_plans_own_spec_keeps_canary(tmp_path, monkeypatch):
    monkeypatch.delenv("PLAN_BUILDER_BUILD_CANARY", raising=False)
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "eval-gates.json").write_text(json.dumps(
        {"smoke": {"kind": "argv", "argv": ["true"]}}))
    (project / ".claude" / "deploy-targets.json").write_text("{}")
    spec = copy.deepcopy(_V8)
    spec["sessions"] = spec["sessions"][:1]
    spec["sessions"][0]["verify"] = {"gates": ["smoke"]}
    spec["items"] = [i for i in spec["items"] if i["id"] in spec["sessions"][0]["items"]]
    tagged = bc.apply(copy.deepcopy(spec), ledger=lambda: {PID: "smoke-in-progress"})
    assert tagged["sessions"][0].get("routing_experiment"), "control: fresh build must tag"
    plan_dir = project / "_plans" / "canary"
    build_plan.build(tagged, plan_dir, project_root=str(project))
    # the note's cycle: edit the plan's own spec.json, then run the CLI with --rebuild
    spec_path = plan_dir / "spec.json"
    edited = json.loads(spec_path.read_text())
    edited["subtitle"] = "hardened"
    spec_path.write_text(json.dumps(edited))
    env = {**os.environ, "PLAN_BUILDER_BUILD_CANARY": ""}
    r = subprocess.run([sys.executable, str(BUILDER / "build_plan.py"), str(spec_path), str(plan_dir),
                        "--rebuild", "--preserve-state"], capture_output=True, text=True, env=env,
                       cwd=str(project))
    assert r.returncode == 0, r.stderr + r.stdout
    s = mio.session_by_id(mio.load_manifest(plan_dir))["s01"]
    assert s["routing_experiment"]["proposal_id"] == PID
    assert s["verify"]["max_rework"] == 4


# Note 4 — a-passing-verify-gate-leaves-no-tally (profile-a-brain)
# Claim: verify-run keeps a gate's output only when the gate FAILS; a pass leaves no tally.
@pytest.mark.xfail(strict=True, reason=(
    "OPEN: _run_gate on a pass saves only the verdict (verify.py `outcome == 'pass'` branch). "
    "A fix stores the redacted output tail under the gate in the verify state on pass; "
    "verify.py has about 27 lines of room but the state schema and its readers need a decision."))
def test_n4_passing_gate_output_is_kept(tmp_path):
    from test_verify import make_plan as make_verify_plan, _verify_sess
    gate = {"suite": {"kind": "argv", "argv": ["sh", "-c", "echo 42 passed in 1.0s"]}}
    plan_dir = make_verify_plan(tmp_path, [_verify_sess(["suite"])], gates=gate)
    write_closeout(plan_dir, "s01")
    vfy.verify_begin(plan_dir, "s01")
    out = vfy.verify_run_argv(plan_dir, "s01", "gate:suite")
    assert out["action"] == "passed"
    blob = "".join(p.read_text(errors="replace") for p in Path(plan_dir).rglob("*")
                   if p.is_file() and p.suffix in {".json", ".ndjson", ".md"} and "_verify_state" in str(p)
                   or p.name == "run.ndjson")
    assert "42 passed" in blob


# Note 5 — plan-execute-evidence-paths-cwd (profile-a-brain)
# Claim: a repo-root-relative evidence path false-fails when the orchestrator's cwd is the plan dir.
def test_n5_repo_root_relative_evidence_resolves_from_plan_dir_cwd(tmp_path, monkeypatch):
    plan_dir = make_plan(tmp_path, [{**S1, "verify": {"require_evidence": True}}])
    root = plan_dir.parents[1]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "docs").mkdir()
    (root / "docs" / "proof.md").write_text("engaged\n")
    monkeypatch.chdir(plan_dir)
    why = vfy.evp.artifact_problem(str(plan_dir), "s01", "docs/proof.md", require_fresh=False)
    assert why is None or "does not exist" not in why, why


# Note 6 — apply-needs-a-clean-closeout-file-not-the-output (profile-a-brain)
# Claim: apply --output-file on a transcript (closeout followed by trailing metadata) sets the halt flag.
@pytest.mark.xfail(strict=True, reason=(
    "OPEN: cmd_apply BLOCKs the session and halts on any bad closeout file, trailing text "
    "included; test_apply_interim_stop i3/i4 pin the halt for a MISSING or MALFORMED closeout. "
    "A fix needs a decision: refuse (no halt) when a well-formed block is only followed by "
    "trailing text, like apply_guards already does for an interim stop."))
def test_n6_trailing_text_after_closeout_does_not_halt(tmp_path, capsys):
    plan_dir = make_plan(tmp_path, [S1])
    block = ('<plan-execute-closeout>\n{"session":"s01","result":"DONE","items_completed":["w-01"],'
             '"items_blocked":[],"notes":{},"dispatch_next":false,"human_checkpoint_reason":null}\n'
             '</plan-execute-closeout>')
    f = tmp_path / "agent.output"
    f.write_text(block + '"}],"stop_reason":null,"stop_sequence":null}')
    try:
        run.cmd_apply(str(plan_dir), "s01", str(f))
    except SystemExit:
        pass
    capsys.readouterr()
    assert not _halted(plan_dir)


# Note 7 — plan-execute-ship-record-sequencing (profile-a-brain)
# Claim: ship-record after a deferred ship-begin (no shipping state) sets a spurious halt.
def test_n7_ship_record_with_no_state_does_not_halt(tmp_path):
    sessions = [{**S1, "dispatch": {"requires_human_checkpoint": True,
                                    "checkpoint": {"reason": "Irreversible.", "decision": "Ship?"}},
                 "post_session": {"git": "commit"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    assert shp.ship_begin(plan_dir, "s01")["action"] == "deferred"
    shp.ship_record(plan_dir, "s01", "commit", "done")
    assert not _halted(plan_dir)
