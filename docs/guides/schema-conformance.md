# Adding Schema Conformance Validation to Existing Tests

This guide shows how to check requests and responses in your existing tests against your OpenAPI schema, without changing how those tests generate data or call the API.

## Prerequisites

- Schemathesis, pytest and `requests` installed
- A running API that serves its OpenAPI schema

The examples use an API at `http://127.0.0.1:8000` with `POST /users`, `GET /users` (with a `limit` query parameter) and `GET /users/{user_id}`.

## 1. Load the schema once

```python
# conftest.py
import pytest
import schemathesis


@pytest.fixture(scope="session")
def base_url():
    return "http://127.0.0.1:8000"


@pytest.fixture(scope="session")
def api_schema(base_url):
    return schemathesis.openapi.from_url(f"{base_url}/openapi.json")
```

## 2. Validate responses

`validate_response()` raises when the response body does not match the schema documented for its status code:

```python
# test_users.py
import requests


def test_get_user(api_schema, base_url):
    response = requests.get(f"{base_url}/users/1")
    api_schema["/users/{user_id}"]["GET"].validate_response(response)
```

`is_valid_response()` returns a boolean instead:

```python
def test_create_user(api_schema, base_url):
    response = requests.post(f"{base_url}/users", json={"name": "Alice"})
    assert api_schema["/users"]["POST"].is_valid_response(response)
```

A single violation raises a `Failure` subclass, which is an `AssertionError`, so pytest reports it as a normal test failure. Several violations in one response raise a `FailureGroup` that contains all of them. For a response whose `id` is a string instead of an integer:

```
E   schemathesis.openapi.checks.JsonSchemaError: Response violates schema
E
E   "999" is not of type "integer"
E
E   Validated against the response schema for status code 200.
```

These methods validate the response body against the schema for the returned status code. A status code the schema does not document is not an error here; use `case.validate_response()` in [Schemathesis-generated tests](../tutorials/pytest.md) for status code and header checks.

## 3. Validate request data

Catch malformed test data before it reaches the API. Every parameter of an operation - request body, query, path, header, cookie - carries `validate()` and `is_valid()`:

```python
def test_request_data(api_schema):
    operation = api_schema["/users"]["POST"]

    body = next(operation.get_bodies_for_media_type("application/json"))
    # Raises `jsonschema_rs.ValidationError`
    body.validate({"name": "Alice"})
    assert body.is_valid({"name": "Alice"})

    limit = api_schema["/users"]["GET"].get_parameter("limit", "query")
    limit.validate(50)
```

Pass values in their parsed form, not serialized. `get_parameter()` returns `None` when the operation declares no such parameter, and `get_bodies_for_media_type()` yields nothing when no body matches the media type.

## Complete example

```python
import requests


def test_user_workflow(api_schema, base_url):
    create_response = requests.post(f"{base_url}/users", json={"name": "Test"})
    api_schema["/users"]["POST"].validate_response(create_response)
    user_id = create_response.json()["id"]

    get_response = requests.get(f"{base_url}/users/{user_id}")
    api_schema["/users/{user_id}"]["GET"].validate_response(get_response)
```

Run `pytest`; tests pass when every response matches the schema:

```
test_users.py ....                                                       [100%]

============================== 4 passed in 0.09s ===============================
```

## Troubleshooting

**`OperationNotFound`.** The path in `api_schema[...]` must match the schema exactly, including path parameter names such as `{user_id}`, not the concrete URL.

**`AttributeError: 'NoneType' object has no attribute 'validate'`.** `get_parameter()` returned `None`: check the parameter name and location (`"query"`, `"path"`, `"header"`, `"cookie"`).

**`StopIteration` from `next(...)`.** The operation has no request body for that media type.
