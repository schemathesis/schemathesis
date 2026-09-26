# Pytest Integration Tutorial

**Estimated time: 15 minutes**

In this tutorial you add Schemathesis to a `pytest` suite, let it find a server error in a small booking API, fix the bug, and confirm the fix. By the end you will have a test module that checks every operation in the API's schema and reads its settings from `schemathesis.toml`.

!!! note "CLI vs pytest"
    The CLI runs every test phase, including stateful testing, in one command. In `pytest`, `@schema.parametrize()` runs the examples, coverage, and fuzzing phases; stateful tests are a separate test class built with `schema.as_state_machine()`. Use `pytest` when you want Schemathesis inside an existing test suite, with its fixtures and plugins.

## Prerequisites

- **[Git](https://git-scm.com/downloads){target=_blank}** - to clone the example API
- **[Docker Compose](https://docs.docker.com/get-docker/){target=_blank}** - included in Docker Desktop
- **[uv](https://docs.astral.sh/uv/getting-started/installation/){target=_blank}** - to install `pytest` and Schemathesis
- **Python 3.10+**

--8<-- "docs/tutorials/_booking-api.md"

## Setting up the test project

Return to the directory you cloned into, create a directory for the tests next to the repository, and install the dependencies into a virtual environment:

```console
cd ../../..
mkdir booking-tests
cd booking-tests
uv venv
uv pip install pytest schemathesis
```

`uv run` runs commands inside that environment, whatever your shell. Verify the setup:

```console
uv run pytest --version
uv run schemathesis --version
```

If you prefer to activate the environment, run `source .venv/bin/activate` (bash, zsh) or `source .venv/bin/activate.fish` (fish), then drop the `uv run` prefix.

## First test run

Create `test_api.py`:

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json")
# Show the token in the curl reproduction command
schema.config.output.sanitization.update(enabled=False)


@schema.parametrize()
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```

`@schema.parametrize()` turns `test_api` into one test per API operation. For each operation, Schemathesis generates many requests, and `case.call_and_validate()` sends each one and runs the built-in checks on the response.

Run the tests:

```console
uv run pytest test_api.py -v
```

`POST /bookings` fails:

```
test_api.py::test_api[POST /bookings] FAILED                             [ 33%]
test_api.py::test_api[GET /bookings/{booking_id}] PASSED                 [ 66%]
test_api.py::test_api[GET /health] PASSED                                [100%]

=================================== FAILURES ===================================
___________________________ test_api[POST /bookings] ___________________________
+ Exception Group Traceback (most recent call last):
  ...
  | schemathesis.core.failures.FailureGroup: Schemathesis found 2 distinct failures
  |
  | - Server error
  |
  | - Undocumented HTTP status code
  |
  |     Received: 500
  |     Documented: 200, 400, 422
  |
  | [500] Internal Server Error:
  |
  |     `Internal Server Error`
  |
  | Reproduce with:
  |
  |     curl -X POST -H 'Authorization: Bearer secret-token' -H 'Content-Type: application/json' -d '{"guest_name": "00", "room_type": "", "nights": 1}' http://127.0.0.1:8080/bookings
  |
  |  (2 sub-exceptions)
...
=========================== short test summary info ============================
FAILED test_api.py::test_api[POST /bookings] - + Exception Group Traceback (m...
========================= 1 failed, 2 passed in 1.40s ==========================
```

The server answered 500, which is both a server error and a status code the schema does not document. Schemathesis has already reduced the input to the simplest case that still fails: an empty `room_type`.

Run the `curl` command to reproduce it. The response body is just `Internal Server Error`; the cause is in the server log. From `examples/booking`, run:

```console
docker compose logs booking-api
```

```
...
booking-api-1  |   File "/app/app.py", line 46, in create_booking
booking-api-1  |     price_per_night = room_prices[booking.room_type]
booking-api-1  |                       ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
booking-api-1  | KeyError: ''
```

--8<-- "docs/tutorials/_booking-fix.md"

## Re-running the tests

Back in `booking-tests`, focus on the operation you fixed. Change the decorator in `test_api.py`:

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json")
schema.config.output.sanitization.update(enabled=False)


@schema.include(operation_id="create_booking_bookings_post").parametrize()
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```

```console
uv run pytest test_api.py -v
```

```
test_api.py::test_api[POST /bookings] PASSED                             [100%]

============================== 1 passed in 0.72s ===============================
```

FastAPI generates operation IDs like `create_booking_bookings_post` automatically; find them in the schema at `/openapi.json`. You can also filter by HTTP method (`method="POST"`) or path (`path_regex="^/bookings"`).

## Generating more test cases

For a more thorough pass over the whole API, raise the number of generated cases with Hypothesis settings:

```python
import schemathesis
from hypothesis import settings

schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json")
schema.config.output.sanitization.update(enabled=False)


@schema.parametrize()
@settings(max_examples=500)
def test_api(case):
    case.call_and_validate(headers={"Authorization": "Bearer secret-token"})
```

`max_examples` caps the cases generated by the fuzzing phase per operation; the examples and coverage phases add their own cases. With the bug fixed, all three tests pass. A clean run is the result you want: up to 500 fuzzed requests per operation found no server errors or schema violations.

See the [Hypothesis documentation](https://hypothesis.readthedocs.io/en/latest/reference/api.html#settings){target=_blank} for other settings.

## Configuration file

Move the settings out of the test module. Create `schemathesis.toml` in `booking-tests`:

```toml
headers = { Authorization = "Bearer ${API_TOKEN}" }

[output.sanitization]
enabled = false

[generation]
max-examples = 500
```

`${API_TOKEN}` is read from the environment, which keeps the token out of the file. The test module shrinks to:

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json")


@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

```console
export API_TOKEN=secret-token
uv run pytest test_api.py -v
```

Schemathesis looks for `schemathesis.toml` in the current directory and its parents, up to the repository root. To load a file elsewhere:

```python
import schemathesis

config = schemathesis.Config.from_path("path/to/config.toml")
schema = schemathesis.openapi.from_url("http://127.0.0.1:8080/openapi.json", config=config)
```

## What's next?

You have a `pytest` module that tests every operation in the booking API, found and fixed a bug with it, and moved its settings into `schemathesis.toml`.

- **[Triaging Failures](../guides/triage.md)** - when your own API reports many failures at once
- **[CI/CD Integration](../guides/cicd.md)** - run Schemathesis on every pull request
- **[Testing multi-step workflows](../guides/stateful-testing.md)** - stateful tests with `schema.as_state_machine()`
- **[How Schemathesis integrates with pytest](../explanations/pytest.md)** - what `parametrize` does under the hood
- **[Python API](../reference/python.md)** - complete reference
