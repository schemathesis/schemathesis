# API Authentication

Configure authentication for APIs that require credentials, from simple static tokens to dynamic refresh patterns.

## Static Authentication

For simple cases use CLI options directly.

```bash
# Bearer token
schemathesis run http://localhost:8000/openapi.json \
  --header "Authorization: Bearer your-token"

# Basic authentication
schemathesis run http://localhost:8000/openapi.json \
  --auth username:password

# API key
schemathesis run http://localhost:8000/openapi.json \
  --header "X-API-Key: your-api-key"
```

For reusable configuration use a config file.

```toml
# schemathesis.toml
headers = { Authorization = "Bearer ${API_TOKEN}" }

# Different auth for specific endpoints
[[operations]]
include-path = "/admin/"
headers = {
  Authorization = "Bearer ${ADMIN_TOKEN}",
  X-Client-ID = "${CLIENT_ID}"
}
```

```bash
export API_TOKEN="your-secret-token"
schemathesis run http://localhost:8000/openapi.json
```

## OpenAPI-Aware Authentication

Configure authentication that automatically aligns with your OpenAPI schema's security definitions. Schemathesis reads parameter names and locations directly from `securitySchemes`.

```toml
# schemathesis.toml
[auth.openapi.ApiKeyAuth]
api_key = "${API_KEY}"

[auth.openapi.BearerAuth]
bearer = "${TOKEN}"

[auth.openapi.BasicAuth]
username = "${USERNAME}"
password = "${PASSWORD}"
```

```bash
export API_KEY="your-api-key"
export TOKEN="your-token"
schemathesis run http://localhost:8000/openapi.json
```

Each config block name must match a `securityScheme` name from your OpenAPI spec. Schemathesis extracts the parameter location (`header`, `query`, or `cookie`) and name from the schema, so you only provide the value.

**Supported types:**

| Type | Scheme | Config Fields | OpenAPI Version |
|------|--------|---------------|-----------------|
| `apiKey` | - | `api_key` | 2.0, 3.x |
| `http` | `basic` | `username`, `password` | 3.x (2.0 as `basic`) |
| `http` | `bearer` | `bearer` | 3.x |

**Authentication precedence (highest to lowest):**

1. **Programmatic auth** - Explicit `@schemathesis.auth()` decorators
2. **CLI flags** - `--auth` and `--header` (always override config)
3. **OpenAPI-aware config** - `[auth.openapi.*]` and `[auth.dynamic.openapi.*]` (target specific security schemes)
4. **Global auth** - Fallback authentication

!!! note
    You cannot mix `[auth.basic]` and `[auth.openapi.*]` in the same config file. Choose one authentication strategy.

## Declarative Dynamic Authentication

Declare the token fetch endpoint in `schemathesis.toml` and Schemathesis handles the rest. For a `BearerAuth` scheme in your OpenAPI spec and a `/auth/token` endpoint returning `{"access_token": "..."}`:

```toml
# schemathesis.toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
extract_selector = "/access_token"
```

Schemathesis POSTs to `/auth/token`, extracts the token using a [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901), and applies it to every request requiring `BearerAuth`. The token is cached for the test run.

To send credentials with the request:

```toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
payload = { username = "${USERNAME}", password = "${PASSWORD}" }
extract_selector = "/access_token"
```

If the token is in a response header instead of the body:

```toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
extract_from = "header"
extract_selector = "X-Auth-Token"
```

Works the same way for `apiKey` schemes — Schemathesis reads the parameter name and location from the schema's `securitySchemes`.

| Field | Default | Description |
|-------|---------|-------------|
| `path` | required | Path on the API host (must start with `/`) |
| `method` | `"post"` | HTTP method for the fetch request |
| `payload` | `{}` | JSON body sent with the fetch request; supports `${ENV_VAR}` substitution |
| `extract_from` | `"body"` | Where to find the token: `"body"` or `"header"` |
| `extract_selector` | required | JSON Pointer (body) or header name |

