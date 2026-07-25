## TASKLIST INTEGRATION

**Purpose:** Track 5-phase UI testing workflow with task dependencies and status visibility.

### INITIALIZATION: Create Phase Tasks at Session Start

When starting a new testing session (not resuming), create all phase tasks:

```
# At Phase 1 (Session Initialization):

TaskCreate(
  subject="Phase 0: UI Discovery - {target}",
  description="Analyze project UI and generate clarifying questions using ui-test-discovery agent",
  activeForm="Discovering UI"
) -> task_id_0

TaskCreate(
  subject="Phase 1: Session Setup - {session_id}",
  description="Create session directory structure and copy UI objectives",
  activeForm="Setting up session"
) -> task_id_1
TaskUpdate(taskId=task_id_1, addBlockedBy=[task_id_0])

TaskCreate(
  subject="Phase 2: Requirements Analysis - {target}",
  description="Extract UI requirements and design scenarios using requirements-analyzer and scenario-designer",
  activeForm="Analyzing requirements"
) -> task_id_2
TaskUpdate(taskId=task_id_2, addBlockedBy=[task_id_1])

TaskCreate(
  subject="Phase 3: UI Test Execution - {target}",
  description="Execute browser tests using chrome-browser-executor",
  activeForm="Executing UI tests"
) -> task_id_3
TaskUpdate(taskId=task_id_3, addBlockedBy=[task_id_2])

TaskCreate(
  subject="Phase 4: Evidence & Reporting - {target}",
  description="Collect evidence and generate BMAD report using evidence-collector and bmad-reporter",
  activeForm="Generating reports"
) -> task_id_4
TaskUpdate(taskId=task_id_4, addBlockedBy=[task_id_3])

Output: "Created 5 phase tasks for UI testing session {session_id}"
```

### PHASE TRANSITIONS: Update Task Status

```
# Phase 0: UI Discovery
TaskUpdate(taskId=task_id_0, status="in_progress")
# After ui-test-discovery completes:
TaskUpdate(taskId=task_id_0, status="completed", metadata={
  "ui_entry_points": {count},
  "workflows_discovered": {count},
  "clarification_questions": {count}
})

# Phase 1: Session Setup
TaskUpdate(taskId=task_id_1, status="in_progress")
TaskUpdate(taskId=task_id_1, status="completed", metadata={
  "session_dir": "{path}",
  "session_id": "{id}"
})

# Phase 2: Requirements Analysis
TaskUpdate(taskId=task_id_2, status="in_progress")
# Sub-tasks for requirements chain:
TaskCreate(subject="Extract requirements", description="Run requirements-analyzer", activeForm="Extracting")
TaskCreate(subject="Design scenarios", description="Run scenario-designer", activeForm="Designing") -> blockedBy: [extract]
TaskUpdate(taskId=task_id_2, status="completed", metadata={
  "requirements_extracted": {count},
  "scenarios_designed": {count}
})

# Phase 3: UI Test Execution
TaskUpdate(taskId=task_id_3, status="in_progress")
TaskUpdate(taskId=task_id_3, status="completed", metadata={
  "scenarios_executed": {count},
  "evidence_files": {count},
  "issues_found": {count}
})

# Phase 4: Evidence & Reporting
TaskUpdate(taskId=task_id_4, status="in_progress")
TaskUpdate(taskId=task_id_4, status="completed", metadata={
  "bmad_report_generated": true,
  "evidence_summary_generated": true,
  "coverage_percentage": {percentage}
})
```

### SESSION RESUME SUPPORT

For --resume operations:

```
IF --resume {session_id}:
  # Read session state to determine last completed phase
  session_state = Read("{session_dir}/session_state.json")

  # Create tasks only for remaining phases
  IF session_state.phase < 2:
    TaskCreate(subject="Phase 2: Requirements Analysis (resumed)", ...)
  IF session_state.phase < 3:
    TaskCreate(subject="Phase 3: UI Test Execution (resumed)", ...)
  IF session_state.phase < 4:
    TaskCreate(subject="Phase 4: Evidence & Reporting (resumed)", ...)

  Output: "Resuming from Phase {session_state.phase + 1}"
END IF
```

### SESSION LIFECYCLE METADATA

Track session state in task metadata:

```
# Session states map to task status:
# - initialized -> Phase 1 in_progress
# - phase_0 -> Phase 0 completed, Phase 1 in_progress
# - phase_1 -> Phase 1 completed, Phase 2 in_progress
# - phase_2 -> Phase 2 completed, Phase 3 in_progress
# - phase_3 -> Phase 3 completed, Phase 4 in_progress
# - completed -> All phases completed
# - failed -> Error state with metadata

TaskUpdate(taskId=current_phase_task, metadata={
  "session_state": "{state}",
  "duration_ms": {elapsed},
  "agent_invocations": {count}
})
```

### ERROR RECOVERY

On phase failure:

```
TaskUpdate(taskId=failed_phase_task, status="completed", metadata={
  "result": "failed",
  "error": "{error_message}",
  "recovery_options": ["retry", "skip", "manual"]
})

Output: "Phase {N} failed. Use /user_testing --resume {session_id} to retry"
```
