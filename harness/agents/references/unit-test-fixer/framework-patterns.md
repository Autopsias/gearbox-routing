# pytest/unittest Specific Patterns

## Common Test Patterns

### Basic Function Testing
```python
import pytest
from pytest import approx
from unittest.mock import Mock, patch

# Basic calculation function test
@pytest.mark.unit
def test_calculate_total():
    """Test basic calculation function."""
    # Basic calculation
    assert calculate_total([10, 20, 30]) == 60

    # Edge cases
    assert calculate_total([]) == 0
    assert calculate_total([5]) == 5

    # Float precision
    assert calculate_total([10.5, 20.5]) == approx(31.0)

# Input validation test
@pytest.mark.unit
def test_calculate_total_validation():
    """Test input validation."""
    with pytest.raises(ValueError, match="Values must be numbers"):
        calculate_total(["not", "numbers"])

    with pytest.raises(TypeError, match="Input must be a list"):
        calculate_total("not a list")
```

### Parametrized Testing
```python
# Test multiple scenarios efficiently
@pytest.mark.unit
@pytest.mark.parametrize("input_value,expected_output", [
    (0, 0),
    (1, 1),
    (10, 100),
    (5, 25),
    (-3, 9),
])
def test_square_function(input_value, expected_output):
    """Test square function with multiple inputs."""
    result = square(input_value)
    assert result == expected_output

# Test validation scenarios
@pytest.mark.unit
@pytest.mark.parametrize("invalid_input,expected_error", [
    ("string", TypeError),
    (None, TypeError),
    ([], TypeError),
])
def test_square_function_validation(invalid_input, expected_error):
    """Test square function input validation."""
    with pytest.raises(expected_error):
        square(invalid_input)
```

### Error Handling Tests
```python
# Test exception handling
@pytest.mark.unit
def test_divide_by_zero_handling():
    """Test division function error handling."""
    # Normal operation
    assert divide(10, 2) == 5.0

    # Division by zero
    with pytest.raises(ZeroDivisionError, match="Cannot divide by zero"):
        divide(10, 0)

    # Type validation
    with pytest.raises(TypeError, match="Arguments must be numbers"):
        divide("10", 2)

# Test custom exceptions
@pytest.mark.unit
def test_custom_exception_handling():
    """Test custom business logic exceptions."""
    with pytest.raises(InvalidDataError, match="Data validation failed"):
        process_invalid_data({"invalid": "data"})
```

### Async Function Testing
```python
# Test async functions
@pytest.mark.asyncio
async def test_async_data_processing():
    """Test async data processing function."""
    with patch('services.async_client') as mock_client:
        mock_client.fetch_async.return_value = {"result": "success"}

        result = await process_data_async("input")
        assert result["result"] == "success"
        mock_client.fetch_async.assert_called_once_with("input")

# Test async generators
@pytest.mark.asyncio
async def test_async_data_stream():
    """Test async generator function."""
    async def mock_stream():
        yield {"item": 1}
        yield {"item": 2}

    with patch('services.data_stream', return_value=mock_stream()):
        results = []
        async for item in get_data_stream():
            results.append(item)

        assert len(results) == 2
        assert results[0]["item"] == 1
```

## MANDATORY SIMPLE TEST TEMPLATE - ENFORCE THIS PATTERN

ALL new/fixed tests MUST follow this exact pattern - no exceptions:

```python
class TestServiceName:
    """Test class following project patterns - no inheritance beyond this"""

    def setup_method(self):
        """Simple setup under 10 lines - use existing fixtures"""
        self.mock_db = Mock()  # Use Mock or AsyncMock as needed
        self.service = ServiceName(db_dependency=self.mock_db)
        # Maximum 3 more lines of setup

    def test_specific_behavior_success(self):
        """Test one specific behavior - descriptive name"""
        # Arrange (maximum 5 lines)
        test_data = {"id": 1, "value": 100}  # Use project's test data patterns
        self.mock_db.execute_query.return_value = [test_data]

        # Act (1-2 lines maximum)
        result = self.service.method_under_test(args)

        # Assert (1-3 lines maximum)
        assert result == expected_value
        self.mock_db.execute_query.assert_called_once_with(expected_query)

    def test_specific_behavior_edge_case(self):
        """Test edge cases separately - keep tests focused"""
        # Same pattern as above - simple and direct
```

**TEMPLATE ENFORCEMENT RULES:**
- Maximum 50 lines per test method (including setup)
- Maximum 5 imports at top of file
- Use existing project fixtures only (discover via pattern analysis)
- No abstract base classes or inheritance (except from pytest)
- Direct assertions only: `assert x == y`
- No custom test helpers or utilities

## MANDATORY POST-FIX VALIDATION WORKFLOW

After making any test changes, ALWAYS run this validation:

```bash
# Verify changes don't break existing tests
echo "Running post-fix validation..."
pytest tests/ -x -v

# If any failures detected
if [ $? -ne 0 ]; then
    echo "ROLLBACK: Changes broke existing tests"
    git checkout -- .  # Rollback changes
    echo "Fix conflicts before proceeding"
    exit 1
fi

echo "Integration validation passed"
```

## File Processing Strategy

### Single File Fixes (Use Edit)
- When fixing 1-2 test issues in a file
- For complex assertion logic requiring context

### Batch File Fixes (Use MultiEdit)
- When fixing 3+ similar test issues in same file
- For systematic mock configuration updates

### Cross-File Fixes (Use Glob + MultiEdit)
- For project-wide test patterns
- Fixture updates across multiple test files
