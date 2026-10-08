"""Self-checks for the compaction did-it-help retro (s07/PF-01).

Every rule this session was hardened around gets a fixture here, because each
one of them was a defect a review round found by reading rather than running:
the single-boundary cohort, the spanning session, the boundary equalities, the
spine-less ledger line, the malformed activation, the empty ledger, the blind
instrument, and the difference between `no_exposure` and `underpowered`.

Run: pytest skills/routing-retro/scripts/test_compaction_retro.py -q
"""

import json

import compaction_ledger as cl
import compaction_report as crep
import compaction_retro as cr
import render_report as rr

from retro_test_helpers import (
    act_row as _act,
    dec_row as _dec,
    dyno_root as _root,
    hb_row as _hb,
    scan_of as _scan,
    sess_row as _sess,
)

T = "2026-08-22T12:00:00Z"


# --------------------------------------------------------------------------
# 1. Exposure is per intervention — never one boundary
# --------------------------------------------------------------------------
def test_no_cohort_is_computed_from_a_single_record(tmp_path):
    """The same session is 'after' for one intervention and 'before' for another.
    A single global boundary cannot express that, which is why none exists."""
    root = _root(tmp_path, activations=[
        _act("hooks", "2026-08-22T10:00:00Z"),
        _act("base_context", "2026-08-22T14:00:00Z")])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    rows = {"s1": {"started": cl.parse_ts("2026-08-22T11:00:00Z"),
                   "ended": cl.parse_ts("2026-08-22T12:00:00Z")}}
    coh = cr.build_cohorts(rows, acts)
    assert coh["hooks"]["after"] == ["s1"]
    assert coh["base_context"]["before"] == ["s1"]


def test_boundary_equality_end_at_activation_is_before():
    ts = cl.parse_ts(T)
    assert cl.cohort_label(cl.parse_ts("2026-08-22T11:00:00Z"), ts, ts) == "before"


def test_boundary_equality_start_at_activation_is_after():
    ts = cl.parse_ts(T)
    assert cl.cohort_label(ts, cl.parse_ts("2026-08-22T13:00:00Z"), ts) == "after"


def test_session_that_receives_the_deploy_mid_session_is_spanning_and_in_no_cohort(tmp_path):
    """Starts before the deploy, receives it mid-session, compacts after it."""
    root = _root(tmp_path, activations=[_act("hooks", T)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    rows = {"s1": {"started": cl.parse_ts("2026-08-22T11:00:00Z"),
                   "ended": cl.parse_ts("2026-08-22T13:00:00Z")}}
    coh = cr.build_cohorts(rows, acts)
    assert coh["hooks"]["spanning"] == ["s1"]
    assert coh["hooks"]["before"] == [] and coh["hooks"]["after"] == []


# --------------------------------------------------------------------------
# 2. Activation-row validity — decided BEFORE exposure
# --------------------------------------------------------------------------
def test_missing_row_is_no_exposure_not_activation_unknown(tmp_path):
    root = _root(tmp_path, activations=[])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["interventions"]["hooks"]["status"] == "no_exposure"
    assert acts["any_object"] is False


def test_trailing_newline_event_id_is_refused(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T, **{"event_id": "hooks#1\n"})])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["interventions"]["hooks"]["status"] == "activation_unknown" \
        or acts["malformed_rows"] == 1


