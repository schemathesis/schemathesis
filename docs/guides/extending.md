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
def map_query(ctx, query):
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
def filter_query(ctx, query):
    return query and query.get("user_id") != "admin"
```

### Using real database values

**Problem:** Your API validates IDs against a database, but Schemathesis generates random values that don't exist.

```python
@schemathesis.hook
def map_path_parameters(ctx, path_parameters):
    if path_parameters and "product_id" in path_parameters:
        path_parameters["product_id"] = "product_1"
    return path_parameters
```

### Generating dependent data

**Problem:** Create relationships between different parts of the request when fields must match.

```python
from hypothesis import strategies as st

@schemathesis.hook
def flatmap_body(ctx, body):
    if body and "email" in body and "organization" in body:
        org = body["organization"]
        domain = f"{org.lower()}.com"
        return st.just(body).map(lambda b: {**b, "email": f"user@{domain}"})
    return st.just(body)
```

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

Define your hooks:

```python
# hooks.py (for CLI) or conftest.py (for pytest)
import schemathesis

@schemathesis.hook
def map_headers(ctx, headers):
    if headers is None:
        headers = {}
    headers["X-Test-Mode"] = "true"
    return headers
```

### For CLI usage

```bash
export SCHEMATHESIS_HOOKS=hooks
schemathesis run http://localhost:8000/openapi.json
```

!!! warning "Common issue"
    Use `SCHEMATHESIS_HOOKS=hooks` (not `hooks.py`). The file must be in your current directory or Python path.

### For pytest integration

Put hooks in conftest.py to make them available to all tests:

```python
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
def map_headers(ctx, headers):
    headers = headers or {}
    headers["X-User-Context"] = "test-user"
    return headers

# Only apply to a specific operation
@schemathesis.hook.apply_to(name="GET /orders/{order_id}")
def map_path_parameters(ctx, path_parameters):
    path_parameters = path_parameters or {}
    path_parameters["order_id"] = "order_12345"  # Known test order
    return path_parameters
```

## Advanced: Request Modification

For complex scenarios, modify the entire request:

```python
@schemathesis.hook
def before_call(ctx, case, kwargs):
    """Modify the request just before it's sent"""
    # Add correlation ID for tracing
    case.headers["X-Correlation-ID"] = f"test-{uuid.uuid4()}"
    # Set `mode=testing` for every request
    case.query["mode"] = "testing"
```

## Advanced: Schema Modification Patterns

**Problem:** You want faster tests by only generating required fields, skipping optional parameters that don't affect core functionality.

```python
@schemathesis.hook
def before_init_operation(ctx, operation):
    """Remove optional properties to focus tests on required fields only"""
    for parameter in operation.iter_parameters():
        schema = parameter.definition.get("schema", {})
        remove_optional_properties(schema)

    for alternative in operation.body:
        schema = alternative.definition.get("schema", {})
        remove_optional_properties(schema)

def remove_optional_properties(schema):
    """Recursively remove non-required properties from schema"""
    if not isinstance(schema, dict):
        return

    required = schema.get("required", [])
    properties = schema.get("properties", {})

    # Remove optional properties
    for name in list(properties.keys()):
        if name not in required:
            del properties[name]

    # Recurse into remaining properties
    for subschema in properties.values():
        remove_optional_properties(subschema)
```

**When to use:** When you want to focus on core functionality testing and reduce test execution time.

**Trade-offs:** Faster tests but reduced coverage of optional parameter combinations.

## GraphQL Hooks

GraphQL hooks work with `graphql.DocumentNode` objects instead of JSON data. The `body` parameter contains the GraphQL query structure that you can modify.

### Modifying GraphQL queries

```python
@schemathesis.hook
def map_body(ctx, body):
    """Change field names in the GraphQL query"""
    node = body.definitions[0].selection_set.selections[0]
    
    # Change the field name
    node.name.value = "addedViaHook"
    
    return body
```

### Adding query variables

Use `map_query` to provide variables:

```python
@schemathesis.hook
def map_query(ctx, query):
    """Add query parameters to GraphQL requests"""
    return {"q": "42"}
```

Note that `query` is always `None` for GraphQL requests since Schemathesis doesn't generate query parameters for GraphQL.

### Filtering GraphQL queries

```python
@schemathesis.hook
def filter_body(ctx, body):
    """Skip queries with specific field names"""
    node = body.definitions[0].selection_set.selections[0]
    return node.name.value != "excludeThisField"
```

### Generating dependent data

```python
from hypothesis import strategies as st

@schemathesis.hook
def flatmap_body(ctx, body):
    """Generate dependent fields based on query content"""
    node = body.definitions[0].selection_set.selections[0]
    if node.name.value == "someField":
        return st.just(body).map(lambda b: modify_body(b, "someDependentField"))
    return st.just(body)

def modify_body(body, new_field_name):
    # Create and add a new field to the query
    new_field = ...  # Create a new field node
    new_field.name.value = new_field_name
    
    body.definitions[0].selection_set.selections.append(new_field)
    return body
```

## What's Next

- **[Authentication Guide](../guides/auth.md)** - Configure API authentication
- **[Hook Reference](../reference/hooks.md)** - Complete list of available hooks
