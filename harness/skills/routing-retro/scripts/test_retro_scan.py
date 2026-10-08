"""Tests for retro_scan's context-carrying metrics (the context-bloat flag's inputs).

The rubric's context-bloat flag reads `context_peak_tokens`, `reread_cost_usd` and
`reread_cost_pct`. A metric that silently reads zero would make that flag one of the
gates that cannot fail, so each assertion below pairs a PLANT (a session that must
register) with a CONTROL (one that must not).

Run: python3 test_retro_scan.py   (delegates to pytest, which collects the
WHOLE file — see test_every_selfcheck_runner_collects_the_whole_file)
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


# ---------------------------------------------------------------------------
# s07 (PF-01): the compaction retro's inputs. Each assertion pairs a PLANT with
# a CONTROL, so a field that silently reads zero cannot pass as a measurement.
# ---------------------------------------------------------------------------
def _scan_raw(lines):
    """Scan a transcript given as raw record dicts."""
    with tempfile.TemporaryDirectory() as td:
        ssot = os.path.join(td, "model-routing.yaml")
        with open(ssot, "w", encoding="utf-8") as f:
            f.write(SSOT)
        tp = os.path.join(td, "s.jsonl")
        with open(tp, "w", encoding="utf-8") as f:
            for rec in lines:
                f.write(json.dumps(rec) + "\n")
        return retro_scan.scan_session(tp, retro_scan.parse_ssot_prices(ssot))


def _assistant(i=0, ctx=1000):
    return {"type": "assistant", "timestamp": f"2026-08-22T10:{i:02d}:00.000Z",
            "message": {"model": "claude-workhorse-1",
                        "usage": {"input_tokens": ctx, "output_tokens": 10}}}


def test_compaction_boundaries_are_counted_and_a_plain_session_is_not():
    # THE REAL RECORD SHAPE: one compaction writes TWO records — a `type: system`
    # boundary and a SEPARATE `type: user` summary. No record carries both markers
    # (measured: 107 boundaries, 107 summaries, 0 both, over 10061 transcripts).
    # An earlier fixture wrote only the boundary, so it asserted == 1 on a shape
    # that never occurs in production and passed over a 2x double count.
    plant = _scan_raw([_assistant(0),
                       {"type": "system", "subtype": "compact_boundary",
                        "timestamp": "2026-08-22T10:05:00.000Z"},
                       {"type": "user", "isCompactSummary": True,
                        "timestamp": "2026-08-22T10:05:01.000Z",
                        "message": {"role": "user", "content": "summary"}},
                       _assistant(1)])
    control = _scan_raw([_assistant(0), _assistant(1)])
    assert plant["compactions"] == 1, "one compaction is one boundary, not two markers"
    assert control["compactions"] == 0


def test_base_context_tokens_is_the_first_call_not_the_peak():
    s = _scan_raw([_assistant(0, ctx=1000), _assistant(1, ctx=50000)])
    assert s["base_context_tokens"] == 1000
    assert s["context_peak_tokens"] == 50000


def test_session_type_and_window_are_none_unless_a_record_is_actually_found(tmp_path):
    # CONTROL: no policy dir, no settings file -> both fields stay None rather
    # than defaulting to a value that would read as a measurement.
    assert retro_scan.compaction_policy_types(str(tmp_path / "missing")) == {}
    assert retro_scan.autocompact_window(str(tmp_path / "missing.json")) is None
    # PLANT
    pol = tmp_path / "policy"
    pol.mkdir()
    (pol / "abc.json").write_text(json.dumps({"type": "orchestrator"}))
    (pol / "VERSION.json").write_text(json.dumps({"policy_version": 1}))
    (tmp_path / "settings.json").write_text(json.dumps({"autoCompactWindow": 300000}))
    assert retro_scan.compaction_policy_types(str(pol)) == {"abc": "orchestrator"}
    assert retro_scan.autocompact_window(str(tmp_path / "settings.json")) == 300000


# ---------------------------------------------------------------------------
# The self-check runner itself must not be able to skip a test
# ---------------------------------------------------------------------------
def test_every_selfcheck_runner_collects_the_whole_file():
    """A `__main__` runner that iterates `globals()` sees only the names defined
    ABOVE it, so tests appended below it are silently skipped — `python3
    test_retro_scan.py` printed 4 ok lines and exited 0 while never running the
    compaction-boundary test that exists to pin a 2x double count. It also cannot
    call a test that takes a pytest fixture. Both failures look exactly like a
    green run, which is the definition of a check that cannot fail.

    So the rule is structural, over the whole directory: a file that offers a
    direct-run harness must delegate it to pytest, which collects the file rather
    than a snapshot of its globals."""
    import ast
    import glob as _glob

    here = os.path.dirname(os.path.abspath(__file__))
    offenders = []
    for path in sorted(_glob.glob(os.path.join(here, "test_*.py"))):
        src = open(path, encoding="utf-8").read()
        for node in ast.parse(src).body:
            if not isinstance(node, ast.If):
                continue
            if "__name__" not in ast.dump(node.test):
                continue
            body = "\n".join(ast.get_source_segment(src, n) or "" for n in node.body)
            if "pytest.main" not in body:
                offenders.append(os.path.basename(path))
    assert offenders == [], (
        f"these files run a non-collecting __main__ harness: {offenders}")


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def test_cost_is_priced_by_the_served_model_id_not_the_family():
    """2026-09-22: family pricing billed Opus 5 at Opus 5.5 rates and Fable 5.1
    cache reads at 4x. Each line must use its own id's rates; an id with no
    model_prices row falls back to the family and is counted, never hidden."""
    rates = {"claude-fable-5-1": (10.0, 50.0, 0.25), "claude-opus-5": (5.0, 25.0, 0.5),
             "claude-opus-5-5": (4.0, 20.0, 0.2)}
    family = {"fable": (10.0, 50.0), "opus": (4.0, 20.0)}

    def line(model, i):
        return {"type": "assistant", "timestamp": f"2026-09-22T10:{i:02d}:00.000Z",
                "message": {"model": model, "usage": {"cache_read_input_tokens": 1_000_000}}}

    with tempfile.TemporaryDirectory() as td:
        tp = os.path.join(td, "s.jsonl")
        with open(tp, "w", encoding="utf-8") as f:
            for i, m in enumerate(("claude-fable-5-1", "claude-opus-5", "claude-opus-5-5", "claude-opus-5-6")):
                f.write(json.dumps(line(m, i)) + "\n")
        s = retro_scan.scan_session(tp, family, rates)
    assert round(s["models"]["fable"]["cost_usd"], 4) == 0.25          # not 1.00
    # opus-5 0.50 + opus-5-5 0.20 + unknown opus-5-6 at family 4.00 x 0.10 = 0.40
    assert round(s["models"]["opus"]["cost_usd"], 4) == 1.10
    assert s["family_priced_models"] == {"claude-opus-5-6": 1}


# ---- one API response split over several lines is counted ONCE ---------------


def test_a_split_response_is_counted_once_but_every_dispatch_is_kept(tmp_path):
    usage = {"input_tokens": 1000, "output_tokens": 100}
    blocks = [{"type": "thinking", "thinking": ""},
              {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "Explore", "model": "sonnet"}}]
    tp = tmp_path / "s.jsonl"
    with open(tp, "w", encoding="utf-8") as f:
        for mid, blk in (("msg_a", blocks[0]), ("msg_a", blocks[1]), ("msg_b", blocks[0])):
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-03T10:00:00.000Z",
                                "message": {"id": mid, "model": "claude-sonnet-5", "usage": usage,
                                            "content": [blk]}}) + "\n")
    s = retro_scan.scan_session(str(tp), {"sonnet": (3.00, 15.00)})
    assert s["assistant_messages"] == 2                       # two API calls, not three lines
    assert s["models"]["sonnet"]["in"] == 2000
    assert s["agent_dispatches"] == [{"agent": "Explore", "model": "sonnet"}]   # the second line's block survives
    assert "_seen_ids" not in s
