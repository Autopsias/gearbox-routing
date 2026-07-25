# Phase 5: Code Review (opus, max 3 iterations)

**Execute when:** `phase == "dev_complete"` OR `phase == "code_review"`

This phase performs adversarial code/prose review finding 3-10 specific issues.

```
# For documentation stories, use prose review instead of code review
IF story_type == "documentation":
  Output: "=== [Phase 5/8] Prose Review: {story_key} (opus) ==="

  Update session:
    - phase: "code_review"
    - review_iteration: 0
    - last_updated: {timestamp}

  Write sprint-status.yaml

  Task(
    subagent_type="epic-code-reviewer",
    model="opus",
    description="Prose review for {story_key}",
    prompt="Review documentation for story {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

This is a DOCUMENTATION story. Review for:
- Completeness: all required sections and acceptance criteria addressed
- Clarity: unambiguous language, correct terminology
- Structure: logical flow, appropriate headers, consistent formatting
- Accuracy: technical accuracy, no broken references
MUST find 3-10 specific issues. NEVER report zero issues.
Return ONLY JSON: {total_issues, high_issues, medium_issues, low_issues, auto_fixable}"
  )

  Parse review JSON output

  IF high_count > 0 OR medium_count > 0:
    Task(
      subagent_type="epic-implementer",
      model="opus",
      description="Fix prose review issues for {story_key}",
      prompt="Fix HIGH and MEDIUM priority prose review issues for documentation story {story_key}.

Issues to Fix:
{high_issues}
{medium_issues}

Apply fixes using Edit tool. Documentation quality matters.
Return ONLY JSON: {fixes_applied, status}"
    )

  Update session:
    - phase: "review_complete"

  Write sprint-status.yaml

  PROCEED TO VERIFICATION GATE 5.5
END IF

INITIALIZE:
  review_iteration = session.review_iteration or 0
  max_reviews = 3

# Step C — money-path reasoning nudge (epics 2/4/6/7 = parity/Death-A critical).
# `ultrathink` is a per-turn reasoning lever (NOT billed effort; H-13). Gate it to the
# catastrophic epics so deep reasoning is spent only where a wrong review is fatal.
SET epic_num = integer prefix of {story_key}   # e.g. "2.4" -> 2, "11.3" -> 11
SET ultrathink_nudge = "ultrathink\n\n" IF epic_num IN {2, 4, 6, 7} ELSE ""

WHILE review_iteration < max_reviews:

  Output: "=== [Phase 5/8] Code Review iteration {review_iteration + 1}: {story_key} (opus) ==="

  Update session:
    - phase: "code_review"
    - review_iteration: {review_iteration}
    - last_updated: {timestamp}

  Write sprint-status.yaml

  Task(
    subagent_type="epic-code-reviewer",
    model="opus",
    description="Code review for {story_key}",
    prompt="{ultrathink_nudge}Review implementation for {story_key} (iteration {review_iteration + 1}).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- Implementation notes: {sprint_artifacts}/stories/{story_key}-implementation-notes.md (READ THIS FIRST, before the diff — it logs the implementer's design decisions, intentional deviations, tradeoffs, and open questions; a finding that only restates something already logged there is not a surprise, it's a confirmed tradeoff)

Execute the bmad-bmm-code-review workflow.
MUST find 3-10 specific issues. NEVER report zero issues.
A finding that merely restates a deviation already logged in implementation-notes.md classifies as ask-user, never a blocking (HIGH/MEDIUM) finding — file it under a new `ask_user_issues` bucket instead of high/medium.
Return ONLY JSON: {total_issues, high_issues, medium_issues, low_issues, ask_user_issues, auto_fixable}"
  )

  Parse review JSON output

  IF total_issues == 0 OR (high_count == 0 AND medium_count == 0):
    # Only LOW issues or no issues
    IF low_count > 0:
      Output: "Review found {low_count} LOW priority issues only."

      low_decision = AskUserQuestion(
        question: "How to handle LOW priority issues?",
        header: "Low Issues",
        options: [
          {label: "Fix all", description: "Fix all {low_count} low priority issues"},
          {label: "Skip", description: "Accept low issues and proceed"}
        ]
      )

      IF low_decision == "Fix all":
        # Apply low fixes
        Task(
          subagent_type="epic-implementer",
          model="opus",
          description="Fix low priority review issues for {story_key}",
          prompt="Fix LOW priority code review issues for {story_key}.

Issues to Fix:
{low_issues}

Apply fixes using Edit tool. Run pnpm prepush after.
Return ONLY JSON: {fixes_applied, prepush_status, tests_passing}"
        )
        review_iteration += 1
        CONTINUE loop
      ELSE:
        BREAK from loop
    ELSE:
      Output: "Code review PASSED - No blocking issues found."
      BREAK from loop

  ELSE:
    # HIGH or MEDIUM issues found
    Output:
    ───────────────────────────────────────────────────────────
    CODE REVIEW FINDINGS
    ───────────────────────────────────────────────────────────
    Total Issues: {total_issues}

    HIGH ({high_count}): {list high issues}
    MEDIUM ({medium_count}): {list medium issues}
    LOW ({low_count}): {list low issues}
    ───────────────────────────────────────────────────────────

    # Auto-fix HIGH and MEDIUM issues
    Output: "Auto-fixing {high_count + medium_count} HIGH/MEDIUM issues..."

    Task(
      subagent_type="epic-implementer",
      model="opus",
      description="Fix review issues for {story_key}",
      prompt="Fix HIGH and MEDIUM priority code review issues for {story_key}.

HIGH PRIORITY (must fix):
{high_issues}

MEDIUM PRIORITY (should fix):
{medium_issues}

Apply all fixes. Run pnpm prepush after.
Return ONLY JSON: {fixes_applied, prepush_status, tests_passing}"
    )

    review_iteration += 1
    CONTINUE loop

END WHILE

IF review_iteration >= max_reviews:
  Output: "Maximum review iterations ({max_reviews}) reached."
  escalation = AskUserQuestion(
    question: "Review limit reached. How to proceed?",
    options: [
      {label: "Continue", description: "Accept current state and proceed"},
      {label: "Manual fix", description: "Pause for manual intervention"}
    ]
  )
  Handle escalation

Update session:
  - phase: "review_complete"

Write sprint-status.yaml

PROCEED TO VERIFICATION GATE 5.5
```

