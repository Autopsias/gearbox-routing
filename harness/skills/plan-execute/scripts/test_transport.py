"""Tests for transport.py — retry classification, backoff, commit-boundary
gating (OR-01, S05 2026-07-03).

Run: pytest plan-execute/scripts/test_transport.py -q
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import transport as tr  # noqa: E402


# --------------------------------------------------------------------------
# classify_error
# --------------------------------------------------------------------------
def test_classify_connection_refused_is_retryable():
    assert tr.classify_error("Error: connect ECONNREFUSED 127.0.0.1:443") == "retryable"


def test_classify_failed_to_open_socket_is_retryable():
    assert tr.classify_error("FailedToOpenSocket: could not reach api.anthropic.com") == "retryable"


def test_classify_529_overloaded_is_retryable():
    assert tr.classify_error("HTTP 529: Overloaded — the API is temporarily overloaded") == "retryable"


def test_classify_timeout_is_retryable():
    assert tr.classify_error("Request timeout after 60000ms") == "retryable"


def test_classify_dns_failure_is_retryable():
    assert tr.classify_error("DNS resolution failed for api.anthropic.com") == "retryable"


def test_classify_mid_response_drop_is_ambiguous_not_retryable():
    text = "connection reset — stream ended unexpectedly mid-response after 400 tokens"
    assert tr.classify_error(text) == "ambiguous"


def test_classify_semantic_refusal_is_semantic():
    assert tr.classify_error("I can't help with that request.") == "semantic"


def test_classify_schema_validation_failure_is_semantic():
    assert tr.classify_error("closeout schema invalid: missing 'result' field") == "semantic"


def test_classify_empty_is_semantic():
    assert tr.classify_error("") == "semantic"
    assert tr.classify_error(None) == "semantic"


def test_classify_generic_error_word_is_not_retryable():
    # "error" / "failed" alone must NOT trigger retryable — only real
    # transport-layer signatures should (avoids masking real bugs).
    assert tr.classify_error("Error: invalid argument 'foo'") == "semantic"
    assert tr.classify_error("Task failed: assertion error in test_foo") == "semantic"


# --------------------------------------------------------------------------
# full_jitter_backoff
# --------------------------------------------------------------------------
def test_backoff_is_bounded_by_cap():
    for attempt in range(1, 10):
        for _ in range(20):
            d = tr.full_jitter_backoff(attempt, rng=lambda: 1.0)
            assert 0 <= d <= tr.CAP_DELAY_SECONDS


def test_backoff_zero_rng_gives_zero_delay():
    assert tr.full_jitter_backoff(1, rng=lambda: 0.0) == 0.0


def test_backoff_grows_with_attempt_at_fixed_rng():
    d1 = tr.full_jitter_backoff(1, rng=lambda: 1.0)
    d2 = tr.full_jitter_backoff(2, rng=lambda: 1.0)
    d3 = tr.full_jitter_backoff(3, rng=lambda: 1.0)
    assert d1 <= d2 <= d3


# --------------------------------------------------------------------------
# decide — the load-bearing gate
# --------------------------------------------------------------------------
def test_decide_retries_first_retryable_failure():
    d = tr.decide("connection refused", attempt=1, elapsed_seconds=0.0, rng=lambda: 0.5)
    assert d.action == "retry"
    assert d.error_class == "retryable"
    assert d.delay_seconds >= 0


def test_decide_surfaces_semantic_failure_immediately():
    d = tr.decide("I refuse to do that.", attempt=1, elapsed_seconds=0.0)
    assert d.action == "surface"
    assert d.error_class == "semantic"


def test_decide_surfaces_ambiguous_mid_response_drop_never_retried():
    d = tr.decide(
        "stream ended unexpectedly mid-response",
        attempt=1,
        elapsed_seconds=0.0,
    )
    assert d.action == "surface"
    assert d.error_class == "ambiguous"


def test_decide_caps_at_max_attempts():
    d = tr.decide("connection refused", attempt=3, elapsed_seconds=1.0, max_attempts=3)
    assert d.action == "surface"
    assert "max_attempts" in d.reason


def test_decide_never_exceeds_three_attempts_even_with_time_left():
    # attempt=4 would only happen if a caller kept retrying past the cap —
    # decide() must still refuse.
    d = tr.decide("timeout", attempt=4, elapsed_seconds=1.0, max_attempts=3)
    assert d.action == "surface"


def test_decide_caps_at_total_time_budget():
    # attempt=1, but 89s already elapsed and next backoff would push past 90s.
    d = tr.decide(
        "connection refused", attempt=1, elapsed_seconds=89.0, rng=lambda: 1.0
    )
    assert d.action == "surface"
    assert "total-time budget" in d.reason


def test_decide_commit_boundary_forces_surface_even_for_retryable_class():
    # THE hardening requirement: a retryable-looking error is STILL surfaced,
    # not retried, once the session crossed its commit boundary.
    d = tr.decide(
        "connection refused",
        attempt=1,
        elapsed_seconds=0.0,
        crossed_commit_boundary=True,
    )
    assert d.action == "surface"
    assert "commit boundary" in d.reason
    # error_class is still computed/reported for transparency, just not acted on.
    assert d.error_class == "retryable"


def test_decide_ambiguous_mid_response_is_non_retryable_by_default_even_pre_commit():
    # Ambiguous == "server may have already committed" — non-retryable by
    # default regardless of whether we THINK the commit boundary was crossed,
    # because the whole point is we don't know.
    d = tr.decide(
        "partial response received then connection dropped mid-response",
        attempt=1,
        elapsed_seconds=0.0,
        crossed_commit_boundary=False,
    )
    assert d.action == "surface"
    assert d.error_class == "ambiguous"


def test_decide_full_three_attempt_sequence_then_surfaces():
    elapsed = 0.0
    attempt = 1
    outcomes = []
    while True:
        d = tr.decide("connection refused", attempt=attempt, elapsed_seconds=elapsed, rng=lambda: 0.1)
        outcomes.append(d.action)
        if d.action == "surface":
            break
        elapsed += d.delay_seconds
        attempt += 1
    assert outcomes == ["retry", "retry", "surface"]
