# Transport-error auto-retry (OR-01, added 2026-07-03)

Full classifier + commit-boundary gate detail. Read this when a `Task` call or a
Bash/MCP call in the dispatch loop actually errors with a transport-layer signature
(connection refused, 529/overloaded, timeout, dropped connection) — SKILL.md's summary
in "Failure handling" is enough for the non-error path; this file is the worked
procedure for the retry decision itself.

A **second, separate** bounded exception: a `Task` dispatch that fails with a
**transport-layer** error (connection refused, "failed to open socket", a
529/"overloaded", a request timeout, a fresh connection dropped with zero
output) is NOT the same failure class as a semantic refusal or a bad closeout
— the model never got to run. Typing "retry" by hand for these is exactly the
toil `scripts/transport.py` exists to remove (142+ manual retries were the
signal that started this item).

**When a `Task` call or a Bash/MCP call in the dispatch loop errors:**

1. Classify it: `python3 scripts/transport.py` isn't a CLI — import
   `classify_error(error_text)` (or reason about it inline using the same
   signatures: `transport.py`'s docstring lists them) to sort the error into
   `retryable` (connection-class, no response), `ambiguous` (dropped
   **mid-response** — partial output already streamed), or `semantic`
   (the model ran and returned something, including a refusal).
2. Call `transport.decide(error_text, attempt, elapsed_seconds,
   crossed_commit_boundary=<bool>)`. It returns `action: "retry"|"surface"`,
   a `delay_seconds`, and a `reason` — log every call via
   `run_state_io.log_retry_attempt(plan_dir, session_id, decision)` (event
   `transport_retry_decision` in `run.ndjson`).
3. **`action == "retry"`:** sleep `delay_seconds` (full-jitter exponential,
   capped at 3 attempts AND ~90s total), then re-dispatch the SAME session
   with the SAME prompt. Announce it in the stream (*"S04 hit a transport
   error (connection refused) — retrying attempt 2/3 in 1.1s"*) exactly like
   the model-degradation announcement above.
4. **`action == "surface"`:** stop retrying. This is the SAME "surface, don't
   silently swallow" behavior as any other failure — but you now know it was
   a transport problem, not a semantic one, so say so.

**Commit-boundary gate — the load-bearing safety rule.** Auto-retry is ONLY
safe **before** a session's work has committed a side effect. The moment a
session's closeout is `apply`'d (PLAN.html mutated), its `post_session` git
commit lands, an MCP write fires, a notification sends, or it writes outside
its own scratch — call `run_state_io.log_commit_boundary(plan_dir,
session_id)` and pass `crossed_commit_boundary=True` to every subsequent
`decide()` call for that session. `decide()` then forces `action="surface"`
**regardless of error class** — a connection drop after a commit is
ambiguous about whether the server side effect landed, and retrying a
non-idempotent op with no idempotency key can duplicate it. `ambiguous`
(mid-response) errors are non-retryable **by the classifier itself**, for the
same reason, even pre-commit.

**Subagent-death checkpoint preservation.** If a dispatched session dies
mid-flight — the `Task` call errors out with no closeout at all, distinct
from a transport error on the dispatch call itself — do not silently
re-dispatch from a blank prompt. Check for ANY partial state the session left
behind (a `_closeouts/<sid>.json` from `cp.mark_replayed`, files it wrote in
project scratch, its last visible progress note in the transcript) and call
`run_state_io.log_subagent_death_checkpoint(plan_dir, session_id,
checkpoint=<summary>)`. When you re-dispatch, **attach that checkpoint summary
to the re-dispatch prompt** ("a prior attempt at this session got as far as X
before dying — resume from there, don't restart") so a 30-minute work product
isn't thrown away. If the dead session's checkpoint shows it had already
crossed its commit boundary, do NOT blindly re-dispatch — surface it for
human review (same rule as any post-commit transport failure).

This module is deliberately a **pure decision library** — no network calls,
no subprocess, nothing that could itself hang. It doesn't make the `Task`
call (the orchestrator does, in this conversation); it only tells you whether
to retry and logs the decision. See `scripts/transport.py`'s module docstring
for the full retryable/ambiguous/semantic taxonomy and
`scripts/test_transport.py` for the behavior contract.

**epic-dev-conductor uses the same classifier.** Its own dispatch loop
(`epic-dev-conductor/SKILL.md`) applies the identical
retryable/ambiguous/semantic split and commit-boundary gate from this same
`scripts/transport.py` — one source of truth for what counts as a transport
error across both orchestration loops.
