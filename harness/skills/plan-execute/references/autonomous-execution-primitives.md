# Claude Code loop primitives — verified contracts (2026-01)

What `/plan-builder` and `/plan-execute` actually rely on, verified against the
in-harness tool contracts and the official docs (docs.claude.com, Jan 2026).
**Corrects** a common confusion: the Agent *tool* in Claude Code is NOT the Agent
*SDK* — params like `context: fresh`, `maxTurns`, `permissionMode`,
`persistentMemory` are SDK concepts and are NOT available on the in-session Agent
tool. Design to the contracts below.

## Subagents / the Agent (Task) tool

- `subagent_type: "fork"` → forks the orchestrator: inherits its **full conversation
  context** AND **always runs the orchestrator's model** — a per-call `model`
  override is **ignored**. Use only when a session genuinely needs the live context.
- Any other `subagent_type` — or **omitting it** — starts a **fresh agent**
  (`general-purpose` by default), with a clean context window and the `model` you
  pass honored. **This is the right default for plan sessions** (isolation =
  robustness; sessions communicate via the project filesystem + closeouts, not
  context).
- Consequence for plans: `fork` + a per-session model is a contradiction
  (`build_plan.py` warns). For a model-pinned session, use a fresh typed agent.
- Subagents run to completion and return their final message; the parent does not
  interleave with them. Continue a prior agent with `SendMessage`; a new dispatch
  starts fresh. The Agent tool also offers `isolation: "worktree"` — **do NOT use
  it to dispatch a plan session.** A measured spike found it silently destroys
  agent output that lands in an untracked or gitignored path, with no error and
  no signal to the orchestrator. Plan-session isolation is orchestrator-managed
  `git worktree add` instead, and note that containment is advisory: putting a
  dispatched subagent into an existing worktree via `EnterWorktree(path=…)` is
  REFUSED at the repo root, contrary to that tool's own docs (measured
  2026-08-12). [`parallel-group-contract.md`](parallel-group-contract.md) is the
  single statement of record for what a parallel group may and may not do; read
  it before designing anything around isolation.

## Workflows ("ultracode")

- Deterministic JS orchestration: `phase()`/`agent()`/`parallel()`/`pipeline()`,
  schema-forced structured output, token budget. Concurrency cap ~16; 1000-agent
  lifetime cap.
- **Cannot take mid-run user input** and **resume is same-session only**. Therefore
  a Workflow can host a *bounded* per-session sub-pipeline (implement → verify →
  adversarially-verify, with schema-forced verdicts) and parallel batch dispatch —
  but it **cannot** host the cross-session loop, human checkpoints, or `--resume`.
  Those stay in the main conversation. This is the "Workflow seam" the skill uses.

## /loop & ScheduleWakeup

- `ScheduleWakeup` re-enters the session later with a prompt you supply — the
  mechanism for condition-gated waits and surviving long/unattended runs. Delay is
  clamped to [60, 3600]s; staying ≤270s keeps the prompt cache warm.
- `/loop` runs a prompt on an interval within a session (self-paced if no interval).
  Session-scoped; ~7-day expiry. Good for "poll until X," not durable scheduling.
- Use these for endurance (re-enter `/plan-execute <dir> --auto`), NOT to bypass a
  human checkpoint — the checkpoint still halts.

## Cloud Routines (`/schedule`) — machine-closed durability

- Run on Anthropic infra on a cron cadence (min interval **1 hour**), independent of
  your laptop. **No local-file access** — the plan dir must live in the repo the
  routine checks out. Use to invoke `/plan-execute <dir> --auto` reliably without
  your machine; keep genuine human checkpoints so it halts+notifies on irreversibles.

## Background tasks

- `run_in_background` (Bash) + `Monitor` drive a long subprocess without blocking the
  turn — the "self-drive long async pipelines" pattern for slow gates/deploys.
- **Ping on completion, don't make the operator poll (OR-03).** When you drive a long
  Monitor wait or a slow background pipeline, emit a `PushNotification` (or invoke
  the plan's configured `notify_on_complete`/`notify_on_gate` hook) when it finishes,
  so "check progress" becomes unnecessary. Plan-complete already fires the
  `notify_on_complete` "you can stop watching" ping automatically.
- `TaskCreate/Get/List/Stop/Update` track work items for visibility.

## Per-gate autonomy policy — block vs notify-and-continue (OR-03)

The reliability assessment found ~26% of April–May prompts were rubber-stamps
(`proceed`/`yes`/menu letters) answered to gates that ALWAYS got the same answer.
The `dispatch.checkpoint_policy` field lets a rubber-stamp gate default-continue
with a notification instead of parking in AWAITS_REVIEW and waiting to be polled.

- **Field:** `sessions[].dispatch.checkpoint_policy: "block" | "notify-and-continue"`
  (default `block` = today's behaviour). Companion guard flag:
  `dispatch.guards_irreversible: bool`.
- **What it governs:** ONLY the `session_review_ack` gate TYPE — a subagent's
  post-session `human_checkpoint_reason` asking for a review ack. Resolved by
  `scripts/gate_policy.py`, applied in both `run.cmd_apply` and `verify-finalize`.
- **Fail-closed, opt-IN.** notify-and-continue is granted only when ALL hold: (a)
  the gate TYPE is on the evidence-derived allowlist (`session_review_ack`,
  `bmad_elicitation_menu`); (b) `requires_human_checkpoint` is false; (c) it guards
  no irreversible action (`guards_irreversible`/`irreversible` false); (d) the gate
  EXPLICITLY sets `checkpoint_policy: notify-and-continue`. Default/unset =
  `block`, so every existing plan is unchanged until an author flips one gate. Any
  unknown TYPE, missing field, or ambiguity → block. A non-allowlisted TYPE CANNOT
  opt in via the field — the allowlist is the gate, never a content heuristic.
- **`requires_human_checkpoint` stays absolute** — it can never be reclassified to
  notify-and-continue, whatever `checkpoint_policy` says. Keep it for irreversibles.
- **On auto-continue:** the session keeps its terminal result (DONE/PARTIAL), a
  `gate_auto_continue` event is written to `run.ndjson` (the authoritative trail),
  and a best-effort push (`notify_on_gate`, falling back to `notify_on_complete`) is
  fired. Full taxonomy: the S08 `gate-taxonomy.md` evidence doc.

## Eval-gate best practices (shaped the verify design)

- Decide on **structured PASS/FAIL + a hard threshold**, not text parsing. (Verify
  argv gates key on returncode; skill gates report `done|failed`.)
- **Bound every rework/retry loop** (turn/agent caps). Verify's `max_rework` is the
  bound; the orchestrator never loops a gate unboundedly.
- For **noisy** evals, replicate and judge a confidence interval rather than a single
  run — relevant when a `verify` gate wraps a stochastic metric.
- **Human gate is the top safety tier** — keep it for irreversibles; let automated
  gates clear the routine cases.

## Gotchas that mattered here

- Omitting `subagent_type` is a fresh agent, not a fork (the old plan docs had this
  backwards).
- A Workflow won't pause for input and won't resume across a session exit — never
  put a human checkpoint or cross-session resume inside one.
- A self-reported `DONE` is not evidence; that's the entire reason `verify` gates
  exist. Run an independent check before it counts.
- `ScheduleWakeup` past 300s pays a prompt-cache miss; pick the delay deliberately.

_Sources: in-harness Agent/Workflow/ScheduleWakeup tool contracts; Claude Code docs
(subagents, workflows, scheduled-tasks, routines, permission-modes, hooks, headless),
fetched 2026-01._
