# Testing Python Apps

This guide shows how to test Python web applications (FastAPI, Flask, Django, etc.) directly with Schemathesis instead of making network requests. You'll learn basic setup patterns and advanced integration techniques for existing test suites.

## Why Test Python Apps Directly?

- ⚡ **Performance**: Direct function calls eliminate HTTP overhead, TCP connections, and serialization, making tests run significantly faster.
- 🔧 **Existing Infrastructure**: Leverage your current test fixtures, database connections, and application configuration without additional network setup.
- 🎛️ **Control**: Full access to application state, middleware behavior, and internal dependencies during test execution.
- ✅ **Simplicity**: No server management, port conflicts, or network-related test flakiness.

## Which Frameworks Work

Schemathesis has exactly two in-process entry points, and they key off the calling convention, not the framework:

- `schemathesis.openapi.from_asgi(path, app)` - anything that is an ASGI 3 callable
- `schemathesis.openapi.from_wsgi(path, app)` - anything that is a WSGI callable

`path` is where *your* application serves its schema, so it varies with the library that generates it.

| Framework | Loader | Schema path in a default setup |
|---|---|---|
| FastAPI | `from_asgi` | `/openapi.json` |
| Starlette | `from_asgi` | whatever route you register |
| Flask | `from_wsgi` | whatever route you register |
| APIFlask | `from_wsgi` | `/openapi.json` |
| Flask-RESTX | `from_wsgi` | `/swagger.json` |
| flask-smorest | `from_wsgi` | `OPENAPI_URL_PREFIX` + `/openapi.json` |
| Litestar | `from_asgi` | `/schema/openapi.json` |
| Quart | `from_asgi` | whatever route you register |
| Sanic | `from_asgi` | whatever route you register |
| Falcon | `from_wsgi` or `from_asgi` | whatever route you register |
| Connexion 3 | `from_asgi` | the spec file you pass to `add_api()` |
| Django + DRF | `from_wsgi` or `from_asgi` | wherever you route `SpectacularAPIView` |
| Django Ninja | `from_wsgi` | `/api/openapi.json` |

### Frameworks with no in-process path

**aiohttp** and **Tornado** expose neither a WSGI nor an ASGI callable, so there is nothing for `from_asgi` or `from_wsgi` to call. Run them over a real server instead:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json
```

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

## Django and Django REST Framework

Point the loader at whatever URL serves your schema - with drf-spectacular that is wherever you routed `SpectacularAPIView`. Either callable works; the WSGI one is shown here:

```python
import schemathesis
from myproject.wsgi import application

schema = schemathesis.openapi.from_wsgi("/schema/?format=json", application)
```

The `?format=json` is drf-spectacular's content negotiation; without it the view returns YAML, which Schemathesis also accepts.

`ALLOWED_HOSTS` has to admit the host the in-process client uses - `localhost` for WSGI, `testserver` for ASGI - or Django's `CommonMiddleware` answers every request with `400 Bad Request`. Loading names the rejected host:

```
Failed to load schema due to client error (HTTP 400 Bad Request)

Django rejected the request because its `Host` header is not in `ALLOWED_HOSTS`

    Host:          localhost
    ALLOWED_HOSTS: ['example.com']

Add 'localhost' to ALLOWED_HOSTS in the Django settings you use for testing
```

```python
# settings.py
ALLOWED_HOSTS = ["localhost", "testserver"]
```

The schema view is itself a documented operation, but Schemathesis recognises the endpoint that served the schema and leaves it out. Add an explicit filter selecting it if you do want it tested.

### Database access under pytest-django

Requests run Django's full request lifecycle, so they hit the database. Tests need a database fixture, and Hypothesis rejects function-scoped fixtures on a `@given`-driven test unless you suppress that health check:

```python
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_api(case, db):
    case.call_and_validate()
```

`db` rolls the whole test back, so rows created by one generated input are visible to the next and `transaction.on_commit()` never fires. Use `transactional_db` if the code under test needs committed data.

## Reusing Values From Your Source

When you load an app with `from_asgi` or `from_wsgi`, Schemathesis reads the request handlers' source code and reuses the literal values it finds - status codes, identifiers, enum members, magic strings - as candidate test data. Free-form parameters that random generation rarely satisfies then occasionally receive a value the application recognizes, reaching code paths behind those checks.

This is on by default. Disable it in the config file:

```toml
[analysis.constants]
enabled = false
```

### Registering additional sources

App inspection reaches the request handlers' modules, plus - for Flask - the module the app is defined in. For Django, it walks the URL resolver and reaches every view it routes to. For literals defined elsewhere, or when the handler is a library view served over ASGI rather than your own code (for example GraphQL resolvers behind a Starlette/FastAPI mount), register the source explicitly:

```python
import schemathesis
import myapp.resolvers


@schemathesis.python.constants
def _constants():
    return myapp.resolvers
```

The decorated function returns what to inspect: a module, an application instance, a dotted module name, or an iterable of them. Registered sources are combined with the automatically inspected app; disabling `analysis.constants` turns off both.

## Custom Test Clients

ASGI lifespan is handled for you: loading a schema with `from_asgi` starts the application's lifespan, every generated call reuses it, and shutdown runs at interpreter exit. You do not need a custom client to get startup and shutdown events.

Reach for one when you need requests to share state that Schemathesis does not manage - a cookie jar carried across cases, a fixed header set, or a connection the surrounding test owns:

```python
from fastapi import FastAPI
import schemathesis
from schemathesis.python.asgi import ASGIClient

app = FastAPI()


@app.get("/users")
async def get_users():
    return [{"id": 1, "name": "Alice"}]


schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.parametrize()
def test_api_with_session(case):
    with ASGIClient(app) as client:
        case.call_and_validate(session=client)
```

`ASGIClient` is a `requests.Session` subclass, so cookies, headers and adapters behave the way they do for any other session.

For WSGI applications the equivalent is a `werkzeug.Client`, passed the same way.

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
import schemathesis
from schemathesis.python.asgi import ASGIClient

schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@schema.auth()
class AppAuth:
    def get(self, case, context):
        # Login to get a fresh token
        client = ASGIClient(context.app)
        response = client.post("/auth/token", json={"username": "test_user", "password": "test_password"})
        return response.json()["access_token"]

    def set(self, case, data, context):
        case.headers["Authorization"] = f"Bearer {data}"
```

!!! note ""
    This pattern is for dynamic authentication (login flows, token refresh). For static authentication (API keys, fixed tokens), simply add headers directly to your test client or case objects.
