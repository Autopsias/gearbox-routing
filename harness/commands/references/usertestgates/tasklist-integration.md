## TASKLIST INTEGRATION

**Purpose:** Track test gate discovery and execution with dynamic task creation.

## Contents

- [INITIALIZATION: Create Discovery Tasks](#initialization-create-discovery-tasks)
- [DYNAMIC GATE TASK CREATION](#dynamic-gate-task-creation)
- [GATE EXECUTION TRACKING](#gate-execution-tracking)
- [STORY READINESS TRACKING](#story-readiness-tracking)
- [COMPLETION SUMMARY](#completion-summary)

### INITIALIZATION: Create Discovery Tasks

When starting test gate finder, create discovery tasks:

```
# At Step 1 (Discovery):

TaskCreate(
  subject="Discover Test Gates",
  description="Run testgates_discovery.py to get test gate configuration",
  activeForm="Discovering gates"
) → task_id_discover

TaskCreate(
  subject="Check Gate Status",
  description="Determine which gates have already passed",
  activeForm="Checking status"
) → task_id_status
TaskUpdate(taskId=task_id_status, addBlockedBy=[task_id_discover])

TaskCreate(
  subject="Find Next Gate",
  description="Identify next gate with prerequisites met",
  activeForm="Finding next gate"
) → task_id_find
TaskUpdate(taskId=task_id_find, addBlockedBy=[task_id_status])
```

### DYNAMIC GATE TASK CREATION

After discovering gates, create a task for each:

```
# At Step 2 (after parsing /tmp/testgates_config.json):

FOR gate_id IN sorted(gates.keys()):
  TaskCreate(
    subject="Gate {gate_id}: {gate_name}",
    description="Execute test gate validation for {gate_id}",
    activeForm="Validating {gate_id}"
  ) → gate_task_id

  # Add dependencies based on gate 'requires' field
  IF gate.requires:
    TaskUpdate(taskId=gate_task_id, addBlockedBy=[required_gate_task_ids])
  END IF

  # Check if gate already passed
  IF gate_passed(gate_id):
    TaskUpdate(taskId=gate_task_id, status="completed", metadata={"passed": true})
  END IF
END FOR
```

### GATE EXECUTION TRACKING

```
# When executing a gate (Step 5):
TaskUpdate(taskId=gate_task_id, status="in_progress")

# For NON-INTERACTIVE gates:
TaskUpdate(taskId=gate_task_id, status="completed", metadata={
  "exit_code": 0,  # 0=PROCEED, 1=REFINE, 2=ESCALATE
  "result": "PROCEED|REFINE|ESCALATE",
  "report_file": "path/to/report.md"
})

# For INTERACTIVE gates:
TaskUpdate(taskId=gate_task_id, metadata={"phase": "parse"})
# ... collect user answers ...
TaskUpdate(taskId=gate_task_id, metadata={"phase": "report", "checks_passed": N})
TaskUpdate(taskId=gate_task_id, status="completed", metadata={
  "result": "PROCEED|REFINE|ESCALATE",
  "checks_total": 30,
  "checks_passed": 28
})
```

### STORY READINESS TRACKING

**Naming note:** `STORY_NOT_READY` (bash output), "Story [X.Y] NOT IMPLEMENTED" (user-facing
text), and `story_ready` (task metadata field below) are three surfaces of the same
story-readiness state — don't rename these machine tokens.

```
# At Step 3.5 (story implementation check):
TaskUpdate(taskId=gate_task_id, metadata={
  "story_ready": true|false,
  "missing_files": ["file1.py", "file2.py"]
})

IF NOT story_ready:
  TaskUpdate(taskId=gate_task_id, status="completed", metadata={
    "skipped": true,
    "reason": "Story not implemented",
    "action": "Complete story implementation first"
  })
END IF
```

### COMPLETION SUMMARY

```
# At workflow end:
tasks = TaskList()

passed_count = count(t for t in tasks if t.status == "completed" and t.metadata.get("result") == "PROCEED")
remaining_count = count(t for t in tasks if t.status != "completed")

Output: "📋 Gate Summary:"
Output: "  ✅ Passed: {passed_count}"
Output: "  ⏳ Remaining: {remaining_count}"
```