---

## VERIFICATION GATE 5.5: Post-Code-Review Test Verification

**Purpose**: Verify all tests still pass after code review fixes. Code review may apply fixes that break tests.
**Skipped for documentation stories** — no automated tests to verify.

```
# Skip Gate 5.5 for documentation stories
IF story_type == "documentation":
  Output: "=== [Gate 5.5] SKIPPING — documentation story (no tests to verify) ==="
  PROCEED TO PHASE 6
END IF

# SPEED (B): code review often applies NO fix. If it changed no code/tests since the last green
# gate (4.5), the suite is provably still green — skip this full run. Detect via git: autonomous
# --auto commits per phase so HEAD == last green state. Scope to apps/api (code+tests) so story-file
# updates don't defeat it. FAIL-SAFE: any apps/api diff, or git unavailable/errors -> run normally.
IF `git -C {project_root} diff --quiet HEAD -- apps/api` exits 0 (no code/test change):
  Output: "=== [Gate 5.5] SKIPPED — code review changed no code/tests since Gate 4.5 (still green) ==="
  PROCEED TO PHASE 6
END IF

Output: "=== [Gate 5.5] Verifying test state after code review fixes ==="

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
    Output: "VERIFICATION ITERATION {verification_iteration}/{max_verification_iterations}: Tests failing after code review"

    IF verification_iteration < max_verification_iterations:
      # Ralph-style: Feed failures back to implementer
      Task(
        subagent_type="epic-test-fixer",
        model="opus",
        description="Fix post-review test failures (iteration {verification_iteration})",
        prompt="Fix test failures caused by code review changes for story {story_key} (iteration {verification_iteration}).

Test failure output (last 50 lines):
{TEST_OUTPUT tail -50}

The code review phase applied fixes that may have broken tests.
Fix the failing tests without reverting the review improvements.
Run tests after fixing. Return JSON: {fixes_applied, tests_passing, status}"
      )
    ELSE:
      # Max iterations reached
      Output: "ERROR: Gate 5.5 - Max verification iterations ({max_verification_iterations}) reached"

      IF "--force-model" in arguments:
        # FAIL-CLOSED (A): unattended runs never advance a story with red tests. Quarantine it.
        Output: "STORY_BLOCKED: Gate 5.5 red after {max_verification_iterations} fix attempts for {story_key} — quarantined, NOT advanced to Phase 6."
        Update session: story {story_key} status -> "blocked", gate_blocked: "5.5", phase: "blocked", last_error: "Gate 5.5 red after {max_verification_iterations} attempts"
        CONTINUE to next story   # surface for a human fix + /epic-dev {epic_num} --full --resume
      ELSE:
        gate_escalation = AskUserQuestion(
          question: "Verification gate 5.5 failed after 3 iterations. How to proceed?",
          header: "Gate 5.5 Failed",
          options: [
            {label: "Quarantine (recommended)", description: "Mark story blocked, continue with next story — never advances with red tests"},
            {label: "Continue anyway", description: "Proceed to test expansion with failing tests (risky)"},
            {label: "Revert review changes", description: "Revert code review fixes and proceed"},
            {label: "Manual fix", description: "Pause for manual intervention"},
            {label: "Stop", description: "Save state and exit"}
          ]
        )
        Handle gate_escalation accordingly (Quarantine -> mark story blocked + CONTINUE to next story)
  ELSE:
    Output: "VERIFICATION GATE 5.5 PASSED: All tests green after code review"
    BREAK from loop
  END IF

END WHILE

PROCEED TO PHASE 6
```
