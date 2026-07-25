# Phase 3: ATDD - Generate Acceptance Tests (opus)

**Execute when:** `phase == "premortem_complete"` OR `phase == "testarch_atdd"`
**Skipped for documentation stories** — no automated acceptance tests needed.

This phase generates FAILING acceptance tests before implementation (TDD RED phase).

```
# Skip Phase 3 for documentation stories
IF story_type == "documentation":
  Output: "=== [Phase 3/8] SKIPPING ATDD — documentation story ==="
  Output: "   Documentation stories don't require acceptance tests in RED state"
  Update session:
    - phase: "atdd_complete"
    - tdd_phase: "skipped"
  Write sprint-status.yaml
  Output: "ATDD phase skipped — proceeding to implementation"
  PROCEED TO PHASE 4
END IF

Output: "=== [Phase 3/8] TDD RED Phase - Generating acceptance tests: {story_key} (opus) ==="

Update session:
  - phase: "testarch_atdd"
  - tdd_phase: "red"
  - last_updated: {timestamp}

Write sprint-status.yaml

# Step C — money-path reasoning nudge (epics 2/4/6/7 = parity/Death-A critical).
# `ultrathink` is a per-turn reasoning lever (NOT billed effort; H-13). Gate it to the
# catastrophic epics so we spend deep reasoning only where a wrong answer is fatal.
SET epic_num = integer prefix of {story_key}   # e.g. "2.4" -> 2, "11.3" -> 11
SET ultrathink_nudge = "ultrathink\n\n" IF epic_num IN {2, 4, 6, 7} ELSE ""

Task(
  subagent_type="epic-atdd-writer",  # ISOLATED: No implementation knowledge
  model="opus",
  description="Generate ATDD tests for {story_key}",
  prompt="{ultrathink_nudge}Generate ATDD tests for story {story_key} (TDD RED phase).

Context:
- Story file: {sprint_artifacts}/stories/{story_key}.md
- Phase: 3 (ATDD)

CRITICAL: You are isolated from implementation details. Focus ONLY on acceptance criteria.
Execute the bmad-tea-testarch-atdd workflow.
All tests MUST fail initially (RED state).
Return ONLY JSON: {checklist_file, tests_created, test_files, acs_covered, status}"
)

Parse ATDD output

Verify tests are FAILING (optional quick validation):
```bash
# Run tests to confirm RED state
cd {project_root}
pnpm test --run 2>&1 | tail -20  # Should show failures
```

Update session:
  - phase: "atdd_complete"
  - atdd_checklist_file: {checklist_file}
  - atdd_tests_count: {tests_created}
  - tdd_phase: "red"

Write sprint-status.yaml

Output: "ATDD tests generated: {tests_created} tests (RED - all failing as expected)"
Output: "Checklist: {checklist_file}"

PROCEED TO PHASE 4
```
