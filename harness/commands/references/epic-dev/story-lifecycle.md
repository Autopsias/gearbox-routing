# Story Lifecycle

Detailed phase-by-phase instructions for developing stories within `/epic-dev`. Covers both phase-level mode (`--phase-single`) and story-level mode.

## Contents

- [Story Type Detection](#story-type-detection)
- [UAT Story Handling](#uat-story-handling)
- [Infrastructure Story Handling](#infrastructure-story-handling)
- [Phase: CREATE (status == "backlog")](#phase-create-status-backlog)
- [Phase: DEVELOP (status == "ready-for-dev")](#phase-develop-status-ready-for-dev)
- [VERIFICATION GATE 2.5: Post-Implementation Test Verification](#verification-gate-25-post-implementation-test-verification)
- [Phase: REVIEW (status == "review" or after DEVELOP)](#phase-review-status-review-or-after-develop)
- [VERIFICATION GATE 3.5: Post-Review Test Verification](#verification-gate-35-post-review-test-verification)
- [Status Update Pattern (Reusable)](#status-update-pattern-reusable)
- [Epic Completion (STEP 5)](#epic-completion-step-5)


---

## Story Type Detection

Applied at the start of both DEVELOP and REVIEW phases. Determines workflow routing.

```
# Read story file (already created or pre-existing)
story_content = Read("{sprint_artifacts}/stories/{story_key}.md")
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

Output: "Story type detected: {story_type} ({story_key})"
```

---

## UAT Story Handling

```
IF story_type == "uat":
  Output: "════════════════════════════════════════════════════════"
  Output: "UAT STORY DETECTED: {story_key}"
  Output: "════════════════════════════════════════════════════════"
  Output: "This story is a UAT validation gate — not a coding story."
  Output: "Run: /epic-dev-uat {epic_num}"
  Output: "════════════════════════════════════════════════════════"
  HALT  # Cannot auto-process; user must run UAT command first
```

---

## Infrastructure Story Handling

Replaces Develop + Review phases entirely. Always prompts user (even in `--yolo`).

```
briefing_ref = Extract first match of "deployment-briefings/\S+" from story_content
IF briefing_ref is empty: briefing_ref = "(see story file for briefing path)"
story_acs = Extract all lines matching /^\*?\*?AC-/ from story_content

Output: "════════════════════════════════════════════════════════"
Output: "INFRASTRUCTURE STORY: {story_key}"
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

# In unattended mode (--force-model), output RALPH_BLOCKED and exit instead of prompting
IF "--force-model" in arguments:
  Output: "RALPH_BLOCKED: Infrastructure story {story_key} requires manual AWS provisioning"
  HALT

# ALWAYS prompt user (even in --yolo) — only in interactive mode
infra_decision = AskUserQuestion(
  question: "Have you completed the AWS provisioning steps for {story_key}?",
  header: "Infrastructure",
  options: [
    {label: "Yes, all ACs verified — mark done", description: "All acceptance criteria verified"},
    {label: "Not yet — pause here", description: "Will complete provisioning and return"},
    {label: "Partially done — continue later", description: "Incomplete, will resume later"}
  ]
)

IF infra_decision == "Yes, all ACs verified — mark done":
  # Use standard retry pattern to mark done (see Status Update Pattern below)
  Update sprint-status.yaml: {story_key} -> done
  Update story file: Status -> done
  Output: "✅ Story {story_key} COMPLETE! Infrastructure provisioning confirmed."
  SKIP to next story (skip Develop and Review phases below)
ELSE:
  Output: "Infrastructure provisioning not complete. Pausing."
  HALT
END IF
```

---

## Phase: CREATE (status == "backlog")

```
Output: "=== Creating story: {story_key} (opus) ==="
Task(
  subagent_type="epic-story-creator",
  model="opus",
  description="Create story {story_key}",
  prompt="Create story for {story_key}.

Context:
- Epic file: {sprint_artifacts}/epic-{epic_num}.md
- Story key: {story_key}
- Sprint artifacts: {sprint_artifacts}

Execute the BMAD create-story workflow.
Return ONLY JSON summary: {story_path, ac_count, task_count, status}"
)

# Parse JSON response - expect: {"story_path": "...", "ac_count": N, "status": "ready-for-dev"}
# Verify story was created successfully
```

---

## Phase: DEVELOP (status == "ready-for-dev")

### Documentation Stories

```
Output: "=== Developing story: {story_key} (sonnet) ==="

Task(
  subagent_type="epic-implementer",
  model="sonnet",
  description="Write documentation for {story_key}",
  prompt="Write documentation content for story {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

This is a DOCUMENTATION story. Focus on:
- Writing clear, accurate prose content
- Satisfying all acceptance criteria (content completeness, not test coverage)
- No code implementation or automated test writing required
Return ONLY JSON summary: {files_created, acs_satisfied, status}"
)
```

### Coding Stories

```
Output: "=== Developing story: {story_key} (sonnet) ==="

Task(
  subagent_type="epic-implementer",
  model="sonnet",
  description="Develop story {story_key}",
  prompt="Implement story {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

Execute the BMAD dev-story workflow.
Make all acceptance criteria pass.
Run pnpm prepush before completing.
Return ONLY JSON summary: {tests_passing, prepush_status, files_modified, status}"
)

# Parse JSON response - expect: {"tests_passing": N, "prepush_status": "pass", "status": "in-progress"}
```

---

## VERIFICATION GATE 2.5: Post-Implementation Test Verification

**Purpose**: Verify all tests pass after implementation. Don't trust JSON output - directly verify.
**Skipped for documentation stories** (no automated tests).

```
IF story_type == "documentation":
  Output: "Skipping Gate 2.5 — documentation story has no automated tests"
ELSE:
  Output: "=== [Gate 2.5] Verifying test state after implementation ==="

  INITIALIZE:
    verification_iteration = 0
    max_verification_iterations = 3

  WHILE verification_iteration < max_verification_iterations:

    # Orchestrator directly runs tests
    ```bash
    cd {project_root}
    TEST_OUTPUT=$(cd apps/api && uv run pytest tests -q --tb=short 2>&1 || true)
    ```

    IF TEST_OUTPUT contains "FAILED" OR "failed" OR "ERROR":
      verification_iteration += 1
      Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing"

      IF verification_iteration < max_verification_iterations:
        Task(
          subagent_type="epic-test-fixer",
          model="sonnet",
          description="Fix failing tests (iteration {verification_iteration})",
          prompt="Fix failing tests for story {story_key} (iteration {verification_iteration}).

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

Fix the failing tests. Return JSON: {fixes_applied, tests_passing, status}"
        )
      ELSE:
        Output: "ERROR: Max verification iterations reached"
        IF "--force-model" in arguments:
          # FAIL-CLOSED (A): NEVER advance / mark a story done with failing tests in
          # unattended mode. Quarantine it and move on — a red story must not ship.
          Output: "STORY_BLOCKED: Gate 2.5 red after 3 fix attempts for {story_key} — quarantined, NOT advanced to review."
          Update sprint-status: epic_dev_session story {story_key} status -> "blocked", gate_blocked: "2.5", last_error: "Gate 2.5 red after 3 attempts"
          # Do NOT proceed to REVIEW for this story; do NOT mark it done. Continue the loop with the NEXT story.
          # The blocked story is surfaced for a human fix + targeted re-run (/epic-dev {epic} --resume).
          SKIP to next story
        ELSE:
          gate_escalation = AskUserQuestion(
            question: "Gate 2.5 failed after 3 iterations. How to proceed?",
            header: "Gate Failed",
            options: [
              {label: "Quarantine (recommended)", description: "Mark story blocked, move to next story — never advances with red tests"},
              {label: "Continue anyway", description: "Proceed to code review with failing tests (risky)"},
              {label: "Manual fix", description: "Pause for manual intervention"},
              {label: "Stop", description: "Save state and exit"}
            ]
          )
          Handle gate_escalation accordingly
    ELSE:
      Output: "VERIFICATION GATE 2.5 PASSED: All tests green"
      BREAK from loop
    END IF

  END WHILE
END IF
```

---

## Phase: REVIEW (status == "review" or after DEVELOP)

### Documentation Stories

```
Output: "=== Reviewing story: {story_key} (opus) ==="

Task(
  subagent_type="epic-code-reviewer",
  model="opus",
  description="Review documentation for {story_key}",
  prompt="Review documentation content for {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

This is a DOCUMENTATION story. Review for:
- Content completeness and accuracy (all ACs satisfied)
- Clarity, structure, and readability
- Correctness of technical details
- No test coverage or code review needed
MUST find 2-5 specific content improvement issues.
Return ONLY JSON summary: {total_issues, content_issues, clarity_issues, auto_fixable}"
)

Output: "Skipping Gate 3.5 — documentation story has no automated tests"
```

### Coding Stories

```
Output: "=== Reviewing story: {story_key} (opus) ==="

Task(
  subagent_type="epic-code-reviewer",
  model="opus",
  description="Review story {story_key}",
  prompt="Review implementation for {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

Execute the BMAD code-review workflow.
MUST find 3-10 specific issues.
Return ONLY JSON summary: {total_issues, high_issues, medium_issues, low_issues, auto_fixable}"
)

# Parse JSON response
# If high/medium issues found, auto-fix and re-review
```

---

## VERIFICATION GATE 3.5: Post-Review Test Verification

**Purpose**: Verify all tests still pass after code review fixes.
**Skipped for documentation stories** (no automated tests).

```
IF story_type == "documentation":
  Output: "Skipping Gate 3.5 — documentation story has no automated tests"
ELSE:
  # SPEED (B): code review often applies NO fix. If it changed no code/tests since DEVELOP's green
  # gate (2.5), the suite is provably still green — skip this full run. git-detected (autonomous
  # --auto commits per phase, so HEAD == last green; scope apps/api so story-file edits don't defeat
  # it). FAIL-SAFE: any apps/api diff, or git unavailable/errors -> run the loop normally.
  IF `git -C {project_root} diff --quiet HEAD -- apps/api` exits 0 (no code/test change):
    Output: "=== [Gate 3.5] SKIPPED — code review changed no code/tests since Gate 2.5 (still green) ==="
    PROCEED TO Status Updates   # gate passed — do NOT run the verification loop below
  END IF

  Output: "=== [Gate 3.5] Verifying test state after code review ==="

  INITIALIZE:
    verification_iteration = 0
    max_verification_iterations = 3

  WHILE verification_iteration < max_verification_iterations:

    # Orchestrator directly runs tests
    ```bash
    cd {project_root}
    TEST_OUTPUT=$(cd apps/api && uv run pytest tests -q --tb=short 2>&1 || true)
    ```

    IF TEST_OUTPUT contains "FAILED" OR "failed" OR "ERROR":
      verification_iteration += 1
      Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing after review"

      IF verification_iteration < max_verification_iterations:
        Task(
          subagent_type="epic-test-fixer",
          model="sonnet",
          description="Fix post-review failures (iteration {verification_iteration})",
          prompt="Fix test failures caused by code review changes for story {story_key}.

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

Fix without reverting the review improvements.
Return JSON: {fixes_applied, tests_passing, status}"
        )
      ELSE:
        Output: "ERROR: Max verification iterations reached"
        IF "--force-model" in arguments:
          # FAIL-CLOSED (A): a story is NEVER marked done with red tests in unattended mode.
          Output: "STORY_BLOCKED: Gate 3.5 red after 3 fix attempts for {story_key} — quarantined, NOT marked done."
          Update sprint-status: epic_dev_session story {story_key} status -> "blocked", gate_blocked: "3.5", last_error: "Gate 3.5 red after 3 attempts"
          # Do NOT mark this story done. Continue the loop with the NEXT story; surface the blocked story for a human fix + re-run.
          SKIP to next story
        ELSE:
          gate_escalation = AskUserQuestion(
            question: "Gate 3.5 failed after 3 iterations. How to proceed?",
            header: "Gate Failed",
            options: [
              {label: "Quarantine (recommended)", description: "Mark story blocked, move on — never marks done with red tests"},
              {label: "Continue anyway", description: "Mark story done with failing tests (risky)"},
              {label: "Revert review", description: "Revert code review fixes"},
              {label: "Manual fix", description: "Pause for manual intervention"},
              {label: "Stop", description: "Save state and exit"}
            ]
          )
          Handle gate_escalation accordingly
    ELSE:
      Output: "VERIFICATION GATE 3.5 PASSED: All tests green after review"
      BREAK from loop
    END IF

  END WHILE
END IF
```

---

## Status Update Pattern (Reusable)

Used after REVIEW phase completion, infrastructure confirmation, and epic completion. **CRITICAL: Execute these steps DIRECTLY using Edit tool (not via subagent).**

### Update sprint-status.yaml

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read current file to get ACTUAL content
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  # 2. Find current status - look for "  {story_key}: <status>"
  SEARCH for line matching "  {story_key}: " and extract current_status

  IF current_status == "done":
    Output: "✅ sprint-status.yaml already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings (preserve 2-space indent)
  Edit(
    file_path="{sprint_artifacts}/sprint-status.yaml",
    old_string="  {story_key}: {current_status}",
    new_string="  {story_key}: done"
  )

  # 4. Verify by re-reading
  updated = Read("{sprint_artifacts}/sprint-status.yaml")
  IF updated contains "  {story_key}: done":
    Output: "✅ sprint-status.yaml updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "⚠️ Verification failed, retry {retry_count}/{max_retries}"

END WHILE

IF retry_count >= max_retries:
  Output: "❌ FAILED to update sprint-status.yaml after 3 retries"
  HALT with "Manual intervention required for status update"
```

### Update story file Status field

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read story file
  content = Read("{sprint_artifacts}/stories/{story_key}.md")

  # 2. Find current Status line (e.g., "Status: in_progress")
  SEARCH for line starting with "Status: " and extract current_status

  IF current_status == "done":
    Output: "✅ Story file already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings
  Edit(
    file_path="{sprint_artifacts}/stories/{story_key}.md",
    old_string="Status: {current_status}",
    new_string="Status: done"
  )

  # 4. Verify by re-reading
  updated = Read("{sprint_artifacts}/stories/{story_key}.md")
  IF updated contains "Status: done":
    Output: "✅ Story file status updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "⚠️ Verification failed, retry {retry_count}/{max_retries}"

END WHILE

IF retry_count >= max_retries:
  Output: "❌ FAILED to update story file status after 3 retries"
  HALT with "Manual intervention required for status update"
```

### Confirm completion

Only after BOTH updates verified:
```
Output: "✅ Story {story_key} COMPLETE! Status updated to 'done' in both files."
```

---

## Epic Completion (STEP 5)

When all stories in the epic are done (no more pending stories).

### Step 0: UAT Gate Check (MANDATORY)

```
# UAT must pass before epic can be marked done
content = Read("{sprint_artifacts}/sprint-status.yaml")

IF content contains "epic_uat_session:" with epic == {epic_num}:
  # Extract gate_decision from epic_uat_session block
  IF gate_decision in ["PASS", "WAIVED"]:
    Output: "UAT gate: {gate_decision}"
    # Proceed to epic status update below
  ELSE:
    Output: "HALT: UAT not passed (current: {gate_decision}). Run: /epic-dev-uat {epic_num}"
    HALT
ELSE:
  Output: "HALT: UAT required before epic completion. Run: /epic-dev-uat {epic_num}"
  HALT
```

### Step A: Update epic status in sprint-status.yaml

```
max_retries = 3
retry_count = 0

WHILE retry_count < max_retries:

  # 1. Read current file
  content = Read("{sprint_artifacts}/sprint-status.yaml")

  # 2. Find current epic status - look for "  epic-{epic_num}: <status>"
  SEARCH for line matching "  epic-{epic_num}: " and extract current_status

  IF current_status == "done":
    Output: "✅ Epic status already shows 'done'"
    BREAK

  # 3. Edit with EXACT strings
  Edit(
    file_path="{sprint_artifacts}/sprint-status.yaml",
    old_string="  epic-{epic_num}: {current_status}",
    new_string="  epic-{epic_num}: done"
  )

  # 4. Verify
  updated = Read("{sprint_artifacts}/sprint-status.yaml")
  IF updated contains "  epic-{epic_num}: done":
    Output: "✅ Epic status updated successfully"
    BREAK
  ELSE:
    retry_count += 1
    Output: "⚠️ Verification failed, retry {retry_count}/{max_retries}"

END WHILE
```

### Step B: Update retrospective status (if exists)

```
# Look for retrospective entry
content = Read("{sprint_artifacts}/sprint-status.yaml")

IF content contains "epic-{epic_num}-retrospective:":
  SEARCH for "  epic-{epic_num}-retrospective: " and extract current_status

  IF current_status in ["optional", "backlog"]:
    Edit(
      file_path="{sprint_artifacts}/sprint-status.yaml",
      old_string="  epic-{epic_num}-retrospective: {current_status}",
      new_string="  epic-{epic_num}-retrospective: pending"
    )
    Output: "✅ Retrospective status set to 'pending'"
```

### Step C: Verify all story statuses

```
content = Read("{sprint_artifacts}/sprint-status.yaml")

# Count stories for this epic that are NOT done
SEARCH for all lines matching "  {epic_num}-*: "
FOR each match:
  IF status != "done":
    Output: "⚠️ Story {key} is still '{status}' - epic cannot be complete"
    HALT

Output: "✅ All {count} stories verified as 'done'"
```

### Final Output

```
Output:
================================================
EPIC {epic_num} COMPLETE!
================================================
Stories completed: {count}
Epic status: done
Retrospective status: pending

Next steps:
- Retrospective: /bmad-bmm-retrospective
- Next epic: /epic-dev {next_epic_num}
================================================
```
