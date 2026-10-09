# Troubleshooting & Error Handling

## Contents
- [Type-Level Mode (--fix-single-type)](#type-level-mode---fix-single-type)
- [Depth Protection](#depth-protection-prevent-infinite-loops)
- [Intelligent Chain Invocation (STEP 10)](#intelligent-chain-invocation-step-10)

## Ralph Loop Mode (retired)

`--loop` was retired with its runner script. Run the command without it; for unattended runs use `/loop` or `/epic-dev --auto`.

## Type-Level Mode (--fix-single-type)

**If `--fix-single-type` is present, execute only ONE type of test failures per iteration.**

This provides phase-level granularity for Ralph loops, resulting in:
- 25-40K tokens per iteration (vs 80-120K for all types)
- Fresh context per test type (prevents tunnel vision)
- Better recovery from failures

```
IF "--fix-single-type" in "$ARGUMENTS":
  # TYPE-LEVEL MODE: Execute ONLY the next type of failures

  Output: "Type-level mode active - fixing ONE test type..."

  # DEFENSIVE: Initialize state file for recovery
  STATE_FILE=".claude/state/test-orchestration.json"
  mkdir -p .claude/state

  IF NOT exists "{STATE_FILE}":
    Write state file:
    {
      "schema_version": "1.0",
      "current_type": null,
      "types_completed": [],
      "types_remaining": ["import", "type", "unit", "api", "database", "e2e"],
      "iteration": 0,
      "last_updated": "{timestamp}"
    }

  # Load existing state
  STATE = Read("{STATE_FILE}")

  # Run tests and analyze failures by type
  ```bash
  cd apps/api && TEST_OUTPUT=$(uv run pytest -v --tb=short 2>&1 || true)
  cd ../..

  # Detect failure types (in priority order)
  HAS_UNIT=$(echo "$TEST_OUTPUT" | grep -cE "FAILED.*test_unit|tests/unit.*FAILED" || echo "0")
  HAS_API=$(echo "$TEST_OUTPUT" | grep -cE "FAILED.*test_api|test_endpoint|tests/integration/api" || echo "0")
  HAS_DB=$(echo "$TEST_OUTPUT" | grep -cE "FAILED.*test_db|database|fixture.*db" || echo "0")
  HAS_E2E=$(echo "$TEST_OUTPUT" | grep -cE "FAILED.*test_e2e|playwright|cypress" || echo "0")
  HAS_IMPORT=$(echo "$TEST_OUTPUT" | grep -cE "ImportError|ModuleNotFoundError" || echo "0")
  HAS_TYPE=$(echo "$TEST_OUTPUT" | grep -cE "TypeError|mypy.*error" || echo "0")
  ```

  # Execute ONLY the first detected type (priority order: foundational -> complex)
  # Priority order: import -> type -> unit -> api -> database -> e2e

  For each type detected (in priority order), dispatch the appropriate agent:

  | Type | Agent | Model | Grep Pattern |
  |------|-------|-------|-------------|
  | import | import-error-fixer | haiku | `ImportError\|ModuleNotFoundError` |
  | type | type-error-fixer | sonnet | `TypeError\|mypy.*error` |
  | unit | unit-test-fixer | sonnet | `FAILED.*test_unit\|tests/unit.*FAILED` |
  | api | api-test-fixer | sonnet | `FAILED.*test_api\|test_endpoint` |
  | database | database-test-fixer | sonnet | `FAILED.*test_db\|database\|fixture.*db` |
  | e2e | e2e-test-fixer | sonnet | `FAILED.*test_e2e\|playwright\|cypress` |

  For each agent dispatch:
  1. **BEFORE** Task call: Update state file with current_type and iteration
  2. Dispatch Task with appropriate agent, grep-filtered test output (head -30/50)
  3. **AFTER** completion: Update state with types_completed, remove from types_remaining
  4. Output: "TYPE_COMPLETE: {TYPE}" and exit

  If NO failures detected:
  - Output: "ALL TESTS PASSING"
  - Exit 0

  ALL agent prompts SHOULD end with the canonical `findings-contract/v1` shape
  (`~/.claude/references/shared/findings-contract.md`):
  ```
  MANDATORY OUTPUT FORMAT - Return ONLY JSON:
  {
    'schema': 'findings-contract/v1',
    'source': '<agent-name>',
    'decision': 'PASS|CONCERNS|FAIL',
    'post_fix_verified': true|false,
    'findings': [ { 'id': 'r1', 'severity': 'error|warning|info', 'action': 'no-op|auto-fix|ask-user', 'status': 'open|fixed|escalated' } ],
    'summary': 'Brief description'
  }
  ```
  The legacy flat shape (`'status': 'fixed|partial|failed'`) is still accepted via the
  contract's legacy adapter but is no longer the recommended template.

ELSE:
  # ALL-TYPES MODE: Original behavior (fix all types in parallel)
  Output: "All-types mode active - fixing all test failures in parallel..."
  PROCEED TO STEP 1
END IF
```

## Depth Protection (prevent infinite loops)

```bash
echo "SLASH_DEPTH=${SLASH_DEPTH:-0}"
```

If SLASH_DEPTH >= 3:
- Report: "Maximum orchestration depth (3) reached. Exiting to prevent loop."
- EXIT immediately

Otherwise, set for any chained commands:
```bash
export SLASH_DEPTH=$((${SLASH_DEPTH:-0} + 1))
```

## Intelligent Chain Invocation (STEP 10)

### 10a. Check Depth
If SLASH_DEPTH >= 3:
- Report: "Maximum depth reached, skipping chain invocation"
- Go to STEP 11

### 10b. Check --no-chain Flag
If --no-chain present:
- Report: "Chain invocation disabled by flag"
- Go to STEP 11

### 10c. Determine Chain Action

**If ALL tests passing AND changes were made:**
```
SlashCommand(skill="/commit_orchestrate",
             args="--message 'fix(tests): resolve test failures'")
```

**If ALL tests passing AND NO changes made:**
- Report: "All tests passing, no changes needed"
- Go to STEP 11

**If SOME tests still failing:**
- Report remaining failure count
- If TACTICAL mode: Suggest "Run with --strategic for root cause analysis"
- Go to STEP 11
