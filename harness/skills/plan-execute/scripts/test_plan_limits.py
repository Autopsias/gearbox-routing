"""plan_limits: the two measured numbers a plan author should know about.

Lives in plan-execute's suite so the quality gate runs it (the gate does not
collect plan-builder/scripts under pytest); plan-builder is on sys.path via
conftest, the same way test_verify reaches build_plan.
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "plan-builder" / "scripts"))
import plan_limits as pl  # noqa: E402


def test_the_defaults_are_the_measured_ones():
    assert pl.DEFAULT_MAX_REWORK == 2
    assert pl.SESSION_TOUCHES_P90 == 6


def test_wide_sessions_is_strictly_over_the_p90_and_skips_unparseable():
    dw = {"s01": ["a"] * 6, "s02": ["a"] * 7, "s03": None, "s04": ["a"] * 13}
    assert pl.wide_sessions(dw) == [("s04", 13), ("s02", 7)]


EMPTY = {"sessions": [], "items": []}


def test_warn_plan_risks_names_each_wide_session_and_the_threshold():
    out = io.StringIO()
    got = pl.warn_plan_risks(EMPTY, {"s09": ["f"] * 10, "s02": ["f"] * 3}, out=out)
    text = out.getvalue()
    assert len(got) == 1
    assert "s09 (10 files)" in text and "more than 6 files" in text and "s02" not in text
    assert "sample, not regressions" in text


def test_no_wide_session_prints_nothing():
    out = io.StringIO()
    assert pl.warn_plan_risks(EMPTY, {"s01": ["f"] * 6}, out=out) == []
    assert out.getvalue() == ""


def test_review_scope_normalises_and_defaults_to_the_whole_tree():
    assert pl.review_scope({"review_scope": ["./skills/x", "skills/y/", " "]}) == ["skills/x", "skills/y/"]
    assert pl.review_scope({"review_scope": "skills/x"}) == ["skills/x"]
    assert pl.review_scope({}) == [] and pl.review_scope(None) == []


def test_review_scope_keeps_a_root_dotfile():
    assert pl.review_scope({"review_scope": [".complexity-exceptions", "./.x/y"]}) == [".complexity-exceptions", ".x/y"]


def test_declared_writes_dedupes_paths_across_a_sessions_items():
    """Review surface is DISTINCT files touched, not paths summed per item.

    Regression (2026-08-21): `_declared_writes` appended every item's paths
    without dedupe, so a session whose four items each listed the same file
    counted it four times. One real 5-file session was reported as "12 files"
    by `warn_wide_sessions`, and that wrong number was used as evidence for a
    plan-harden lint rule before anyone checked it.
    """
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve()
                          .parents[2] / "plan-builder" / "scripts"))
    import build_plan as bp
    import plan_limits as pl

    spec = {
        "items": [
            {"id": "a", "touches": "hooks/x.py, settings.json"},
            {"id": "b", "touches": "hooks/x.py, settings.json"},
            {"id": "c", "touches": "hooks/x.py, settings.json"},
            {"id": "d", "touches": "README.md"},
        ],
        "sessions": [{"id": "sA", "items": ["a", "b", "c", "d"]}],
    }
    got = bp._declared_writes(spec)["sA"]
    assert sorted(got) == ["README.md", "hooks/x.py", "settings.json"], got
    assert pl.wide_sessions(bp._declared_writes(spec)) == [], "false positive returned"

    # KNOWN POSITIVE: a genuinely wide session must still be flagged, or this
    # test would pass against a `wide_sessions` that never flags anything.
    wide = {
        "items": [{"id": f"i{n}", "touches": f"f{n}.py"} for n in range(9)],
        "sessions": [{"id": "sW", "items": [f"i{n}" for n in range(9)]}],
    }
    assert pl.wide_sessions(bp._declared_writes(wide)) == [("sW", 9)]


# --- MAX_REWORK_CEILING ------------------------------------------------------
# The ceiling is not a taste call: it is the length of the longest escalation
# ladder the routing SSOT defines. Pin it to the SSOT so a ladder that grows
# fails HERE, loudly, instead of silently capping a session below its own apex.

def _rungs_to_apex(model, effort):
    sys.path.insert(0, str(Path(__file__).parents[3] / "scripts"))
    import resolve_route as rr
    cur, seen = {"model_id": model, "native_effort": effort}, [(model, effort)]
    for _ in range(12):
        nxt = rr.escalate("agentic_build", "anthropic", cur)
        if isinstance(nxt, str):
            break
        key = (nxt.get("model_id"), nxt.get("native_effort"))
        if key in seen:
            break
        seen.append(key)
        cur = nxt
    return len(seen)


def test_the_ceiling_is_the_longest_ladder_the_ssot_actually_defines():
    """Re-measured 2026-08-25 after routing SSOT v21 armed opus@xhigh: sonnet@
    medium needs 7 rungs, and the old cap of 6 made it unauthorable. KNOWN
    POSITIVE for the pin: a shorter starting rung must come out strictly under
    the ceiling, or the walk is not walking."""
    longest = max(_rungs_to_apex(m, e) for m, e in
                  [("opus", "high"), ("opus", "medium"), ("sonnet", "high"), ("sonnet", "medium")])
    assert longest == pl.MAX_REWORK_CEILING == 7
    assert _rungs_to_apex("opus", "high") == 5 < pl.MAX_REWORK_CEILING


def test_build_plan_accepts_a_max_rework_that_can_reach_the_apex():
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "plan-builder" / "scripts"))
    import build_plan as bp
    bp._validate_verify_block({"gates": ["g"], "max_rework": pl.MAX_REWORK_CEILING}, "s01.verify")
    for bad in (pl.MAX_REWORK_CEILING + 1, -1, "6"):
        try:
            bp._validate_verify_block({"gates": ["g"], "max_rework": bad}, "s01.verify")
            raise AssertionError(f"max_rework={bad!r} should have been refused")
        except ValueError as e:
            assert "max_rework must be an int 0..7" in str(e)


# --- stale prose references --------------------------------------------------

def test_stale_session_refs_finds_a_split_away_id_and_skips_the_negations():
    """KNOWN POSITIVE is the real shape measured on this machine: s07 was split
    into s07a/s07b and a sibling's prompt still names `s07`."""
    spec = {
        "sessions": [
            {"id": "s06", "prompt": "This session writes docs, and s07 edits the install surface."},
            {"id": "s07a", "prompt": "Own the changelog. s06 froze the format."},
            {"id": "s07b", "prompt": "No s04b exists to own it, so do it here."},
            {"id": "s08", "prompt": "s08 checks its own work."},
        ],
        "items": [{"id": "it-1", "deliverable": "The table s99 produces."}],
    }
    got = pl.stale_session_refs(spec)
    assert ("s06", "prompt", "s07") in got            # real: the split left it behind
    assert ("it-1", "deliverable", "s99") in got      # items are prose too
    assert not [g for g in got if g[2] == "s04b"]     # negated -> not a finding
    assert not [g for g in got if g[0] == g[2]]       # a session naming itself is fine
    assert len(got) == 2


