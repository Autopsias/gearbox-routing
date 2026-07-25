"""Verification suite for shipping-action awareness (plan §Verification).

Covers the deterministic steps that don't need a live orchestrator:
  1  build-time resolution of inherited post_session
  5  deploy-target-missing at runtime -> skipped + halt
  7  parallel-shipping serialization (resource lock blocks a foreign holder)
  8  idempotent resume (push fails -> resume at push, no duplicate commit)
  9  --dry-run-shipping discovery (no file writes)
 11  checkpoint precedence (deploy + checkpoint -> deferred, fires only on resume)
 12  state-drift refusal (stale digest -> refuse, not skip-as-shipped)
 13  secret redaction in the persisted stderr excerpt
 14  adapter capability probe against the live /commit-orchestrate + /pr

Run: pytest plan-execute/scripts/test_shipping.py -q
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
BUILD_PLAN = SCRIPTS.parent.parent / "plan-builder" / "scripts"
sys.path.insert(0, str(BUILD_PLAN))

import build_plan  # noqa: E402
import closeout_pipeline as cp  # noqa: E402
import manifest_io as mio  # noqa: E402
import ship_state_io as ssio  # noqa: E402
import shipping as shp  # noqa: E402
import shipping_adapter as adapter  # noqa: E402


# --------------------------------------------------------------------------
# Fixture builder
# --------------------------------------------------------------------------
def _spec(sessions, phases=None):
    items = sorted({iid for s in sessions for iid in s.get("items", [])})
    return {
        "title": "Shipping Fixture Plan",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [{"id": iid, "title": iid.upper(), "category": "work"} for iid in items],
        "phases": phases or [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey", "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": items[:1]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }


def _write_registries(project_root, deploy=None, gates=None):
    cl = project_root / ".claude"
    cl.mkdir(parents=True, exist_ok=True)
    (cl / "deploy-targets.json").write_text(json.dumps(deploy or {}))
    (cl / "eval-gates.json").write_text(json.dumps(gates or {}))


def make_plan(tmp_path, sessions, *, phases=None, deploy=None, gates=None):
    """Build a real plan dir (via build_plan) inside an isolated project root."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_registries(project_root, deploy, gates)
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec(sessions, phases), plan_dir, project_root=str(project_root))
    return plan_dir


def write_closeout(plan_dir, session_id, *, result="DONE", completed=None):
    """Persist a verified closeout record (with _closeout_digest) for a session."""
    manifest = mio.load_manifest(plan_dir)
    items = mio.session_by_id(manifest)[session_id]["items"]
    completed = items if completed is None else completed
    parsed = {"session": session_id, "result": result, "items_completed": completed,
              "items_blocked": [i for i in items if i not in completed], "notes": {}}
    cp.persist(plan_dir, session_id, parsed)


GATE_OK = {"smoke": {"kind": "argv", "argv": ["true"],
                     "fixture_fake": {"returncode": 0, "stdout": "PASS"}}}
DEPLOY_OK = {"test-ec2": {"kind": "argv", "argv": ["true"], "cwd": ".",
                         "fixture_fake": {"returncode": 0, "stdout": "deployed"}}}


# --------------------------------------------------------------------------
# 1 — build-time resolution of inherited post_session
# --------------------------------------------------------------------------
def test_phase_closer_inheritance_and_override(tmp_path):
    sessions = [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
         "phase": "p1"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
         "phase": "p1", "post_session": {"git": "commit"}},  # override
        {"id": "s03", "title": "S3", "model": "Sonnet", "items": ["w-03"], "prompt": "do"},  # none
    ]
    phases = [{"id": "p1", "phase_closer": {"git": "commit-push-pr"}}]
    plan_dir = make_plan(tmp_path, sessions, phases=phases)
    manifest = mio.load_manifest(plan_dir)
    by_id = mio.session_by_id(manifest)
    assert by_id["s01"]["post_session"]["git"] == "commit-push-pr"   # inherited
    assert by_id["s02"]["post_session"]["git"] == "commit"           # overridden
    assert "post_session" not in by_id["s03"]                        # opt-in: nothing
    # The prompt file carries the Post-session actions section for shipping sessions.
    prompt = (plan_dir / "sessions" / "s01.prompt.md").read_text()
    assert "Post-session actions" in prompt and "commit-push-pr" in prompt
    assert "Post-session actions" not in (plan_dir / "sessions" / "s03.prompt.md").read_text()


