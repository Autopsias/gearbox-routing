# Phased Execution Patterns

## PHASED EXECUTION (when conflicts detected)

When file conflicts are detected, use phased execution:

```
PHASE 1 (First): type-error-fixer, import-error-fixer
   - Foundational issues that affect other domains
   - Wait for completion before Phase 2

PHASE 2 (Parallel): unit-test-fixer, api-test-fixer, linting-fixer
   - Independent domains, safe to run together
   - Launch ALL in single message

PHASE 3 (Last): e2e-test-fixer
   - Integration tests depend on other fixes
   - Run only after Phases 1 & 2 complete

PHASE 4 (Validation): Run full validation suite
   - pytest, mypy, ruff
   - Confirm all fixes work together
```

## EXAMPLE PROMPT TEMPLATE FOR SPAWNED AGENTS

```markdown
You are a specialized {AGENT_TYPE} agent working as part of a parallel execution.

## YOUR SCOPE
- **ONLY modify these files:** {FILE_LIST}
- **DO NOT modify:** {FORBIDDEN_FILES}

## YOUR TASK
{SPECIFIC_TASK_DESCRIPTION}

## CONSTRAINTS
- Complete your work independently
- Do not modify files outside your scope
- Return results in JSON format

## MANDATORY OUTPUT FORMAT
Return ONLY this JSON structure:
{
  "status": "fixed|partial|failed",
  "files_modified": ["list"],
  "issues_fixed": N,
  "remaining_issues": N,
  "summary": "Brief description"
}
```

## COMMON PATTERNS

### Pattern: Fix All Test Errors

```
1. Run pytest to capture failures
2. Categorize by type:
   - Unit test failures -> unit-test-fixer
   - API test failures -> api-test-fixer
   - Database test failures -> database-test-fixer
3. Check for file overlaps
4. Spawn appropriate agents in parallel
5. Aggregate results and validate
```

### Pattern: Fix All CI Errors

```
1. Parse CI output
2. Categorize:
   - Linting errors -> linting-fixer
   - Type errors -> type-error-fixer
   - Import errors -> import-error-fixer
   - Test failures -> appropriate test fixer
3. Phase 1: type-error-fixer, import-error-fixer (foundational)
4. Phase 2: linting-fixer, test fixers (parallel)
5. Aggregate and validate
```

### Pattern: Refactor Multiple Files

```
1. Identify all files in scope
2. Partition into non-overlapping sets
3. Spawn general-purpose agents for each partition
4. Aggregate changes
5. Run validation
```

## RESULT AGGREGATION

After all agents complete, provide a summary:

```markdown
## Parallel Execution Results

### Agents Spawned: 3
| Agent | Status | Files Modified | Issues Fixed |
|-------|--------|----------------|--------------|
| linting-fixer | fixed | 5 | 12 |
| type-error-fixer | fixed | 3 | 8 |
| unit-test-fixer | partial | 2 | 4 (2 remaining) |

### Overall Status: PARTIAL
- Total issues fixed: 24
- Remaining issues: 2

### Validation Results
- pytest: PASS (45/45)
- mypy: PASS (0 errors)
- ruff: PASS (0 violations)

### Follow-up Required
- unit-test-fixer reported 2 remaining issues in tests/test_auth.py
```

**TaskList Summary (MANDATORY):**

Always include current task status in the result:

```markdown
### Task Progress (via TaskList)
| Task ID | Subject | Status |
|---------|---------|--------|
| 1 | Batch 1: Foundational | completed |
| 2 | Agent: import-error-fixer | completed |
| 3 | Agent: type-error-fixer | completed |
| 4 | Batch 2: Independent | in_progress |
| 5 | Agent: linting-fixer | completed |
| 6 | Agent: unit-test-fixer | completed (partial) |
| 7 | Batch 3: Integration | pending |

**Next:** Batch 3 pending. Invoke again to continue.
```
