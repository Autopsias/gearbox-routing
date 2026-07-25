# Failure Categorization & Prioritization

## Enhanced Failure Categorization (Regex-Based)

Use regex pattern matching for precise categorization:

### Unit Test Patterns -> unit-test-fixer
- `/AssertionError:.*expected.*got/` -> Assertion mismatch
- `/Mock.*call_count.*expected/` -> Mock verification failure
- `/fixture.*not found/` -> Fixture missing
- Business logic failures

### API Test Patterns -> api-test-fixer
- `/status.*(4\d\d|5\d\d)/` -> HTTP error response
- `/validation.*failed|ValidationError/` -> Schema validation
- `/timeout.*\d+\s*(s|ms)/` -> Request timeout
- FastAPI/Flask/Django endpoint failures

### Database Test Patterns -> database-test-fixer
- `/connection.*refused|ConnectionError/` -> Connection failure
- `/relation.*does not exist|table.*not found/` -> Schema mismatch
- `/deadlock.*detected/` -> Concurrency issue
- `/IntegrityError|UniqueViolation/` -> Constraint violation
- Fixture/mock database issues

### E2E Test Patterns -> e2e-test-fixer
- `/locator.*timeout|element.*not found/` -> Selector failure
- `/navigation.*failed|page.*crashed/` -> Page load issue
- `/screenshot.*captured/` -> Visual regression
- Playwright/Cypress failures

### Type Error Patterns -> type-error-fixer
- `/TypeError:.*expected.*got/` -> Type mismatch
- `/mypy.*error/` -> Static type check failure
- `/TypeScript.*error TS/` -> TS compilation error

### Import Error Patterns -> import-error-fixer
- `/ModuleNotFoundError|ImportError/` -> Missing module
- `/circular import/` -> Circular dependency
- `/cannot import name/` -> Named import failure

## Failure Prioritization

Assign priority based on test type:

| Priority | Criteria | Detection |
|----------|----------|-----------|
| P0 Critical | Security/auth tests | `test_auth_*`, `test_security_*`, `test_permission_*` |
| P1 High | Core business logic | `test_*_service`, `test_*_handler`, most unit tests |
| P2 Medium | Integration tests | `test_*_integration`, API tests |
| P3 Low | Edge cases, performance | `test_*_edge_*`, `test_*_perf_*`, `test_*_slow` |

Pass priority information to agents:
- "Priority: P0 - Fix these FIRST (security critical)"
- "Priority: P1 - High importance (core logic)"

## Analysis Phase

### Test Isolation Analysis

Check for potential isolation issues:

```bash
echo "=== Shared State Detection ===" && grep -rn "global\|class.*:$" tests/ 2>/dev/null | grep -v "conftest\|__pycache__" | head -10
```

```bash
echo "=== Fixture Scope Analysis ===" && grep -rn "@pytest.fixture.*scope=" tests/ 2>/dev/null | head -10
```

```bash
echo "=== Order Dependency Markers ===" && grep -rn "pytest.mark.order\|pytest.mark.serial" tests/ 2>/dev/null | head -5
```

If isolation issues detected:
- Add to agent context: "WARNING: Potential test isolation issues detected"
- List affected files

### Flakiness Detection

Check for flaky test indicators:

```bash
echo "=== Timing Dependencies ===" && grep -rn "sleep\|time.sleep\|setTimeout" tests/ 2>/dev/null | grep -v "__pycache__" | head -5
```

```bash
echo "=== Async Race Conditions ===" && grep -rn "asyncio.gather\|Promise.all" tests/ 2>/dev/null | head -5
```

If flakiness indicators found:
- Add to agent context: "Known flaky patterns detected"
- Recommend: pytest-rerunfailures or vitest retry

### Coverage Analysis (if --coverage)

```bash
test -f "test-results/pytest/coverage.xml" && grep -o 'line-rate="[0-9.]*"' test-results/pytest/coverage.xml | head -1
```

Coverage gates:
- < 60%: WARN "Critical: Coverage below 60%"
- 60-80%: INFO "Coverage could be improved"
- > 80%: OK
