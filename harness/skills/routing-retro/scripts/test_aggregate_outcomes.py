"""aggregate_outcomes — cohorts, denominators and the proposal thresholds (ADA-01).

Covers: cohort construction (a 6-attempt single session is ONE cohort and can
never satisfy N>=6 alone), verified-only denominators (unverified/blocked/wontfix
excluded from every rate), N-1 silence / exact-N fire on upgrade, attestation,
explicit-tag canary cells kept separate from class-default cells, two-stage
canary gating, downgrade-open, malformed-line tolerance and the malformed-ratio
abort, dedup, and the snapshot/concurrent-append guarantee.

The signal half — apex revisit, cost-per-success, time-to-signal, the --since /
--last filters and did-it-help — is in test_aggregate_signals.py.
Shared builders: retro_test_helpers.py.

Run: pytest skills/routing-retro/scripts/test_aggregate_outcomes.py -q
"""
import json

import aggregate_outcomes as ao
import retro_common
from retro_test_helpers import EPOCH, _canary, _cohort, _rec, _run, _write

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
def test_requested_source_counts_as_evidence(tmp_path, ssot_path):
    # OPERATOR DECISION 2026-08-15: a "requested" record counts. An observed
    # downgrade is already recorded as degraded_from and attributed to what RAN;
    # only SILENT inheritance is unrecorded, and that is rare. Before this, a cell
    # of entirely-requested records reported "attribution-limited" and could never
    # propose anything — which left the whole learning half inert.
    recs = []
    for i in range(3):
        recs += _cohort(f"att{i}", result="passed", model_ran_source="attested")
    for i in range(5):
        recs += _cohort(f"req{i}", result="passed", model_ran_source="requested")
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8
    assert cell["attested_pool_n"] == 8               # all 8 are admissible evidence
    assert cell["attested_share"] == 0.375            # but only 3/8 are PROVEN — still reported
    assert cell["status"] != "attribution-limited"
    assert cell["proposal"] is not None               # N=8 >= 6, pass 1.0 -> downgrade opens


def test_unknown_source_is_still_refused_as_evidence(tmp_path, ssot_path):
    # The line that did NOT move. A record that cannot even name the model that was
    # ASKED for is not evidence of anything; counting it would be exactly the
    # "check that cannot fail" defect this plan spent itself finding.
    recs = [r for i in range(8) for r in _cohort(
        f"unk{i}", result="passed", model_ran_source="unknown")]
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["facts"]["n"] == 8                    # visible as facts
    assert cell["attested_pool_n"] == 0               # but never as proposal evidence
    assert cell["proposal"] is None
    assert cell["status"] == "below_min_n"


def test_proposal_computed_only_over_admissible_subset(tmp_path, ssot_path):
    # 5 admissible cohorts all FAILING + 3 unknown-source cohorts all PASSING.
    # The passing three must not mask the failure rate, because they are not
    # admissible evidence.
    recs = []
    for i in range(5):
        recs += _cohort(f"att{i}", result="exhausted", verified=False, model_ran_source="attested")
    for i in range(3):
        recs += _cohort(f"unk{i}", result="passed", model_ran_source="unknown")
    out = _run(tmp_path, ssot_path, recs)
    cell = out["cells"][0]
    assert cell["attested_pool_n"] == 5
    assert cell["proposal"] is None                   # N=5 < MIN_N_UPGRADE(6)
    assert cell["status"] == "below_min_n"


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
    stamp_before = retro_common.stamp_path(ledger).read_text()

    with open(ledger, "a", encoding="utf-8") as f:
        f.write("{bad1\n{bad2\n{bad3\n")
    out2 = ao.run(ledger, ssot_path)
    assert out2["ok"] is False
    stamp_after = retro_common.stamp_path(ledger).read_text()
    assert stamp_before == stamp_after


def test_stamp_advances_only_after_main_prints(tmp_path, ssot_path, monkeypatch, capsys):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))
    argv = ["aggregate_outcomes.py", str(ledger), "--ssot", str(ssot_path)]

    class Broken:
        def write(self, s):
            raise BrokenPipeError
        flush = write

    monkeypatch.setattr("sys.argv", argv)
    monkeypatch.setattr("sys.stdout", Broken())
    try:
        ao.main()
    except BrokenPipeError:
        pass
    else:
        raise AssertionError("print failure was swallowed")
    assert not retro_common.stamp_path(ledger).exists()
    monkeypatch.undo()
    monkeypatch.setattr("sys.argv", argv)
    try:
        ao.main()
    except SystemExit:
        pass
    assert retro_common.stamp_path(ledger).exists()


def test_failed_cost_aggregation_never_writes_the_stamp(tmp_path, ssot_path, monkeypatch):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))

    def boom(records):
        raise RuntimeError("cost aggregation failed")

    monkeypatch.setattr(ao, "cost_cells", boom)
    try:
        ao.run(ledger, ssot_path)
    except RuntimeError:
        pass
    else:
        raise AssertionError("cost_cells failure was swallowed")
    assert not retro_common.stamp_path(ledger).exists()


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
    records, malformed, total, _sole_trailing = ao.parse_lines(text)
    assert total == 1                 # only the pre-boundary record is visible
    assert malformed == 0


def test_stamp_records_boundary_not_live_file_size(tmp_path, ssot_path):
    ledger = tmp_path / "outcomes.ndjson"
    _write(ledger, _cohort("s01", result="passed"))
    out = ao.run(ledger, ssot_path)
    stamp = json.loads(retro_common.stamp_path(ledger).read_text())
    assert stamp["offset"] == out["ledger"]["snapshot_bytes"]
    assert stamp["offset"] == ledger.stat().st_size


