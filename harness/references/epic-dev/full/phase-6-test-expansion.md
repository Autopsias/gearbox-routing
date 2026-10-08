# Phase 6: Test Automation Expansion (sonnet)

**Execute when:** `phase == "review_complete"` OR `phase == "testarch_automate"`
**Skipped for documentation stories** — no automated tests to expand.

This phase expands test coverage beyond the initial ATDD tests.

```
# Skip Phase 6 for documentation stories
IF story_type == "documentation":
  Output: "=== [Phase 6/8] SKIPPING test expansion — documentation story ==="
  Update session:
    - phase: "automate_complete"
  Write sprint-status.yaml
  PROCEED TO VERIFICATION GATE 6.5
END IF

Output: "=== [Phase 6/8] Expanding test coverage: {story_key} (sonnet) ==="

Update session:
  - phase: "testarch_automate"
  - last_updated: {timestamp}

Write sprint-status.yaml

Task(
  subagent_type="epic-test-expander",  # ISOLATED: Fresh perspective on coverage gaps
  model="sonnet",
  description="Expand test coverage for {story_key}",
  prompt="Expand test coverage for story {story_key} (Phase 6).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- ATDD checklist: {session.atdd_checklist_file}

CRITICAL: You did NOT write the original tests. Analyze the IMPLEMENTATION for gaps.
Execute the bmad-tea-testarch-automate workflow.
Add edge cases, error paths, integration tests with priority tagging.
Return ONLY JSON: {tests_added, coverage_before, coverage_after, test_files, by_priority, gaps_found, status}"
)

Parse automation output

Update session:
  - phase: "automate_complete"

Write sprint-status.yaml

Output: "Test automation complete. Added {tests_added} tests."
Output: "Coverage: {coverage_before}% -> {coverage_after}%"

PROCEED TO VERIFICATION GATE 6.5
```

---

## VERIFICATION GATE 6.5: Post-Test-Expansion Verification

**Purpose**: Verify all tests (original + new) pass after test expansion. New tests may conflict with existing implementation.
**Skipped for documentation stories** — no automated tests to verify.

```
# Skip Gate 6.5 for documentation stories
IF story_type == "documentation":
  Output: "=== [Gate 6.5] SKIPPING — documentation story (no tests to verify) ==="
  PROCEED TO PHASE 7
END IF

Output: "=== [Gate 6.5] Verifying test state after test expansion ==="

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
    Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing after expansion"

    IF verification_iteration < max_verification_iterations:
      # Determine if failure is in NEW tests or EXISTING tests
      # If new tests fail, they may need adjustment
      # If existing tests fail, implementation needs fixing
      Task(
        subagent_type="epic-test-fixer",
        model="opus",
        description="Fix post-expansion test failures (iteration {verification_iteration})",
        prompt="Fix test failures after test expansion for story {story_key} (iteration {verification_iteration}).

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

Determine if failures are in:
1. NEW tests (from expansion): May need to adjust test expectations
2. EXISTING tests (original ATDD): Implementation needs fixing

Fix accordingly. Return JSON: {fixes_applied, new_tests_adjusted, implementation_fixed, tests_passing, status}"
      )
    ELSE:
      # Max iterations reached
      Output: "ERROR: Gate 6.5 - Max verification iterations ({max_verification_iterations}) reached"

      IF "--force-model" in arguments:
        # FAIL-CLOSED (A): unattended runs never advance a story with red tests. Quarantine it.
        Output: "STORY_BLOCKED: Gate 6.5 red after {max_verification_iterations} fix attempts for {story_key} — quarantined, NOT advanced to Phase 7."
        Update session: story {story_key} status -> "blocked", gate_blocked: "6.5", phase: "blocked", last_error: "Gate 6.5 red after {max_verification_iterations} attempts"
        CONTINUE to next story   # surface for a human fix + /epic-dev {epic_num} --full --resume
      ELSE:
        gate_escalation = AskUserQuestion(
          question: "Verification gate 6.5 failed after 3 iterations. How to proceed?",
          header: "Gate 6.5 Failed",
          options: [
            {label: "Quarantine (recommended)", description: "Mark story blocked, continue with next story — never advances with red tests"},
            {label: "Remove failing new tests", description: "Delete new tests that are blocking progress"},
            {label: "Continue anyway", description: "Proceed to test review with failing tests"},
            {label: "Manual fix", description: "Pause for manual intervention"},
            {label: "Stop", description: "Save state and exit"}
          ]
        )
        Handle gate_escalation accordingly (Quarantine -> mark story blocked + CONTINUE to next story)
  ELSE:
    Output: "VERIFICATION GATE 6.5 PASSED: All tests green after expansion"
    BREAK from loop
  END IF

END WHILE

PROCEED TO PHASE 7
```