def test_stale_session_refs_is_quiet_on_a_plan_whose_prose_is_clean():
    """KNOWN NEGATIVE -- without this the check could return [] because it never
    looked, and a silent sweep reads exactly like a clean one."""
    spec = {"sessions": [{"id": "s01", "prompt": "s02 follows this."}, {"id": "s02", "prompt": "x"}],
            "items": []}
    assert pl.stale_session_refs(spec) == []


# --- embedded programs -------------------------------------------------------

def test_embedded_programs_catches_a_heredoc_and_ignores_a_short_one():
    """KNOWN POSITIVE is the measured shape: `python3 - <<'EOF'` carrying a
    program the reviewer can only read. The short one is the known negative."""
    long_body = "\n".join(f"    x = {i}" for i in range(14))
    spec = {"sessions": [
        {"id": "s09", "prompt": f"Run it:\n    python3 - <<'EOF'\n{long_body}\n    EOF\ndone."},
        {"id": "s03", "prompt": "cat > f <<EOF\none\ntwo\nEOF\n"},
        {"id": "s01", "prompt": "No code here at all."},
    ]}
    assert pl.embedded_programs(spec) == [("s09", "EOF", 14)]


def test_plan_risk_warnings_reports_each_class_it_finds_and_nothing_it_does_not():
    spec = {"sessions": [{"id": "s01", "prompt": "s42 does the rest."}], "items": []}
    msgs = pl.plan_risk_warnings(spec, {"s01": ["f"] * 3})
    assert len(msgs) == 1 and "s42" in msgs[0] and "prose reference" in msgs[0]
    assert pl.plan_risk_warnings({"sessions": [], "items": []}, {}) == []


