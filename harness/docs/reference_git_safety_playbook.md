# Git Safety Playbook (agent-authoring reference)

Worked patterns behind the invariants in `~/.claude/rules/git-safety.md`. That
file is always loaded; this one is not — read it when writing or debugging an
agent that manipulates git state.

---

## Prohibited patterns, with the failure each causes

### 1. Bare `git stash` without a cleanup plan

```bash
# WRONG - Creates hidden state with no cleanup
git stash push -m "some-checkpoint"
# ... do work ...
# Agent exits without dropping stash
```

The stash persists indefinitely. If the agent crashes, times out, or forgets to
clean up, it remains as a hidden landmine.

### 2. Relying on stash across process boundaries

```bash
# WRONG - Agent 1 creates stash, expects Agent 2 to pop it
# Agent 1:
git stash push -m "handoff"
# Agent 2:
git stash pop  # May pop wrong stash, or fail if Agent 1 never stashed
```

Stash is a stack. Other operations may push/pop entries between agents.

### 3. Using stash for baseline preservation

```bash
# WRONG - Stash for "emergency rollback"
git stash push -m "baseline"
# ... make changes ...
# If success: drop stash (often forgotten)
# If failure: pop stash
```

The success path often forgets to drop. Work appears in the working tree, the
stash contains OLD state, confusion ensues.

---

## Required patterns

### 1. Temp branch pattern (PREFERRED)

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

Visible in `git log` and `git branch`; pushable to remote for backup; the
orchestrator can verify with `git branch --list temp/agent-*`; cleanup is
explicit.

### 2. If stash is necessary, use explicit cleanup

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

### 3. Report git state in output

```json
{
  "git_state": {
    "stash_cleanup": "dropped|none|preserved_for_rollback|warning_orphaned",
    "temp_branches_cleaned": true,
    "working_tree_clean": false
  }
}
```

### 4. Verify clean state before returning

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

## Orchestrator verification

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

## Pattern reference

| Operation | Recommended | Avoid |
|-----------|-------------|-------|
| Baseline checkpoint | Temp branch | `git stash` |
| Incremental save | `git commit` on temp branch | `git stash` |
| Rollback | `git reset --hard` or `git checkout` | `git stash pop` |
| Cleanup | `git branch -D` | (forgetting to drop stash) |

---

## Agent development checklist

- [ ] Does the agent use `git stash`? If so, is there a cleanup path for ALL exit scenarios?
- [ ] Is there a "PHASE FINAL" or equivalent cleanup step?
- [ ] Does the agent report `stash_cleanup` or equivalent in its output?
- [ ] Can the orchestrator verify the agent cleaned up properly?
- [ ] Is the temp branch pattern used instead of stash where possible?

---

## Recovery procedures

### Orphaned stash found

```bash
# List all stashes
git stash list

# If work is hidden in stash (working tree is clean but stash has changes):
git stash pop stash@{N}

# If stash contains OLD state (should be dropped):
git stash drop stash@{N}
```

### Orphaned temp branch found

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

## Example: correct agent workflow

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

## References

- Root cause analysis: safe-refactor agent stash bug
- Industry best practice: avoid `git stash` in automation
- Atlassian Git documentation on stash behavior
