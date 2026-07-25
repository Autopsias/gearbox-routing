# Phase 2b: Pre-Implementation Premortem (Lane-A adversarial gate)

**Execute when:** `phase == "validation_complete"` OR `phase == "premortem_pending"`
**Skipped for:** documentation stories, infrastructure stories, UAT stories (only `coding` stories)
**Runs:** BEFORE Phase 3 (ATDD) — red-teams the PRD + story before any implementation begins

This phase adversarially stress-tests the BMAD PRD and story file BEFORE building, surfacing
severity-tagged findings in the findings-contract/v1 envelope. It is the Lane-A equivalent of
Lane B's plan-harden premortem, reusing `/adversarial-review` machinery exclusively.

**Lane-separation invariant (MANDATORY):**
- `/adversarial-review` is the lane-neutral primitive used here.
- `/plan-harden` is Lane B ONLY — NEVER invoke it from Lane A.
- Lane A retains: BMAD phases, story lifecycle, sprint-status.yaml, epic-dev conductor.
- Lane B retains: plan/manifest/session model, plan-harden, /plan-execute.
- This phase adds an adversarial gate inside the epic-dev conductor only (this file).
  Zero edits to any bmad-* framework files.

---

```
# Skip Phase 2b for non-coding stories
IF story_type != "coding":
  Output: "=== [Phase 2b] SKIPPING premortem — {story_type} story ==="
  Update session:
    - phase: "premortem_complete"
  Write sprint-status.yaml
  PROCEED TO PHASE 3
END IF

Output: ""
Output: "════════════════════════════════════════════════════════════════════════════════"
Output: "PHASE 2b — PRE-IMPLEMENTATION PREMORTEM"
Output: "Red-teaming PRD + story before ATDD/implementation begins"
Output: "════════════════════════════════════════════════════════════════════════════════"
Output: ""

Update session:
  - phase: "premortem_pending"
  - last_updated: {timestamp}
Write sprint-status.yaml

# Locate architecture and PRD artifacts for review context.
# Standard BMAD layout: epic file, story file, optional docs/architecture.md.
# The adversarial review agent will discover what's present from the project root.
EPIC_FILE     = "{sprint_artifacts}/epic-{epic_num}.md"
STORY_FILE    = "{sprint_artifacts}/stories/{story_key}.md"
ARCH_HINT     = "docs/architecture.md or docs/adrs/ if present — include in review scope"

# CONDUCTOR NOTE — structural guard (mirrors phase-ship.md):
# The conductor does NOT run SlashCommand directly.
# The premortem is delegated to a fresh `claude` agent via Task() — that agent
# has its own tool list that includes SlashCommand. This preserves the conductor's
# pure-orchestrator invariant while still invoking /adversarial-review.
Task(
  description="Run premortem adversarial review for {story_key}",
  prompt="You are the premortem agent for epic-dev Lane A. Your ONLY job is to
adversarially red-team the BMAD PRD and story file BEFORE implementation begins,
and emit findings in the findings-contract/v1 envelope.

LANE-SEPARATION RULE: You MUST use /adversarial-review (lane-neutral primitive).
NEVER invoke /plan-harden — that is a Lane B tool and its invocation from Lane A
violates the non-negotiable lane boundary.

Context:
- Epic number: {epic_num}
- Story key: {story_key}
- Epic file (PRD proxy): {EPIC_FILE}
- Story file: {STORY_FILE}
- Architecture hint: {ARCH_HINT}
- Sprint artifacts root: {sprint_artifacts}

STEP 1: Depth guard
```bash
echo \"SLASH_DEPTH=${SLASH_DEPTH:-0}\"
```
If SLASH_DEPTH >= 2: output the findings-contract/v1 PASS report (premortem depth-skipped)
and STOP:
```json
{
  \"schema\": \"findings-contract/v1\",
  \"source\": \"epic-dev-premortem\",
  \"decision\": \"PASS\",
  \"post_fix_verified\": true,
  \"findings\": [{
    \"id\": \"pm-skip\",
    \"severity\": \"info\",
    \"file\": \"\",
    \"line\": 0,
    \"description\": \"Premortem skipped: SLASH_DEPTH >= 2 (maximum chain depth)\",
    \"action\": \"no-op\",
    \"suggestion\": \"\",
    \"status\": \"open\",
    \"blocking\": false,
    \"source\": \"agent\",
    \"evidence\": \"SLASH_DEPTH guard\",
    \"intent_touched\": false,
    \"post_fix_verified\": true
  }],
  \"summary\": \"Premortem skipped at chain depth limit.\"
}
```

STEP 2: Read the review targets
Read the epic file at {EPIC_FILE} and story file at {STORY_FILE}.
If docs/architecture.md exists, note that path as additional context.

STEP 3: Invoke adversarial review
Run /adversarial-review with a scope hint targeting PRD + story:

  SlashCommand(command=\"/adversarial-review PRD and story pre-implementation: focus on missing requirements, flawed assumptions, implementation risk, and acceptance-criteria gaps. Story: {story_key}. Epic file: {EPIC_FILE}. Story file: {STORY_FILE}.\")

STEP 4: Translate findings to findings-contract/v1
Map the /adversarial-review output to the canonical envelope:

Severity mapping from /adversarial-review output:
  CRITICAL / HIGH  => severity: error   (blocking: true if unresolved)
  MEDIUM           => severity: warning (blocking: false)
  LOW              => severity: info    (action: no-op)

Action assignment rules (findings-contract/v1 §action-assignment-rules):
  1. Intent-precedence: if a finding would re-add/undo deliberately-named code => ask-user
  2. Ambiguous or design/taste/architecture call => ask-user
  3. Objective correctness (missing requirement, broken AC, security hole) => auto-fix
     (but cross-cutting blast radius => ask-user instead)
  4. Nothing to do => no-op

Gate-decision mapping:
  - ANY finding with severity:error AND status:open => decision: FAIL
  - ELSE ANY finding with action:ask-user => decision: CONCERNS
  - ELSE all no-op or auto-fix fixed+post_fix_verified => decision: PASS

STEP 5: Output the findings-contract/v1 report
Output ONLY the JSON block (so the conductor can parse it). Example shape:
```json
{
  \"schema\": \"findings-contract/v1\",
  \"source\": \"epic-dev-premortem\",
  \"decision\": \"PASS|CONCERNS|FAIL\",
  \"post_fix_verified\": false,
  \"findings\": [
    {
      \"id\": \"pm-1\",
      \"severity\": \"error|warning|info\",
      \"file\": \"{story_key}.md\",
      \"line\": 0,
      \"description\": \"finding — authoritative, the defect and why it is wrong\",
      \"action\": \"no-op|auto-fix|ask-user\",
      \"suggestion\": \"hypothesis for the fixer\",
      \"status\": \"open\",
      \"blocking\": false,
      \"source\": \"agent\",
      \"evidence\": \"what proves the finding\",
      \"intent_touched\": false,
      \"post_fix_verified\": false
    }
  ],
  \"summary\": \"one-line summary\"
}
```

You have access to: Task, Bash, SlashCommand, Read, Grep, Write"
)

# Parse the premortem report from Task output
premortem_report = Parse JSON from task output
premortem_decision = premortem_report.decision  # PASS | CONCERNS | FAIL

# Display findings
Output: ""
Output: "─────────────────────────────────────────────────────────────────────"
Output: "PREMORTEM RESULT: {premortem_decision}"
Output: "─────────────────────────────────────────────────────────────────────"
FOR each finding in premortem_report.findings:
  Output: "[{finding.severity.upper()}] {finding.id} — {finding.description}"
  IF finding.action == "ask-user" OR finding.blocking:
    Output: "  Action: {finding.action} | Blocking: {finding.blocking}"
    Output: "  Suggestion: {finding.suggestion}"
Output: ""
Output: "Summary: {premortem_report.summary}"
Output: "─────────────────────────────────────────────────────────────────────"
Output: ""

# Branch on decision
IF premortem_decision == "PASS":
  Output: "PREMORTEM PASSED — no blocking findings. Proceeding to ATDD (Phase 3)."

  Update session:
    - phase: "premortem_complete"
    - last_updated: {timestamp}
  Write sprint-status.yaml
  PROCEED TO PHASE 3

ELIF premortem_decision == "CONCERNS":
  # CONCERNS = has ask-user findings — surface, not a hard block
  IF "--force-model" in arguments:
    # Unattended mode: log concerns but proceed (not a hard block)
    Output: "PREMORTEM CONCERNS (unattended mode) — {count(findings where action==ask-user)} ask-user finding(s). Logged; proceeding."
    Update session:
      - phase: "premortem_complete"
      - premortem_concerns: true
      - last_updated: {timestamp}
    Write sprint-status.yaml
    PROCEED TO PHASE 3
  ELSE:
    # Attended mode: ask user
    premortem_decision_choice = AskUserQuestion(
      question: "Premortem found CONCERNS (ask-user findings). How to proceed?",
      header: "Premortem — CONCERNS",
      options: [
        {label: "Proceed anyway (accept concerns)", description: "CONCERNS are not a hard block; log and continue to ATDD"},
        {label: "Fix story/PRD first", description: "Pause for manual revision of story or epic file, then resume"},
        {label: "Waive all concerns", description: "Accept concerns as known-debt and continue"}
      ]
    )
    IF premortem_decision_choice == "Proceed anyway (accept concerns)" OR premortem_decision_choice == "Waive all concerns":
      Output: "CONCERNS accepted. Proceeding to ATDD (Phase 3)."
      Update session:
        - phase: "premortem_complete"
        - premortem_concerns: true
        - last_updated: {timestamp}
      Write sprint-status.yaml
      PROCEED TO PHASE 3
    ELSE:
      Output: "Pausing for story/PRD revision."
      Output: "Story file: {STORY_FILE}"
      Output: "Epic file: {EPIC_FILE}"
      Output: "Resume with: /epic-dev {epic_num} --full --resume"
      HALT
    END IF
  END IF

ELIF premortem_decision == "FAIL":
  # FAIL = blocking error findings — NEVER advance with a FAIL premortem
  IF "--force-model" in arguments:
    # Fail-closed: quarantine the story
    Output: "STORY_BLOCKED: Premortem FAIL for {story_key} — {count(findings where blocking==true)} blocking finding(s). Quarantined; NOT advanced to ATDD."
    Output: "Findings:"
    FOR each finding in premortem_report.findings WHERE blocking == true:
      Output: "  [{finding.severity.upper()}] {finding.id}: {finding.description}"
    Update session:
      - story {story_key} status -> "blocked"
      - gate_blocked: "premortem"
      - phase: "blocked"
      - last_error: "Premortem FAIL — blocking findings"
      - last_updated: {timestamp}
    Write sprint-status.yaml
    CONTINUE to next story  # surface for human fix + /epic-dev {epic_num} --full --resume
  ELSE:
    # Attended mode: escalate
    Output: "PREMORTEM FAILED — blocking findings require resolution before implementation."
    Output: ""
    Output: "Blocking findings:"
    FOR each finding in premortem_report.findings WHERE blocking == true:
      Output: "  [{finding.severity.upper()}] {finding.id}: {finding.description}"
      Output: "  Evidence: {finding.evidence}"
    Output: ""
    
    premortem_fail_choice = AskUserQuestion(
      question: "Premortem FAIL — blocking findings must be resolved. How to proceed?",
      header: "Premortem — FAIL",
      options: [
        {label: "Fix story/PRD and re-run premortem", description: "Revise story or epic file to address blocking findings"},
        {label: "Waive FAIL (risky)", description: "Override the FAIL gate — accept all findings as known-debt and continue (not recommended)"},
        {label: "Quarantine story", description: "Mark story blocked, continue with next story"},
        {label: "Stop", description: "Save state and exit"}
      ]
    )
    
    IF premortem_fail_choice == "Waive FAIL (risky)":
      Output: "FAIL waived by operator. Proceeding to ATDD (Phase 3) with acknowledged blocking findings."
      Update session:
        - phase: "premortem_complete"
        - premortem_waived: true
        - last_updated: {timestamp}
      Write sprint-status.yaml
      PROCEED TO PHASE 3
    ELIF premortem_fail_choice == "Fix story/PRD and re-run premortem":
      Output: "Pausing for story/PRD revision to address blocking findings."
      Output: "Story file: {STORY_FILE}"
      Output: "Epic file: {EPIC_FILE}"
      Output: "After fixing, resume with: /epic-dev {epic_num} --full --resume"
      Update session:
        - phase: "premortem_pending"  # Re-run premortem on resume
        - last_updated: {timestamp}
      Write sprint-status.yaml
      HALT
    ELIF premortem_fail_choice == "Quarantine story":
      Update session:
        - story {story_key} status -> "blocked"
        - gate_blocked: "premortem"
        - phase: "blocked"
        - last_error: "Premortem FAIL — operator-quarantined"
        - last_updated: {timestamp}
      Write sprint-status.yaml
      CONTINUE to next story
    ELSE:  # Stop
      Output: "Saving state and exiting."
      HALT
    END IF
  END IF
END IF
```

