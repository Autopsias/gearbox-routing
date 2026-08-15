# Shipping actions (post-session version-control + deploy)

Loaded on demand — read this when `apply` reports a non-null `post_session` for a
session. Registry shapes live in `shipping-registries.md`.

A plan built by `/plan-builder` may declare, per session (or phase), a
`post_session` block: commit / push / open-PR / run pre-deploy gates / deploy.
Plan-declared shipping is **pre-authorized** (example-project's "plan-enumerated
destructive calls are pre-authorized" rule) — you do NOT re-ask. The deterministic
state machine lives in `scripts/shipping.py`; **you invoke the actual skills** (a
plain Python process can't call the Skill tool), exactly like you dispatch Tasks.

`PYBP` means: `python ~/.claude/skills/plan-execute/scripts/run.py`.

## The shipping sub-loop

After `apply` reports a non-null `post_session`:

1. `PYBP ship-begin <dir> --session sNN` (add `--resume` when the operator is resuming past a checkpoint or confirming a stale deploy; `--confirm-stale` to proceed past a `deploy-auth-stale` halt). It returns an `action`:
   - `noop` / `already-shipped` / `skipped` (e.g. `skip-if-partial`, `deploy-target-missing`) — nothing more to do; surface and continue.
   - `deferred` (`reason: checkpoint:…`) — a human checkpoint outranks shipping. Stop; shipping fires on `--resume`.
   - `locked` — another shipping holds a shared resource (`git:`/`push:`/`deploy:`/`gate:`); finish that one, then retry.
   - `failed` — surface `reason` + the **redacted** `stderr_excerpt`; the plan is halted. STOP. The operator fixes the root cause and resumes (shipping resumes at the failed step — it never re-runs the session).
   - `confirm-required` (`reason: deploy-auth-stale`) — an intervening session changed what this deploy ships, so the pre-authorization was downgraded. Surface the `rollback_hint` and the stale deploy, ask the operator, then re-run with `--resume`/`--confirm-stale` to proceed.
   - `invoke-skill` `{skill, args, step}` — invoke that skill via the Skill tool: `Skill(skill="<skill>", args="<args>")` (copy the `/plan-harden` → `/grill-with-docs` invocation pattern). Write the skill's final message to a temp file, then record the outcome: `PYBP ship-record <dir> --session sNN --step <step> --status done|failed [--result-file /tmp/<step>.txt]`. (A failure's stderr is redacted by the helper before it touches `run.ndjson`.)
   - `run-argv` `{step}` — an argv-kind step (a `deploy_argv` / argv registry target or gate). The helper runs it itself: `PYBP ship-run <dir> --session sNN --step <step>`.
   - `done` — finalize: `PYBP ship-finalize <dir> --session sNN`.
2. Each `ship-record` / `ship-run` returns the NEXT directive. Loop until `done` (then `ship-finalize`) or a terminal `failed`/`deferred`/`confirm-required`.

**Announce before you ship.** Before running the sub-loop for a session, print its
declared shipping actions to the terminal stream — e.g. `s03 will run:
git:commit-push, deploy:example-project-ec2 (gate: eval-smoke-baseline)` — so a runaway plan
is visible live, not only in retrospective audit. The `apply` output's `post_session`
block and `PYBP ship-status` give you the exact actions.

**Read-only / preview / CI:** `PYBP ship-status <dir> --session sNN` prints the
computed step plan + current shipping state (no lock, no mutation). `PYBP apply …
--dry-run-shipping` appends a non-executing shipping plan to the apply output.
`/plan-builder … --dry-run-shipping` lists the whole plan's destructive surface before
you ever run it. `PYBP ship-simulate <dir> --session sNN` runs the full pipeline
producing real events + state but auto-succeeding every step (no destructive skill /
real command) — for CI / smoke. `PYBP status <dir>` includes a `shipping` summary
(per-session badge + recent `post_session_*` events) so a silent skip/halt is visible
in the default output.

## Registries (project-local)

Deploy targets and gates resolve through `<project>/.claude/deploy-targets.json` and
`.claude/eval-gates.json` (merged over skill-bundled defaults; project wins). A
target/gate is `skill`-kind (you invoke it) or `argv`-kind (the helper runs it
`shell=False` with an allow-listed env). See `shipping-registries.md`. The single home
for per-skill flags is `scripts/shipping_adapter.py` — a skill changing its interface
is ONE adapter edit + a probe update, not a hunt through `run.py`.

## Safety guarantees (so you don't have to)

- **Checkpoint precedence.** A human checkpoint (dispatch / phase_closer / closeout) ALWAYS outranks shipping — never ship before review.
- **Lock-first, resource-scoped.** Locks (`git:`/`push:`/`deploy:`/`gate:`) are acquired BEFORE the idempotency read (no TOCTOU) and are per-resource so a long deploy doesn't block unrelated commits.
- **Digest-bound idempotency.** Shipping state binds to the manifest + closeout digests. A rebuilt plan or changed closeout → `state-drift` refusal, never a skip-as-already-shipped. Resume restarts at the first unfinished step — no duplicate commit/deploy.
- **Secret redaction.** GitHub/AWS tokens, bearer tokens, and credential-bearing URLs are stripped from any logged `stderr_excerpt`.
- **Durable state.** `_shipping_state/<sid>.json` writes are fsync'd with a `.bak` and validated on read; a corrupt record halts rather than masquerading as valid.
- **Dashboard is source-of-truth.** PLAN.html gains a per-session shipping badge (`committed`/`pushed`/`PR-open`/`deployed`/`SHIP-FAILED@<step>`) driven from the SAME write as the state — the human surface never implies "shipped" when it didn't.

## New `run.ndjson` events

`post_session_started{session_id, declared_steps}` · `post_session_completed{durations_ms}` ·
`post_session_skipped{reason}` · `post_session_deferred{reason}` ·
`post_session_failed{failed_step, stderr_excerpt:REDACTED, resumable}` ·
`shipping_lock_acquired/released{resource}` · `shipping_badge_failed`.

## Shipping recovery

If `_shipping_state/<sid>.json` shows a `failed` step, the plan halted mid-ship. Fix
the root cause (the failed step's `stderr_excerpt` is recorded, redacted),
`--clear-halt`, then re-run — `ship-begin` resumes at the failed step; finished steps
are skipped, so no duplicate commit/deploy fires. Two shipping-specific halt reasons
need operator judgement, not a blind re-run:

- **`state-drift`** — the manifest or closeout changed since the shipping state was
  written (e.g. the plan was rebuilt). The helper refuses rather than skip a
  now-stale `deploy: done`. Decide whether the prior shipping is still valid; if
  so, delete `_shipping_state/<sid>.json` to re-ship from scratch.
- **`deploy-auth-stale` (later-invalidation)** — an intervening session changed
  what this deploy ships. The `rollback_hint` for the prior deploy is surfaced.
  Confirm the deploy is still correct, then re-run with `--resume`/`--confirm-stale`.
  Rollback execution itself stays manual.
