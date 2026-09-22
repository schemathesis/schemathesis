# Adding Schema Conformance Validation to Existing Tests

Use Schemathesis to validate API requests and responses in your existing test suite without changing your current data generation or test structure.

## Responses

### `validate_response()` - Raises on Validation Errors

```python
def test_get_user():
    response = requests.get("http://api.example.com/users/123")

    # Raises validation errors if response doesn't match schema
    schema["/users/{id}"]["GET"].validate_response(response)
```

### `is_valid_response()` - Returns Boolean

```python
def test_with_conditional_logic():
    response = requests.post("http://api.example.com/users", json={"name": "Alice"})

    assert schema["/users"]["POST"].is_valid_response(response)
```

## Requests

Catch malformed test data before it reaches the API. Every parameter of an operation - request body, query, path, header, cookie - carries `validate()` and `is_valid()`:

```python
operation = schema["/users"]["POST"]

body = next(operation.get_bodies_for_media_type("application/json"))
body.validate({"name": "Alice"})  # raises `jsonschema_rs.ValidationError`
assert body.is_valid({"name": "Alice"})

limit = operation.get_parameter("limit", "query")
limit.validate(50)
```

Pass values in their parsed form, not serialized. `get_parameter()` returns `None` when the operation declares no such parameter, and `get_bodies_for_media_type()` yields nothing when no body matches the media type.

## Example

```python
import pytest
import schemathesis


@pytest.fixture(scope="session")
def api_schema():
    return schemathesis.openapi.from_url("http://api.example.com/openapi.json")


def test_user_workflow(api_schema):
    create_response = requests.post("http://api.example.com/users", json={"name": "Test"})
    user_id = create_response.json()["id"]

    api_schema["/users"]["POST"].validate_response(create_response)

    get_response = requests.get(f"http://api.example.com/users/{user_id}")
    api_schema["/users/{id}"]["GET"].validate_response(get_response)
```

This approach adds schema validation while preserving your existing code.
