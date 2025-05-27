# Extending Schemathesis

Customize how Schemathesis generates test data, validates responses, and handles requests through hooks, custom checks, and data generation strategies.

## When to extend Schemathesis

Extend Schemathesis when the default behavior doesn't match your API requirements:

- **Test with realistic data** - Use actual user IDs, valid timestamps, or existing database records
- **Validate business rules** - Check application-specific response patterns
- **Work with custom formats** - Generate valid credit cards, phone numbers, or other formats
- **Filter problematic test cases** - Skip data combinations that aren't relevant for your API

## Quick Start: Your First Hook

**Problem:** Your API requires existing user IDs, but Schemathesis generates random values that cause 404 errors.

Replace random generated data with realistic values that work with your test environment:

```python
# hooks.py
import schemathesis

@schemathesis.hook  
def map_query(context, query):
    """Replace random user_id with a known test user"""
    if query and "user_id" in query:
        query["user_id"] = "test-user-123"
    return query
```

```bash
# Run with hooks
export SCHEMATHESIS_HOOKS=hooks
schemathesis run http://localhost:8000/openapi.json
```

**Result:** Instead of generating random user IDs that might not exist in your test database, this hook ensures tests use a known test user.

## Hook Types and Naming

**Data generation hooks** use a naming pattern: `<operation>_<part>` where the operation determines what the hook does and the part determines which request data it affects.

**Operations:**

- **`filter_<part>`** - Skip test cases (return `True` to keep, `False` to skip)
- **`map_<part>`** - Modify existing data (return the modified data)
- **`flatmap_<part>`** - Generate new data with dependencies using Hypothesis strategies

**Request parts:**

- `query` - Query parameters
- `headers` - HTTP headers  
- `path_parameters` - URL path parameters
- `body` - Request body
- `case` - The entire test case

**Other hooks** like `before_call`, `after_call`, and `before_load_schema` have specific names based on when they execute in the testing process.

Data generation hooks apply in this sequence during test case generation:

```
filter_* → map_* → flatmap_* → Final test case
```

## Common Hook Patterns

### Filtering unwanted data

**Problem:** Skip test cases that cause known issues or aren't relevant for your API.

```python
@schemathesis.hook
def filter_query(context, query):
    """Skip tests with admin user to avoid permission issues"""
    return query and query.get("user_id") != "admin"
```

**Result:** Tests with `user_id=admin` are skipped, avoiding permission errors in your test environment.

### Using real database values

**Problem:** Your API validates IDs against a database, but Schemathesis generates random values that don't exist.

```python
@schemathesis.hook
def map_path_parameters(context, path_parameters):
    """Use real product IDs from the database"""
    if path_parameters and "product_id" in path_parameters:
        # In practice, query your test database
        path_parameters["product_id"] = "product_1"
    return path_parameters
```

**Result:** `GET /products/product_1` instead of `GET /products/random_abc123`, eliminating 404 errors.

### Generating dependent data

**Problem:** Create relationships between different parts of the request when fields must match.

```python
from hypothesis import strategies as st

@schemathesis.hook
def flatmap_body(context, body):
    """Ensure email domain matches organization"""
    if body and "email" in body and "organization" in body:
        org = body["organization"]
        domain = f"{org.lower()}.com"
        return st.just(body).map(lambda b: {**b, "email": f"user@{domain}"})
    return st.just(body)
```

**Result:** Generates `{"email": "user@acme.com", "organization": "acme"}` instead of mismatched combinations.

## Custom Validation Checks

Check business rules specific to your application beyond schema validation:

```python
@schemathesis.check
def check_user_permissions(ctx, response, case):
    """Verify user can only access their own data"""
    if case.path.startswith("/users/") and response.status_code == 200:
        user_id = case.path_parameters.get("user_id")
        response_data = response.json()
        
        if response_data.get("id") != user_id:
            actual = response_data.get("id")
            raise AssertionError(
                f"Accessed wrong data: expected {user_id}, got {actual}"
            )

@schemathesis.check
def check_audit_trail(ctx, response, case):
    """Ensure all data modifications are logged"""
    if case.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        if "X-Audit-ID" not in response.headers:
            raise AssertionError("Data modification missing audit trail")
```

## Custom Data Formats

Generate valid data for custom string formats in your OpenAPI schema:

```python
from hypothesis import strategies as st

# Generate valid phone numbers
phone_strategy = st.from_regex(r"\+1-\d{3}-\d{3}-\d{4}")
schemathesis.openapi.format("phone", phone_strategy)

# Generate valid credit card numbers (simplified)
card_strategy = st.from_regex(r"4\d{15}")  # Visa-like format
schemathesis.openapi.format("credit_card", card_strategy)
```

Now when your schema uses these formats, Schemathesis generates appropriate data:

```yaml
# In your OpenAPI schema
user_phone:
  type: string
  format: phone  # Uses your custom strategy
```

## Setting Up Extensions

### For CLI usage

Create a hooks file and reference it with an environment variable:

```python
# hooks.py
import schemathesis

@schemathesis.hook
def map_headers(context, headers):
    if headers is None:
        headers = {}
    headers["X-Test-Mode"] = "true"
    return headers
```

```bash
# Run with hooks
export SCHEMATHESIS_HOOKS=hooks
schemathesis run http://localhost:8000/openapi.json
```

!!! warning "Common issue"
    Use `SCHEMATHESIS_HOOKS=hooks` (not `hooks.py`). The file must be in your current directory or Python path.

### For pytest integration

Define hooks in your conftest.py to make them available to all tests:

```python
# conftest.py
import schemathesis

@schemathesis.hook
def map_headers(context, headers):
    if headers is None:
        headers = {}
    headers["X-Test-Mode"] = "true"
    return headers

# test_api.py
schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

## Targeting Specific Operations

Apply hooks only to certain API endpoints:

```python
# Only apply to user endpoints, skip POST requests
@schemathesis.hook.apply_to(path_regex=r"/users/").skip_for(method="POST")
def map_headers(context, headers):
    headers = headers or {}
    headers["X-User-Context"] = "test-user"
    return headers

# Only apply to a specific operation
@schemathesis.hook.apply_to(name="GET /orders/{order_id}")
def map_path_parameters(context, path_parameters):
    path_parameters["order_id"] = "order_12345"  # Known test order
    return path_parameters
```

## Advanced: Request Modification

For complex scenarios, modify the entire request:

```python
@schemathesis.hook
def before_call(context, case):
    """Modify the request just before it's sent"""
    # Add correlation ID for tracing
    if case.headers is None:
        case.headers = {}
    case.headers["X-Correlation-ID"] = f"test-{uuid.uuid4()}"
    
    # Ensure test environment
    if case.query is None:
        case.query = {}
    case.query["test_mode"] = "true"
```

## What's Next

- **[Authentication Guide](../guides/auth.md)** - Configure API authentication
- **[Hook Reference](../reference/hooks.md)** - Complete list of available hooks
- **[Checks Reference](../reference/checks.md)** - All built-in checks
