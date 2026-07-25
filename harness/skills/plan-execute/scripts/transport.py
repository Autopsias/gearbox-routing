"""Transport-error classification + bounded retry for orchestration dispatch
loops (OR-01, S05 2026-07-03).

Companion to the `_FALLBACK_LADDER` model-degradation pattern in `run.py`:
that ladder answers "the model was refused, what do we substitute"; this
module answers "the API connection dropped, do we retry or surface it". Same
philosophy — bounded, logged, never silent — different failure axis.

## Scope

This module is a pure decision-making library — no network calls, no
subprocess, no Task-tool invocation (this script can't make one; the
orchestrator's Task calls happen in the parent conversation, not here). The
orchestrator (`plan-execute` SKILL.md's dispatch loop, and the
`epic-dev-conductor` dispatch loop) calls `classify_error` + `decide` between
attempts and logs every attempt via `run_state_io.log_event`.

## Retryable vs semantic

`classify_error` sorts a transport/tool-error string into one of three
buckets:

- "retryable"  — connection-class failure BEFORE any response was produced.
  ConnectionRefused, "failed to open socket", DNS/network errors, plain
  request timeouts, HTTP 529/503 ("overloaded"), or a fresh/idle connection
  dropped with zero partial output.
- "ambiguous"  — a connection dropped MID-RESPONSE (partial output already
  streamed, or the drop is described as happening after generation started).
  The server may have already committed a side effect before the drop; this
  is NOT auto-retried by default (see `decide`) — surfaced with the partial
  checkpoint instead. Never assume idempotency you don't have evidence for.
- "semantic"   — anything else: the model ran and returned a real answer
  (including a refusal, an error the model reported, a schema/validation
  failure, a bad closeout). Retrying a semantic failure just reproduces it
  (or masks a real bug); current behavior (surface, no auto-retry) is
  unchanged.

## Commit-boundary gate (hardening, grill Phase 1)

Auto-retry is ONLY safe before any external side effect is committed. Once a
dispatched session's work has crossed a commit boundary — its closeout was
`apply`'d (PLAN.html mutated), a `post_session` git commit ran, an MCP write
landed, a notification fired, or it wrote outside its own scratch — a
subsequent transport failure must be surfaced with the last checkpoint, never
silently retried (a retry after commit risks a duplicate side effect with no
idempotency key). `decide()` takes `crossed_commit_boundary` and forces
`action="surface"` whenever it's true, regardless of error class.

## Backoff budget

Full-jitter exponential backoff (AWS-style: `random(0, min(cap, base * 2**attempt))`),
capped at BOTH `MAX_ATTEMPTS` (3) attempts AND `MAX_TOTAL_SECONDS` (~90s)
total elapsed — whichever is hit first. `decide()` is deterministic given a
`rng` (defaults to `random.random`, injectable for tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from random import random

MAX_ATTEMPTS = 3
MAX_TOTAL_SECONDS = 90.0
BASE_DELAY_SECONDS = 2.0
CAP_DELAY_SECONDS = 30.0

# Connection-class signatures — matched case-insensitively against the raw
# error/tool-result text. Deliberately conservative: false negatives (an
# actually-retryable error classified "semantic") just mean one manual
# "retry" typed by the human, same as today. False positives (a semantic
# failure classified "retryable") would silently mask a real bug — worse —
# so every pattern here is a well-known *transport*-layer signature, not a
# generic "error" or "failed" catch-all.
_RETRYABLE_PATTERNS = [
    r"\bconnection\s*refused\b",
    r"\bfailed\s*to\s*open\s*socket\b",
    r"\bfailedtoopensocket\b",
    r"\beconnreset\b",
    r"\beconnrefused\b",
    r"\bepipe\b",
    r"\betimedout\b",
    r"\bconnection reset\b",
    r"\bconnection (?:closed|dropped|aborted)\b(?!.*\bmid-?response\b)",
    r"\bsocket hang up\b",
    r"\bnetwork error\b",
    r"\bdns (?:lookup|resolution) (?:failed|error)\b",
    r"\b(?:request )?timeout\b",
    r"\b529\b",
    r"\boverloaded\b",
    r"\bservice unavailable\b",
    r"\b503\b",
    r"\btls handshake\b",
    r"\bstream error\b.*\bconnect\b",
]

# Mid-response signatures — a superset check applied FIRST: if the text says
# the drop happened after output had already started streaming, it's
# "ambiguous" (side effect may already be in flight) even though the
# underlying transport signature (e.g. "connection reset") also matches
# _RETRYABLE_PATTERNS.
_MID_RESPONSE_PATTERNS = [
    r"\bmid-?response\b",
    r"\bpartial (?:response|output|completion)\b",
    r"\bdropped after (?:streaming|generating|output)\b",
    r"\bstream (?:ended|closed) (?:unexpectedly )?(?:mid|during|while)\b",
    r"\bincomplete response\b.*\b(?:already|had) (?:started|begun|streamed)\b",
]

_RETRYABLE_RE = re.compile("|".join(_RETRYABLE_PATTERNS), re.IGNORECASE)
_MID_RESPONSE_RE = re.compile("|".join(_MID_RESPONSE_PATTERNS), re.IGNORECASE)


def classify_error(error_text):
    """Classify an error/tool-result string as 'retryable' | 'ambiguous' |
    'semantic'. Empty/None input classifies 'semantic' (nothing to retry)."""
    text = (error_text or "").strip()
    if not text:
        return "semantic"
    if _MID_RESPONSE_RE.search(text):
        return "ambiguous"
    if _RETRYABLE_RE.search(text):
        return "retryable"
    return "semantic"


def full_jitter_backoff(attempt, base=BASE_DELAY_SECONDS, cap=CAP_DELAY_SECONDS, rng=random):
    """AWS full-jitter exponential backoff for the Nth retry attempt
    (attempt=1 is the delay before the FIRST retry, i.e. after the initial
    failure). Returns seconds, 0 <= delay <= cap."""
    if attempt < 1:
        attempt = 1
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return rng() * ceiling


@dataclass
class RetryDecision:
    action: str  # "retry" | "surface"
    reason: str
    delay_seconds: float = 0.0
    error_class: str = "semantic"
    attempt: int = 0


def decide(
    error_text,
    attempt,
    elapsed_seconds,
    crossed_commit_boundary=False,
    max_attempts=MAX_ATTEMPTS,
    max_total_seconds=MAX_TOTAL_SECONDS,
    rng=random,
):
    """Decide whether to retry a failed dispatch attempt.

    `attempt` is the attempt number that JUST failed (1 = the first try).
    `elapsed_seconds` is total wall-clock time spent on this session's
    dispatch attempts so far (across all prior attempts), used against the
    ~90s total-time budget.

    Returns a RetryDecision. `action == "surface"` means: stop, log the
    failure, attach the last checkpoint (if any) to the human/re-dispatch
    path — never silently swallowed.
    """
    error_class = classify_error(error_text)

    if crossed_commit_boundary:
        return RetryDecision(
            action="surface",
            reason="commit boundary already crossed for this session — a retry "
            "here could duplicate a side effect with no idempotency key; "
            "surfacing with the last checkpoint instead of auto-retrying",
            error_class=error_class,
            attempt=attempt,
        )

    if error_class != "retryable":
        return RetryDecision(
            action="surface",
            reason=f"error classified '{error_class}' — not a transport-layer "
            "failure (or ambiguous mid-response drop); auto-retry would "
            "reproduce or mask the real failure, not fix a dropped connection",
            error_class=error_class,
            attempt=attempt,
        )

    if attempt >= max_attempts:
        return RetryDecision(
            action="surface",
            reason=f"attempt {attempt} reached max_attempts={max_attempts}",
            error_class=error_class,
            attempt=attempt,
        )

    delay = full_jitter_backoff(attempt, rng=rng)
    if elapsed_seconds + delay >= max_total_seconds:
        return RetryDecision(
            action="surface",
            reason=f"elapsed {elapsed_seconds:.1f}s + next delay {delay:.1f}s would "
            f"exceed the {max_total_seconds:.0f}s total-time budget",
            error_class=error_class,
            attempt=attempt,
        )

    return RetryDecision(
        action="retry",
        reason=f"retryable transport error, attempt {attempt}/{max_attempts}, "
        f"backing off {delay:.1f}s",
        delay_seconds=delay,
        error_class=error_class,
        attempt=attempt,
    )
