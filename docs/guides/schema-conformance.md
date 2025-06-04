# Adding Schema Conformance Validation to Existing Tests

Use Schemathesis to validate API responses in your existing test suite without changing your current data generation or test structure.

### `validate_response()` - Raises on Validation Errors

```python
def test_get_user():
    response = requests.get("http://api.example.com/users/123")

    # Raises detailed validation errors if response doesn't match schema
    schema["/users/{id}"]["GET"].validate_response(response)
```

### `is_response_valid()` - Returns Boolean

```python
def test_with_conditional_logic():
    response = requests.post(
        "http://api.example.com/users", json={"name": "Alice"}
    )

    assert schema["/users"]["POST"].is_response_valid(response):
```

## Integration Example

```python
import pytest
import schemathesis

@pytest.fixture(scope="session")
def api_schema():
    return schemathesis.openapi.from_url("http://api.example.com/openapi.json")

def test_user_workflow(api_schema):
    # Your existing test logic
    create_response = requests.post(
        "http://api.example.com/users", json={"name": "Test"}
    )
    user_id = create_response.json()["id"]

    # Add schema validation 
    api_schema["/users"]["POST"].validate_response(create_response)
    
    get_response = requests.get(f"http://api.example.com/users/{user_id}")
    api_schema["/users/{id}"]["GET"].validate_response(get_response)
```

This approach adds schema validation to any HTTP client code while preserving your existing test patterns.
