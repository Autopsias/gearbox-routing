# Fixture redaction rules (ST-03 runner-log path)

The fixtures in this directory are **real captured** self-hosted GitHub Actions
`Worker_*.log` files, trimmed and SANITIZED. The executable source of truth for
the rules is `../sanitize_runner_log.sh`; this file is the human-readable
contract. Any new fixture MUST pass `sanitize_runner_log.sh verify <fixture>`
(also enforced by `../test_runner_log_path.sh` T3).

| Rule | What it redacts | Real → Sanitized |
|------|-----------------|------------------|
| R1 | Absolute home paths | `/Users/<name>/…`, `/home/<name>/…` → `/Users/RUNNER_HOME/…` |
| R2 | Runner IDs / binary versions | `<slug>-runner-<N>` → `RUNNER_<N>`; `bin.X.Y.Z` → `bin.RUNNER_VER` |
| R3 | Tokens / secrets | `ghp_…/ghs_…/ghu_…/ghr_…`, `github_pat_…`, `Bearer <jwt>`, `x-access-token:…` → `REDACTED_TOKEN` |
| R4 | Real repo identity | `acme-org/example-repo`, its `git://…/…​.git` URL → `acme-org/example-repo` |
| R5 | Owner / actor / repo numeric IDs | `repository_owner` value → `acme-org`; `*_owner_id`/`actor_id`/`repository_id` value → `REDACTED_ID` |

## Intentionally RETAINED (needed for correlation, not sensitive)

- The verdict line `Job result after all job steps finish: Succeeded|Failed`.
- `jobDisplayName` (e.g. `ci-ok`, `Performance Regression Check`).
- `head_sha` / `sha` (synthetic-but-stable in the fixture; the parser correlates
  on it but it is not a real secret).
- `run_id`, `run_number`, `jobId` — ephemeral CI run handles, used for run-identity
  correlation. Not secrets.
- Timestamps — used for the after-push freshness gate.

## Fixtures

- `Worker_succeeded.log` — a `Performance Regression Check` job, overall
  **Succeeded**.
- `Worker_failed.log` — a `ci-ok` job (the required gate), overall **Failed**,
  and DELIBERATELY contains a green per-step `"result": "succeeded"` line BEFORE
  the overall `Failed` verdict, so the parser test proves it honors the
  authoritative job-result line and not the misleading per-step JSON.

## Regenerating a fixture from a fresh raw log

```bash
references/ship-tail/sanitize_runner_log.sh redact <raw_Worker.log> <out_fixture.log>
# redact runs verify automatically and refuses to emit a leaky fixture.
```
