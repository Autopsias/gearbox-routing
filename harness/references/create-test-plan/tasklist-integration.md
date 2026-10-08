## TASKLIST INTEGRATION

**Purpose:** Track 6-step test plan creation process with task visibility.

## Contents

- [INITIALIZATION: Create Step Tasks](#initialization-create-step-tasks)
- [STEP TRANSITIONS: Update Task Status](#step-transitions-update-task-status)
- [COMPLETION SUMMARY](#completion-summary)

### INITIALIZATION: Create Step Tasks

When starting test plan creation, create all step tasks:

```
# At workflow start:

TaskCreate(
  subject="Step 0: Detect Project Structure",
  description="Detect documentation and output directories",
  activeForm="Detecting structure"
) → task_id_0

TaskCreate(
  subject="Step 1: Check Existing Plan",
  description="Check if test plan already exists for {functionalityMatch}",
  activeForm="Checking existing"
) → task_id_1
TaskUpdate(taskId=task_id_1, addBlockedBy=[task_id_0])

TaskCreate(
  subject="Step 2: Requirements Analysis",
  description="Comprehensive analysis of all relevant documentation",
  activeForm="Analyzing requirements"
) → task_id_2
TaskUpdate(taskId=task_id_2, addBlockedBy=[task_id_1])

TaskCreate(
  subject="Step 3: Test Scenario Design",
  description="Design automated/interactive/hybrid test scenarios",
  activeForm="Designing scenarios"
) → task_id_3
TaskUpdate(taskId=task_id_3, addBlockedBy=[task_id_2])

TaskCreate(
  subject="Step 4: Validation Criteria",
  description="Define measurable success criteria and evidence requirements",
  activeForm="Defining criteria"
) → task_id_4
TaskUpdate(taskId=task_id_4, addBlockedBy=[task_id_3])

TaskCreate(
  subject="Step 5: Agent Prompt Generation",
  description="Create specialized prompts for each subagent",
  activeForm="Generating prompts"
) → task_id_5
TaskUpdate(taskId=task_id_5, addBlockedBy=[task_id_4])

TaskCreate(
  subject="Step 6: Write Test Plan File",
  description="Generate comprehensive test plan markdown file",
  activeForm="Writing test plan"
) → task_id_6
TaskUpdate(taskId=task_id_6, addBlockedBy=[task_id_5])

Output: "📋 Created 7 tasks for test plan creation workflow"
```

### STEP TRANSITIONS: Update Task Status

```
# Step 0: Structure Detection
TaskUpdate(taskId=task_id_0, status="in_progress")
TaskUpdate(taskId=task_id_0, status="completed", metadata={
  "docs_dirs": ["docs", "documentation"],
  "plans_dir": "workspace/testing/plans"
})

# Step 1: Existing Plan Check
TaskUpdate(taskId=task_id_1, status="in_progress")
IF plan_exists AND NOT overwrite:
  TaskUpdate(taskId=task_id_1, status="completed", metadata={
    "result": "blocked",
    "reason": "Plan exists, use --overwrite"
  })
  # Mark remaining tasks as skipped
  FOR task IN [task_id_2, task_id_3, task_id_4, task_id_5, task_id_6]:
    TaskUpdate(taskId=task, status="completed", metadata={"skipped": true})
  END FOR
  EXIT
ELSE:
  TaskUpdate(taskId=task_id_1, status="completed", metadata={"result": "proceed"})
END IF

# Step 2: Requirements Analysis
TaskUpdate(taskId=task_id_2, status="in_progress")
TaskUpdate(taskId=task_id_2, status="completed", metadata={
  "documents_analyzed": N,
  "acceptance_criteria": N,
  "user_stories": N,
  "integration_points": N
})

# Step 3: Scenario Design
TaskUpdate(taskId=task_id_3, status="in_progress")
TaskUpdate(taskId=task_id_3, status="completed", metadata={
  "automated_scenarios": N,
  "interactive_scenarios": N,
  "hybrid_scenarios": N
})

# Step 4: Validation Criteria
TaskUpdate(taskId=task_id_4, status="in_progress")
TaskUpdate(taskId=task_id_4, status="completed", metadata={
  "success_thresholds": N,
  "evidence_requirements": N
})

# Step 5: Agent Prompts
TaskUpdate(taskId=task_id_5, status="in_progress")
TaskUpdate(taskId=task_id_5, status="completed", metadata={
  "prompts_generated": 7,  # All 7 subagent prompts
  "agents": ["requirements-analyzer", "scenario-designer", "validation-planner", ...]
})

# Step 6: Write Plan File
TaskUpdate(taskId=task_id_6, status="in_progress")
TaskUpdate(taskId=task_id_6, status="completed", metadata={
  "plan_file": "{plans_dir}/{functionalityMatch}-test-plan.md",
  "file_size": N
})
```

### COMPLETION SUMMARY

```
# After Step 6 completes:
Output: "📋 Test Plan Creation Complete:"
Output: "  [✅] Step 0: Structure detected"
Output: "  [✅] Step 1: No existing plan"
Output: "  [✅] Step 2: {N} documents analyzed"
Output: "  [✅] Step 3: {N} scenarios designed"
Output: "  [✅] Step 4: Validation criteria defined"
Output: "  [✅] Step 5: 7 agent prompts generated"
Output: "  [✅] Step 6: Plan file created"
```
