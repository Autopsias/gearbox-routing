#!/usr/bin/env python3
"""Rule (h), end to end through the real hook: a `default` session compacts at
the turn START and is deferred MID-turn, bounded by compact_turn's own ceiling.

The in-process halves (the sweep, the spec envelope, the neuter probe) live with
the rest of the policy checks in test_compact_policy.py.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compact_policy_testkit as kit  # noqa: E402
import compact_turn as turn  # noqa: E402

WINDOW_1M = 1_000_000


@pytest.fixture()
def home(tmp_path):
    return tmp_path / "home"


def _transcript(tmp_path, name, ctx):
    path = str(tmp_path / name)
    kit.write_transcript(path, [kit.assistant_line(input_tokens=ctx, window=WINDOW_1M)])
    return path


def _age_the_turn(home, session):
    """Move the recorded turn start back past the start window, on disk — the
    hook runs as a subprocess, so the clock cannot be patched from here."""
    path = kit.live_root(home, "policy", session + ".json")
    policy = kit.policy(home, session)
    policy["turn_start_ms"] -= 10 * turn.TURN_START_MS
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(policy, handle)


def test_every_prompt_stamps_the_turn_start(home):
    kit.prompt(home, "t0", "fix a typo")
    first = kit.policy(home, "t0")["turn_start_ms"]
    _age_the_turn(home, "t0")
    kit.prompt(home, "t0", "and the other one")  # provisional: used to write nothing
    assert kit.policy(home, "t0")["turn_start_ms"] >= first, (
        "a later prompt did not restart the turn — every attempt after the first "
        "turn would read as mid-turn forever"
    )


def test_a_default_session_is_allowed_at_the_turn_start(home, tmp_path):
    kit.prompt(home, "t1", "fix a typo")
    done = kit.pre_compact(home, "t1", _transcript(tmp_path, "t1.jsonl", 200_000))
    assert done.stdout == ""
    assert kit.decisions(home)[-1]["reason"] == "default_type_not_blocked"


def test_a_default_session_is_deferred_mid_turn_under_the_tight_ceiling(home, tmp_path):
    kit.prompt(home, "t2", "fix a typo")
    _age_the_turn(home, "t2")
    done = kit.pre_compact(home, "t2", _transcript(tmp_path, "t2.jsonl", 200_000))
    assert json.loads(done.stdout)["decision"] == "block"
    last = kit.decisions(home)[-1]
    assert last["reason"] == "deferred_to_turn_end"
    assert last["ceiling"] == 200_000 + int(turn.TURN_END_FRACTION * 800_000), (
        "the deferral must use compact_turn's fraction, not the type's 0.5"
    )


def test_the_deferral_releases_at_its_ceiling(home, tmp_path):
    kit.prompt(home, "t3", "fix a typo")
    _age_the_turn(home, "t3")
    kit.pre_compact(home, "t3", _transcript(tmp_path, "t3a.jsonl", 200_000))  # freezes 200k
    done = kit.pre_compact(home, "t3", _transcript(tmp_path, "t3b.jsonl", 330_000))
    assert done.stdout == "", "past the turn-end ceiling the compaction must proceed"
    assert kit.decisions(home)[-1]["reason"] == "at_ceiling"


def test_the_next_prompt_releases_the_deferral(home, tmp_path):
    kit.prompt(home, "t4", "fix a typo")
    _age_the_turn(home, "t4")
    transcript = _transcript(tmp_path, "t4.jsonl", 200_000)
    assert kit.pre_compact(home, "t4", transcript).stdout != ""  # blocked mid-turn
    kit.prompt(home, "t4", "thanks, next thing")
    assert kit.pre_compact(home, "t4", transcript).stdout == "", (
        "the first attempt of the next turn is the boundary — it must be allowed"
    )


def test_planning_and_orchestrator_sessions_are_untouched(home, tmp_path):
    kit.prompt(home, "t5", "/plan-execute go")
    _age_the_turn(home, "t5")
    done = kit.pre_compact(home, "t5", _transcript(tmp_path, "t5.jsonl", 200_000))
    assert done.stdout == "", "rule (h) is for `default` sessions only"