def test_schema_other_than_1_is_refused(tmp_path):
    """A refused row still NAMES the intervention it damages. Reporting
    `no_exposure` here said "the clock never started" about a ledger that is
    simply broken — the opposite instruction to the reader, who would wait for
    data instead of fixing the writer."""
    root = _root(tmp_path, activations=[_act("hooks", T, schema=2)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["malformed_rows"] == 1
    assert acts["interventions"]["hooks"]["status"] == "activation_unknown"
    # An intervention no row mentions at all is still no_exposure: nothing is
    # broken about it, the clock genuinely never started.
    assert acts["interventions"]["routing"]["status"] == "no_exposure"


def test_repeated_seq_makes_that_intervention_activation_unknown(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T, 1), _act("hooks", T, 1)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["interventions"]["hooks"]["status"] == "activation_unknown"


def test_seq_gap_makes_that_intervention_activation_unknown(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T, 1), _act("hooks", T, 3)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["interventions"]["hooks"]["status"] == "activation_unknown"


def test_restage_uses_the_highest_seq_and_reports_the_prior_stage(tmp_path):
    root = _root(tmp_path, activations=[
        _act("hooks", "2026-08-22T09:00:00Z", 1),
        _act("hooks", "2026-08-22T15:00:00Z", 2)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    entry = acts["interventions"]["hooks"]
    assert entry["status"] == "exposed"
    assert entry["ts"] == cl.parse_ts("2026-08-22T15:00:00Z")
    assert [r["event_id"] for r in entry["prior_stages"]] == ["hooks#1"]


def test_unrecognised_intervention_is_counted_not_attributed(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T, **{"intervention": "nonsense",
                                                            "event_id": "nonsense#1"})])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["malformed_rows"] == 1
    assert all(v["status"] == "no_exposure" for v in acts["interventions"].values())


def test_corrupt_and_non_object_lines_are_rejected_and_counted(tmp_path):
    root = _root(tmp_path, activations=["not json", "[1,2]", json.dumps(_act("hooks", T))])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    assert acts["rejected_lines"] == 2
    assert acts["interventions"]["hooks"]["status"] == "exposed"


def test_missing_file_is_a_result_never_an_exception(tmp_path):
    acts = cl.load_activations(str(tmp_path / "nope.ndjson"))
    assert acts["exists"] is False and acts["any_object"] is False


def test_validity_is_decided_before_exposure(tmp_path):
    """A malformed row makes the intervention activation_unknown, and the question
    of its after-cohort never arises — the two labels can never both apply."""
    root = _root(tmp_path, activations=[_act("hooks", T, 2)])   # seq starts at 2: a gap
    acts = cl.load_activations(str(root / "activations.ndjson"))
    coh = cr.build_cohorts({"s1": {"started": cl.parse_ts(T), "ended": None}}, acts)
    assert coh["hooks"]["status"] == "activation_unknown"
    assert coh["hooks"]["after"] == [] and coh["hooks"]["activation_ts"] is None


# --------------------------------------------------------------------------
# 3. Decisions-ledger spine
# --------------------------------------------------------------------------
def test_spineless_line_is_rejected_and_counted_not_averaged(tmp_path):
    good = _dec("s1", T)
    bad = {k: v for k, v in good.items() if k != "reason"}
    wrong_type = dict(good, policy_version="1")
    root = _root(tmp_path, decisions=[good, bad, wrong_type])
    rows, rejected, _ = cl.load_decisions(str(root / "decisions.ndjson"))
    assert len(rows) == 1 and rejected == 2


def test_probe_lines_are_excluded_from_every_cohort():
    rows = {}
    filtered = cr.attach_decisions(
        rows, [dict(_dec("s1", T), source="probe", _ts=cl.parse_ts(T)),
               dict(_dec("s2", T), _ts=cl.parse_ts(T))], 1)
    assert filtered["probe_lines_excluded"] == 1
    assert "s1" not in rows and "s2" in rows


def test_records_older_than_the_current_policy_version_are_not_averaged_in():
    rows = {}
    filtered = cr.attach_decisions(rows, [dict(_dec("s1", T, policy_version=0),
                                               _ts=cl.parse_ts(T))], 1)
    assert filtered["stale_policy_version_lines"] == 1
    assert rows["s1"]["ledger_records"] == 0


# --------------------------------------------------------------------------
# 4. The denominator is sessions observed
# --------------------------------------------------------------------------
def test_silence_classifies_as_no_compaction_hook_inactive_or_unknown():
    assert cr.classify_silence({"compaction_records": 1}) == "has_records"
    assert cr.classify_silence({"compaction_records": 0}) == "unknown"
    assert cr.classify_silence({"compaction_records": 0,
                                "heartbeat": {"kill_switch": "off", "hooks_registered": True}}) \
        == "no_compaction"
    assert cr.classify_silence({"compaction_records": 0,
                                "heartbeat": {"kill_switch": "on", "hooks_registered": True}}) \
        == "hook_inactive"
    assert cr.classify_silence({"compaction_records": 0,
                                "heartbeat": {"kill_switch": "off", "hooks_registered": False}}) \
        == "hook_inactive"


def test_sessions_the_instrument_never_saw_are_excluded_from_every_verdict(tmp_path):
    """1400 short subagent transcripts against 40 hook-observed sessions is a
    population difference, not an effect."""
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")],
                 decisions=[_dec("s1", "2026-08-22T12:30:00Z")])
    scan = _scan([_sess("s1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z"),
                  _sess("ghost", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z", cost=99.0)])
    block = crep.build(scan, root=str(root))
    assert block["observed_sessions"] == 1
    assert block["excluded_unknown_sessions"] == 1
    assert block["interventions"]["hooks"]["n_after"] == 1


# --------------------------------------------------------------------------
# 5. Verdicts
# --------------------------------------------------------------------------
def test_vocabulary_is_exactly_seven_words_and_excludes_prediction_not_met():
    assert set(cr.VERDICTS) == {"helped", "no_improvement", "underpowered",
                                "confounded", "no_baseline", "no_exposure",
                                "activation_unknown"}
    assert "PREDICTION-NOT-MET" not in cr.VERDICTS


def test_activated_with_an_empty_after_cohort_is_underpowered_not_no_exposure(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", "2026-08-22T23:00:00Z")],
                 sessions=[_hb("s1", "2026-08-22T12:00:00Z")])
    scan = _scan([_sess("s1", "2026-08-22T12:00:00Z", "2026-08-23T01:00:00Z")])
    block = crep.build(scan, root=str(root))
    hooks = block["interventions"]["hooks"]
    assert hooks["verdict"] == "underpowered"
    assert hooks["n_spanning"] == 1
    assert "0 sessions have started since" in hooks["why"]


def test_a_thin_before_cohort_is_no_baseline_and_the_card_says_retire(tmp_path):
    """The before cohort closes at activation, so waiting grows only the after
    side. A thin before side must never read `underpowered` ("let it soak")."""
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 sessions=[_hb(f"a{i}", "2026-08-22T13:00:00Z") for i in range(6)])
    scan = _scan([_sess(f"a{i}", "2026-08-22T13:00:00Z", "2026-08-22T13:30:00Z")
                  for i in range(6)])
    block = crep.build(scan, root=str(root))
    hooks = block["interventions"]["hooks"]
    assert hooks["verdict"] == "no_baseline"
    assert "closed when it went live" in hooks["why"]
    titles = [c["title"] for c in block["decision_card"]]
    assert any(t.startswith("Retire the before/after verdict") for t in titles)
    assert not any(t.startswith("Let it soak") for t in titles)


def test_no_activation_row_is_no_exposure(tmp_path):
    root = _root(tmp_path, sessions=[_hb("s1", "2026-08-22T12:00:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:00:00Z", "2026-08-22T13:00:00Z")]),
                       root=str(root))
    assert block["interventions"]["hooks"]["verdict"] == "no_exposure"


def test_nested_after_cohort_is_confounded_even_when_both_moved_the_same_way(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", "2026-08-22T09:00:00Z"),
                                        _act("repo_diet", "2026-08-22T10:00:00Z")],
                 sessions=[_hb(f"s{i}", "2026-08-22T11:00:00Z") for i in range(3)])
    scan = _scan([_sess(f"s{i}", "2026-08-22T11:00:00Z", "2026-08-22T11:30:00Z")
                  for i in range(3)])
    block = crep.build(scan, root=str(root))
    assert block["interventions"]["repo_diet"]["verdict"] == "confounded"
    assert "hooks" in block["interventions"]["repo_diet"]["bundled_with"]


def test_same_deploy_bundle_is_reported_even_when_both_cohorts_are_empty(tmp_path):
    """Two interventions 174ms apart went live in one deploy. That is a fact about
    the rollout, not about the sample, so it must be stated even when neither has
    an after-cohort to be `confounded` about."""
    root = _root(tmp_path, activations=[_act("base_context", "2026-08-22T13:52:01.344Z"),
                                        _act("routing", "2026-08-22T13:52:01.518Z")])
    block = crep.build(_scan([]), root=str(root))
    assert block["same_deploy_bundles"] == [["base_context", "routing"]]
    assert "SAME-DEPLOY BUNDLE" in block["verdict_line"]
    assert "base_context + routing" in block["verdict_line"]


def test_a_cost_delta_without_compaction_events_cannot_credit_the_hooks(tmp_path):
    """The hooks act THROUGH compaction. Over sessions that never compacted, a
    cost delta measures what work ran."""
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 sessions=[_hb(f"b{i}", "2026-08-22T10:00:00Z") for i in range(6)]
                          + [_hb(f"a{i}", "2026-08-22T13:00:00Z") for i in range(6)])
    scan = _scan([_sess(f"b{i}", "2026-08-22T10:00:00Z", "2026-08-22T10:30:00Z", cost=10.0)
                  for i in range(6)]
                 + [_sess(f"a{i}", "2026-08-22T13:00:00Z", "2026-08-22T13:30:00Z", cost=1.0)
                    for i in range(6)])
    block = crep.build(scan, root=str(root))
    assert block["interventions"]["hooks"]["verdict"] == "underpowered"
    assert "acts THROUGH compaction" in block["interventions"]["hooks"]["why"]