---

## Phase state values added by this gate

The `epic_dev_session.phase` field transitions are:

| From state | Phase 2b entry | Outcome states |
|------------|---------------|----------------|
| `validation_complete` | `premortem_pending` | `premortem_complete` (PASS/CONCERNS) |
| `premortem_pending` | *(resume)* | `premortem_complete`, `blocked` |
| `premortem_complete` | *(skip — already ran)* | → proceed to `testarch_atdd` |

## Lane-private elements (testability)

The following are Lane-A-ONLY and must not appear in Lane B (plan-harden / plan-execute):

| Element | Lane | Must stay in |
|---------|------|-------------|
| `sprint-status.yaml` session state (`premortem_pending`, `premortem_complete`, `gate_blocked`) | A | epic-dev conductor only |
| `epic_dev_session` YAML fields (`premortem_concerns`, `premortem_waived`) | A | epic-dev conductor only |
| Story-lifecycle phase routing (validation_complete → premortem → ATDD) | A | epic-dev conductor only |
| BMAD sub-agent dispatch (`epic-atdd-writer`, `epic-implementer`, etc.) | A | epic-dev conductor only |

The following are Lane-B-ONLY and must not appear in Lane A:

| Element | Lane | Must stay in |
|---------|------|-------------|
| `manifest.json` / session files (`s01.prompt.md`, etc.) | B | plan-execute / plan-harden |
| `/plan-harden` invocation | B | plan-harden only |
| Plan session model (PLAN.html, sessions/sNN.prompt.md) | B | plan-execute only |

**Verify command (run to confirm lane separation is intact):**
```bash
# Lane A must NOT invoke plan-harden
grep -r "plan-harden" ~/.claude/commands/references/epic-dev/ && echo "LANE VIOLATION" || echo "OK"

# Lane B must NOT invoke BMAD story lifecycle
grep -r "sprint-status" ~/.claude/commands/plan-execute.md ~/.claude/commands/plan-harden.md && echo "LANE VIOLATION" || echo "OK"

# Phase 2b must exist in epic-dev references (Lane A)
ls ~/.claude/commands/references/epic-dev/full/phase-2b-premortem.md && echo "OK" || echo "MISSING"
```
