# Autonomous mode (`--auto`) — in-session self-drive

`--auto` runs an epic to completion UNATTENDED, **in-session** — the conductor
(this conversation) IS the loop. There is no external `claude -p` runner and no
bash `while`. This is the modern replacement for the retired Ralph loop; it keeps
every durability property (on-disk state + git checkpoints) and adds harness-native
endurance (ScheduleWakeup / cloud Routine) and human-gate compatibility.

## Contents

- [Why in-session beats the old external runner](#why-in-session-beats-the-old-external-runner)
- [`--auto` implies `--force-model` + `--yolo`](#-auto-implies-force-model-yolo)
- [The loop](#the-loop)
- [Composes with `--parallel-stories`](#composes-with-parallel-stories)
- [Hard stops (the ONLY things that halt the run)](#hard-stops-the-only-things-that-halt-the-run)
- [Endurance — long epics, long waits, machine-closed](#endurance-long-epics-long-waits-machine-closed)
- [Invariants (do not weaken)](#invariants-do-not-weaken)


## Why in-session beats the old external runner

The thing the Ralph runner bought — *fresh context per iteration, to fight context
rot over a long epic* — you already get for free: **each phase is dispatched as a
subagent** (its own context window), so the phase's heavy work never lands in the
orchestrator's context. The orchestrator accrues only short per-phase summaries.
You therefore do NOT need to restart a process every phase; you re-enter only when
the orchestrator's *own* context actually gets heavy (see Endurance). Fewer
restarts, no spawn overhead, no `--resume` session juggling, and human gates work.

## `--auto` implies `--force-model` + `--yolo`

Treat both as present everywhere downstream:
- `--force-model` → no `AskUserQuestion` (it hangs unattended anyway); every gate is
  **fail-closed** (gate exhaustion / Phase-8 FAIL → quarantine the story, never
  mark-done-with-red — see the gate logic in `story-lifecycle.md` / `full/phase-*`).
- `--yolo` → no "Confirm Next" pause between stories.

## The loop

Repeat until the epic is complete or a hard stop fires:

1. **Read state.** Load `{sprint_artifacts}/sprint-status.yaml`. Find the next
   non-`done`, non-`blocked` story for the epic; within it, the next incomplete
   phase (per `epic_dev_session.phase`). If none remain → **epic complete** (run the
   Epic Completion section in `story-lifecycle.md`, including the mandatory UAT gate
   check) → STOP.
2. **Dispatch the phase** exactly as the attended flow does — one Task subagent,
   `subagent_type`/`model`/`effort` from `epic-dev-assignments.yaml` (the SSOT;
   money-path `ultrathink` nudge on phases 3/5/8 of epics 2/4/6/7). Follow the phase
   spec in `full/phase-N-*.md` (full mode) or `story-lifecycle.md` (standard).
3. **Run the phase's verification gate** (if any). Gates are fail-closed under
   `--auto`: on exhaustion the story is **quarantined** (`status: blocked`,
   `gate_blocked` recorded, `STORY_BLOCKED` emitted) — do NOT advance it.
4. **Git checkpoint.** After each phase transition, commit the working tree
   (`git add -A && git commit -m "epic {N} {story}: {phase} complete"`) so a crash
   between phases loses nothing. (Use a temp branch only if on a protected branch.)
5. **Advance.** Update `sprint-status.yaml` (the orchestrator owns status writes —
   subagents never write plan/status state). Loop to step 1.

## Composes with `--parallel-stories`

If `--parallel-stories N` is also set AND the epic has a DECLARED-independent story group (see
`parallel-stories.md`), dispatch that group concurrently via the worktree mechanism instead of
picking one next story in step 1 — then resume this serial loop for the remaining (non-grouped)
stories. Undeclared stories always run serially. The fail-closed gates, sequential conflict-halting
merge, and post-merge integration run from `parallel-stories.md` all still apply.

## Hard stops (the ONLY things that halt the run)

- **Epic complete** — all stories `done`/terminal; report + STOP.
- **Quarantined story** — a gate-blocked story does NOT stop the run: skip it,
  keep going with siblings, and **list all quarantined stories at the end** with the
  resume command. (A run that finishes with 1 blocked story + 4 done beats a run that
  shipped 5 with red tests.)
- **Manual-required story** — an `infrastructure` or `uat` story emits
  `STORY_BLOCKED: requires manual …`; skip + surface, continue with siblings.
- **Unrecoverable error** — save state to `sprint-status.yaml`, emit
  `EPIC_DEV_ERROR: {reason}`, STOP.
- **Stall guard** — if the same `{story, phase}` is attempted twice with no state
  change (a phase that can't make progress), quarantine that story rather than
  spinning. (Replaces the Ralph runner's 3-iteration stall detector.)

Human checkpoints are not a concept in epic-dev's autonomous path — the gates are
the decision surface, and under `--auto` they resolve fail-closed without a prompt.

## Endurance — long epics, long waits, machine-closed

You hold the turn open and self-drive; three patterns keep that viable:

- **Context hygiene over a long epic.** Subagents keep phase work out of your
  context, but the orchestrator still accrues summaries over many phases. When that
  grows heavy (rule of thumb: after an epic's worth of stories, or when you notice
  the summary stream is large), use `ScheduleWakeup` to **re-enter
  `/epic-dev {epic} [--full] --auto --resume`** — a fresh orchestrator that resumes
  from `sprint-status.yaml` on disk. Nothing lives only in context, so this is
  lossless. This replaces Ralph's every-iteration restart with a re-entry that fires
  only when actually needed.
- **Long external wait** (a slow gate, a deploy to settle, a CI run). Don't block
  idle — use `run_in_background` + `Monitor`, or `ScheduleWakeup` tied to the
  measurable condition, then resume.
- **Machine-closed / overnight.** Schedule a cloud **Routine** (`/schedule`)
  invoking `/epic-dev {epic} --full --auto --resume` on a cadence (min interval 1h;
  cloud has no local files, so the plan/repo must be what the routine checks out).
  This is the modern replacement for the old background bash runner. Keep the
  manual-required stops (infra/UAT) so the routine halts + notifies rather than
  steamrolling work that genuinely needs you.

## Invariants (do not weaken)

- The orchestrator owns ALL `sprint-status.yaml` / story-status writes; subagents
  return results, never write plan state.
- Gates stay fail-closed under `--auto` — a story is never marked done with red
  tests or a FAIL quality gate.
- Model/effort always come from `epic-dev-assignments.yaml` via each agent's
  frontmatter — `--auto` never flattens the tuned scheme (the old env-flatten guard's
  intent: never export effort/model env vars that would override frontmatter).
- BMAD workflows (`bmad-*`) are invoked, never reimplemented or edited.
