# Failure Modes & Recovery

Every failure halts the loop loudly rather than corrupting state silently. The
halt flag lives in `run_state.json`; a `HALT_NOTICE.txt` is written at the plan
root, and an event lands in `run.ndjson`.

## Closeout failures (from `apply`)

| `failure` | Cause | What `apply` does | Recovery |
|---|---|---|---|
| `missing` | No `<plan-execute-closeout>` block found (or only fenced examples) | session → BLOCKED, halt set, note "closeout missing" | Inspect the subagent's output. Re-dispatch the session: `/plan-execute <dir> --session sNN` after `--clear-halt`. |
| `json_error` | Block found but JSON won't parse, or trailing text after it | session → BLOCKED, halt set | Same — the subagent emitted a malformed block; re-dispatch. |
| `schema_error` | JSON parses but wrong shape (missing field, bad `result`, non-string ids) | session → BLOCKED, halt set, violations listed | Re-dispatch; if it recurs, the prompt may be mis-teaching the format. |
| `semantic_error` | Hallucinated item id, completed∩blocked overlap, or DONE without full coverage | session → BLOCKED, halt set, violation named | Investigate whether the subagent actually did the work; re-dispatch or hand-correct. |

When a batch member fails, **finish applying the other members' closeouts first**
(so their work is recorded) — then stop. The halt prevents the *next* batch, not
the consumption of in-flight siblings.

## Result-driven outcomes (valid closeout)

| `result` | Effect |
|---|---|
| `DONE` | items_completed → DONE, session → DONE. Loop continues. |
| `PARTIAL` | items_completed → DONE, session → PARTIAL. Loop re-dispatches the session next pass to finish remaining items. |
| `BLOCKED` | items_blocked → BLOCKED, session → BLOCKED, halt set. Loop stops. |
| `human_checkpoint_reason` non-null | session → AWAITS_REVIEW, loop halts; continue with `--resume`. |

## Structural / environmental failures

| Symptom | Source | Recovery |
|---|---|---|
| `manifest/HTML mismatch` | A session in manifest.json has no matching `<!-- ARTICLE:id -->` anchor in PLAN.html (stale manifest or hand-edited HTML) | Rebuild: `/plan-builder --rebuild --preserve-state <slug>`. |
| `expected exactly 1 '<!-- ARTICLE:id:BEGIN -->'` | Anchor count wrong — duplicated/deleted block | The HTML was hand-edited or a prior write corrupted it. Restore from git or rebuild. |
| `block 'id' missing data-status / pill / notes-content` | Anchor preflight: the article structure was damaged | Same as above. |
| `plan_schema_version is None/1` | A v1 (Cowork-era) plan | Rebuild via `/plan-builder --rebuild <spec.json>`, or view read-only in a browser. v1 plans can't auto-execute. |
| lock contention | Another `/plan-execute` is in flight (or a stale `.lock`) | If stale (>1h or dead pid), the helper overwrites it automatically. Otherwise wait, or remove `<dir>/.lock` manually. |
| `refusing to acquire the plan lock … is on <FS class>` | The plan dir resolves onto a networked/sync FS (iCloud, Google Drive / OneDrive / Dropbox via Finder, NFS/SMB) where the pidfile lock is unreliable (P5) | Move the plan to local disk, or re-run `begin` with `--unsafe-lock` to override (logs a `lock_fs_warning` event and proceeds at your own risk). |

## Halt notification (opt-in, P8)

When a halt is set, if `manifest.json` carries `notify_on_halt.command` the halt
path runs it once — `shell=False`, ~10s timeout, env `PLAN_DIR` / `PLAN_TITLE` /
`HALT_SESSION` / `HALT_REASON`. It is best-effort and never blocks the halt:

| ndjson event | Meaning |
|---|---|
| `notify_sent` | Command ran and exited 0. |
| `notify_failed` | Command was missing/unparseable, timed out, raised, or exited non-zero (`returncode`/`stderr`/`error` captured). The halt is still set — only the notification failed. |

