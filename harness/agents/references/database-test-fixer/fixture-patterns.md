# Database Fixture and Mock Data Patterns

## Database Fixture Setup Problems
```python
# FAILING TEST
@pytest.fixture
async def database_client():
    return MockDatabaseClient()  # FAILING: Not async compatible

# ROOT CAUSE ANALYSIS
# - Async fixture not properly configured
# - Mock client missing required methods
# - Test environment variables not set correctly
```

**Fix Strategy**:
1. Check conftest.py fixture configuration
2. Ensure async compatibility for database operations
3. Verify all required mock methods are implemented

## Strategy B: Fix Mock Data Structure
When mock data doesn't match expected schema:
```python
# Before: Outdated mock data structure
def create_mock_order():
    return {"id": 1, "total": 100.0}

# After: Updated to match current schema
def create_mock_order():
    return {
        "id": 1,
        "total": 100.0,
        "customer_id": "test_customer",
        "items_count": 3,
        "created_at": "2024-01-01T00:00:00Z",
        "status": "pending"
    }
```

## Strategy F: Complete Table Schema Mock
Mock your application's database tables with proper structure:
```python
# Complete table schema mocks (adapt to your project's SQL files)
TABLE_SCHEMAS = {
    "orders": {
        "id": "uuid",
        "customer_id": "text",
        "product_name": "text",
        "price": "numeric",
        "quantity": "integer",
        "discount": "numeric",
        "order_number": "integer",
        "created_at": "timestamptz",
        # Computed columns (auto-calculated)
        "status": "integer",
        "tax_amount": "numeric",
        "total_price": "numeric",
        "discount_amount": "numeric",
        "final_price": "numeric"
    },
    "products": {
        "id": "uuid",
        "name": "text",
        "category": "text",
        "price": "numeric",
        "stock_quantity": "integer",
        "rating": "numeric",
        "reviews_count": "integer",
        "popularity_score": "numeric"  # Computed
    },
    "analytics": {
        "id": "uuid",
        "customer_id": "text",
        "period": "text",
        "total_orders": "integer",
        "total_spent": "numeric",
        "created_at": "timestamptz"
    },
    "customer_metrics": {
        "id": "uuid",
        "customer_id": "text",
        "satisfaction_score": "numeric",
        "engagement_level": "integer",
        "purchase_frequency": "numeric",
        "loyalty_points": "integer",
        "activity_level": "integer",
        "date": "date"
    },
    "sales_tracking": {
        "id": "uuid",
        "customer_id": "text",
        "product_category": "text",
        "weekly_sales": "numeric",
        "week": "integer",
        "target_achievement": "numeric"
    },
    "product_ratings": {
        "id": "uuid",
        "customer_id": "text",
        "product_name": "text",
        "quality": "integer",
        "value": "integer",
        "usability": "integer",
        "delivery": "integer",
        "date": "date"
    },
    "customer_profiles": {
        "id": "uuid",
        "customer_id": "text",
        "membership_tier": "text",
        "purchase_phase": "text",
        "preferences": "jsonb"
    },
    "pricing_tiers": {
        "id": "uuid",
        "product_category": "text",
        "customer_level": "text",
        "min_price": "numeric",  # Minimum Price Point
        "optimal_price": "numeric",  # Optimal Price Point
        "max_price": "numeric"   # Maximum Price Point
    }
}

def create_mock_factory(table: str, **overrides):
    """Create realistic mock data for any table"""
    base_data = {
        "orders": {
            "id": "test-order-id",
            "customer_id": "test-customer-123",
            "product_name": "laptop",
            "price": 999.99,
            "quantity": 1,
            "discount": 0.1,
            "order_number": 1,
            "created_at": "2024-01-01T10:00:00Z"
        },
        "products": {
            "id": "test-product-id",
            "name": "Gaming Laptop",
            "category": "electronics",
            "price": 999.99,
            "stock_quantity": 50,
            "rating": 4.5,
            "reviews_count": 128
        },
        # Add base data for all your tables...
    }

    data = base_data.get(table, {}).copy()
    data.update(overrides)
    return data
```

## Fix Workflow Process

### Phase 1: Database Test Analysis
1. **Read Test File**: Examine failing database test structure
2. **Check Mock Configuration**: Review fixture setup and mock client
3. **Validate Data Schema**: Compare mock data with actual database schema
4. **Check Environment**: Verify test environment configuration

### Phase 2: Mock Client Investigation

#### Factory Pattern Issues
```python
# Check mock factory implementation
Read("/tests/fixtures/database.py")
Read("/tests/api/database/mock_factory.py")

# Common issues:
# - Missing factory methods for new data types
# - Outdated mock data structures
# - Async method signatures not matching
```

#### Fixture Configuration
```python
# Check pytest fixture setup
Read("/tests/api/conftest.py")
Read("/tests/conftest.py")

# Verify:
# - Database client fixture properly configured
# - Mock overrides correctly applied
# - Test environment variables set
```
