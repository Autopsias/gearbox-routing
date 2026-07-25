# Common Database Test Errors and Solutions

## ANTI-MOCKING-THEATER PRINCIPLES FOR DATABASE TESTING

### What NOT to Mock (Test Real Database Logic)
- SQL queries: Test actual query logic and data transformations
- Computed columns: Test actual calculation logic (totals, averages, derived values)
- Business rules: Domain-specific validations and constraints
- Data validations: Schema constraints, foreign keys, data integrity
- Database functions: Stored procedures, user-defined functions, triggers

### What TO Mock (External Dependencies Only)
- Database connections: Connection pools, network calls to database servers
- External services: Email notifications, file uploads, webhooks
- Third-party integrations: Payment processors, analytics services
- Time-dependent operations: Current timestamps, date calculations

### Database Test Quality Requirements
- **Use test databases**: In-memory databases or test database instances when possible
- **Test actual data transformations**: Verify computed columns work correctly
- **Validate business logic**: Test domain-specific calculations and rules
- **Test constraints**: Foreign keys, check constraints, unique constraints
- **Integration testing**: Test multiple tables working together

### Quality Indicators for Database Tests
- High Quality: Tests actual SQL, real data transformations, business rules
- Medium Quality: Mocks connections but tests data processing logic
- Low Quality: Mocks everything, no actual database logic tested

### Preferred Testing Patterns
- **Factory pattern**: Generate realistic test data matching actual schema
- **Transactional tests**: Use rollbacks to keep tests isolated
- **Fixture data**: Realistic data that matches your domain (users, products, orders, etc.)
- **Computed column validation**: Test that calculated fields work correctly (e.g., total = price * quantity)

## Output Format

```markdown
## Database Test Fix Report

### Mock Client Issues Fixed
- **MockDatabaseClient::rpc method**
  - Issue: Missing implementation for calculate_total function
  - Fix: Added proper RPC function mocking with correct calculation
  - File: tests/fixtures/database.py:45

- **Training Plan Factory**
  - Issue: Mock data structure outdated, missing required fields
  - Fix: Updated factory to match current database schema
  - File: tests/api/database/mock_factory.py:78

### Database Integration Tests Fixed
- **test_order_processing_workflow**
  - Issue: Async fixture configuration error
  - Fix: Updated conftest.py to properly handle async database client
  - File: tests/api/conftest.py:23

### Schema Validation
- Verified mock data matches automation/sql/01_create_tables.sql
- Updated computed column calculations in mock client
- Ensured RPC function signatures match PostgreSQL definitions

### Test Results
- **Before**: 4 database integration test failures
- **After**: All database tests passing
- **Performance**: Mock operations under 100ms

### Summary
Fixed 4 database integration test failures by updating mock client implementation, correcting data schemas, and fixing async fixture configuration. All database operations now properly mocked.
```

## Performance & Best Practices

- **Realistic Mock Data**: Generate mock data that closely matches production patterns
- **Async Compatibility**: Ensure all database operations properly handle async/await
- **Schema Consistency**: Keep mock data in sync with actual database schema
- **Transaction Testing**: Test both success and failure transaction scenarios
- **Performance Simulation**: Mock realistic database response times

Focus on creating robust, realistic database mocks that accurately simulate production database behavior while maintaining test isolation and performance.

## MANDATORY JSON OUTPUT FORMAT

Return ONLY this JSON format at the end of your response:

```json
{
  "status": "fixed|partial|failed",
  "tests_fixed": 4,
  "files_modified": ["tests/fixtures/database.py", "tests/api/conftest.py"],
  "remaining_failures": 0,
  "mock_updates": ["MockDatabaseClient.rpc", "create_mock_order"],
  "summary": "Fixed mock client configuration and schema alignment"
}
```

**DO NOT include:**
- Full file contents in response
- Verbose step-by-step execution logs
- Multiple paragraphs of explanation

This JSON format is required for orchestrator token efficiency.
