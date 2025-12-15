# Pytest Integration Tutorial

**Estimated time: 15-20 minutes**

This tutorial shows how to integrate Schemathesis into your `pytest` test suite using a Booking API.

!!! note "CLI vs Pytest Integration"
    The CLI offers more features (API probes, multiple phases, advanced reporting). Use pytest integration when you need direct integration with existing `pytest` test suites.

## Prerequisites

- **[Git](https://git-scm.com/downloads){target=_blank}** - to clone the example API repository
- **[Docker Compose](https://docs.docker.com/get-docker/){target=_blank}** - Install Docker Desktop which includes Docker Compose  
 - **[uv](https://docs.astral.sh/uv/getting-started/installation/){target=_blank}** - Python package manager to install pytest and Schemathesis
- **Python 3.10+** - this tutorial uses Python 3.13

**Install dependencies:**

```console
uv venv -p 3.13
source .venv/bin/activate.fish 
uv pip install pytest==8.3.5 schemathesis==4.0.0
```

!!! note "Shell differences"
    This tutorial uses the Fish shell. Depending on your setup, you will need to adjust the `source` command based on the output of `uv venv`

**Verify your setup:**

```console
git --version
docker compose version
python --version
pytest --version
schemathesis --version
```

## API under test

We'll test a booking API that handles hotel reservations - creating bookings and retrieving guest information.

The API lives in the [Schemathesis repository](https://github.com/schemathesis/schemathesis/tree/master/examples/booking):

```console
git clone https://github.com/schemathesis/schemathesis.git
cd schemathesis/examples/booking
docker compose up -d
```
!!! success "Verify the API is running"

    Open [http://localhost:8080/docs](http://localhost:8080/docs){target=_blank} - you should see the interactive API documentation.

!!! note "Authentication token"

    The API requires bearer token authentication. Use: `secret-token`

## First Test Run

Create your first Schemathesis pytest test:

**Create `test_api.py`:**

```python
import schemathesis

schema = schemathesis.openapi.from_url(
    "http://127.0.0.1:8080/openapi.json",
)
# To show the token in the cURL snippet
schema.config.output.sanitization.update(enabled=False)

@schema.parametrize()
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```


**Run the test:**

```bash
pytest test_api.py -v
```

**Output shows the same bug:**

```
test_api.py::test_api[POST /bookings] FAILED
test_api.py::test_api[GET /bookings/{booking_id}] PASSED
test_api.py::test_api[GET /health] PASSED

================================== FAILURES ===================================
__________________________ test_api[POST /bookings] ___________________________
+ Exception Group Traceback (most recent call last):
  |   File "/schemathesis-tutorial/test_api.py", line 10, in test_api
  |     case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
  |     ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  |   File "/../schemathesis/generation/case.py", line 185, in call_and_validate
  |     self.validate_response(
  |     ~~~~~~~~~~~~~~~~~~~~~~^
  |         response,
  |         ^^^^^^^^^
  |     ...<4 lines>...
  |         transport_kwargs=kwargs,
  |         ^^^^^^^^^^^^^^^^^^^^^^^^
  |     )
  |     ^
  |   File "/schemathesis/generation/case.py", line 171, in validate_response
  |     raise FailureGroup(_failures, message) from None
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

Empty `room_type` causes a 500 error because the pricing logic can't handle unexpected values.

Run the provided `curl` command to reproduce this failure.

## Fixing the bug

!!! bug "Root cause"
    The schema allows any string for `room_type`, but our pricing logic only handles specific values.

**Fix: Constrain room types in the schema**

=== "Before (broken)"
    ```python
    class BookingRequest(BaseModel):
        room_type: str  # Any string allowed!
    ```

=== "After (fixed)"
    ```python
    from enum import Enum

    class RoomType(str, Enum):
        standard = "standard"
        deluxe = "deluxe" 
        suite = "suite"

    class BookingRequest(BaseModel):
        room_type: RoomType  # Only valid values
    ```

**Don't forget to restart:**

```bash
docker compose restart
```

## Re-running the tests

Now let's verify our fix by re-running the tests. Focus on the operation you just fixed:

```python
import schemathesis

schema = ...  # snip

@schema.include(operation_id="create_booking_bookings_post").parametrize()
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```

!!! info "Operation ID explained"
    FastAPI generates operation IDs automatically. You can find them in the OpenAPI schema or API docs.

The `operation_id="create_booking_bookings_post"` option targets only the specific operation we fixed, making the test run faster during development. You can also filter by HTTP method (`method="POST"`) or path patterns (`path_regex="/bookings/.*"`).

## Generating more test cases

Let's be more thorough:

```python
import schemathesis
from hypothesis import settings

schema = ...  # snip

@schema.parametrize()
@settings(max_examples=500)
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```

**`max_examples=500`** generates more test cases per operation, increasing the chance of finding edge cases that smaller test runs might miss.

The trade-off is longer execution time, but you'll get more chances to find bugs.

!!! tip "Hypothesis configuration"
    See the whole list of available settings in the [Hypothesis documentation](https://hypothesis.readthedocs.io/en/latest/reference/api.html#settings).

## Configuration File

**Create `schemathesis.toml` in your project:**

```toml
# Core settings from our previous tests
headers = { Authorization = "Bearer ${API_TOKEN}" }

[output.sanitization]
enabled = false

[generation] 
max-examples = 500
```

!!! tip "Environment variables"
    ```bash
    export API_TOKEN=secret-token
    ```

**Now your tests look like this**:

```python
import schemathesis

schema = schemathesis.openapi.from_url(
    "http://127.0.0.1:8080/openapi.json",
)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

Schemathesis automatically loads `schemathesis.toml` from the current directory or project root. You can specify a custom configuration file:

```python
import schemathesis

config = schemathesis.Config.from_path(
    "path-to-my/config.toml"
)
schema = schemathesis.openapi.from_url(
    "http://127.0.0.1:8080/openapi.json",
    config=config
)
```

## What's next?

**Continue learning:**

- **[Python API](../reference/python.md)** - Complete Python API reference
- **[Configuration Reference](../reference/configuration.md)** - All configuration options
- **[How Schemathesis Integrates with Pytest
](../explanations/pytest.md)**
