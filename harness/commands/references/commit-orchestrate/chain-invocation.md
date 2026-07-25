## INTELLIGENT CHAIN INVOCATION

**STEP 8: Automated Workflow Continuation**
After successful commit, intelligently invoke related commands:

```bash
# After commit success, check for workflow continuation
echo "Analyzing commit success for workflow continuation..."

# Check if user disabled chaining
if [[ "$ARGUMENTS" == *"--no-chain"* ]]; then
    echo "Auto-chaining disabled by user flag"
    exit 0
fi

# Prevent infinite loops
INVOCATION_DEPTH=${SLASH_DEPTH:-0}
if [[ $INVOCATION_DEPTH -ge 3 ]]; then
    echo "Maximum command chain depth reached. Stopping auto-invocation."
    exit 0
fi

# Set depth for next invocation
export SLASH_DEPTH=$((INVOCATION_DEPTH + 1))

# If --push-after flag was used and commit succeeded, create/update PR
if [[ "$ARGUMENTS" == *"--push-after"* ]] && [[ "$COMMIT_SUCCESS" == "true" ]]; then
    echo "Commit pushed to remote. Creating/updating PR..."
    SlashCommand(command="/pr create")
fi

# If on a feature branch and commit succeeded, offer PR creation
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "main" ]] && [[ "$CURRENT_BRANCH" != "master" ]] && [[ "$COMMIT_SUCCESS" == "true" ]]; then
    echo "Commit successful on feature branch: $CURRENT_BRANCH"

    # Check if PR already exists
    PR_EXISTS=$(gh pr view --json number 2>/dev/null)
    if [[ -z "$PR_EXISTS" ]]; then
        echo "No PR exists for this branch. Creating one..."
        SlashCommand(command="/pr create")
    else
        echo "PR already exists. Checking status..."
        SlashCommand(command="/pr status")
    fi
fi
```
