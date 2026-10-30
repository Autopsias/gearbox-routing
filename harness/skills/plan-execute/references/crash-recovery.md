# Crash recovery — verify and shipping

Moved from SKILL.md "Crash recovery". Read it when a session is stuck in `DOING` with a replayed closeout, or the plan halted mid-ship.

**Verify recovery.** A session stuck in `DOING` WITH a replayed `_closeouts/<sid>.json`
AND a `_verify_state/<sid>.json` whose `outcome` is not `passed` is a verify that
was interrupted — re-run `PYBP verify-begin <dir> --session sNN --resume` to
resume the gates (passed gates are not re-run). **`--resume` is REQUIRED here
(RT-04):** calling `verify-begin` on this same open state without it is refused
(`action: "requires-resume"`, exit 2) instead of silently re-advancing — the flag
is the deliberate acknowledgment that this call is a resume, not an accidental
re-invocation. This refusal fires ONLY on re-entering existing open state; a
brand-new verify pass and the documented post-rework-redispatch re-entry (a
FRESH closeout was just applied) both need no flag. A `state-drift` refusal means
the manifest changed since the verify state was written (e.g. a rebuild); decide
whether the prior verification still holds, and delete `_verify_state/<sid>.json`
to re-verify from scratch.

**Shipping recovery.** If `_shipping_state/<sid>.json` shows a `failed` step, the
plan halted mid-ship. Fix the root cause (the failed step's `stderr_excerpt` is
recorded, redacted), `--clear-halt`, then re-run — `ship-begin` resumes at the
failed step; finished steps are skipped, so no duplicate commit/deploy fires. Two
shipping-specific halt reasons need operator judgement, not a blind re-run:

- **`state-drift`** — the manifest or closeout changed since the shipping state was
  written (e.g. the plan was rebuilt). The helper refuses rather than skip a
  now-stale `deploy: done`. Decide whether the prior shipping is still valid; if
  so, delete `_shipping_state/<sid>.json` to re-ship from scratch.
- **`deploy-auth-stale` (later-invalidation)** — an intervening session changed
  what this deploy ships. The `rollback_hint` for the prior deploy is surfaced.
  Confirm the deploy is still correct, then re-run with `--resume`/`--confirm-stale`.
  Rollback execution itself stays manual.
