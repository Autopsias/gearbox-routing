"""Self-check for render_report.py (MON-02) — decision card caps at 3, cell
table and did-it-help sections render from real aggregate_outcomes.py output
shapes (not placeholders).

Run: pytest skills/routing-retro/scripts/test_render_report.py -q
"""

import render_report as rr


def _proposal(pid, kind="upgrade", n=6):
    return {"proposal_id": pid, "kind": kind, "class": "agentic_build",
            "current_cell": "opus@high", "n": n, "first_attempt_pass_rate": 0.3,
            "escalation_rate": 0.6, "recommendation": f"raise it ({pid})"}


def test_decision_card_caps_at_three_and_defers_the_rest():
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "did_it_help": [],
           "apex_revisit": {}, "proposals": [_proposal(f"p{i}") for i in range(5)]}
    out = rr.render(agg)
    assert out.count('<h3>#') == 3
    assert "2 additional proposal(s)" in out


def test_empty_proposals_shows_healthy_message_not_blank():
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "did_it_help": [],
           "apex_revisit": {}, "proposals": []}
    out = rr.render(agg)
    assert "No proposals this run" in out
    assert '<h3>#' not in out


def test_did_it_help_row_renders_verdict():
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "proposals": [],
           "apex_revisit": {}, "did_it_help": [{
               "proposal_id": "pidX", "class": "agentic_build",
               "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
               "before": {"n": 6, "first_attempt_pass_rate": 0.5},
               "after": {"n": 6, "first_attempt_pass_rate": 0.9}, "verdict": "helped"}]}
    out = rr.render(agg)
    assert "verdict-helped" in out
    assert "opus@high &rarr; sonnet@high" in out or "opus@high → sonnet@high" in out


def test_html_escapes_untrusted_field_values():
    agg = {"ssot_version": 1, "generated_at": "t", "did_it_help": [], "apex_revisit": {},
           "cells": [], "proposals": [{
               "proposal_id": "<script>alert(1)</script>", "kind": "upgrade",
               "class": "x", "current_cell": "y", "n": 6,
               "first_attempt_pass_rate": 0.1, "escalation_rate": 0.1,
               "recommendation": "r"}]}
    out = rr.render(agg)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def _canary(pid, n=12):
    """An adoption-ready canary as aggregate_canaries actually emits it: `kind`
    is the EXPERIMENT's kind ("canary"), and the readiness lives in `stage`."""
    return {"proposal_id": pid, "kind": "canary", "stage": "adoption-ready",
            "class": "agentic_build", "cell": "sonnet@high",
            "stats": {"n": n, "first_attempt_pass_rate": 0.95}}


def test_adoption_ready_canary_ranks_above_a_downgrade_open():
    # s07 review round 2: the ranking key fell back from `kind` to `stage` with
    # `or`, but a canary's `kind` is always the truthy "canary", so `stage` was
    # never consulted and every adoption-ready canary scored the unknown rank
    # and sorted LAST — the opposite of the documented order (upgrades, then
    # adoption-ready canaries, then downgrade-opens).
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "did_it_help": [],
           "apex_revisit": {}, "proposals": [
               _proposal("dn", kind="downgrade-open", n=99),   # big N, still ranks after
               _canary("cn"),
               _proposal("up", kind="upgrade", n=6)]}
    ranked = [p["proposal_id"] for p in rr._rank_proposals(agg)]
    assert ranked == ["up", "cn", "dn"]


def test_no_action_proposal_never_takes_a_decision_card_slot():
    # s07 review round 3: cell_proposal also emits a downgrade whose stage is
    # "smoke-in-progress" — its recommendation text is only "canary already
    # underway, see canaries[]", i.e. nothing to act on. It scored rank 2 like a
    # real downgrade-open, and rank ties sort by N descending, so a stalled entry
    # with a big N took one of the three slots and pushed an ACTIONABLE proposal
    # into the deferred list — offering the operator a card with nothing to accept.
    stalled = _proposal("stalled", kind="downgrade", n=500)
    stalled["stage"] = "smoke-in-progress"
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "did_it_help": [],
           "apex_revisit": {}, "proposals": [
               stalled,
               _proposal("real", kind="downgrade-open", n=7)]}
    ranked = [p["proposal_id"] for p in rr._rank_proposals(agg)]
    assert ranked == ["real"]          # the no-action entry is gone, not merely last
    out = rr.render(agg)
    assert "stalled" not in out


# ---------------------------------------------------------------------------
# s07 (PF-01): the Compaction section.
# ---------------------------------------------------------------------------
def _comp(**over):
    block = {"verdict_line": "hooks: underpowered", "vocabulary": ["underpowered"],
             "prediction": {"basis_note": "projection"}, "ledgers": {},
             "observed_sessions": 1, "scanned_sessions": 2, "excluded_unknown_sessions": 1,
             "filtered": {}, "type_table": [], "blindness": {"blind": False},
             "interventions": {}, "decision_card": [], "cheaper_and_worse": {}}
    block.update(over)
    return block


