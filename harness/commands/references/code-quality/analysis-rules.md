# Code Quality Analysis Rules

Thresholds, detection rules, violation categories, and quality reporting.

---

## Quality Check Scripts

Execute quality check scripts (portable centralized tools with backward compatibility):

```bash
# File size checker - try centralized first, then project-local
# NOTE: Use uv run for Python 3.11+ (tomllib support)
if [ -f ~/.claude/scripts/quality/check_file_sizes.py ]; then
    echo "Running file size check (centralized)..."
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" 2>&1 || true
    else
        python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" 2>&1 || true
    fi
elif [ -f scripts/check_file_sizes.py ]; then
    echo "⚠️  Using project-local scripts (consider migrating to ~/.claude/scripts/quality/)"
    python3 scripts/check_file_sizes.py 2>&1 || true
elif [ -f scripts/check-file-size.py ]; then
    echo "⚠️  Using project-local scripts (consider migrating to ~/.claude/scripts/quality/)"
    python3 scripts/check-file-size.py 2>&1 || true
else
    echo "✗ File size checker not available"
    echo "  Install: Copy quality tools to ~/.claude/scripts/quality/"
fi
```

```bash
# Function length checker - try centralized first, then project-local
if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
    echo "Running function length check (centralized)..."
    python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" 2>&1 || true
elif [ -f scripts/check_function_lengths.py ]; then
    echo "⚠️  Using project-local scripts (consider migrating to ~/.claude/scripts/quality/)"
    python3 scripts/check_function_lengths.py 2>&1 || true
elif [ -f scripts/check-function-length.py ]; then
    echo "⚠️  Using project-local scripts (consider migrating to ~/.claude/scripts/quality/)"
    python3 scripts/check-function-length.py 2>&1 || true
else
    echo "✗ Function length checker not available"
    echo "  Install: Copy quality tools to ~/.claude/scripts/quality/"
fi
```

```bash
# Complexity check — enforced via ruff C901 (there is NO separate checker script)
if command -v ruff &> /dev/null; then
    echo "Running complexity check (ruff C901)..."
    ruff check --select C901 --config 'lint.mccabe.max-complexity=12' "$PWD" 2>&1 || true
else
    echo "⚠️ ruff not found - skipping complexity check"
fi
```

```bash
# Slop scan (ADVISORY) — patterns typical of agent-authored code:
# unused imports/variables (F401/F841), bare/blind excepts (E722/BLE001),
# commented-out code (ERA001). Findings go to linting-fixer, never block.
if command -v ruff &> /dev/null; then
    echo "Running slop scan (advisory)..."
    ruff check --select F401,F841,E722,ERA001,BLE001 "$PWD" 2>&1 || true
fi
```

## Violation Categories

Capture violations into categories:
- **FILE_SIZE_VIOLATIONS**: Files >500 LOC (production, blocking) or >800 LOC (tests, blocking)
- **FUNCTION_LENGTH_VIOLATIONS**: Functions >100 lines (blocking)
- **COMPLEXITY_VIOLATIONS**: Functions with cyclomatic complexity >12 (ruff C901, blocking)
- **SLOP_VIOLATIONS**: unused imports/variables, bare/blind excepts, commented-out code (advisory — delegate to `linting-fixer`)

**Targets vs maxima.** The numbers above are the blocking maxima. The refactor agents
aim lower — file ≤300 LOC, function ≤50 lines, complexity ≤10 (their charter targets).
An agent reports `fixed` when the result is below the maximum; the target only shapes
where it aims. Do not report a below-maximum file as a violation.

**Split by responsibility, never by count.** Each extra file costs an agent one tool
call and tokens, so a cohesive file near the limit beats three fragments spread across
layers. When a refactor is needed, extract whole responsibilities (vertical slices)
with unique, grep-able names.

---

## Quality Report Format

Create structured report in this format:

```
## Code Quality Report

### File Size Violations (X files)
| File | LOC | Limit | Status |
|------|-----|-------|--------|
| path/to/file.py | 612 | 500 | BLOCKING |
...

### Function Length Violations (X functions)
| File:Line | Function | Lines | Status |
|-----------|----------|-------|--------|
| path/to/file.py:125 | _process_job() | 125 | BLOCKING |
...

### Test File Violations (X files)
| File | LOC | Limit | Status |
|------|-----|-------|--------|
| path/to/test.py | 850 | 800 | BLOCKING |
...

### Summary
- Total violations: X
- Critical (blocking): Y
- Warnings (non-blocking): Z
```

