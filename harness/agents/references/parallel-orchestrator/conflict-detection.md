# File Conflict Detection Algorithm

## Rule 3: Conflict Detection (MANDATORY)

Before spawning ANY agents, you MUST:
1. Use Glob/Grep to identify all files in scope
2. Build a file ownership map per potential agent
3. Detect overlaps -> serialize conflicting agents
4. Create non-overlapping partitions

```
SAFE TO PARALLELIZE (different file domains):
- linting-fixer + api-test-fixer -> Different files -> PARALLEL OK

MUST SERIALIZE (overlapping file domains):
- linting-fixer + import-error-fixer -> Both modify imports -> RUN SEQUENTIALLY
```

## Step 2: Conflict Detection

Use Glob/Grep to identify files each potential agent would touch:

```bash
# Example: Identify Python files with linting issues
grep -l "E501\|F401" **/*.py

# Example: Identify files with type errors
grep -l "error:" **/*.py
```

Build ownership map:
- Agent A: files [x.py, y.py]
- Agent B: files [z.py, w.py]
- If overlap detected -> serialize or reassign

## REFACTORING-SPECIFIC RULES

**CRITICAL**: When routing to `safe-refactor` agents, special rules apply due to test dependencies.

### Mandatory Pre-Analysis

When ANY refactoring work is requested:

1. **ALWAYS call dependency-analyzer first**
   ```bash
   # For each file to refactor, find test dependencies
   for FILE in $REFACTOR_FILES; do
       MODULE_NAME=$(basename "$FILE" .py)
       TEST_FILES=$(grep -rl "$MODULE_NAME" tests/ --include="test_*.py" 2>/dev/null)
       echo "$FILE -> tests: [$TEST_FILES]"
   done
   ```

2. **Group files by cluster** (shared deps/tests)
   - Files sharing test files = SAME cluster
   - Files with independent tests = SEPARATE clusters

3. **Within cluster with shared tests**: SERIALIZE
   - Run one safe-refactor agent at a time
   - Wait for completion before next file
   - Check result status before proceeding

4. **Across independent clusters**: PARALLELIZE (max 6 total)
   - Can run multiple clusters simultaneously
   - Each cluster follows its own serialization rules internally

5. **On any failure**: Invoke failure-handler, await user decision
   - Continue: Skip failed file
   - Abort: Stop all refactoring
   - Retry: Re-attempt (max 2 retries)

### Prohibited Patterns

**NEVER do this:**
```
# WRONG: Parallel refactoring without dependency analysis
Task(safe-refactor, file1)  # Spawns agent
Task(safe-refactor, file2)  # Spawns agent - MAY CONFLICT!
Task(safe-refactor, file3)  # Spawns agent - MAY CONFLICT!
```

Files that share test files will cause:
- Test pollution (one agent's changes affect another's tests)
- Race conditions on git stash
- Corrupted fixtures
- False positives/negatives in test results

### Required Pattern

**ALWAYS do this:**
```
# CORRECT: Dependency-aware scheduling

# First: Analyze dependencies
clusters = analyze_dependencies([file1, file2, file3])

# Example result:
# cluster_a (shared tests/test_user.py): [file1, file2]
# cluster_b (independent): [file3]

# Then: Schedule based on clusters
for cluster in clusters:
    if cluster.has_shared_tests:
        # Serial execution within cluster
        for file in cluster:
            result = Task(safe-refactor, file, cluster_context)
            await result  # WAIT before next

            if result.status == "failed":
                # Invoke failure handler
                decision = prompt_user_for_decision()
                if decision == "abort":
                    break
    else:
        # Parallel execution (up to 6)
        Task(safe-refactor, cluster.files, cluster_context)
```

### Cluster Context Parameters

When dispatching safe-refactor agents, MUST include:

```json
{
  "cluster_id": "cluster_a",
  "parallel_peers": ["file2.py", "file3.py"],
  "test_scope": ["tests/test_user.py"],
  "execution_mode": "serial|parallel"
}
```

### Safe-Refactor Result Handling

Parse agent results to detect conflicts:

```json
{
  "status": "fixed|partial|failed|conflict",
  "cluster_id": "cluster_a",
  "files_modified": ["..."],
  "test_files_touched": ["..."],
  "conflicts_detected": []
}
```

| Status | Action |
|--------|--------|
| `fixed` | Continue to next file/cluster |
| `partial` | Log warning, may need follow-up |
| `failed` | Invoke failure handler (user decision) |
| `conflict` | Wait and retry after delay |

### Test File Serialization

When refactoring involves test files:

| Scenario | Handling |
|----------|----------|
| conftest.py changes | SERIALIZE (blocks ALL other test work) |
| Shared fixture changes | SERIALIZE within fixture scope |
| Independent test files | Can parallelize |

### Maximum Concurrent Safe-Refactor Agents

**ABSOLUTE LIMIT: 6 agents at any time - NO EXCEPTIONS**

Even if you have 10 independent clusters, never spawn more than 6 safe-refactor agents simultaneously.

```
BATCH LIMIT ENFORCEMENT
  - Spawn up to 6 agents
  - Wait for all to complete (TaskOutput)
  - Report results
  - EXIT - do not spawn more batches
  - The caller will invoke you again for the next batch
  - This gives user control and prevents context explosion
```

This prevents:
- Context window explosion (CRITICAL)
- Resource exhaustion
- Git lock contention
- System overload
- Loss of user control

### Observability

Log all refactoring orchestration decisions:

```json
{
  "event": "refactor_cluster_scheduled",
  "cluster_id": "cluster_a",
  "files": ["user_service.py", "user_utils.py"],
  "execution_mode": "serial",
  "reason": "shared_test_file",
  "shared_tests": ["tests/test_user.py"]
}
```

**TaskList provides additional observability:**

- Use `TaskList()` to see all batch/agent status at any time
- Use `TaskGet(taskId)` to inspect specific task details and metadata
- Task metadata captures file assignments, results, and conflict status
- Progress persists across conversation turns within the session
