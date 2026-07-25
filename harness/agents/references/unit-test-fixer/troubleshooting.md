# Common Test Fix Issues

## Error Handling

### If Tests Still Fail After Fixes:
1. Re-examine function implementation for recent changes
2. Check if mock data matches actual API responses
3. Verify test expectations match business requirements
4. Consider if function behavior actually changed correctly

### If Mock Configuration Breaks Other Tests:
1. Use more specific mock patches instead of global ones
2. Create separate fixtures for different test scenarios
3. Reset mock state between tests with proper cleanup

## Output Format

```markdown
## Unit Test Fix Report

### Test Logic Issues Fixed
- **test_calculate_total**
  - Issue: Expected int result, function returns float
  - Fix: Updated assertion to expect float type with isinstance check
  - File: tests/test_calculations.py:45

- **test_get_user_profile**
  - Issue: Mock database return value incomplete
  - Fix: Added complete user profile structure to mock data
  - File: tests/test_user_service.py:78

### Business Logic Corrections
- **calculate_percentage function**
  - Issue: Missing input validation for zero division
  - Fix: Added validation and proper error handling
  - File: src/utils/math_helpers.py:23

### Mock Configuration Updates
- **Database client mock**
  - Issue: Query method not properly mocked for all test cases
  - Fix: Added comprehensive mock configuration with realistic data
  - File: tests/conftest.py:34

### Test Results
- **Before**: 8 unit test assertion failures
- **After**: All unit tests passing
- **Coverage**: Maintained 80%+ function coverage

### Summary
Fixed 8 unit test failures by updating test assertions, correcting function bugs, and improving mock configurations. All functions now properly tested with realistic scenarios.
```

## MANDATORY JSON OUTPUT FORMAT

Return ONLY this JSON format at the end of your response:

```json
{
  "status": "fixed|partial|failed",
  "tests_fixed": 8,
  "files_modified": ["tests/test_calculations.py", "tests/conftest.py"],
  "remaining_failures": 0,
  "summary": "Fixed mock configuration and assertion order"
}
```

**DO NOT include:**
- Full file contents in response
- Verbose step-by-step execution logs
- Multiple paragraphs of explanation

This JSON format is required for orchestrator token efficiency.

## Performance & Best Practices

- **Test One Thing**: Each test should validate one specific behavior
- **Realistic Mocks**: Mock data should reflect actual production data patterns
- **Edge Case Coverage**: Test boundary conditions and error scenarios
- **Clear Assertions**: Use descriptive assertion messages for better debugging
- **Maintainable Tests**: Keep tests simple and easy to understand

Focus on ensuring tests accurately reflect the intended behavior while catching real bugs in business logic implementation for any Python project.

## Intelligent Chain Invocation

After fixing unit tests, validate coverage improvements:

```python
# After all unit test fixes are complete
if tests_fixed > 0 and all_tests_passing:
    print(f"Unit test fixes complete: {tests_fixed} tests fixed, all passing")

    # Check invocation depth to prevent loops
    invocation_depth = int(os.getenv('SLASH_DEPTH', 0))
    if invocation_depth < 3:
        os.environ['SLASH_DEPTH'] = str(invocation_depth + 1)

        # Check if coverage validation is appropriate
        if tests_fixed > 5 or coverage_impacted:
            print("Validating coverage after test fixes...")
            SlashCommand(command="/coverage validate")

        # If significant test improvements, commit them
        if tests_fixed > 10:
            print("Committing unit test improvements...")
            SlashCommand(command="/commit_orchestrate 'test: Fix unit test failures and improve test reliability'")
```
