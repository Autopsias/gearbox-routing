---
description: "Scans codebase for quality violations (file size >500 LOC, function length >100 lines, complexity >12) and dispatches safe-refactor agents to fix them. Use when you say 'check code/file size and complexity', 'files too large', 'reduce complexity', 'split large files'."
argument-hint: "[--check] [--fix] [--dry-run] [--refresh-exceptions] [--focus=file-size|function-length|complexity] [--path=...] [--max-parallel=N] [--no-chain] [--continue] [--loop N] [--loop-delay S] [--fix-single-rule]"
allowed-tools: ["Task", "Bash", "Grep", "Read", "Glob", "SlashCommand", "AskUserQuestion", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet"]
---

# Code Quality Orchestrator

Analyze and fix code quality violations for: "$ARGUMENTS"

## CRITICAL: ORCHESTRATION ONLY

**MANDATORY**: This command NEVER fixes code directly.
- Use Bash/Grep/Read for READ-ONLY analysis
- Delegate ALL fixes to specialist agents
- Guard: "Am I about to edit a file? STOP and delegate."

---

## STEP 1: Parse Arguments

Parse flags from "$ARGUMENTS":
- `--check`: Analysis only, no fixes (DEFAULT if no flags provided)
- `--fix`: Analyze and delegate fixes to agents with TEST-SAFE workflow
- `--dry-run`: Show refactoring plan without executing changes
- `--focus=file-size|function-length|complexity`: Filter to specific issue type
- `--path=apps/api|apps/web`: Limit scope to specific directory
- `--max-parallel=N`: Maximum parallel agents (default: 3, max: 4)
  WARNING: >4 parallel agents causes context degradation and hallucination risk
- `--no-chain`: Disable automatic chain invocation after fixes
- `--continue`: Resume from saved batch state (do NOT start fresh analysis)
- `--refresh-exceptions`: Regenerate exception baselines to remove stale entries (no fixes)
- `--ralph`: Force Ralph Loop for ALL files (skip single-shot attempt)
  Use when you want guaranteed reliability at the cost of speed
- `--no-ralph`: Disable auto-Ralph fallback on hallucination detection
  WARNING: Without Ralph fallback, hallucinated files will be marked as failed (no retry)
- `--lightweight`: Force lightweight mode (no git operations) for ALL file types
  Use when refactoring shell scripts or when git safety is causing more harm than good
- `--loop N`: Enable fresh-context loop mode (spawns new Claude instances for unattended execution)
  Max N iterations with completely fresh 200K context per iteration
- `--loop-delay S`: Seconds to wait between loop iterations (default: 5)

If no arguments provided, default to `--check` (analysis only).

### Special: --refresh-exceptions

Single-branch flag, only relevant when `$ARGUMENTS` contains `--refresh-exceptions`:
`Read ~/.claude/commands/references/code-quality/refresh-exceptions.md` and run it,
then exit (no other steps execute).

---

## STEP 1.25: Ralph Loop Mode Detection (Fresh Context)

**Instructions:** Read `~/.claude/commands/references/code-quality/chaining-logic.md` and follow the "Ralph Loop Mode Detection" section. If `--loop` is present, launch the runner script and EXIT. Otherwise proceed to STEP 1.26.

---

## STEP 1.26: Rule-Level Mode Detection (Phase Granularity)

**Instructions:** Read `~/.claude/commands/references/code-quality/chaining-logic.md` and follow the "Rule-Level Mode Detection" section. If `--fix-single-rule` is present, execute only ONE category and EXIT. Otherwise proceed to STEP 1.5.

---

## STEP 1.5: Load Batch State (if --continue)

If `--continue` flag provided:

### 1. Validate State File Exists and Has Correct Schema

```bash
if [ ! -f .claude/state/code-quality-batch.json ]; then
    echo "ERROR: No saved state found at .claude/state/code-quality-batch.json"
    echo "Run '/code_quality --fix' first to create a state file."
    # EXIT - cannot continue without state
fi

# Check schema version (v2.0 or v2.1 required)
SCHEMA=$(cat .claude/state/code-quality-batch.json | python3 -c "import json,sys; print(json.load(sys.stdin).get('schema_version','1.0'))" 2>/dev/null || echo "1.0")
if [ "$SCHEMA" != "2.0" ] && [ "$SCHEMA" != "2.1" ]; then
    echo "ERROR: State file uses incompatible schema version ($SCHEMA)"
    echo "State v2.0/v2.1 required. Delete old state and run fresh analysis:"
    echo "  rm .claude/state/code-quality-batch.json"
    echo "  /code_quality --fix"
    # EXIT - cannot continue with old schema
fi

echo "Valid v${SCHEMA} state file found"
cat .claude/state/code-quality-batch.json
```

### 2. Parse Execution Plan (DO NOT re-analyze)

Read the state file and extract:
- `execution_plan.batches` - The ordered batch sequence
- `execution_plan.current_batch` - Next batch number to process
- `clusters` - Full cluster definitions with mode and shared_tests
- `file_status.pending` - Files waiting to be processed

**CRITICAL: DO NOT rebuild dependency graph. Trust the saved execution plan.**

Find the resume point:
```python
# Pseudocode for finding resume point
batches = state["execution_plan"]["batches"]
for batch in batches:
    if batch["status"] != "completed":
        resume_batch = batch
        break
```

### 3. Skip PHASE 1-3 Entirely

When `--continue` is used with valid v2.0 state:
- DO NOT run quality analysis scripts (STEP 2)
- DO NOT generate quality report (STEP 3)
- DO NOT rebuild dependency graph (PHASE 1)
- DO NOT re-identify clusters (PHASE 2)
- DO NOT recalculate priorities (PHASE 3)
- Jump directly to PHASE 4 using saved execution plan

### 4. Display Resume Context

```
RESUMING FROM SAVED STATE
  State file:     .claude/state/code-quality-batch.json
  Schema version: {version}
  Session ID:     {session_id}
  Progress:
    Completed batches: {N} of {M}
    Files completed:   {X}
    Files remaining:   {Y}
  Next batch: #{batch_number}
    Files: {file_list}
    Mode:  {serial|parallel}
    Reason: {reason if serial}
```

### 5. Prompt Before Continuing

```
AskUserQuestion(
  questions=[{
    "question": "Resume batch {N}/{M}? ({X} files: {file_list}) - {mode} mode",
    "header": "Resume Batch",
    "options": [
      {"label": "Yes, resume processing", "description": "Continue from saved state with batch {N}"},
      {"label": "No, abort", "description": "Exit without processing. State preserved for later."},
      {"label": "Start fresh", "description": "Delete state and run new analysis"}
    ],
    "multiSelect": false
  }]
)
```

**On "Yes, resume"**: Jump to PHASE 4 with loaded batch from execution_plan
**On "No, abort"**: Exit gracefully, state file remains intact
**On "Start fresh"**: Delete state file and continue to STEP 2 (full analysis)

---

## STEP 2: Run Quality Analysis

**Instructions:** Read `~/.claude/commands/references/code-quality/analysis-rules.md` and follow the "Quality Check Scripts" and "Violation Categories" sections.

---

## STEP 3: Generate Quality Report

**Instructions:** Read `~/.claude/commands/references/code-quality/analysis-rules.md` and follow the "Quality Report Format" section.

---

## STEP 4: Smart Parallel Refactoring (if --fix or --dry-run flag provided)

**Instructions:** Read `~/.claude/commands/references/code-quality/agent-dispatch.md` and follow all phases:
- For `--dry-run`: Follow the "Dry Run Plan Format" section and exit.
- For `--fix`: Follow PHASE 0 through PHASE 4 for dependency-aware batched execution.

After each batch of agents completes, execute the BATCH GATE:

**Instructions:** Read `~/.claude/commands/references/code-quality/troubleshooting.md` and follow:
- "Batch Gate" section (save state, wait for agents)
- "Anti-Hallucination Verification" section (verify git changes)
- "Auto-Ralph on Hallucination" section (if hallucination detected)
- "Verify Git Stash State" section (check for orphaned stashes)
- "Batch User Prompt" section (prompt user before next batch)
- "Failure Handling" section (if agent fails)
- "Early Termination Check" section (after each batch)

---

## STEP 5: Parallel-Safe Operations (Linting, Type Errors)

**Instructions:** Read `~/.claude/commands/references/code-quality/analysis-rules.md` and follow the "Parallel-Safe Operations" section.

---

## STEP 6: Verify Results and Update Exceptions (after --fix)

**Instructions:** Read `~/.claude/commands/references/code-quality/analysis-rules.md` and follow the "Verify Results and Update Exceptions" section (re-run checks + refresh stale exceptions).

---

## STEP 7: Report Summary

Output final status:

```
## Code Quality Summary

### Execution Mode
- Dependency-aware smart batching: YES
- Clusters identified: 3
- Parallel batches: 1
- Serial batches: 2

### Before
- File size violations: X
- Function length violations: Y
- Test file warnings: Z

### After (if --fix was used)
- File size violations: A
- Function length violations: B
- Test file warnings: C

### Refactoring Results
| Cluster | Files | Mode | Status |
|---------|-------|------|--------|
| Cluster B | 3 | parallel | COMPLETE |
| Cluster A | 2 | serial | 1 skipped |
| Cluster C | 3 | serial | COMPLETE |

### Skipped Files (user decision)
- user_utils.py: TestFailed (user chose continue)

### Status
[PASS/FAIL based on blocking violations]

### Time Breakdown
- Dependency analysis: ~30s
- Parallel batch (3 files): ~4 min
- Serial batches (5 files): ~15 min
- Total: ~20 min (saved ~8 min vs fully serial)

### Suggested Next Steps
- If violations remain: Run `/code_quality --fix` to auto-fix
- If all passing: Run `/pr --fast` to commit changes
- For skipped files: Run `/test_orchestrate` to investigate test failures
```

---

## STEP 8: Chain Invocation (unless --no-chain)

**Instructions:** Read `~/.claude/commands/references/code-quality/chaining-logic.md` and follow the "Chain Invocation" section.

---

## STEP 4-RALPH: Ralph Loop Mode (DEFAULT FALLBACK or --ralph flag)

**Instructions:** Read `~/.claude/commands/references/code-quality/chaining-logic.md` and follow the "Ralph Loop Mode" section (STEP 4-RALPH).

---

## Examples

```
# Check only (default)
/code_quality

# Check with specific focus
/code_quality --focus=file-size

# Preview refactoring plan (no changes made)
/code_quality --dry-run

# Auto-fix all violations with smart batching (default max 6 parallel)
/code_quality --fix

# Auto-fix with lower parallelism (e.g., resource-constrained)
/code_quality --fix --max-parallel=3

# Auto-fix only Python backend
/code_quality --fix --path=apps/api

# Auto-fix without chain invocation
/code_quality --fix --no-chain

# Preview plan for specific path
/code_quality --dry-run --path=apps/web

# Refresh stale exceptions (remove entries for already-fixed functions)
/code_quality --refresh-exceptions

# Resume from previous batch
/code_quality --continue

# Force Ralph Loop for ALL files (skip single-shot attempt)
/code_quality --fix --ralph

# Disable auto-Ralph fallback (fail on hallucination instead of retry)
/code_quality --fix --no-ralph
```

**Default behavior (recommended):**
```bash
/code_quality --fix  # Single-shot first -> auto-Ralph on hallucination detection
```

---

## TASKLIST INTEGRATION (MANDATORY)

**Instructions:** Read `~/.claude/commands/references/code-quality/troubleshooting.md` and follow the "TASKLIST INTEGRATION" section for task creation, dispatch tracking, Ralph loop bridge, and progress summary patterns.
