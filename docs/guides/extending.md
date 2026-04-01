# Extending Schemathesis

Customize how Schemathesis generates test data, validates responses, and handles requests through hooks, custom checks, and data generation strategies.

## When to extend Schemathesis

- **Test with realistic data** - Use actual user IDs, valid timestamps, or existing database records
- **Validate business rules** - Check application-specific response patterns
- **Work with custom formats** - Generate valid credit cards, phone numbers, or other formats
- **Filter problematic test cases** - Skip data combinations that aren't relevant for your API

## Quick Start: Your First Hook

Your API requires existing user IDs, but Schemathesis generates random values that cause 404 errors. Replace random generated data with realistic values that work with your test environment:

```python
# hooks.py
import schemathesis

@schemathesis.hook
def map_query(ctx, query):
    if query and "user_id" in query:
        query["user_id"] = "test-user-123"
    return query
```

```bash
export SCHEMATHESIS_HOOKS=hooks
schemathesis run http://localhost:8000/openapi.json
```

## Hook Types and Naming

Data generation hooks use a naming pattern: `<operation>_<part>` where the operation determines what the hook does and the part determines which request data it affects.

**Operations:**

- **`filter_<part>`** - Skip test cases (return `True` to keep, `False` to skip)
- **`map_<part>`** - Transform the drawn value; return the new value
- **`flatmap_<part>`** - Transform the drawn value using additional Hypothesis strategies; return a `SearchStrategy`

**Request parts:**

- `query` - Query parameters
- `headers` - HTTP headers
- `path_parameters` - URL path parameters
- `body` - Request body
- `case` - The entire test case

**Other hooks** like `before_call`, `after_call`, and `before_load_schema` have specific names based on when they execute in the testing process.

Data generation hooks apply in this sequence during the fuzzing phase:

```
filter_* → map_* → flatmap_* → Final test case
```

