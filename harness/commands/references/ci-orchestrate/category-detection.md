# CI Failure Category Detection & Single-Category Execution

## Contents
- [Category-Level Mode Detection (Phase Granularity)](#category-level-mode-detection-phase-granularity)

This file contains the detailed logic for detecting CI failure categories and executing single-category fixes (used with `--fix-single-category` flag).

> **Status field note:** the `'status': 'fixed|partial|failed'` field in the agent prompt
> templates below is a **deprecated legacy alias**. `ci-orchestrate.md` now emits the
> Uniform Findings Contract (`findings-contract/v1`); the legacy adapter maps
> `fixed -> auto-fix` and `partial|failed -> ask-user`+blocking. New agents SHOULD emit
> the canonical envelope directly — see `~/.claude/commands/references/shared/findings-contract.md`.

## Category-Level Mode Detection (Phase Granularity)

**If `--fix-single-category` is present, execute only ONE category of CI failures per iteration.**

This provides phase-level granularity for Ralph loops, resulting in:
- 30-50K tokens per iteration (vs 80-120K for all categories)
- Fresh context per category (prevents tunnel vision)
- Better recovery from failures

```
IF "--fix-single-category" in "$ARGUMENTS":
  # CATEGORY-LEVEL MODE: Execute ONLY the next category of failures

  Output: "Category-level mode active - fixing ONE failure category..."

  # DEFENSIVE: Initialize state file for recovery
  BRANCH=$(git branch --show-current)
  STATE_FILE=".claude/state/ci-orchestration-${BRANCH}.json"
  mkdir -p .claude/state

  IF NOT exists "{STATE_FILE}":
    Write state file:
    {
      "schema_version": "1.0",
      "branch": "{BRANCH}",
      "current_category": null,
      "categories_completed": [],
      "categories_remaining": ["linting", "types", "tests", "security", "import"],
      "mode": "tactical",
      "iteration": 0,
      "last_updated": "{timestamp}"
    }

  # Load existing state
  STATE = Read("{STATE_FILE}")

  # Analyze CI failures and group by category
  ```bash
  # Get CI failure output
  CI_LOG=$(gh run view --log 2>&1 || echo "")

  # Detect failure categories (in priority order)
  HAS_LINTING=$(echo "$CI_LOG" | grep -cE "(ruff|mypy|lint|format|E[0-9]+|F[0-9]+)" || echo "0")
  HAS_TYPES=$(echo "$CI_LOG" | grep -cE "(type.*error|mypy.*error|TypeScript.*error)" || echo "0")
  HAS_TESTS=$(echo "$CI_LOG" | grep -cE "(FAILED.*test_|pytest.*failed|jest.*failed|vitest.*failed)" || echo "0")
  HAS_SECURITY=$(echo "$CI_LOG" | grep -cE "(security|vulnerability|bandit|safety)" || echo "0")
  HAS_IMPORT=$(echo "$CI_LOG" | grep -cE "(ImportError|ModuleNotFoundError|cannot find module)" || echo "0")
  ```

  # Execute ONLY the first detected category (priority order)
  IF HAS_LINTING > 0:
    Output: "=== [CATEGORY: LINTING] Fixing linting/formatting failures ==="

    # DEFENSIVE: Update state BEFORE Task call (protects against hangs)
    Update STATE:
      - current_category: "linting"
      - iteration: {STATE.iteration + 1}
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Task(
      subagent_type="linting-fixer",
      model="haiku",
      description="Fix CI linting failures",
      prompt="Fix linting and formatting failures detected in CI.

CI log excerpt (linting errors):
{CI_LOG | grep -E '(ruff|mypy|lint|format|E[0-9]+|F[0-9]+)' | head -50}

Fix all linting issues. Run verification after.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'verification_passed': true|false,
  'summary': 'Brief description'
}"
    )

    # Update state after successful completion
    Update STATE:
      - categories_completed: [...STATE.categories_completed, "linting"]
      - categories_remaining: [filter "linting" from STATE.categories_remaining]
      - current_category: null
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Output: "CATEGORY_COMPLETE: LINTING"
    Output: "   Next category: types (if any)"
    Exit 0

  ELIF HAS_IMPORT > 0:
    Output: "=== [CATEGORY: IMPORTS] Fixing import/module failures ==="

    # DEFENSIVE: Update state BEFORE Task call
    Update STATE:
      - current_category: "import"
      - iteration: {STATE.iteration + 1}
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Task(
      subagent_type="import-error-fixer",
      model="haiku",
      description="Fix CI import failures",
      prompt="Fix import and module resolution failures detected in CI.

CI log excerpt (import errors):
{CI_LOG | grep -E '(ImportError|ModuleNotFoundError|cannot find module)' | head -50}

Fix all import issues. Run verification after.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'verification_passed': true|false,
  'summary': 'Brief description'
}"
    )

    # Update state after successful completion
    Update STATE:
      - categories_completed: [...STATE.categories_completed, "import"]
      - categories_remaining: [filter "import" from STATE.categories_remaining]
      - current_category: null
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Output: "CATEGORY_COMPLETE: IMPORTS"
    Output: "   Next category: types (if any)"
    Exit 0

  ELIF HAS_TYPES > 0:
    Output: "=== [CATEGORY: TYPES] Fixing type checking failures ==="

    # DEFENSIVE: Update state BEFORE Task call
    Update STATE:
      - current_category: "types"
      - iteration: {STATE.iteration + 1}
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Task(
      subagent_type="type-error-fixer",
      model="sonnet",
      description="Fix CI type failures",
      prompt="Fix type checking failures detected in CI.

CI log excerpt (type errors):
{CI_LOG | grep -E '(type.*error|mypy.*error|TypeScript.*error)' | head -50}

Fix all type errors. Run verification after.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'verification_passed': true|false,
  'summary': 'Brief description'
}"
    )

    # Update state after successful completion
    Update STATE:
      - categories_completed: [...STATE.categories_completed, "types"]
      - categories_remaining: [filter "types" from STATE.categories_remaining]
      - current_category: null
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Output: "CATEGORY_COMPLETE: TYPES"
    Output: "   Next category: tests (if any)"
    Exit 0

  ELIF HAS_TESTS > 0:
    Output: "=== [CATEGORY: TESTS] Fixing test failures ==="

    # Detect test type for appropriate fixer
    HAS_API_TESTS=$(echo "$CI_LOG" | grep -cE "(test_api|test_endpoint|FastAPI)" || echo "0")
    HAS_DB_TESTS=$(echo "$CI_LOG" | grep -cE "(test_db|database|fixture)" || echo "0")
    HAS_E2E_TESTS=$(echo "$CI_LOG" | grep -cE "(e2e|playwright|cypress)" || echo "0")

    IF HAS_API_TESTS > 0:
      test_fixer = "api-test-fixer"
    ELIF HAS_DB_TESTS > 0:
      test_fixer = "database-test-fixer"
    ELIF HAS_E2E_TESTS > 0:
      test_fixer = "e2e-test-fixer"
    ELSE:
      test_fixer = "unit-test-fixer"
    END IF

    # DEFENSIVE: Update state BEFORE Task call
    Update STATE:
      - current_category: "tests"
      - iteration: {STATE.iteration + 1}
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Task(
      subagent_type="{test_fixer}",
      model="sonnet",
      description="Fix CI test failures",
      prompt="Fix test failures detected in CI.

CI log excerpt (test failures):
{CI_LOG | grep -E '(FAILED.*test_|pytest.*failed|jest.*failed)' | head -50}

Fix all test failures. Run verification after.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'verification_passed': true|false,
  'summary': 'Brief description'
}"
    )

    # Update state after successful completion
    Update STATE:
      - categories_completed: [...STATE.categories_completed, "tests"]
      - categories_remaining: [filter "tests" from STATE.categories_remaining]
      - current_category: null
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Output: "CATEGORY_COMPLETE: TESTS"
    Output: "   Next category: security (if any)"
    Exit 0

  ELIF HAS_SECURITY > 0:
    Output: "=== [CATEGORY: SECURITY] Fixing security failures ==="

    # DEFENSIVE: Update state BEFORE Task call
    Update STATE:
      - current_category: "security"
      - iteration: {STATE.iteration + 1}
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Task(
      subagent_type="security-scanner",
      model="sonnet",
      description="Fix CI security failures",
      prompt="Fix security vulnerabilities detected in CI.

CI log excerpt (security issues):
{CI_LOG | grep -E '(security|vulnerability|bandit|safety)' | head -50}

Fix all security issues. Run verification after.

MANDATORY OUTPUT FORMAT - Return ONLY JSON:
{
  'status': 'fixed|partial|failed',
  'issues_fixed': N,
  'files_modified': ['...'],
  'verification_passed': true|false,
  'summary': 'Brief description'
}"
    )

    # Update state after successful completion
    Update STATE:
      - categories_completed: [...STATE.categories_completed, "security"]
      - categories_remaining: [filter "security" from STATE.categories_remaining]
      - current_category: null
      - last_updated: {timestamp}
    Write "{STATE_FILE}"

    Output: "CATEGORY_COMPLETE: SECURITY"
    Output: "   State file: {STATE_FILE}"
    Exit 0

  ELSE:
    # No failures detected or all categories fixed
    Output: "════════════════════════════════════════════════════════"
    Output: "ALL CI CHECKS PASSING"
    Output: "════════════════════════════════════════════════════════"
    Output: "   All failure categories have been addressed."
    Output: "   CI pipeline should be green."
    Exit 0
  END IF

ELSE:
  # ALL-CATEGORIES MODE: Original behavior (fix all categories in parallel)
  Output: "All-categories mode active - fixing all failures in parallel..."
  PROCEED TO "DELEGATE IMMEDIATELY" section
END IF
```