def test_deploy_auth_digest_emitted_for_deploy_sessions(tmp_path):
    sessions = [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do",
         "dispatch": {"depends_on": ["s01"]},
         "post_session": {"git": "commit-push", "deploy": "test-ec2",
                          "pre_deploy_gates": ["smoke"], "rollback_hint": "git revert HEAD"}},
    ]
    plan_dir = make_plan(tmp_path, sessions, deploy=DEPLOY_OK, gates=GATE_OK)
    ps = mio.session_by_id(mio.load_manifest(plan_dir))["s02"]["post_session"]
    assert "deploy_auth_digest" in ps and len(ps["deploy_auth_digest"]) == 64


def test_build_rejects_unknown_deploy_target(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit", "deploy": "ghost", "rollback_hint": "x"}}]
    with pytest.raises(ValueError, match="ghost"):
        make_plan(tmp_path, sessions)  # empty registries -> ghost unresolved


def test_build_rejects_deploy_without_rollback_hint(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit", "deploy": "test-ec2"}}]  # no rollback_hint
    with pytest.raises(ValueError, match="rollback_hint"):
        make_plan(tmp_path, sessions, deploy=DEPLOY_OK)


# --------------------------------------------------------------------------
# 5 — deploy-target-missing at runtime -> skipped + halt
# --------------------------------------------------------------------------
def test_runtime_deploy_target_missing(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit", "deploy": "test-ec2",
                                  "rollback_hint": "git revert HEAD"}}]
    plan_dir = make_plan(tmp_path, sessions, deploy=DEPLOY_OK)
    write_closeout(plan_dir, "s01")
    # The target vanishes between build and run.
    (plan_dir.parent.parent / ".claude" / "deploy-targets.json").write_text("{}")
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "skipped" and out["reason"] == "deploy-target-missing"
    from run_state_io import is_halted
    assert is_halted(plan_dir)


# --------------------------------------------------------------------------
# 7 — resource lock blocks a foreign holder (serialization)
# --------------------------------------------------------------------------
def test_resource_lock_blocks_foreign_holder(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit-push"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    # Pre-plant a FOREIGN, live lock on the git resource (pid 1 = always alive).
    root = shp.find_project_root(plan_dir)
    lp = plan_dir / "_shipping_locks" / ssio._resource_slug(f"git:{root}")
    lp = lp.with_suffix(".lock")
    lp.parent.mkdir(parents=True, exist_ok=True)
    import socket
    from datetime import UTC, datetime
    lp.write_text(json.dumps({"pid": 1, "started_at": datetime.now(UTC).isoformat(),
                              "host": socket.gethostname(), "resource": f"git:{root}"}))
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "locked"


# --------------------------------------------------------------------------
# 8 — idempotent resume: push fails -> resume at push, no duplicate commit
# --------------------------------------------------------------------------
def test_idempotent_resume_after_push_failure(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit-push", "deploy": "test-ec2",
                                  "rollback_hint": "git revert HEAD"}}]
    plan_dir = make_plan(tmp_path, sessions, deploy=DEPLOY_OK)
    write_closeout(plan_dir, "s01")

    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "invoke-skill" and out["step"] == "commit"
    out = shp.ship_record(plan_dir, "s01", "commit", "done")
    assert out["step"] == "push"
    out = shp.ship_record(plan_dir, "s01", "push", "failed")
    assert out["action"] == "failed" and out["failed_step"] == "push"

    state = ssio.load_ship_state(plan_dir, "s01")
    assert state["steps"]["commit"] == "done" and state["steps"]["push"] == "failed"

    # Resume: must restart at push (commit stays done), then run deploy once.
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "invoke-skill" and out["step"] == "push"
    out = shp.ship_record(plan_dir, "s01", "push", "done")
    assert out["action"] == "run-argv" and out["step"] == "deploy"
    out = shp.ship_run_argv(plan_dir, "s01", "deploy")
    assert out["action"] == "done"
    state = ssio.load_ship_state(plan_dir, "s01")
    assert state["steps"] == {"commit": "done", "push": "done", "deploy": "done"}


