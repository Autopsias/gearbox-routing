import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import mod_signals  # noqa: E402


def _line(**d):
    return json.dumps(d, separators=(",", ":")) + "\n"


def test_scan_counts_each_signal_and_skips_temp_projects(tmp_path):
    proj = tmp_path / "-Users-me-repo"
    proj.mkdir()
    shell = "<bash-input>git -C /Users/me/repo/wt-1 status</bash-input><bash-stdout>ok</bash-stdout>"
    rows = [
        _line(type="user", message={"role": "user", "content": "is it live now?"}),
        _line(type="user", message={"role": "user", "content": shell}),
        _line(type="user", message={"role": "user", "content": shell.replace("wt-1", "wt-2")}),
        _line(type="user", message={"role": "user", "content": "<command-name>/context</command-name>"}),
        _line(type="assistant", message={"content": [
            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}]}),
        _line(type="user", message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
             "content": "The user doesn't want to proceed with this tool use."}]}),
        _line(type="attachment", attachment={"type": "hook_cancelled", "hookName": "PreToolUse:Bash",
                                             "command": "~/.claude/hooks/guard.sh", "timedOut": True}),
        _line(type="attachment", attachment={"type": "hook_additional_context",
                                             "content": ["QUEUE: 9 need you"]}),
        _line(type="user", isSidechain=True, message={"role": "user", "content": "subagent prompt"}),
    ]
    (proj / "s1.jsonl").write_text("".join(rows))
    temp = tmp_path / "-private-tmp-eval"
    temp.mkdir()
    (temp / "s2.jsonl").write_text(rows[0])

    out = mod_signals.scan(str(tmp_path), days=1, minimum=1)

    assert out["files_scanned"] == 1 and out["files_skipped_temp"] == 1
    assert out["sessions_with_owner_prompt"] == 1
    assert out["typed_shell"][0]["count"] == 2  # the two worktree paths share one key
    assert out["slash_commands"][0]["key"] == "/context"
    assert out["short_prompts"][0]["key"] == "is it live now?"  # the sidechain prompt is not counted
    assert out["stopped_calls"][0]["key"] == "Bash | owner_rejected"
    assert out["hook_trouble"][0]["key"] == "PreToolUse:Bash | guard.sh | timed_out"
    assert out["hook_text"][0]["key"] == "QUEUE: N need you"
