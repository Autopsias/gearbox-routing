# Auto-generated commit message heuristics

## Contents
- [Best Practices Reference](#best-practices-reference)
- [Good vs Bad Examples](#good-vs-bad-examples)
- [Generation logic](#generation-logic)

Read this when `USER_PROVIDED_MESSAGE` is empty (no message was supplied) — this is the
branch-only logic for synthesizing a Conventional Commits message from the staged diff.
When the user DID provide a message, skip this file entirely; the caller only needs the
validation block still inline in `commit-orchestrate.md` STEP 6.

## Best Practices Reference
Following Conventional Commits (conventionalcommits.org) and Git project standards:
- **Subject**: Imperative mood, ≤50 chars, no period, format: `<type>[scope]: <description>`
- **Body**: Explain WHY (not HOW), wrap at 72 chars, separate from subject with blank line
- **Footer**: Reference issues (`Closes #123`), note breaking changes
- **Types**: feat, fix, docs, style, refactor, perf, test, build, ci, chore

## Good vs Bad Examples
❌ BAD: "fix: address quality issues in auth.py" (vague, focuses on file not change)
✅ GOOD: "feat(auth): implement JWT refresh token endpoint" (specific, clear type/scope)

❌ BAD: "updated code" (past tense, no detail)
✅ GOOD: "refactor(api): simplify error handling middleware" (imperative, descriptive)

## Generation logic

```bash
echo "🤖 Generating intelligent commit message..."

# Analyze staged changes to determine type and scope
CHANGED_FILES=$(git diff --cached --name-only)
ADDED_FILES=$(git diff --cached --diff-filter=A --name-only | wc -l)
MODIFIED_FILES=$(git diff --cached --diff-filter=M --name-only | wc -l)
DELETED_FILES=$(git diff --cached --diff-filter=D --name-only | wc -l)
TEST_FILES=$(echo "$CHANGED_FILES" | grep -E "(test_|_test\.py|\.test\.|\.spec\.)" | wc -l)

# Detect commit type based on file patterns
TYPE="chore"  # default
SCOPE=""

if echo "$CHANGED_FILES" | grep -qE "^docs/"; then
  TYPE="docs"
elif echo "$CHANGED_FILES" | grep -qE "^test/|^tests/|test_|_test\.py"; then
  TYPE="test"
elif echo "$CHANGED_FILES" | grep -qE "\.github/|ci/|\.gitlab-ci"; then
  TYPE="ci"
elif [ "$ADDED_FILES" -gt 0 ] && [ "$TEST_FILES" -gt 0 ]; then
  TYPE="feat"  # New files + tests = feature
elif [ "$MODIFIED_FILES" -gt 0 ] && git diff --cached | grep -qE "^\+.*def |^\+.*class "; then
  # New functions/classes without breaking existing = likely feature
  if git diff --cached | grep -qE "^\-.*def |^\-.*class "; then
    TYPE="refactor"  # Modifying existing functions/classes
  else
    TYPE="feat"
  fi
elif git diff --cached | grep -qE "^\+.*#.*fix|^\+.*#.*bug"; then
  TYPE="fix"
elif git diff --cached | grep -qE "performance|optimize|speed"; then
  TYPE="perf"
fi

# Detect scope from directory structure
PRIMARY_DIR=$(echo "$CHANGED_FILES" | head -1 | cut -d'/' -f1)
if [ "$PRIMARY_DIR" != "" ] && [ "$PRIMARY_DIR" != "." ]; then
  # Extract meaningful scope (e.g., "auth" from "src/auth/login.py")
  SCOPE_CANDIDATE=$(echo "$CHANGED_FILES" | head -1 | cut -d'/' -f2)
  if [ "$SCOPE_CANDIDATE" != "" ] && [ ${#SCOPE_CANDIDATE} -lt 15 ]; then
    SCOPE="($SCOPE_CANDIDATE)"
  fi
fi

# Extract issue number from branch name
BRANCH_NAME=$(git branch --show-current)
ISSUE_REF=""
if [[ "$BRANCH_NAME" =~ \#([0-9]+) ]] || [[ "$BRANCH_NAME" =~ issue[-_]([0-9]+) ]]; then
  ISSUE_NUM="${BASH_REMATCH[1]}"
  ISSUE_REF="Closes #$ISSUE_NUM"
elif [[ "$BRANCH_NAME" =~ story/([0-9]+\.[0-9]+) ]]; then
  STORY_NUM="${BASH_REMATCH[1]}"
  ISSUE_REF="Story $STORY_NUM"
fi

# Generate meaningful subject from code analysis
# Use git diff to find key changes (function names, class names, imports)
KEY_CHANGES=$(git diff --cached | grep -E "^\+.*def |^\+.*class |^\+.*import " | head -3 | sed 's/^+//' | sed 's/def //' | sed 's/class //' | sed 's/import //' | tr '\n' ', ' | sed 's/,$//')

# Create descriptive subject (fallback to file-based if no key changes)
if [ -n "$KEY_CHANGES" ] && [ ${#KEY_CHANGES} -lt 40 ]; then
  SUBJECT="implement ${KEY_CHANGES}"
else
  PRIMARY_FILE=$(echo "$CHANGED_FILES" | head -1 | xargs basename)
  MODULE_NAME=$(echo "$PRIMARY_FILE" | sed 's/\.py$//' | sed 's/_/ /g')
  SUBJECT="update ${MODULE_NAME} module"
fi

# Enforce 50-char limit on subject
FULL_SUBJECT="${TYPE}${SCOPE}: ${SUBJECT}"
if [ ${#FULL_SUBJECT} -gt 50 ]; then
  # Truncate subject intelligently
  MAX_DESC_LEN=$((50 - ${#TYPE} - ${#SCOPE} - 2))
  SUBJECT=$(echo "$SUBJECT" | cut -c1-$MAX_DESC_LEN)
  FULL_SUBJECT="${TYPE}${SCOPE}: ${SUBJECT}"
fi

# Generate commit body (WHY, not HOW)
COMMIT_BODY="Improves code quality and maintainability by addressing:"
if echo "$CHANGED_FILES" | grep -qE "test"; then
  COMMIT_BODY="${COMMIT_BODY}\n- Test coverage and reliability"
fi
if git diff --cached | grep -qE "type:|->"; then
  COMMIT_BODY="${COMMIT_BODY}\n- Type safety and error handling"
fi
if git diff --cached | grep -qE "import"; then
  COMMIT_BODY="${COMMIT_BODY}\n- Module organization and dependencies"
fi

# Construct full commit message
COMMIT_MSG="${FULL_SUBJECT}\n\n${COMMIT_BODY}"
if [ -n "$ISSUE_REF" ]; then
  COMMIT_MSG="${COMMIT_MSG}\n\n${ISSUE_REF}"
fi

# Validate message quality
if echo "$FULL_SUBJECT" | grep -qiE "stuff|things|update code|fix bug|changes"; then
  echo "⚠️  WARNING: Generated commit message may be too vague"
  echo "Consider providing specific message via: /commit_orchestrate 'type(scope): specific description'"
fi

echo "📝 Generated commit message:"
echo "$COMMIT_MSG"
```
