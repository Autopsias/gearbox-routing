"""Codex-harness worktree dispatch.

The test exercises the public ``run.py begin --harness codex`` path against a
real git repository.  A worktree declaration must change the actual Codex
command and keep its prompt/receipt files inside the member checkout.
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent.parent / "plan-builder" / "scripts"))

import build_plan  # noqa: E402
import egress  # noqa: E402
import plan_version_gate as pvg  # noqa: E402
import run  # noqa: E402
import worktree as wt  # noqa: E402
from codex_helpers import _CLOSEOUT, SSOT_TEMPLATE  # noqa: E402
from verify_paths import _feedback_path  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True,
                          capture_output=True, text=True)


def _write_ssot(tmp_path, monkeypatch):
    path = tmp_path / "model-routing.yaml"
    path.write_text(SSOT_TEMPLATE.format(
        provider="anthropic",
        openai_status="researched",
        executor_for="standard_build, agentic_build, deep_reasoning",
        opt_ins="      []",
        allowlist="      []",
    ))
    monkeypatch.setenv(run._SSOT_ENV, str(path))


def _make_plan(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "alpha.py").write_text("VALUE = 1\n")
    (root / ".claude").mkdir()
    (root / ".claude" / "deploy-targets.json").write_text("{}")
    (root / ".claude" / "eval-gates.json").write_text("{}")

    sessions = [
        {
            "id": "s01",
            "title": "Member",
            "model": "Opus",
            "items": ["i1"],
            "prompt": "edit alpha",
            "dispatch": {"parallel_group": "g", "depends_on": []},
        },
        {
            "id": "s02",
            "title": "Integration",
            "model": "Opus",
            "items": ["i2"],
            "prompt": "integrate",
            "dispatch": {"depends_on": ["s01"]},
            "post_session": {"git": "none"},
        },
    ]
    spec = {
        "title": "Codex Worktree Fixture",
        "categories": [{"key": "work", "label": "Work"}],
        "items": [
            {"id": "i1", "title": "I1", "category": "work",
             "touches": "src/alpha.py"},
            {"id": "i2", "title": "I2", "category": "work",
             "touches": "src"},
        ],
        "phases": [],
        "sessions": sessions,
        "infographic": {
            "type": "phase-journey",
            "title": "t",
            "phases": [{"num": 1, "name": "P1", "items": ["i1"]}],
            "anchor_now": {"name": "a", "tagline": "b"},
            "anchor_goal": {"name": "c", "tagline": "d"},
        },
    }
    plan_dir = root / "_plans" / "fixture"
    build_plan.build(spec, plan_dir, project_root=str(root))

    # The current builder port still needs these dispatch-time contract fields;
    # the dispatch test must exercise the executor, not the builder lane.
    manifest_path = plan_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest["items"]:
        item["touches"] = next(i["touches"] for i in spec["items"]
                               if i["id"] == item["id"])
    manifest["sessions"][0]["dispatch"].update(
        parallel_group="g", isolation="worktree"
    )
    manifest["sessions"][1]["dispatch"]["integrates_group"] = "g"
    # PINNED BELOW THE ISOLATION GATE, explicitly. This file is about the CODEX
    # dispatch command — a member worktree as a sibling of the project root, an
    # integration session in the shared root — which is the pre-isolation layout.
    # s10's activation moved the builder's stamp to the gate, so an inherited
    # stamp would silently re-point these fixtures at the nested plan-worktree
    # layout and the assertions below would be describing a world that no longer
    # exists. The ISOLATED layout (member branches nested under the plan, the
    # merge landing on the plan branch) is covered by `test_group_scope.py`,
    # which owns it; this file keeps the sub-7 shape it was written for.
    manifest["plan_schema_version"] = pvg.ISOLATION_MIN_SCHEMA - 1
    manifest_path.write_text(json.dumps(manifest, indent=2))

    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "fixture@example.com")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture base")
    return plan_dir


def test_codex_harness_dispatches_an_isolated_member_in_its_worktree(
    tmp_path, monkeypatch, capsys
):
    _write_ssot(tmp_path, monkeypatch)
    monkeypatch.delenv(run._CODEX_SANDBOX_ENV, raising=False)
    plan_dir = _make_plan(tmp_path)

    run.cmd_begin(plan_dir, ["s01"], harness="codex")
    payload = json.loads(capsys.readouterr().out)
    member = payload["batch"][0]

    worktree = Path(member["worktree"])
    assert worktree.is_dir()
    assert member["worktree_branch"] == "plan/g/s01"
    assert member["isolation"] == "worktree"
    assert f"cd {worktree} &&" in member["dispatch_cmd"]

    prompt = Path(member["prompt_file"])
    receipt = Path(member["last_message_file"])
    session = json.loads((plan_dir / "manifest.json").read_text())["sessions"][0]
    assert prompt.is_relative_to(worktree)
    assert receipt.is_relative_to(worktree)
    assert prompt.read_text() == (
        run.ps.dispatch_preamble(plan_dir, session, "s01", worktree)
        + (plan_dir / "sessions" / "s01.prompt.md").read_text()
    )
    assert str(prompt) in member["dispatch_cmd"]
    assert wt._porcelain(worktree) == []
    assert wt._porcelain(worktree, ignored=True) == ["!! .plan-worktrees/"]
    assert run._statuses(plan_dir)["s01"] == "DOING"

    run.cmd_release(plan_dir)


def test_codex_isolated_dispatch_carries_live_feedback_once_without_mutating_source(
    tmp_path, monkeypatch, capsys
):
    _write_ssot(tmp_path, monkeypatch)
    monkeypatch.delenv(run._CODEX_SANDBOX_ENV, raising=False)
    plan_dir = _make_plan(tmp_path)
    source = plan_dir / "sessions" / "s01.prompt.md"
    original = source.read_text()
    feedback = "public-test-workspace-boundary: use TemporaryDirectory(dir=WORKSPACE).\n"
    feedback_path = _feedback_path(plan_dir, "s01")
    feedback_path.parent.mkdir(exist_ok=True)
    feedback_path.write_text(feedback)

    run.cmd_begin(plan_dir, ["s01"], harness="codex")
    member = json.loads(capsys.readouterr().out)["batch"][0]
    body = Path(member["prompt_file"]).read_text()
    assert body.count(feedback) == 1
    assert source.read_text() == original

    session = json.loads((plan_dir / "manifest.json").read_text())["sessions"][0]
    repeated, _, _ = run._codex_worktree_files(plan_dir, session, source, member["worktree"], "repeat")
    assert Path(repeated).read_text().count(feedback) == 1
    run.cmd_release(plan_dir)


def test_codex_worktree_member_applies_then_codex_integration_uses_shared_root(
    tmp_path, monkeypatch, capsys
):
    _write_ssot(tmp_path, monkeypatch)
    monkeypatch.delenv(run._CODEX_SANDBOX_ENV, raising=False)
    plan_dir = _make_plan(tmp_path)

    run.cmd_begin(plan_dir, ["s01"], harness="codex")
    member = json.loads(capsys.readouterr().out)["batch"][0]
    worktree = Path(member["worktree"])
    root = worktree.parents[2]
    (worktree / "src" / "alpha.py").write_text("VALUE = 2\n")
    receipt = Path(member["last_message_file"])
    receipt.write_text(_CLOSEOUT)

    run.cmd_apply(str(plan_dir), "s01", str(receipt))
    json.loads(capsys.readouterr().out)
    assert run._statuses(plan_dir)["s01"] == "DONE"
    run.cmd_release(plan_dir)
    capsys.readouterr()
    monkeypatch.setenv(egress._EGRESS_ROOT_ENV, str(root))
    feedback = "public-test-workspace-boundary: use TemporaryDirectory(dir=WORKSPACE).\n"
    feedback_path = _feedback_path(plan_dir, "s02")
    feedback_path.parent.mkdir(exist_ok=True)
    feedback_path.write_text(feedback)

    run.cmd_begin(plan_dir, ["s02"], harness="codex")
    integration = json.loads(capsys.readouterr().out)["batch"][0]
    assert integration.get("worktree") is None
    assert f"cd {root} &&" in integration["dispatch_cmd"]
    assert Path(integration["prompt_file"]).read_text().count(feedback) == 1
    assert (root / "src" / "alpha.py").read_text() == "VALUE = 2\n"

    run.cmd_release(plan_dir)


def test_codex_omits_archived_feedback_from_a_repeated_nonisolated_dispatch(tmp_path):
    plan_dir = tmp_path / "plan"
    source = plan_dir / "sessions" / "s01.prompt.md"
    source.parent.mkdir(parents=True)
    source.write_text("original prompt\n")
    feedback = _feedback_path(plan_dir, "s01")
    feedback.parent.mkdir()
    feedback.write_text("public-test-workspace-boundary\n")

    current, _, _ = run._codex_worktree_files(plan_dir, {"id": "s01"}, source, None, "one")
    assert Path(current).read_text().count("public-test-workspace-boundary") == 1

    run._archive_session_state(plan_dir, "s01", 1)
    archived = feedback.with_name("s01.r1.feedback.md")
    assert archived.is_file()
    repeated, _, _ = run._codex_worktree_files(plan_dir, {"id": "s01"}, source, None, "two")
    assert repeated == str(source)
    assert Path(repeated).read_text() == "original prompt\n"
