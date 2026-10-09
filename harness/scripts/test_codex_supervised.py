"""Tests for codex_supervised.py — resume targets the captured thread, never --last.

Run: pytest scripts/test_codex_supervised.py -q  (from ~/.claude)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import codex_supervised as cs  # noqa: E402

TID = "019fcd38-a476-7241-8b06-41b5ed15e86f"
FLAGS = ["--json", "-o", "out.md"]


def test_thread_id_parsed_from_real_event_shape(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_bytes(
        b'{"type":"thread.started","thread_id":"' + TID.encode() + b'"}\n'
        b'{"type":"turn.started"}\n'
    )
    assert cs.thread_id_from_log(log) == TID


def test_thread_id_none_when_no_session_started(tmp_path):
    log = tmp_path / "run.jsonl"
    log.write_bytes(b'{"type":"turn.started"}\n[codex] noise line, not json\n')
    assert cs.thread_id_from_log(log) is None
    assert cs.thread_id_from_log(tmp_path / "missing.jsonl") is None


def test_first_attempt_is_fresh_exec():
    argv, mode = cs.attempt_argv(1, FLAGS, None)
    assert mode == "fresh"
    assert argv[:2] == ["codex", "exec"] and argv[-1] == "-"


def test_retry_resumes_by_explicit_id_never_last():
    argv, mode = cs.attempt_argv(2, FLAGS, TID)
    assert mode == "resume"
    assert argv[:4] == ["codex", "exec", "resume", TID]
    assert "--last" not in argv


def test_retry_without_id_restarts_fresh_instead_of_last():
    argv, mode = cs.attempt_argv(2, FLAGS, None)
    assert mode == "fresh"
    assert "--last" not in argv and "resume" not in argv
