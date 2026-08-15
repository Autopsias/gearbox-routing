"""Tests for aggregate_outcomes.py — ADA-01.

Covers every named boundary from the plan: N-1 silence / exact-N fire (upgrade),
two-stage canary gating (smoke abort, adoption evidence), malformed-ratio abort,
malformed-line tolerance below the bound, explicit-tag canary cells kept separate
from class-default cells, cohort construction (a 6-attempt single session is ONE
cohort and can never satisfy N>=6 alone), verified-only denominators (unverified/
blocked/wontfix excluded from every rate), the snapshot/concurrent-append
guarantee, and the "malformed run leaves the prior stamp untouched" contract.

Uses a SYNTHETIC model-routing.yaml fixture (never the live repo file) so class
defaults/prices are pinned and independent of future SSOT edits.

Run: pytest skills/routing-retro/scripts/test_aggregate_outcomes.py -q
"""

import hashlib
import json
from datetime import UTC, datetime

import pytest

import aggregate_outcomes as ao

SSOT_FIXTURE = """version: 99
active_provider: anthropic

prices:
  fable:   { in: 10.00, out: 50.00 }
  opus:    { in:  5.00, out: 25.00 }
  sonnet:  { in:  3.00, out: 15.00 }
  haiku:   { in:  1.00, out:  5.00 }

task_classes:
  mechanical:     { tier: cheap_fast,      effort: light    }
  standard_build: { tier: workhorse,       effort: standard }
  agentic_build:  { tier: frontier_reasoner, effort: thorough }
  deep_reasoning: { tier: frontier_reasoner, effort: standard }
  linchpin:       { tier: frontier_reasoner, effort: thorough }

providers:
  anthropic:
    models:
      cheap_fast:        haiku
      workhorse:         sonnet
      frontier_reasoner: opus
      apex_reasoner:     fable
    effort:
      control: effort
      map:
        cheap_fast:        { light: null, standard: null,   thorough: null }
        workhorse:         { light: low,  standard: medium, thorough: high }
        frontier_reasoner: { light: low,  standard: medium, thorough: high }
        apex_reasoner:     { light: low,  standard: medium, thorough: high }
"""

EPOCH = 99


@pytest.fixture
def ssot_path(tmp_path):
    p = tmp_path / "model-routing.yaml"
    p.write_text(SSOT_FIXTURE)
    return p


def _rid(project, plan, session, generation, attempt, resolution):
    blob = "|".join(str(x) for x in (project, plan, session, generation, attempt, resolution))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _rec(**kw):
    base = dict(
        ts="2026-08-01T00:00:00+00:00",
        project="proj", plan="planA", session="s01", generation=0,
        task_class="agentic_build", backend="claude",
        model_authored="opus", reasoning_authored="high", tier_authored="opus@high",
        model_ran="opus", reasoning_ran="high", tier_ran="opus@high",
        model_ran_source="attested", effort_mechanism="tier_agent",
        degraded_from=None, escalated_from=None, routing_experiment=None,
        routing_provenance="default_resolved", ssot_version_ran=EPOCH,
        attempt=1, rework_count=0, stuck_armed=False, gates_failed=[],
        result="passed", verified=True,
    )
    base.update(kw)
    resolution = base.pop("resolution", "unit")
    base["record_id"] = _rid(base["project"], base["plan"], base["session"],
                              base["generation"], base["attempt"], resolution)
    return base


def _cohort(session, result="passed", attempts=1, **overrides):
    """attempts-1 rework records followed by one terminal record. `overrides`
    always wins over the computed defaults (e.g. an explicit `verified=`)."""
    recs = []
    for i in range(1, attempts):
        kw = {"session": session, "attempt": i, "result": "rework", "verified": False}
        kw.update(overrides)
        recs.append(_rec(**kw))
    kw = {"session": session, "attempt": attempts, "result": result, "verified": (result == "passed")}
    kw.update(overrides)
    recs.append(_rec(**kw))
    return recs


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _run(tmp_path, ssot_path, records, **kw):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, records)
    return ao.run(ledger, ssot_path, **kw)


