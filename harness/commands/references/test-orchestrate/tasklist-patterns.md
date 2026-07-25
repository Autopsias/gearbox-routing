# TaskList Integration Patterns

Use TaskList tools to track test type fixes and provide visibility into orchestration progress.

## TaskList Workflow for Test Orchestration

```
STEP 0 (After Depth Check): Check for existing task state
  └── TaskList() - see if resuming from previous iteration

STEP 6 (After Failure Categorization): Create type-level tasks
  └── TaskCreate for each test failure type with priority
  └── Set blockedBy for dependent types

STEP 8 (When Dispatching Specialists): Create agent tasks
  └── TaskCreate for each specialist agent
  └── TaskUpdate status="in_progress" when agent spawns

After Agent Completion: Update task status
  └── TaskUpdate status="completed" with result metadata
```

## Test Type Task Creation Pattern

```
# After STEP 6 failure categorization, create priority-ordered tasks:
TaskCreate(
  subject="Fix import tests (P0)",
  description="Foundation: Import errors block all other tests",
  activeForm="Fixing import tests"
)
TaskCreate(
  subject="Fix type tests (P0)",
  description="Foundation: Type errors affect test expectations",
  activeForm="Fixing type tests"
)
TaskUpdate(taskId="2", addBlockedBy=["1"])

TaskCreate(
  subject="Fix unit tests (P1)",
  description="Core: Business logic tests",
  activeForm="Fixing unit tests"
)
TaskUpdate(taskId="3", addBlockedBy=["1", "2"])

TaskCreate(
  subject="Fix API tests (P1)",
  description="Integration: Endpoint tests",
  activeForm="Fixing API tests"
)
TaskUpdate(taskId="4", addBlockedBy=["1", "2"])

TaskCreate(
  subject="Fix database tests (P1)",
  description="Integration: Database fixture tests",
  activeForm="Fixing database tests"
)
TaskUpdate(taskId="5", addBlockedBy=["1", "2"])

TaskCreate(
  subject="Fix E2E tests (P2)",
  description="End-to-end: Require backend fixes complete",
  activeForm="Fixing E2E tests"
)
TaskUpdate(taskId="6", addBlockedBy=["3", "4", "5"])
```

## Agent Dispatch with Task Tracking

```
# When dispatching each test fixer agent:
agent_task = TaskCreate(
  subject="Agent: unit-test-fixer",
  description="Fix unit test failures",
  activeForm="Running unit-test-fixer agent"
)
TaskUpdate(taskId=agent_task, status="in_progress")

# After agent completes:
TaskUpdate(
  taskId=agent_task,
  status="completed",
  metadata={"tests_fixed": 3, "result": "fixed", "files_modified": [...]}
)

# Update parent type task
TaskUpdate(taskId=type_task, status="completed")
```

## Ralph Loop Bridge Pattern

When using `--loop`, persist task state for cross-session recovery:

```
# Before exiting session:
tasks = TaskList()
Write(".claude/state/task-bridge.json", json.dumps({
  "command": "test-orchestrate",
  "tasks": [task_to_dict(t) for t in tasks],
  "timestamp": now()
}))

# At session start (STEP 0):
if exists(".claude/state/task-bridge.json"):
    bridge = Read(".claude/state/task-bridge.json")
    for task in bridge["tasks"]:
        if task["status"] != "completed":
            TaskCreate(subject=task["subject"], ...)
```

## Progress Summary

Always include TaskList summary in STEP 11 output:

```
### Task Progress (via TaskList)
| Task ID | Subject | Status |
|---------|---------|--------|
| 1 | Fix import tests (P0) | completed |
| 2 | Fix type tests (P0) | completed |
| 3 | Fix unit tests (P1) | completed |
| 4 | Fix API tests (P1) | in_progress |
| 5 | Fix database tests (P1) | pending |
| 6 | Fix E2E tests (P2) | pending |

**Current:** API test fixes in progress. 2 types remaining.
```
