#!/usr/bin/env python3
"""Behaviour tests for hooks/compact_reorient.py: post-compact and session-start.

Split out of test_compact_policy.py, which had reached its 800-line test
bound (see compact_policy_testkit.py's docstring for the two limits). The
NEUTER probes for these same functions stay in compact_policy_neuters.py and
run from test_compact_policy.py's parametrized NEUTERS list — they exercise
compact_reorient.py through a different mechanism (breaker+check) than the
direct fixture tests here, so nothing about their coverage moved.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compact_policy_testkit as kit  # noqa: E402

WINDOW_1M = 1_000_000


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "home"


def _transcript(tmp_path, name, ctx, window=WINDOW_1M):
    path = str(tmp_path / name)
    kit.write_transcript(path, [kit.assistant_line(input_tokens=ctx, window=window)])
    return path


# --------------------------------------------------------------------------
# Post-compact and session-start
# --------------------------------------------------------------------------


def test_post_compact_reads_and_deletes_its_own_pending_file(home, tmp_path):
    pre = _transcript(tmp_path, "pending.jsonl", 180_000)
    kit.pre_compact(home, "pc1", pre)
    pending = os.path.join(kit.live_root(home), "pending", "pc1.json")
    assert os.path.exists(pending), "PreCompact must leave a pending record for post-compact"

    summary = "Summary: did X, then did Y, then did Z."  # len != 22, the length of the boundary record's fixed label
    post = kit.compacted_transcript(
        str(tmp_path / "pc1_post.jsonl"), 20_000, summary=summary, window=200_000
    )
    done = kit.post_compact(home, "pc1", post)
    assert done.returncode == 0 and done.stdout == ""
    assert not os.path.exists(pending), "post-compact must CONSUME (read + delete) its pending file"

    row = kit.decisions(home)[-1]
    assert row["event"] == "compacted"
    assert row["ctx_before"] == 180_000, "ctx_before must come from the pending record"
    assert row["ctx_after"] is None and row["ctx_after_deferred"] is True, (
        "PostCompact must NOT claim a ctx_after: the compact_boundary record "
        "lands after this hook, so anything read here is the PRE-compaction "
        "context wearing the after label"
    )
    kit.prompt(home, "pc1", "carry on")
    measured = kit.last_event(home, "compaction-measured", "pc1")
    assert measured is not None
    assert measured["ctx_before"] == 180_000 and measured["ctx_after"] == 20_000, (
        "the deferred measurement must read the first turn AFTER the boundary"
    )
    assert row["compact_summary_chars"] == len(summary), (
        "must measure the summary text, never the boundary record's fixed "
        "'Conversation compacted' label"
    )


def test_two_interleaved_sessions_never_cross_ctx_before(home, tmp_path):
    """A's PreCompact, then B's PreCompact, THEN A's PostCompact: A's own ctx
    must survive B's write landing in between — never B's, and never the last
    line of the shared ledger."""
    ta = _transcript(tmp_path, "ia.jsonl", 111_111)
    tb = _transcript(tmp_path, "ib.jsonl", 222_222)
    kit.pre_compact(home, "ia", ta)
    kit.pre_compact(home, "ib", tb)  # interleaves BETWEEN ia's pre-compact and its post-compact

    post_a = kit.compacted_transcript(str(tmp_path / "ia_post.jsonl"), 9_000, window=200_000)
    kit.post_compact(home, "ia", post_a)
    row_a = [r for r in kit.decisions(home) if r["event"] == "compacted" and r["session"] == "ia"]
    assert row_a[-1]["ctx_before"] == 111_111, (
        "session ia's ctx_before crossed with session ib's: %r" % row_a[-1]["ctx_before"]
    )

    post_b = kit.compacted_transcript(str(tmp_path / "ib_post.jsonl"), 18_000, window=200_000)
    kit.post_compact(home, "ib", post_b)
    row_b = [r for r in kit.decisions(home) if r["event"] == "compacted" and r["session"] == "ib"]
    assert row_b[-1]["ctx_before"] == 222_222


def test_post_compact_with_no_pending_record_reports_ctx_before_none(home, tmp_path):
    """A manual compaction never calls our PreCompact (matcher `auto` only), so
    there is no pending record. ctx_before must be honestly None, never guessed."""
    post = kit.compacted_transcript(str(tmp_path / "manual_post.jsonl"), 5_000, window=200_000)
    kit.settle(home, "manual1", post, trigger="manual")
    row = kit.last_event(home, "compaction-measured", "manual1")
    assert row["ctx_before"] is None and row["ctx_after"] == 5_000


def test_a_blocked_pre_compact_leaves_no_pending_record_for_a_later_manual_compact(
    home, tmp_path
):
    """A BLOCKED auto pre-compact has no matching 'after' coming: it must leave
    no pending record behind. Without that, a later MANUAL compaction (which
    never calls our own PreCompact) reaches post-compact with the blocked
    attempt's stale record still on disk and ledgers its ctx as ctx_before —
    exactly the bug the README's 'ctx_before is honestly None for a manual
    compaction' promise says cannot happen."""
    kit.prompt(home, "blocked-then-manual", "/plan-harden go")  # sticky 'planning'
    blocked = kit.pre_compact(
        home, "blocked-then-manual", _transcript(tmp_path, "bm.jsonl", 200_000)
    )
    assert json.loads(blocked.stdout)["decision"] == "block"
    pending = os.path.join(kit.live_root(home), "pending", "blocked-then-manual.json")
    assert not os.path.exists(pending), (
        "a blocked pre-compact must not leave a pending record behind"
    )

    post = kit.compacted_transcript(str(tmp_path / "bm_post.jsonl"), 20_000, window=200_000)
    kit.settle(home, "blocked-then-manual", post, trigger="manual")
    row = kit.last_event(home, "compaction-measured", "blocked-then-manual")
    assert row["ctx_before"] is None, (
        "a manual compaction must never inherit ctx_before from an earlier "
        "BLOCKED auto attempt, got %r" % (row["ctx_before"],)
    )
    assert row["ctx_after"] == 20_000


