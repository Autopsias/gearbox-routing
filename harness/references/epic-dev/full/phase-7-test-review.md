# Phase 7: Test Quality Review (sonnet)

**Execute when:** `phase == "automate_complete"` OR `phase == "testarch_test_review"`
**Skipped for documentation stories** — no automated tests to review.

This phase reviews test quality against best practices.

```
# Skip Phase 7 for documentation stories
IF story_type == "documentation":
  Output: "=== [Phase 7/8] SKIPPING test quality review — documentation story ==="
  Update session:
    - phase: "test_review_complete"
  Write sprint-status.yaml
  PROCEED TO VERIFICATION GATE 7.5
END IF

Output: "=== [Phase 7/8] Reviewing test quality: {story_key} (sonnet) ==="

Update session:
  - phase: "testarch_test_review"
  - last_updated: {timestamp}

Write sprint-status.yaml

Task(
  subagent_type="epic-test-reviewer",  # ISOLATED: Objective quality assessment
  model="sonnet",
  description="Review test quality for {story_key}",
  prompt="Review test quality for story {story_key} (Phase 7).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

CRITICAL: You did NOT write these tests. Apply quality criteria objectively.
Execute the bmad-tea-testarch-test-review workflow.
Check BDD format, test IDs, priority markers, no hard waits, deterministic assertions.
Return ONLY JSON: {quality_score, grade, tests_reviewed, issues_found, by_category, recommendations, status}"
)

Parse quality report

IF quality_score < 80 OR has_high_severity_issues:
  Output: "Test quality issues detected (score: {quality_score}%)"
  Output: "Issues: {issues_found}"

  quality_decision = AskUserQuestion(
    question: "How to handle test quality issues?",
    header: "Quality",
    options: [
      {label: "Fix issues", description: "Auto-fix test quality issues"},
      {label: "Continue", description: "Accept current quality and proceed to gate"}
    ]
  )

  IF quality_decision == "Fix issues":
    Task(
      subagent_type="epic-test-reviewer",  # Same agent fixes issues it identified
      model="sonnet",
      description="Fix test quality issues for {story_key}",
      prompt="Fix test quality issues for story {story_key}.

Issues to Fix:
{issues_found}

Apply auto-fixes for: hard waits, missing docstrings, missing priority markers.
Run tests after fixes to ensure they still pass.
Return ONLY JSON: {fixes_applied, tests_passing, quality_score, status}"
    )

Update session:
  - phase: "test_review_complete"

Write sprint-status.yaml

Output: "Test quality review complete. Score: {quality_score}%"

PROCEED TO VERIFICATION GATE 7.5
```

---

## VERIFICATION GATE 7.5: Post-Quality-Review Test Verification

**Purpose**: Verify all tests still pass after quality review fixes. Test refactoring may inadvertently break functionality.
**Skipped for documentation stories** — no automated tests to verify.

```
# Skip Gate 7.5 for documentation stories
IF story_type == "documentation":
  Output: "=== [Gate 7.5] SKIPPING — documentation story (no tests to verify) ==="
  PROCEED TO PHASE 8
END IF

# SPEED (B): test-quality review is largely read-only assessment. If it changed no code/tests since
# the last green gate (6.5), the suite is provably still green — skip this full run. Detect via git:
# autonomous --auto commits per phase so HEAD == last green. Scope to apps/api so story-file updates
# don't defeat it. FAIL-SAFE: any apps/api diff, or git unavailable/errors -> run normally.
IF `git -C {project_root} diff --quiet HEAD -- apps/api` exits 0 (no code/test change):
  Output: "=== [Gate 7.5] SKIPPED — quality review changed no code/tests since Gate 6.5 (still green) ==="
  PROCEED TO PHASE 8
END IF

Output: "=== [Gate 7.5] Verifying test state after quality review fixes ==="

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
    Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing after quality fixes"

    IF verification_iteration < max_verification_iterations:
      # Quality review changes may have altered test behavior
      Task(
        subagent_type="epic-test-fixer",
        model="opus",
        description="Fix post-quality-review test failures (iteration {verification_iteration})",
        prompt="Fix test failures after quality review for story {story_key} (iteration {verification_iteration}).

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

Quality review may have:
1. Refactored test structure
2. Fixed test naming/IDs
3. Removed hardcoded waits
4. Changed assertions

Ensure fixes maintain test intent while passing.
Return JSON: {fixes_applied, tests_passing, status}"
      )
    ELSE:
      # Max iterations reached
      Output: "ERROR: Gate 7.5 - Max verification iterations ({max_verification_iterations}) reached"

      IF "--force-model" in arguments:
        # FAIL-CLOSED (A): unattended runs never advance a story with red tests. Quarantine it.
        Output: "STORY_BLOCKED: Gate 7.5 red after {max_verification_iterations} fix attempts for {story_key} — quarantined, NOT advanced to Phase 8."
        Update session: story {story_key} status -> "blocked", gate_blocked: "7.5", phase: "blocked", last_error: "Gate 7.5 red after {max_verification_iterations} attempts"
        CONTINUE to next story   # surface for a human fix + /epic-dev {epic_num} --full --resume
      ELSE:
        gate_escalation = AskUserQuestion(
          question: "Verification gate 7.5 failed after 3 iterations. How to proceed?",
          header: "Gate 7.5 Failed",
          options: [
            {label: "Quarantine (recommended)", description: "Mark story blocked, continue with next story — never advances with red tests"},
            {label: "Revert quality fixes", description: "Revert test quality changes and proceed"},
            {label: "Continue anyway", description: "Proceed to quality gate with failing tests"},
            {label: "Manual fix", description: "Pause for manual intervention"},
            {label: "Stop", description: "Save state and exit"}
          ]
        )
        Handle gate_escalation accordingly (Quarantine -> mark story blocked + CONTINUE to next story)
  ELSE:
    Output: "VERIFICATION GATE 7.5 PASSED: All tests green after quality review"
    BREAK from loop
  END IF

END WHILE

PROCEED TO PHASE 8
```