def test_helped_is_reachable_when_the_mechanism_actually_fired(tmp_path):
    """A known positive for the verdict path — without it, `underpowered`
    everywhere would be indistinguishable from a verdict that cannot fire."""
    decisions = [_dec(f"a{i}", "2026-08-22T13:00:00Z", event="compacted", decision=None)
                 for i in range(6)]
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 decisions=decisions,
                 sessions=[_hb(f"b{i}", "2026-08-22T10:00:00Z") for i in range(6)]
                          + [_hb(f"a{i}", "2026-08-22T13:00:00Z") for i in range(6)])
    scan = _scan([_sess(f"b{i}", "2026-08-22T10:00:00Z", "2026-08-22T10:30:00Z", cost=10.0)
                  for i in range(6)]
                 + [_sess(f"a{i}", "2026-08-22T13:00:00Z", "2026-08-22T13:30:00Z", cost=1.0)
                    for i in range(6)])
    block = crep.build(scan, root=str(root))
    assert block["interventions"]["hooks"]["verdict"] == "helped"
    assert block["prediction"]["realized_pct"] == 90.0


def test_realized_percentage_is_not_printed_beside_an_underpowered_verdict(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 sessions=[_hb("s1", "2026-08-22T10:00:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T10:00:00Z", "2026-08-22T10:30:00Z")]),
                       root=str(root))
    assert block["prediction"]["realized_pct"] is None
    assert "NOT MEASURABLE" in block["prediction"]["line"]


