# TaskList Integration Patterns

## Scenario Task Creation Pattern

At session start, parse BROWSER_INSTRUCTIONS.md and create tasks for each test scenario:

```python
# Parse test scenarios from instructions
scenarios = parse_browser_instructions(browser_instructions_path)

# Create parent scenario tasks
for i, scenario in enumerate(scenarios):
    TaskCreate(
        subject=f"Scenario {i+1}: {scenario.title}",
        description=f"Execute browser test: {scenario.description}",
        activeForm=f"Testing {scenario.title}"
    )

    # Set up sequential dependencies
    if i > 0:
        TaskUpdate(taskId=current_task_id, addBlockedBy=[previous_task_id])
```

## Step-Level Sub-Tasks

For each scenario, create step tasks:

```python
def create_scenario_steps(scenario_task_id, steps):
    prev_step_id = None
    for j, step in enumerate(steps):
        step_task = TaskCreate(
            subject=f"Step {j+1}: {step.action}",
            description=step.description,
            activeForm=f"Executing {step.action}"
        )

        # Chain steps sequentially
        if prev_step_id:
            TaskUpdate(taskId=step_task.id, addBlockedBy=[prev_step_id])
        prev_step_id = step_task.id
```

## Evidence Capture Tasks

```python
# Create evidence capture task for each screenshot/capture
TaskCreate(
    subject=f"Capture Evidence: {description}",
    description=f"Screenshot at {timestamp}",
    activeForm=f"Capturing {description}",
    metadata={
        "type": "screenshot",
        "path": evidence_file_path,
        "scenario": scenario_id
    }
)
```

## Execution Status Updates

```python
# When starting a scenario
TaskUpdate(taskId=scenario_task_id, status="in_progress")

# When scenario passes with evidence
TaskUpdate(
    taskId=scenario_task_id,
    status="completed",
    metadata={
        "result": "passed",
        "evidence_files": [file1, file2],
        "duration_ms": elapsed
    }
)

# When scenario fails
TaskUpdate(
    taskId=scenario_task_id,
    status="completed",  # Mark complete but with failed result
    metadata={
        "result": "failed",
        "error": error_message,
        "evidence_files": [error_screenshot],
        "recovery_actions": ["action1", "action2"]
    }
)
```

## Progress Summary in EXECUTION_LOG.md

Include task summary in execution log:

```markdown
## Task Progress Summary
| Task | Status | Evidence | Duration |
|------|--------|----------|----------|
| Scenario 1: Login Flow | PASSED | 3 files | 12.5s |
| Scenario 2: Navigation | FAILED | 2 files | 8.2s |
| Scenario 3: Form Submit | BLOCKED | - | - |
```
