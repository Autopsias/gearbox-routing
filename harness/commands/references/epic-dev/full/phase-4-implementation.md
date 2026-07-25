# Phase 4: Dev Story - Implementation (sonnet)

**Execute when:** `phase == "atdd_complete"` OR `phase == "dev_story"`

This phase implements the story (TDD GREEN phase for coding; prose writing for documentation).

```
Output: "=== [Phase 4/8] Implementing story: {story_key} (sonnet) ==="

Update session:
  - phase: "dev_story"
  - tdd_phase: "green"
  - last_updated: {timestamp}

Write sprint-status.yaml

IF story_type == "documentation":
  Task(
    subagent_type="epic-implementer",
    model="opus",
    description="Write documentation for {story_key}",
    prompt="Write documentation for story {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

This is a DOCUMENTATION story. No code or tests required.
Write the documentation artifacts described in the acceptance criteria.
Focus on: clarity, completeness, accuracy, correct structure, and all required sections.
Run pnpm prepush only if config/non-doc files were touched.
Return ONLY JSON: {docs_created, docs_updated, acs_addressed, status}"
  )

  Verify documentation:
  - All required doc files created/updated
  - Story acceptance criteria addressed

  Update session:
    - phase: "dev_complete"
    - tdd_phase: "skipped"

  Write sprint-status.yaml

  Output: "Documentation complete."

  PROCEED TO VERIFICATION GATE 4.5

ELSE:
  Task(
    subagent_type="epic-implementer",
    model="opus",
    description="Implement story {story_key}",
    prompt="Implement story {story_key} (TDD GREEN phase).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- ATDD checklist: {session.atdd_checklist_file}
- Tests to pass: {session.atdd_tests_count}

Execute the bmad-bmm-dev-story workflow.
Make all tests pass. Run pnpm prepush before completing.

Maintain {sprint_artifacts}/stories/{story_key}-implementation-notes.md as you work. As much as a spec covers, there are always ambiguities and unknown unknowns — this file is your out to make a reasonable call and keep the human in the loop rather than stall or guess silently. Capture:
- Design decisions where the spec was ambiguous
- Intentional deviations from the story/spec, and why
- Tradeoffs considered and the reasoning for the choice made
- Open questions to confirm with the human later

Return ONLY JSON: {tests_passing, tests_total, prepush_status, files_modified, implementation_notes_file, status}"
  )

  Verify implementation:
  - All ATDD tests passing
  - pnpm prepush passes (or equivalent validation)
  - Story status updated to "review"

  Update session:
    - phase: "dev_complete"
    - tdd_phase: "complete"

  Write sprint-status.yaml

  Output: "Implementation complete. All ATDD tests passing (GREEN)."

  PROCEED TO VERIFICATION GATE 4.5
END IF
```

---

## VERIFICATION GATE 4.5: Post-Implementation Test Verification

**Purpose**: Verify all tests still pass after dev-story phase. This prevents regressions from being hidden by JSON-only output.
**Skipped for documentation stories** — no automated tests to verify.

```
# Skip Gate 4.5 for documentation stories
IF story_type == "documentation":
  Output: "=== [Gate 4.5] SKIPPING — documentation story (no tests to verify) ==="
  PROCEED TO PHASE 5
END IF

Output: "=== [Gate 4.5] Verifying test state after implementation ==="

INITIALIZE:
  verification_iteration = 0
  max_verification_iterations = 3

WHILE verification_iteration < max_verification_iterations:

  # Orchestrator directly runs tests (not delegated)
  ```bash
  cd {project_root}
  TEST_OUTPUT=$(cd apps/api && uv run pytest tests -q --tb=short 2>&1 || true)
  ```

  # Check for failures
  IF TEST_OUTPUT contains "FAILED" OR "failed" OR "ERROR":
    verification_iteration += 1
    Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing"

    IF verification_iteration < max_verification_iterations:
      # Ralph-style: Feed failures back to implementer
      Task(
        subagent_type="epic-test-fixer",
        model="opus",
        description="Fix failing tests (iteration {verification_iteration})",
        prompt="Fix failing tests for story {story_key} (verification iteration {verification_iteration}).

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

Fix the failing tests. Do NOT modify test files unless absolutely necessary.
Run tests after fixing. Return JSON: {fixes_applied, tests_passing, status}"
      )
    ELSE:
      # Max iterations reached
      Output: "ERROR: Max verification iterations ({max_verification_iterations}) reached"
      Output: "Tests still failing after {max_verification_iterations} fix attempts."

      IF "--force-model" in arguments:
        # FAIL-CLOSED (A): unattended runs NEVER advance a story with red tests. Quarantine it.
        # (AskUserQuestion hangs headless anyway — the safe auto-choice is quarantine, NOT "continue".)
        Output: "STORY_BLOCKED: Gate 4.5 red after {max_verification_iterations} fix attempts for {story_key} — quarantined, NOT advanced to Phase 5."
        Update session: story {story_key} status -> "blocked", gate_blocked: "4.5", phase: "blocked", last_error: "Gate 4.5 red after {max_verification_iterations} attempts"
        CONTINUE to next story   # surface for a human fix + /epic-dev {epic_num} --full --resume
      ELSE:
        gate_escalation = AskUserQuestion(
          question: "Verification gate 4.5 failed after 3 iterations. How to proceed?",
          header: "Gate 4.5 Failed",
          options: [
            {label: "Quarantine (recommended)", description: "Mark story blocked, continue with next story — never advances with red tests"},
            {label: "Continue anyway", description: "Proceed to code review with failing tests (risky)"},
            {label: "Manual fix", description: "Pause for manual intervention"},
            {label: "Stop", description: "Save state and exit"}
          ]
        )
        Handle gate_escalation accordingly
        IF gate_escalation == "Quarantine":
          Update session: story {story_key} status -> "blocked", gate_blocked: "4.5", last_error: "Gate 4.5 failed"
          CONTINUE to next story
        ELSE IF gate_escalation == "Continue anyway":
          BREAK from loop
        ELSE IF gate_escalation == "Manual fix":
          Output: "Pausing for manual test fix."
          Output: "Resume with: /epic-dev {epic_num} --full --resume"
          HALT
        ELSE:
          HALT
        END IF
  ELSE:
    Output: "VERIFICATION GATE 4.5 PASSED: All tests green"
    BREAK from loop
  END IF

END WHILE

PROCEED TO PHASE 5
```
