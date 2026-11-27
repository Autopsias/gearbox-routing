"""Scoped reviewer and recovery tests for the verify gate."""

import pytest
import scoped_claude_review as scoped
import test_verifier_wiring as wiring

from test_verifier_wiring import (
    LLM_GATE, LLM_GATE_ID, SID, _claude_runs, _codex_harness, _fixture_tree,
    _opt_in, _run_the_registry_gate, vfy, vpk,
)

claude_shim = wiring.claude_shim
deployed_home = wiring.deployed_home


def test_approval_follows_worktrees_but_refuses_other_repositories(tmp_path):
    approved = tmp_path / "approved"
    other = tmp_path / "other"
    approved.mkdir()
    other.mkdir()
    wiring._git(approved, "init", "--quiet")
    wiring._git(other, "init", "--quiet")
    wiring._git(approved, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "--allow-empty", "-qm", "baseline")
    checkout = tmp_path / "linked"
    wiring._git(approved, "worktree", "add", "--detach", str(checkout))
    config = tmp_path / "routing.yaml"
    config.write_text(f'claude_verifier_repo_git_dir: "{approved / ".git"}"\n')
    assert scoped.approved(config, checkout)
    assert not scoped.approved(config, other)


@pytest.mark.parametrize("prior", [None, "Owner must approve the next step"])
def test_scoped_reviewer_retries_without_answering_owner_checkpoint(
        tmp_path, claude_shim, deployed_home, monkeypatch, prior):
    calls = claude_shim("clean")
    ssot = _opt_in(monkeypatch, tmp_path, "false")
    plan_dir = _fixture_tree(tmp_path, restricted=False, gate=LLM_GATE_ID)
    _codex_harness(plan_dir)
    vpk.set_closeout_checkpoint(plan_dir, SID, prior)
    assert _run_the_registry_gate(plan_dir, LLM_GATE)["action"] == "awaits-review"
    assert _claude_runs(calls) == []

    root = plan_dir.parent.parent
    with ssot.open("a") as file:
        file.write(f'    claude_verifier_repo_git_dir: "{root / ".git"}"\n')
    assert vfy.verify_run_argv(plan_dir, SID, LLM_GATE)["action"] == "passed"
    result = vfy.verify_finalize(plan_dir, SID)
    assert result["final_status"] == ("AWAITS_REVIEW" if prior else "DONE")
    assert vpk.pending_park(plan_dir, SID) is None
    assert vpk.closeout_checkpoint(plan_dir, SID) == prior
    assert _claude_runs(calls) == [["-p"]]
