# Git Safety Rules for Agents

**PRIORITY: HIGH** - These rules prevent git state corruption and lost work in automated workflows.

---

## Core Principle

**Git operations in automated agents must be VISIBLE and RECOVERABLE.**

Hidden state (like `git stash`) is dangerous because:
- Orchestrators cannot verify what state exists
- State persists across sessions, causing confusion
- No natural "finally" cleanup mechanism
- Work can appear "lost" when it's hidden in stash

---

## Prohibited Patterns

### 1. Bare `git stash` Without Cleanup Plan
```bash
# WRONG - Creates hidden state with no cleanup
git stash push -m "some-checkpoint"
# ... do work ...
# Agent exits without dropping stash
```

**Why it's dangerous:** The stash persists indefinitely. If the agent crashes, times out, or forgets to cleanup, the stash remains as a hidden landmine.

### 2. Relying on Stash Across Process Boundaries
```bash
# WRONG - Agent 1 creates stash, expects Agent 2 to pop it
# Agent 1:
git stash push -m "handoff"
# Agent 2:
git stash pop  # May pop wrong stash, or fail if Agent 1 never stashed
```

**Why it's dangerous:** Stash is a stack. Other operations may push/pop entries between agents.

### 3. Using Stash for Baseline Preservation
```bash
# WRONG - Stash for "emergency rollback"
git stash push -m "baseline"
# ... make changes ...
# If success: drop stash (often forgotten)
# If failure: pop stash
```

**Why it's dangerous:** Success path often forgets to drop. Work appears in working tree, stash contains OLD state. Confusion ensues.

---

## Required Patterns

### 1. Temp Branch Pattern (PREFERRED)

For baseline preservation, use temp branches instead of stash:

```bash
# Create visible baseline checkpoint
ORIGINAL_BRANCH=$(git branch --show-current)
TIMESTAMP=$(date +%s)

git checkout -b temp/agent-checkpoint-$TIMESTAMP
git add -A
git commit -m "agent checkpoint" --allow-empty

# ... do work ...

# Cleanup (whether success or failure)
git checkout $ORIGINAL_BRANCH
git branch -D temp/agent-checkpoint-$TIMESTAMP
```

**Benefits:**
- Visible in `git log` and `git branch`
- Can be pushed to remote for backup
- Orchestrator can verify: `git branch --list temp/agent-*`
- Cleanup is explicit and verifiable

### 2. If Stash Is Necessary, Use Explicit Cleanup

When stash is unavoidable:

```bash
# Create with identifiable name
STASH_NAME="agent-{agent_type}-$(date +%s)"
git stash push -m "$STASH_NAME"

# ... do work ...

# MANDATORY: Cleanup before returning
STASH_REF=$(git stash list | grep "$STASH_NAME" | head -1 | cut -d: -f1)
if [ -n "$STASH_REF" ]; then
    git stash drop "$STASH_REF"
fi
```

### 3. Report Git State in Output

All agents that modify git state MUST include in their JSON output:

```json
{
  "git_state": {
    "stash_cleanup": "dropped|none|preserved_for_rollback|warning_orphaned",
    "temp_branches_cleaned": true,
    "working_tree_clean": false
  }
}
```

### 4. Verify Clean State Before Returning

Before returning results, agents MUST verify:

```bash
# Check for orphaned agent stashes
if git stash list | grep -q "agent-"; then
    echo "WARNING: Orphaned agent stashes exist"
    STASH_CLEANUP="warning_orphaned"
fi

# Check for orphaned temp branches
if git branch --list temp/agent-* | grep -q .; then
    echo "WARNING: Orphaned temp branches exist"
fi
```

---

## Orchestrator Verification

Orchestrators MUST verify agent git state after completion:

```bash
# After agent completes
ORPHANED_STASHES=$(git stash list | grep -E "agent-|safe-refactor|mikado-" | wc -l)
ORPHANED_BRANCHES=$(git branch --list temp/agent-* temp/safe-refactor-* | wc -l)

if [ "$ORPHANED_STASHES" -gt 0 ] || [ "$ORPHANED_BRANCHES" -gt 0 ]; then
    echo "ERROR: Agent left orphaned git state"
    echo "Stashes: $ORPHANED_STASHES"
    echo "Branches: $ORPHANED_BRANCHES"
    # Log as workflow bug, attempt recovery
fi
```

