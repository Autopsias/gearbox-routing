"""digest_sessions.py must see desktop-app sessions the way it sees terminal ones."""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import digest_sessions as ds  # noqa: E402

DENIED = ("Permission for this action was denied by the Claude Code auto mode classifier. "
          "Reason: [Merge Without Review]. If you have other tasks, continue.")


def _write(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def _user(content, **kw):
    return {"type": "user", "entrypoint": "claude-desktop", "timestamp": "2026-11-01T10:00:00Z",
            "message": {"role": "user", "content": content}, **kw}


def _desktop_session(path: Path) -> Path:
    return _write(path, [
        _user("<command-message>improve</command-message>\n<command-name>/improve</command-name>"),
        _user("proceed"),
        _user("fix the login check, it fails after a restart"),
        _user("<system-reminder>hook text</system-reminder>", isMeta=True),
        _user("[SYSTEM NOTIFICATION - NOT USER INPUT]\n<task-notification>done</task-notification>"),
        {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "plan-builder"}},
            {"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "gh pr merge 1"}}]}},
        _user([{"type": "tool_result", "tool_use_id": "t2", "is_error": True, "content": DENIED}]),
    ])


def test_desktop_session_is_interactive_with_commands_skills_and_denials(tmp_path):
    s = ds.digest_session(_desktop_session(tmp_path / "-Users-me-repo" / "abc.jsonl"), 5)
    assert s["interactive"] is True
    assert s["human_msgs"] == 2            # "proceed" and the real request; wrappers and notices excluded
    assert s["babysit_msgs"] == 1
    assert s["commands"] == {"/improve": 1}
    assert s["skills"] == {"plan-builder": 1}
    assert s["classifier_denials"] == {"Merge Without Review": 1}


def test_session_with_only_machine_text_is_not_interactive(tmp_path):
    p = _write(tmp_path / "-Users-me-repo" / "auto.jsonl", [
        _user("[SYSTEM NOTIFICATION - NOT USER INPUT]\nscheduled run"),
        _user("<command-name>/loop</command-name>", isMeta=True),
    ])
    s = ds.digest_session(p, 5)
    assert s["human_msgs"] == 0 and s["interactive"] is False


def test_main_skips_old_files_and_temp_eval_projects(tmp_path):
    projects = tmp_path / "projects"
    fresh = _desktop_session(projects / "-Users-me-repo" / "fresh.jsonl")
    old = _desktop_session(projects / "-Users-me-repo" / "old.jsonl")
    _desktop_session(projects / "-private-var-folders-ml-x-T-h2h-cwd-abc" / "eval.jsonl")
    long_ago = time.time() - 90 * 86400
    os.utime(old, (long_ago, long_ago))
    out = tmp_path / "digest.json"
    assert ds.main(["--projects", str(projects), "--history", str(tmp_path / "none.jsonl"),
                    "--since-days", "30", "--out", str(out)]) == 0
    d = json.loads(out.read_text())
    assert d["totals"]["sessions"] == 1 and fresh.exists()
    assert d["totals"]["interactive_sessions"] == 1
    assert d["skipped_old_files"] == 1 and d["skipped_temp_project_files"] == 1
    assert d["command_usage"] == {"/improve": 1} and d["skill_usage"] == {"plan-builder": 1}
    assert d["totals"]["human_msgs"] == 2 and d["totals"]["babysit_msgs"] == 1


def test_select_files_applies_temp_and_age_filters(tmp_path):
    projects = tmp_path / "projects"
    fresh = _desktop_session(projects / "-Users-me-repo" / "fresh.jsonl")
    old = _desktop_session(projects / "-Users-me-repo" / "old.jsonl")
    _desktop_session(projects / "-private-var-folders-ml-x-T-h2h-cwd-abc" / "eval.jsonl")
    then = time.time() - 90 * 86400
    os.utime(old, (then, then))
    files, n_all, n_temp = ds.select_files(projects, 30, False)
    assert files == [fresh] and n_all == 3 and n_temp == 1
    assert len(ds.select_files(projects, None, True)[0]) == 3
