# Code and Technical Review

Apply these lenses in addition to the common review contract. Inspect repository
instructions and use the project's own contracts and tests as the primary basis.

## Behavior and Contracts

- Trace changed inputs through outputs, side effects, and failure paths.
- Check callers, consumers, API schemas, database schemas, events, and public
  interfaces for compatibility.
- Look for behavior that passes local tests but violates an external contract.
- Distinguish an intentional contract change from an accidental regression.

## Data Integrity

- Check transaction boundaries, partial writes, retries, rollback, and recovery.
- Test null, empty, duplicate, malformed, stale, and out-of-order data.
- Inspect migrations for mixed-version operation, backfill safety, and rollback.
- Verify authorization and tenancy filters on every read and write path.

## Concurrency and Idempotency

- Look for race conditions, lost updates, duplicate processing, and unsafe
  check-then-act sequences.
- Check retry behavior, idempotency keys, lock scope, and timeout handling.
- Confirm background jobs tolerate replay, cancellation, and partial completion.

## Security and Trust Boundaries

- Identify every untrusted input and privilege transition.
- Check authentication, authorization, secret handling, injection, path
  traversal, deserialization, and output escaping as applicable.
- Verify logs and errors do not expose sensitive data.
- Do not claim exploitability without a concrete path or reproducible evidence.

## Reliability and Observability

- Inspect exception handling, cleanup, resource limits, and degraded behavior.
- Check whether failures are visible through useful logs, metrics, traces, or
  user-facing errors.
- Look for swallowed errors, infinite retries, ambiguous success, and unsafe
  defaults.

## Performance and Resources

- Evaluate algorithmic growth, query count, memory, network calls, file handles,
  and concurrency limits against realistic workloads.
- Distinguish measured regressions from theoretical concerns.
- Ask for a benchmark or profile when impact depends on scale not established by
  the artifact.

## Dependencies and Compatibility

- Check runtime, language, framework, protocol, and dependency version support.
- Inspect lockfiles and release notes when a version change is material.
- Verify platform assumptions and fallback behavior.

## Test Effectiveness

- Confirm tests exercise the behavior they claim to prove.
- Look for assertions against mocks rather than observable outcomes.
- Check failure paths, boundaries, integration contracts, and regression cases.
- Treat missing tests as a finding only when they leave a concrete material
  behavior unvalidated; otherwise state the gap as residual risk.

## Evidence

Prefer reproducible failures, tests, static analysis results, runtime traces, and
contract citations. Include precise file and line references. Before reporting,
check whether surrounding code, configuration, or documented constraints refute
the candidate.
