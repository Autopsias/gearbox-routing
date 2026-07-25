# Merge Conflict Resolution Details

## Pre-Push Conflict Check (MANDATORY)

**BEFORE ANY PUSH OPERATION, check if PR has merge conflicts:**

```bash
# Check if current branch has a PR with merge conflicts
BRANCH=$(git branch --show-current)
PR_INFO=$(gh pr list --head "$BRANCH" --json number,mergeStateStatus -q '.[0]' 2>/dev/null)

if [[ -n "$PR_INFO" && "$PR_INFO" != "null" ]]; then
    MERGE_STATE=$(echo "$PR_INFO" | jq -r '.mergeStateStatus // "UNKNOWN"')
    PR_NUM=$(echo "$PR_INFO" | jq -r '.number')

    if [[ "$MERGE_STATE" == "DIRTY" ]]; then
        echo ""
        echo "WARNING: PR #$PR_NUM has merge conflicts with base branch!"
        echo ""
        echo "GitHub Actions LIMITATION:"
        echo "   The 'pull_request' event will NOT trigger when PRs have conflicts."
        echo ""
        echo "Jobs that WON'T run:"
        echo "   - E2E Tests (4 shards)"
        echo "   - UAT Tests"
        echo "   - Performance Benchmarks"
        echo "   - Burn-in / Flaky Test Detection"
        echo ""
        echo "Jobs that WILL run (via push event):"
        echo "   - Lint (Python + TypeScript)"
        echo "   - Unit Tests (Backend + Frontend)"
        echo "   - Quality Gate"
        echo ""
        echo "RECOMMENDED: Sync with base branch first:"
        echo "   Option 1: /pr sync"
        echo "   Option 2: git fetch origin main && git merge origin/main"
        echo ""

        # Return this status to inform caller
        CONFLICT_STATUS="DIRTY"
    else
        CONFLICT_STATUS="CLEAN"
    fi
else
    CONFLICT_STATUS="NO_PR"
fi
```

**WHY THIS MATTERS:** GitHub Actions docs state:
> "Workflows will not run on pull_request activity if the pull request has a merge conflict."

This is a known GitHub limitation since 2019. Without this check, users won't know why their E2E tests aren't running.

## Sync Branch (IMPORTANT for CI)

**Use this when PR has merge conflicts to enable full CI coverage:**

```bash
# Detect base branch from PR or Git config
BASE_BRANCH=$(gh pr view --json baseRefName -q '.baseRefName' 2>/dev/null)
if [[ -z "$BASE_BRANCH" ]]; then
    BASE_BRANCH=$(git config --get init.defaultBranch 2>/dev/null || echo "main")
fi

echo "Syncing with $BASE_BRANCH to resolve conflicts..."
echo "   This will enable E2E, UAT, and Benchmark CI jobs."
echo ""

# Fetch latest
git fetch origin "$BASE_BRANCH"

# Attempt merge
if git merge "origin/$BASE_BRANCH" --no-edit; then
    echo ""
    echo "Successfully synced with $BASE_BRANCH"
    echo "   PR merge state should now be CLEAN"
    echo "   Full CI (including E2E/UAT) will run on next push"
    echo ""

    # Push the merge
    git push

    # Verify merge state is now clean
    NEW_STATE=$(gh pr view --json mergeStateStatus -q '.mergeStateStatus' 2>/dev/null)
    if [[ "$NEW_STATE" == "CLEAN" || "$NEW_STATE" == "UNSTABLE" || "$NEW_STATE" == "HAS_HOOKS" ]]; then
        echo "PR merge state is now: $NEW_STATE"
        echo "   pull_request events will now trigger!"
    else
        echo "PR merge state: $NEW_STATE (may still have issues)"
    fi
else
    echo ""
    echo "Merge conflicts detected!"
    echo ""
    echo "Files with conflicts:"
    git diff --name-only --diff-filter=U
    echo ""
    echo "Please resolve manually, then:"
    echo "  1. Edit conflicting files"
    echo "  2. git add <resolved-files>"
    echo "  3. git commit"
    echo "  4. git push"
fi
```

## Merge PR

```bash
# Detect merge strategy based on branch type
CURRENT_BRANCH=$(git branch --show-current)

if [[ "$CURRENT_BRANCH" =~ ^(epic-|feature/epic) ]]; then
    # Epic branches: preserve full commit history with merge commit
    MERGE_STRATEGY="merge"
    DELETE_BRANCH=""  # Don't auto-delete epic branches

    # Tag the branch before merge for easy recovery
    TAG_NAME="archive/${CURRENT_BRANCH//\//-}"  # Replace / with - for valid tag name
    git tag "$TAG_NAME" HEAD 2>/dev/null || echo "Tag already exists"
    git push origin "$TAG_NAME" 2>/dev/null || true

    echo "Tagged branch as: $TAG_NAME (for recovery)"
else
    # Feature/fix branches: squash to keep main history clean
    MERGE_STRATEGY="squash"
    DELETE_BRANCH="--delete-branch"
fi

# Merge with detected strategy
gh pr merge --${MERGE_STRATEGY} ${DELETE_BRANCH}

# Cleanup
git checkout "$BASE_BRANCH"
git pull origin "$BASE_BRANCH"

# For epic branches, remind about the archive tag
if [[ -n "$TAG_NAME" ]]; then
    echo "Epic branch preserved at tag: $TAG_NAME"
    echo "   Recover with: git checkout $TAG_NAME"
fi
```
