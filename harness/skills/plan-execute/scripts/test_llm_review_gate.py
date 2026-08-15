"""Unit checks for the llm-review-* gate's findings-block parser.

The fixture harness (`fixtures/llm-review-gate/rerun.sh`) proves the gate
end-to-end against the real reviewer; these are the fast checks that the
classifier itself never turns an unrecognised answer into a pass.
"""
import llm_review_gate as g


def test_low_clean_marker_is_empty_block():
    assert g.classify("(none)")[:2] == ("empty", 0)
    assert g.classify("  none  ")[:2] == ("empty", 0)


def test_low_finding_lines_count():
    out = "sumrange.py:9 — off-by-one\nother.py:12 — leaks a handle"
    assert g.classify(out)[:2] == ("findings", 2)
    # the reviewer sometimes wraps the line in backticks
    assert g.classify("`sumrange.py:9 — off-by-one`")[:2] == ("findings", 1)


def test_json_array_shapes():
    assert g.classify('prose\n\n```json\n[]\n```')[:2] == ("empty", 0)
    assert g.classify('prose\n\n```json\n[{"file": "a.py"}]\n```')[:2] == ("findings", 1)
    assert g.classify('[{"file": "a.py"}, {"file": "b.py"}]')[:2] == ("findings", 2)


def test_unparseable_shapes_are_never_a_pass():
    # These are the INDETERMINATE captures in fixtures/llm-review-gate/.
    for bad in ("", "   ", "The review is complete. Both issues were reported above."):
        verdict, count, _ = g.classify(bad)
        assert verdict == "unparseable", bad
        assert count is None
    assert g.classify(None)[0] == "unparseable"


def test_intent_is_wrapped_in_untrusted_markers():
    # The prompt is PROSE, not a bare slash command. Measured 2026-08-14 on CLI
    # 2.1.232: `claude -p "/code-review high"` QUEUES the command and never runs
    # it (stalled transcripts held one `queue-operation` line and nothing else;
    # a level-low probe returned num_turns=0 with empty stdout). The old
    # assertions here pinned that dead shape, so they pinned a gate that could
    # only ever time out. fixtures/llm-review-gate/rerun.sh is the end-to-end
    # proof of the replacement: 11/11, planted bug caught at every level.
    p = g.build_prompt("low", "widen the window on purpose")
    assert "code-review" in p and '"low"' in p
    assert "git diff HEAD" in p
    assert "```json" in p, "the parseable output shape must be pinned in the prompt"
    assert g.INTENT_HEADER in p and g.INTENT_FOOTER in p
    assert "widen the window on purpose" in p
    # Intent may DOWNGRADE a finding, never remove it: the verdict is the
    # array's length, so an instruction to drop entries would let a claim of
    # deliberateness clear a real defect.
    assert "ask-user" in p and "NEVER drop a finding" in p
    # No intent -> no markers, and nothing that smuggles an empty intent block in.
    bare = g.build_prompt("medium", "")
    assert g.INTENT_HEADER not in bare and g.INTENT_FOOTER not in bare
    assert "ask-user" not in bare
    assert '"medium"' in bare and "```json" in bare


def test_exit_codes_are_three_valued():
    assert (g.PASS, g.FINDINGS, g.INDETERMINATE) == (0, 1, 2)