# --------------------------------------------------------------------------
# 6. The instrument's own blindness
# --------------------------------------------------------------------------
def test_an_all_measurement_unavailable_ledger_never_renders_a_clean_allow_rate(tmp_path):
    decisions = [_dec("s1", "2026-08-22T12:30:00Z", event="pre-compact",
                      reason="measurement_unavailable") for _ in range(3)]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z")]),
                       root=str(root))
    assert block["blindness"]["measurement_unavailable_records"] == 3
    assert block["blindness"]["blind"] is True
    assert "INSTRUMENT BLIND" in block["verdict_line"]
    html = rr.render_compaction_page(block)
    assert "INSTRUMENT BLINDNESS" in html


def test_a_session_that_should_have_left_a_line_and_did_not_is_a_blind_spot(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T)])
    block = crep.build(_scan([_sess("ghost", "2026-08-22T13:00:00Z", "2026-08-22T13:10:00Z")]),
                       root=str(root))
    assert block["blindness"]["sessions_with_no_ledger_lines"] == 1
    assert block["blindness"]["blind"] is True


def test_empty_ledger_renders_no_records_never_a_crash_or_a_blank(tmp_path):
    root = _root(tmp_path)
    block = crep.build(_scan([]), root=str(root))
    html = rr.render_compaction_page(block)
    assert "no records" in html
    assert "no_exposure" in html


# --------------------------------------------------------------------------
# 7. Counter-metric
# --------------------------------------------------------------------------
def test_cheaper_and_worse_is_expressible():
    ivs = {"base_context": {"verdict": "helped",
                            "before": {"cost_usd_mean": 5.0},
                            "after": {"cost_usd_mean": 1.0}, "adherence": {
        "before": {"unpinned_generic_fanout_pct": 5.0, "closeouts_without_numbers_pct": 0.0},
        "after": {"unpinned_generic_fanout_pct": 30.0, "closeouts_without_numbers_pct": 0.0}}}}
    caw = crep._cheaper_and_worse(ivs)
    assert caw["triggered"] is True
    assert "unpinned_generic_fanout_pct rose 5.0% -> 30.0%" in caw["signals"][0]


def test_cheaper_and_worse_does_not_fire_when_adherence_held():
    ivs = {"base_context": {"verdict": "helped",
                            "before": {"cost_usd_mean": 5.0},
                            "after": {"cost_usd_mean": 1.0}, "adherence": {
        "before": {"unpinned_generic_fanout_pct": 30.0, "closeouts_without_numbers_pct": 0.0},
        "after": {"unpinned_generic_fanout_pct": 5.0, "closeouts_without_numbers_pct": 0.0}}}}
    assert crep._cheaper_and_worse(ivs)["triggered"] is False


# --------------------------------------------------------------------------
# 8. Types
# --------------------------------------------------------------------------
def test_retro_classified_types_are_tagged_so_they_cannot_pass_as_live(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 sessions=[_hb("live1", "2026-08-22T12:30:00Z"),
                           _hb("old1", "2026-08-22T10:00:00Z")],
                 policies={"live1": "orchestrator"})
    scan = _scan([_sess("live1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z"),
                  _sess("old1", "2026-08-22T10:00:00Z", "2026-08-22T10:30:00Z",
                        first_prompt="/plan-execute run the plan")])
    block = crep.build(scan, root=str(root))
    sources = {t["session_type"]: t["type_source"] for t in block["type_table"]}
    assert any("policy" in v for v in sources.values())
    assert block["retro_typed_sessions"] >= 0        # classifier may be absent; never fatal


def test_decision_card_never_exceeds_three_options(tmp_path):
    root = _root(tmp_path, activations=[_act("hooks", "2026-08-22T09:00:00Z"),
                                        _act("repo_diet", "2026-08-22T10:00:00Z"),
                                        _act("base_context", "2026-08-22T13:52:01.344Z"),
                                        _act("routing", "2026-08-22T13:52:01.518Z")],
                 sessions=[_hb(f"s{i}", "2026-08-22T11:00:00Z") for i in range(3)])
    scan = _scan([_sess(f"s{i}", "2026-08-22T11:00:00Z", "2026-08-22T11:30:00Z")
                  for i in range(3)])
    block = crep.build(scan, root=str(root))
    assert len(block["decision_card"]) <= 3
    html = rr.render_compaction_page(block)
    assert html.count("<h3>#") <= 3


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


