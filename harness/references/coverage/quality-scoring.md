## TEST QUALITY SCORING ALGORITHM

Automatically score generated and existing tests to ensure quality and prevent mocking theater.

### Scoring Criteria (0-10 scale) - UPDATED WITH ANTI-OVER-ENGINEERING

#### Functionality Focus (30% weight)
- **10 points**: Tests actual business logic, calculations, transformations
- **7 points**: Tests API behavior with realistic data validation
- **4 points**: Tests with some mocking but meaningful assertions
- **1 point**: Primarily tests mock interactions, not functionality

#### Mock Usage Quality (25% weight)
- **10 points**: Mocks only external dependencies (DB, APIs, file system)
- **7 points**: Some internal mocking but tests core logic
- **4 points**: Over-mocks but still tests some real behavior
- **1 point**: Mocks everything including business logic

#### Simplicity & Anti-Over-Engineering (30% weight) - NEW
- **10 points**: Under 30 lines, direct assertions, no abstractions, uses existing fixtures
- **7 points**: Under 50 lines, simple structure, reuses patterns
- **4 points**: 50-75 lines, some complexity but focused
- **1 point**: Over 75 lines, abstract patterns, custom frameworks, unnecessary complexity

#### Pattern Integration (10% weight) - NEW
- **10 points**: Follows exact existing patterns, reuses fixtures, compatible imports
- **7 points**: Mostly follows patterns with minor deviations
- **4 points**: Some pattern compliance, creates minimal new infrastructure
- **1 point**: Ignores existing patterns, creates conflicting infrastructure

#### Data Realism (5% weight) - REDUCED
- **10 points**: Realistic data matching production patterns
- **7 points**: Good test data with proper structure
- **4 points**: Basic test data, somewhat realistic
- **1 point**: Trivial data like "test123", no business context

### Quality Categories
- **Excellent (8.5-10.0)**: Production-ready, maintainable tests
- **Good (7.0-8.4)**: Solid tests with minor improvements needed
- **Acceptable (5.5-6.9)**: Functional but needs refactoring
- **Poor (3.0-5.4)**: Major issues, likely mocking theater
- **Unacceptable (<3.0)**: Complete rewrite required

### Automated Quality Checks - ENHANCED WITH ANTI-OVER-ENGINEERING
- **Mock ratio analysis**: Count mock lines vs assertion lines
- **Business logic detection**: Identify tests of calculations/transformations
- **Integration span**: Measure how many real components are tested together
- **Data quality assessment**: Check for realistic vs trivial test data
- **Complexity metrics**: Lines of code, import count, nesting depth
- **Over-engineering detection**: Flag abstract base classes, custom frameworks, deep inheritance
- **Pattern compliance measurement**: Compare against learned project patterns
- **Fixture reuse analysis**: Measure usage of existing vs new fixtures
- **Simplicity scoring**: Penalize tests exceeding 50 lines or 5 imports
- **Mock chain depth**: Flag mock chains deeper than 2 levels

## ANTI-MOCKING-THEATER PRINCIPLES

CRITICAL: All test generation and improvement must follow anti-mocking-theater principles.

**Reference**: Read `~/.claude/knowledge/anti-mocking-theater.md` for complete guidelines.

**Quick Summary**:
- Mock only system boundaries (DB, APIs, file I/O, network, time)
- Never mock business logic, value objects, pure functions, or domain services
- Mock-to-assertion ratio must be < 50%
- At least 70% of assertions must test actual functionality

## CRITICAL: ANTI-OVER-ENGINEERING PRINCIPLES

YAGNI: Don't build elaborate test infrastructure for simple code.

**Reference**: Read `~/.claude/knowledge/test-simplicity.md` for complete guidelines.

**Quick Summary**:
- Maximum 50 lines per test, 5 imports per file, 3 patch decorators
- NO abstract base classes, factory factories, custom test frameworks
- Use existing fixtures (MockSupabaseClient, TestDataFactory) as-is
- Direct assertions only: `assert x == y`
