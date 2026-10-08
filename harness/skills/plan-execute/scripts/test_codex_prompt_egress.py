"""Reviewer text from OUTSIDE the scanned tree must pass the egress scan too.

The data_sensitivity_guard scans the tree a Codex process runs in. Two pieces of
prompt text come from the shared plan directory instead, which under plan
isolation is outside that tree: the findings digest and the rework feedback
file. `codex_command._refuse_restricted_text` scans both with the same gitleaks
pass (`egress._text_hit`) and refuses the dispatch on a hit.

Run: pytest skills/plan-execute/scripts/test_codex_prompt_egress.py -q
"""
import json
from pathlib import Path

import pytest

import codex_command as cc
import egress
from codex_helpers import make_plan, rsi, run
from ssot_policy import UnroutableCodexSession
from verify_paths import _feedback_path

# Built at runtime so this file itself never carries a scannable token.
FAKE_PAT = "ghp_" + "x9Kq2LmT7vB4nR8pW3sZ6yD1fH5jC0gA" + "e4Nk"


def _plan_with_ledger(tmp_path, summary):
    plan_dir = tmp_path / "plan"
    ledger = plan_dir / "_verify_state" / "s01.findings.ndjson"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(json.dumps({
        "kind": "finding", "fid": "f1", "severity": "high", "status": "open",
        "summary": summary, "file": "a.py", "line": 1}) + "\n")
    prompt_file = plan_dir / "s02.prompt.md"
    prompt_file.write_text("original prompt body\n")
    return plan_dir, prompt_file


def test_digest_secret_refuses_no_worktree_prompt(tmp_path):
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, f"Leaked {FAKE_PAT} in a test.")
    with pytest.raises(UnroutableCodexSession, match="findings digest.*github-pat"):
        cc._codex_no_worktree_prompt(plan_dir, "s02", prompt_file, "stamp1")
    assert not (plan_dir / "_codex").exists()  # refused before any prompt copy


def test_digest_secret_refuses_worktree_files(tmp_path):
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, f"Leaked {FAKE_PAT} in a test.")
    with pytest.raises(UnroutableCodexSession, match="findings digest.*github-pat"):
        cc._codex_worktree_files(plan_dir, {"id": "s02"}, prompt_file, None, "stamp1")


def test_feedback_secret_refuses_and_names_the_file(tmp_path):
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, "A clean finding.")
    feedback = _feedback_path(plan_dir, "s02")
    feedback.write_text(f"The reviewer quoted {FAKE_PAT} from an old commit.\n")
    with pytest.raises(UnroutableCodexSession, match=f"feedback file {feedback.name}"):
        cc._codex_worktree_files(plan_dir, {"id": "s02"}, prompt_file, None, "stamp1")


def test_clean_digest_and_feedback_dispatch(tmp_path):
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, "Unguarded read of c[id].")
    _feedback_path(plan_dir, "s02").write_text("Fix the unguarded read.\n")
    effective, _dir, _meta = cc._codex_worktree_files(
        plan_dir, {"id": "s02"}, prompt_file, None, "stamp1")
    text = Path(effective).read_text()
    assert "Unguarded read of c[id]" in text and "Fix the unguarded read" in text


def test_repo_egress_opt_in_clears_the_text_scan(tmp_path):
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, f"Leaked {FAKE_PAT} in a test.")
    effective = cc._codex_no_worktree_prompt(plan_dir, "s02", prompt_file, "stamp1",
                                             opted_in=True)
    assert FAKE_PAT in Path(effective).read_text()


def test_missing_gitleaks_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(egress.shutil, "which", lambda name: None)
    plan_dir, prompt_file = _plan_with_ledger(tmp_path, "A clean finding.")
    with pytest.raises(UnroutableCodexSession, match="gitleaks is NOT on PATH"):
        cc._codex_no_worktree_prompt(plan_dir, "s02", prompt_file, "stamp1")


def test_begin_blocks_codex_session_whose_digest_holds_a_secret(tmp_path, capsys, ssot):
    """End to end: a Codex-pinned session under the Claude harness is refused
    and the plan halts, the same way the tree scan refuses it."""
    ssot("anthropic")
    sessions = [{"id": "s02", "title": "S2", "items": ["w-02"],
                 "model": "gpt-5.6-sol", "reasoning": "high", "prompt": "do s02"}]
    plan_dir = make_plan(tmp_path, sessions)
    ledger = Path(plan_dir) / "_verify_state" / "s01.findings.ndjson"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({
        "kind": "finding", "fid": "f1", "severity": "high", "status": "open",
        "summary": f"Leaked {FAKE_PAT}.", "file": "a.py", "line": 1}) + "\n")
    with pytest.raises(SystemExit) as exc:
        run.cmd_begin(plan_dir, ["s02"])
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert "findings digest" in out["unroutable"]["s02"]
    assert FAKE_PAT not in out["unroutable"]["s02"]  # --redact: never echo the secret
    assert rsi.is_halted(plan_dir)
