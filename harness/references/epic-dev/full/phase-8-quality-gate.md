# Phase 8: Requirements Traceability & Quality Gate (opus)

**Execute when:** `phase == "test_review_complete"` OR `phase == "testarch_trace"`

This phase generates traceability matrix and makes quality gate decision.
For documentation stories, this checks AC completeness instead of test coverage.

```
Output: "=== [Phase 8/8] Quality Gate Decision: {story_key} (opus) ==="

Update session:
  - phase: "testarch_trace"
  - last_updated: {timestamp}

Write sprint-status.yaml

# Step C — money-path reasoning nudge (epics 2/4/6/7 = parity/Death-A critical).
# `ultrathink` is a per-turn reasoning lever (NOT billed effort; H-13). Gate it to the
# catastrophic epics so deep reasoning is spent only where a wrong gate decision is fatal.
SET epic_num = integer prefix of {story_key}   # e.g. "2.4" -> 2, "11.3" -> 11
SET ultrathink_nudge = "ultrathink\n\n" IF epic_num IN {2, 4, 6, 7} ELSE ""

IF story_type == "documentation":
  Task(
    subagent_type="epic-quality-gate",
    model="opus",
    description="Documentation completeness gate for {story_key}",
    prompt="Make quality gate decision for DOCUMENTATION story {story_key} (Phase 8).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md

This is a DOCUMENTATION story. No test coverage metrics apply.
Instead, evaluate COMPLETENESS:
1. Read the story acceptance criteria
2. Verify each AC is addressed by the written documentation
3. Check for: required sections present, accurate content, proper formatting, no broken references
4. Make gate decision: PASS (all ACs addressed), CONCERNS (minor gaps), FAIL (ACs missing)
Return ONLY JSON: {decision, acs_total, acs_addressed, acs_missing, completeness_pct, rationale}"
  )

ELSE:
  Task(
    subagent_type="epic-quality-gate",
    model="opus",
    description="Quality gate decision for {story_key}",
    prompt="{ultrathink_nudge}Make quality gate decision for story {story_key} (Phase 8).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- ATDD checklist: {session.atdd_checklist_file}

Execute the bmad-tea-testarch-trace workflow.
Generate traceability matrix and make gate decision (PASS/CONCERNS/FAIL).
Return ONLY JSON: {decision, p0_coverage, p1_coverage, overall_coverage, traceability_matrix, gaps, rationale}"
  )
END IF

Parse gate decision

# ═══════════════════════════════════════════════════════════════════════════
# QUALITY GATE DECISION HANDLING
# ═══════════════════════════════════════════════════════════════════════════

Output:
═══════════════════════════════════════════════════════════════════════════
QUALITY GATE RESULT: {decision}
═══════════════════════════════════════════════════════════════════════════
P0 Coverage (Critical): {p0_coverage}% (required: 100%)
P1 Coverage (Important): {p1_coverage}% (target: 90%)
Overall Coverage: {overall_coverage}% (target: 80%)

Rationale: {rationale}
═══════════════════════════════════════════════════════════════════════════

IF decision == "PASS":
  Output: "Quality Gate: PASS - Story ready for completion"

  Update session:
    - phase: "complete"
    - gate_decision: "PASS"
    - p0_coverage: {p0_coverage}
    - p1_coverage: {p1_coverage}
    - overall_coverage: {overall_coverage}

  Write sprint-status.yaml
  PROCEED TO STORY COMPLETION

ELSE IF decision == "CONCERNS":
  Output: "Quality Gate: CONCERNS - Minor gaps detected"

  Update session:
    - phase: "gate_decision"
    - gate_decision: "CONCERNS"

  Write sprint-status.yaml

  IF "--force-model" in arguments:
    # Unattended: CONCERNS = P0 fully covered (100%), only minor P1/overall gaps. This is
    # accept-WITH-FLAG (not a red gate like FAIL) — proceed, but record gate_decision=CONCERNS
    # prominently so the gap is visible for review, never silently "clean".
    Output: "Quality Gate CONCERNS for {story_key} — accepted unattended (P0 covered); recorded gate_decision=CONCERNS for human review."
    Mark story done with gate_decision=CONCERNS (flagged for review)
    CONTINUE to next story
  ELSE:
    concerns_decision = AskUserQuestion(
      question: "Quality gate has CONCERNS. How to proceed?",
      header: "Gate Decision",
      options: [
        {label: "Accept and continue", description: "Acknowledge gaps, mark story done"},
        {label: "Loop back to dev", description: "Fix gaps, re-run phases 4-8"},
        {label: "Skip story", description: "Mark as skipped, continue to next"},
        {label: "Stop", description: "Save state and exit"}
      ]
    )
    Handle concerns_decision (see LOOP-BACK LOGIC below)

ELSE IF decision == "FAIL":
  Output: "Quality Gate: FAIL - Blocking issues detected"
  Output: "Gaps identified:"
  FOR each gap in gaps:
    Output: "  - {gap.ac_id}: {gap.reason}"

  Update session:
    - phase: "gate_decision"
    - gate_decision: "FAIL"

  Write sprint-status.yaml

  IF "--force-model" in arguments:
    # FAIL-CLOSED (A): a FAIL gate (P0 < 100% or critical gaps) NEVER marks a story done unattended.
    Output: "STORY_BLOCKED: Quality Gate FAIL for {story_key} — quarantined, NOT marked done (unattended fail-closed). Resume with /epic-dev {epic_num} --full --resume after fixing."
    Update session: story {story_key} status -> "blocked", gate_blocked: "8", last_error: "Quality gate FAIL"
    CONTINUE to next story
  ELSE:
   fail_decision = AskUserQuestion(
    question: "Quality gate FAILED. How to proceed?",
    header: "Gate Failed",
    options: [
      {label: "Loop back to dev", description: "Fix gaps, re-run phases 4-8"},
      {label: "Request waiver", description: "Document business justification"},
      {label: "Skip story", description: "Mark as skipped, continue to next"},
      {label: "Stop", description: "Save state and exit"}
    ]
  )

  IF fail_decision == "Request waiver":
    Output: "Requesting waiver for quality gate failure."
    Output: "Provide waiver details:"

    waiver_info = AskUserQuestion(
      question: "What is the business justification for waiver?",
      options: [
        {label: "Time-critical", description: "Deadline requires shipping now"},
        {label: "Low risk", description: "Missing coverage is low-risk area"},
        {label: "Tech debt", description: "Will address in future sprint"},
        {label: "Custom", description: "Provide custom justification"}
      ]
    )

    # Mark as WAIVED
    Update session:
      - gate_decision: "WAIVED"
      - waiver_reason: {waiver_info}

    PROCEED TO STORY COMPLETION

  ELSE:
    Handle fail_decision accordingly
```

---

## LOOP-BACK LOGIC

When user chooses "Loop back to dev" after gate FAIL or CONCERNS:

```
Output: "Looping back to Phase 4 (dev-story) to address gaps..."

# Reset tracking for phases 4-8
Update session:
  - phase: "dev_story"
  - review_iteration: 0
  - gate_iteration: {gate_iteration + 1}
  - gate_decision: null

Write sprint-status.yaml

# Provide gap context to dev-story
Task(
  subagent_type="epic-implementer",
  model="opus",
  description="Fix gaps for {story_key}",
  prompt="Fix quality gate gaps for story {story_key} (loop-back iteration {gate_iteration + 1}).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- Previous gate decision: {previous_decision}

GAPS TO ADDRESS:
{FOR each gap in gaps:}
- {gap.ac_id}: {gap.reason}
{END FOR}

Add missing tests and implement missing functionality.
Run pnpm prepush. Ensure P0 coverage reaches 100%.
Return ONLY JSON: {gaps_fixed, tests_added, prepush_status, p0_coverage}"
)

# Continue through phases 5-8 again
PROCEED TO PHASE 5
```