def test_kill_switched_post_compact_still_clears_the_pending_record(home, tmp_path):
    """The THIRD appearance of the same leak. The
    blocked-PreCompact fix clears pending on the PRE side; this is the POST
    side: cmd_post_compact used to return on kill_switch() BEFORE calling
    take_pending(), so a pending record written while the switch was off
    survived a kill-switched post-compact call untouched. A LATER manual
    compaction (switch off again, matcher `auto` never involved) would then
    reach post-compact with that stale record still on disk and ledger its
    ctx as ITS OWN ctx_before — the exact promise the post-compact tests already established:
    'ctx_before is honestly None for a manual compaction'."""
    pre = _transcript(tmp_path, "killed.jsonl", 150_000)
    kit.pre_compact(home, "killed-post", pre)
    pending = os.path.join(kit.live_root(home), "pending", "killed-post.json")
    assert os.path.exists(pending), "PreCompact must leave a pending record"

    switched = kit.post_compact(
        home, "killed-post", transcript=None, env={"GEARBOX_COMPACT_POLICY": "off"}
    )
    assert switched.returncode == 0 and switched.stdout == ""
    assert not os.path.exists(pending), (
        "a kill-switched post-compact must still consume its own pending record, "
        "or a later compaction inherits this session's stale ctx_before"
    )

    later = kit.compacted_transcript(str(tmp_path / "later.jsonl"), 9_000, window=200_000)
    kit.post_compact(home, "killed-post", later, trigger="manual")
    row = kit.decisions(home)[-1]
    assert row["ctx_before"] is None, (
        "a later manual compaction inherited a stale ctx_before from a session "
        "whose kill-switched post-compact never cleared its pending record: %r"
        % (row["ctx_before"],)
    )


