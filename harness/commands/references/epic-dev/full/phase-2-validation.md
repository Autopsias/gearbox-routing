# Phase 2: Validate Create Story (sonnet, max 3 iterations)

**Execute when:** `phase == "create_complete"` OR `phase == "validation"`

This phase validates the story file for completeness using tier-based issue classification.

```
INITIALIZE:
  validation_iteration = session.validation_iteration or 0
  max_validations = 3

WHILE validation_iteration < max_validations:

  Output: "=== [Phase 2/8] Validation iteration {validation_iteration + 1} for: {story_key} (sonnet) ==="

  Update session:
    - phase: "validation"
    - validation_iteration: {validation_iteration}
    - last_updated: {timestamp}

  Write sprint-status.yaml

  Task(
    subagent_type="epic-story-validator",
    model="sonnet",
    description="Validate story {story_key}",
    prompt="Validate story {story_key} (iteration {validation_iteration + 1}).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- Epic: {epic_num}

Check: story header, BDD acceptance criteria, task links, dev notes, testing requirements.
Return ONLY JSON: {pass_rate, total_issues, critical_issues, enhancement_issues, optimization_issues}"
  )

  Parse validation JSON output

  IF pass_rate == 100 OR total_issues == 0:
    Output: "Story validation PASSED (100%)"
    Update session:
      - phase: "validation_complete"
      - validation_last_pass_rate: 100
    Write sprint-status.yaml
    BREAK from loop
    PROCEED TO PHASE 3

  ELSE:
    # Display issues by tier
    Output:
    ───────────────────────────────────────────────────────────
    VALIDATION ISSUES FOUND
    ───────────────────────────────────────────────────────────
    Pass Rate: {pass_rate}% | Total Issues: {total_issues}

    CRITICAL ({critical_count}): {list critical issues}
    ENHANCEMENT ({enhancement_count}): {list enhancement issues}
    OPTIMIZATION ({optimization_count}): {list optimization issues}
    ───────────────────────────────────────────────────────────

    user_decision = AskUserQuestion(
      question: "How to proceed with validation issues?",
      header: "Validation",
      options: [
        {label: "Fix all", description: "Apply all {total_issues} fixes (recommended)"},
        {label: "Fix critical only", description: "Apply only critical fixes"},
        {label: "Skip validation", description: "Proceed to ATDD with current issues"},
        {label: "Manual review", description: "Pause for manual inspection"}
      ]
    )

    IF user_decision == "Fix all" OR user_decision == "Fix critical only":
      # Apply fixes - use epic-story-creator since it handles story file creation/modification
      Task(
        subagent_type="epic-story-creator",
        model="sonnet",
        description="Fix validation issues for {story_key}",
        prompt="Fix validation issues in story {story_key}.

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- Fix mode: {IF user_decision == 'Fix all': 'ALL ISSUES' ELSE: 'CRITICAL ONLY'}

Issues to Fix:
{IF user_decision == 'Fix all': all_issues ELSE: critical_issues}

Apply fixes using Edit tool. Preserve existing content.
Return ONLY JSON: {fixes_applied, sections_modified}"
      )
      validation_iteration += 1
      CONTINUE loop (re-validate)

    ELSE IF user_decision == "Skip validation":
      Output: "Skipping remaining validation. Proceeding to ATDD phase..."
      Update session:
        - phase: "validation_complete"
      Write sprint-status.yaml
      BREAK from loop
      PROCEED TO PHASE 3

    ELSE IF user_decision == "Manual review":
      Output: "Pausing for manual review."
      Output: "Story file: {sprint_artifacts}/stories/{story_key}.md"
      Output: "Resume with: /epic-dev-full {epic_num} --resume"
      HALT

END WHILE

IF validation_iteration >= max_validations AND pass_rate < 100:
  Output: "Maximum validation iterations ({max_validations}) reached."
  Output: "Current pass rate: {pass_rate}%"

  escalation = AskUserQuestion(
    question: "Validation limit reached. How to proceed?",
    header: "Escalate",
    options: [
      {label: "Continue anyway", description: "Proceed to ATDD with remaining issues"},
      {label: "Manual fix", description: "Pause for manual intervention"},
      {label: "Skip story", description: "Skip this story, continue to next"}
    ]
  )

  Handle escalation choice accordingly
```
