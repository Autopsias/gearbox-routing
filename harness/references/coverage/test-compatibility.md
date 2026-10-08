## TEST COMPATIBILITY MATRIX - CRITICAL INTEGRATION REQUIREMENTS

MANDATORY COMPLIANCE: All generated tests MUST meet these compatibility requirements

### Project-Specific Requirements
- **Python Path**: `apps/api/src` must be in sys.path before imports
- **Environment Variables**: `TESTING=true` required for test mode
- **Required Imports**:
  ```python
  from apps.api.src.services.service_name import ServiceName
  from tests.fixtures.database import MockSupabaseClient, TestDataFactory
  from unittest.mock import AsyncMock, patch
  import pytest
  ```

### Fixture Compatibility Requirements
| Fixture Name | Usage Pattern | Import Path | Notes |
|--------------|---------------|-------------|-------|
| `MockSupabaseClient` | `self.mock_db = AsyncMock()` | `tests.fixtures.database` | Use AsyncMock, not direct MockSupabaseClient |
| `TestDataFactory` | `TestDataFactory.workout()` | `tests.fixtures.database` | Static methods only |
| `mock_supabase_client` | `def test_x(mock_supabase_client):` | pytest fixture | When function-scoped needed |
| `test_data_factory` | `def test_x(test_data_factory):` | pytest fixture | Access via fixture parameter |

### Mock Pattern Requirements
- **Database Mocking**: Always mock at service boundary (`db_service_override=self.mock_db`)
- **Patch Locations**:
  ```python
  @patch('apps.api.src.services.service_name.external_dependency')
  @patch('apps.api.src.database.client.db_service')  # Database patches
  ```
- **AsyncMock Usage**: Use `AsyncMock()` for all async database operations
- **Return Value Patterns**:
  ```python
  self.mock_db.execute_query.return_value = [test_data]  # List wrapper
  self.mock_db.rpc.return_value.execute.return_value.data = value  # RPC calls
  ```

### Test Structure Requirements
- **Class Naming**: `TestServiceNameBusinessLogic` or `TestServiceNameFunctionality`
- **Method Naming**: `test_method_name_condition` (e.g., `test_calculate_volume_success`)
- **Setup Pattern**: Always use `setup_method(self)` - never `setUp` or class-level setup
- **Import Organization**: Project imports first, then test imports, then mocks

### Integration Safety Requirements
- **Pre-test Validation**: Existing tests must pass before new test addition
- **Post-test Validation**: All tests must pass after new test addition
- **Fixture Conflicts**: No overlapping fixture names or mock patches
- **Environment Isolation**: Tests must not affect global state or other tests

### Anti-Over-Engineering Requirements
- **Maximum Complexity**: 50 lines per test method, 5 imports per file
- **No Abstractions**: No abstract base classes, builders, or managers
- **Direct Testing**: Test real business logic, not mock configurations
- **Simple Assertions**: Use `assert x == y`, not custom matchers
