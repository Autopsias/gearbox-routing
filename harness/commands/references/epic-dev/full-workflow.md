# BMAD Epic Development - Full TDD/ATDD Workflow

Execute the complete TDD/ATDD-driven BMAD development cycle for epic: "$ARGUMENTS"

## Contents

- [CRITICAL ORCHESTRATION CONSTRAINTS](#critical-orchestration-constraints)
- [CRITICAL EXECUTION CONSTRAINTS](#critical-execution-constraints)
- [STEP 1: Parse Arguments](#step-1-parse-arguments)
- [STEP 1.5: Autonomous mode (`--auto`)](#step-15-autonomous-mode-auto)
- [STEP 2: Detect BMAD Project](#step-2-detect-bmad-project)
- [STEP 3: Load Sprint Status and Discover Stories](#step-3-load-sprint-status-and-discover-stories)
- [STEP 4: Session Management](#step-4-session-management)
- [STEP 5: Story Processing Loop](#step-5-story-processing-loop)
- [Story & Epic Completion](#story-epic-completion)
- [PHASE 9: Ship (post-quality-gate ops chain)](#phase-9-ship-post-quality-gate-ops-chain)
- [Error Handling & Troubleshooting](#error-handling-troubleshooting)
- [EXECUTE NOW](#execute-now)


## CRITICAL ORCHESTRATION CONSTRAINTS

Canonical wording: `Read ~/.claude/commands/references/shared/pure-orchestrator-invariant.md`.

**This command's ONE documented exception**: you MUST use the Edit tool DIRECTLY for
status file updates (sprint-status.yaml story/epic status AND the story file's Status
field). This is NOT delegated because subagents don't have orchestration context — it is
the sole carve-out from the shared invariant above.

**SUBAGENT EXECUTION PATTERN**: Each Task call spawns an independent subagent that:
- Has its own context window (preserves main agent context)
- Executes autonomously until completion
- Returns results to the orchestrator

---

## CRITICAL EXECUTION CONSTRAINTS

**SEQUENTIAL EXECUTION ONLY** - Each phase MUST complete before the next starts:
- Never invoke multiple BMAD workflows in parallel
- Wait for each Task to complete before proceeding
- This ensures proper context flow through the 8-phase workflow

**UNATTENDED MODE**: When `--force-model` (or `--auto`, which implies it) is in arguments, you are running unattended.
- NEVER use AskUserQuestion — it is not available.
- Gate failure after 3 iterations: **FAIL-CLOSED** — quarantine the story (status -> `blocked`, emit `STORY_BLOCKED`); NEVER advance / mark-done with red tests. Continue with the next story; surface blocked stories at the end. (See the gate logic in `story-lifecycle.md` / `full/phase-*`.)
- Infrastructure/UAT stories: output "STORY_BLOCKED: requires manual intervention" and skip (continue with siblings).
- Errors: save state to sprint-status.yaml, output "EPIC_DEV_ERROR: {error}" and stop.

**MODEL STRATEGY** - Different models for different phases:

| # | Phase | Model | Rationale |
|---|-------|-------|-----------|
| 1 | create-story | `opus` | Deep understanding for quality story creation |
| 2 | validate-create-story | `sonnet` | Fast feedback loop for validation iterations |
| 2b | premortem (adversarial gate) | dispatched agent | Reuses /adversarial-review (dual-model: Opus + Codex) — no separate model override needed |
| 3 | testarch-atdd | `opus` | Quality test generation requires deep understanding |
| 4 | dev-story | `sonnet` | Balanced speed/quality for implementation |
| 5 | code-review | `opus` | Thorough adversarial review |
| 6 | testarch-automate | `sonnet` | Iterative test expansion |
| 7 | testarch-test-review | `sonnet` | Quality review needs judgment — Death-A silent-lie guard (S02 audit, H-10) |
| 8 | testarch-trace | `opus` | Quality gate decision requires careful analysis |

**PURE ORCHESTRATION** - This command:
- Invokes existing BMAD workflows via Task tool with model specifications
- Reads/writes sprint-status.yaml for state management
- Never directly modifies story implementation files (workflows do that)

---

## STEP 1: Parse Arguments

Parse "$ARGUMENTS" to extract:
- **epic_number** (required): First positional argument (e.g., "2" for Epic 2)
- **--interactive**: Run in current session with real-time visibility (can respond to prompts)
- **--resume**: Continue from last incomplete story/phase
- **--yolo**: Skip user confirmation pauses between stories
- **--force-model**: Skip model selection confirmation prompts (enables unattended automation)
- **--no-ship**: Skip the ship phase (Phase 9) after epic completion (commit/push/PR/CI loop)

**Validation:**
- epic_number must be a positive integer
- If no epic_number provided, error with: "Usage: /epic-dev <epic-number> --full [--yolo] [--resume] [--auto] [--interactive] [--force-model]"

---

## STEP 1.5: Autonomous mode (`--auto`)

**If `--auto` is present, self-drive the full workflow UNATTENDED, in-session** — no external
runner, no `claude -p`. This is the modern replacement for the retired Ralph loop.

```
IF "$ARGUMENTS" contains "--auto":
  # --auto implies --force-model + --yolo (treat both as present everywhere downstream).
  Output: "AUTONOMOUS MODE — Epic {epic_num}, full 8-phase cycle, in-session self-drive (fail-closed gates)."
  Read ~/.claude/commands/references/epic-dev/auto-mode.md and follow the in-session loop:
    - drive STEP 5's story/phase loop to completion; each phase is a fresh subagent (no process restarts);
    - gates are fail-closed (exhaustion -> quarantine, never advance with red tests);
    - git-checkpoint after each phase;
    - halt ONLY on epic-complete / quarantined-or-manual story (skip + surface) / unrecoverable error;
    - endurance: ScheduleWakeup to re-enter `/epic-dev {epic} --full --auto --resume` when context gets
      heavy; a cloud Routine (/schedule) for machine-closed runs.
  Then PROCEED TO STEP 2 (the loop itself runs the normal phases).
ELSE:
  PROCEED TO STEP 2   # attended / interactive
END IF
```

---

## STEP 2: Detect BMAD Project

```bash
PROJECT_ROOT=$(pwd)
while [[ ! -d "$PROJECT_ROOT/_bmad" ]] && [[ "$PROJECT_ROOT" != "/" ]]; do
  PROJECT_ROOT=$(dirname "$PROJECT_ROOT")
done

if [[ ! -d "$PROJECT_ROOT/_bmad" ]]; then
  echo "ERROR: Not a BMAD project. Run 'npx bmad-method install' first."
  exit 1
fi
```

Load sprint artifacts path from `_bmad/bmm/config.yaml` (default: `_bmad-output/implementation-artifacts`)

---

## STEP 3: Load Sprint Status and Discover Stories

Read `{sprint_artifacts}/sprint-status.yaml`

If not found:
- Output: "Sprint status file not found. Running sprint-planning workflow first..."
- Run: `Skill(skill="bmad-bmm-sprint-planning")`

Find stories for epic {epic_number}:
- Pattern: `{epic_num}-{story_num}-{story_title}`
- Filter: status NOT "done"
- Order by story number

If no pending stories:
- Output: ""
- Output: "<promise>EPIC COMPLETE</promise>"
- Output: ""
- Output: "All stories in Epic {epic_num} complete!"
- HALT

---

## STEP 4: Session Management

**Extended Session Schema for 8-Phase Workflow:**

```yaml
epic_dev_session:
  epic: {epic_num}
  current_story: "{story_key}"
  phase: "starting"  # See PHASE VALUES below

  # Validation tracking (Phase 2)
  validation_iteration: 0
  validation_issues_count: 0
  validation_last_pass_rate: 100

  # Premortem tracking (Phase 2b — pre-implementation adversarial gate)
  premortem_concerns: false   # true if CONCERNS decision; non-blocking
  premortem_waived: false     # true if FAIL waived by operator (risky)

  # TDD tracking (Phases 3-4)
  tdd_phase: "red"  # red | green | complete
  atdd_checklist_file: null
  atdd_tests_count: 0

  # Code review tracking (Phase 5)
  review_iteration: 0

  # Story type (set after Phase 1, persists through phases 2-8)
  story_type: "coding"  # coding | documentation | infrastructure | uat

  # Quality gate tracking (Phase 8)
  gate_decision: null  # PASS | CONCERNS | FAIL | WAIVED
  gate_iteration: 0
  p0_coverage: 0
  p1_coverage: 0
  overall_coverage: 0

  # Timestamps
  started: "{timestamp}"
  last_updated: "{timestamp}"
```

**PHASE VALUES:**
- `starting` - Initial state
- `create_story` - Phase 1: Creating story file
- `create_complete` - Phase 1 complete, proceed to validation
- `validation` - Phase 2: Validating story completeness
- `validation_complete` - Phase 2 complete, proceed to premortem
- `premortem_pending` - Phase 2b: Adversarial premortem (red-teams PRD+story before ATDD)
- `premortem_complete` - Phase 2b complete, proceed to ATDD
- `testarch_atdd` - Phase 3: Generating acceptance tests (RED)
- `atdd_complete` - Phase 3 complete, proceed to dev
- `dev_story` - Phase 4: Implementing story (GREEN)
- `dev_complete` - Phase 4 complete, proceed to review
- `code_review` - Phase 5: Adversarial review
- `review_complete` - Phase 5 complete, proceed to test automation
- `testarch_automate` - Phase 6: Expanding test coverage
- `automate_complete` - Phase 6 complete, proceed to test review
- `testarch_test_review` - Phase 7: Reviewing test quality
- `test_review_complete` - Phase 7 complete, proceed to trace
- `testarch_trace` - Phase 8: Quality gate decision
- `gate_decision` - Awaiting user decision on gate result
- `complete` - Story complete
- `error` - Error state

**If --resume AND session exists for this epic:**
- Resume from recorded phase
- Output: "Resuming Epic {epic_num} from story {current_story} at phase: {phase}"

**If NOT --resume (fresh start):**
- Clear any existing session
- Create new session with `phase: "starting"`

---

## STEP 5: Story Processing Loop

**CRITICAL: Process stories SERIALLY (one at a time)**

**Phase-Level Mode Detection:**

```
IF "--phase-single" in "$ARGUMENTS":
  # PHASE-LEVEL MODE: Execute ONLY the next incomplete phase

  Output: "PHASE-LEVEL MODE active (Full TDD/ATDD) - executing next incomplete phase..."

  # Load session state from sprint-status.yaml
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  # Extract session info (epic_dev_session)
  session = Extract epic_dev_session from content
  current_phase = session.phase
  current_story = session.current_story

  # If no session or phase is "starting" or "complete", find next story
  IF current_phase == "starting" OR current_phase == "complete" OR current_story is null:
    # Find next incomplete story
    FOR each story in epic {epic_number}:
      IF story.status == "backlog":
        current_story = story_key
        current_phase = "create_story"
        BREAK
      ELIF story.status == "ready-for-dev":
        current_story = story_key
        current_phase = "validation"
        BREAK
      # ... continue for other statuses
    END FOR

    IF current_story is null:
      Output: "ALL STORIES COMPLETE - Epic {epic_num} done!"
      Exit 0
  END IF

  # ─────────────────────────────────────────────────────────────
  # STORY TYPE DETECTION (for phase-level mode)
  # ─────────────────────────────────────────────────────────────
  IF current_phase IN ["validation", "create_complete"] AND (session.story_type is null OR session.story_type == "coding"):
    story_content = Read("{sprint_artifacts}/stories/{current_story}.md")
    story_title = Extract first line starting with "# " from story_content (strip "# " prefix)

    story_type = "coding"  # default

    IF story_title contains "UAT Validation" OR "UAT Gate" OR story_title matches "\bUAT\b":
      story_type = "uat"
    ELIF story_title contains any of "AWS", "IAM"
         OR story_title OR story_content contains any of
            "deployment-briefings", "MANUAL setup", "No coding required", "/aws-deploy", "AWS_setup":
      story_type = "infrastructure"
    ELIF story_title contains any of "Documentation", "Guide", "Reference", "Runbook", "README"
         AND story_content does NOT contain any of "pytest", "test_", ".test.ts", "uv run pytest":
      story_type = "documentation"

    Output: "Story type detected: {story_type} ({current_story})"

    # Persist to session
    Update session: story_type = {story_type}
    Write sprint-status.yaml

    # Handle UAT and Infrastructure before entering phase dispatch
    IF story_type == "uat":
      Output: "════════════════════════════════════════════════════════"
      Output: "UAT STORY DETECTED: {current_story}"
      Output: "════════════════════════════════════════════════════════"
      Output: "This story is a UAT validation gate — not a coding story."
      Output: "Run: /epic-dev {epic_num} --uat"
      Output: "════════════════════════════════════════════════════════"
      Exit 0  # Clean exit — not an error

    ELIF story_type == "infrastructure":
      # Infrastructure handling — Read ~/.claude/commands/references/epic-dev/full/phase-1-story-creation.md
      # for the full infrastructure workflow (INFRASTRUCTURE WORKFLOW section)
      briefing_ref = Extract first match of "deployment-briefings/\S+" from story_content
      IF briefing_ref is empty: briefing_ref = "(see story file for briefing path)"
      story_acs = Extract all lines matching /^\*?\*?AC-/ from story_content

      Output: "════════════════════════════════════════════════════════"
      Output: "INFRASTRUCTURE STORY: {current_story}"
      Output: "════════════════════════════════════════════════════════"
      Output: "This story requires manual AWS provisioning."
      Output: "No automated implementation will run."
      Output: ""
      Output: "REQUIRED ACTIONS:"
      Output: ""
      Output: "Step 1 — Run /aws-deploy in this project"
      Output: "  Creates/updates the deployment briefing at:"
      Output: "  {briefing_ref}"
      Output: ""
      Output: "Step 2 — Switch to the AWS_setup project and run: /example-project-deploy"
      Output: ""
      Output: "Step 3 — Verify Acceptance Criteria:"
      FOR each ac in story_acs: Output: "  {ac}"
      Output: ""
      Output: "Step 4 — Return here and confirm"
      Output: "════════════════════════════════════════════════════════"

      # ALWAYS prompt user (even in --yolo)
      infra_decision = AskUserQuestion(
        question: "Have you completed the AWS provisioning steps for {current_story}?",
        header: "Infrastructure",
        options: [
          {label: "Yes, all ACs verified — mark done", description: "All acceptance criteria verified"},
          {label: "Not yet — pause here", description: "Will complete provisioning and return"},
          {label: "Partially done — continue later", description: "Incomplete, will resume later"}
        ]
      )

      IF infra_decision == "Yes, all ACs verified — mark done":
        max_retries = 3; retry_count = 0
        WHILE retry_count < max_retries:
          content = Read("{sprint_artifacts}/sprint-status.yaml")
          SEARCH for "  {current_story}: " and extract current_status
          IF current_status == "done": Output: "already done"; BREAK
          Edit(file_path="{sprint_artifacts}/sprint-status.yaml",
               old_string="  {current_story}: {current_status}",
               new_string="  {current_story}: done")
          updated = Read("{sprint_artifacts}/sprint-status.yaml")
          IF updated contains "  {current_story}: done": Output: "sprint-status.yaml updated"; BREAK
          ELSE: retry_count += 1
        END WHILE
        retry_count = 0
        WHILE retry_count < max_retries:
          content = Read("{sprint_artifacts}/stories/{current_story}.md")
          SEARCH for "Status: " and extract current_status
          IF current_status == "done": Output: "story already done"; BREAK
          Edit(file_path="{sprint_artifacts}/stories/{current_story}.md",
               old_string="Status: {current_status}",
               new_string="Status: done")
          updated = Read("{sprint_artifacts}/stories/{current_story}.md")
          IF updated contains "Status: done": Output: "Story file updated"; BREAK
          ELSE: retry_count += 1
        END WHILE
        Output: "PHASE_COMPLETE: INFRASTRUCTURE {current_story}"
        Output: "   AWS provisioning confirmed, story marked done"
      ELSE:
        Output: "Infrastructure provisioning not complete. Pausing."
      Exit 0
    END IF  # END UAT/infrastructure handling
  END IF  # END type detection block

  # Restore story_type from session for subsequent phases
  story_type = session.story_type or "coding"

  # Execute the current phase for current_story
  Output: "=== Executing phase '{current_phase}' for story: {current_story} ==="

  # ─────────────────────────────────────────────────────────────
  # PHASE DISPATCH TABLE (phase-single mode)
  # ─────────────────────────────────────────────────────────────
  # Each phase block: update session BEFORE task, update to next phase AFTER task, then Exit 0.
  # For full phase details, read the corresponding reference file.

  # Phase 1: Create Story
  IF current_phase == "create_story":
    Output: "=== [Phase 1/8] Creating story: {current_story} (opus) ==="
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-1-story-creation.md and follow the Phase 1 steps (story creation only, not type detection which is handled above).

    Update session: phase: "create_complete"
    Output: "PHASE_COMPLETE: CREATE_STORY {current_story}"
    Output: "   Next phase: validation"
    Exit 0

  # Phase 2: Validate Story
  ELIF current_phase == "create_complete" OR current_phase == "validation":
    Output: "=== [Phase 2/8] Validating story: {current_story} (sonnet) ==="
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-2-validation.md and follow all steps.

    Update session: phase: "validation_complete"
    Output: "PHASE_COMPLETE: VALIDATION {current_story}"
    Output: "   Next phase: premortem (Phase 2b)"
    Exit 0

  # Phase 2b: Pre-Implementation Premortem (adversarial gate before ATDD)
  ELIF current_phase == "validation_complete" OR current_phase == "premortem_pending":
    Output: "=== [Phase 2b] Pre-implementation premortem: {current_story} ==="
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-2b-premortem.md and follow all steps.
    Exit 0

  # Phase 3: ATDD - Generate Acceptance Tests
  ELIF current_phase == "premortem_complete" OR current_phase == "testarch_atdd":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-3-atdd.md and follow all steps.
    Exit 0

  # Phase 4: Dev Story - Implementation
  ELIF current_phase == "atdd_complete" OR current_phase == "dev_story":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-4-implementation.md and follow all steps (including Gate 4.5).
    Exit 0

  # Phase 5: Code Review
  ELIF current_phase == "dev_complete" OR current_phase == "code_review":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-5-code-review.md and follow all steps (including Gate 5.5).
    Exit 0

  # Phase 6: Test Automation Expansion
  ELIF current_phase == "review_complete" OR current_phase == "testarch_automate":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-6-test-expansion.md and follow all steps (including Gate 6.5).
    Exit 0

  # Phase 7: Test Quality Review
  ELIF current_phase == "automate_complete" OR current_phase == "testarch_test_review":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-7-test-review.md and follow all steps (including Gate 7.5).
    Exit 0

  # Phase 8: Requirements Traceability & Quality Gate
  ELIF current_phase == "test_review_complete" OR current_phase == "testarch_trace":
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-8-quality-gate.md and follow all steps (including loop-back logic).

    # If gate passes, also follow story completion steps:
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/story-and-epic-completion.md and follow STEP 6 (Story Completion).
    Exit 0

  ELSE:
    Output: "ERROR: Unknown phase '{current_phase}'"
    Exit 1
  END IF

  # Check if all stories complete
  all_done = true
  FOR each story in epic {epic_number}:
    IF story_status != "done":
      all_done = false
      BREAK
  END FOR

  IF all_done:
    # Follow epic completion steps
    **Instructions:** Read ~/.claude/commands/references/epic-dev/full/story-and-epic-completion.md and follow STEP 7 (Epic Completion).
    Exit 0
  END IF

ELSE:
  # ─────────────────────────────────────────────────────────────
  # STORY-LEVEL MODE: Complete entire story with all 8 phases
  # ─────────────────────────────────────────────────────────────
  Output: "STORY-LEVEL MODE active - executing complete 8-phase TDD/ATDD workflow per story..."
END IF
```

For each pending story in story-level mode:

### PHASE 1: Create Story (opus)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-1-story-creation.md and follow all steps (including story type detection and infrastructure/UAT handling).

---

### PHASE 2: Validate Create Story (sonnet, max 3 iterations)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-2-validation.md and follow all steps.

---

### PHASE 2b: Pre-Implementation Premortem (adversarial gate, dispatched agent)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-2b-premortem.md and follow all steps.

Runs for `coding` stories only; skipped for documentation/infrastructure/UAT.
PASS → proceed to Phase 3. CONCERNS → surface + ask (or log in --auto). FAIL → block story.

---

### PHASE 3: ATDD - Generate Acceptance Tests (opus)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-3-atdd.md and follow all steps.

---

### PHASE 4: Dev Story - Implementation (sonnet)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-4-implementation.md and follow all steps (including Verification Gate 4.5).

---

### PHASE 5: Code Review (opus, max 3 iterations)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-5-code-review.md and follow all steps (including Verification Gate 5.5).

---

### PHASE 6: Test Automation Expansion (sonnet)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-6-test-expansion.md and follow all steps (including Verification Gate 6.5).

---

### PHASE 7: Test Quality Review (sonnet)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-7-test-review.md and follow all steps (including Verification Gate 7.5).

---

### PHASE 8: Requirements Traceability & Quality Gate (opus)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-8-quality-gate.md and follow all steps (including loop-back logic).

---

## Story & Epic Completion
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/story-and-epic-completion.md and follow all steps (STEP 6 for story completion, STEP 7 for epic completion).

---

## PHASE 9: Ship (post-quality-gate ops chain)
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/phase-ship.md and follow all steps.

This phase runs AFTER STEP 7 (epic completion) and dispatches the shared ship-tail via a
dedicated Task agent. The conductor itself does NOT call SlashCommand — the ship agent has
its own tool list that includes SlashCommand. Skip with `--no-ship`.

---

## Error Handling & Troubleshooting
**Instructions:** Read ~/.claude/commands/references/epic-dev/full/troubleshooting.md for error handling patterns and tasklist integration.

---

## EXECUTE NOW

Parse "$ARGUMENTS" and begin processing immediately with the full 8-phase TDD/ATDD workflow.
