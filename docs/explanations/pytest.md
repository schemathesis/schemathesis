# How Schemathesis Integrates with Pytest

This documents explains the mechanics of how Schemathesis works within pytest to automatically generate and run property-based API tests.

## Test Execution Flow

When you run a Schemathesis pytest test, here's what happens:

```python
import schemathesis

schema = schemathesis.openapi.from_url(
    "http://127.0.0.1:8080/openapi.json",
)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

**Execution sequence:**

1. **Schema Loading:** Schemathesis loads your API schema (happens at module import time)
2. **Test Collection:** pytest discovers your test and `@schema.parametrize()` creates one parametrized test per API operation found in the schema
3. **Test Execution:** For each operation (e.g., `POST /users`), pytest starts executing the parametrized test
4. **Property-Based Testing:** Hypothesis generates multiple examples (~100 by default) based on the schema constraints
5. **Test Function Calls:** Your test function runs once per Hypothesis example, receiving each `case` object

**What you see in pytest output:**
```bash
test.py::test_api[GET /users] PASSED    # One line per API operation
test.py::test_api[POST /users] FAILED   # Each runs many Hypothesis examples
test.py::test_api[DELETE /users/{id}] PASSED
```
!!! question "How many examples run?"
    Up to 100 per operation by default, but often fewer due to schema constraints. Simple schemas (e.g., `enum: ["A", "B"]`) only generate a few unique test cases, while complex schemas may hit the full limit.

## The Case Object

Each `case` represents **one Hypothesis-generated example** for an API operation:

```python
def test_api(case):
    # case.method = "POST" 
    # case.path = "/users"
    # case.body = {"name": "generated_string", "age": 42}
    # case.headers = {"Content-Type": "application/json"}
```

The `case` includes:

- **Operation metadata:** HTTP method, path, operation details
- **Generated data:** headers, query params, path params, request body
- **Methods:** `call()`, `call_and_validate()` for making requests

!!! note "Case Object Reference"
    See the [Case Object Reference](../reference/python.md#schemathesis.Case) for complete attributes and methods.

## Deferred Discovery with Fixtures

When you need pytest fixtures for schema setup, use `schemathesis.pytest.from_fixture`:

```python
@pytest.fixture
def api_schema(database):
    return schemathesis.openapi.from_asgi("/openapi.json", app)

schema = schemathesis.pytest.from_fixture("api_schema")

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

**Key differences:**

- **Schema loading:** Happens during test execution (not import time)
- **Test output:** Uses pytest-subtests instead of parametrization
- **Fixture support:** Schema can depend on other pytest fixtures

## Error Handling and Reporting

Failures from multiple Hypothesis examples are deduplicated, grouped by operation, and include reproduction steps for debugging:

```
test_api.py::test_api[POST /bookings] FAILED
test_api.py::test_api[GET /bookings/{booking_id}] PASSED
test_api.py::test_api[GET /health] PASSED

================================== FAILURES ===================================
__________________________ test_api[POST /bookings] ___________________________
+ Exception Group Traceback (most recent call last):
  | # snip
  | schemathesis.FailureGroup: Schemathesis found 2 distinct failures
  |
  | - Server error
  |
  | - Undocumented HTTP status code
  |
  |     Received: 500
  |     Documented: 200, 422
  |
  | [500] Internal Server Error:
  |
  |     `Internal Server Error`
  |
  | Reproduce with:
  |
  |     curl -X POST -H 'Authorization: Bearer secret-token' \
  |       -H 'Content-Type: application/json' \
  |       -d '{"guest_name": "00", "nights": 1, "room_type": ""}' \
  |       http://127.0.0.1:8080/bookings
  |
  |  (2 sub-exceptions)
  +-+---------------- 1 ----------------
```

## Async Support

Schemathesis supports asynchronous test functions with no additional configuration beyond installing `pytest-asyncio` or `pytest-trio`:

```python
import pytest
import schemathesis

schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json")

@pytest.mark.asyncio
@schema.parametrize()
async def test_api_async(case, client):
    response = await client.request(
        case.method, case.formatted_path, headers=case.headers
    )
    schema[case.path][case.method].validate_response(response)

# Or with trio
@pytest.mark.trio
@schema.parametrize()
async def test_api_trio(case, client):
    ...
```

!!! important "Async Network Calls"
    Schemathesis uses synchronous network calls, therefore you need to serialize the test case yourself if you'd like to use an async test client.