# --------------------------------------------------------------------------
# Cohort construction
# --------------------------------------------------------------------------
def test_six_attempt_single_session_is_one_cohort_never_satisfies_n(tmp_path, ssot_path):
    recs = _cohort("s01", result="passed", attempts=6)
    out = _run(tmp_path, ssot_path, recs)
    assert out["ok"]
    assert out["cohorts"]["complete"] == 1
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 1          # ONE cohort, not six attempt records
    assert cell["proposal"] is None
    assert cell["status"] == "below_min_n"


def test_incomplete_cohort_missing_attempt1_excluded(tmp_path, ssot_path):
    recs = [_rec(session="s01", attempt=2, result="passed")]
    out = _run(tmp_path, ssot_path, recs)
    assert out["cohorts"]["incomplete"] == 1
    assert out["cohorts"]["complete"] == 0
    assert out["cells"] == []


def test_incomplete_cohort_gap_in_sequence_excluded(tmp_path, ssot_path):
    recs = [
        _rec(session="s01", attempt=1, result="rework", verified=False, resolution="r1"),
        _rec(session="s01", attempt=3, result="passed", resolution="r3"),
    ]
    out = _run(tmp_path, ssot_path, recs)
    assert out["cohorts"]["incomplete"] == 1
    assert out["cohorts"]["complete"] == 0


def test_open_cohort_still_in_rework_excluded_everywhere(tmp_path, ssot_path):
    recs = [
        _rec(session="s01", attempt=1, result="rework", verified=False, resolution="r1"),
        _rec(session="s01", attempt=2, result="rework", verified=False, resolution="r2"),
    ]
    out = _run(tmp_path, ssot_path, recs)
    assert out["cohorts"]["open"] == 1
    assert out["cohorts"]["complete"] == 0
    assert out["cells"] == []


# --------------------------------------------------------------------------
# Verified-only denominators
# --------------------------------------------------------------------------
def test_blocked_done_unverified_wontfix_excluded_from_every_rate(tmp_path, ssot_path):
    recs = []
    for i in range(6):
        recs += _cohort(f"pass{i}", result="passed")
    for i in range(3):
        recs += _cohort(f"blk{i}", result="blocked", verified=False)
    recs += _cohort("s-unv", result="done_unverified", verified=False)
    recs += _cohort("s-wf", result="wontfix", verified=False)
    out = _run(tmp_path, ssot_path, recs)
    assert out["cohorts"]["complete"] == 11
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 6                     # only the passed cohorts
    assert cell["facts"]["first_attempt_pass_rate"] == 1.0
    assert cell["excluded"] == {"blocked": 3, "done_unverified": 1, "wontfix": 1}


# --------------------------------------------------------------------------
# UPGRADE — N-1 silence / exact-N fire
# --------------------------------------------------------------------------
def _failing_cohorts(n, session_prefix="fail"):
    return [r for i in range(n) for r in _cohort(f"{session_prefix}{i}", result="exhausted", verified=False)]


def test_upgrade_silent_at_n_minus_one(tmp_path, ssot_path):
    out = _run(tmp_path, ssot_path, _failing_cohorts(5))
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 5
    assert cell["proposal"] is None
    assert out["proposals"] == []


def test_upgrade_fires_at_exact_n(tmp_path, ssot_path):
    out = _run(tmp_path, ssot_path, _failing_cohorts(6))
    cell = out["cells"][0]
    assert cell["proposal"] is not None
    assert cell["proposal"]["kind"] == "upgrade"
    assert cell["proposal"]["n"] == 6
    assert cell["proposal"]["first_attempt_pass_rate"] == 0.0
    assert out["proposals"][0]["proposal_id"] == cell["proposal"]["proposal_id"]
    assert "/routing-update" in out["markdown"]
    assert cell["proposal"]["proposal_id"] in out["markdown"]