def test_already_shipped_is_idempotent(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    shp.ship_record  # noqa
    out = shp.ship_begin(plan_dir, "s01")
    assert out["step"] == "commit"
    shp.ship_record(plan_dir, "s01", "commit", "done")  # -> all done -> finalize
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "already-shipped"


# --------------------------------------------------------------------------
# 9 — --dry-run-shipping discovery (no file writes)
# --------------------------------------------------------------------------
def test_dry_run_shipping_writes_nothing(tmp_path, capsys):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    _write_registries(project_root, DEPLOY_OK, GATE_OK)
    spec = _spec([
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
         "post_session": {"git": "commit-push", "deploy": "test-ec2",
                          "pre_deploy_gates": ["smoke"], "rollback_hint": "revert"}},
    ])
    before = set(project_root.rglob("*"))
    build_plan._print_shipping_dry_run(spec, str(project_root))
    after = set(project_root.rglob("*"))
    out = capsys.readouterr().out
    assert "git:commit-push" in out and "deploy:test-ec2" in out and "gate:smoke" in out
    assert before == after  # nothing written


# --------------------------------------------------------------------------
# 11 — checkpoint precedence: deploy + checkpoint -> deferred, fires on resume
# --------------------------------------------------------------------------
def test_checkpoint_outranks_shipping(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "dispatch": {"requires_human_checkpoint": True,
                              "checkpoint": {"reason": "Deploy is irreversible.",
                                             "decision": "Ship now or hold?"}},
                 "post_session": {"git": "commit", "deploy": "test-ec2",
                                  "rollback_hint": "revert"}}]
    plan_dir = make_plan(tmp_path, sessions, deploy=DEPLOY_OK)
    write_closeout(plan_dir, "s01")
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "deferred" and out["reason"].startswith("checkpoint")
    # No shipping state created, nothing deployed, no lock acquired.
    assert ssio.load_ship_state(plan_dir, "s01") is None
    assert ssio.held_ship_locks(plan_dir) == []
    # Resume past the checkpoint -> shipping proceeds.
    out = shp.ship_begin(plan_dir, "s01", resume=True)
    assert out["action"] in ("invoke-skill", "run-argv")


# --------------------------------------------------------------------------
# 12 — state-drift refusal (stale digest -> refuse, not skip-as-shipped)
# --------------------------------------------------------------------------
def test_state_drift_refused(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    # Write a shipping state with all steps done but a STALE manifest digest.
    ssio.save_ship_state(plan_dir, "s01", {
        "session_id": "s01", "manifest_digest": "0" * 64, "closeout_digest": "0" * 64,
        "result": "DONE", "declared_steps": ["commit"], "steps": {"commit": "done"},
        "command_failure_mode": "fail-halt", "failures": {}, "started_at": "x",
    })
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "failed" and out["reason"] == "state-drift"


# --------------------------------------------------------------------------
# 13 — secret redaction
# --------------------------------------------------------------------------
def test_redact_strips_credentials():
    raw = ("fatal: remote error ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab pushing to "
           "https://alice:s3cr3tpw@github.com/x/y.git AKIAIOSFODNN7EXAMPLE bearer abc.def.ghi")
    red = adapter.redact(raw)
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in red
    assert "s3cr3tpw" not in red
    assert "AKIAIOSFODNN7EXAMPLE" not in red
    assert "REDACTED" in red


def test_failed_argv_step_redacts_stderr_in_state(tmp_path):
    leaky = {"leaky": {"kind": "argv", "argv": ["true"],
                       "fixture_fake": {"returncode": 1,
                                        "stderr": "boom ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab "
                                                  "https://u:p@h/x"}}}
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "none", "pre_deploy_gates": ["leaky"]}}]
    plan_dir = make_plan(tmp_path, sessions, gates=leaky)
    write_closeout(plan_dir, "s01")
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "run-argv" and out["step"] == "gate:leaky"
    out = shp.ship_run_argv(plan_dir, "s01", "gate:leaky")
    assert out["action"] == "failed"
    excerpt = ssio.load_ship_state(plan_dir, "s01")["failures"]["gate:leaky"]["stderr_excerpt"]
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in excerpt and "u:p@h" not in excerpt
    # The run.ndjson event must also be redacted.
    events = (plan_dir / "run.ndjson").read_text()
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in events


