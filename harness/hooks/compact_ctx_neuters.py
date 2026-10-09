#!/usr/bin/env python3
"""NEUTER probes for the CONTEXT READER — hooks/context_tokens.py.

Split out of compact_policy_neuters.py when that file crossed its 500-line
bound. These four pairs all break one rule of how a context size is read out
of a transcript, and check that the number goes wrong in the direction the
rule exists to prevent:

  * the three-tuple contract every caller unpacks,
  * the output tokens a turn carries forward into the next request,
  * the trailing records no usage block has counted,
  * and WHERE the post-compaction context is read from — after the
    compact_boundary, never across it.

They are registered in compact_policy_neuters.NEUTERS and run from
test_compact_policy.py like every other probe; only their definitions moved.
"""

from __future__ import annotations

import json
import os

import compact_policy_testkit as kit

WINDOW_1M = 1_000_000


def _ct(m):
    """The context reader a check runs against: the NEUTERED module when a
    breaker installed one, the real one otherwise."""
    return getattr(m, "_ct_under_test", None) or kit.load_context_tokens()


def n_three_tuple(m):
    ct = kit.load_context_tokens()
    ct._resolve = lambda found, trailing: found  # the real internal, 2 values
    m._ct_under_test = ct


def n_carried_forward(m):
    """Empty the table naming the usage fields a turn ADDS to the next request."""
    ct = kit.load_context_tokens()
    ct.CARRIED_FORWARD_FIELDS = ()
    m._ct_under_test = ct


def n_trailing_estimate(m):
    """Blow up the divisor the trailing-record estimate really divides by, so
    the records after the last usage block contribute nothing."""
    ct = kit.load_context_tokens()
    ct.BYTES_PER_TOKEN = 10 ** 9
    m._ct_under_test = ct


def c_carried_forward(m):
    """ctx must be the size of the NEXT request: the turn's own output goes out
    again with it."""
    import tempfile

    ct = _ct(m)
    line = ('{"type":"assistant","message":{"role":"assistant","usage":'
            '{"input_tokens":1000,"output_tokens":700,"contextWindow":200000}}}')
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(line + "\n")
    got = ct.context_tokens(fh.name)[0]
    assert got == 1700, (
        "output tokens were dropped from ctx: got %s, expected 1700" % got
    )


def c_trailing_estimate(m):
    """The tool results appended after the last usage block are carried by the
    next request and counted by nothing else."""
    import tempfile

    ct = _ct(m)
    payload = 40 * 1024
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(kit.assistant_line(1000, window=200_000) + "\n")
        fh.write(kit.tool_result_line(payload) + "\n")
    got = ct.context_tokens(fh.name)[0]
    assert got >= 1000 + payload // 8, (
        "the records after the last usage block were not counted: ctx read %s, "
        "but %d bytes of tool result follow it" % (got, payload)
    )


def c_three_tuple(m):
    ct = getattr(m, "_ct_under_test", None) or kit.load_context_tokens()
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        fh.write(kit.assistant_line(42, window=200_000) + "\n")
    got = ct.context_tokens(fh.name)
    assert len(got) == 3, "context_tokens returned %d values, not 3" % len(got)


def n_ctx_after_measured_too_early(m):
    """THE ORIGINAL DEFECT, restored. Point the deferred measurement at the
    whole-transcript reader again, so it walks back PAST the compact_boundary
    to the last PRE-compaction record — which is why no live ledger
    pair ever shrank."""
    ct = kit.load_context_tokens()

    def too_early(path):
        ctx, _window, source = ct.context_tokens(path)
        return (ctx, source)

    m.reorient.context_after_compaction = too_early


def c_ctx_after_is_measured_after_the_boundary(m, home):
    """ctx_after must be the FIRST turn after the compact_boundary, not the
    latest assistant record in the file. The transcript here carries a
    pre-compaction turn at 300k, the boundary, the post-compaction turn at 20k,
    and a later turn at 90k: only 20k is this compaction's ctx_after."""
    os.makedirs(str(home), exist_ok=True)
    path = os.path.join(str(home), "after.jsonl")
    kit.write_transcript(path, [
        kit.assistant_line(300_000, window=WINDOW_1M),
        json.dumps({"type": "system", "subtype": "compact_boundary",
                    "content": "Conversation compacted"}, separators=(",", ":")),
        json.dumps({"type": "user", "message": {"role": "user", "content": "Summary."}},
                   separators=(",", ":")),
        kit.assistant_line(20_000, window=WINDOW_1M),
        kit.assistant_line(90_000, window=WINDOW_1M),
    ])
    m.reorient.cmd_post_compact(
        {"session_id": "aft", "trigger": "manual", "transcript_path": path})
    m.cmd_prompt({"session_id": "aft", "prompt": "carry on"})
    rows = [r for r in kit.decisions(home)
            if r["event"] == "compaction-measured" and r["session"] == "aft"]
    assert rows, "no deferred measurement was ever ledgered"
    assert rows[-1]["ctx_after"] == 20_000, (
        "ctx_after must be the first turn after the boundary, got %r"
        % rows[-1]["ctx_after"]
    )
