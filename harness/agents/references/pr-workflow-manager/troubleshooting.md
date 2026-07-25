# Common PR Workflow Issues

## Error Handling

```bash
# PR already exists
if gh pr view &> /dev/null; then
    echo "PR already exists for this branch"
    gh pr view
    exit 0
fi

# Not on a branch
if [[ $(git branch --show-current) == "" ]]; then
    echo "Error: Not on a branch (detached HEAD)"
    exit 1
fi

# No changes
if [[ -z $(git log origin/$BASE_BRANCH..HEAD) ]]; then
    echo "Error: No commits to create PR from"
    exit 1
fi
```

## Natural Language Processing

Parse user intent from natural language:

```python
INTENT_PATTERNS = {
    r'create.*PR': 'create_pr',
    r'PR.*status|status.*PR': 'check_status',
    r'update.*PR': 'update_pr',
    r'ready.*merge|merge.*ready': 'validate_merge',
    r'merge.*PR|merge this': 'merge_pr',
    r'sync.*branch|update.*branch': 'sync_branch',
}
```

## Best Practices

### DO:
- **Check for merge conflicts BEFORE every push** (critical for CI)
- Use gh CLI for all GitHub operations
- Auto-detect everything from Git
- Generate descriptions from commits
- Use --fast mode when requested (skip validation)
- Use git commit directly (hooks are now fast)
- Clean up branches after merge
- Delegate to ci_orchestrate for CI issues (when not in --fast mode)
- Warn users when E2E/UAT won't run due to conflicts
- Offer `/pr sync` to resolve conflicts

### DON'T:
- Push without checking merge state first
- Let users be surprised by missing CI jobs
- Hardcode branch names
- Assume project structure
- Create state files
- Make project-specific assumptions
- Delegate to orchestrators when --fast is specified
- Add unnecessary overhead to simple update operations

## Git Introspection (Auto-Detect Everything)

### Detect Base Branch
```bash
# Start with Git default
BASE_BRANCH=$(git config --get init.defaultBranch 2>/dev/null || echo "main")

# Check common alternatives
git branch -r | grep -q "origin/develop" && BASE_BRANCH="develop"
git branch -r | grep -q "origin/master" && BASE_BRANCH="master"
git branch -r | grep -q "origin/next" && BASE_BRANCH="next"

# For this specific branch, check if it has a different target
CURRENT_BRANCH=$(git branch --show-current)
# If on epic-X branch, might target v2-expansion
git branch -r | grep -q "origin/v2-expansion" && [[ "$CURRENT_BRANCH" =~ ^epic- ]] && BASE_BRANCH="v2-expansion"
```

### Detect Branching Pattern
```bash
# Detect from existing branches
if git branch -a | grep -q "feature/"; then
    PATTERN="feature-based"
elif git branch -a | grep -q "story/"; then
    PATTERN="story-based"
elif git branch -a | grep -q "epic-"; then
    PATTERN="epic-based"
else
    PATTERN="simple"
fi
```

### Detect Current PR
```bash
# Check if current branch has PR
gh pr view --json number,title,state,url 2>/dev/null || echo "No PR for current branch"
```

## Output Format

```markdown
## PR Operation Complete

### Action
[What was done: Created PR / Checked status / Merged PR]

### Details
- **Branch:** feature/add-auth
- **Base:** main
- **PR:** #123
- **URL:** https://github.com/user/repo/pull/123

### Status
- PR created successfully
- CI checks passing
- Awaiting review

### Next Steps
[If any actions needed]
```
