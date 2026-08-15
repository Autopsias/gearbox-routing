# Autonomous mode (`--auto`) and endurance

Loaded on demand — read this when the user passes `--auto`, or when a gate/deploy
will hold the loop open long enough that idle-blocking matters. Primitive-level
contracts live in `autonomous-execution-primitives.md`.

## `--auto` — stop babysitting, keep the human gates

`--auto` is for "kick it off and walk away." It changes how YOU (the orchestrator)
self-drive between halts; it does **not** change the dispatch decision (`PYBP plan`
returns the same `action`, now with `auto_mode: true` echoed for the audit log).

**With `--auto`, drive the loop to completion without pausing for:**
- `dispatch_next: false` closeout hints — treat as advisory, keep going.
- verify `rework` — re-dispatch the `PARTIAL` session with feedback automatically (it's already bounded by `max_rework`).
- the gap between sessions — don't stop to ask "continue?" after each batch.

**`--auto` NEVER overrides these — they always halt, exactly as without it:**
- `requires_human_checkpoint` (a pre-dispatch human gate) → `checkpoint` → STOP for `--resume`.
- a closeout's `human_checkpoint_reason` / a `phase_closer.require_human_checkpoint` → `AWAITS_REVIEW` → STOP — **UNLESS** the session opts into notify-and-continue on this rubber-stamp gate (OR-03, below), in which case the loop auto-continues with a ping instead of stopping.
- `BLOCKED` / `halted` (including a verify `halted` and a failed shipping step).
- `deploy-auth-stale` `confirm-required`.

So the human boundary is **sacrosanct by construction**: autonomy is a property the
plan AUTHOR grants per-gate by choosing an automated **verify gate** (the loop
clears it) over a **human checkpoint** (the loop always stops). `--auto` just
removes the *incidental* pauses, never the deliberate ones.

**Verify + bounded rework run whether or not `--auto` is set** — a `verify` block buys
"don't ship trash"; `--auto` only buys "don't babysit." Run a gated plan attended
(default) or unattended (`--auto`). When it finishes or halts unattended, the opt-in
`notify_on_halt` / `notify_on_complete` hooks (plan spec) fire a project command so you
learn it ended without watching.

## Per-gate notify-and-continue (OR-03 — stop rubber-stamping)

~26% of April–May prompts were rubber-stamps (`proceed`/`yes`/menu letters) to gates
that ALWAYS got the same answer. A plan author can set
`sessions[].dispatch.checkpoint_policy: "notify-and-continue"` (default `"block"`) so
the post-session **AWAITS_REVIEW-ack** gate default-continues with a push notification
instead of parking for a poll. This is **fail-closed and TYPE-scoped**
(`scripts/gate_policy.py`): it applies ONLY to the `session_review_ack` gate TYPE, and
NEVER when `requires_human_checkpoint` is true or `dispatch.guards_irreversible`/an
irreversible action is in play — those stay absolute. On auto-continue the session keeps
its terminal result (DONE/PARTIAL), a `gate_auto_continue` event lands in `run.ndjson`,
and the `notify_on_gate` (→`notify_on_complete` fallback) hook fires. When you drive a
long `Monitor` wait, emit a `PushNotification`/notify hook on completion too, so "check
progress" is never needed. Full taxonomy + field reference:
[autonomous-execution-primitives.md](autonomous-execution-primitives.md) § Per-gate
autonomy policy.

## Endurance — long gates, soak windows, and surviving a long run

Autonomy is only useful if the loop survives the wait. Three patterns (none change
the state machine — only how you hold the turn open):

- **Long gate / deploy:** don't block idle. argv gates time out themselves; for a long skill gate or deploy, use `run_in_background` + `Monitor` (Bash) and react when it finishes — don't surface a task id and wait for "continue."
- **Soak / condition wait:** to wait for a measurable condition (coverage ≥ X%, error rate stable N min, deploy healthy), use `ScheduleWakeup` to re-enter and re-check — tied to the **condition**, not a calendar default — re-entering with `/plan-execute <dir> --auto`.
- **Very long plan:** each session is a SUBAGENT, so your context accrues only closeouts, not the work. State lives on disk (`PLAN.html` + `run_state.json` + `_closeouts/` + `_verify_state/`), so a fresh `/plan-execute <dir> --auto` resumes across a compaction boundary. Nothing is held only in context.

For a run that must survive your **machine being closed**, use a cloud **Routine**
(`/schedule`) to invoke `/plan-execute <dir> --auto` on a cadence (min interval 1h;
no local-file access — the plan dir must be in the repo the routine checks out).
Keep genuine human checkpoints so it halts+notifies rather than steamrolling an
irreversible step.