For token refresh or scope-based caching, use a [Python auth class](#dynamic-token-authentication) instead.

## Dynamic Token Authentication

Static options can't handle tokens that expire, so create a custom authentication class:

```python
# auth.py
import requests
import schemathesis

@schemathesis.auth()
class TokenAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        response = requests.post(
            "http://localhost:8000/auth/token",
            json={"username": "demo", "password": "test"}
        )
        return response.json()["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
```

Schemathesis caches tokens for 300 seconds by default.

## Token Refresh Management

```python
@schemathesis.auth(refresh_interval=600)  # Refresh every 10 minutes
class RefreshableAuth:
    def __init__(self) -> None:
        self.refresh_token = None

    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        if self.refresh_token:
            return self.refresh_access_token()
        else:
            return self.login()
    
    def login(self) -> str:
        response = requests.post(
            "http://localhost:8000/auth/login",
            json={"username": "demo", "password": "test"}
        )
        data = response.json()
        self.refresh_token = data["refresh_token"]
        return data["access_token"]

    def refresh_access_token(self) -> str:
        response = requests.post(
            "http://localhost:8000/auth/refresh",
            headers={"Authorization": f"Bearer {self.refresh_token}"}
        )
        data = response.json()
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        return data["access_token"]

    # Define `set` as before ... 
```

- `refresh_interval=600` - Get new tokens every 10 minutes
- `refresh_interval=None` - Disable caching entirely
- Default: 300 seconds


## Cache Key Management

Cache different tokens based on specific criteria like OAuth scopes:

```python
@schemathesis.auth(cache_by_key=lambda case, ctx: get_required_scopes(ctx))
class ScopedAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        scopes = get_required_scopes(ctx)
        response = requests.post(
            "http://localhost:8000/auth/token",
            json={
                "username": "demo", 
                "password": "test",
                "scopes": scopes.split(",") if scopes else []
            }
        )
        return response.json()["access_token"]

    # Define `set` as before ... 

def get_required_scopes(ctx: schemathesis.AuthContext) -> str:
    """Extract required OAuth scopes from operation security requirements"""
    security = ctx.operation.definition.raw.get("security", [])
    if not security:
        return ""

    # Get first security requirement
    security_req = security[0]
    if not security_req:
        return ""

    # Get scopes for the first scheme
    scheme_name = list(security_req.keys())[0]
    scopes = security_req.get(scheme_name, [])

    return ",".join(sorted(scopes))
```

This ensures separate tokens for operations requiring different permissions (e.g., `read` vs `read,write` scopes).

## Selective Authentication

Apply authentication only to specific endpoints:

```python
@schemathesis.auth().apply_to(path="/users/").skip_for(method="POST")
class UserAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        response = requests.post(
            "http://localhost:8000/auth/user-token",
            json={"username": "demo", "password": "test"}
        )
        return response.json()["access_token"]
    
    # Define `set` as before ... 

@schemathesis.auth().apply_to(path="/admin/")
class AdminAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        response = requests.post(
            "http://localhost:8000/auth/admin-token",
            json={"username": "admin", "password": "admin-pass"}
        )
        return response.json()["access_token"]
    
    # Define `set` as before ... 
```

**Common filter patterns:**

```python
# Multiple paths
@schemathesis.auth().apply_to(path=["/users/", "/orders/"])

# Regex matching
@schemathesis.auth().apply_to(path_regex="^/admin")

# Method-specific
@schemathesis.auth().apply_to(method=["POST", "PUT", "DELETE"])

# Skip public endpoints
@schemathesis.auth().skip_for(path="/health", method="GET")
```

**Available filters:** `path`, `method`, `name`, `tag`, `operation_id` (add `_regex` for regex matching)

To apply auth only to operations that require a specific OpenAPI security scheme:

```python
@schemathesis.auth().apply_to(
    schemathesis.openapi.require_security_scheme("session")
)
class SessionAuth:
    ...
```

## Applying Different Auth to Different Operations

When different operations require different credentials, use multiple `[[operations]]` blocks in `schemathesis.toml`:

```toml
# schemathesis.toml

# Default: API key for most operations
headers = { X-API-Key = "${API_KEY}" }

# Admin token for the user management endpoint
[[operations]]
include-name = "POST /admin/users"
headers = { Authorization = "Bearer ${ADMIN_TOKEN}" }

# Override API key for item listing
[[operations]]
include-name = "GET /items"
headers = { X-API-Key = "${ITEMS_API_KEY}" }
```

```bash
export API_KEY="default-api-key"
export ADMIN_TOKEN="admin-secret-token"
export ITEMS_API_KEY="items-api-key"
schemathesis run http://localhost:8000/openapi.json
```

Headers from a matching `[[operations]]` block are merged with the global `headers`. Matching keys override the global value, and any keys not mentioned in the operation block are still inherited.

!!! note
    `auth.openapi.*` schemes are only supported at the global level, not inside `[[operations]]`. Use `headers` for per-operation credential overrides.

## Advanced: Third-Party Authentication

For specialized authentication protocols not covered by custom auth classes, use third-party `requests.auth` implementations:

```python
# ntlm_auth.py
import schemathesis
from requests_ntlm import HttpNtlmAuth

# Use existing requests auth implementation
schemathesis.auth.set_from_requests(
    HttpNtlmAuth("domain\\username", "password")
)
```

## Setup

Custom authentication classes use the same setup as other extensions:

```bash
export SCHEMATHESIS_HOOKS=auth
schemathesis run http://localhost:8000/openapi.json
```

## Python Tests

### Simple Authentication

Use requests authentication directly with `Case.call_and_validate` or `Case.call`:

```python
import schemathesis
from requests.auth import HTTPDigestAuth

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")

@schema.parametrize()
def test_api(case: schemathesis.Case) -> None:
    # HTTP Basic
    case.call_and_validate(auth=("user", "password"))

    # HTTP Digest
    case.call_and_validate(auth=HTTPDigestAuth("user", "password"))

    # Static headers
    case.call_and_validate(headers={"Authorization": "Bearer your-token"})
```

### Custom Authentication Classes

Register auth at the schema level for all tests:

```python
@schema.auth()
class APITokenAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        # Same implementation as CLI examples above
        response = requests.post("http://localhost:8000/auth/token", ...)
        return response.json()["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"

@schema.parametrize()
def test_api(case: schemathesis.Case) -> None:
    # Auth applied automatically
    case.call_and_validate()
```

Or register for specific tests only:

```python
@schema.auth(MyAuth)
@schema.parametrize() 
def test_protected_endpoints(case: schemathesis.Case) -> None:
    case.call_and_validate()
```

### Session Management

For persistent sessions or custom client configuration:

```python
import requests

@schema.parametrize()
def test_with_session(case: schemathesis.Case) -> None:
    with requests.Session() as session:
        session.auth = ("user", "password")
        case.call_and_validate(session=session)
```

!!! tip ""
    Custom auth classes support the same advanced features as CLI (refresh intervals, cache keys, selective application) with identical syntax.

## Verifying That Auth Is Applied

By default, Schemathesis sanitizes sensitive values in test output and reproduction commands. To see the actual `Authorization` or `X-API-Key` headers in output, disable sanitization:

```bash
schemathesis run http://localhost:8000/openapi.json \
  --header "Authorization: Bearer your-token" \
  --output-sanitize false
```

!!! note
    If you see the `Authorization` header missing or modified on some requests, this is intentional. Schemathesis removes or alters auth on certain security-testing operations to check whether your API correctly rejects unauthenticated requests. See [Why is Schemathesis skipping my Authorization header?](../faq.md#why-is-schemathesis-skipping-my-authorization-header) in the FAQ.

## What's Next

- **[Configuration Reference](../reference/configuration.md)** - Complete configuration options
- **[Extending Schemathesis](extending.md)** - Other customization options
