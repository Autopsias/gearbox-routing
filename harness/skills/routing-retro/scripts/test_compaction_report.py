"""Self-checks for compaction_report.py's assembly — the type-table pair,
the prediction flag, and the verdict line they reach.

Split out of test_compaction_retro.py when that file crossed its size bound;
the fixture builders both files share live in retro_test_helpers.py.

Run: pytest skills/routing-retro/scripts/test_compaction_report.py -q
"""

import compaction_report as crep
import compaction_retro as cr
from retro_test_helpers import (
    act_row as _act,
    dec_row as _dec,
    state_root as _root,
    hb_row as _hb,
    scan_of as _scan,
    sess_row as _sess,
)

T = "2026-08-22T12:00:00Z"


def test_prediction_met_is_a_flag_beside_the_verdict_never_inside_it(tmp_path):
    """Tri-state: True/False only when a forecast and a realized number were
    both measurable; None when no comparison was possible. An empty scan has
    nothing measurable, so False here would assert a missed forecast that was
    never tested."""
    root = _root(tmp_path, activations=[_act("hooks", T)])
    block = crep.build(_scan([]), root=str(root))
    assert block["prediction"]["prediction_met"] is None
    assert block["interventions"]["hooks"]["verdict"] in cr.VERDICTS


def test_prediction_met_false_means_the_forecast_missed_and_nothing_else():
    """`prediction_met=False` must be a verdict ON THE FORECAST. When either
    side is missing there was no comparison: the flag is None and the printed
    line says n/a — a shipped page reading `realized NOT MEASURABLE ...
    prediction_met=False` asserted a failed prediction where the honest
    statement is 'not measurable'."""
    # No comparison possible: realized side not measurable (underpowered).
    p = crep._prediction(300000, "underpowered",
                         {"n_sessions_observed": 1}, {"n_sessions_observed": 2})
    assert p["prediction_met"] is None
    line = crep._verdict_line({}, p, {"blind": False}, {"triggered": False})
    assert "prediction_met=n/a" in line
    assert "prediction_met=False" not in line
    # A real comparison that missed still prints False.
    p2 = crep._prediction(300000, "helped",
                          {"cost_usd_mean": 2.0, "n_sessions_observed": 5},
                          {"cost_usd_mean": 1.9, "n_sessions_observed": 5})
    assert p2["prediction_met"] is False    # realized 5.0% < predicted 22.0%
    line2 = crep._verdict_line({}, p2, {"blind": False}, {"triggered": False})
    assert "prediction_met=False" in line2


def test_a_window_with_no_projection_never_prints_none_percent():
    """PREDICTION holds keys for 250k and 300k only; any other window rendered
    the literal `predicted None%`, bypassing the n/a handling."""
    before = {"cost_usd_mean": 2.0, "n_sessions_observed": 10}
    after = {"cost_usd_mean": 1.0, "n_sessions_observed": 10}
    for window in (200000, None):
        p = crep._prediction(window, "helped", before, after)
        assert "None" not in p["line"], p["line"]
        # No projection exists for this window, so no comparison was possible —
        # None, not a False that would read as a missed forecast.
        assert p["prediction_met"] is None


def test_p50_ctx_after_compaction_reaches_the_type_table(tmp_path):
    """MOVE 1 mandates a ctx_after stat — the one number that says whether a
    compaction actually shrank context. It was collected and read by nothing.
    (The rows carry ctx_before too: the stat is computed over PAIRED rows only —
    see test_ctx_shrink_pair_shares_one_population.)"""
    decisions = [_dec("s1", "2026-08-22T12:30:00Z", event="compacted",
                      reason="trigger:auto", decision=None,
                      ctx_before=55000, ctx_after=50000),
                 _dec("s1", "2026-08-22T12:31:00Z", event="compacted",
                      reason="trigger:auto", decision=None,
                      ctx_before=75000, ctx_after=70000)]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    assert block["type_table"][0]["p50_ctx_after_compaction"] == 70000