def test_session_start_note_is_at_most_400_tokens(home):
    kit.run(home, "safe-point", "--state", "ok", "--session", "long",
            "--plan-dir", "_plans/example-plan", "--phase", "verify",
            "--note", "x" * 5000)  # deliberately far past the budget on its own
    done = kit.session_start(home, "long")
    assert done.returncode == 0
    out = json.loads(done.stdout)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert len(context.encode("utf-8")) <= 400 * 4, "additionalContext exceeded the 400-token budget"


def test_session_start_emits_only_sections_with_a_writer(home, tmp_path):
    # Nothing written at all: no policy file exists -> no output whatsoever.
    bare = kit.session_start(home, "nothing-recorded")
    assert bare.returncode == 0 and bare.stdout == "", (
        "an unclassified session with nothing recorded must print NOTHING, "
        "never an empty labelled section"
    )

    # Only a plan_dir: 'Active plan' and 'Next command' appear; nothing else does.
    kit.run(home, "safe-point", "--state", "ok", "--session", "plandir-only",
            "--plan-dir", "_plans/example-plan")
    done = kit.session_start(home, "plandir-only")
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Active plan:" in context and "Next command:" in context
    assert "Current phase:" not in context, "no --phase was ever written"
    assert "Last note:" not in context, "no --note was ever written"
    assert "Context at compaction:" not in context, "no post-compact ledger line exists"

    # A compaction happened for this session: 'Context at compaction' appears.
    kit.pre_compact(home, "plandir-only", _transcript(tmp_path, "so.jsonl", 90_000))
    kit.settle(
        home, "plandir-only",
        kit.compacted_transcript(str(tmp_path / "so_post.jsonl"), 15_000, window=200_000),
    )
    done2 = kit.session_start(home, "plandir-only")
    context2 = json.loads(done2.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Context at compaction: 90000 -> 15000 tokens" in context2


# --------------------------------------------------------------------------
# The re-orient note never prints a bare `None`, and names the
# session type the README already promised
# --------------------------------------------------------------------------


def test_session_start_omits_context_before_when_it_is_the_missing_half(home, tmp_path):
    """Manual compaction has no pending record, so ctx_before is honestly
    None. Show the known half only — never 'None -> 5000 tokens'."""
    post = kit.compacted_transcript(str(tmp_path / "manual_post.jsonl"), 5_000, window=200_000)
    kit.settle(home, "manual-note", post, trigger="manual")
    done = kit.session_start(home, "manual-note")
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Context after compaction: 5000 tokens" in context
    assert "None" not in context
    assert "Context at compaction:" not in context, "that format needs BOTH halves"


def test_session_start_omits_context_after_when_the_post_transcript_is_unreadable(
    home, tmp_path
):
    """A real PreCompact ran but the post-compact transcript is unreadable, so
    ctx_after is None. Show the known half only."""
    pre = _transcript(tmp_path, "readable_pre.jsonl", 77_000)
    kit.pre_compact(home, "unreadable-post", pre)
    kit.post_compact(home, "unreadable-post", transcript=None)
    done = kit.session_start(home, "unreadable-post")
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Context before compaction: 77000 tokens" in context
    assert "None" not in context
    assert "Context at compaction:" not in context, "that format needs BOTH halves"


def test_session_start_omits_the_whole_line_when_both_counts_are_missing(home):
    """Neither half is known (no PreCompact, no readable post transcript): the
    whole 'Context ...' line is omitted, not printed as a placeholder."""
    kit.post_compact(home, "both-missing", transcript=None, trigger="manual")
    done = kit.session_start(home, "both-missing")
    assert done.returncode == 0 and done.stdout == "", (
        "no field has a writer here, so the note must be empty, not just missing 'Context'"
    )


def test_session_start_names_the_session_type(home):
    """README.md promises 'the session type'; cmd_session_start must emit it."""
    kit.prompt(home, "typed-session", "/plan-harden go")  # sticky 'planning'
    kit.run(home, "safe-point", "--state", "ok", "--session", "typed-session",
            "--plan-dir", "_plans/example-plan")
    done = kit.session_start(home, "typed-session")
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Session type: planning" in context


# --------------------------------------------------------------------------
# The deferred ctx_after measurement
# --------------------------------------------------------------------------


def test_the_first_prompt_after_a_compaction_is_too_early_and_the_record_survives(
    home, tmp_path
):
    """THE CASE THE WHOLE DESIGN TURNS ON. After a compaction the order is
    SessionStart -> user prompt -> assistant turn, so at the FIRST prompt no
    assistant record exists after the boundary yet. Measuring must fail there
    AND the parked record must survive, or the compaction is lost."""
    boundary_only = kit.write_transcript(str(tmp_path / "early.jsonl"), [
        json.dumps({"type": "system", "subtype": "compact_boundary",
                    "content": "Conversation compacted"}, separators=(",", ":")),
        json.dumps({"type": "user", "message": {"role": "user", "content": "Summary."}},
                   separators=(",", ":")),
    ])
    kit.pre_compact(home, "early", _transcript(tmp_path, "early_pre.jsonl", 140_000))
    kit.post_compact(home, "early", boundary_only)
    parked = os.path.join(kit.live_root(home), "deferred", "early.json")
    assert os.path.exists(parked)

    kit.prompt(home, "early", "first prompt after the compaction")
    assert kit.last_event(home, "compaction-measured", "early") is None, (
        "nothing is measurable yet: no turn has landed after the boundary"
    )
    assert os.path.exists(parked), (
        "the parked record must SURVIVE a too-early attempt — deleting it here "
        "loses the compaction's ctx_after forever"
    )

    # The turn lands, and the NEXT prompt measures it.
    with open(boundary_only, "a", encoding="utf-8") as handle:
        handle.write(kit.assistant_line(input_tokens=31_000, window=200_000) + "\n")
    kit.prompt(home, "early", "second prompt")
    measured = kit.last_event(home, "compaction-measured", "early")
    assert measured is not None and measured["ctx_after"] == 31_000
    assert measured["ctx_before"] == 140_000
    assert not os.path.exists(parked), "a measured record must be cleared"


def test_a_compaction_that_never_becomes_measurable_gives_up_and_says_so(home, tmp_path):
    """An unbounded retry would re-read the transcript on every prompt forever.
    After MAX_MEASURE_TRIES the record is dropped and the give-up is LEDGERED —
    an unmeasured compaction must be visible, never silently absent."""
    no_boundary = _transcript(tmp_path, "noboundary.jsonl", 50_000)
    kit.post_compact(home, "giveup", no_boundary, trigger="manual")
    parked = os.path.join(kit.live_root(home), "deferred", "giveup.json")
    for _ in range(8):
        assert os.path.exists(parked)
        kit.prompt(home, "giveup", "again")
    assert not os.path.exists(parked), "the retry must be bounded"
    row = kit.last_event(home, "compaction-unmeasurable", "giveup")
    assert row is not None and row["reason"] == "no_boundary_found"


def test_ctx_after_is_the_first_turn_after_the_boundary_not_the_last(home, tmp_path):
    """The measurement must not drift. Taking the LAST assistant record would
    grow with the session and report a context that compaction never produced."""
    path = kit.compacted_transcript(str(tmp_path / "drift.jsonl"), 12_000, window=200_000)
    kit.post_compact(home, "drift", path, trigger="manual")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(kit.assistant_line(input_tokens=98_000, window=200_000) + "\n")
    kit.prompt(home, "drift", "go")
    measured = kit.last_event(home, "compaction-measured", "drift")
    assert measured["ctx_after"] == 12_000, (
        "must be the FIRST post-boundary turn (12000), not the latest (98000)"
    )
