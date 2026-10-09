#!/usr/bin/env python3
"""Direct tests for hooks/context_tokens.py.

Split out of test_compact_policy.py, which had reached its 800-line test
bound (see compact_policy_testkit.py's docstring for the two limits). The
NEUTER probes for these same functions stay in compact_policy_neuters.py and
run from test_compact_policy.py's parametrized NEUTERS list — they exercise
context_tokens.py through a different mechanism (breaker+check) than the
direct fixture tests here, so nothing about their coverage moved.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compact_policy_testkit as kit  # noqa: E402

# --------------------------------------------------------------------------
# context_tokens
# --------------------------------------------------------------------------


@pytest.fixture()
def ct():
    return kit.load_context_tokens()


def test_reads_the_formula_off_a_fixture_transcript(ct, tmp_path):
    path = kit.write_transcript(
        str(tmp_path / "f.jsonl"),
        [kit.assistant_line(11111, 22222, 33333, window=200_000)],
    )
    assert ct.context_tokens(path) == (66666, 200_000, "read")


def test_ctx_is_the_size_of_the_NEXT_request_not_the_last_one(ct, tmp_path):
    """The last assistant record's input + cache tokens measure what was
    ALREADY sent. The next request also carries that turn's own output and
    every tool result appended after it — the recorded run's series steps ~15.5k per turn on
    turns whose output_tokens is 1, so nearly all of it is those trailing
    records. Under-reading ctx defers the veto exactly as an over-stated window
    does."""
    body = ('{"type":"assistant","message":{"role":"assistant","usage":'
            '{"input_tokens":1000,"cache_read_input_tokens":500,'
            '"output_tokens":700,"contextWindow":200000}}}')
    bare = kit.write_transcript(str(tmp_path / "bare.jsonl"), [body])
    assert ct.context_tokens(bare)[0] == 2200, "the turn's own output goes out again"

    tool_result = kit.tool_result_line(40 * 1024)
    withtail = kit.write_transcript(str(tmp_path / "tail.jsonl"), [body, tool_result])
    expected = 2200 + (len(tool_result) + 1) // ct.BYTES_PER_TOKEN
    assert ct.context_tokens(withtail)[0] == expected, (
        "the tool results after the last usage block are carried by the next "
        "request and counted by nothing else"
    )


def test_finds_an_assistant_record_larger_than_the_first_64kb_window(ct, tmp_path):
    path = kit.write_transcript(
        str(tmp_path / "big.jsonl"),
        [kit.assistant_line(1, window=200_000, filler=300 * 1024)],
    )
    assert os.path.getsize(path) > ct.START_BYTES
    assert ct.context_tokens(path)[0] == 1, (
        "a reader that truncates a big record returns None, "
        "which silently disables the veto"
    )


def test_a_truncated_last_line_falls_back_to_the_last_complete_record(ct, tmp_path):
    fragment = '{"type":"assistant","mess'
    path = kit.write_transcript(
        str(tmp_path / "trunc.jsonl"),
        [kit.assistant_line(500, window=200_000), fragment],
        trailing_newline=False,
    )
    assert ct.context_tokens(path)[0] == 500 + (len(fragment) + 1) // ct.BYTES_PER_TOKEN, (
        "the fragment is not a usage record, so the reader falls back to the "
        "complete one behind it; the fragment's own BYTES still count, because "
        "the next request carries them"
    )


def test_a_file_appended_to_mid_read_is_bounded_by_the_stat_snapshot(ct, tmp_path, monkeypatch):
    path = str(tmp_path / "grow.jsonl")
    kit.write_transcript(path, [kit.assistant_line(100, window=200_000)])
    snapshot = os.path.getsize(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(kit.assistant_line(999, window=200_000) + "\n")
    monkeypatch.setattr(ct.os.path, "getsize", lambda _p: snapshot)
    assert ct.context_tokens(path)[0] == 100, "the read must stay inside its own snapshot"


@pytest.mark.parametrize("kind", ["missing", "empty", "no_usage", "none", "directory"])
def test_every_error_path_returns_a_THREE_element_tuple(ct, tmp_path, kind):
    target = {
        "missing": str(tmp_path / "nope.jsonl"),
        "empty": kit.write_transcript(str(tmp_path / "e.jsonl"), [], trailing_newline=False),
        "no_usage": kit.write_transcript(str(tmp_path / "u.jsonl"), ['{"type":"user"}']),
        "none": None,
        "directory": str(tmp_path),
    }[kind]
    got = ct.context_tokens(target)
    assert len(got) == 3, "a caller unpacking three values must never meet two"
    assert got[0] is None and got[1] is None
    assert got[2].startswith("measurement_unavailable"), (
        "a veto that is off because it cannot measure must be VISIBLE"
    )


def test_the_model_window_comes_from_the_transcript_or_the_operator_never_a_guess(
    ct, tmp_path, monkeypatch
):
    """There is no built-in table, and the reason is measured: every transcript
    writes the bare id (`claude-opus-5`), a host can run BOTH variants of that
    id (settings selects `opus[1m]`, subagent dispatches pin `opus`), and no
    transcript can tell them apart. A table entry would be a MAXIMUM over a
    mixed population, and overstating a window is what kills a session."""
    monkeypatch.setenv("HOME", str(tmp_path / "nooverride"))
    stated = kit.write_transcript(
        str(tmp_path / "stated.jsonl"), [kit.assistant_line(9, window=200_000)]
    )
    assert ct.context_tokens(stated) == (9, 200_000, "read"), "the transcript wins"

    bare = kit.write_transcript(
        str(tmp_path / "bare.jsonl"), [kit.assistant_line(7, model="claude-opus-5")]
    )
    assert ct.context_tokens(bare) == (7, None, "read:no_model_window"), (
        "a bare id must yield None, VISIBLY — never a number nobody measured"
    )
    assert ct.window_for_model("claude-opus-5") is None
    assert ct.window_for_model("claude-haiku-4-5-20251001") is None
    assert ct.window_for_model("gpt-5.6-sol") is None
    assert ct.window_for_model("claude-opus-5[1m]") == 1_000_000, (
        "an id that names its own window is the one thing that needs no table"
    )


def test_the_operator_override_is_the_only_way_to_arm_a_bare_model_id(
    ct, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert ct.window_for_model("claude-opus-5") is None, "absent override = veto off"
    os.makedirs(str(tmp_path / ".gearbox-state" / "compaction"), exist_ok=True)
    with open(str(tmp_path / ".gearbox-state" / "compaction" / "model-windows.json"), "w") as fh:
        json.dump({"claude-opus-5-1m-only": 1_000_000, "bogus": "nope"}, fh)
    assert ct.window_for_model("claude-opus-5-1m-only-x") == 1_000_000
    assert ct.window_for_model("claude-opus-5") is None, "prefix match, not substring"
    assert ct.window_for_model("claude-fable-5") is None, "unlisted ids stay off"


def test_a_context_bigger_than_its_window_disarms_instead_of_going_negative(
    ct, tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))
    os.makedirs(str(tmp_path / ".gearbox-state" / "compaction"), exist_ok=True)
    with open(str(tmp_path / ".gearbox-state" / "compaction" / "model-windows.json"), "w") as fh:
        json.dump({"claude-mini": 200_000}, fh)
    path = kit.write_transcript(
        str(tmp_path / "over.jsonl"), [kit.assistant_line(900_000, model="claude-mini")]
    )
    assert ct.context_tokens(path) == (900_000, None, "read:model_window_contradicted"), (
        "a stale bound must fail OPEN and say so, never produce negative headroom"
    )


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_token_count_does_not_escape_as_an_exception(ct, tmp_path, literal):
    """json.loads accepts NaN/Infinity by default and int() of either RAISES.
    context_tokens must still hand back three values."""
    line = ('{"type":"assistant","message":{"role":"assistant","usage":'
            '{"input_tokens":%s,"cache_read_input_tokens":1,'
            '"cache_creation_input_tokens":1,"contextWindow":200000}}}' % literal)
    path = kit.write_transcript(str(tmp_path / "nan.jsonl"), [line])
    got = ct.context_tokens(path)
    assert len(got) == 3, "a caller unpacking three values must never meet two"
    assert got[0] is None and got[2].startswith("measurement_unavailable"), (
        "a corrupt usage block must be REJECTED, not partially summed: dropping "
        "the bad part under-reports ctx, which makes the veto block when it "
        "should have allowed"
    )

    # ...and an older, sound record behind it is still found. The rejected
    # record's BYTES still count: it is in the transcript, so the next request
    # carries it whether or not its usage block can be trusted.
    good = kit.assistant_line(500, window=200_000)
    path2 = kit.write_transcript(str(tmp_path / "nan2.jsonl"), [good, line])
    assert ct.context_tokens(path2) == (
        500 + (len(line) + 1) // ct.BYTES_PER_TOKEN, 200_000, "read",
    )