def test_healthy_cell_emits_no_proposal(tmp_path, ssot_path):
    # N=8, pass rate 0.75 (between the upgrade floor 0.60 and downgrade-open 0.90),
    # low escalation — squarely healthy.
    recs = []
    for i in range(6):
        recs += _cohort(f"ok{i}", result="passed")
    for i in range(2):
        recs += _cohort(f"bad{i}", result="exhausted", verified=False)
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8
    assert cell["facts"]["first_attempt_pass_rate"] == 0.75
    assert cell["proposal"] is None
    assert cell["status"] == "healthy"
    assert out["proposals"] == []


# --------------------------------------------------------------------------
# Attestation
# --------------------------------------------------------------------------
def test_attested_share_below_half_reports_attribution_limited(tmp_path, ssot_path):
    recs = []
    for i in range(3):
        recs += _cohort(f"att{i}", result="exhausted", verified=False, model_ran_source="attested")
    for i in range(5):
        recs += _cohort(f"req{i}", result="exhausted", verified=False, model_ran_source="requested")
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8
    assert cell["attested_share"] < 0.5
    assert cell["status"] == "attribution-limited"
    assert cell["proposal"] is None


def test_proposal_computed_only_over_attested_subset(tmp_path, ssot_path):
    # 5 attested cohorts all failing (pass=0) + 3 unattested cohorts all PASSING
    # (which would otherwise mask the failure rate). attested_share = 5/8 >= 0.5.
    recs = []
    for i in range(5):
        recs += _cohort(f"att{i}", result="exhausted", verified=False, model_ran_source="attested")
    for i in range(3):
        recs += _cohort(f"req{i}", result="passed", model_ran_source="requested")
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["attested_pool_n"] == 5
    assert cell["attested_share"] == 0.625            # 5/8 of the default-epoch pool is attested (>= 0.5)
    # N=5 attested, below MIN_N_UPGRADE(6) -> no proposal yet, but note the pool
    # used is the ATTESTED one (5), not all 8 raw cohorts.
    assert cell["proposal"] is None
    assert cell["status"] == "below_min_n"


# --------------------------------------------------------------------------
# Provenance / epoch — facts-only, never proposal input
# --------------------------------------------------------------------------
def test_pinned_override_and_prior_epoch_never_enter_proposal_pool(tmp_path, ssot_path):
    recs = []
    recs += [r for i in range(4) for r in
             _cohort(f"pin{i}", result="passed", routing_provenance="pinned_override")]
    recs += [r for i in range(4) for r in
             _cohort(f"old{i}", result="passed", ssot_version_ran=EPOCH - 1)]
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8            # counted as facts
    assert cell["proposal_pool_n"] == 0        # but NOT proposal-eligible
    assert cell["status"] == "below_min_n"
    assert cell["proposal"] is None


# --------------------------------------------------------------------------
# Explicit-tag canary cells — separate from class-default cells
# --------------------------------------------------------------------------
def test_canary_cohorts_excluded_from_main_cells_and_keyed_by_proposal_id(tmp_path, ssot_path):
    recs = []
    recs += _cohort("main1", result="passed")
    recs += [r for r in _cohort(
        "canary1", result="passed", model_authored="sonnet", reasoning_authored="high",
        routing_provenance="experimental",
        routing_experiment={"kind": "canary", "proposal_id": "pidABC"},
    )]
    out = _run(tmp_path, ssot_path, recs)
    assert len(out["cells"]) == 1                    # canary never merges into class-default cells
    assert out["cells"][0]["model_authored"] == "opus"
    assert len(out["canaries"]) == 1
    assert out["canaries"][0]["proposal_id"] == "pidABC"


# --------------------------------------------------------------------------
# Two-stage canary gating
# --------------------------------------------------------------------------
def _canary(session, result="passed", ts="2026-08-01T00:00:00+00:00", proposal_id="pidX", **kw):
    base = {
        "ts": ts, "model_authored": "sonnet", "reasoning_authored": "high",
        "routing_provenance": "experimental",
        "routing_experiment": {"kind": "canary", "proposal_id": proposal_id},
    }
    base.update(kw)
    return _cohort(session, result=result, **base)