def test_ctx_shrink_pair_shares_one_population(tmp_path):
    """p50-at and mean-after are COMPARED on the page, so both must be computed
    over the SAME rows — the ones carrying ctx_before AND ctx_after. Building
    each from whichever rows carried its own field compared different sets of
    compactions: on live data 4 manual after-only rows dragged mean-after ~31k
    above p50-at and reversed the sign of the shrink answer. One-sided rows are
    excluded from both stats and counted, never silently absorbed."""
    decisions = [
        _dec("s1", "2026-08-22T12:30:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=250000, ctx_after=200000),   # the one pair
        _dec("s1", "2026-08-22T12:31:00Z", event="compacted", reason="trigger:manual",
             decision=None, ctx_after=400000),                      # after, no before
        _dec("s1", "2026-08-22T12:32:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=999999),                     # before, no after
    ]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    row = block["type_table"][0]
    assert row["p50_ctx_at_compaction"] == 250000       # not the unpaired 999999
    assert row["p50_ctx_after_compaction"] == 200000   # not (200000+400000)/2
    assert row["n_ctx_pairs"] == 1
    assert row["ctx_one_sided_records"] == 2


def test_ctx_shrink_pair_is_one_statistic(tmp_path):
    """The two columns are COMPARED, so both must be the same statistic. A
    median before against a mean after reverses the sign on a skewed sample:
    every pair here shrank, yet median-vs-mean renders 100000 -> 326667."""
    decisions = [
        _dec("s1", "2026-08-22T12:30:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=100000, ctx_after=90000),
        _dec("s1", "2026-08-22T12:31:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=100000, ctx_after=90000),
        _dec("s1", "2026-08-22T12:32:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=900000, ctx_after=800000),
    ]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    row = block["type_table"][0]
    assert row["p50_ctx_at_compaction"] == 100000
    assert row["p50_ctx_after_compaction"] == 90000     # not the mean 326667
    assert row["p50_ctx_after_compaction"] < row["p50_ctx_at_compaction"]


def test_missing_hooks_activation_is_a_blind_spot_not_health(tmp_path):
    """Three of the blindness counters can only be computed against the hooks
    activation timestamp. With no activation record they read 0, and the page
    printed 'Instrument healthy: every blindness counter is zero' over the very
    input that reports blind=True once the activation is present."""
    scan = _scan([_sess("s1", "2026-08-22T12:30:00Z", "2026-08-22T12:40:00Z")])

    with_act = crep.build(scan, root=str(_root(
        tmp_path / "a", activations=[_act("hooks", T)])))["blindness"]
    without = crep.build(scan, root=str(_root(
        tmp_path / "b", activations=[])))["blindness"]

    assert with_act["blind"] is True
    assert with_act["sessions_with_no_ledger_lines"] == 1
    # The same input, minus a readable activation, must not read as healthy.
    assert without["sessions_with_no_ledger_lines"] == 0   # genuinely not measurable
    assert without["sessions_unclassifiable_no_activation"] == 1
    assert without["blind"] is True


def test_a_deferred_compaction_pairs_with_its_measured_row(tmp_path):
    """THE SHAPE THE WRITER NOW EMITS. One compaction takes TWO
    ledger rows: `compacted` with ctx_after=None + ctx_after_deferred=True, and
    a later `compaction-measured` carrying the real number — because the
    post-compaction context cannot be read inside the PostCompact hook at all.

    A reader that took pairs off `compacted` rows alone would report every
    compaction as one-sided forever, and the shrink question would go
    permanently unanswerable. A reader that counted the measured row as a
    compaction record would double every compaction count on the page."""
    decisions = [
        _dec("s1", "2026-08-22T12:30:00Z", event="compacted", reason="trigger:auto",
             decision=None, ctx_before=260000, ctx_after=None, ctx_after_deferred=True),
        _dec("s1", "2026-08-22T12:32:00Z", event="compaction-measured",
             reason="read_after_boundary", decision=None,
             ctx_before=260000, ctx_after=87000),
    ]
    root = _root(tmp_path, activations=[_act("hooks", T)], decisions=decisions,
                 sessions=[_hb("s1", "2026-08-22T12:30:00Z")])
    block = crep.build(_scan([_sess("s1", "2026-08-22T12:30:00Z",
                                    "2026-08-22T12:40:00Z")]), root=str(root))
    row = block["type_table"][0]
    assert row["n_ctx_pairs"] == 1, "the deferred row and its measurement are ONE pair"
    assert row["p50_ctx_at_compaction"] == 260000
    assert row["p50_ctx_after_compaction"] == 87000
    assert row["ctx_one_sided_records"] == 0, (
        "a row still awaiting its measurement is not a one-sided record"
    )
    assert row["compactions"] == 1, (
        "the measured row is the SECOND record of ONE compaction, never a "
        "second compaction"
    )
