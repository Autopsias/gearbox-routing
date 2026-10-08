## TASKLIST INTEGRATION

**Purpose:** Track parallel quality check workflow with task visibility for commit orchestration.

### INITIALIZATION: Create Quality Check Tasks

When starting commit orchestration, create tasks for parallel quality checks:

```
# At STEP 5 (before launching parallel agents):

TaskCreate(
  subject="Pre-commit Quality Analysis",
  description="Analyze repository state and detect quality issues",
  activeForm="Analyzing commit"
) -> task_id_analysis

TaskCreate(
  subject="Linting Check",
  description="Run linting-fixer for code style and formatting",
  activeForm="Checking linting"
) -> task_id_lint
TaskUpdate(taskId=task_id_lint, addBlockedBy=[task_id_analysis])

TaskCreate(
  subject="Type Check",
  description="Run type-error-fixer for type validation",
  activeForm="Checking types"
) -> task_id_types
TaskUpdate(taskId=task_id_types, addBlockedBy=[task_id_analysis])

TaskCreate(
  subject="Security Scan",
  description="Run security-scanner for vulnerability detection",
  activeForm="Scanning security"
) -> task_id_security
TaskUpdate(taskId=task_id_security, addBlockedBy=[task_id_analysis])

TaskCreate(
  subject="Test Validation",
  description="Run unit-test-fixer for test validation",
  activeForm="Validating tests"
) -> task_id_tests
TaskUpdate(taskId=task_id_tests, addBlockedBy=[task_id_analysis])

TaskCreate(
  subject="Stage and Commit",
  description="Stage quality-fixed files and execute commit",
  activeForm="Committing changes"
) -> task_id_commit
TaskUpdate(taskId=task_id_commit, addBlockedBy=[task_id_lint, task_id_types, task_id_security, task_id_tests])

Output: "Created 6 tasks for commit quality workflow"
```

### PARALLEL EXECUTION: Track Quality Agents

```
# When analysis starts:
TaskUpdate(taskId=task_id_analysis, status="in_progress")
TaskUpdate(taskId=task_id_analysis, status="completed", metadata={"issues_detected": N})

# When launching parallel agents (all start simultaneously):
TaskUpdate(taskId=task_id_lint, status="in_progress")
TaskUpdate(taskId=task_id_types, status="in_progress")
TaskUpdate(taskId=task_id_security, status="in_progress")
TaskUpdate(taskId=task_id_tests, status="in_progress")

# When each agent completes:
TaskUpdate(taskId=task_id_lint, status="completed", metadata={
  "status": "fixed|partial|failed",
  "issues_fixed": N,
  "files_modified": ["path/to/file.py"]
})
# Repeat for types, security, tests
```

### COMMIT EXECUTION: Final Stage

```
# When all quality checks pass:
TaskUpdate(taskId=task_id_commit, status="in_progress")

# After successful commit:
TaskUpdate(taskId=task_id_commit, status="completed", metadata={
  "commit_hash": "{hash}",
  "commit_message": "{subject}",
  "files_committed": N
})

# If commit fails:
TaskUpdate(taskId=task_id_commit, status="completed", metadata={
  "result": "failed",
  "error": "pre-commit hooks failed"
})
```

### SKIP-HOOKS MODE

When --skip-hooks is specified, create simplified task chain:

```
IF --skip-hooks:
  TaskCreate(subject="Fast Commit (skip hooks)", description="Direct commit without validation", activeForm="Committing")
  # Skip quality check tasks
END IF
```