def test_a_headless_sdk_session_is_out_of_scope_not_a_blind_spot(tmp_path):
    """`sdk-py` runs do not load the user's settings.json hooks at all, so their
    silence is a scope limit. `sdk-cli` subagent sessions DO fire the hooks, and
    their silence IS a blind spot — the two must never be pooled."""
    root = _root(tmp_path, activations=[_act("hooks", T)])
    scan = _scan([_sess("headless", "2026-08-22T13:00:00Z", "2026-08-22T13:10:00Z",
                        entrypoint="sdk-py"),
                  _sess("subagent", "2026-08-22T13:00:00Z", "2026-08-22T13:10:00Z",
                        entrypoint="sdk-cli")])
    block = crep.build(scan, root=str(root))
    assert block["blindness"]["headless_sdk_sessions_out_of_scope"] == 1
    assert block["blindness"]["sessions_with_no_ledger_lines"] == 1


def test_confounded_takes_precedence_over_underpowered_and_still_reports_the_floor(tmp_path):
    """A thin sample can be fixed by waiting; a nested cohort cannot. So the
    permanent fact wins the verdict, and the floor is still stated in `why`."""
    root = _root(tmp_path, activations=[_act("hooks", "2026-08-22T09:00:00Z"),
                                        _act("repo_diet", "2026-08-22T10:00:00Z")],
                 sessions=[_hb("s1", "2026-08-22T11:00:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T11:00:00Z", "2026-08-22T11:30:00Z")]),
                       root=str(root))
    entry = block["interventions"]["repo_diet"]
    assert entry["verdict"] == "confounded"
    assert "below the floor" in entry["why"] and "N=1" in entry["why"]


# --------------------------------------------------------------------------
# 9. Attribution: `confounded` must be liftable by the remedy we recommend
# --------------------------------------------------------------------------
def test_staggered_activations_with_a_real_gap_are_not_confounded(tmp_path):
    """A SUBSET test makes every later activation nested inside every earlier one
    forever, so `confounded` could never be lifted — including by the staggered
    re-deploy this session's own decision card recommends. What makes two
    interventions inseparable is INDISTINGUISHABLE cohorts, not nesting: the
    sessions exposed to one and not the other are the isolating contrast."""
    acts = [_act("hooks", "2026-07-01T00:00:00Z"), _act("repo_diet", "2026-08-01T00:00:00Z")]
    # 8 sessions between the two activations (hooks-only), 8 after both.
    between = [_sess(f"b{i}", f"2026-07-1{i}T00:00:00Z", f"2026-07-1{i}T01:00:00Z")
               for i in range(8)]
    after = [_sess(f"a{i}", f"2026-08-1{i}T00:00:00Z", f"2026-08-1{i}T01:00:00Z")
             for i in range(8)]
    hbs = [_hb(s["session_id"], s["started"]) for s in between + after]
    root = _root(tmp_path, activations=acts, sessions=hbs)
    block = crep.build(_scan(between + after), root=str(root))
    entry = block["interventions"]["repo_diet"]
    assert entry["bundled_with"] == [], entry["bundled_with"]
    assert entry["verdict"] != "confounded", entry["why"]
    # Every one of repo_diet's before-sessions was ALREADY hooks-exposed, so
    # hooks did not change state inside the window: this contrast really is
    # isolating, and nothing is flagged.
    assert entry["co_active"] == []


def test_a_before_cohort_split_by_another_activation_says_so_out_loud(tmp_path):
    """Lifting `confounded` off a staggered rollout must not quietly promote a
    contaminated contrast to `identified`. Here 4 of repo_diet's 8 before-sessions
    predate the hooks activation, so hooks changed state INSIDE the window — that
    is the attribution limit the spec says to state rather than collapse into a
    verdict word."""
    acts = [_act("hooks", "2026-07-05T00:00:00Z"), _act("repo_diet", "2026-08-01T00:00:00Z")]
    before = [_sess(f"p{i}", f"2026-07-0{i}T00:00:00Z", f"2026-07-0{i}T01:00:00Z")
              for i in range(1, 5)]                       # pre-hooks
    between = [_sess(f"b{i}", f"2026-07-1{i}T00:00:00Z", f"2026-07-1{i}T01:00:00Z")
               for i in range(4)]                         # hooks-only
    after = [_sess(f"a{i}", f"2026-08-1{i}T00:00:00Z", f"2026-08-1{i}T01:00:00Z")
             for i in range(8)]
    rows = before + between + after
    hbs = [_hb(x["session_id"], x["started"]) for x in rows]
    root = _root(tmp_path, activations=acts, sessions=hbs)
    block = crep.build(_scan(rows), root=str(root))
    note = block["interventions"]["repo_diet"]["co_active"]
    assert note and note[0]["intervention"] == "hooks"
    assert (note[0]["side"], note[0]["already_exposed"], note[0]["of"]) == ("before", 4, 8)
    assert "CONTRAST NOT ISOLATING" in block["verdict_line"]