# --------------------------------------------------------------------------
# 14 — adapter capability probe against live skills
# --------------------------------------------------------------------------
def test_adapter_probe_passes_on_live_skills():
    for _step, res in adapter.probe_git_value("commit-push-pr"):
        assert res["ok"], f"probe failed: {res}"


def test_adapter_probe_fails_on_missing_flag():
    res = adapter.probe_skill_flags("commit-orchestrate", ["--definitely-not-a-real-flag"])
    assert not res["ok"] and "--definitely-not-a-real-flag" in res["missing"]


def test_adapter_probe_reports_missing_skill():
    res = adapter.probe_skill_flags("no-such-skill-xyz", ["--x"])
    assert not res["ok"] and res.get("error") == "skill-not-found"


# --------------------------------------------------------------------------
# best-effort failure mode never blocks
# --------------------------------------------------------------------------
def test_best_effort_deploy_never_blocks(tmp_path):
    target = {"hook": {"kind": "argv", "argv": ["true"], "command_failure_mode": "best-effort",
                       "fixture_fake": {"returncode": 7, "stderr": "ignored"}}}
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "none", "deploy": "hook",
                                  "command_failure_mode": "best-effort", "rollback_hint": "n/a"}}]
    plan_dir = make_plan(tmp_path, sessions, deploy=target)
    write_closeout(plan_dir, "s01")
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "run-argv"
    out = shp.ship_run_argv(plan_dir, "s01", "deploy")
    assert out["action"] == "done"  # non-zero exit ignored


# --------------------------------------------------------------------------
# 4 (headless surrogate) — a REAL command runs end-to-end via the argv path
# against an actual git repo (proves run_deploy_argv, allow-listed env, real
# state transition). The git-skill steps still need the orchestrator; this
# covers the deterministic argv half.
# --------------------------------------------------------------------------
def test_real_git_command_runs_via_argv(tmp_path):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    # A lightweight tag needs a commit to point at.
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-q", "-m", "init"], cwd=project_root, check=True)
    _write_registries(project_root, deploy={
        "tag-it": {"kind": "argv", "argv": ["git", "tag", "shipped-s01"], "cwd": "."}})
    plan_dir = project_root / "_plans" / "fixture"
    build_plan.build(_spec([
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
         "post_session": {"git": "none", "deploy": "tag-it", "rollback_hint": "git tag -d shipped-s01"}},
    ]), plan_dir, project_root=str(project_root))
    write_closeout(plan_dir, "s01")

    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "run-argv" and out["step"] == "deploy"
    out = shp.ship_run_argv(plan_dir, "s01", "deploy")
    assert out["action"] == "done"
    tags = subprocess.run(["git", "tag"], cwd=project_root, capture_output=True, text=True).stdout
    assert "shipped-s01" in tags  # the real git command actually ran


# --------------------------------------------------------------------------
# 2 + 3 — simulate mode produces real events end-to-end (no destructive skills)
# --------------------------------------------------------------------------
def test_simulate_produces_started_and_completed_events(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "commit-push", "deploy": "test-ec2",
                                  "pre_deploy_gates": ["smoke"], "rollback_hint": "git revert HEAD"}}]
    plan_dir = make_plan(tmp_path, sessions, deploy=DEPLOY_OK, gates=GATE_OK)
    write_closeout(plan_dir, "s01")
    out = shp.ship_simulate(plan_dir, "s01")
    assert out["action"] == "done"
    assert out["_simulated_steps"] == ["commit", "push", "gate:smoke", "deploy"]
    state = ssio.load_ship_state(plan_dir, "s01")
    assert all(v == "done" for v in state["steps"].values())
    events = [json.loads(ln)["event"] for ln in (plan_dir / "run.ndjson").read_text().splitlines()]
    assert "post_session_started" in events and "post_session_completed" in events


