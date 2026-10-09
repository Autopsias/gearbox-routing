"""aggregate_outcomes — the signals computed over aggregated cells (ADA-01).

The second half of the suite (cohorts, denominators and proposal thresholds are
in test_aggregate_outcomes.py). Covers: the apex revisit callout, cost-per-
success n/a vs computed, time-to-signal projection, the --since / --last read
window, canary smoke where administrative results are absence of evidence rather
than failure, the incremental stamp contract and the value-epoch range.

Shared builders: retro_test_helpers.py.

Run: pytest skills/routing-retro/scripts/test_aggregate_signals.py -q
"""
import json
from datetime import UTC, datetime

import aggregate_outcomes as ao
import retro_cohorts
import retro_common
from retro_test_helpers import _canary, _cohort, _rec, _run, _write

# --------------------------------------------------------------------------
# Apex revisit callout
# --------------------------------------------------------------------------
def _fable_escalated_cohort(session):
    return _cohort(
        session, result="passed",
        escalated_from={"authored": {"model": "opus"}, "ran": {"model": "fable"}, "rung": 2, "generation": 0},
    )


def test_apex_revisit_callout_fires_at_five(tmp_path, ssot_path):
    recs = [r for i in range(4) for r in _fable_escalated_cohort(f"s{i}")]
    out = _run(tmp_path, ssot_path, recs)
    assert out["apex_revisit"]["cumulative_fable_escalation_cohorts"] == 4
    assert out["apex_revisit"]["callout"] is False

    recs += _fable_escalated_cohort("s4")
    out = _run(tmp_path, ssot_path, recs)
    assert out["apex_revisit"]["cumulative_fable_escalation_cohorts"] == 5
    assert out["apex_revisit"]["callout"] is True
    assert "apex revisit" in out["apex_revisit"]["message"]
    assert "apex revisit" in out["markdown"].lower()


# --------------------------------------------------------------------------
# cost_per_success — n/a vs computed
# --------------------------------------------------------------------------
def test_cost_per_success_is_na_when_absent(tmp_path, ssot_path):
    recs = _cohort("s01", result="passed")
    out = _run(tmp_path, ssot_path, recs)
    assert out["cells"][0]["facts"]["cost_per_success"] == "n/a"


def test_cost_per_success_computed_when_present(tmp_path, ssot_path):
    recs = (_cohort("s01", result="passed", usage={"cost_usd": 2.0})
            + _cohort("s02", result="passed", usage={"cost_usd": 4.0}))
    out = _run(tmp_path, ssot_path, recs)
    assert out["cells"][0]["facts"]["cost_per_success"] == 3.0


def test_cost_per_success_is_na_when_any_attempt_is_unpriced(tmp_path, ssot_path):
    """A priced subset would read cheap: one unpriced attempt makes the cell n/a."""
    recs = (_cohort("s01", result="passed", usage={"cost_usd": 2.0})
            + _cohort("s02", result="passed", usage={"cost_usd": None}))
    out = _run(tmp_path, ssot_path, recs)
    assert out["cells"][0]["facts"]["cost_per_success"] == "n/a"


# --------------------------------------------------------------------------
# Time-to-signal
# --------------------------------------------------------------------------
def test_time_to_signal_reports_no_data_for_empty_cell(tmp_path, ssot_path):
    out = _run(tmp_path, ssot_path, [])
    agentic = next(e for e in out["time_to_signal"] if e["class"] == "agentic_build")
    assert agentic["current_n"] == 0
    assert agentic["upgrade_projection"]["status"] == "no_data"


def test_time_to_signal_reachable_now_once_n_met(tmp_path, ssot_path):
    recs = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed",
            ts=f"2026-08-{i+1:02d}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs)
    agentic = next(e for e in out["time_to_signal"] if e["class"] == "agentic_build")
    assert agentic["current_n"] == 6
    assert agentic["upgrade_projection"]["status"] == "threshold_reachable_now"
    assert agentic["downgrade_open_projection"]["status"] == "threshold_reachable_now"


