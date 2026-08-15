"""Tests for retro_scan's context-carrying metrics (the context-bloat flag's inputs).

The rubric's context-bloat flag reads `context_peak_tokens`, `reread_cost_usd` and
`reread_cost_pct`. A metric that silently reads zero would make that flag one of the
gates that cannot fail, so each assertion below pairs a PLANT (a session that must
register) with a CONTROL (one that must not).

Run: python3 test_retro_scan.py   (or pytest)
"""

import json
import os
import tempfile

import retro_scan

SSOT = """
prices:
  workhorse:         { in: 3.00, out: 15.00 }
  cheap_fast:        { in: 1.00, out:  5.00 }
"""


def _write_transcript(path, messages):
    with open(path, "w", encoding="utf-8") as f:
        for i, (model, usage) in enumerate(messages):
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": f"2026-07-30T10:{i:02d}:00.000Z",
                "message": {"model": model, "usage": usage},
            }) + "\n")


def _scan(messages):
    with tempfile.TemporaryDirectory() as td:
        ssot = os.path.join(td, "model-routing.yaml")
        with open(ssot, "w", encoding="utf-8") as f:
            f.write(SSOT)
        tp = os.path.join(td, "s.jsonl")
        _write_transcript(tp, messages)
        # NOTE: prices: keys are TIER names in the SSOT but retro_scan buckets by model
        # FAMILY — the test SSOT therefore names families the scanner can resolve.
        return retro_scan.scan_session(tp, {"sonnet": (3.00, 15.00), "haiku": (1.00, 5.00)})


# ---- plant: a session re-reading a big carried context -----------------------


def test_bloated_session_registers_peak_and_reread_share():
    s = _scan([
        ("claude-sonnet-5", {"input_tokens": 500, "output_tokens": 200, "cache_read_input_tokens": 120_000}),
        ("claude-sonnet-5", {"input_tokens": 400, "output_tokens": 150, "cache_read_input_tokens": 150_000}),
    ])
    assert s["context_peak_tokens"] == 150_400          # the LARGEST single-turn context, not the sum
    assert s["reread_cost_usd"] > 0
    assert s["reread_cost_pct"] > 50                    # re-read dominates this session's spend


# ---- control: a lean session must not look bloated --------------------------


def test_lean_session_shows_no_reread():
    s = _scan([
        ("claude-sonnet-5", {"input_tokens": 2_000, "output_tokens": 800}),
    ])
    assert s["context_peak_tokens"] == 2_000
    assert s["reread_cost_usd"] == 0.0
    assert s["reread_cost_pct"] == 0.0


# ---- the peak must not depend on the model being priced ---------------------


def test_peak_counts_unpriced_models():
    # A model with no prices: row still contributes context — the peak is measured
    # before the pricing branch precisely so an unknown model can't hide a huge turn.
    s = _scan([("some-future-model", {"input_tokens": 10, "cache_read_input_tokens": 90_000})])
    assert s["context_peak_tokens"] == 90_010
    assert s["unknown_models"]                          # and it is still reported as unpriced
    assert s["reread_cost_usd"] == 0.0                  # but never priced with a guessed rate


def test_cache_writes_count_toward_context():
    s = _scan([("claude-sonnet-5", {"input_tokens": 100, "cache_creation_input_tokens": 40_000})])
    assert s["context_peak_tokens"] == 40_100


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