def test_simulate_respects_checkpoint(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "dispatch": {"requires_human_checkpoint": True,
                              "checkpoint": {"reason": "Deploy is irreversible.",
                                             "decision": "Ship now or hold?"}},
                 "post_session": {"git": "commit"}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    out = shp.ship_simulate(plan_dir, "s01")
    assert out["action"] == "deferred"


# --------------------------------------------------------------------------
# monitoring — shipping summary surfaces per-session badges + recent events
# --------------------------------------------------------------------------
def test_shipping_summary(tmp_path):
    sessions = [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
         "post_session": {"git": "commit"}},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do"},
    ]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01")
    shp.ship_simulate(plan_dir, "s01")
    summary = shp.shipping_summary(plan_dir, mio.load_manifest(plan_dir))
    assert summary["sessions"] == {"s01": "committed"}        # s02 has no shipping
    assert any(e["event"] == "post_session_completed" for e in summary["recent_events"])


# --------------------------------------------------------------------------
# skip_if_partial
# --------------------------------------------------------------------------
def test_skip_if_partial_skips_shipping(tmp_path):
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01", "w-02"],
                 "prompt": "do", "post_session": {"git": "commit", "skip_if_partial": True}}]
    plan_dir = make_plan(tmp_path, sessions)
    write_closeout(plan_dir, "s01", result="PARTIAL", completed=["w-01"])
    out = shp.ship_begin(plan_dir, "s01")
    assert out["action"] == "skipped" and out["reason"] == "skip-if-partial"


# --------------------------------------------------------------------------
# LW-01 — ship-tail deploy target + test-orchestrate verify-gate (s05)
# --------------------------------------------------------------------------

def test_probe_registry_entry_passes_for_ship_tail_bundled_default():
    """The bundled ship-tail deploy target's probe_flags must all exist in the
    live ship-tail skill — proves the CI-loop rung (ci-loop.md, CI_MAX_CYCLES)
    is present and wired, not a TODO seam."""
    import json
    from pathlib import Path
    bundled = Path(adapter.__file__).resolve().parent.parent / "references" / "deploy-targets.default.json"
    assert bundled.is_file(), f"bundled deploy-targets.default.json not found at {bundled}"
    reg = json.loads(bundled.read_text())
    assert "ship-tail" in reg, "ship-tail entry missing from deploy-targets.default.json"
    entry = reg["ship-tail"]
    res = adapter.probe_registry_entry("ship-tail", entry)
    assert res["ok"], (
        f"ship-tail deploy target probe FAILED — probe_flags {res.get('missing')} "
        f"absent from ship-tail skill. This means the CI-loop rung is not wired "
        f"(believed-but-dead). Locate ship-tail.md and add the missing text, or "
        f"update deploy-targets.default.json probe_flags to match the live interface."
    )


def test_probe_registry_entry_fails_when_ci_rung_flags_absent():
    """Adversarial hardening (s05): a ship-tail-like entry whose probe_flags
    reference CI-rung markers MUST FAIL if those markers are removed. This
    ensures Lane B can never register a 'full ship-tail' that silently points
    at the s01 TODO seam (no CI loop). The probe must be load-bearing — not
    documentation."""
    # Craft a synthetic entry identical to ship-tail but with a probe flag that
    # is guaranteed absent from any real skill file: the CI rung marker combined
    # with a sentinel. We check the *negative* path — the probe fails loud.
    fake_entry = {
        "kind": "skill",
        "skill": "ship-tail",
        "args": "--skip-tests",
        "probe_flags": ["ci-loop.md", "--THIS-FLAG-DOES-NOT-EXIST-CI-RUNG-SENTINEL"],
    }
    res = adapter.probe_registry_entry("ship-tail", fake_entry)
    assert not res["ok"], "probe should have FAILED when CI rung sentinel flag is absent"
    assert "--THIS-FLAG-DOES-NOT-EXIST-CI-RUNG-SENTINEL" in res["missing"]


def test_probe_registry_entry_passes_for_test_orchestrate_bundled_gate():
    """The bundled test-orchestrate verify-gate's probe_flags must exist in the
    live test-orchestrate skill."""
    import json
    from pathlib import Path
    bundled = Path(adapter.__file__).resolve().parent.parent / "references" / "eval-gates.default.json"
    assert bundled.is_file(), f"bundled eval-gates.default.json not found at {bundled}"
    reg = json.loads(bundled.read_text())
    assert "test-orchestrate" in reg, "test-orchestrate entry missing from eval-gates.default.json"
    entry = reg["test-orchestrate"]
    res = adapter.probe_registry_entry("test-orchestrate", entry)
    assert res["ok"], (
        f"test-orchestrate verify-gate probe FAILED — probe_flags {res.get('missing')} "
        f"absent from test-orchestrate skill (adapter-contract-drift)."
    )