def test_canary_smoke_in_progress_below_three(tmp_path, ssot_path):
    recs = _canary("c1") + _canary("c2")
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["stage"] == "smoke-in-progress"
    assert cn["adoption_ready"] is False


def test_canary_smoke_failure_aborts(tmp_path, ssot_path):
    recs = (_canary("c1", result="passed", ts="2026-08-01T00:00:00+00:00")
            + _canary("c2", result="exhausted", verified=False, ts="2026-08-02T00:00:00+00:00")
            + _canary("c3", result="passed", ts="2026-08-03T00:00:00+00:00"))
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["smoke_failed"] is True
    assert cn["stage"] == "smoke-failed"
    assert cn["adoption_ready"] is False
    assert "SMOKE-FAILED" in cn["stage"].upper().replace("_", "-")


def test_canary_below_ten_never_adopts_even_with_perfect_smoke(tmp_path, ssot_path):
    recs = []
    for i in range(5):
        recs += _canary(f"c{i}", result="passed", ts=f"2026-08-{i+1:02d}T00:00:00+00:00")
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["stats"]["n"] == 5
    assert cn["stage"] == "smoke-passed-awaiting-adoption-evidence"
    assert cn["adoption_ready"] is False
    assert out["proposals"] == []


def test_canary_reaches_adoption_evidence_at_n10(tmp_path, ssot_path):
    recs = []
    for i in range(10):
        recs += _canary(f"c{i}", result="passed", ts=f"2026-08-{i+1:02d}T00:00:00+00:00")
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["stats"]["n"] == 10
    assert cn["stats"]["first_attempt_pass_rate"] == 1.0
    assert cn["stats"]["attempts_per_success"] == 1.0
    assert cn["stage"] == "adoption-ready"
    assert cn["adoption_ready"] is True
    assert out["proposals"] and out["proposals"][0]["proposal_id"] == "pidX"


def test_canary_n10_but_weak_attempts_per_success_not_adoption_ready(tmp_path, ssot_path):
    recs = []
    # 9 clean passes + 1 cohort that needed 3 attempts to pass -> attempts_per_success
    # = (9*1 + 3) / 10 = 1.2 > 1.1
    for i in range(9):
        recs += _canary(f"c{i}", result="passed", ts=f"2026-08-{i+1:02d}T00:00:00+00:00")
    recs += _canary("c9", result="passed", attempts=3, ts="2026-08-10T00:00:00+00:00")
    out = _run(tmp_path, ssot_path, recs)
    cn = out["canaries"][0]
    assert cn["stats"]["n"] == 10
    assert cn["stats"]["attempts_per_success"] > 1.1
    assert cn["stage"] == "adoption-evidence-insufficient"
    assert cn["adoption_ready"] is False


# --------------------------------------------------------------------------
# DOWNGRADE-OPEN (deliberate threshold — see module docstring)
# --------------------------------------------------------------------------
def test_downgrade_open_proposal_fires_and_names_smoke(tmp_path, ssot_path):
    recs = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed")]
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["proposal"]["kind"] == "downgrade"
    assert cell["proposal"]["stage"] == "open"
    assert "SMOKE" in cell["proposal"]["recommendation"]
    assert "routing_experiment" in cell["proposal"]["recommendation"]


def test_downgrade_open_points_to_existing_canary_when_already_running(tmp_path, ssot_path):
    recs = [r for i in range(6) for r in _cohort(f"ok{i}", result="passed")]
    out1 = _run(tmp_path, ssot_path, recs)
    pid = out1["cells"][0]["proposal"]["proposal_id"]
    recs += _canary("c0", result="passed", proposal_id=pid,
                     model_authored="opus", reasoning_authored="high")
    out2 = _run(tmp_path, ssot_path, recs)
    cell = out2["cells"][0]
    assert cell["proposal"]["stage"] == "smoke-in-progress"


