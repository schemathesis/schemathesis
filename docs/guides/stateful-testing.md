# Customizing Stateful Testing

This guide shows how to customize Schemathesis's stateful testing to handle non-standard authentication scenarios, inject realistic test data, and adapt requests for your environment.

## Why Customize Stateful Testing?

- **Data initialization:** Start scenarios with realistic data instead of random generation
- **Authentication:** Add login flows and token management to test sequences
- **Request customization:** Inject environment-specific headers and parameters
- **Cleanup:** Prevent test pollution by properly resetting state between scenarios

## Components of Stateful Testing

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

APIWorkflow = schema.as_state_machine()
TestAPI = APIWorkflow.TestCase
```

Stateful testing consists of three components:

- **Schema** - Defines available operations and their links
- **State machine** (`APIWorkflow`) - Controls test scenario behavior and request customization  
- **Test class** (`TestAPI`) - Integrates with pytest/unittest for fixtures and test execution

The state machine automatically sequences API operations based on OpenAPI links. 

!!! info "How is it implemented?"
    Schemathesis implements stateful testing on top of Hypothesis's [rule-based state machines](https://hypothesis.readthedocs.io/en/latest/stateful.html)

## Basic Customization Pattern

Extend the state machine class to adjust its behavior:

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        """Run once at the start of each test scenario."""

    def teardown(self):
        """Run once at the end of each test scenario."""

    def before_call(self, case):
        """Modify every request in the sequence."""

    def after_call(self, response, case):
        """Process every response."""

TestAPI = APIWorkflow.TestCase
```

The state machine automatically handles operation sequencing based on OpenAPI links. You customize how requests are made and responses are processed.

!!! note "Reference Documentation"
    See the [APIStateMachine reference](../reference/python.md#stateful-testing) for all available customization methods and their parameters.

## Per-Run Setup with pytest Fixtures

For expensive setup that should happen once per test execution (database creation, external services), extend the test class:

```python
class TestAPI(APIWorkflow.TestCase):
    def setUp(self):
        """Create database, start services - runs once per test execution."""

    def tearDown(self):
        """Cleanup resources - runs once per test execution."""
```

Or use pytest fixtures:

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

!!! tip "Key difference"
    State machine methods (`setup`/`teardown`) run for each generated scenario. `TestCase` methods (`setUp`/`tearDown`) run once for the entire test, regardless of how many scenarios Hypothesis generates.

## Schema Loading with Fixtures

When your application requires fixtures to initialize (database connections, app configuration), load the schema inside a pytest fixture:

```python
import pytest
import schemathesis

@pytest.fixture
def api_schema(database, app_config):
    # Schema loading requires initialized app
    return schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

@pytest.fixture  
def state_machine(api_schema):
    return api_schema.as_state_machine()

def test_statefully(state_machine):
    state_machine.run()
```

You can also extend the state machine inside the fixture:

```python
@pytest.fixture
def state_machine(api_schema, auth_service):
    class APIWorkflow(api_schema.as_state_machine()):
        def setup(self):
            # Use fixture dependencies
            self.token = auth_service.get_test_token()

        def before_call(self, case):
            case.headers["Authorization"] = f"Bearer {self.token}"
 
    return APIWorkflow
```

## Hypothesis Configuration

Configure how many test scenarios run and how many steps each scenario contains:

```python
from hypothesis import settings

# Set on TestCase class
TestCase = schema.as_state_machine().TestCase
TestCase.settings = settings(max_examples=200, stateful_step_count=10)
```

For fixture-based schema loading, pass settings to the `run()` method:

```python
def test_statefully(state_machine):
    state_machine.run(
        settings=settings(
            max_examples=200,
            stateful_step_count=10,
        )
    )
```

- `max_examples=200` - Run 200 test scenarios (default: 100)
- `stateful_step_count=10` - Maximum 10 API calls per scenario (default: 6)

## Common Customization Examples

### Data Initialization

Create realistic test data at the start of each scenario:

```python
class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        # Create a test user for this scenario
        case = schema["/users"]["POST"].Case(body={
            "username": "test_user",
            "email": "test@example.com"
        })
        response = case.call()
        self.user_id = response.json()["id"]

    def before_call(self, case):
        # Use the created user in operations that need user_id
        if "user_id" in case.path_parameters:
            case.path_parameters["user_id"] = self.user_id
```

### Authentication Flow

Handle login and token management for protected endpoints:

```python
import requests

class APIWorkflow(schema.as_state_machine()):
    def setup(self):
        # Login and get auth token
        response = requests.post("http://localhost:8000/auth/login", json={
            "username": "test_user",
            "password": "test_password"
        })
        token = response.json()["access_token"]
        self.auth_headers = {"Authorization": f"Bearer {token}"}

    def before_call(self, case):
        # Add auth to every request
        case.headers = {**case.headers, **self.auth_headers}
```