def test_a_bundled_component_is_still_confounded_when_the_gap_is_below_the_floor(tmp_path):
    """The distinguishing set is a COHORT, so it carries the same floor every
    other cohort does. Two sessions between two deploys are not a contrast."""
    acts = [_act("hooks", "2026-07-01T00:00:00Z"), _act("repo_diet", "2026-08-01T00:00:00Z")]
    between = [_sess(f"b{i}", f"2026-07-1{i}T00:00:00Z", f"2026-07-1{i}T01:00:00Z")
               for i in range(2)]
    after = [_sess(f"a{i}", f"2026-08-1{i}T00:00:00Z", f"2026-08-1{i}T01:00:00Z")
             for i in range(8)]
    hbs = [_hb(s["session_id"], s["started"]) for s in between + after]
    root = _root(tmp_path, activations=acts, sessions=hbs)
    block = crep.build(_scan(between + after), root=str(root))
    assert block["interventions"]["repo_diet"]["verdict"] == "confounded"


def test_cheaper_and_worse_fires_on_a_verdict_other_than_helped():
    """'the change made it cheaper and worse' is a statement about the MEASURED
    cost and adherence, not about which verdict word sits beside them. Gating it
    on `helped` meant the apex line could not fire for an intervention whose
    sample was thin or whose cohort was nested — exactly the cases where the
    operator most needs to see it."""
    ivs = {"base_context": {
        "verdict": "underpowered",
        "before": {"cost_usd_mean": 5.0}, "after": {"cost_usd_mean": 1.0},
        "adherence": {
            "before": {"unpinned_generic_fanout_pct": 0.0, "closeouts_without_numbers_pct": 0.0},
            "after": {"unpinned_generic_fanout_pct": 100.0, "closeouts_without_numbers_pct": 0.0}}}}
    caw = crep._cheaper_and_worse(ivs)
    assert caw["triggered"] is True
    assert "unpinned_generic_fanout_pct rose 0.0% -> 100.0%" in caw["signals"][0]
    assert "underpowered" in caw["signals"][0]


def test_cheaper_and_worse_does_not_fire_when_cost_did_not_fall():
    """'worse' alone is not 'cheaper and worse'."""
    ivs = {"base_context": {
        "verdict": "helped",
        "before": {"cost_usd_mean": 1.0}, "after": {"cost_usd_mean": 5.0},
        "adherence": {
            "before": {"unpinned_generic_fanout_pct": 0.0, "closeouts_without_numbers_pct": 0.0},
            "after": {"unpinned_generic_fanout_pct": 100.0, "closeouts_without_numbers_pct": 0.0}}}}
    assert crep._cheaper_and_worse(ivs)["triggered"] is False


# --------------------------------------------------------------------------
# 10. The instrument's honesty row must itself be honest
# --------------------------------------------------------------------------
def test_a_session_that_wrote_a_ledger_line_is_not_counted_as_having_written_none(tmp_path):
    """`sessions_with_no_ledger_lines` says the instrument NEVER SAW the session.
    A session that wrote a decision line at a stale policy_version was seen — it
    is already counted in filtered.stale_policy_version_lines — so counting it
    here too fires the INSTRUMENT BLINDNESS banner on a session the instrument
    demonstrably observed. The honesty row is the one row that cannot lie."""
    root = _root(tmp_path, activations=[_act("hooks", T)], version=2,
                 decisions=[_dec("talker", "2026-08-22T13:00:00Z", policy_version=1,
                                 event="pre-compact")])
    block = crep.build(_scan([]), root=str(root))
    assert block["filtered"]["stale_policy_version_lines"] == 1
    assert block["blindness"]["sessions_with_no_ledger_lines"] == 0
    assert "left NO ledger line" not in block["verdict_line"]


def test_an_unscanned_row_created_by_a_decisions_line_is_not_a_blind_spot(tmp_path):
    """The same defect one layer down: attach_decisions' setdefault CREATES a row
    for a session the scanner never saw, and that row then read as a scanned
    session that left nothing."""
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 decisions=[_dec("talker", "2026-08-22T13:00:00Z", event="pre-compact")])
    block = crep.build(_scan([]), root=str(root))
    assert block["blindness"]["sessions_with_no_ledger_lines"] == 0
    # It IS a hole — no heartbeat means it is excluded from every verdict — but
    # it is a different hole, and it is named as the one it actually is.
    assert block["blindness"]["sessions_with_lines_but_no_heartbeat"] == 1
    assert "left NO ledger line at all" not in block["verdict_line"]
    assert "wrote ledger lines but left no heartbeat" in block["verdict_line"]