def test_time_to_signal_projects_a_future_date_when_below_n(tmp_path, ssot_path):
    # 3 cohorts spread over ~30 days -> cadence ~3/month -> needs 3 more for N=6
    recs = [r for i in range(3) for r in _cohort(f"ok{i}", result="passed",
            ts=f"2026-08-{1 + i * 15:02d}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs, now=datetime(2026, 8, 31, tzinfo=UTC))
    agentic = next(e for e in out["time_to_signal"] if e["class"] == "agentic_build")
    assert agentic["upgrade_projection"]["status"] == "silence_expected"
    assert agentic["upgrade_projection"]["months"] > 0
    assert "earliest_fire_date" in agentic["upgrade_projection"]


# --------------------------------------------------------------------------
# --since / --last filters
# --------------------------------------------------------------------------
def test_since_filter_narrows_the_read_window(tmp_path, ssot_path):
    recs = (_cohort("old", result="passed", ts="2026-07-01T00:00:00+00:00")
            + _cohort("new", result="passed", ts="2026-08-10T00:00:00+00:00"))
    out = _run(tmp_path, ssot_path, recs, since="2026-08-01")
    assert out["ledger"]["records_parsed"] == 1
    assert out["cells"][0]["facts"]["n"] == 1


def test_since_filter_never_splits_a_cohort_across_the_boundary(tmp_path, ssot_path):
    # Reviewer finding 3: the filter used to run on raw attempt records BEFORE
    # cohorts were built. attempt 1 (rework) sits before the --since floor,
    # attempt 2 (the passing terminal) sits after it. A record-level filter
    # drops attempt 1 alone, leaving attempt 2 orphaned -> reported INCOMPLETE
    # (which the module's own docs define as ledger corruption). Filtering
    # must happen at the cohort level (keyed on the cohort's terminal ts) so
    # the whole cohort is kept or dropped together.
    recs = [
        _rec(session="s01", attempt=1, result="rework", verified=False,
             ts="2026-07-01T00:00:00+00:00", resolution="r1"),
        _rec(session="s01", attempt=2, result="passed",
             ts="2026-08-10T00:00:00+00:00", resolution="r2"),
    ]
    out = _run(tmp_path, ssot_path, recs, since="2026-08-01")
    assert out["cohorts"]["incomplete"] == 0
    assert out["cohorts"]["complete"] == 1
    assert out["cells"][0]["facts"]["n"] == 1


def test_filtered_run_does_not_advance_the_incremental_stamp(tmp_path, ssot_path):
    # Reviewer finding 7: a --since/--last run only analyzed a SUBSET of the
    # ledger, but write_stamp() previously stamped the FULL raw file boundary
    # unconditionally. A filtered run must not claim the whole file was
    # incorporated — the prior stamp (from the last FULL run, or none at all)
    # must be left untouched.
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed", ts="2026-08-01T00:00:00+00:00"))
    out1 = ao.run(ledger, ssot_path)
    assert out1["ok"] is True
    stamp_before = retro_common.stamp_path(ledger).read_text()

    with open(ledger, "a", encoding="utf-8") as f:
        for r in _cohort("s02", result="passed", ts="2026-08-10T00:00:00+00:00"):
            f.write(json.dumps(r) + "\n")
    out2 = ao.run(ledger, ssot_path, since="2026-08-05")
    assert out2["ok"] is True
    stamp_after = retro_common.stamp_path(ledger).read_text()
    assert stamp_before == stamp_after


# --------------------------------------------------------------------------
# Canary smoke — administrative results are absence of evidence, not failure
# --------------------------------------------------------------------------
def test_canary_smoke_blocked_cohort_is_not_a_smoke_failure(tmp_path, ssot_path):
    # Reviewer finding 1: the smoke check used to read the first 3 records of
    # cohorts_sorted (EVERY result, including administrative ones). One
    # `blocked` cohort among the first 3 flipped the whole canary to
    # smoke-failed, even when every GATE-CHECKED outcome (passed/exhausted)
    # actually passed. `blocked` means "never gate-checked", not "the model
    # under test failed" — it must be excluded from the smoke batch, not
    # counted as a failure in it.
    recs = (_canary("c1", result="passed", ts="2026-08-01T00:00:00+00:00")
            + _canary("c2", result="blocked", verified=False, ts="2026-08-02T00:00:00+00:00")
            + _canary("c3", result="passed", ts="2026-08-03T00:00:00+00:00")
            + _canary("c4", result="passed", ts="2026-08-04T00:00:00+00:00"))
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["smoke_failed"] is False
    assert cn["stage"] != "smoke-failed"
    assert cn["excluded"] == {"blocked": 1}


def test_canary_counts_only_effort_bound_claude_cohorts(tmp_path, ssot_path):
    # v25: every row tagged 4abfa765d726 ran prompt_directive_advisory —
    # no tier-opus-low agent existed, so each dispatch inherited the orchestrator's
    # effort and the canary "passed smoke" on a rung that never ran.
    # PLANT: unbound Claude cohorts must not count, even when they passed.
    unbound = dict(effort_mechanism="prompt_directive_advisory")
    recs = (_canary("c1", ts="2026-08-01T00:00:00+00:00", **unbound)
            + _canary("c2", ts="2026-08-02T00:00:00+00:00", **unbound)
            + _canary("c3", ts="2026-08-03T00:00:00+00:00", **unbound))
    cn = _run(tmp_path, ssot_path, recs)["canaries"][0]
    assert cn["stats"]["n"] == 0
    assert cn["stage"] == "smoke-in-progress"
    assert cn["excluded"] == {"effort_unbound": 3}

    # ALLOW CONTROL: a bound Claude cohort counts; a Codex cohort counts on its
    # advisory label, because that lane binds effort outside the tier-agent table.
    recs += _canary("c4", ts="2026-08-04T00:00:00+00:00")
    recs += _canary("x1", ts="2026-08-05T00:00:00+00:00", proposal_id="pidCodex",
                    backend="codex", model_authored="gpt-5.6-sol", **unbound)
    out = _run(tmp_path, ssot_path, recs)
    cn = next(c for c in out["canaries"] if c["proposal_id"] == "pidX")
    assert cn["stats"]["n"] == 1
    assert cn["excluded"] == {"effort_unbound": 3}
    codex = next(c for c in out["canaries"] if c["proposal_id"] == "pidCodex")
    assert codex["stats"]["n"] == 1
    assert "effort_unbound" not in codex["excluded"]


# --------------------------------------------------------------------------
# time_to_signal key normalization — classes with no effort dial
# --------------------------------------------------------------------------
def test_time_to_signal_reports_data_for_class_with_no_effort_dial(tmp_path, ssot_path):
    # Reviewer finding 2: time_to_signal keyed on the SSOT's native_effort
    # (None for mechanical/haiku, which has no effort dial per the fixture's
    # `cheap_fast` map), while the cells keyed on the ledger's
    # reasoning_authored (the writer normalizes an unset dial to "" via
    # run.py's _reasoning_tier, never None). None != "" as a dict key ->
    # mechanical/haiku cells always reported current_n: 0 / no_data even with
    # 6 real cohorts. Both sides must normalize the same way.
    recs = [r for i in range(6) for r in _cohort(
        f"m{i}", result="passed", task_class="mechanical", model_authored="haiku",
        reasoning_authored="", ts=f"2026-08-{i + 1:02d}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs)
    mech = next(e for e in out["time_to_signal"] if e["class"] == "mechanical")
    assert mech["current_n"] == 6
    assert mech["upgrade_projection"]["status"] == "threshold_reachable_now"


# --------------------------------------------------------------------------
# proposal_id stability across an SSOT version bump
# --------------------------------------------------------------------------
def test_proposal_id_independent_of_epoch(tmp_path, ssot_path):
    # Reviewer finding 4: proposal_id used to hash in the SSOT epoch. A
    # version bump mid-canary re-issues a DIFFERENT id for the same cell/kind,
    # so `existing_canary_pids` (computed from records tagged under the OLD
    # id) no longer matches the newly-computed id -> the aggregator opens a
    # duplicate "STAGE 1 SMOKE" instead of recognizing the in-flight canary.
    # proposal_id must be a pure function of (class, cell, kind), stable
    # across epochs; the epoch is already reported separately as
    # `ssot_version`.
    recs99 = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed", ssot_version_ran=99)]
    recs100 = [r for i in range(6) for r in _cohort(f"ok{i}b", result="passed", ssot_version_ran=100)]
    cohorts99, _, _ = ao.build_cohorts(recs99)
    cohorts100, _, _ = ao.build_cohorts(recs100)
    key = ao.cell_key(cohorts99[0])
    agg99 = ao.aggregate_cell(cohorts99, 99)
    agg100 = ao.aggregate_cell(cohorts100, 100)
    p99, _ = ao.cell_proposal(key, agg99, 99, set())
    p100, _ = ao.cell_proposal(key, agg100, 100, set())
    assert p99 is not None and p100 is not None
    assert p99["proposal_id"] == p100["proposal_id"]


# --------------------------------------------------------------------------
# Malformed-ratio bound on a small ledger — a lone trailing torn write
# --------------------------------------------------------------------------
def test_single_trailing_malformed_line_tolerated_on_small_ledger(tmp_path, ssot_path):
    # Reviewer finding 5: the 2% ratio bound cannot survive a SINGLE bad line
    # under 50 total lines (1/6 = 16.7% > 2%). The live writer leaves exactly
    # this shape after a crash: one torn line at the very END of the file.
    # That specific, narrow case (malformed count == 1 AND it is the last
    # physical line) must be tolerated regardless of ratio; the plan's bound
    # ("ratio > 2% OR >= 3 lines") still applies to everything else.
    ledger = tmp_path / "outcomes.ndjson"
    recs = [r for i in range(5) for r in _cohort(f"s{i}", result="passed")]
    lines = [json.dumps(r) for r in recs]
    lines.append("{torn trailing wri")   # malformed, and it's the LAST line
    ledger.write_text("\n".join(lines) + "\n")
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is True
    assert out["ledger"]["malformed"] == 1


def test_malformed_line_mid_file_on_small_ledger_still_aborts(tmp_path, ssot_path):
    # The trailing-torn-write carve-out is narrow: a malformed line that is
    # NOT the last line of the file is still subject to the ordinary bound —
    # this is not a blanket loosening of the ratio rule.
    ledger = tmp_path / "outcomes.ndjson"
    recs = [r for i in range(5) for r in _cohort(f"s{i}", result="passed")]
    lines = [json.dumps(r) for r in recs]
    lines.insert(2, "{mid file torn")   # malformed, NOT the last line
    ledger.write_text("\n".join(lines) + "\n")
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is False


# --------------------------------------------------------------------------
# cost_per_success — its n/a rule stated in the output, not hidden
# --------------------------------------------------------------------------
def test_cost_per_success_caveat_is_surfaced_in_output(tmp_path, ssot_path):
    # Reviewer finding 6: a metric that reads "n/a" for most cells is
    # indistinguishable from "no cost this run" unless the output says why.
    # Since s07 the writer emits usage.cost_usd; the caveat names that field
    # and the reason most attempts stay unpriced.
    out = _run(tmp_path, ssot_path, _cohort("s01", result="passed"))
    assert "usage.cost_usd" in out["cost_per_success_caveat"]
    assert "cache-creation" in out["cost_per_success_caveat"]


# --------------------------------------------------------------------------
# S06 rework attempt 2 — seven findings, each reproduced LIVE through run()
# against the real model-routing.yaml before being fixed (see the s06 session
# closeout notes); these are the tests that keep each repro alive.
# --------------------------------------------------------------------------
def test_downgrade_open_recognizes_inflight_canary_still_open(tmp_path, ssot_path):
    # S06 finding 1: an in-flight canary session (its cohort is still OPEN —
    # last record "rework", never reached a terminal result) must still
    # suppress a fresh downgrade-open proposal for the same cell.
    # `existing_canary_pids` used to be built only from COMPLETE experimental
    # cohorts, so the in-flight one was invisible and the aggregator
    # re-issued a duplicate "STAGE 1 SMOKE" on top of it.
    recs = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed")]
    out1 = _run(tmp_path, ssot_path, recs)
    pid = out1["cells"][0]["proposal"]["proposal_id"]
    recs += [_rec(session="inflight", attempt=1, result="rework", verified=False,
                   routing_provenance="experimental",
                   routing_experiment={"kind": "canary", "proposal_id": pid})]
    out2 = _run(tmp_path, ssot_path, recs)
    assert out2["cohorts"]["open"] == 1
    assert out2["cells"][0]["proposal"]["stage"] == "smoke-in-progress"


def test_canary_smoke_failure_aborts_before_batch_of_three(tmp_path, ssot_path):
    # S06 finding 2: "any failure aborts the experiment" (plan text) means
    # the MOMENT a failure appears in the smoke batch, not once 3 gate-checked
    # cohorts have accumulated. A single failing cohort (1 of an eventual 3)
    # used to report "smoke-in-progress" — this skill's own SKILL.md defines
    # that as "no action" — instead of the documented immediate abort.
    recs = _canary("c1", result="exhausted", verified=False, ts="2026-08-01T00:00:00+00:00")
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["smoke_failed"] is True
    assert cn["stage"] == "smoke-failed"


def test_time_to_signal_ignores_pinned_override_cohorts_for_current_n(tmp_path, ssot_path):
    # S06 finding 3: time_to_signal read facts.n, which also counts
    # pinned_override/prior-epoch cohorts that can NEVER feed a proposal — an
    # 8-cohort pinned_override cell reported "sample-size floor already met"
    # while its status stayed below_min_n / proposal_pool_n: 0 forever. The
    # projection must count the pool that actually gates the proposal
    # (attested_pool_n — the same count cell_proposal compares to
    # MIN_N_UPGRADE/MIN_N_DOWNGRADE_OPEN).
    recs = [r for i in range(8) for r in _cohort(
        f"pinned{i}", result="passed", routing_provenance="pinned_override",
        ts=f"2026-08-{i + 1:02d}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8
    assert cell["proposal_pool_n"] == 0
    assert cell["status"] == "below_min_n"
    agentic = next(e for e in out["time_to_signal"] if e["class"] == "agentic_build")
    assert agentic["current_n"] == 0
    assert agentic["upgrade_projection"]["status"] != "threshold_reachable_now"


def test_last_filter_counts_cohorts_globally_not_per_bucket(tmp_path, ssot_path):
    # S06 finding 4: --last N used to be applied to the complete, incomplete
    # and open lists INDEPENDENTLY, so --last 2 returned up to 6 cohorts. It
    # must read as "the last N cohorts" (the unit of analysis) across the
    # whole run.
    recs = []
    for i in range(3):
        recs += _cohort(f"complete{i}", result="passed", attempts=1,
                         ts=f"2026-08-0{i + 1}T00:00:00+00:00")
    for i in range(3):
        recs += [_rec(session=f"incomplete{i}", attempt=2, result="passed",
                       ts=f"2026-08-1{i + 1}T00:00:00+00:00")]
    for i in range(3):
        recs += [_rec(session=f"open{i}", attempt=1, result="rework", verified=False,
                       ts=f"2026-08-2{i + 1}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs, last=2)
    total = out["cohorts"]["complete"] + out["cohorts"]["incomplete"] + out["cohorts"]["open"]
    assert total == 2


def test_since_filter_tolerates_timestamp_without_timezone(tmp_path, ssot_path):
    # S06 finding 5: a ts with no timezone offset parses as a naive datetime;
    # comparing it to the (tz-aware) --since floor raised "TypeError: can't
    # compare offset-naive and offset-aware datetimes" and killed the run. A
    # bare timestamp must be treated as UTC, not crash the aggregator.
    recs = _cohort("s01", result="passed", ts="2026-08-01T00:00:00")  # no offset
    out = _run(tmp_path, ssot_path, recs, since="2026-07-01")
    assert out["ok"] is True


def test_run_survives_missing_ledger_parent_directory(tmp_path, ssot_path):
    # S06 finding 6: write_stamp() raised an uncaught FileNotFoundError when
    # the ledger's parent directory doesn't exist, while every other read on
    # that path (snapshot_boundary/read_prefix) already degrades to a valid
    # empty result. write_stamp must degrade the same way rather than crash
    # the whole run over a housekeeping side effect.
    ledger = tmp_path / "nonexistent" / "outcomes.ndjson"
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is True
    assert out["ledger"]["total_lines"] == 0


def test_adoptions_default_path_derives_from_ledger_being_analyzed(tmp_path, ssot_path):
    # S06 finding 7: read_adoptions()'s own fallback always points at the
    # live repo's evals/routing/adoptions.ndjson regardless of which ledger is
    # being analyzed. A run against an alternate ledger (e.g. under tmp_path)
    # must consult the adoptions.ndjson SIDE BY SIDE with THAT ledger, not
    # inherit production adoption state.
    ledger = tmp_path / "outcomes.ndjson"
    (tmp_path / "adoptions.ndjson").write_text('{"proposal_id": "pidX"}\n')
    _write(ledger, _canary("c1", result="passed", proposal_id="pidX"))
    out = ao.run(ledger, ssot_path)
    assert out["canaries"][0]["adopted"] is True


# --------------------------------------------------------------------------
# Round-3 review findings
# --------------------------------------------------------------------------
def test_smoke_failed_canary_frees_its_cell_for_a_new_experiment(tmp_path, ssot_path):
    # Round-3 finding 3: existing_canary_pids collected EVERY experimental
    # cohort regardless of stage, so a smoke-FAILED canary kept its proposal_id
    # in the "already underway" set. The one JSON document then contradicted
    # itself -- canaries[] said "smoke-failed" while the cell said "canary
    # already underway" -- and that cell could never open another downgrade
    # experiment. SKILL.md step 3 defines smoke-failed as "drop the proposal",
    # so the cell must be free to open a fresh one.
    recs = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed")]
    pid = _run(tmp_path, ssot_path, recs)["cells"][0]["proposal"]["proposal_id"]
    # a smoke batch for that pid in which one gate-checked run did NOT pass
    recs += _canary("c1", result="passed", proposal_id=pid,
                    model_authored="opus", reasoning_authored="high",
                    ts="2026-08-01T00:00:00+00:00")
    recs += _canary("c2", result="exhausted", verified=True, proposal_id=pid,
                    model_authored="opus", reasoning_authored="high",
                    ts="2026-08-02T00:00:00+00:00")
    out = _run(tmp_path, ssot_path, recs)
    assert out["canaries"][0]["stage"] == "smoke-failed"
    # the cell must NOT still be told an experiment is running
    cell = out["cells"][0]
    assert cell["proposal"]["stage"] == "open"
    assert "already underway" not in cell["proposal"]["recommendation"]


def test_time_to_signal_cadence_comes_from_the_pool_it_counts(tmp_path, ssot_path):
    # Round-3 finding 2: `n` was taken from the ATTESTED pool while the cadence
    # came from the FACTS pool. When nothing is attested the attested pool stays
    # at 0 forever while the facts pool keeps growing, so the projection
    # promised a concrete "earliest" date that could never arrive. A pool that
    # does not grow must read as "never at the current cadence".
    recs = [r for i in range(8) for r in _cohort(
        f"u{i}", result="passed", model_ran_source="unknown",
        ts=f"2026-08-{i + 1:02d}T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs)
    row = next(e for e in out["time_to_signal"] if e["class"] == "agentic_build")
    assert row["current_n"] == 0          # nothing admissible -> nothing countable
    # the cadence must describe the SAME (empty) pool, so no date is promised
    assert not row["cadence_per_month"]
    assert row["upgrade_projection"]["status"] != "threshold_reachable_now"
    assert not row["upgrade_projection"].get("earliest_fire_date")


# --------------------------------------------------------------------------
# S07 — did_it_help (adoption before/after comparison)
# --------------------------------------------------------------------------
def _write_adoptions(tmp_path, records):
    with open(tmp_path / "adoptions.ndjson", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_did_it_help_underpowered_below_min_n_either_side(tmp_path, ssot_path):
    _write_adoptions(tmp_path, [{
        "proposal_id": "pidZ", "action": "adopt", "class": "agentic_build",
        "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
        "date": "2026-08-10",
    }])
    # only 3 "before" cohorts, none "after" -> underpowered on both counts
    recs = [r for i in range(3) for r in _cohort(f"b{i}", result="passed", ssot_version_ran=99)]
    out = _run(tmp_path, ssot_path, recs)
    row = next(e for e in out["did_it_help"] if e["proposal_id"] == "pidZ")
    assert row["before"]["n"] == 3
    assert row["after"]["n"] == 0
    assert row["verdict"] == "underpowered"


def test_did_it_help_compares_by_ssot_version_ran_not_calendar_date(tmp_path, ssot_path):
    # [HARDENED:codex-verify-r3] a record dated AFTER the adoption date but
    # still carrying the OLD ssot_version_ran (pre-deploy) must land in
    # "before", never "after" — grouping is strictly by ssot_version_ran.
    _write_adoptions(tmp_path, [{
        "proposal_id": "pidY", "action": "adopt", "class": "agentic_build",
        "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
        "date": "2026-08-01",
    }])
    recs = [r for i in range(6) for r in _cohort(
        f"before{i}", result="passed", ssot_version_ran=99,
        ts="2026-08-15T00:00:00+00:00")]  # dated AFTER the adoption, but old epoch
    recs += [r for i in range(6) for r in _cohort(
        f"after{i}", result="passed", ssot_version_ran=100,
        ts="2026-08-02T00:00:00+00:00")]
    out = _run(tmp_path, ssot_path, recs)
    row = next(e for e in out["did_it_help"] if e["proposal_id"] == "pidY")
    assert row["before"]["n"] == 6
    assert row["after"]["n"] == 6
    assert row["verdict"] == "no_improvement"  # both pools are 100% pass, no delta


def test_did_it_help_reports_helped_when_after_pass_rate_improves(tmp_path, ssot_path):
    _write_adoptions(tmp_path, [{
        "proposal_id": "pidW", "action": "adopt", "class": "agentic_build",
        "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
        "date": "2026-08-01",
    }])
    recs = [r for i in range(6) for r in _cohort(
        f"before{i}", result=("passed" if i < 2 else "exhausted"), ssot_version_ran=99)]
    recs += [r for i in range(6) for r in _cohort(
        f"after{i}", result="passed", ssot_version_ran=100)]
    out = _run(tmp_path, ssot_path, recs)
    row = next(e for e in out["did_it_help"] if e["proposal_id"] == "pidW")
    assert row["verdict"] == "helped"


def test_did_it_help_rollback_record_supersedes_prior_adopt(tmp_path, ssot_path):
    # read_adoptions keeps only the LATEST record per proposal_id (dict
    # overwrite) — a later "rollback" line for the same pid must drop it
    # from did_it_help entirely (action != "adopt").
    _write_adoptions(tmp_path, [
        {"proposal_id": "pidV", "action": "adopt", "class": "agentic_build",
         "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
         "date": "2026-08-01"},
        {"proposal_id": "pidV", "action": "rollback", "class": "agentic_build",
         "old_rung": "sonnet@high", "new_rung": "opus@high", "ssot_version": 101,
         "date": "2026-08-05"},
    ])
    recs = [r for i in range(6) for r in _cohort(f"x{i}", result="passed", ssot_version_ran=99)]
    out = _run(tmp_path, ssot_path, recs)
    assert not any(e["proposal_id"] == "pidV" for e in out["did_it_help"])


def test_hand_written_string_ssot_version_does_not_crash_the_run(tmp_path, ssot_path):
    # s07 review finding: adoptions.ndjson is hand-authored and
    # routing-update/SKILL.md gives no type for `ssot_version`, so an operator
    # may write "100" or "v100". The did-it-help boundary is an ORDERED
    # comparison, so a string raised TypeError and killed the WHOLE aggregation
    # run -- every cell, every proposal -- over one unusable record. A string
    # that names a real version must work; one that names nothing must skip
    # that record only.
    _write_adoptions(tmp_path, [
        {"proposal_id": "pidStr", "action": "adopt", "class": "agentic_build",
         "old_rung": "opus@high", "new_rung": "sonnet@high",
         "ssot_version": "v100", "date": "2026-08-10"},
        {"proposal_id": "pidJunk", "action": "adopt", "class": "agentic_build",
         "old_rung": "opus@high", "new_rung": "sonnet@high",
         "ssot_version": "not-a-version", "date": "2026-08-10"},
    ])
    recs = [r for i in range(6) for r in _cohort(f"b{i}", result="passed", ssot_version_ran=99)]
    recs += [r for i in range(6) for r in _cohort(f"a{i}", result="passed", ssot_version_ran=100)]
    out = _run(tmp_path, ssot_path, recs)          # must not raise
    assert out["ok"] is True
    row = next(e for e in out["did_it_help"] if e["proposal_id"] == "pidStr")
    assert row["before"]["n"] == 6                 # "v100" understood as 100
    assert row["after"]["n"] == 6
    # the unusable record is skipped, not fatal, and not silently invented
    assert not [e for e in out["did_it_help"] if e["proposal_id"] == "pidJunk"]


def test_unknown_ssot_version_is_not_counted_as_before(tmp_path, ssot_path):
    # s07 review, round 2: an absent/null ssot_version_ran used to default to 0
    # and therefore land in "before". outcomes._ssot_version_ran() returns None
    # whenever the routing file is unreadable or has no `version:` line — a
    # GLOBAL condition — so a whole period of NEW-routing cohorts would have
    # counted as evidence for the OLD routing. Unknown must be unplaceable.
    _write_adoptions(tmp_path, [{
        "proposal_id": "pidU", "action": "adopt", "class": "agentic_build",
        "old_rung": "opus@high", "new_rung": "sonnet@high", "ssot_version": 100,
        "date": "2026-08-10",
    }])
    recs = [r for i in range(6) for r in _cohort(f"b{i}", result="passed", ssot_version_ran=99)]
    recs += [r for i in range(6) for r in _cohort(f"a{i}", result="passed", ssot_version_ran=100)]
    # six cohorts whose version could not be read at write time
    recs += [r for i in range(6) for r in _cohort(f"u{i}", result="passed", ssot_version_ran=None)]
    out = _run(tmp_path, ssot_path, recs)
    row = next(e for e in out["did_it_help"] if e["proposal_id"] == "pidU")
    assert row["before"]["n"] == 6      # the 6 unknowns must NOT swell this
    assert row["after"]["n"] == 6


def test_value_epoch_range_admits_record_only_bumps():
    """v1.17: a record-only SSOT bump must not zero the proposal pool.

    With value_epoch: 16 and version: 18, records that ran under 16 and 17 are
    admissible; 15 is not. Without the key, only an exact version match is."""
    assert ao.ssot_value_epoch("version: 18\nvalue_epoch: 16\n") == 16
    assert ao.ssot_value_epoch("version: 18\n") is None

    def cohort(ran):
        return {"attempt1": {"routing_provenance": "default_resolved",
                             "ssot_version_ran": ran}}

    assert retro_cohorts.is_default_current_epoch(cohort(16), 18, 16)
    assert retro_cohorts.is_default_current_epoch(cohort(17), 18, 16)
    assert retro_cohorts.is_default_current_epoch(cohort(18), 18, 16)
    assert not retro_cohorts.is_default_current_epoch(cohort(15), 18, 16)
    # no floor -> old exact-match rule
    assert not retro_cohorts.is_default_current_epoch(cohort(16), 18, None)
    assert retro_cohorts.is_default_current_epoch(cohort(18), 18, None)
