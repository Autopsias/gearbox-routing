# Agent Dispatch & Dependency-Aware Batching

## CRITICAL: Parallel Execution Safety

**Three rules that prevent change loss:**

1. **Never mix lightweight and full-mode agents in the same parallel batch.**
   `git checkout` in a full-mode agent destroys all lightweight agents' uncommitted work.
   Split mixed-type clusters into two sub-batches: lightweight first (committed), then full.

2. **Shell scripts ALWAYS use lightweight mode.**
   `.sh`/`.bash`/`.zsh` files → `git_mode: "lightweight"` (no git operations, bash -n verification).

3. **Full-mode agents ONLY in serial clusters or after inter-batch commit.**
   `.py`/`.ts`/`.js` files → `git_mode: "full"` but ONLY when the agent has exclusive working tree access.

---

Routing to safe-refactor/code-quality-analyzer agents, dependency analysis, cluster identification, and batched execution.

---

## PHASE 0: Warm-Up (Check Dependency Cache)

```bash
# Check if dependency cache exists and is fresh (< 15 min)
CACHE_FILE=".claude/cache/dependency-graph.json"
CACHE_AGE=900  # 15 minutes

if [ -f "$CACHE_FILE" ]; then
    MODIFIED=$(stat -f %m "$CACHE_FILE" 2>/dev/null || stat -c %Y "$CACHE_FILE" 2>/dev/null)
    NOW=$(date +%s)
    if [ $((NOW - MODIFIED)) -lt $CACHE_AGE ]; then
        echo "Using cached dependency graph (age: $((NOW - MODIFIED))s)"
    else
        echo "Cache stale, will rebuild"
    fi
else
    echo "No cache found, will build dependency graph"
fi
```

## PHASE 1: Dependency Graph Construction

Before ANY refactoring agents are spawned:

```bash
echo "=== PHASE 2: Dependency Analysis ==="
echo "Analyzing imports for violation files..."

# For each violating file, find its test dependencies
for FILE in $VIOLATION_FILES; do
    MODULE_NAME=$(basename "$FILE" .py)

    # Find test files that import this module
    TEST_FILES=$(grep -rl "$MODULE_NAME" tests/ --include="test_*.py" 2>/dev/null | sort -u)

    echo "  $FILE -> tests: [$TEST_FILES]"
done

echo ""
echo "Building dependency graph..."
echo "Mapping test file relationships..."
```

## PHASE 2: Cluster Identification

Group files by shared test files (CRITICAL for safe parallelization):

```bash
# Files sharing test files MUST be serialized
# Files with independent tests CAN be parallelized

# Example output:
echo "
Cluster A (SERIAL - shared tests/test_user.py):
  - user_service.py (612 LOC)
  - user_utils.py (534 LOC)

Cluster B (PARALLEL - independent):
  - auth_handler.py (543 LOC)
  - payment_service.py (489 LOC)
  - notification.py (501 LOC)

Cluster C (SERIAL - shared tests/test_api.py):
  - api_router.py (567 LOC)
  - api_middleware.py (512 LOC)
"
```

## PHASE 3: Calculate Cluster Priority

Score each cluster for execution order (higher = execute first):

```bash
# +10 points per file with >600 LOC (worst violations)
# +5 points if cluster contains frequently-modified files
# +3 points if cluster is on critical path (imported by many)
# -5 points if cluster only affects test files
```

Sort clusters by priority score (highest first = fail fast on critical code).

## PHASE 3.5: Determine Agent Mode and Segregate Batches

### Step 1: Assign git_mode per file

| Extension | git_mode | Reason |
|-----------|----------|--------|
| `.sh`, `.bash`, `.zsh` | `lightweight` | No test framework; bash -n + --help sufficient |
| `.py` | `full` | pytest test-safe workflow required |
| `.ts`, `.tsx`, `.js`, `.jsx` | `full` | Jest/Vitest test-safe workflow required |
| All others | `full` | Default to safe mode |
| Override: `--lightweight` flag | `lightweight` | User forces lightweight for all |

### Step 2: Segregate mixed-type parallel clusters

If a parallel cluster contains BOTH lightweight and full-mode files:

```
BEFORE (unsafe):
  Cluster B (PARALLEL): manage-data.sh [lightweight], routes.py [full], utils.sh [lightweight]

AFTER (safe):
  Cluster B-light (PARALLEL): manage-data.sh [lightweight], utils.sh [lightweight]
  Cluster B-full (SERIAL): routes.py [full]
  Execution: B-light first → commit → B-full
```

**Rule: Parallel batches MUST be mode-homogeneous.** Mixed clusters are split by mode.

## PHASE 4: Execute Batched Refactoring

For each cluster, respecting parallelization rules:

**Parallel clusters (no shared tests):**
Launch up to `--max-parallel` (default 6) agents simultaneously:

```
Task(
    subagent_type="safe-refactor",
    description="Safe refactor: auth_handler.py",
    prompt="Refactor this file using TEST-SAFE workflow:
    File: auth_handler.py
    Current LOC: 543
    Target threshold: 500 LOC

    CLUSTER CONTEXT (NEW):
    - cluster_id: cluster_b
    - parallel_peers: [payment_service.py, notification.py]
    - test_scope: tests/test_auth.py
    - execution_mode: parallel

    GIT MODE:
    - git_mode: {lightweight|full}
    - If lightweight: DO NOT run git checkout, git stash, git branch, or git commit
    - If lightweight: Use cp {file} {file}.bak for rollback, bash -n + --help for verification
    - If lightweight: Add '# shellcheck shell=bash' header to all extracted lib files
    - If full: Standard test-safe workflow with temp branches

    MANDATORY WORKFLOW:
    1. PHASE 0: Run existing tests, establish GREEN baseline
    2. PHASE 1: Create facade structure (tests must stay green)
    3. PHASE 2: Migrate code incrementally (test after each change)
    4. PHASE 3: Update test imports only if necessary
    5. PHASE 4: Cleanup legacy, final test verification
    6. PHASE 5: CRITICAL - Verify largest file < 500 LOC threshold

    CRITICAL RULES:
    - If tests fail at ANY phase, REVERT with git stash pop
    - Use facade pattern to preserve public API
    - Never proceed with broken tests
    - DO NOT modify files outside your scope
    - PHASE 5: MUST check if largest new file < 500 LOC
    - If largest file >= 500 LOC: status MUST be \"partial\" NOT \"fixed\"

    MANDATORY OUTPUT FORMAT - Return ONLY JSON:
    {
      \"status\": \"fixed|partial|failed\",
      \"cluster_id\": \"cluster_b\",
      \"files_modified\": [\"...\"],
      \"test_files_touched\": [\"...\"],
      \"issues_fixed\": N,
      \"remaining_issues\": N,
      \"conflicts_detected\": [],
      \"new_structure\": {
        \"largest_file_loc\": N,
        \"target_threshold\": 500,
        \"meets_threshold\": true/false
      },
      \"tools_invoked\": [\"Edit\", \"MultiEdit\", \"Write\"],
      \"git_diff_files\": [\"list of files from git diff --name-only\"],
      \"verification\": {
        \"git_shows_changes\": true/false,
        \"actual_loc_after\": N
      },
      \"summary\": \"...\"
    }
    DO NOT include full file contents.

    CRITICAL EXECUTION REQUIREMENTS (ANTI-HALLUCINATION):
    1. You MUST actually use Edit/Write/MultiEdit tools - do NOT just produce JSON
    2. BEFORE returning JSON, run: git diff --name-only
    3. If git shows NO changes for target file, your status MUST be \"failed\"
    4. Include actual git diff file list in \"git_diff_files\" field
    5. Your work will be VERIFIED - false success reports will be detected and rejected
    6. If you cannot make changes (file not found, tests fail, etc.), report \"failed\" honestly

    WARNING: The orchestrator will verify your claims using git diff.
    If git shows no changes but you report success, your status will be overridden to \"failed\"."
)
```

**Serial clusters (shared tests):**
Execute ONE agent at a time, wait for completion:

```
# File 1/2: user_service.py
Task(safe-refactor, ...) → wait for completion

# Check result
if result.status == "failed":
    → Invoke FAILURE HANDLER (see troubleshooting.md)

# File 2/2: user_utils.py
Task(safe-refactor, ...) → wait for completion
```

## Dry Run Plan Format

If `--dry-run` flag provided, show the dependency analysis and execution plan:

```
## Dry Run: Refactoring Plan

### PHASE 2: Dependency Analysis
Analyzing imports for 8 violation files...
Building dependency graph...
Mapping test file relationships...

### Identified Clusters

Cluster A (SERIAL - shared tests/test_user.py):
  - user_service.py (612 LOC)
  - user_utils.py (534 LOC)

Cluster B (PARALLEL - independent):
  - auth_handler.py (543 LOC)
  - payment_service.py (489 LOC)
  - notification.py (501 LOC)

### Proposed Schedule
  Batch 1: Cluster B (3 agents in parallel)
  Batch 2: Cluster A (2 agents serial)

### Estimated Time
  - Parallel batch (3 files): ~4 min
  - Serial batch (2 files): ~10 min
  - Total: ~14 min
```

Exit after showing plan (no changes made).

---

## Observability & Logging

Log all orchestration decisions to `.claude/logs/orchestration-{date}.jsonl`:

```json
{"event": "cluster_scheduled", "cluster_id": "cluster_b", "files": ["auth.py", "payment.py"], "mode": "parallel", "priority": 18}
{"event": "batch_started", "batch": 1, "agents": 3, "cluster_id": "cluster_b"}
{"event": "agent_completed", "file": "auth.py", "status": "fixed", "duration_s": 240}
{"event": "failure_handler_invoked", "file": "user_utils.py", "error": "TestFailed"}
{"event": "user_decision", "action": "continue", "remaining": 3}
{"event": "early_termination_offered", "completed_priority": 45, "remaining_priority": 10}
```

---

## Worktree Isolation: Merge-Back Protocol (If Used)

**NOTE:** Worktree isolation is NOT recommended for code-quality. Prefer lightweight mode + inter-batch commits.

If worktree IS used, the orchestrator MUST copy files back before cleanup:

```bash
WORKTREE_PATH=".claude/worktrees/{agent_id}"

# Copy changed files
CHANGED=$(cd "$WORKTREE_PATH" && git diff --name-only HEAD)
for FILE in $CHANGED; do
    cp "$WORKTREE_PATH/$FILE" "$FILE"
done

# Copy new files
NEW=$(cd "$WORKTREE_PATH" && git ls-files --others --exclude-standard)
for FILE in $NEW; do
    mkdir -p "$(dirname $FILE)"
    cp "$WORKTREE_PATH/$FILE" "$FILE"
done

# NOW safe to clean up
git worktree remove "$WORKTREE_PATH" --force
```