def test_no_compaction_block_renders_no_compaction_section():
    agg = {"ssot_version": 1, "generated_at": "t", "cells": [], "did_it_help": [],
           "apex_revisit": {}, "proposals": []}
    assert "Compaction &mdash; did it help" not in rr.render(agg)
    assert "Compaction — did it help?" not in rr.render(agg)


def test_empty_type_table_renders_no_records_not_a_blank_table():
    out = rr.render_compaction_page(_comp())
    assert "no records" in out
    assert "<tbody></tbody>" not in out


def test_a_missing_measurement_renders_n_a_never_none_percent():
    comp = _comp(interventions={"base_context": {
        "status": "exposed", "activation_ts": "t", "verdict": "underpowered", "why": "w",
        "n_before": 0, "n_after": 0, "n_spanning": 0, "before": {}, "after": {},
        "adherence": {"before": {"unpinned_generic_fanout_pct": None,
                                 "closeouts_without_numbers_pct": None,
                                 "dispatches_observed": 0, "closeouts_observed": 0},
                      "after": {"unpinned_generic_fanout_pct": None,
                                "closeouts_without_numbers_pct": None,
                                "dispatches_observed": 0, "closeouts_observed": 0}}}})
    out = rr.render_compaction_page(comp)
    assert "None%" not in out
    assert "n/a" in out


def test_blind_instrument_is_shouted_not_footnoted():
    comp = _comp(blindness={"blind": True, "measurement_unavailable_records": 4,
                            "sessions_with_no_ledger_lines": 2, "sessions_hook_inactive": 1})
    out = rr.render_compaction_page(comp)
    assert "INSTRUMENT BLINDNESS" in out
    assert 'class="apex"' in out


def test_containers_render_as_prose_never_as_python_literals():
    """Both shipped: the Type-source cell read `{'policy': 47}` and the apex
    CHEAPER-AND-WORSE line — the highest-severity line in the one-pager — read as
    a list literal, because _esc was html.escape(str(v)). Neither was caught by a
    structural check; only reading the rendered page shows it."""
    comp = _comp(
        type_table=[{"session_type": "default", "n": 47, "compactions": 12,
                     "vetoes": 0, "allows": 112, "p50_ctx_at_compaction": 271303,
                     "cost_per_session": 40.2, "reread_pct": 22.3,
                     "records_per_observed_session": 0.255,
                     "type_source": {"policy": 47, "retro": 3}}],
        cheaper_and_worse={"triggered": True,
                           "signals": ["base_context: cost fell 30.0% while fan-out rose",
                                       "repo_diet: cost fell 12.0% while closeouts rose"]})
    out = rr.render_compaction_page(comp)
    for literal in ("&#x27;", "{&", "[&", "&#x27;policy&#x27;"):
        assert literal not in out, f"python literal leaked into the page: {literal}"
    assert "policy 47, retro 3" in out
    assert "cost fell 30.0% while fan-out rose; repo_diet" in out


def test_a_missing_measurement_never_renders_the_word_none():
    """`None` in a cell reads as a value the same way `{'policy': 47}` did. The
    fix belongs in the shared formatter, not at the call sites that happened to
    be noticed: the live page rendered `p50 ctx at compaction = None` for the
    orchestrator row while four other numeric columns had the same exposure."""
    assert rr._esc(None) == "n/a"
    assert rr._esc({"p50": None}) == "p50 n/a"
    assert rr._esc([None, 1]) == "n/a; 1"
    assert rr._esc(False) == "False"          # a measured False is not a missing one
    assert rr._esc(0) == "0"


def test_adherence_all_clear_is_never_printed_over_an_empty_sample():
    """Three of four live after-cells read n/a and the only `helped` intervention
    had 0 dispatches and 0 closeouts after — yet the page printed the positive
    all-clear. An unexamined zero must render as 'not measured', never as clean."""
    none_side = {"unpinned_generic_fanout_pct": None, "dispatches_observed": 0,
                 "closeouts_without_numbers_pct": None, "closeouts_observed": 0}
    ivs = {"base_context": {"adherence": {"before": dict(none_side), "after": dict(none_side)}}}
    comp = {"cheaper_and_worse": {"triggered": False, "signals": [],
                                  "comparable_interventions": 0}}
    html = rr._adherence_section(comp, ivs)
    assert "No cheaper-and-worse signal" not in html
    assert "NOT MEASURED" in html


