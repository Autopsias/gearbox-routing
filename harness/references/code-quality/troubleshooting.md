# Troubleshooting, Verification & State Management

Error handling, anti-hallucination gates, batch gate logic, stash verification, state file format, and tasklist integration.

## Contents

- [Batch Gate (MANDATORY — PHASE 4.5)](#batch-gate-mandatory-phase-45)
- [Anti-Hallucination Verification (MANDATORY)](#anti-hallucination-verification-mandatory)
- [Inter-Batch Commit Gate (MANDATORY — After Each Verified Batch)](#inter-batch-commit-gate-mandatory-after-each-verified-batch)
- [Auto-Ralph on Hallucination (Default Behavior)](#auto-ralph-on-hallucination-default-behavior)
- [Verify Git Stash State (CRITICAL — Prevents Orphaned Stashes)](#verify-git-stash-state-critical-prevents-orphaned-stashes)
- [Batch User Prompt (Step 3 of Batch Gate)](#batch-user-prompt-step-3-of-batch-gate)
- [Failure Handling (Interactive) — PHASE 5](#failure-handling-interactive-phase-5)
- [Early Termination Check (After Each Batch) — PHASE 6](#early-termination-check-after-each-batch-phase-6)
- [Context Window Protection Summary](#context-window-protection-summary)
- [State File Format (v2.0)](#state-file-format-v20)
- [Batch Progress Display](#batch-progress-display)
- [TASKLIST INTEGRATION (MANDATORY)](#tasklist-integration-mandatory)


---

## Batch Gate (MANDATORY — PHASE 4.5)

**HARD STOP: This section MUST be executed after spawning ANY batch of agents.**

After spawning up to 6 agents for the current batch:

**Step 1: Save State (v2.0 Schema)** - Write progress to `.claude/state/code-quality-batch.json`.

**CRITICAL: You MUST serialize ALL fields explicitly. Do NOT use placeholders.**

Create the state directory and write the JSON file:
```bash
mkdir -p .claude/state
```

Then write a JSON file with this EXACT structure (substitute actual values):

```json
{
  "schema_version": "2.1",
  "session_id": "{unix_timestamp}",
  "created_at": "{ISO8601_datetime}",
  "updated_at": "{ISO8601_datetime}",
  "project_path": "{absolute_project_path}",

  "analysis": {
    "total_violations": 0,
    "file_size_violations": 0,
    "function_length_violations": 0
  },

  "ralph_state": {
    "current_rule": "complexity|function-length|file-size|null",
    "rule_iteration": 0,
    "rules_completed": [],
    "rules_remaining": ["complexity", "function-length", "file-size"]
  },

  "execution_plan": {
    "total_batches": 0,
    "current_batch": 0,
    "batches": [
      {
        "batch_number": 1,
        "cluster_id": "{cluster_id}",
        "files": ["{file1.py}", "{file2.py}"],
        "mode": "parallel|serial",
        "status": "completed|in_progress|pending",
        "completed_at": "{ISO8601 or null}",
        "reason": "{why serial, if applicable}"
      }
    ]
  },

  "clusters": [
    {
      "id": "{cluster_id}",
      "files": ["{file1.py}", "{file2.py}"],
      "mode": "serial|parallel",
      "shared_tests": ["{test_file.py}"],
      "priority_score": 0
    }
  ],

  "file_status": {
    "completed": [
      {"file": "{path}", "original_loc": 0, "new_loc": 0, "batch": 0}
    ],
    "pending": ["{file1.py}", "{file2.py}"],
    "failed": [{"file": "{path}", "error": "{message}"}],
    "skipped": [{"file": "{path}", "reason": "{user_choice}"}]
  }
}
```

**MANDATORY SERIALIZATION CHECKLIST** (verify before writing):

| Field | Source | Required |
|-------|--------|----------|
| `schema_version` | "2.0" or "2.1" (v2.1 adds ralph_state tracking) | YES |
| `execution_plan.batches` | From PHASE 2 cluster analysis | YES |
| `execution_plan.batches[].mode` | "serial" if shared_tests, else "parallel" | YES |
| `execution_plan.batches[].status` | Track as batches complete | YES |
| `clusters` | From PHASE 2 cluster identification | YES |
| `clusters[].shared_tests` | From dependency graph (can be empty array) | YES |
| `file_status.pending` | Files not yet processed | YES |

**If ANY field is missing, --continue WILL FAIL in the next session.**

**Step 2: Wait for Current Batch** - Use TaskOutput to block until ALL spawned agents complete:
```
# MANDATORY: Wait for each agent spawned in this batch
# The Task tool returns agent IDs that can be used with TaskOutput

for each agent_id in spawned_agents:
    TaskOutput(task_id=agent_id, block=true, timeout=300000)
    # Parse result and update state
```

---

## Anti-Hallucination Verification (MANDATORY)

### Verify Agent Actually Made Changes

**After EACH agent completes, IMMEDIATELY verify git state before recording to state file:**

```bash
# For each file the agent claimed to have modified:
TARGET_FILE="{file_from_agent_result}"

# Check if git shows actual modifications
if git diff --name-only | grep -q "$TARGET_FILE"; then
    echo "VERIFIED: $TARGET_FILE was actually modified by git diff"
    # Also verify file size actually changed
    ACTUAL_LOC=$(wc -l < "$TARGET_FILE" 2>/dev/null | tr -d ' ')
    echo "Actual LOC after refactor: $ACTUAL_LOC"

    if [ "$ACTUAL_LOC" -ge "{agent_claimed_original_loc}" ]; then
        echo "WARNING: LOC did not decrease as claimed"
        AGENT_STATUS="partial"
    fi
else
    echo "FAILURE: Agent reported success but NO GIT CHANGES detected for $TARGET_FILE"
    echo "Agent may have hallucinated the refactoring"
    # OVERRIDE agent status - this is CRITICAL
    AGENT_STATUS="failed"
    FAILURE_REASON="Agent reported success but git shows no modifications"
fi
```

**State File Recording Rules:**
1. If git shows NO changes for target file -> **status = "failed"**, add to `file_status.failed`
2. If git shows changes but LOC didn't decrease -> **status = "partial"**
3. If git shows changes AND LOC decreased -> **status = "fixed"** (accept agent result)
4. **NEVER record "completed" status without git verification**

### Verification Script (ANTI-HALLUCINATION GATE)

**NEVER skip this step. Without verification, agent hallucinations go undetected.**

```bash
# MANDATORY: Verify EVERY file claim before recording to state
for TARGET_FILE in {list_of_files_from_agent_batch}; do
    echo "Verifying: $TARGET_FILE"
    python ~/.claude/scripts/quality/verify_refactoring.py --git-check "$TARGET_FILE"

    if [ $? -ne 0 ]; then
        echo "HALLUCINATION DETECTED: $TARGET_FILE - overriding status to failed"
        # Override agent status for this file
        AGENT_STATUS="failed"
        FAILURE_REASON="Hallucination: git shows no changes despite agent claiming success"
    fi
done

# Also verify all completed entries in state file (run after batch completes)
python ~/.claude/scripts/quality/verify_refactoring.py --state-file .claude/state/code-quality-batch.json --json
```

**CRITICAL ENFORCEMENT:**
- If `verify_refactoring.py` returns exit code 1 -> Agent hallucinated
- Override agent's claimed status to "failed"
- Add to `file_status.failed` array with reason "hallucination_detected"
- DO NOT record to `file_status.completed`

If verification fails, the entry is a hallucination and MUST be moved to `file_status.failed`.

---

## Inter-Batch Commit Gate (MANDATORY — After Each Verified Batch)

**After all agents in a batch complete AND pass anti-hallucination verification, commit before the next batch starts.**

This prevents Cross-Batch Destruction where Batch N+1's git operations destroy Batch N's uncommitted work.

### Pre-Commit Checks

Before committing, verify shellcheck compatibility:
```bash
# Check all new lib files have shellcheck directive
NEW_SH_FILES=$(git diff --name-only --diff-filter=A | grep '\.sh$')
for FILE in $NEW_SH_FILES; do
    if ! head -1 "$FILE" | grep -q "shellcheck"; then
        echo "# shellcheck shell=bash" | cat - "$FILE" > temp && mv temp "$FILE"
    fi
done
```

### Commit Procedure

```bash
BATCH_NUM="{current_batch_number}"
BATCH_FILES="{list of verified files from this batch}"

# Stage each verified file + its specific lib directory
for FILE in $BATCH_FILES; do
    git add "$FILE"

    # Stage the script-specific lib dir (not the parent lib/)
    SCRIPT_NAME=$(basename "$FILE" .sh)
    SCRIPT_DIR=$(dirname "$FILE")

    # Check common lib locations for this specific script
    for LIB_CANDIDATE in \
        "$SCRIPT_DIR/lib/$SCRIPT_NAME" \
        "$SCRIPT_DIR/lib/$(echo $SCRIPT_NAME | sed 's/-runner$//')" \
        "$SCRIPT_DIR/lib"; do
        if [ -d "$LIB_CANDIDATE" ]; then
            git add "$LIB_CANDIDATE/"
            break
        fi
    done
done

FILE_COUNT=$(echo "$BATCH_FILES" | wc -w | tr -d ' ')
git commit -m "refactor: code-quality batch $BATCH_NUM — $FILE_COUNT files split

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### If commit fails (pre-commit hook)

```bash
# Most likely: shellcheck errors on new lib files
# Fix: add missing directives, re-stage, retry
FAILED_FILES=$(shellcheck scripts/lib/**/*.sh 2>&1 | grep "^In " | sed 's/In //' | sed 's/ line.*//' | sort -u)
for FILE in $FAILED_FILES; do
    # Add shellcheck shell=bash if missing
    if ! head -1 "$FILE" | grep -q "shellcheck"; then
        sed -i '' '1i\
# shellcheck shell=bash' "$FILE"
    fi
done
git add -A && git commit -m "refactor: code-quality batch $BATCH_NUM — $FILE_COUNT files split

Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

### When to skip commit

- All agents in the batch reported `status: "failed"` (nothing to commit)
- `--dry-run` mode (no changes made)
- Single-batch run with no subsequent batches

### --continue compatibility

Inter-batch commits are compatible with `--continue` because batches contain non-overlapping file sets. The state file tracks batch status independently of git commits.

---

## Auto-Ralph on Hallucination (Default Behavior)

**When hallucination is detected, automatically invoke Ralph Loop for self-correction:**

```
HYBRID APPROACH: Verify-First, Ralph-on-Failure

  PHASE 1: Single-shot agent attempts refactor
                      |
  PHASE 2: MANDATORY git verification
                      |
  Git shows actual changes? --YES--> Record success, proceed
                            --NO---> AUTO-INVOKE RALPH (up to 3x)
```

**Auto-Ralph Implementation:**

```bash
# After single-shot agent completes and hallucination is detected
HALLUCINATED_FILES=()

for TARGET_FILE in {list_of_files_from_agent_batch}; do
    python ~/.claude/scripts/quality/verify_refactoring.py --git-check "$TARGET_FILE"
    if [ $? -ne 0 ]; then
        echo "HALLUCINATION DETECTED: $TARGET_FILE"
        HALLUCINATED_FILES+=("$TARGET_FILE")
    fi
done

# AUTO-INVOKE RALPH for hallucinated files (unless --no-ralph flag set)
if [ ${#HALLUCINATED_FILES[@]} -gt 0 ] && [ "$NO_RALPH" != "true" ]; then
    echo ""
    echo "AUTO-RALPH ACTIVATED: ${#HALLUCINATED_FILES[@]} files need self-correction"

    for FILE in "${HALLUCINATED_FILES[@]}"; do
        echo "Starting Ralph Loop for: $FILE"
        # Invoke Ralph Loop (see chaining-logic.md STEP 4-RALPH for details)
        # Ralph will iterate until git verification succeeds or max-iterations reached
    done
fi
```

**Ralph Auto-Invocation Rules:**

| Condition | Action |
|-----------|--------|
| Hallucination detected + no `--no-ralph` | Auto-invoke Ralph Loop |
| Hallucination detected + `--no-ralph` set | Mark as failed, user must retry manually |
| Git shows changes | Record success, skip Ralph |
| Ralph exhausts max-iterations (3) | Mark as failed, require manual intervention |

**Why This Is Now Default:**

Research from the hallucination incident showed:
- Single-shot agents have ~75% hallucination rate without context pressure
- Ralph's iterative self-correction catches 100% of hallucinations
- Cost increase is only ~2-3x per hallucinated file (not all files)
- Hybrid approach balances speed (single-shot first) with reliability (Ralph fallback)

**To DISABLE auto-Ralph:** Use `--no-ralph` flag
```bash
/code_quality --fix --no-ralph  # Pure single-shot, fail on hallucination
```

---

## Verify Git Stash State (CRITICAL — Prevents Orphaned Stashes)

**After EACH agent completes, verify no orphaned stashes remain:**

```bash
# Check for orphaned safe-refactor or mikado stashes
ORPHANED_STASHES=$(git stash list | grep -E "safe-refactor|mikado-" | head -5)

if [ -n "$ORPHANED_STASHES" ]; then
    echo "WARNING: Orphaned stash detected - agent did not cleanup properly"
    echo "Orphaned stashes:"
    echo "$ORPHANED_STASHES"

    # Check agent's stash_cleanup field
    AGENT_STASH_STATE="{from agent JSON: stash_cleanup field}"

    if [ "$AGENT_STASH_STATE" == "dropped" ] || [ "$AGENT_STASH_STATE" == "none" ]; then
        echo "ERROR: Agent claimed stash_cleanup='$AGENT_STASH_STATE' but orphaned stashes exist"
        echo "This indicates a workflow bug in the agent"
        # Flag this for review but don't fail - work may still be valid
    fi

    # AUTO-RECOVERY: Pop the stash if it contains the refactored work
    echo ""
    echo "Checking if stash contains the expected changes..."
    STASH_REF=$(git stash list | grep "safe-refactor-baseline" | head -1 | cut -d: -f1)
    if [ -n "$STASH_REF" ]; then
        # Show what's in the stash
        git stash show "$STASH_REF" 2>/dev/null || true

        # If working tree has NO changes but stash exists, the work is likely hidden
        if [ -z "$(git diff --name-only)" ]; then
            echo "ALERT: Working tree is clean but stash exists - work may be hidden in stash"
            echo "Attempting auto-recovery with git stash pop..."
            git stash pop "$STASH_REF"

            # Verify recovery worked
            if [ -n "$(git diff --name-only)" ]; then
                echo "SUCCESS: Recovered hidden work from stash"
                AGENT_STATUS="fixed"  # Upgrade status since work was recovered
            else
                echo "WARNING: Stash pop succeeded but still no changes detected"
            fi
        fi
    fi
else
    echo "Git stash state is clean (no orphaned stashes)"
fi
```

**Stash State Recording Rules:**
1. If `stash_cleanup == "dropped"` and no orphaned stashes -> Expected state, record as-is
2. If `stash_cleanup == "none"` and no orphaned stashes -> Acceptable, but unusual
3. If `stash_cleanup == "warning_orphaned"` -> Agent detected issue, investigate
4. If `stash_cleanup == "preserved_for_rollback"` -> Failure case, stash intentionally kept
5. If orphaned stashes exist but agent claimed "dropped" -> **Workflow bug, flag for review**

---

## Batch User Prompt (Step 3 of Batch Gate)

**STOP AND PROMPT USER** - Do NOT proceed to next batch automatically:
```
AskUserQuestion(
  questions=[{
    "question": "Batch {N}/{M} complete and COMMITTED ({commit_sha}). {X} files refactored, {Y} remaining, {Z} failed. Continue to next batch?",
    "header": "Batch Gate",
    "options": [
      {"label": "Continue next batch", "description": "Process next batch (up to 6 files)"},
      {"label": "Stop here", "description": "Save state and exit. Run /code_quality --continue to resume later."}
    ],
    "multiSelect": false
  }]
)
```

**On "Stop here":**
- Update state file with final status
- Report progress summary
- Output: "Progress saved. Run `/code_quality --continue` to resume."
- EXIT the command (do NOT continue)

**On "Continue":**
- Increment current_batch counter
- Process ONLY the next batch (max 6 agents)
- Return to Step 1 (BATCH GATE repeats after each batch)

**WHY THIS IS MANDATORY:**
- Each batch uses ~10-20% of context window
- 3+ simultaneous batches = overflow
- BATCH GATE ensures ONE batch per context window
- User controls when to continue
- Recovery possible from any failure

**ENFORCEMENT RULES:**
- NEVER spawn more than 6 agents in a single response
- NEVER continue to next batch without user confirmation
- NEVER skip state saving between batches
- ALWAYS wait for all agents with TaskOutput before prompting
- ALWAYS save state before prompting user
- ALWAYS respect user's "Stop here" decision

---

## Failure Handling (Interactive) — PHASE 5

When a refactoring agent fails, use AskUserQuestion to prompt:

```
AskUserQuestion(
  questions=[{
    "question": "Refactoring of {file} failed: {error}. {N} files remain. What would you like to do?",
    "header": "Failure",
    "options": [
      {"label": "Continue with remaining files", "description": "Skip {file} and proceed with remaining {N} files"},
      {"label": "Abort refactoring", "description": "Stop now, preserve current state"},
      {"label": "Retry this file", "description": "Attempt to refactor {file} again"}
    ],
    "multiSelect": false
  }]
)
```

**On "Continue"**: Add file to skipped list, continue with next
**On "Abort"**: Clean up locks, report final status, exit
**On "Retry"**: Re-attempt (max 2 retries per file)

---

## Early Termination Check (After Each Batch) — PHASE 6

After completing high-priority clusters, check if user wants to terminate early:

```bash
# Calculate completed vs remaining priority
COMPLETED_PRIORITY=$(sum of completed cluster priorities)
REMAINING_PRIORITY=$(sum of remaining cluster priorities)
TOTAL_PRIORITY=$((COMPLETED_PRIORITY + REMAINING_PRIORITY))

# If 80%+ of priority work complete, offer early exit
if [ $((COMPLETED_PRIORITY * 100 / TOTAL_PRIORITY)) -ge 80 ]; then
    # Prompt user
    AskUserQuestion(
      questions=[{
        "question": "80%+ of high-priority violations fixed. Complete remaining low-priority work?",
        "header": "Progress",
        "options": [
          {"label": "Complete all remaining", "description": "Fix remaining {N} files (est. {time})"},
          {"label": "Terminate early", "description": "Stop now, save ~{time}. Remaining files can be fixed later."}
        ],
        "multiSelect": false
      }]
    )
fi
```

---

## Context Window Protection Summary

| Protection | Mechanism |
|------------|-----------|
| Max agents per batch | 6 (hard limit) |
| Batch gate | AskUserQuestion after each batch |
| State persistence | JSON file between batches |
| User control | Confirmation before every batch |
| Recovery | `--continue` flag to resume |

**No bypass available** - Every batch requires explicit user confirmation.

---

## State File Format (v2.0)

**Location:** `.claude/state/code-quality-batch.json`

**Schema Version:** 2.0 (required for `--continue` to work)

```json
{
  "schema_version": "2.0",
  "session_id": "1704067200",
  "created_at": "2024-01-01T00:00:00Z",
  "updated_at": "2024-01-01T01:30:00Z",
  "project_path": "/path/to/project",

  "analysis": {
    "total_violations": 15,
    "file_size_violations": 8,
    "function_length_violations": 7
  },

  "execution_plan": {
    "total_batches": 4,
    "current_batch": 2,
    "batches": [
      {
        "batch_number": 1,
        "cluster_id": "cluster_b",
        "files": ["auth_handler.py", "payment.py", "notification.py"],
        "mode": "parallel",
        "status": "completed",
        "completed_at": "2024-01-01T00:30:00Z",
        "reason": null
      },
      {
        "batch_number": 2,
        "cluster_id": "cluster_a",
        "files": ["user_service.py", "user_utils.py"],
        "mode": "serial",
        "status": "pending",
        "completed_at": null,
        "reason": "shared test: tests/test_user.py"
      }
    ]
  },

  "clusters": [
    {
      "id": "cluster_a",
      "files": ["user_service.py", "user_utils.py"],
      "mode": "serial",
      "shared_tests": ["tests/test_user.py"],
      "priority_score": 15
    },
    {
      "id": "cluster_b",
      "files": ["auth_handler.py", "payment.py", "notification.py"],
      "mode": "parallel",
      "shared_tests": [],
      "priority_score": 18
    }
  ],

  "file_status": {
    "completed": [
      {"file": "auth_handler.py", "original_loc": 543, "new_loc": 245, "batch": 1},
      {"file": "payment.py", "original_loc": 489, "new_loc": 210, "batch": 1},
      {"file": "notification.py", "original_loc": 501, "new_loc": 198, "batch": 1}
    ],
    "pending": ["user_service.py", "user_utils.py"],
    "failed": [],
    "skipped": []
  }
}
```

### Required Fields for --continue

| Field | Purpose | If Missing |
|-------|---------|------------|
| `schema_version` | Must be "2.0" | --continue fails |
| `execution_plan.batches` | Ordered batch sequence | Cannot determine resume point |
| `execution_plan.batches[].status` | Track completion | Cannot identify next batch |
| `clusters` | Dependency groupings | Cannot reconstruct batching logic |
| `clusters[].mode` | serial vs parallel | Cannot safely spawn agents |
| `clusters[].shared_tests` | Why serial | Missing context on resume |
| `file_status.pending` | What's left to do | Cannot show progress |

### Schema Migration

Current schema version: **2.1** (adds `ralph_state` tracking over 2.0; see the field table above). Old v1.0 state files (without `schema_version` or with flat arrays) are **NOT compatible**.

If you have an old state file, delete it and run fresh:
```bash
rm .claude/state/code-quality-batch.json
/code_quality --fix
```

---

## Batch Progress Display

After each batch completes, show progress:

```
╔══════════════════════════════════════════════════════════╗
║                    BATCH PROGRESS                        ║
╠══════════════════════════════════════════════════════════╣
║  Batch:       2 of 4                                     ║
║  Completed:   6 files                                    ║
║  Pending:     9 files                                    ║
║  Failed:      1 file                                     ║
║  Skipped:     0 files                                    ║
╠══════════════════════════════════════════════════════════╣
║  Status:      Waiting for user confirmation              ║
║  Est. remaining batches: 2                               ║
╚══════════════════════════════════════════════════════════╝

To continue: Select "Continue next batch" when prompted
To stop: Select "Stop here" - state will be saved
To resume later: Run `/code_quality --continue`
```

---

## TASKLIST INTEGRATION (MANDATORY)

### Rule-Level Task Creation Pattern

At workflow start (STEP 1.26 for rule-level mode), create tasks for each quality category:

```
# Create rule-level tasks with dependencies
TaskCreate(subject="Fix Complexity Violations", description="Reduce cyclomatic complexity < 12 per function", activeForm="Fixing complexity")
TaskCreate(subject="Fix Function-Length Violations", description="Split functions > 100 lines", activeForm="Fixing function lengths")
TaskCreate(subject="Fix File-Size Violations", description="Reduce files > 500 LOC", activeForm="Fixing file sizes")

# Set up dependency chain (complexity -> function-length -> file-size)
TaskUpdate(taskId="function-length task ID", addBlockedBy=["complexity task ID"])
TaskUpdate(taskId="file-size task ID", addBlockedBy=["function-length task ID"])
```

### Batch Task Creation Pattern

For all-rules mode (STEP 4), create batch and file tasks:

```python
# Create batch-level parent tasks
for batch in execution_plan.batches:
    TaskCreate(
        subject=f"Batch {batch.number}: {batch.cluster_id}",
        description=f"Process {len(batch.files)} files in {batch.mode} mode",
        activeForm=f"Processing batch {batch.number}",
        metadata={
            "cluster_id": batch.cluster_id,
            "mode": batch.mode,
            "files": batch.files
        }
    )

# Create file-level child tasks within batch
for file in batch.files:
    TaskCreate(
        subject=f"Refactor: {os.path.basename(file)}",
        description=f"Reduce LOC from {original_loc} to < {threshold}",
        activeForm=f"Refactoring {file}",
        metadata={
            "batch": batch.number,
            "original_loc": original_loc,
            "target_threshold": threshold
        }
    )
```

### Agent Dispatch Tracking

```python
# When spawning safe-refactor agent
TaskUpdate(taskId=file_task_id, status="in_progress", owner="safe-refactor")

# After agent completes (from TaskOutput)
TaskUpdate(
    taskId=file_task_id,
    status="completed",
    metadata={
        "result_status": agent_result.status,  # fixed|partial|failed
        "new_loc": agent_result.new_structure.largest_file_loc,
        "git_verified": agent_result.verification.git_shows_changes
    }
)

# If hallucination detected (CRITICAL)
TaskUpdate(
    taskId=file_task_id,
    status="completed",
    metadata={
        "result_status": "failed",
        "hallucination_detected": True,
        "reason": "Agent claimed success but git shows no modifications"
    }
)
```

### Ralph Loop Bridge Pattern (for --loop mode)

Before exiting session in --loop mode, persist task state:

```python
# BEFORE spawning fresh Claude instance
def persist_tasks_for_ralph():
    tasks = TaskList()
    bridge_data = {
        "session_id": current_session,
        "command": "code-quality",
        "tasks": [{"id": t.id, "subject": t.subject, "status": t.status,
                   "blockedBy": t.blockedBy, "metadata": t.metadata}
                  for t in tasks],
        "ralph_state": {
            "current_rule": STATE.ralph_state.current_rule,
            "rules_completed": STATE.ralph_state.rules_completed,
            "rules_remaining": STATE.ralph_state.rules_remaining
        },
        "timestamp": now()
    }
    write_json(".claude/state/task-bridge-code-quality.json", bridge_data)

# AT session start (first action in --loop iteration)
def restore_tasks_from_ralph():
    if exists(".claude/state/task-bridge-code-quality.json"):
        bridge = read_json(".claude/state/task-bridge-code-quality.json")
        for task in bridge["tasks"]:
            if task["status"] != "completed":
                TaskCreate(subject=task["subject"],
                          blockedBy=task["blockedBy"],
                          metadata=task["metadata"])
```

### Progress Summary Pattern

After each batch or at command end:

```
╔══════════════════════════════════════════════════════════════╗
║                  CODE QUALITY TASK PROGRESS                   ║
╠══════════════════════════════════════════════════════════════╣
║  Rule Tasks:                                                  ║
║    Complexity:       Completed (3 functions fixed)            ║
║    Function-Length:  In Progress                              ║
║    File-Size:        Pending                                  ║
╠══════════════════════════════════════════════════════════════╣
║  Batch Progress (if all-rules mode):                          ║
║    Batch 1 (parallel): 3/3 files completed                   ║
║    Batch 2 (serial):   1/2 files in progress                 ║
║    Batch 3 (serial):   0/2 files pending                     ║
╠══════════════════════════════════════════════════════════════╣
║  Files: 4 completed | 1 in progress | 2 pending | 0 failed   ║
╚══════════════════════════════════════════════════════════════╝
```
