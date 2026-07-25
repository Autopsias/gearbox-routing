# Mock Configuration and Assertion Patterns

## ANTI-MOCKING-THEATER PRINCIPLES

### What NOT to Mock (Focus on Real Testing)
- **Business logic functions**: Calculations, data transformations, validators
- **Value objects**: Data classes, DTOs, configuration objects
- **Pure functions**: Functions without side effects or external dependencies
- **Internal services**: Application logic within the same bounded context
- **Simple utilities**: String formatters, math helpers, converters

### What TO Mock (System Boundaries Only)
- **Database connections**: Database clients, ORM queries
- **External APIs**: HTTP requests, third-party service calls
- **File system**: File I/O, path operations
- **Network operations**: Email sending, message queues
- **Time dependencies**: datetime.now(), sleep, timers

### Test Quality Validation
- **Mock setup ratio**: Should be < 50% of test code
- **Assertion focus**: Test actual outputs, not mock.assert_called_with()
- **Real functionality**: Each test must verify actual behavior/calculations
- **Integration preference**: Test multiple components together when reasonable
- **Meaningful data**: Use realistic test data, not trivial "test123" examples

### Quality Questions for Every Test
1. "If I change the implementation but keep the same behavior, does the test still pass?"
2. "Does this test verify what the user actually cares about?"
3. "Am I testing the mock setup more than the actual functionality?"
4. "Could this test catch a real bug in business logic?"

## Common Unit Test Failure Patterns

### 1. Assertion Failures - Expected vs Actual
```python
# FAILING TEST
def test_calculate_total():
    result = calculate_total([10, 20, 30], multiplier=2)
    assert result == 120  # FAILING: Getting 120.0

# ROOT CAUSE ANALYSIS
# - Function returns float, test expects int
# - Data type mismatch in assertion
```

**Fix Strategy**:
1. Examine function implementation to understand current behavior
2. Determine if test expectation or function logic is incorrect
3. Update test assertion to match correct behavior

### 2. Mock Configuration Issues
```python
# FAILING TEST
@patch('services.data_service.database_client')
def test_get_user_data(mock_db):
    mock_db.query.return_value = []
    result = get_user_data("user123")
    assert result is not None  # FAILING: Getting None

# ROOT CAUSE ANALYSIS
# - Mock return value doesn't match function expectations
# - Function changed to handle empty results differently
# - Mock not configured for all database calls
```

**Fix Strategy**:
1. Read function implementation to understand database usage
2. Update mock configuration to return appropriate test data
3. Verify all external dependencies are properly mocked

### 3. Test Data and Edge Cases
```python
# FAILING TEST
def test_process_empty_data():
    # Empty input
    result = process_data([])
    assert len(result) > 0  # FAILING: Getting empty list

# ROOT CAUSE ANALYSIS
# - Function doesn't handle empty input as expected
# - Test expecting fallback behavior that doesn't exist
# - Edge case not implemented in business logic
```

**Fix Strategy**:
1. Identify edge case handling in function implementation
2. Either fix function to handle edge case or update test expectation
3. Add appropriate fallback logic or error handling

## Mock Pattern Examples
```python
# Service dependency mocking
@pytest.fixture
def mock_database():
    with patch('services.database') as mock_db:
        # Configure common responses
        mock_db.query.return_value = [
            {"id": 1, "name": "Test Item", "value": 100}
        ]
        mock_db.save.return_value = True
        yield mock_db

@pytest.mark.unit
def test_data_service_get_items(mock_database):
    """Test data service with mocked database."""
    result = data_service.get_items("query")
    assert len(result) == 1
    assert result[0]["name"] == "Test Item"
    mock_database.query.assert_called_once_with("query")
```

## Advanced Mock Patterns

### Service Dependency Mocking
```python
# Mock external service dependencies
@patch('services.external_api.APIClient')
def test_get_remote_data(mock_api):
    """Test external API integration."""
    mock_api.return_value.get_data.return_value = {
        "status": "success",
        "data": [{"id": 1, "name": "Test"}]
    }

    result = get_remote_data("endpoint")
    assert result["status"] == "success"
    assert len(result["data"]) == 1
    mock_api.return_value.get_data.assert_called_once_with("endpoint")

# Mock database transactions
@pytest.fixture
def mock_database_transaction():
    with patch('database.transaction') as mock_transaction:
        mock_transaction.__enter__ = Mock(return_value=mock_transaction)
        mock_transaction.__exit__ = Mock(return_value=None)
        mock_transaction.commit = Mock()
        mock_transaction.rollback = Mock()
        yield mock_transaction
```

## Fix Implementation Strategies

### Strategy A: Update Test Assertions - USE EDIT TOOL
When function behavior changed but is correct:
```python
# EXAMPLE: Use Edit tool to fix test expectations
Edit("/path/to/tests/test_calculations.py",
     old_string="""def test_calculate_percentage():
    result = calculate_percentage(80, 100)
    assert result == 80  # Old expectation""",
     new_string="""def test_calculate_percentage():
    result = calculate_percentage(80, 100)
    assert result == 80.0  # Function returns float
    assert isinstance(result, float)  # Verify return type""")

# Then verify fix with Read and pytest
```

### Strategy B: Fix Mock Configuration - USE EDIT TOOL
When mocks don't reflect realistic behavior:
```python
# BAD: Mocking theater example
@patch('services.external_api')
def test_get_data(mock_api):
    mock_api.fetch.return_value = []
    result = get_data("query")
    assert len(result) == 0
    mock_api.fetch.assert_called_once_with("query")  # Testing mock, not functionality!

# GOOD: Test real behavior with minimal mocking
@patch('services.external_api')
def test_get_data(mock_api):
    mock_test_data = [
        {"id": 1, "name": "Product A", "category": "electronics", "quality_score": 8.5},
        {"id": 2, "name": "Product B", "category": "home", "quality_score": 9.2}
    ]
    mock_api.fetch.return_value = mock_test_data

    # Test the actual business logic, not the mock
    result = get_data("premium_products")
    assert len(result) == 2
    assert result[0]["name"] == "Product A"
    assert all(prod["quality_score"] > 8.0 for prod in result)  # Test business rule
    # NO assertion on mock.assert_called_with - focus on functionality!
```

### Strategy C: Fix Function Implementation
When unit tests reveal actual bugs:
```python
# Before: Function with bug
def calculate_average(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers)  # Division by zero bug

# After: Fixed calculation with validation
def calculate_average(numbers: list[float]) -> float:
    if not numbers:
        raise ValueError("Cannot calculate average of empty list")
    return sum(numbers) / len(numbers)
```
