# API Authentication

Configure Schemathesis to send valid credentials, from a static token to a login flow that refreshes expiring tokens.

## Prerequisites

- A running API and its schema URL, for example `http://localhost:8000/openapi.json`
- Credentials for that API: a token, an API key, a username and password, or a login endpoint that issues tokens
- `uvx` from [uv](https://docs.astral.sh/uv/) to run Schemathesis, or Schemathesis installed with `pip`

Without valid credentials, Schemathesis lists the rejected operations under `WARNINGS` at the end of the run:

```
Authentication failed: 4 operations returned authentication errors

401 Unauthorized (4 operations):
  - DELETE /users/{userId}
  - GET /users/{userId}
  - POST /auth/token
  - POST /users

💡 Ensure valid authentication credentials are set via --auth or -H
```

Once authentication works, the operations you authenticated disappear from that list.

## Static Authentication

For simple cases pass credentials on the command line:

```bash
# Bearer token
uvx schemathesis run http://localhost:8000/openapi.json \
  --header "Authorization: Bearer your-token"

# Basic authentication
uvx schemathesis run http://localhost:8000/openapi.json \
  --auth username:password

# API key
uvx schemathesis run http://localhost:8000/openapi.json \
  --header "X-API-Key: your-api-key"
```

For reusable configuration use a config file. `${VAR}` values are read from environment variables:

```toml
# schemathesis.toml
headers = { Authorization = "Bearer ${API_TOKEN}" }

# Different auth for every operation under /admin/
[[operations]]
include-path-regex = "^/admin/"
headers = { Authorization = "Bearer ${ADMIN_TOKEN}", X-Client-ID = "${CLIENT_ID}" }
```

```bash
export API_TOKEN="your-secret-token"
uvx schemathesis run http://localhost:8000/openapi.json
```

## OpenAPI-Aware Authentication

Configure authentication that follows your OpenAPI schema's security definitions. Schemathesis reads parameter names and locations directly from `securitySchemes`.

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
uvx schemathesis run http://localhost:8000/openapi.json
```

Each config block name must match a `securityScheme` name from your OpenAPI spec. Schemathesis extracts the parameter location (`header`, `query`, or `cookie`) and name from the schema, so you only provide the value.

| Type | Scheme | Config Fields | OpenAPI Version |
|------|--------|---------------|-----------------|
| `apiKey` | - | `api_key` | 2.0, 3.x |
| `http` | `basic` | `username`, `password` | 3.x (2.0 as `basic`) |
| `http` | `bearer` | `bearer` | 3.x |

When several sources provide authentication:

- Any configured authentication - an `[auth.*]` section, `--auth` or `--auth-wfc` - disables auth classes registered with `@schemathesis.auth()` in hooks. Those classes apply only when nothing else configures authentication.
- `--auth` and `--auth-wfc` on the command line override the matching settings in `schemathesis.toml`.
- Headers from `-H` or `[headers]` are not authentication settings: they are sent with every request and do not disable auth classes.

!!! note
    A config file can use only one of these authentication methods: `[auth.basic]`, OpenAPI-aware authentication (`[auth.openapi.*]` and `[auth.dynamic.openapi.*]`, which can be combined), or `[auth.wfc]`. Configuring two of them is a configuration error.

## Declarative Dynamic Authentication

Declare the token fetch endpoint in `schemathesis.toml` and Schemathesis handles the rest. For a `BearerAuth` scheme in your OpenAPI spec and a `/auth/token` endpoint returning `{"access_token": "..."}`:

```toml
# schemathesis.toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
extract_selector = "/access_token"
```

Schemathesis POSTs to `/auth/token`, extracts the token using a [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901), and applies it to every request requiring `BearerAuth`. The token is cached and fetched again every 300 seconds.

To send credentials with the request:

```toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
payload = { username = "${USERNAME}", password = "${PASSWORD}" }
extract_selector = "/access_token"
```

For login endpoints that expect form-encoded credentials (common for OAuth 2.0 password grants), set `payload_content_type`:

```toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
payload = { grant_type = "password", username = "${USERNAME}", password = "${PASSWORD}" }
payload_content_type = "application/x-www-form-urlencoded"
extract_selector = "/access_token"
```

If the token is in a response header instead of the body:

```toml
[auth.dynamic.openapi.BearerAuth]
path = "/auth/token"
extract_from = "header"
extract_selector = "X-Auth-Token"
```

For form logins that answer with `Set-Cookie`, name the cookie and Schemathesis sends it back as a cookie, without its attributes:

```toml
[auth.dynamic.openapi.SessionCookie]
path = "/login"
payload = { username = "${USERNAME}", password = "${PASSWORD}" }
payload_content_type = "application/x-www-form-urlencoded"
extract_from = "cookie"
extract_selector = "SESSION"
```

Works the same way for `apiKey` schemes — Schemathesis reads the parameter name and location from the schema's `securitySchemes`.

| Field | Default | Description |
|-------|---------|-------------|
| `path` | required | Path on the API host (must start with `/`) |
| `method` | `"post"` | HTTP method for the fetch request |
| `payload` | none (no body) | Body sent with the fetch request; supports `${ENV_VAR}` substitution |
| `payload_content_type` | `"application/json"` | Media type for the payload; accepts `application/json` (and any `+json` variant) or `application/x-www-form-urlencoded` |
| `extract_from` | `"body"` | Where to find the token: `"body"`, `"header"` or `"cookie"` |
| `extract_selector` | required | JSON Pointer (body), header name, or cookie name |

For a different refresh schedule or scope-based caching, use a [Python auth class](#dynamic-token-authentication) instead.

## Web Fuzzing Commons

Point Schemathesis at a [Web Fuzzing Commons](https://github.com/WebFuzzing/Commons) (WFC) auth document to authenticate from it:

```toml
# schemathesis.toml
[auth.wfc]
path = "auth.json"
```

Or from the CLI:

```console
$ uvx schemathesis run http://localhost:8000/openapi.json --auth-wfc auth.json
```

The document lists one or more users. Static credentials use `fixedHeaders`:

```json
{
  "auth": [
    {"name": "admin", "fixedHeaders": [{"name": "X-Api-Key", "value": "your-api-key"}]}
  ]
}
```

A login flow uses `loginEndpointAuth` — Schemathesis calls the endpoint, extracts the token with a [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901), and applies it to every request:

```json
{
  "auth": [
    {
      "name": "user",
      "loginEndpointAuth": {
        "verb": "POST",
        "endpoint": "/auth/login",
        "contentType": "application/json",
        "payloadUserPwd": {
          "username": "demo",
          "password": "your-password",
          "usernameField": "username",
          "passwordField": "password"
        },
        "token": {
          "extractFrom": "body",
          "extractSelector": "/access_token",
          "sendIn": "header",
          "sendName": "Authorization",
          "sendTemplate": "Bearer {token}"
        }
      }
    }
  ]
}
```

Set `"expectCookies": true` instead of `token` when the endpoint returns a session cookie. The login result is cached and the login flow runs again every `refresh_interval` seconds (300 by default).

When the login lives on a separate auth server, the document points at it with `externalEndpointURL`, and Schemathesis calls that address as written.

The WFC document holds literal values: `${VAR}` inside it is sent as written. To keep secrets out of the repository, generate the file from environment variables in CI, for example with `jq`:

```bash
jq -n --arg key "$API_KEY" \
  '{auth: [{name: "admin", fixedHeaders: [{name: "X-Api-Key", value: $key}]}]}' > auth.json
uvx schemathesis run http://localhost:8000/openapi.json --auth-wfc auth.json
```

The `[auth.wfc]` keys in `schemathesis.toml` do support `${VAR}`, so `path = "${WFC_AUTH_FILE}"` works.

| Field | CLI | Default | Description |
|-------|-----|---------|-------------|
| `path` | `--auth-wfc` | required | Path to the WFC auth document (JSON or YAML) |
| `user` | `--auth-wfc-user` | unset | `name` of the auth entry to use for every request |
| `refresh_interval` | — | `300` | Seconds before re-running the login flow |

When the document lists several users and `user` is unset, Schemathesis chooses an identity per operation. It starts with the first user, and each time the operation answers `401` or `403` it moves on: next to sending no credentials, then to the remaining users in document order. Once the operation answers with a `2xx` or `3xx` status, it keeps that identity for the rest of the run. Set `user` to send one identity with every request.

## Dynamic Token Authentication

Static options can't handle tokens that expire, so write a Python auth class and load it with Schemathesis.

1. Create `auth.py` next to where you run Schemathesis:

    ```python
    # auth.py
    import requests

    import schemathesis


    @schemathesis.auth()
    class TokenAuth:
        def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
            response = requests.post("http://localhost:8000/auth/token", json={"username": "demo", "password": "test"})
            response.raise_for_status()
            return response.json()["access_token"]

        def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
            case.headers = case.headers or {}
            case.headers["Authorization"] = f"Bearer {data}"
    ```

    `get` fetches a token, and `set` attaches it to each request.

2. Load the module through `SCHEMATHESIS_HOOKS` and run the tests:

    ```bash
    export SCHEMATHESIS_HOOKS=auth
    uvx schemathesis run http://localhost:8000/openapi.json
    ```

    To load it without the environment variable, set `hooks = "auth"` in `schemathesis.toml`.

The protected operations no longer appear under `Authentication failed` in the warnings. Schemathesis caches the token for 300 seconds by default.

## Token Refresh Management

To refresh tokens on a different schedule, pass `refresh_interval`:

```python
# auth.py
import requests

import schemathesis


@schemathesis.auth(refresh_interval=600)
class RefreshableAuth:
    def __init__(self) -> None:
        self.refresh_token = None

    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        if self.refresh_token:
            return self.refresh_access_token()
        return self.login()

    def login(self) -> str:
        response = requests.post("http://localhost:8000/auth/login", json={"username": "demo", "password": "test"})
        data = response.json()
        self.refresh_token = data["refresh_token"]
        return data["access_token"]

    def refresh_access_token(self) -> str:
        response = requests.post(
            "http://localhost:8000/auth/refresh", headers={"Authorization": f"Bearer {self.refresh_token}"}
        )
        data = response.json()
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        return data["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
```

- `refresh_interval=600` - Get new tokens every 10 minutes
- `refresh_interval=None` - Disable caching entirely
- Default: 300 seconds

## Cache Key Management

Cache different tokens based on specific criteria like OAuth scopes:

```python
# auth.py
import requests

import schemathesis


def get_required_scopes(case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
    """Comma-separated OAuth scopes of the operation's first security requirement."""
    security = ctx.operation.definition.raw.get("security", [])
    if not security or not security[0]:
        return ""
    scheme_name = next(iter(security[0]))
    return ",".join(sorted(security[0][scheme_name]))


@schemathesis.auth(cache_by_key=get_required_scopes)
class ScopedAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        scopes = get_required_scopes(case, ctx)
        response = requests.post(
            "http://localhost:8000/auth/token",
            json={"username": "demo", "password": "test", "scopes": scopes.split(",") if scopes else []},
        )
        return response.json()["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
```

Operations requiring different scopes (e.g., `read` vs `read,write`) get separate tokens.

## Selective Authentication

Apply authentication only to specific endpoints:

```python
# auth.py
import requests

import schemathesis


def fetch_token(url: str, username: str, password: str) -> str:
    response = requests.post(url, json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


@schemathesis.auth().apply_to(path_regex="^/users/").skip_for(method="POST")
class UserAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        return fetch_token("http://localhost:8000/auth/user-token", "demo", "test")

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"


@schemathesis.auth().apply_to(path_regex="^/admin/")
class AdminAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        return fetch_token("http://localhost:8000/auth/admin-token", "admin", "admin-pass")

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"
```

`path` matches the whole path exactly, so use `path_regex` to cover every path under a prefix. Other filter patterns:

```python
# Exact paths
@schemathesis.auth().apply_to(path=["/users", "/orders"])

# Method-specific
@schemathesis.auth().apply_to(method=["POST", "PUT", "DELETE"])

# Skip public endpoints
@schemathesis.auth().skip_for(path="/health", method="GET")
```

Available filters: `path`, `method`, `name`, `tag`, `operation_id` (add `_regex` for regex matching).

To apply auth only to operations that require a specific OpenAPI security scheme:

```python
@schemathesis.auth().apply_to(schemathesis.openapi.require_security_scheme("session"))
class SessionAuth: ...
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
uvx schemathesis run http://localhost:8000/openapi.json
```

Headers from a matching `[[operations]]` block are merged with the global `headers`. Matching keys override the global value, and any keys not mentioned in the operation block are still inherited.

!!! note
    `auth.openapi.*` schemes are only supported at the global level, not inside `[[operations]]`. Use `headers` for per-operation credential overrides.

## Third-Party Authentication

For protocols such as NTLM, reuse an existing `requests.auth` implementation. Install its package in the same environment as Schemathesis (`uvx --with requests-ntlm schemathesis ...`) and load this module through `SCHEMATHESIS_HOOKS` as shown [above](#dynamic-token-authentication):

```python
# auth.py
from requests_ntlm import HttpNtlmAuth

import schemathesis

schemathesis.auth.set_from_requests(HttpNtlmAuth("domain\\username", "password"))
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
import requests

import schemathesis

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")


@schema.auth()
class APITokenAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        response = requests.post("http://localhost:8000/auth/token", json={"username": "demo", "password": "test"})
        return response.json()["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"


@schema.parametrize()
def test_api(case: schemathesis.Case) -> None:
    # Auth applied automatically
    case.call_and_validate()
```

Or register it for specific tests only, passing the class instead of decorating it:

```python
import requests

import schemathesis

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")


class APITokenAuth:
    def get(self, case: schemathesis.Case, ctx: schemathesis.AuthContext) -> str:
        response = requests.post("http://localhost:8000/auth/token", json={"username": "demo", "password": "test"})
        return response.json()["access_token"]

    def set(self, case: schemathesis.Case, data: str, ctx: schemathesis.AuthContext) -> None:
        case.headers = case.headers or {}
        case.headers["Authorization"] = f"Bearer {data}"


@schema.auth(APITokenAuth)
@schema.parametrize()
def test_protected_endpoints(case: schemathesis.Case) -> None:
    case.call_and_validate()
```

### Session Management

For persistent sessions or custom client configuration:

```python
import requests

import schemathesis

schema = schemathesis.openapi.from_url("http://localhost:8000/openapi.json")


@schema.parametrize()
def test_with_session(case: schemathesis.Case) -> None:
    with requests.Session() as session:
        session.auth = ("user", "password")
        case.call_and_validate(session=session)
```

!!! tip ""
    Custom auth classes support the same features in pytest as in the CLI (refresh intervals, cache keys, selective application) with identical syntax.

## Troubleshooting

**Operations still listed under `Authentication failed`**: The credentials are missing or rejected. Check that the environment variables referenced in `schemathesis.toml` are exported in the same shell, and that `SCHEMATHESIS_HOOKS` names a module importable from the current directory.

**`Cannot use multiple authentication methods simultaneously`**: The config file sets more than one of `[auth.basic]`, `[auth.openapi.*]`/`[auth.dynamic.openapi.*]`, and `[auth.wfc]`. Keep one.

**The `Authorization` header is missing or modified on some requests**: This is intentional. Schemathesis removes or alters auth on some requests to check that your API rejects unauthenticated calls. See [Why is Schemathesis skipping my Authorization header?](../faq.md#why-is-schemathesis-skipping-my-authorization-header).

**You need to see the credentials Schemathesis sent**: Output and reproduction commands hide sensitive values by default. Disable sanitization to see them:

```bash
uvx schemathesis run http://localhost:8000/openapi.json \
  --header "Authorization: Bearer your-token" \
  --output-sanitize false
```

## What's Next

- **[Configuration Reference](../reference/configuration.md)** - Complete configuration options
- **[Extending Schemathesis](extending.md)** - Other customization options