def test_adherence_all_clear_still_appears_when_the_sample_is_real():
    """The known positive for the guard above: a genuinely measured, genuinely
    clean sample must still get its all-clear."""
    side_b = {"unpinned_generic_fanout_pct": 20.0, "dispatches_observed": 10,
              "closeouts_without_numbers_pct": 10.0, "closeouts_observed": 5}
    side_a = {"unpinned_generic_fanout_pct": 10.0, "dispatches_observed": 12,
              "closeouts_without_numbers_pct": 5.0, "closeouts_observed": 6}
    ivs = {"base_context": {"adherence": {"before": side_b, "after": side_a}}}
    comp = {"cheaper_and_worse": {"triggered": False, "signals": [],
                                  "comparable_interventions": 1}}
    html = rr._adherence_section(comp, ivs)
    assert "No cheaper-and-worse signal" in html


def test_blindness_banner_names_every_counter_that_can_raise_the_flag():
    """A page blinded by unrecognized_decision_records or
    sessions_with_lines_but_no_heartbeat showed the red banner over three zeros —
    the flag's actual reason was nowhere on the page. The banner renders the
    holes list the builder derived `blind` from, so the two cannot drift."""
    holes = [{"key": "measurement_unavailable_records", "count": 0, "what": "w1"},
             {"key": "unrecognized_decision_records", "count": 7,
              "what": "vocabulary drift"},
             {"key": "sessions_with_lines_but_no_heartbeat", "count": 3,
              "what": "no heartbeat"}]
    comp = _comp(blindness={"blind": True, "holes": holes,
                            "headless_sdk_sessions_out_of_scope": 0})
    out = rr.render_compaction_page(comp)
    assert "INSTRUMENT BLINDNESS" in out
    assert "7 vocabulary drift" in out and "3 no heartbeat" in out
    assert "0 w1" in out       # every counter prints, zeros included


def test_healthy_line_names_the_counters_it_checked():
    """The all-clear is a positive claim; say what was checked, from the same
    holes list, rather than hand-listing three of the counters."""
    holes = [{"key": "measurement_unavailable_records", "count": 0, "what": "w"},
             {"key": "hook_error_records", "count": 0, "what": "w"}]
    comp = _comp(blindness={"blind": False, "holes": holes})
    out = rr.render_compaction_page(comp)
    assert "hook_error_records" in out


def test_scanned_count_reads_as_scanned_transcripts():
    """'2 of 3 scanned' counted heartbeat-only and ledger-only sessions as
    scanned transcripts; the page must count what each name claims."""
    comp = _comp(observed_sessions=2, scanned_sessions=1, universe_sessions=3,
                 excluded_unknown_sessions=1)
    out = rr.render_compaction_page(comp)
    assert "universe of 3" in out
    assert "1 scanned transcript" in out


def test_type_table_shows_p50_ctx_after_compaction():
    comp = _comp(type_table=[{"session_type": "default", "n": 1, "compactions": 2,
                              "vetoes": 0, "allows": 2, "p50_ctx_at_compaction": 250000,
                              "p50_ctx_after_compaction": 60000,
                              "cost_per_session": 1.0, "reread_pct": 5.0,
                              "records_per_observed_session": 2.0,
                              "type_source": {"policy": 1}}])
    out = rr.render_compaction_page(comp)
    assert "ctx after" in out and "60000" in out


def test_ctx_columns_declare_the_paired_population():
    """The two ctx stats are compared, so the page must SAY they share one
    population (the paired records) and print the shared N beside them — a
    reader cannot be left to assume it, because on live data the unpaired
    version reversed the sign of the answer."""
    comp = _comp(type_table=[{"session_type": "default", "n": 60, "compactions": 16,
                              "vetoes": 0, "allows": 6, "p50_ctx_at_compaction": 266910,
                              "p50_ctx_after_compaction": 253722,
                              "n_ctx_pairs": 7, "ctx_one_sided_records": 4,
                              "cost_per_session": 35.0, "reread_pct": 25.0,
                              "records_per_observed_session": 0.267,
                              "type_source": {"policy": 60}}])
    out = rr.render_compaction_page(comp)
    assert "7 paired" in out
    assert "4 one-sided" in out
    assert "paired records" in out.lower()   # the population choice, stated


def test_silence_classification_and_policy_version_reach_the_page():
    """MOVE 2 classifies every record-less observed session as no_compaction or
    hook_inactive — the classification was computed and reached only the JSON,
    and the page named a policy_version filter without saying which version."""
    comp = _comp(policy_version=3,
                 blindness={"blind": False, "holes": [],
                            "sessions_with_records": 4, "sessions_no_compaction": 9,
                            "sessions_hook_inactive": 2})
    out = rr.render_compaction_page(comp)
    assert "4 left compaction records" in out
    assert "9 had nothing to compact" in out
    assert "2 ran with the hooks inactive" in out
    assert "policy_version 3" in out
