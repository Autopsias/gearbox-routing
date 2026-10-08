## TASKLIST INTEGRATION (MANDATORY)

## Contents

- [Mode-Specific Task Creation Pattern](#mode-specific-task-creation-pattern)
- [Agent Coordination Tracking](#agent-coordination-tracking)
- [Batch Generation Tracking](#batch-generation-tracking)
- [Rollback Tracking](#rollback-tracking)
- [Progress Summary Pattern](#progress-summary-pattern)

### Mode-Specific Task Creation Pattern

At workflow start, create tasks based on mode:

```python
# For "learn" mode
TaskCreate(subject="Learn Test Patterns", description="Analyze existing test patterns for safe integration", activeForm="Learning patterns")

# For "analyze" mode
TaskCreate(subject="Run Coverage Analysis", description="Execute coverage.py and identify gaps", activeForm="Analyzing coverage")
TaskCreate(subject="Prioritize Gaps", description="Score gaps by business impact and complexity", activeForm="Prioritizing gaps")
TaskCreate(subject="Generate Report", description="Create structured coverage analysis report", activeForm="Generating report")

# For "improve" mode
TaskCreate(subject="Pre-flight Validation", description="Verify existing tests pass", activeForm="Validating baseline")
TaskCreate(subject="Learn Patterns", description="Load test patterns for safe integration", activeForm="Learning patterns")
TaskCreate(subject="Spawn Specialist Agents", description="Coordinate test-fixer agents", activeForm="Spawning agents")
TaskCreate(subject="Post-flight Validation", description="Verify no tests broken", activeForm="Validating integration")

# For "generate" mode
TaskCreate(subject="Load Learned Patterns", description="Load patterns from previous learn execution", activeForm="Loading patterns")
TaskCreate(subject="Pre-flight Safety Check", description="Verify existing tests pass", activeForm="Safety check")
TaskCreate(subject="Generate Tests (Batch 1)", description="Add tests in batches of 5-10", activeForm="Generating tests")
TaskCreate(subject="Integration Validation", description="Run tests after each batch", activeForm="Validating")

# For "validate" mode
TaskCreate(subject="Integration Safety Check", description="Verify no existing tests broken", activeForm="Checking integration")
TaskCreate(subject="Anti-Mocking Theater Audit", description="Check mock-to-assertion ratios", activeForm="Auditing mocks")
TaskCreate(subject="Simplicity Scoring", description="Flag over-engineered tests", activeForm="Scoring simplicity")
TaskCreate(subject="Generate Quality Report", description="Comprehensive quality score report", activeForm="Generating report")
```

### Agent Coordination Tracking

```python
# When spawning specialist agents in "improve" mode
for agent_type in ["unit-test-fixer", "api-test-fixer", "database-test-fixer"]:
    TaskCreate(
        subject=f"Agent: {agent_type}",
        description=f"Fix {domain} test coverage gaps",
        activeForm=f"Running {agent_type}",
        metadata={"agent_type": agent_type, "target_domain": domain}
    )
    TaskUpdate(taskId=task_id, status="in_progress", owner=agent_type)

# After agent completes
TaskUpdate(
    taskId=agent_task_id,
    status="completed",
    metadata={
        "result_status": "success",
        "coverage_delta": "+5.2%",
        "tests_added": 12,
        "tests_fixed": 3
    }
)
```

### Batch Generation Tracking

For "generate" mode with incremental test addition:

```python
# Create batch tasks
for batch_num in range(1, num_batches + 1):
    TaskCreate(
        subject=f"Generate Test Batch {batch_num}",
        description=f"Add 5-10 tests, then validate integration",
        activeForm=f"Generating batch {batch_num}",
        metadata={"batch_number": batch_num, "max_tests": 10}
    )

# After each batch
TaskUpdate(
    taskId=batch_task_id,
    status="completed",
    metadata={
        "tests_generated": 8,
        "integration_status": "passed",
        "rollback_needed": False
    }
)
```

### Rollback Tracking

```python
# If integration validation fails
TaskCreate(
    subject="Rollback: Integration Failure",
    description="Restore test state due to integration failure",
    activeForm="Rolling back",
    metadata={"reason": "integration_failure", "affected_tests": [...]}
)

TaskUpdate(
    taskId=rollback_task_id,
    status="completed",
    metadata={"rollback_status": "success", "state_restored": True}
)
```

### Progress Summary Pattern

After mode execution:

```
COVERAGE TASK PROGRESS
  Mode: {mode}
  Target: {target or "all"}

  Tasks:
    Pre-flight Validation:    Completed
    Learn Patterns:           Completed
    Agent: unit-test-fixer:   +3.2% coverage
    Agent: api-test-fixer:    In Progress
    Agent: database-fixer:    Pending
    Post-flight Validation:   Pending

  Coverage: 68% -> 71.2% (+3.2%)
  Quality Score: 8.1/10 (Good)
```