def test_probe_registry_entry_argv_kind_always_ok():
    """argv-kind entries have no skill file to probe; probe must return ok=True."""
    entry = {"kind": "argv", "argv": ["true"], "probe_flags": ["--irrelevant"]}
    res = adapter.probe_registry_entry("some-argv-target", entry)
    assert res["ok"] and res.get("reason") == "argv-kind-no-probe"


def test_probe_registry_entry_skill_kind_missing_skill_field_fails():
    """A skill-kind entry without an explicit 'skill' field must fail loud — never
    infer the skill name from the entry id (explicit metadata required)."""
    entry = {"kind": "skill", "probe_flags": ["--something"]}
    res = adapter.probe_registry_entry("bad-entry", entry)
    assert not res["ok"] and "skill" in res.get("error", "")


def test_build_rejects_ship_tail_deploy_with_ci_rung_probe_failure(tmp_path):
    """Build-time gate: if the ship-tail deploy target's probe_flags are absent
    from the live skill, validate_shipping_resolves must raise ValueError — the
    plan must never build with a believed-but-dead CI rung wiring."""
    import json
    # Register a synthetic ship-tail-alike deploy target whose probe_flags include
    # a flag that is absent from any real skill — simulating a CI rung removed from
    # ship-tail.md after the registry entry was authored.
    bad_target = {
        "ship-tail-broken": {
            "kind": "skill",
            "skill": "ship-tail",
            "args": "--skip-tests",
            "probe_flags": ["--THIS-SENTINEL-IS-ABSENT-FROM-SHIP-TAIL"],
        }
    }
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "none", "deploy": "ship-tail-broken",
                                  "rollback_hint": "git revert HEAD"}}]
    with pytest.raises(ValueError, match="probe_flags"):
        make_plan(tmp_path, sessions, deploy=bad_target)


def test_build_resolves_ship_tail_from_bundled_defaults(tmp_path):
    """A session declaring deploy: ship-tail resolves from the bundled defaults
    (no project-local deploy-targets.json needed) and its probe passes."""
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "post_session": {"git": "none", "deploy": "ship-tail",
                                  "rollback_hint": "git revert HEAD"}}]
    # make_plan writes empty registries; the bundled default supplies ship-tail.
    plan_dir = make_plan(tmp_path, sessions)
    manifest = mio.load_manifest(plan_dir)
    ps = mio.session_by_id(manifest)["s01"]["post_session"]
    assert ps.get("deploy") == "ship-tail"


def test_build_resolves_test_orchestrate_verify_gate_from_bundled_defaults(tmp_path):
    """A session declaring verify.gates: [test-orchestrate] resolves from the
    bundled defaults and its probe passes."""
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do",
                 "verify": {"gates": ["test-orchestrate"]}}]
    plan_dir = make_plan(tmp_path, sessions)
    manifest = mio.load_manifest(plan_dir)
    vb = mio.session_by_id(manifest)["s01"].get("verify")
    assert vb is not None and "test-orchestrate" in vb["gates"]


def test_cmd_checkpoint_surfaces_decision_brief(tmp_path, capsys):
    """The operator at a parked gate must see WHY it exists and WHAT they are
    deciding: cmd_checkpoint emits the manifest's checkpoint brief as
    checkpoint_brief and stamps the decision into the PLAN.html note."""
    import run

    brief = {"reason": "Irreversible deploy.", "decision": "Ship now or hold?",
             "options": ["Ship", "Hold"]}
    sessions = [{"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"],
                 "prompt": "do",
                 "dispatch": {"requires_human_checkpoint": True, "checkpoint": brief}}]
    plan_dir = make_plan(tmp_path, sessions)
    run.cmd_checkpoint(str(plan_dir), "s01")
    out = json.loads(capsys.readouterr().out)
    assert out["checkpoint_brief"] == brief
    assert "awaiting human decision: Ship now or hold?" in (plan_dir / "PLAN.html").read_text()