---

## Verify Results and Update Exceptions (after --fix)

After agents complete, re-run analysis to verify fixes AND update stale exceptions:

### Re-run Quality Checks

```bash
# Re-run file size check
if [ -f ~/.claude/scripts/quality/check_file_sizes.py ]; then
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD"
    else
        python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD"
    fi
elif [ -f scripts/check_file_sizes.py ]; then
    python3 scripts/check_file_sizes.py
elif [ -f scripts/check-file-size.py ]; then
    python3 scripts/check-file-size.py
fi
```

```bash
# Re-run function length check
if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
    python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD"
elif [ -f scripts/check_function_lengths.py ]; then
    python3 scripts/check_function_lengths.py
elif [ -f scripts/check-function-length.py ]; then
    python3 scripts/check-function-length.py
fi
```

### Refresh Stale Exceptions (MANDATORY after --fix)

**CRITICAL:** After successful refactoring, regenerate exception baselines to remove stale entries.
This prevents the exceptions file from becoming outdated with functions that have already been fixed.

```bash
echo "=== REFRESHING EXCEPTION BASELINES ==="

# Count exceptions BEFORE refresh
BEFORE_FILE=$(cat .file-size-exceptions 2>/dev/null | grep -c '"file":' || echo "0")
BEFORE_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

# Regenerate file size exceptions (if script supports it)
if [ -f ~/.claude/scripts/quality/check_file_sizes.py ]; then
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" --generate-baseline 2>/dev/null || true
    else
        python3 ~/.claude/scripts/quality/check_file_sizes.py --project "$PWD" --generate-baseline 2>/dev/null || true
    fi
fi

# Regenerate function length exceptions
if [ -f ~/.claude/scripts/quality/check_function_lengths.py ]; then
    # Use uv if available (for Python 3.10+ syntax), otherwise python3
    if command -v uv &> /dev/null; then
        uv run python ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    else
        python3 ~/.claude/scripts/quality/check_function_lengths.py --project "$PWD" --generate-baseline
    fi
fi

# Count exceptions AFTER refresh
AFTER_FILE=$(cat .file-size-exceptions 2>/dev/null | grep -c '"file":' || echo "0")
AFTER_FUNC=$(cat .function-length-exceptions 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('exceptions',{})))" 2>/dev/null || echo "0")

# Report changes
echo ""
echo "Exception file updates:"
echo "  File size:      $BEFORE_FILE -> $AFTER_FILE ($(($BEFORE_FILE - $AFTER_FILE)) stale entries removed)"
echo "  Function length: $BEFORE_FUNC -> $AFTER_FUNC ($(($BEFORE_FUNC - $AFTER_FUNC)) stale entries removed)"
```

**Why this is mandatory:**
- Exceptions files track "grandfathered" violations that existed before enforcement
- When functions are refactored below the threshold, their exceptions become stale
- Stale exceptions cause confusion: `/code_quality --fix` reports violations that don't exist
- Automatic refresh ensures the exceptions file always reflects current codebase state

---

## Conflict Detection Quick Reference

| Operation Type | Parallelizable? | Reason |
|----------------|-----------------|--------|
| Linting fixes | YES | Independent, no test runs |
| Type error fixes | YES | Independent, no test runs |
| Import fixes | PARTIAL | May conflict on same files |
| **File refactoring** | **CONDITIONAL** | Depends on shared tests |

**Safe to parallelize (different clusters, no shared tests)**
**Must serialize (same cluster, shared test files)**

---

## Parallel-Safe Operations (Linting, Type Errors)

These operations are ALWAYS safe to parallelize (no shared state):

**For linting issues -> delegate to existing `linting-fixer`:**
```
Task(
    subagent_type="linting-fixer",
    description="Fix linting errors",
    prompt="Fix all linting errors found by ruff check and eslint."
)
```

**For type errors -> delegate to existing `type-error-fixer`:**
```
Task(
    subagent_type="type-error-fixer",
    description="Fix type errors",
    prompt="Fix all type errors found by mypy and tsc."
)
```

These can run IN PARALLEL with each other and with safe-refactor agents (different file domains).