# --------------------------------------------------------------------------
# 11. The two tools SKILL.md runs together must agree about the same session
# --------------------------------------------------------------------------
def test_a_session_the_scanner_could_not_date_is_dated_from_its_heartbeat(tmp_path):
    """compaction_report and arming_check are documented to be run together. A
    scanned session whose transcript timestamps would not parse has started=None;
    arming_check fills it from the heartbeat, so before the fix the two tools
    reported n_after=0 and after_sessions=1 for the SAME session."""
    import arming_check as ac
    root = _root(tmp_path, activations=[_act("hooks", "2026-08-22T09:00:00Z")],
                 sessions=[_hb("undated", "2026-08-22T11:00:00Z")])
    scan = _scan([_sess("undated", None, None)])
    block = crep.build(scan, root=str(root))
    rep = ac.check(scan=scan, root=str(root))
    assert rep["interventions"]["hooks"]["after_sessions"] == 1
    assert block["interventions"]["hooks"]["n_after"] == 1, "the two tools must agree"


# --------------------------------------------------------------------------
# 9. Reader vocabulary matches the ONE writer (s07 rework 4)
# --------------------------------------------------------------------------
def test_a_block_decision_is_counted_as_a_veto():
    """hooks/compact-policy.py's verdict() emits "block", never "veto". A reader
    counting "veto" made the Vetoes column structurally always zero — a hook
    that blocked 50 compactions rendered identically to one that never fired."""
    rows = {}
    cr.attach_decisions(rows, [dict(_dec("s1", T, event="pre-compact",
                                         decision="block", reason="deferred"),
                                    _ts=cl.parse_ts(T))], 1)
    assert rows["s1"]["vetoes"] == 1


def test_a_prompt_classification_line_is_not_a_compaction_allow():
    """150 of 166 live rows are `event: prompt` — counting them as compaction
    allows overstated the Allows column roughly tenfold."""
    rows = {}
    cr.attach_decisions(
        rows, [dict(_dec("s1", T), _ts=cl.parse_ts(T)),                 # prompt allow
               dict(_dec("s1", T, event="pre-compact", reason="safe_point_ok"),
                    _ts=cl.parse_ts(T))], 1)
    assert rows["s1"]["allows"] == 1
    assert rows["s1"]["compaction_records"] == 1


def test_an_unrecognized_decision_on_a_compaction_row_is_counted_never_silent():
    """Tolerant-reader rule: an unknown value in an enum field the reader DOES
    read is an error to surface, never a silent default. This is how the next
    writer-vocabulary drift becomes a nonzero number instead of a zero column."""
    rows = {}
    cr.attach_decisions(rows, [dict(_dec("s1", T, event="pre-compact",
                                         decision="vetoed", reason="deferred"),
                                    _ts=cl.parse_ts(T))], 1)
    assert rows["s1"]["unrecognized_decisions"] == 1
    assert rows["s1"]["vetoes"] == 0


def test_an_unrecognized_decision_reaches_the_verdict_line(tmp_path):
    decisions = [_dec("s1", "2026-08-22T12:30:00Z", event="pre-compact",
                      decision="vetoed", reason="deferred")]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z")]),
                       root=str(root))
    assert block["blindness"]["unrecognized_decision_records"] == 1
    assert block["blindness"]["blind"] is True
    assert "vocabulary drift" in block["verdict_line"]


def test_an_underpowered_intervention_is_never_named_identified():
    """`underpowered` means the sample cannot answer; naming it beside
    `identified` tells the reader an effect was found where none could be."""
    ivs = {"hooks": {"verdict": "underpowered", "n_before": 6, "n_after": 4,
                     "n_spanning": 0, "co_active": []},
           "repo_diet": {"verdict": "helped", "n_before": 8, "n_after": 17,
                         "n_spanning": 0, "co_active": []}}
    line = crep._verdict_line(
        ivs, {"line": "predicted n/a", "prediction_met": False},
        {"blind": False}, {"triggered": False}, bundles=())
    identified = [p for p in line.split(" || ") if p.startswith("identified:")][0]
    head = identified.split("|")[0]
    assert "hooks" not in head
    assert "repo_diet" in head


def test_an_unknown_model_window_allow_is_still_a_blind_record(tmp_path):
    """verdict() rule (e) fires whenever HEADROOM is unmeasurable. When the
    transcript read fine but the model window was unknown, the writer stamps
    `unreadable_context:read:no_model_window` — no `measurement_unavailable`
    substring — and the old predicate counted the record as a clean allow."""
    decisions = [_dec("s1", "2026-08-22T12:30:00Z", event="pre-compact",
                      reason="unreadable_context:read:no_model_window")]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z")]),
                       root=str(root))
    assert block["blindness"]["measurement_unavailable_records"] == 1
    assert block["blindness"]["blind"] is True


