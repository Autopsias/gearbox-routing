---
description: "Splits a task across multiple specialist agents running in parallel with file conflict detection and phased execution. Use when you say 'parallelize this', 'run in parallel', 'split this work across agents', or need concurrent execution of independent subtasks."
argument-hint: "<task_description>"
allowed-tools: ["Task", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

Invoke the parallel-orchestrator agent to handle this parallelization request:

$ARGUMENTS

The parallel-orchestrator will:
1. Analyze the task and categorize by domain expertise
2. Detect file conflicts to prevent race conditions
3. Create non-overlapping work packages for each agent
4. Spawn appropriate specialized agents in TRUE parallel (single message)
5. Aggregate results and validate

## Agent Routing

The orchestrator automatically routes to the best specialist:
- **Test failures** → unit-test-fixer, api-test-fixer, database-test-fixer, e2e-test-fixer
- **Type errors** → type-error-fixer
- **Import errors** → import-error-fixer
- **Linting** → linting-fixer
- **Security** → security-scanner
- **Generic** → general-purpose

## Orchestrator Phase-Completion Checklist

1. [ ] All agents in this phase returned (no hung/timed-out tasks)
2. [ ] Each agent's output touches only its assigned files (no unexpected overlap)
3. [ ] No agent reported a blocking error unaddressed
4. [ ] Dependent (next) phase's inputs are actually present/valid
5. [ ] Aggregate result reviewed before declaring the phase done

## Safety Controls

- MUST NOT spawn more than 6 agents per batch
- MUST run automatic conflict detection before dispatch
- MUST phase execution when work is dependent
- JSON output enforcement for efficiency

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| Agent spawn timeout | Too many agents requested at once | Reduce batch size to 3-4 agents max |
| File conflict false positive | Glob pattern too broad | Use specific file paths instead of `**/*.py` |
| Agent returns empty result | Task description too vague | Add concrete file paths and expected output to the prompt |
| Deadlock between phases | Phase 2 depends on Phase 1 that failed | Check Phase 1 results before launching Phase 2 |
