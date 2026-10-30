"""findings_digest: the owner's checkpoint answer a new dispatch starts with.

Two layers: pure unit tests of `digest()` against a hand-written plan dir (no
orchestrator needed), then integration tests through `run.cmd_begin` proving
the wiring at both dispatch funnels actually fires -- including a neuter probe
that catches the wiring being silently removed.

The findings part of the digest was removed 2026-10-13 (see the module
docstring); its tests went with it.

Run: pytest skills/plan-execute/scripts/test_findings_digest.py -q
"""
import json
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import codex_command as cc  # noqa: E402
import findings_digest as fd  # noqa: E402
import plan_scope as ps  # noqa: E402
from codex_helpers import make_plan, run  # noqa: E402


# --------------------------------------------------------------------------
# codex_command.py: both prompt-building paths prepend the answer
# --------------------------------------------------------------------------
def test_codex_worktree_files_no_worktree_byte_for_byte_with_no_answer(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    prompt_file = plan_dir / "s02.prompt.md"
    prompt_file.write_text("original prompt body\n")
    effective, _runtime_dir, meta = cc._codex_worktree_files(
        plan_dir, {"id": "s02"}, prompt_file, None, "stamp1")
    assert effective == str(prompt_file)  # untouched — no copy, no digest
    assert meta == {}


def test_codex_no_worktree_prompt_byte_for_byte_with_no_answer(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    prompt_file = plan_dir / "s02.prompt.md"
    prompt_file.write_text("original prompt body\n")
    effective = cc._codex_no_worktree_prompt(plan_dir, "s02", prompt_file, "stamp1")
    assert effective == str(prompt_file)


# --------------------------------------------------------------------------
# run.py's Claude funnel, through a real dispatch (cmd_begin)
# --------------------------------------------------------------------------
def _claude_sessions():
    return [
        {"id": "s01", "title": "S1", "model": "Sonnet", "items": ["w-01"], "prompt": "do s01"},
        {"id": "s02", "title": "S2", "model": "Sonnet", "items": ["w-02"], "prompt": "do s02"},
    ]


def test_claude_dispatch_no_answer_is_byte_for_byte(tmp_path, capsys, egress_root):
    plan_dir = make_plan(tmp_path, _claude_sessions())
    run.cmd_begin(plan_dir, ["s02"])
    out = json.loads(capsys.readouterr().out)
    m = {mm["id"]: mm for mm in out["batch"]}["s02"]
    s = next(s for s in _claude_sessions() if s["id"] == "s02")
    original = (Path(plan_dir) / "sessions" / "s02.prompt.md").read_text()
    expected = ps.dispatch_preamble(plan_dir, s, "s02", None) + ps.CLAUDE_DISPATCH_GUIDANCE + original
    assert m["prompt_text"] == expected


def test_claude_dispatch_neuter_probe_digest_call_is_live(tmp_path, capsys, egress_root, monkeypatch):
    """If the `fd.digest(...)` call is removed from
    `plan_scope.dispatch_preamble_with_digest` (what run.py's Claude member
    builder calls), this sentinel — injected by monkeypatching the SHARED
    findings_digest module (plan_scope's `fd` and codex_command's `fd` are the
    same module object) — would never reach the dispatched prompt, and this
    test fails."""
    monkeypatch.setattr(fd, "digest", lambda plan_dir, sid: "SENTINEL-SNIP-42\n\n")
    plan_dir = make_plan(tmp_path, _claude_sessions())
    run.cmd_begin(plan_dir, ["s02"])
    out = json.loads(capsys.readouterr().out)
    m = {mm["id"]: mm for mm in out["batch"]}["s02"]
    assert "SENTINEL-SNIP-42" in m["prompt_text"]


def test_codex_backend_dispatch_under_claude_harness_carries_digest(
        tmp_path, capsys, egress_root, ssot, monkeypatch):
    """The SECOND Codex funnel in run.py: a Codex-pinned session dispatched by
    the Claude orchestrator with no worktree. Its `codex exec` reads the prompt
    file named after `<`; if run.py stops routing through
    `_codex_no_worktree_prompt`, that file is the bare prompt and this fails."""
    ssot("anthropic")
    monkeypatch.setattr(fd, "digest", lambda plan_dir, sid: "SENTINEL-CODEX-77\n\n")
    sessions = [{"id": "s02", "title": "S2", "items": ["w-02"],
                 "model": "gpt-5.6-sol", "reasoning": "high", "prompt": "do s02"}]
    plan_dir = make_plan(tmp_path, sessions)
    run.cmd_begin(plan_dir, ["s02"])
    m = {mm["id"]: mm for mm in json.loads(capsys.readouterr().out)["batch"]}["s02"]
    assert m["backend"] == "codex"
    prompt_path = m["codex_cmd"].rsplit("< ", 1)[1].split(" > ")[0].strip().strip("'")
    assert "SENTINEL-CODEX-77" in Path(prompt_path).read_text()
    run.cmd_release(plan_dir)


# --------------------------------------------------------------------------
# ANS-01: the owner's checkpoint answer on both funnels
# --------------------------------------------------------------------------
def _plant_answer(plan_dir, text="Hold until Friday."):
    ts = "2026-10-05T10:00:00+00:00"
    (Path(plan_dir) / "run.ndjson").write_text(json.dumps(
        {"ts": ts, "event": "checkpoint_reached", "session_ids": ["s02"], "kind": "pre_dispatch"}) + "\n")
    (Path(plan_dir) / fd.ANSWERS_FILE).write_text(json.dumps(
        {"at": "2026-10-05T10:05:00+00:00", "session": "s02", "answers_checkpoint": ts,
         "given": True, "answer": text}) + "\n")


def test_codex_no_worktree_prompt_carries_the_checkpoint_answer(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    _plant_answer(plan_dir)
    prompt_file = plan_dir / "s02.prompt.md"
    prompt_file.write_text("original prompt body\n")
    text = Path(cc._codex_no_worktree_prompt(plan_dir, "s02", prompt_file, "stamp1")).read_text()
    assert text.startswith("--- OWNER'S ANSWER AT THE CHECKPOINT BEFORE THIS SESSION ---\n  Hold until Friday.\n")
    assert text.endswith("original prompt body\n")


def test_claude_funnel_carries_the_checkpoint_answer(tmp_path):
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    _plant_answer(plan_dir)
    out = ps.dispatch_preamble_with_digest(plan_dir, {"id": "s02"}, "s02", None)
    assert out.startswith("--- OWNER'S ANSWER") and "Hold until Friday." in out
