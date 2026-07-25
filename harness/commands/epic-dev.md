---
description: "Orchestrates BMAD development: standard, TDD/ATDD (--full), UAT (--uat), or end-of-epic validation (--end). Use when you say 'develop epic N', 'start epic', 'run epic UAT', 'end of epic tests'."
argument-hint: "<epic-number> [--full|--uat|--end|--phase N|--yolo|--auto|--parallel-stories N|--no-ship]"
allowed-tools: ["Task", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

# BMAD Epic Development

Execute development cycle for epic: "$ARGUMENTS"

---

## STEP 1: Parse Arguments

Parse "$ARGUMENTS":
- **epic_number** (required): First positional argument (e.g., "2")
- **--full**: Use the complete 8-phase TDD/ATDD workflow
- **--uat**: Run the three-tier hybrid UAT phase
- **--end**: Run end-of-epic validation (NFR + test review + quality gate)
- **--yolo**: Skip confirmation prompts between stories
- **--force-model**: Skip model selection confirmation prompts (enables unattended automation)
- **--auto**: Unattended in-session self-drive to epic completion (modern loop; replaces the retired Ralph `--loop`). Implies `--yolo` + `--force-model`. See STEP 1.5.
- **--parallel-stories N** (+ optional `--stories S1,S2,…`): run DECLARED-independent stories concurrently in isolated git worktrees (opt-in; default serial). See STEP 1.6 + `references/epic-dev/parallel-stories.md`. Only safe for file-disjoint stories.
- **--resume**: Continue from last incomplete story/phase
- **--phase-single**: Execute only the next incomplete phase
- **--no-ship**: Skip the ship phase (Phase 9) after epic completion (commit/push/PR/CI loop)

Validation:
- If no epic_number: Error "Usage: /epic-dev <epic-number> [--full|--uat|--end] [--yolo] [--force-model] [--auto]"

---

## Flag Routing

Parse the arguments for mode flags:
- If `--full` is present: Read `~/.claude/commands/references/epic-dev/full-workflow.md` and follow those instructions instead of the standard cycle below. Pass remaining args (epic number, --phase, --yolo, --auto, --resume, --force-model) to that workflow.
- If `--uat` is present: Read `~/.claude/commands/references/epic-dev/uat-phase.md` and follow those instructions. Pass the epic number and any remaining flags (--waiver, --resume, --retest-only).
- If `--end` is present: Read `~/.claude/commands/references/epic-dev/end-tests.md` and follow those instructions. Pass the epic number and any remaining flags (--yolo, --resume).
- If no mode flag: Continue with the standard cycle below.

---

## STEP 1.5: Autonomous mode (`--auto`)

**If `--auto` is present:** run the epic UNATTENDED, in-session — no external runner, no `claude -p` loop.

**Instructions:** Read `~/.claude/commands/references/epic-dev/auto-mode.md` and follow the in-session self-drive loop. In brief:
- `--auto` IMPLIES `--force-model` (no `AskUserQuestion` prompts; gates are fail-closed → quarantine on exhaustion) AND `--yolo` (no "Confirm Next" pause). Treat both as present everywhere downstream.
- Self-drive STEP 4's story/phase loop to completion. Each phase is already a fresh subagent (that is what keeps the run lean — no per-iteration process restart needed).
- Halt ONLY on: epic complete; a quarantined / `STORY_BLOCKED` story (keep going with siblings, surface blocked ones at the end); an infra/UAT story that needs manual work; or an unrecoverable error (save state + surface).
- Endurance: if the orchestrator's own context grows large over a long epic, use `ScheduleWakeup` to re-enter `/epic-dev {epic} [--full] --auto --resume` — it resumes from `sprint-status.yaml` on disk. For a machine-closed run, schedule a cloud Routine (`/schedule`) invoking the same. See `auto-mode.md`.

If `--auto` is NOT present, continue to STEP 2 (attended / interactive).

---

## STEP 1.6: Parallel stories (`--parallel-stories N`)

**If `--parallel-stories N` is present:** independent stories of this epic may run CONCURRENTLY in
isolated git worktrees (opt-in; default is serial). This is the biggest wall-clock lever but is ONLY
safe for genuinely file-disjoint stories.

**Instructions:** Read `~/.claude/commands/references/epic-dev/parallel-stories.md` and follow it. In brief:
- Parallelize ONLY stories the operator DECLARED independent (`parallel_group:` in story metadata, or
  an explicit `--stories S1,S2,…` list). Undeclared stories run serially. Never infer independence.
- Each parallel story runs its FULL BMAD pipeline (fail-closed gates) in its own `git worktree` on its
  own branch from a clean base; the orchestrator merges branches back SEQUENTIALLY, HALTING on any
  conflict (a conflict means the independence declaration was wrong), then runs ONE post-merge
  integration suite. Concurrency is capped at `min(N, group_size, 4)`.
- Composes with `--auto`. If no parallel group is declared, behaves exactly as serial.

If `--parallel-stories` is NOT present, all stories run serially (the default).

---

## STEP 2: Verify BMAD Project

```bash
PROJECT_ROOT=$(pwd)
while [[ ! -d "$PROJECT_ROOT/_bmad" ]] && [[ "$PROJECT_ROOT" != "/" ]]; do
  PROJECT_ROOT=$(dirname "$PROJECT_ROOT")
done

if [[ ! -d "$PROJECT_ROOT/_bmad" ]]; then
  echo "ERROR: Not a BMAD project. Run bmad-method install first."
  exit 1
fi
```

Load sprint artifacts path from `_bmad/bmm/config.yaml` (default: `_bmad-output/implementation-artifacts`)

---

## STEP 3: Load Stories

Read `{sprint_artifacts}/sprint-status.yaml`

If not found:
- Error: "Run /bmad-bmm-sprint-planning first"

Find stories for epic {epic_number}:
- Pattern: `{epic_num}-{story_num}-{title}`
- Filter: status NOT "done"
- Order by story number

If no pending stories:
- Output: "All stories in Epic {epic_num} complete!"
- HALT

---

## MODEL STRATEGY

| Phase | Model | Rationale |
|-------|-------|-----------|
| create-story | opus | Deep understanding for quality stories |
| dev-story | sonnet | Balanced speed/quality for implementation |
| code-review | opus | Thorough adversarial review |

---

## STEP 4: Process Each Story

**Phase-Level Mode Detection:**

```
IF "--phase-single" in "$ARGUMENTS":
  # PHASE-LEVEL MODE: Execute ONLY the next incomplete phase
  Output: "📋 PHASE-LEVEL MODE active - executing next incomplete phase..."
```

**Instructions:** Read `~/.claude/commands/references/epic-dev/story-lifecycle.md` and follow all steps for the current story's phase:

1. **Story Type Detection** — Detect type (coding, uat, infrastructure, documentation)
2. **Phase routing based on story status:**
   - `backlog` → Phase: CREATE (opus)
   - `ready-for-dev` → Phase: DEVELOP (sonnet) + Gate 2.5
   - `review` → Phase: REVIEW (opus) + Gate 3.5 + Status Updates
3. **Special story types** (UAT, infrastructure, documentation) have distinct workflows
4. **Verification Gates** 2.5 and 3.5 run tests and auto-fix up to 3 iterations
5. **Status Updates** use the retry pattern to update both sprint-status.yaml and story file

In phase-single mode, exit after completing one phase. Otherwise, process the full story lifecycle.

```
ELSE:
  # STORY-LEVEL MODE: Original behavior (complete entire story)
  Output: "📋 STORY-LEVEL MODE active - executing complete stories..."
END IF
```

FOR each pending story, execute the full lifecycle:

```
[ ] 1. CREATE (if backlog)
[ ] 2. Story Type Detection
[ ] 3. DEVELOP (with Gate 2.5)
[ ] 4. REVIEW (with Gate 3.5)
[ ] 5. Status Updates (mark done)
[ ] 6. Confirm Next (unless --yolo)
```

**Instructions for each sub-step:** Read `~/.claude/commands/references/epic-dev/story-lifecycle.md` and follow the matching section.

---

## STEP 5: Epic Complete - MANDATORY EPIC STATUS UPDATE

When all stories in the epic are done (no more pending stories):

**Instructions:** Read `~/.claude/commands/references/epic-dev/story-lifecycle.md` and follow the "Epic Completion (STEP 5)" section, which includes:
- Step 0: UAT Gate Check (MANDATORY)
- Step A: Update epic status in sprint-status.yaml
- Step B: Update retrospective status (if exists)
- Step C: Verify all story statuses

---

## STEP 6: Ship Phase (post-quality-gate ops chain)

After STEP 5 completes (epic status marked done):

**Instructions:** Read `~/.claude/commands/references/epic-dev/full/phase-ship.md` and follow all steps.

Skip with `--no-ship` flag. The ship phase dispatches ship-tail via a Task agent — the
conductor itself does NOT call SlashCommand directly (pure-orchestrator invariant preserved;
the Task agent has its own tool list that includes SlashCommand).

---

## TASKLIST INTEGRATION (MANDATORY)

**Instructions:** Read `~/.claude/commands/references/epic-dev/troubleshooting.md` and follow all TaskList integration patterns:
- Phase Task Creation Pattern (with dependencies)
- Phase Execution Pattern (status transitions)
- Verification Gate Sub-Tasks
- Progress Summary Pattern (after each phase)

For autonomous (`--auto`) runs, also read `~/.claude/commands/references/epic-dev/auto-mode.md` for the in-session self-drive loop + endurance (ScheduleWakeup / cloud Routine).

---

## ERROR HANDLING

**Instructions:** Read `~/.claude/commands/references/epic-dev/troubleshooting.md` and follow:
- Error handling (retry/skip/stop)
- Gate escalation patterns (Gate 2.5 and 3.5 failures)
- Confirm next story pattern (unless --yolo)

---

## EXECUTE NOW

Parse "$ARGUMENTS" and begin processing immediately.