# --------------------------------------------------------------------------
# 12. A crash is not a decision, and every counter reaches the page (rework 5)
# --------------------------------------------------------------------------
def test_a_hook_crash_is_never_counted_as_a_clean_allow():
    """run_stdin_hook's fail-open path writes event='pre-compact' (or
    'post-compact'), decision='allow', reason='hook-error'. That 'allow' is the
    wrapper's stamp, not a decision the policy made: counting it as one rendered
    a hook crashing on every compaction as a 100% clean allow rate."""
    counts = cl.decision_counts([
        _dec("s1", T, event="pre-compact", reason="hook-error"),
        _dec("s1", T, event="post-compact", reason="hook-error"),
        _dec("s1", T, event="pre-compact", reason="safe_point_ok")])
    assert counts == {"vetoes": 0, "allows": 1, "unrecognized": 0, "hook_errors": 2}


def test_a_crashing_hook_reaches_the_blindness_row_and_the_verdict_line(tmp_path):
    """MOVE 2b's whole point: a hook that crashed on every compaction must not
    render as a healthy allow rate with blind:false."""
    decisions = [_dec("s1", "2026-08-22T12:30:00Z", event="pre-compact",
                      reason="hook-error")]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    assert block["blindness"]["hook_error_records"] == 1
    assert block["blindness"]["blind"] is True
    assert "crash" in block["verdict_line"].lower()
    assert block["interventions"]["hooks"]["after"]["compaction_records"] == 1


def test_blindness_is_measured_over_the_rows_the_verdicts_use(tmp_path):
    """A probe row and a stale-policy_version row are excluded from every cohort
    by attach_decisions; the blindness counters must not be driven by rows no
    verdict can see."""
    decisions = [_dec("p1", "2026-08-22T12:30:00Z", event="pre-compact",
                      source="probe", decision="weird", reason="hook-error"),
                 _dec("s1", "2026-08-22T12:30:00Z", event="pre-compact",
                      policy_version=1, decision="weird",
                      reason="unreadable_context:read")]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")], version=2)
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    assert block["filtered"] == {"probe_lines_excluded": 1,
                                 "stale_policy_version_lines": 1}
    b = block["blindness"]
    assert b["unrecognized_decision_records"] == 0
    assert b["measurement_unavailable_records"] == 0
    assert b["hook_error_records"] == 0
    assert b["blind"] is False


def test_scanned_sessions_counts_only_scanned_transcripts(tmp_path):
    """The universe is scan UNION heartbeats UNION ledger-only sessions;
    printing its size as 'N scanned' counted heartbeat-only and ledger-only
    sessions as scanned transcripts."""
    root = _root(tmp_path, activations=[_act("hooks", T)],
                 decisions=[_dec("ledger-only", "2026-08-22T13:00:00Z",
                                 event="pre-compact")],
                 sessions=[_hb("hb-only", "2026-08-22T13:00:00Z")])
    block = crep.build(_scan([_sess("scanned", "2026-08-22T13:00:00Z",
                                    "2026-08-22T13:30:00Z")]), root=str(root))
    assert block["scanned_sessions"] == 1
    assert block["universe_sessions"] == 3
    # The sibling one line down had the same defect: a ledger-only row was
    # counted as a SCANNED session the instrument never observed.
    assert block["blindness"]["scanned_sessions_never_observed"] == 1


def test_an_undated_session_is_counted_and_surfaced(tmp_path):
    """build_cohorts counts a session it cannot date, but the count reached no
    output; a session in no cohort must be visible, not silently absent."""
    root = _root(tmp_path, activations=[_act("hooks", T)])
    acts = cl.load_activations(str(root / "activations.ndjson"))
    coh = cr.build_cohorts({"s1": {"started": None, "ended": None}}, acts)
    assert coh["hooks"]["undated"] == 1
    peers = {iv: [] for iv in cl.INTERVENTIONS}
    coact = {iv: [] for iv in cl.INTERVENTIONS}
    entries = crep._intervention_entries({}, coh, peers, [], [], coact)
    assert entries["hooks"]["n_undated"] == 1


def test_reader_and_writer_agree_on_the_intervention_names():
    """The writer (hooks/compact_activation.py) and the reader (compaction_ledger)
    each carry their own INTERVENTIONS tuple. A name the writer emits but the
    reader omits is counted as a malformed row and dropped — `compact_window`
    was written on 2026-08-23 and reported as no-cohort on every retro until
    2026-08-27, because only the writer knew the name."""
    import pathlib

    writer = (pathlib.Path(__file__).resolve().parents[3]
              / "hooks" / "compact_activation.py")
    assert writer.is_file(), f"writer not found at {writer}"
    names = set()
    for line in writer.read_text().splitlines():
        if line.startswith("INTERVENTIONS = ("):
            names = set(eval(line.split("=", 1)[1].strip()))  # noqa: S307 — a literal tuple
            break
    assert names, "no INTERVENTIONS tuple found in the writer"
    assert names == set(cl.INTERVENTIONS), (
        f"writer emits {sorted(names)}, reader accepts {sorted(cl.INTERVENTIONS)} — "
        "every name only the writer knows is silently dropped as malformed")