!!! note
    Component-level hooks have no effect in the coverage phase — cases are built directly and bypass the strategy pipeline where these hooks are applied. See [phase compatibility](../reference/hooks.md#phase-compatibility) for the full breakdown.

## Common Hook Patterns

### Filtering unwanted data

Skip test cases that cause known issues or aren't relevant for your API:

```python
@schemathesis.hook
def filter_query(ctx, query):
    if not query:
        return True
    return query.get("user_id") != "admin"
```

When multiple hooks of the same type are registered, they run in the order they were registered. For `filter_*` hooks, the case is discarded if any hook returns `False` - later hooks are not called. For `map_*` hooks, each receives the output of the previous.

### Using real database values

Your API validates IDs against a database, but Schemathesis generates random values that don't exist:

```python
@schemathesis.hook
def map_path_parameters(ctx, path_parameters):
    if path_parameters and "product_id" in path_parameters:
        path_parameters["product_id"] = "product_1"
    return path_parameters
```

### Generating dependent data with `flatmap`

Use `flatmap_<part>` when the strategy for one field depends on the value of another - the already-drawn value determines which strategy runs next.

`flatmap` receives the already-drawn value and must return a `SearchStrategy`. Hypothesis draws from that strategy to produce the final value.

```python
from hypothesis import strategies as st

SUBCATEGORIES = {
    "electronics": ["phones", "laptops", "tablets"],
    "clothing": ["shirts", "pants", "shoes"],
}

@st.composite
def with_subcategory(draw, body):
    category = (body or {}).get("category")
    options = SUBCATEGORIES.get(category, ["other"])
    subcategory = draw(st.sampled_from(options))
    return {**(body or {}), "subcategory": subcategory}

@schemathesis.hook
def flatmap_body(ctx, body):
    return with_subcategory(body)
```

The subcategory choices are determined by the generated `category` - that dependency is why `flatmap` is needed here. Use `map_<part>` instead when the transformation is deterministic.

## Custom Validation Checks

```python
@schemathesis.check
def check_user_permissions(ctx, response, case):
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
    if case.method in ("POST", "PUT", "DELETE") and response.status_code < 400:
        if "X-Audit-ID" not in response.headers:
            raise AssertionError("Data modification missing audit trail")
```

## Custom Data Formats

```python
from hypothesis import strategies as st

phone_strategy = st.from_regex(r"\+1-\d{3}-\d{3}-\d{4}")
schemathesis.openapi.format("phone", phone_strategy)

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

### Overriding built-in formats

The same API overrides standard formats like `date`, `date-time`, `uuid`, and others. By default Schemathesis generates values across the full valid range - including far-future dates and extreme integers - which is intentional: many server-side bugs only surface when the input isn't sanitised before hitting a database or arithmetic operation. If your application already handles those cases and the out-of-range values are producing noise, restrict the range:

```python
from datetime import date
from hypothesis import strategies as st
import schemathesis

today = date.today()
schemathesis.openapi.format("date", st.dates(max_value=today).map(str))
```

## Setting Up Extensions

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

!!! tip
    Two forms are accepted:

    - **Module name**: `SCHEMATHESIS_HOOKS=hooks` - imports `hooks.py` from the current directory or Python path
    - **File path**: `SCHEMATHESIS_HOOKS=hooks.py` or `SCHEMATHESIS_HOOKS=/path/to/hooks.py` - loads the file directly, useful in Docker or CI where the file isn't on the Python path

### For pytest integration

Put hooks in `conftest.py` to make them available to all tests:

```python
# test_api.py
schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

## Targeting Specific Operations

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
    path_parameters["order_id"] = "order_12345"
    return path_parameters
```

## Advanced: Request Modification

```python
@schemathesis.hook
def before_call(ctx, case, kwargs):
    case.headers["X-Correlation-ID"] = f"test-{uuid.uuid4()}"
    case.query["mode"] = "testing"
```

## Advanced: Schema Modification Patterns

Remove optional properties to focus tests on required fields only:

```python
@schemathesis.hook
def before_init_operation(ctx, operation):
    for parameter in operation.iter_parameters():
        schema = parameter.definition.get("schema", {})
        remove_optional_properties(schema)

    for alternative in operation.body:
        schema = alternative.definition.get("schema", {})
        remove_optional_properties(schema)

def remove_optional_properties(schema):
    if not isinstance(schema, dict):
        return
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for name in list(properties.keys()):
        if name not in required:
            del properties[name]
    for subschema in properties.values():
        remove_optional_properties(subschema)
```

## GraphQL Hooks

GraphQL hooks work with `graphql.DocumentNode` objects instead of JSON data. The `body` parameter contains the GraphQL query structure that you can modify.

### Modifying GraphQL queries

```python
@schemathesis.hook
def map_body(ctx, body):
    node = body.definitions[0].selection_set.selections[0]
    node.name.value = "addedViaHook"
    return body
```

### Adding query variables

```python
@schemathesis.hook
def map_query(ctx, query):
    return {"q": "42"}
```

Note that `query` is always `None` for GraphQL requests since Schemathesis doesn't generate query parameters for GraphQL.

### Filtering GraphQL queries

```python
@schemathesis.hook
def filter_body(ctx, body):
    node = body.definitions[0].selection_set.selections[0]
    return node.name.value != "excludeThisField"
```

### Generating dependent data

```python
from hypothesis import strategies as st

@schemathesis.hook
def flatmap_body(ctx, body):
    node = body.definitions[0].selection_set.selections[0]
    if node.name.value == "someField":
        return st.just(body).map(lambda b: modify_body(b, "someDependentField"))
    return st.just(body)

def modify_body(body, new_field_name):
    new_field = ...  # Create a new field node
    new_field.name.value = new_field_name
    body.definitions[0].selection_set.selections.append(new_field)
    return body
```

## What's Next

- [Authentication Guide](../guides/auth.md) - Configure API authentication
- [Hook Reference](../reference/hooks.md) - Complete list of available hooks
