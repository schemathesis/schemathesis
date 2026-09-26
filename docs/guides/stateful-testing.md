# Customizing Stateful Testing

This guide shows how to customize Schemathesis's stateful tests in pytest: log in before each scenario, seed realistic data, adjust every request, and tune how many scenarios run.

For how Schemathesis chains operations and where links come from, see [Understanding Stateful Testing](../explanations/stateful.md).

## Prerequisites

- Python with `schemathesis` and `pytest` installed (`pip install schemathesis pytest`)
- A running API that serves its OpenAPI schema, for example at `http://localhost:8000/openapi.json`
- Operations that Schemathesis can connect, for example `POST /users` returning an `id` and `GET /users/{userId}` taking it. Schemathesis [discovers these connections](../explanations/stateful.md#connecting-operations) from the schema; if it finds none, [define OpenAPI links](#defining-openapi-links)

The examples below use an API with `POST /auth/token`, `POST /users`, `GET /users/{userId}`, and `DELETE /users/{userId}`, where user endpoints require a bearer token.

## Write a stateful test

Create `test_stateful.py`:

```python
import requests
from hypothesis import settings

import schemathesis

BASE_URL = "http://localhost:8000"

schema = schemathesis.openapi.from_url(f"{BASE_URL}/openapi.json")


class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        # Runs at the start of each scenario
        response = requests.post(f"{BASE_URL}/auth/token", json={"username": "demo", "password": "test"})
        response.raise_for_status()
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    def before_call(self, case):
        # Runs before every request in the scenario
        case.headers = {**(case.headers or {}), **self.headers}


TestAPI = APIWorkflow.TestCase
TestAPI.settings = settings(TestAPI.settings, max_examples=200, stateful_step_count=10)
```

`schema.as_state_machine()` returns a state machine class that sequences operations along the discovered links. Subclass it to hook into each scenario, and expose its `TestCase` so pytest collects it.

Run it:

```console
$ pytest test_stateful.py
```

You should see:

```
collected 1 item

test_stateful.py .                                                       [100%]

============================== 1 passed in 13.69s ==============================
```

When a check fails, pytest reports the failure with the sequence of calls that led to it and a `curl` command to reproduce the last one.

## Choose where to hook in

The state machine exposes four methods:

```python
class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        """Run once at the start of each test scenario."""

    def teardown(self):
        """Run once at the end of each test scenario."""

    def before_call(self, case):
        """Modify every request in the sequence."""

    def after_call(self, response, case):
        """Process every response."""
```

See the [APIStateMachine reference](../reference/python.md#stateful-testing) for all methods and their parameters.

## Seed data at the start of each scenario

Create a known user in `setup` and route every request that takes a `userId` to it:

```python
class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        response = requests.post(f"{BASE_URL}/auth/token", json={"username": "demo", "password": "test"})
        self.headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
        case = schema["/users"]["POST"].Case(
            body={"username": "test_user", "email": "test@example.com"},
            headers=self.headers,
        )
        self.user_id = case.call().json()["id"]

    def before_call(self, case):
        case.headers = {**(case.headers or {}), **self.headers}
        if "userId" in (case.path_parameters or {}):
            case.path_parameters["userId"] = self.user_id
```

## Run setup once per test run

State machine methods (`setup`/`teardown`) run for each generated scenario. For expensive setup that should happen once, such as creating a database, use `TestCase` methods or a pytest fixture:

```python
class TestAPI(APIWorkflow.TestCase):
    def setUp(self):
        """Runs once before all scenarios."""

    def tearDown(self):
        """Runs once after all scenarios."""
```

```python
import pytest


@pytest.fixture(scope="session")
def database():
    # create database
    yield
    # drop database


@pytest.mark.usefixtures("database")
class TestAPI(APIWorkflow.TestCase):
    pass
```

## Load the schema inside a fixture

When the schema is only available after fixtures run (for example, the app starts inside a fixture), build the state machine in a fixture and call `run()`:

```python
import pytest
import requests
from hypothesis import settings

import schemathesis

BASE_URL = "http://localhost:8000"


@pytest.fixture
def api_schema():
    return schemathesis.openapi.from_url(f"{BASE_URL}/openapi.json")


@pytest.fixture
def state_machine(api_schema):
    class APIWorkflow(api_schema.as_state_machine()):
        def setup(self):
            response = requests.post(f"{BASE_URL}/auth/token", json={"username": "demo", "password": "test"})
            self.token = response.json()["access_token"]

        def before_call(self, case):
            case.headers["Authorization"] = f"Bearer {self.token}"

    return APIWorkflow


def test_statefully(state_machine):
    state_machine.run(settings=settings(state_machine.TestCase.settings, max_examples=200, stateful_step_count=10))
```

## Tune the number of scenarios and steps

Derive new settings from `TestCase.settings` so you keep Schemathesis's defaults for stateful tests (no deadline, health checks suppressed) and change only what you need:

```python
from hypothesis import settings

TestAPI = APIWorkflow.TestCase
TestAPI.settings = settings(TestAPI.settings, max_examples=200)
```

With a fixture-built state machine, pass the same settings to `run()`:

```python
state_machine.run(settings=settings(state_machine.TestCase.settings, max_examples=200))
```

- `max_examples` - number of scenarios (default: 100)
- `stateful_step_count` - maximum API calls per scenario (default: 6)

## Defining OpenAPI links

When Schemathesis does not discover a connection you need, add a link to your schema. A link connects a **producer** operation (`POST /users`) with a **consumer** operation (`GET /users/{userId}`):

```yaml
paths:
  /users:
    post:
      operationId: createUser
      responses:
        '201':
          description: A new user was created
          content:
            application/json:
              schema:
                type: object
                properties:
                  id:
                    type: string
          links:
            GetUserById:
              operationId: getUser
              parameters:
                userId: '$response.body#/id'

  /users/{userId}:
    get:
      operationId: getUser
      parameters:
        - name: userId
          in: path
          required: true
          schema:
            type: string
```

Define the link under the status code your API actually returns. See the [OpenAPI Links specification](https://spec.openapis.org/oas/v3.1.0.html#link-object) for the full syntax.

To take part of a header value, such as the ID from a `Location: /orders/42` header, use Schemathesis's regex extension. See [Regex Extraction](../explanations/stateful.md#regex-extraction) for the matching rules:

```yaml
          links:
            GetOrder:
              operationId: getOrder
              parameters:
                orderId: '$response.header.Location#regex:/orders/(.+)'
```

## Run stateful tests from the CLI

The stateful phase runs by default after examples, coverage, and fuzzing:

```console
$ uvx schemathesis run http://localhost:8000/openapi.json
```

To run only the stateful phase, pass `--phases stateful`. The stateful phase block reports how many links were exercised:

```
     Scenarios:    28
     API Links:    0 covered / 4 selected / 4 total (4 inferred)
```

In the CLI, Schemathesis also learns links from `Location` headers it sees in earlier phases, so running only the stateful phase can find fewer links.

## Troubleshooting

**`Schema contains no link definitions required for stateful testing`** (pytest) or **`Missing Open API links`** (CLI): Schemathesis found no connections between operations. Add [OpenAPI links](#defining-openapi-links), or check that your response schemas describe the fields that consumer operations take.

**`All link definitions required for stateful testing are excluded by filters`**: Your `--include-*` / `--exclude-*` filters or config filters remove the producer or the consumer operation. Keep both in scope.

**`API Links: 0 covered`**: Schemathesis found links but no producer call succeeded, so there was nothing to pass along. In the output above, the API rejected every request without a token. Pass credentials with `--header` or a [config file](auth.md).

**Every call fails with 401 in pytest**: `setup` did not obtain a token, or `before_call` does not attach it. Call `response.raise_for_status()` after the login request so a failed login stops the scenario with a clear error.

**Links are defined but never followed**: Check that the link sits under the status code the producer actually returns, and that the referenced field or header is present in real responses.