# --- what a stale-id sweep must NOT flag ------------------------------------
# Every fixture below is a REAL shape from the 29 plans on this machine. Before
# these suppressions the sweep ran at 4 true positives out of 12 hits, and a
# check at 33% precision teaches its reader to skip it.

def test_stale_session_refs_skips_the_four_shapes_that_only_look_stale():
    spec = {"sessions": [
        {"id": "s01", "prompt": "Read _evidence/s04/wave1-proof-pack.md first."},
        {"id": "s02", "prompt": "Tag the run [adv-consensus-s07-split] before shipping."},
        {"id": "s03", "prompt": 'The notice said "s00, s01 are DONE and UNAFFECTED" so we stopped.'},
        {"id": "s05", "prompt": "No s04b exists to own it, so do it here."},
        {"id": "s07a", "prompt": "s07a half of the s07 -> s07a/s07b split."},
        {"id": "s07b", "prompt": "s07b half of the split."},
    ], "items": []}
    assert pl.stale_session_refs(spec) == []


def test_stale_session_refs_still_catches_the_real_ones_beside_them():
    """KNOWN POSITIVE. `s03-defined` must survive the hyphen rule -- only a hyphen
    BEFORE the id disqualifies it, or this true positive would be suppressed."""
    spec = {"sessions": [
        {"id": "s04", "prompt": "Quiesce per the s03-defined operational rule."},
        {"id": "s06", "prompt": "This writes docs, and s07 edits the install surface."},
        {"id": "s03a", "prompt": "x"}, {"id": "s03b", "prompt": "x"},
    ], "items": []}
    got = pl.stale_session_refs(spec)
    assert ("s04", "prompt", "s03") in got
    assert ("s06", "prompt", "s07") in got and len(got) == 2


# --- the item half of the vocabulary ----------------------------------------

def test_stale_item_refs_uses_the_plans_own_prefixes_and_not_english():
    """The real shape: a plan carrying cal-02/cal-03 that names a dropped cal-01.
    `top-10` and `task-10` are the measured false positives an untethered
    `[a-z]+-\\d+` pattern produces -- neither prefix is an item prefix here."""
    spec = {"items": [{"id": "cal-02"}, {"id": "cal-03"}, {"id": "dec-01"}],
            "sessions": [{"id": "s08", "prompt":
                          "cal-01 is projected THEN measured; see the top-10 list and task-10."}]}
    assert pl.stale_item_refs(spec) == [("s08", "prompt", "cal-01")]


def test_stale_item_refs_is_quiet_with_no_prefixed_items_or_no_gaps():
    assert pl.stale_item_refs({"items": [{"id": "alpha"}],
                               "sessions": [{"id": "s01", "prompt": "cal-01"}]}) == []
    assert pl.stale_item_refs({"items": [{"id": "cal-01"}],
                               "sessions": [{"id": "s01", "prompt": "cal-01 runs."}]}) == []


def test_plan_risk_warnings_reports_session_and_item_ids_in_one_message():
    spec = {"items": [{"id": "cal-02"}],
            "sessions": [{"id": "s01", "prompt": "s42 owns cal-01 now."}]}
    msgs = pl.plan_risk_warnings(spec, {})
    assert len(msgs) == 1
    assert "2 prose reference(s)" in msgs[0] and "s42" in msgs[0] and "cal-01" in msgs[0]
