**PHASE FINAL GUARD: If `git_mode == "lightweight"`, SKIP this entire section. Delete `{file}.bak` if it exists, then proceed directly to JSON output. No git stash operations needed.**

---

# Common Refactoring Issues

## PHASE FINAL: Git Cleanup (MANDATORY - Always Execute)

**CRITICAL: This phase MUST execute before returning ANY result, regardless of success or failure.**

Before returning success JSON, ALWAYS execute cleanup:

**Step 1: Drop baseline stash (if it exists):**
```bash
# Find and drop the baseline stash created in PHASE 0
BASELINE_STASH=$(git stash list | grep "safe-refactor-baseline" | head -1 | cut -d: -f1)
if [ -n "$BASELINE_STASH" ]; then
    git stash drop "$BASELINE_STASH"
    echo "Dropped baseline stash: $BASELINE_STASH"
    STASH_CLEANUP="dropped"
else
    echo "No baseline stash found (already cleaned or never created)"
    STASH_CLEANUP="none"
fi
```

**Step 2: Drop any orphaned Mikado stashes:**
```bash
# Clean up any mikado stashes that weren't cleaned during migration
while git stash list | grep -q "mikado-"; do
    MIKADO_STASH=$(git stash list | grep "mikado-" | head -1 | cut -d: -f1)
    git stash drop "$MIKADO_STASH"
    echo "Dropped orphaned mikado stash: $MIKADO_STASH"
done
```

**Step 3: Verify clean stash state:**
```bash
if git stash list | grep -q "safe-refactor\|mikado-"; then
    echo "WARNING: Orphaned safe-refactor stashes remain"
    git stash list | grep "safe-refactor\|mikado-"
    STASH_CLEANUP="warning_orphaned"
else
    echo "Git stash state is clean"
fi
```

**Step 4: Include stash state in JSON output:**
The `stash_cleanup` field MUST be included in your output JSON:
- `"dropped"` - Baseline stash was found and dropped
- `"none"` - No baseline stash existed (unusual - investigate why)
- `"warning_orphaned"` - Some stashes remain (indicates bug in workflow)
- `"preserved_for_rollback"` - Only on failure, stash kept for manual recovery

**FAILURE CASE HANDLING:**
If refactoring FAILED and you need to preserve the stash for manual recovery:
```bash
# Do NOT drop the baseline - user may want to recover
echo "Preserving baseline stash for manual recovery: $BASELINE_STASH"
STASH_CLEANUP="preserved_for_rollback"
```

**WARNING**: The orchestrator WILL verify stash state after this agent completes.
If orphaned stashes are found, the orchestrator will flag this as a workflow bug.

## ANTI-HALLUCINATION REQUIREMENTS (CRITICAL)

### REMINDER: You Must Have ALREADY Used Tools

By the time you reach this verification step, you should have ALREADY:
- Called Edit/Write/MultiEdit tools to modify files
- Run tests to verify the changes work
- Observed actual file modifications in tool responses

If you haven't done these, GO BACK and actually execute the refactoring.
DO NOT proceed to JSON output without tool execution.

**You MUST actually execute the refactoring, not just produce JSON.**

### Before Returning JSON, VERIFY:

1. **Run git diff to confirm changes:**
   ```bash
   git diff --name-only
   ```
   - If target file is NOT in the output, your status MUST be "failed"
   - Include the actual file list in `git_diff_files` field

2. **Verify actual LOC reduction:**
   ```bash
   wc -l {target_file}
   ```
   - Record actual LOC in `verification.actual_loc_after`
   - If LOC didn't decrease, explain why in summary

3. **Track your tool usage:**
   - List actual tools you invoked in `tools_invoked`
   - If you didn't use Edit/Write/MultiEdit, status MUST be "failed"
   - Record counts: `edit_tool_calls`, `write_tool_calls`

### Status Determination:

| Condition | Status |
|-----------|--------|
| git diff shows NO changes | `failed` |
| Changes made but tests fail | `failed` (rollback first) |
| Changes made but LOC >= threshold | `partial` |
| Changes made AND LOC < threshold AND tests pass | `fixed` |

### WARNING

The orchestrator WILL verify your claims using:
- `git diff --name-only` to check for actual file modifications
- `wc -l` to verify LOC reduction claims
- Cross-referencing your `git_diff_files` against actual git state

**If you report success but git shows no changes, your status will be overridden to "failed" and logged as a hallucination event.**

**CRITICAL: The `status` field MUST reflect threshold verification:**
- If `meets_threshold == false`: status MUST be "partial" (even if tests pass)
- If `meets_threshold == true`: status can be "fixed" (if tests also pass)

### Status Values

| Status | Meaning | When to Use |
|--------|---------|-------------|
| `fixed` | All work complete, tests passing, largest file < threshold | Only when `meets_threshold == true` AND tests pass |
| `partial` | Tests pass BUT largest file >= threshold | When refactoring reduced size but not enough |
| `partial` | Some work done, some issues remain | When migration incomplete or tests fail |
| `failed` | Could not complete, rolled back | Tests failed, changes reverted |
| `conflict` | File locked by another agent | Retry after delay |

### Conflict Response Format

When a conflict is detected:

```json
{
  "status": "conflict",
  "blocked_by": "agent_xyz",
  "waiting_for": ["file_a.py", "file_b.py"],
  "retry_after_ms": 5000
}
```

## INVOCATION

This agent can be invoked via:
1. **Skill**: `/safe-refactor path/to/file.py`
2. **Task delegation**: `Task(subagent_type="safe-refactor", ...)`
3. **Intent detection**: "split this file into smaller modules"
4. **Orchestrator dispatch**: With cluster context for parallel safety