No `notify_on_halt` in the manifest → no command runs and neither event is logged.

## Crash recovery (write-ahead log)

The pipeline persists `_closeouts/<sid>.json` (verified) **before** editing
PLAN.html, and marks it `replayed: true` after.

- **Session in `DOING`, no `_closeouts/<sid>.json`:** the subagent crashed or never returned. Re-dispatch with `--session sNN`.
- **`_closeouts/<sid>.json` exists, `replayed: false`, PLAN.html not yet DONE:** a prior run captured the closeout but died before/during the HTML edit. Re-running `apply` for that session is idempotent and completes the mutation. (In practice: re-run the loop; the helper's mutations are safe to repeat.)
- **`replayed: true`:** already applied; nothing to do.

## Shipping failures (post-session actions)

Shipping runs AFTER a closeout is applied (and after any human checkpoint). Every
failure halts; shipping resumes at the failed step — it never re-runs the session
and never rolls back the already-recorded closeout.

| `reason` / `action` | Cause | Recovery |
|---|---|---|
| `failed` (`failed_step` named) | A commit/push/PR/gate/deploy sub-step failed; `stderr_excerpt` is recorded **redacted** | Fix the root cause, `--clear-halt`, re-run. `ship-begin` resumes at the failed step; finished steps are skipped (no duplicate commit/deploy). |
| `skipped` `deploy-target-missing` | The `deploy` target vanished from `.claude/deploy-targets.json` between build and run | Restore the registry entry (or remove the `deploy` from the session) and re-run. |
| `skipped` `skip-if-partial` | `skip_if_partial: true` and the closeout `result` was not `DONE` | Intentional — finish the session (`PARTIAL` → `DONE`) to ship. |
| `failed` `state-drift` | The manifest or closeout digest changed since the shipping state was written (e.g. plan rebuilt) | Decide whether the prior shipping is still valid. If so, delete `_shipping_state/<sid>.json` to re-ship from scratch; otherwise reconcile manually. The helper refuses to skip-as-already-shipped against a stale digest. |
| `confirm-required` `deploy-auth-stale` | An intervening session changed what this deploy ships, so the pre-authorization was downgraded to surface-and-confirm | The `rollback_hint` for the prior deploy is surfaced. Confirm the deploy is still correct, then re-run with `--resume` / `--confirm-stale`. |
| `failed` `adapter-contract-drift` | A skill flag the adapter references no longer exists in the skill's current interface (a plan authored week-1, run week-20 against an evolved skill) | Update `scripts/shipping_adapter.py` (or the skill) so the flag matches, then re-run. |
| `failed` `state-corrupt` | `_shipping_state/<sid>.json` is unreadable and has no usable `.bak` | Inspect / delete the corrupt file and re-run (shipping re-plans from the closeout). |

Shipping lock events (`shipping_lock_acquired` / `shipping_lock_released`,
resource-scoped: `git:`/`push:`/`deploy:`/`gate:`) bracket each session's
shipping. Orphaned locks from a dead run are reclaimed automatically (stale pid).

**CI / smoke without a live orchestrator:** `ship-simulate <dir> --session sNN`
runs the whole pipeline producing real `post_session_*` events + state but
auto-succeeds every step (no real skill invocation / command). Use it to verify
the machinery (events, badge, idempotency) deterministically.

## Clearing a halt

After investigating and fixing the underlying cause:

```
python ~/.claude/skills/plan-execute/scripts/run.py clear-halt <dir>
```

(or `/plan-execute <dir> --clear-halt`). Then resume the loop normally, or
re-dispatch the specific session.

## What never auto-retries

Semantic failures and `result: BLOCKED` are **decisions**, not transient errors.
They never auto-retry — a human decides whether the work was actually done and
whether to re-dispatch. (Transient Task-tool errors — network, timeout, rate
limit — are the only retry candidates, and that backoff is a v1.5 addition.)
