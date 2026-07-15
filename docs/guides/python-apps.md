# Testing Python Apps

This guide shows how to test Python web applications (FastAPI, Flask, etc.) directly with Schemathesis instead of making network requests. You'll learn basic setup patterns and advanced integration techniques for existing test suites.

## Why Test Python Apps Directly?

- ⚡ **Performance**: Direct function calls eliminate HTTP overhead, TCP connections, and serialization, making tests run significantly faster.
- 🔧 **Existing Infrastructure**: Leverage your current test fixtures, database connections, and application configuration without additional network setup.
- 🎛️ **Control**: Full access to application state, middleware behavior, and internal dependencies during test execution.
- ✅ **Simplicity**: No server management, port conflicts, or network-related test flakiness.

## Basic Setup

### FastAPI (ASGI)

```python
from fastapi import FastAPI
import schemathesis

app = FastAPI()

@app.get("/users")
async def get_users():
    return [{"id": 1, "name": "Alice"}]

# Load schema directly from the app
schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

### Flask (WSGI)

```python
from flask import Flask, jsonify
import schemathesis

app = Flask(__name__)

@app.route("/users")
def get_users():
    return jsonify([{"id": 1, "name": "Alice"}])

@app.route("/openapi.json")
def openapi_spec():
    return {...}  # Your OpenAPI schema

schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema.parametrize()
def test_api(case):
    case.call_and_validate()
```

Both methods expect the schema endpoint path and your application instance.

## Reusing Values From Your Source

When you load an app with `from_asgi` or `from_wsgi`, Schemathesis reads the request handlers' source code and reuses the literal values it finds - status codes, identifiers, enum members, magic strings - as candidate test data. Free-form parameters that random generation rarely satisfies then occasionally receive a value the application recognizes, reaching code paths behind those checks.

This is on by default. Disable it in the config file:

```toml
[analysis.constants]
enabled = false
```

### Registering additional sources

App inspection reaches the request handlers' modules, plus - for Flask - the module the app is defined in. For literals defined elsewhere, or when the handler is a library view served over ASGI rather than your own code (for example GraphQL resolvers behind a Starlette/FastAPI mount), register the source explicitly:

```python
import schemathesis
import myapp.resolvers

@schemathesis.python.constants
def _constants():
    return myapp.resolvers
```

The decorated function returns what to inspect: a module, an application instance, a dotted module name, or an iterable of them. Registered sources are combined with the automatically inspected app; disabling `analysis.constants` turns off both.

## Custom Test Clients

Use custom test clients for shared configuration, authentication, or application lifecycle management:

```python
from fastapi import FastAPI
from starlette.testclient import TestClient
import schemathesis

app = FastAPI()

@app.get("/users")
async def get_users():
    return [{"id": 1, "name": "Alice"}]

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
def test_api_with_session(case):
    with TestClient(app) as client:
        # Client handles startup/shutdown events automatically
        case.call_and_validate(session=client)
```

This pattern enables:

- Persistent authentication across test cases
- Database transaction management
- Custom headers or middleware configuration

## Integration with pytest Fixtures

Combine direct app testing with existing pytest fixtures:

```python
import pytest
from fastapi import FastAPI
import schemathesis

@pytest.fixture
def configured_app(database_session):
    app = FastAPI()
    app.state.db = database_session

    @app.get("/users")
    async def get_users():
        return app.state.db.query_users()

    return app

@pytest.fixture
def api_schema(configured_app):
    return schemathesis.openapi.from_asgi("/openapi.json", configured_app)

schema = schemathesis.pytest.from_fixture("api_schema")

@schema.parametrize()
def test_operations(case):
    case.call_and_validate()
```

## Authentication Integration

For scenarios where you need to dynamically obtain authentication tokens (login flows, OAuth), integrate with your app's auth system:

```python
from starlette.testclient import TestClient
import schemathesis

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.auth()
class AppAuth:
    def get(self, case, context):
        # Login to get a fresh token
        client = TestClient(context.app)
        response = client.post("/auth/token", json={
            "username": "test_user", 
            "password": "test_password"
        })
        return response.json()["access_token"]

    def set(self, case, data, context):
        case.headers["Authorization"] = f"Bearer {data}"
```

!!! note ""
    This pattern is for dynamic authentication (login flows, token refresh). For static authentication (API keys, fixed tokens), simply add headers directly to your test client or case objects.
