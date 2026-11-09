"""Tests for improve-nudge.py. Fixtures are frozen; s04 reuses them."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = str(Path(__file__).with_name("improve-nudge.py"))
DAY = 86400

POSITIVE = ["no, that's the wrong file", "I told you to use the edit clone", "that's not what I asked for",
            "you missed the settings entry", "it is still failing after your fix",
            "why did you delete that?", "that’s not it"]
NEGATIVE = ["don't touch settings.json", "stop the dev server after the tests", "no worries, continue",
            "run the tests and tell me what fails", "deploy to the north region",
            "<command-message>plan-harden</command-message> what went wrong",
            "x" * 690 + " wrong " + "x" * 10]


def run(state, mode, stdin, **env):
    e = {k: v for k, v in os.environ.items() if k != "IMPROVE_NUDGE"}
    e.update(IMPROVE_NUDGE_STATE_DIR=str(state), **env)
    r = subprocess.run([sys.executable, HOOK, mode], input=stdin, capture_output=True, text=True, env=e)
    assert r.returncode == 0
    return r.stdout


def prompt(state, sid, text):
    return run(state, "prompt", json.dumps({"session_id": sid, "prompt": text}))


def fired(state):
    p = state / "fired.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def age(path, days):
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def test_third_pushback_nudges_exactly_once(tmp_path):
    outs = [prompt(tmp_path, "s1", t) for t in POSITIVE[:5]]
    assert outs[0] == outs[1] == "" and outs[3] == outs[4] == ""
    assert outs[2].startswith("improve-nudge: 3 pushback prompts") and outs[2].count("\n") == 1
    assert fired(tmp_path) == 1


def test_all_positive_fixtures_count(tmp_path):
    for i, t in enumerate(POSITIVE):
        assert prompt(tmp_path, f"p{i}", t) == ""
        prompt(tmp_path, f"p{i}", t)
        assert prompt(tmp_path, f"p{i}", t) != "", t


def test_negative_fixtures_never_count(tmp_path):
    for _ in range(2):
        for t in NEGATIVE:
            assert prompt(tmp_path, "neg", t) == ""
    assert fired(tmp_path) == 0


def test_stamp_seed_missing_stamp_is_silent_and_created(tmp_path):
    assert run(tmp_path, "session-start", "") == ""
    assert (tmp_path / "last-full-scope").exists() and fired(tmp_path) == 0


def test_stamp_throttle_stale_prints_once_then_throttled(tmp_path):
    (tmp_path / "last-full-scope").touch()
    age(tmp_path / "last-full-scope", 31)
    assert run(tmp_path, "session-start", "").startswith("improve-nudge: no full-scope")
    assert fired(tmp_path) == 1
    assert run(tmp_path, "session-start", "") == ""
    age(tmp_path / "last-start-nudge", 8)
    assert run(tmp_path, "session-start", "") != ""
    assert fired(tmp_path) == 2


def test_fresh_stamp_prints_nothing(tmp_path):
    (tmp_path / "last-full-scope").touch()
    age(tmp_path / "last-full-scope", 5)
    assert run(tmp_path, "session-start", "") == ""
    assert fired(tmp_path) == 0


def test_session_ids_differing_outside_safe_chars_stay_separate(tmp_path):
    for _ in range(2):
        prompt(tmp_path, "a/b", POSITIVE[0])
    assert prompt(tmp_path, "a:b", POSITIVE[0]) == ""
    assert len(list((tmp_path / "sessions").iterdir())) == 2


@pytest.mark.parametrize("stdin", ["not json", "[]", '"str"', '{"session_id":"s","prompt":5}',
                                   '{"session_id":"","prompt":"you missed it"}',
                                   '{"session_id":7,"prompt":"you missed it"}', ""])
def test_malformed_input_silent_in_prompt_mode(tmp_path, stdin):
    assert run(tmp_path, "prompt", stdin) == ""


def test_session_start_same_with_or_without_stdin(tmp_path):
    outs = []
    for i, stdin in enumerate(["", "garbage", "[]", json.dumps({"session_id": "x"})]):
        d = tmp_path / str(i)
        d.mkdir()
        (d / "last-full-scope").touch()
        age(d / "last-full-scope", 40)
        outs.append(run(d, "session-start", stdin))
    assert len(set(outs)) == 1 and outs[0] != ""


def test_kill_switches(tmp_path):
    for _ in range(3):
        out = run(tmp_path, "prompt", json.dumps({"session_id": "k", "prompt": POSITIVE[0]}), IMPROVE_NUDGE="off")
        assert out == ""
    (tmp_path / "DISABLED").touch()
    for _ in range(3):
        assert prompt(tmp_path, "k2", POSITIVE[0]) == ""


def test_old_session_files_pruned(tmp_path):
    prompt(tmp_path, "old", POSITIVE[0])
    f = next((tmp_path / "sessions").iterdir())
    age(f, 15)
    prompt(tmp_path, "other", "hello")
    assert not f.exists()


def finished(state, sid, cmd, **extra):
    return run(state, "finished", json.dumps({"session_id": sid, "tool_name": "Bash",
                                              "tool_input": {"command": cmd}, **extra}))


@pytest.mark.parametrize("cmd", ["git push -u origin HEAD", "git -C ~/your-private-harness push",
                                 "~/.claude/scripts/gearbox deploy", "gh pr merge 61 --squash",
                                 "gh release create v1.2", "uv publish", "cd x && ./deploy.sh prod",
                                 ".venv/bin/python tools/publish_public.py v0.20.49 --confirm pypi"])
def test_finished_command_offers_once(tmp_path, cmd):
    out = finished(tmp_path, "f1", cmd)
    ctx = json.loads(out)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "PostToolUse" and ctx["additionalContext"].startswith("improve-nudge:")
    assert finished(tmp_path, "f1", cmd) == ""
    assert fired(tmp_path) == 1


@pytest.mark.parametrize("cmd", ["git status", "git commit -m 'push the fix'", "echo deploy", "git log | grep push",
                                 "grep -n docker deploy.sh", "tools/release.py bump patch"])
def test_other_commands_are_silent(tmp_path, cmd):
    assert finished(tmp_path, "f2", cmd) == "" and fired(tmp_path) == 0


def test_finished_silent_in_subagent_and_after_prompt_nudge(tmp_path):
    assert finished(tmp_path, "f3", "git push", agent_id="a1") == ""
    for t in POSITIVE[:3]:
        prompt(tmp_path, "f4", t)
    assert finished(tmp_path, "f4", "git push") == ""
    assert fired(tmp_path) == 1  # only the prompt nudge


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"session_id": "x", "tool_input": "git push"}'])
def test_malformed_input_silent_in_finished_mode(tmp_path, stdin):
    assert run(tmp_path, "finished", stdin) == ""


def _transcript(path, improve=False):
    rows = [{"type": "user", "message": {"content": "that's not what I asked for"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed it."},
                                                           {"type": "tool_use", "name": "Bash", "input": {"command": "git push"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True, "content": "denied: sleep 60"}]}}]
    if improve:
        rows.append({"type": "user", "message": {"content": "<command-name>/improve</command-name>"}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return str(path)


def test_preview_writes_digest_and_names_it(tmp_path):
    tp = _transcript(tmp_path / "t.jsonl")
    ctx = json.loads(finished(tmp_path, "pv1", "git push", transcript_path=tp))["hookSpecificOutput"]["additionalContext"]
    digest = next((tmp_path / "digests").iterdir())
    assert str(digest) in ctx and "triage-prompt.md" in ctx
    text = digest.read_text()
    assert "OWNER: that's not what I asked for" in text and "TOOL ERROR: denied: sleep 60" in text
    assert "AGENT: Fixed it." in text and "TOOL Bash: git push" in text


def test_preview_silent_when_improve_already_ran(tmp_path):
    tp = _transcript(tmp_path / "t.jsonl", improve=True)
    assert finished(tmp_path, "pv2", "git push", transcript_path=tp) == "" and fired(tmp_path) == 0


def test_preview_falls_back_to_plain_offer(tmp_path):
    out = finished(tmp_path, "pv3", "git push", transcript_path=str(tmp_path / "missing.jsonl"))
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "offer /improve" in ctx and "triage-prompt.md" not in ctx


def test_digest_cap_keeps_both_ends(tmp_path):
    sys.path.insert(0, str(Path(HOOK).resolve().parent.parent / "scripts"))
    from improve_digest import condense
    p = tmp_path / "long.jsonl"
    p.write_text("\n".join(json.dumps({"type": "user", "message": {"content": f"line {i} " + "x" * 50}}) for i in range(200)))
    digest, ran, _ = condense(str(p), cap=3000)
    assert not ran and "line 0 " in digest and "line 199 " in digest and "[... middle cut ...]" in digest


def _codex_rollout(path, originator="Codex Desktop", improve=False):
    rows = [{"type": "session_meta", "payload": {"originator": originator, "session_id": "c1"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "# AGENTS.md instructions for /x"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "$improve-harness" if improve else "you forgot the tests"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Added them."}]}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
                                                  "input": 'text(await tools.exec_command({cmd:"git push origin main"}))'}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "output": [
                {"type": "input_text", "text": '{"exit_code":1,"output":"rejected"}'}]}}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def test_codex_preview_uses_codex_wording_and_format(tmp_path):
    tp = _codex_rollout(tmp_path / "rollout.jsonl")
    out = finished(tmp_path, "cx1", "git push origin main", transcript_path=tp, turn_id="t1")
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "$improve-harness" in ctx and "spawn ONE subagent" in ctx and "sonnet" not in ctx
    text = next((tmp_path / "digests").iterdir()).read_text()
    assert "OWNER: you forgot the tests" in text and "AGENTS.md" not in text
    assert "AGENT: Added them." in text and "TOOL exec: git push origin main" in text and "TOOL ERROR:" in text


def test_codex_command_found_when_tool_input_has_no_command_key(tmp_path):
    out = run(tmp_path, "finished", json.dumps({"session_id": "cx2", "turn_id": "t",
                                                "tool_input": {"cmd": "git push origin main"}}))
    assert "$improve-harness" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("make", [lambda p: _codex_rollout(p, improve=True),
                                  lambda p: _codex_rollout(p, originator="codex_exec"),
                                  lambda p: _codex_rollout(p, originator="Claude Code")])
def test_codex_silent_after_improve_or_in_automated_runs(tmp_path, make):
    tp = make(tmp_path / "rollout.jsonl")
    assert finished(tmp_path, "cx3", "git push", transcript_path=tp, turn_id="t") == "" and fired(tmp_path) == 0


@pytest.mark.parametrize("entry", ["sdk-cli", "sdk-py"])
def test_claude_automated_sessions_get_no_offer(tmp_path, entry):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "user", "entrypoint": entry, "message": {"content": "run s03"}}) + "\n")
    assert finished(tmp_path, "au1", "git push", transcript_path=str(p)) == "" and fired(tmp_path) == 0
