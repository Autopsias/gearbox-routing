---
name: plan-execute
description: The plan already exists — now run it. Use to execute, run, or resume a multi-session plan built by plan-builder (a `_plans/<slug>-<date>/` folder with a PLAN.html dashboard), dispatching each ready session to a subagent until a human-review checkpoint, a blocker, or completion. Trigger on any intent to carry out a finished plan — "run/execute the plan", "kick off the plan in <dir>", "run plan-execute on …", "go work through it automatically", "dispatch/execute the next session", or "continue/resume it" after an AWAITS_REVIEW checkpoint or blocker. Do NOT use to create, build, design, or draft a plan/roadmap/dashboard (that's plan-builder), nor for unrelated run/execute jobs (data ingestion, a morning briefing, resuming a chat).
allowed-tools: [Skill, Task, Workflow, Read, Write, Bash, TaskCreate, TaskUpdate, TaskList, Glob, Grep]
effort: medium  # orchestration is mechanical (the deterministic helper + dispatched subagents do the heavy work; per-session depth comes from the manifest's reasoning directives). Deliberately lower than the session default — delete to inherit. Verified honored in binary 2.1.170.
---

# Plan Execute

Orchestrates execution of a plan built by `/plan-builder`. **You (the main conversation) are the orchestrator.** You own every write to `PLAN.html` through `scripts/run.py`. Subagents do project work and return a structured closeout — they never touch plan files.

`PYBP` below means: `python "$HOME/.claude/skills/plan-execute/scripts/run.py"` (always `$HOME`, never `~` in a variable). `<plan-dir>` accepts the plan directory or its `PLAN.html`, and searches upward from cwd.

## Invocation forms

- `/plan-execute <plan-dir>` — run from the next ready session until checkpoint, blocker, or completion. (Bare `/plan-execute` resolves `_plans_index.md` upward for the `**CANONICAL**` or newest row.)
- `/plan-execute <plan-dir> --auto` — self-drive through verify + rework; still halts on human checkpoints, REPLAN, blockers, and stale-deploy confirms ([references/autonomous-mode.md](references/autonomous-mode.md)).
- `/plan-execute <plan-dir> --session sNN` — dispatch one named session.
- `/plan-execute <plan-dir> --resume` — continue past a **pre-dispatch** `AWAITS_REVIEW` gate (requires `--answer-file` or `--no-answer`) or a cleared halt. Does NOT approve a finished post-session checkpoint (use `ack-checkpoint`).
- `/plan-execute <plan-dir> --status` (or `--status --all`) — read-only state dump (surfaces `HALT_NOTICE.txt` first).
- `/plan-execute <plan-dir> --clear-halt` — clear halt after fixing cause (2nd clear of same session requires `--cost-report`; 3rd review clear takes only `--exit accept|split|escalate`; `--answer-file F` passes owner words to the halted session's next attempt; [references/gates-and-lifecycle.md](references/gates-and-lifecycle.md)).
- `/plan-execute <plan-dir> --unsafe-lock` — override networked/cloud-sync filesystem lock refusal (P5).
- **Lifecycle subcommands (`PYBP <cmd> <plan-dir> ...`):** `ack-checkpoint`, `redispatch`, `resolve-replan`, `add-session`, `amend-session`, `retire-session`, `record-refusal`, `retire-plan` ([references/gates-and-lifecycle.md](references/gates-and-lifecycle.md), [references/plan-mutation.md](references/plan-mutation.md), [references/plan-lifecycle.md](references/plan-lifecycle.md)).

## The dispatch loop

Repeat until `plan` reports `complete`, `land`, `checkpoint`, `replan`, `blocked`, or `halted`:

### 1. Check state

Run `PYBP plan <dir>` (with `--resume` / `--session sNN` as requested). Announce `plan_url` at every checkpoint and completion. Act on `action`:

| Action | What you do |
|---|---|
| `halted` | On a **terminal halt** (including exhausted verification or failed shipping), run one learning-capture pass ([references/learning-capture.md](references/learning-capture.md)), then STOP. Surface `halt.reason` + `halt.by_session`. If owner replies with words for the halted session, write verbatim to `<dir>/_decisions/<sid>.halt-<n>.md` and run `PYBP clear-halt <dir> --answer-file <file>` (add `--decision` if stuck). |
| `complete` | Run one learning-capture pass ([references/learning-capture.md](references/learning-capture.md)), then report and STOP. If any session has `acceptance_review: true`, lead with its per-criterion verdict (`ACHIEVED`/`GAP`/`NEVER CHECKED`) and verify `SPEC.md ## Results` write-back ([references/land-and-finish.md](references/land-and-finish.md)). |
| `land` | Run `PYBP land <dir>` (background + `Monitor` if any gate timeout > 540 s). Stops at `land-awaits-review` (approve with `PYBP land-ack <dir> --note '...'` which also pushes). Handle `land-reserved`/`land-locked` via Monitor retry (max 3), `land-repair` via 4-step repair, `invoke-skill` via `land-record`, `gate-on-box-verification` via `land-verify`. After `landed`, re-run `plan` → returns `finish`. Full protocol: [references/land-and-finish.md](references/land-and-finish.md). |
| `finish` | Run `PYBP finish <dir>` with `run_in_background: true` + `Monitor` (`--no-wait` for one-shot CI check). Handle `finish-parked`, `finish-superseded`, and `finished` reporting, then re-run `plan` → `complete` ([references/land-and-finish.md](references/land-and-finish.md)). |
| `checkpoint` | Session is parked at `AWAITS_REVIEW`. Present `checkpoint_brief` as a decision (not a toll booth; open referenced files in Orca + summarize key points), then STOP. Record owner reply verbatim to `<dir>/_decisions/<sid>.md` and resume with `PYBP plan <dir> --resume --answer-file <file>` ([references/gates-and-lifecycle.md](references/gates-and-lifecycle.md)). |
| `replan` | Present `replan[]` brief verbatim with your recommendation filled in, then STOP. Apply pick (`amend-session`/`retire-session`/`redispatch`), then `PYBP resolve-replan` ([references/gates-and-lifecycle.md](references/gates-and-lifecycle.md)). |
| `blocked` | On `reason: repair-start-pending`, run the command in `message`. Otherwise run one learning-capture pass ([references/learning-capture.md](references/learning-capture.md)), surface the blocker, and STOP. |
| `verify-pending` | Run `verify-begin` → `verify-run`/`verify-record` → `verify-finalize`, then re-run `plan`. |
| `dispatch` | Proceed to step 2 with `batch`. |

Every payload also carries `blocked_items` and `ack_required` (post-session checkpoints: approve with `PYBP ack-checkpoint <dir> --session sNN`, never `--resume`).

### 2. Begin the batch

Run `PYBP begin <dir> --sessions <id...>` (add `--resume` if resuming from a halted plan). Acquires `.lock`, preflights anchors, flips sessions to `DOING`, enforces `executor_policy` + `gitleaks`/`.env*` egress guard fail-closed, and returns per-member `backend`, `prompt_text`, `subagent_type`, `model_arg`, `reasoning`, `effort_mechanism` (`tier_agent` | `agent_definition` | `prompt_directive_advisory`), and `fallback_model`/`fallback_reasoning`. Details: [references/dispatch-and-routing.md](references/dispatch-and-routing.md).

### 3. Dispatch (ONE assistant turn)

First, from the **MAIN CONVERSATION**, arm the compaction brake (`--session "$CLAUDE_CODE_SESSION_ID"` is mandatory):
```bash
[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state hold --plan-dir <dir> --phase batch-<n> || true
```

**Task path** (1-member batch, codex members, or fallback when Workflow is unavailable/excluded; put >1 Task calls in one message):
- `subagent_type` = `member.subagent_type` (`tier-*`, `session-effort-worker`, or `"fork"`; omit only when `null`).
- `prompt` = `member.prompt_text` verbatim (never add/strip thinking lines).
- `description` = `member.dispatch_description` verbatim when non-null, else concise session label.
- `model` = the member's `model_arg` when it is non-null (normalized token: `sonnet`, `opus`, `haiku`, `fable`). **Honor it.** **When `model_arg` IS `null` and `subagent_type` is GENERIC — omitted, `general-purpose`, `Explore`, or `Plan` (never a `tier-*` agent, whose own frontmatter already pins a model, and never the literal `"fork"`, which always runs the orchestrator's model by design) — omitting `model` is ILLEGAL (RT-02).** Pass the SSOT's `fanout_policy.default_model` (`~/.claude/model-routing.yaml`; use `mechanical_shard_model` for a `mechanical` shard) as `model`. Never override an explicitly pinned `model_arg`, a `tier-*` agent, or a fork.
- **Announce name-first:** *"Dispatching '<title>' (sNN) on <model> · reasoning: <reasoning>"*. Use name-first everywhere the user reads a session reference.
- <!-- routing-ssot: vN (stamp with your own SSOT's revision) -->
  **Codex-backed members (`backend: "codex"`):** dispatch the returned two-layer wrapper `prompt_text` with `model` = `model_arg`; on `CODEX-DISPATCH-FAILED` re-dispatch once on `fallback_prompt_text` and set `degraded_from` (`quota_exhausted` or null fallback → STOP, never run on Claude). See [references/dispatch-and-routing.md](references/dispatch-and-routing.md).
- <!-- fallback-ladder-source: ~/.claude/model-routing.yaml providers.<name>.degrade (provider-scoped; see the plan-builder routing-ssot stamp) -->
  **Reactive model degradation:** on a real model access/entitlement error, re-dispatch on `fallback_model` @ `fallback_reasoning` (Claude ladder Fable → Opus → Sonnet @ `high`, never `xhigh`; GLM keeps `max`) and record `degraded_from`. **Precedence:** an escalated member carrying `on_dispatch_refusal` is NOT degraded — run `record-refusal` instead.
- **Sandbox (H8) & isolation:** subagents must not edit `<plan-dir>/`. For `isolation: "worktree"` members, dispatch normally — **NEVER pass the Agent/Task tool's own `isolation: "worktree"` parameter** (barred by name).

**Workflow path — DEFAULT for ≥ 2 `backend: "claude"` members** (when Workflow is available and no member invokes skills mid-session): run `parallel()` with one `agent()` each: `agent(prompt_text, {label: <id>, model: <model_arg, or — when it is null AND agentType is GENERIC (omitted / `general-purpose` / `Explore` / `Plan`) — the SSOT's `fanout_policy.default_model` (RT-02; never omit `model` on that combination, and never override an explicitly pinned `model_arg`, a `tier-*` agentType, or `"fork"`)>, agentType: <subagent_type — omit when null>, schema: <closeout schema>})`. Wrap each validated JSON in `<plan-execute-closeout>` for `apply`. Human gates and `run.py` stay in the main conversation. Details: [references/dispatch-and-routing.md](references/dispatch-and-routing.md).

### 4. Record receipt and apply closeout

Wait for the final message (ignore "still running background work" notices). Write the final message to `/tmp/closeout-sNN.txt`, then run:
```bash
PYBP record-receipt <dir> --session sNN --agent-id <id> [--transcript <path>] [--backend claude|codex]
PYBP apply <dir> --session sNN --output-file /tmp/closeout-sNN.txt
```
If `apply` prints `"applied": false`, finish applying any sibling closeouts in the batch, then surface `reason` and STOP. Transcript paths and telemetry details: [references/dispatch-and-routing.md](references/dispatch-and-routing.md).
- **Completion checks (every session, whether or not `verify_pending` is true):** `apply` (and `verify-finalize`) enforces the structural DONE-gate (`PS-01`). **Eval-deliverable check (`PS-02`):** every eval/benchmark/audit session must ship both one rendered one-pager (PNG/PDF/Artifact, data inline) and a decision card of at most three options — if its closeout does not obviously carry both, ask for them before treating the session as done. See [references/gates-and-lifecycle.md](references/gates-and-lifecycle.md).

### 5. Verify (if `verify_pending: true`)

Precedence after closeout: **human checkpoint ▸ verify ▸ shipping**. Run `PYBP verify-begin <dir> --session sNN`, execute `verify-run` / `Skill`+`verify-record` (fix rounds run review gates first; long gates >540 s run in background + `Monitor`; `QG-01` re-runs a failed test file once alone; `RV-02` prints surface size on first review), then `PYBP verify-finalize <dir> --session sNN`.
- On `rework`: re-dispatch through `begin` with `feedback_file` appended (v6+ climbs the SSOT ladder on repeated same-root-cause failure; >5 min since last turn uses a fresh agent; record dispatch refusals with `record-refusal`).
- On `indeterminate`: do NOT re-dispatch; fix cause and run `verify-begin … --resume`.
- Full sub-loop, review-gate intent/scoping/2-pass rules, and escalation contract: [references/gates-and-lifecycle.md](references/gates-and-lifecycle.md) and [references/verify-gates.md](references/verify-gates.md).

### 6. Ship (if `post_session` is non-null)

Plan-declared shipping is pre-authorized (never re-ask). Verify and human checkpoints outrank shipping. Read [references/shipping-actions.md](references/shipping-actions.md) for the `ship-begin` → `ship-record`/`ship-run` → `ship-finalize` sub-loop.

### 7. Release and loop

Release the compaction brake from the MAIN CONVERSATION (`--state ok` with the same `batch-<n>`), then run `PYBP release <dir>` and return to step 1:
```bash
[ -f ~/.claude/hooks/compact-policy.py ] && python3 ~/.claude/hooks/compact-policy.py safe-point --session "$CLAUDE_CODE_SESSION_ID" --state ok --plan-dir <dir> --phase batch-<n> || true
```

## Key contracts (read before touching)

- **Route at dispatch (v8):** `begin` resolves unpinned v8 sessions from `model-routing.yaml`, enforces `verify.locked` hashes, and records transcript usage (`--no-route-at-dispatch` to turn off). See [references/dispatch-and-routing.md](references/dispatch-and-routing.md) and [references/route-at-dispatch-contract.md](references/route-at-dispatch-contract.md).
- **Worktree-isolated parallel groups:** managed structurally by `begin`/`apply`/`worktree-cleanup`; integration merges producer-first and runs the union of gates. See [references/land-and-finish.md](references/land-and-finish.md) and [references/parallel-group-contract.md](references/parallel-group-contract.md).
- **Plan-level git isolation (v7+) & deploy seam:** own branch + worktree through `land`/`finish`. **Never run `gearbox deploy` mid-plan** while executing a plan that edits `skills/plan-execute/**`. See [references/land-and-finish.md](references/land-and-finish.md) and [references/plan-isolation-contract.md](references/plan-isolation-contract.md).
- **Transport-error auto-retry (OR-01):** up to 3× full-jitter backoff (~90 s cap) ONLY for pre-commit-boundary transport errors (`scripts/transport.py`). See [references/transport-retry.md](references/transport-retry.md).
- **Crash recovery & Stop hook:** `DOING` with no closeout → re-run `--session sNN`; `replayed: false` → re-run `apply`; interrupted verify/ship → [references/crash-recovery.md](references/crash-recovery.md). `hooks/turnend-guard.py` blocks ending a turn with orphaned `DOING` sessions.
- **Learning capture (loop end):** Run ONE orchestrator learning-capture pass ([references/learning-capture.md](references/learning-capture.md)) on `complete` AND on any **terminal halt** (`blocked`/`halted`, including exhausted verification or a failed shipping step). Zero qualifying learnings ⇒ write nothing.
- **Preconditions:** schema version ≥ 2 (`v1` refused); run from inside the project; never invoke `/plan-execute` from inside a dispatched subagent.

## Status semantics & failure handling

| Status | Meaning |
|---|---|
| `TODO` | Not yet dispatched |
| `DOING` | Subagent in-flight, or verify gates running |
| `DONE` | Fully complete (passed verify + structural DONE-gate) |
| `PARTIAL` | Some items done, or verify rework pending |
| `AWAITS_REVIEW` | Human checkpoint — pre-dispatch (`--resume`) or post-session (`ack-checkpoint`) |
| `BLOCKED` | Halts loop; needs investigation (no auto-retry) |
| `DEFERRED` / `WONTFIX` | Terminal, skipped by loop |

| Failure symptom | `apply` / `verify` action | Orchestrator action |
|---|---|---|
| Missing / malformed / invalid closeout | `BLOCKED` + halt | Surface diagnostics; apply siblings; STOP |
| `result: BLOCKED` | `BLOCKED` + halt | Surface reason (`decision_brief`); STOP |
| `human_checkpoint_reason` | `AWAITS_REVIEW` (after verify) | Present decision; approve with `ack-checkpoint` |
| `plan_impact` (v3+) | Halt (`kind: replan`) | Present brief + recommendation; mutate; `resolve-replan` |
| Verify failed (budget left) | `PARTIAL` + `feedback_file` | Re-dispatch via `begin` (auto in `--auto`) |
| Verify failed (exhausted / `halt`) | `BLOCKED` + halt | Surface gate + `feedback_file`; STOP |

## References

- [references/dispatch-and-routing.md](references/dispatch-and-routing.md) — `executor_policy`, egress guard, effort enforcement, safe-point holds, Codex wrapper, reactive degradation, GLM, Workflow, `record-receipt`, v8 routing.
- [references/land-and-finish.md](references/land-and-finish.md) — `land`, `land-repair`, `finish`, long gates, `acceptance_review` reporting, worktree parallel groups, plan isolation & deploy seam.
- [references/gates-and-lifecycle.md](references/gates-and-lifecycle.md) — verify sub-loop, review-gate rules, upward escalation, PS-01/PS-02, checkpoints, `redispatch`, mutations, REPLAN, `clear-halt`, locking, notifications.
- [references/closeout-contract.md](references/closeout-contract.md) — closeout JSON shape with worked examples.
- [references/verify-gates.md](references/verify-gates.md) — full verification-gate contract, LLM/cross-family review gates, stop rules.
- [references/shipping-actions.md](references/shipping-actions.md) & [references/shipping-registries.md](references/shipping-registries.md) — `post_session` sub-loop and deploy/eval registries.
- [references/autonomous-mode.md](references/autonomous-mode.md) & [references/autonomous-execution-primitives.md](references/autonomous-execution-primitives.md) — `--auto` semantics and Claude Code loop primitives.
- [references/plan-isolation-contract.md](references/plan-isolation-contract.md) & [references/plan-lifecycle.md](references/plan-lifecycle.md) — whole-plan isolation contract and registry commands.
- [references/parallel-group-contract.md](references/parallel-group-contract.md) — frozen `parallel_group` isolation contract (v2).
- [references/route-at-dispatch-contract.md](references/route-at-dispatch-contract.md) — schema v8 route-at-dispatch contract.
- [references/dual-harness-contract.md](references/dual-harness-contract.md) — Claude Code + Codex CLI dual-harness ADR.
- [references/plan-mutation.md](references/plan-mutation.md) — `add-session` / `amend-session` / `retire-session` rules.
- [references/failure-modes.md](references/failure-modes.md), [references/crash-recovery.md](references/crash-recovery.md), [references/transport-retry.md](references/transport-retry.md), [references/learning-capture.md](references/learning-capture.md).
