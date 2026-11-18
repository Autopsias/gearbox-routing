**PHASE FINAL GUARD: If `git_mode == "lightweight"`, SKIP this entire section. Delete `{file}.bak` if it exists, then proceed directly to JSON output. No git operations needed.**

---

# Common Refactoring Issues

## PHASE FINAL: Git cleanup (run on every exit)

This phase runs before the agent returns any result, on success and on failure.

Before returning success JSON, ALWAYS execute cleanup:

**Step 1: On FAILURE only, restore the baseline:**
```bash
git reset --hard "$BASELINE"   # the PHASE 0 checkpoint; it holds the caller's original tree
```

**Step 2: Carry the tree back to the original branch and delete the temp branch:**
```bash
git reset --soft "$ORIGINAL_BRANCH"   # HEAD to the original tip; the tree keeps the work
git reset -q                          # unstage; the caller commits, not this agent
git checkout "$ORIGINAL_BRANCH"
git branch -D "$TEMP_BRANCH"
```

**Step 3: Verify clean git state:**
```bash
if git branch --list 'temp/safe-refactor-*' | grep -q . || git stash list | grep -q "safe-refactor\|mikado-"; then
    echo "WARNING: Orphaned safe-refactor branches or stashes remain"
    STASH_CLEANUP="warning_orphaned"
else
    STASH_CLEANUP="none"
fi
```

**Step 4: Include the state in JSON output:**
The `stash_cleanup` field MUST be included in your output JSON. This agent uses no stash, so the value is
`"none"`, or `"warning_orphaned"` if Step 3 found a leftover branch or stash.

**WARNING**: The orchestrator WILL verify git state after this agent completes.
If an orphaned branch or stash is found, the orchestrator will flag this as a workflow bug.

## Anti-hallucination requirements

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
1. **By name**: "use the safe-refactor agent on path/to/file.py" (it is a subagent, not a slash command)
2. **Task delegation**: `Task(subagent_type="safe-refactor", ...)`
3. **Intent detection**: "split this file into smaller modules"
4. **Orchestrator dispatch**: With cluster context for parallel safety