# --------------------------------------------------------------------------
# Malformed-line handling
# --------------------------------------------------------------------------
def test_malformed_line_tolerance_below_bound(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    recs = [r for i in range(60) for r in _cohort(f"s{i}", result="passed")]
    lines = [json.dumps(r) for r in recs]
    lines.insert(5, "{not valid json")   # 1 malformed / 61 lines = 1.6% < 2%, count < 3
    ledger.write_text("\n".join(lines) + "\n")
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is True
    assert out["ledger"]["malformed"] == 1


def test_malformed_ratio_abort(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    recs = [r for i in range(5) for r in _cohort(f"s{i}", result="passed")]
    lines = [json.dumps(r) for r in recs]
    lines += ["{bad1", "{bad2"]   # 2 malformed / 7 lines = 28.6% > 2%
    ledger.write_text("\n".join(lines) + "\n")
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is False
    assert out["proposals"] == []
    assert out["cells"] == []


def test_malformed_line_count_abort_even_with_low_ratio(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    recs = [r for i in range(200) for r in _cohort(f"s{i}", result="passed")]
    lines = [json.dumps(r) for r in recs]
    lines += ["{bad1", "{bad2", "{bad3"]   # 3 malformed / 203 lines = 1.5% but count>=3
    ledger.write_text("\n".join(lines) + "\n")
    out = ao.run(ledger, ssot_path)
    assert out["ok"] is False


def test_malformed_abort_leaves_prior_stamp_untouched(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))
    out1 = ao.run(ledger, ssot_path)
    assert out1["ok"] is True
    stamp_before = ao.stamp_path(ledger).read_text()

    with open(ledger, "a", encoding="utf-8") as f:
        f.write("{bad1\n{bad2\n{bad3\n")
    out2 = ao.run(ledger, ssot_path)
    assert out2["ok"] is False
    stamp_after = ao.stamp_path(ledger).read_text()
    assert stamp_before == stamp_after


# --------------------------------------------------------------------------
# Dedup / conflicting duplicates
# --------------------------------------------------------------------------
def test_identical_duplicate_record_id_silently_deduped(tmp_path, ssot_path):
    r = _cohort("s01", result="passed")[0]
    out = _run(tmp_path, ssot_path, [r, dict(r)])
    assert out["ok"] is True
    assert out["ledger"]["records_parsed"] == 1


def test_conflicting_duplicate_record_id_fails(tmp_path, ssot_path):
    r1 = _cohort("s01", result="passed")[0]
    r2 = dict(r1)
    r2["result"] = "blocked"          # same record_id, different payload
    out = _run(tmp_path, ssot_path, [r1, r2])
    assert out["ok"] is False
    assert out["conflict"]["record_id"] == r1["record_id"]


# --------------------------------------------------------------------------
# Snapshot / concurrent-append discipline
# --------------------------------------------------------------------------
def test_concurrent_append_excluded_from_snapshot_boundary(tmp_path):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))
    boundary = ao.snapshot_boundary(ledger)

    # A writer appends MORE data after the boundary was captured.
    with open(ledger, "a", encoding="utf-8") as f:
        for r in _cohort("s02", result="passed"):
            f.write(json.dumps(r) + "\n")

    text = ao.read_prefix(ledger, boundary)
    records, malformed, total = ao.parse_lines(text)
    assert total == 1                 # only the pre-boundary record is visible
    assert malformed == 0


def test_stamp_records_boundary_not_live_file_size(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))
    out = ao.run(ledger, ssot_path)
    stamp = json.loads(ao.stamp_path(ledger).read_text())
    assert stamp["offset"] == out["ledger"]["snapshot_bytes"]
    assert stamp["offset"] == ledger.stat().st_size


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
    recs = _cohort("s01", result="passed", cost_usd=2.0) + _cohort("s02", result="passed", cost_usd=4.0)
    out = _run(tmp_path, ssot_path, recs)
    assert out["cells"][0]["facts"]["cost_per_success"] == 3.0


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
