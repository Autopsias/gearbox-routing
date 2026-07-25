# Error Handling & Troubleshooting

## ERROR HANDLING

On any workflow failure:

```
1. Capture error output
2. Update session:
   - phase: "error"
   - last_error: "{error_message}"
3. Write sprint-status.yaml

4. Display error with phase context:
   Output: "ERROR in Phase {current_phase}: {error_message}"

5. In unattended mode (--force-model in arguments):
   Output: "RALPH_BLOCKED: Phase {current_phase} error: {error_message}"
   Exit cleanly (let runner handle retry)

6. In interactive mode, offer recovery options:
   error_decision = AskUserQuestion(
     question: "How to handle this error?",
     header: "Error Recovery",
     options: [
       {label: "Retry", description: "Re-run the failed phase"},
       {label: "Skip phase", description: "Skip to next phase (if safe)"},
       {label: "Skip story", description: "Mark story skipped, continue to next"},
       {label: "Stop", description: "Save state and exit"}
     ]
   )

7. Handle recovery choice:
   - Retry: Reset phase state, re-execute
   - Skip phase: Only allowed for non-critical phases (6, 7)
   - Skip story: Mark skipped in sprint-status, continue loop
   - Stop: HALT with resume instructions
```

---

## TASKLIST INTEGRATION

**Purpose:** Track 8-phase TDD/ATDD workflow progress with task dependencies and status visibility.

### INITIALIZATION: Create Phase Tasks at Workflow Start

When starting a new story (not resuming), create all 8 phase tasks with dependencies:

```
# At STEP 5 start (before entering story processing loop):

IF NOT resuming_session:
  # Create all 8 phase tasks with blockedBy dependencies
  TaskCreate(
    subject="Phase 1: Create Story {story_key}",
    description="Create story file using PM agent (opus model)",
    activeForm="Creating story"
  ) -> task_id_1

  TaskCreate(
    subject="Phase 2: Validate Story {story_key}",
    description="Validate story completeness using validator agent (sonnet model)",
    activeForm="Validating story"
  ) -> task_id_2
  TaskUpdate(taskId=task_id_2, addBlockedBy=[task_id_1])

  TaskCreate(
    subject="Phase 3: ATDD Tests (RED) {story_key}",
    description="Generate failing acceptance tests using ATDD writer (opus model)",
    activeForm="Generating ATDD tests"
  ) -> task_id_3
  TaskUpdate(taskId=task_id_3, addBlockedBy=[task_id_2])

  TaskCreate(
    subject="Phase 4: Dev Story (GREEN) {story_key}",
    description="Implement story to make tests pass using implementer (sonnet model)",
    activeForm="Implementing story"
  ) -> task_id_4
  TaskUpdate(taskId=task_id_4, addBlockedBy=[task_id_3])

  TaskCreate(
    subject="Phase 5: Code Review {story_key}",
    description="Adversarial code review using reviewer (opus model)",
    activeForm="Reviewing code"
  ) -> task_id_5
  TaskUpdate(taskId=task_id_5, addBlockedBy=[task_id_4])

  TaskCreate(
    subject="Phase 6: Test Expansion {story_key}",
    description="Expand test coverage using test expander (sonnet model)",
    activeForm="Expanding tests"
  ) -> task_id_6
  TaskUpdate(taskId=task_id_6, addBlockedBy=[task_id_5])

  TaskCreate(
    subject="Phase 7: Test Quality Review {story_key}",
    description="Review test quality using test reviewer (haiku model)",
    activeForm="Reviewing test quality"
  ) -> task_id_7
  TaskUpdate(taskId=task_id_7, addBlockedBy=[task_id_6])

  TaskCreate(
    subject="Phase 8: Quality Gate {story_key}",
    description="Make quality gate decision using validator (opus model)",
    activeForm="Making quality gate decision"
  ) -> task_id_8
  TaskUpdate(taskId=task_id_8, addBlockedBy=[task_id_7])

  Output: "Created 8 phase tasks for story {story_key}"
END IF
```

### PHASE TRANSITIONS: Update Task Status

```
# When entering each phase:
TaskUpdate(taskId=phase_task_id, status="in_progress")

# When completing each phase:
TaskUpdate(taskId=phase_task_id, status="completed")

# Verification gates (4.5, 5.5, 6.5, 7.5) use metadata:
TaskUpdate(taskId=phase_task_id, metadata={"verification_iteration": N, "tests_passing": true/false})
```

### LOOP-BACK HANDLING

When quality gate returns CONCERNS/FAIL and user chooses "Loop back to dev":

```
# Reset phases 4-8 to pending (they'll be re-executed)
TaskUpdate(taskId=task_id_4, status="pending")
TaskUpdate(taskId=task_id_5, status="pending")
TaskUpdate(taskId=task_id_6, status="pending")
TaskUpdate(taskId=task_id_7, status="pending")
TaskUpdate(taskId=task_id_8, status="pending", metadata={"gate_iteration": N})
```