---

## Pattern Reference

| Operation | Recommended | Avoid |
|-----------|-------------|-------|
| Baseline checkpoint | Temp branch | `git stash` |
| Incremental save | `git commit` on temp branch | `git stash` |
| Rollback | `git reset --hard` or `git checkout` | `git stash pop` |
| Cleanup | `git branch -D` | (forgetting to drop stash) |

---

## Agent Development Checklist

When creating or modifying agents that use git:

- [ ] Does the agent use `git stash`? If so, is there a cleanup path for ALL exit scenarios?
- [ ] Is there a "PHASE FINAL" or equivalent cleanup step?
- [ ] Does the agent report `stash_cleanup` or equivalent in its output?
- [ ] Can the orchestrator verify the agent cleaned up properly?
- [ ] Is the temp branch pattern used instead of stash where possible?

---

## Recovery Procedures

### Orphaned Stash Found

```bash
# List all stashes
git stash list

# If work is hidden in stash (working tree is clean but stash has changes):
git stash pop stash@{N}

# If stash contains OLD state (should be dropped):
git stash drop stash@{N}
```

### Orphaned Temp Branch Found

```bash
# List temp branches
git branch --list temp/*

# If branch contains useful work:
git checkout temp/agent-xyz
git log -1  # Check what's there
git checkout original-branch
git merge temp/agent-xyz  # Or cherry-pick

# Cleanup
git branch -D temp/agent-xyz
```

---

## Example: Correct Agent Workflow

```python
# Pseudocode for a safe agent workflow

def run_agent():
    # PHASE 0: Setup with temp branch (not stash)
    original_branch = git_current_branch()
    temp_branch = f"temp/agent-{uuid4()}"
    git_checkout_new_branch(temp_branch)
    git_commit("baseline checkpoint", allow_empty=True)

    try:
        # PHASE 1-N: Do actual work
        result = do_work()

        # PHASE FINAL: Cleanup
        git_checkout(original_branch)
        git_branch_delete(temp_branch)

        return {
            "status": "success",
            "git_state": {
                "temp_branches_cleaned": True,
                "stash_cleanup": "none"  # We didn't use stash
            }
        }
    except Exception as e:
        # Cleanup even on failure
        git_checkout(original_branch)
        git_branch_delete(temp_branch)

        return {
            "status": "failed",
            "error": str(e),
            "git_state": {
                "temp_branches_cleaned": True,
                "stash_cleanup": "none"
            }
        }
```

---

## Destructive Deletion of Pre-Existing Files

**PRIORITY: HIGH** — deleting work you did not create is hard to undo and easy to over-authorize.

- **NEVER delete or `rm -rf` a pre-existing file/directory you did not create without explicit, unambiguous confirmation that names the target.** A one-letter menu pick (e.g. "b") or a terse "yes" to a multi-part question is NOT sufficient authorization for an irreversible delete — re-confirm the specific path first.
- **NEVER bundle `rm -rf` with `git rm` in one command.** Use `git rm` alone — tracked deletions stay recoverable from history; `rm -rf` on top also destroys the untracked working copy.
- **Look before you delete.** Inspect the target first; if what you find contradicts how it was described, or you didn't create it, surface that instead of proceeding.
- Prefer reversible paths (`git rm`, or move-to-quarantine) over outright `rm -rf`.

*Why:* in one session a terse "b" was read as authorization for `git rm -r <dir> && rm -rf <dir>` on a pre-existing directory the agent never created; the harness correctly blocked it as scope escalation. Explicit, target-naming confirmation is the bar for any destructive delete.

---

## References

- Root cause analysis: safe-refactor agent stash bug (2025-01-08)
- Industry best practice: Avoid `git stash` in automation
- Atlassian Git documentation on stash behavior
