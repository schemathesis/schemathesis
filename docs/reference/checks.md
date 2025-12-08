# Checks

Schemathesis validates every API response with a fixed set of checks. All checks are **enabled by default** unless explicitly disabled.

## Controlling checks

**Command line:**
```bash
# Only specific checks
st run --checks status_code_conformance,response_schema_conformance
# Skip specific checks
st run --exclude-checks not_a_server_error
```

**Configuration file:**
```toml
[checks.status_code_conformance]
# Disable globally or per-operation
enabled = false

[checks.negative_data_rejection]
# Override expected status codes
expected-statuses = ["400", "422"]
```

See [CLI reference](../reference/cli.md#-c-checks-checks) and [configuration reference](../reference/configuration.md#checks) for details.

!!! note "Exception: max_response_time"
    The `max_response_time` check is **disabled by default**. Enable it with `--max-response-time <seconds>` or `[checks.max_response_time]` in configuration. It is independent from `--checks`/`--exclude-checks`.

## Response timing

### `max_response_time`

Catches responses that take longer than the configured limit.

```text
- Response time limit exceeded

Actual: 2500.00ms
Limit: 1500.00ms
```

This check validates response times against your threshold. The actual HTTP request timeout is controlled separately by `--request-timeout` (defaults to 10 seconds).

---

## Core response validation

### `not_a_server_error`

Catches server-side errors (5xx status codes) and GraphQL `errors` arrays.

```text
- Server error

[500] Internal Server Error:
    `Server got itself in trouble`
```

For GraphQL APIs, this check validates both transport-level errors and GraphQL semantics.

---

### `status_code_conformance`

Verifies the response status code is documented in the schema for the operation (or a `default` response is defined).

```text
- Undocumented HTTP status code

Received: 403
Documented: 200, 404
```

Wildcards such as `2XX` in the schema are expanded automatically. To silence failures for intentional extra status codes, add them to the schema or use a `default` response.

---

### `content_type_conformance`

Validates the `Content-Type` header matches the schema. Fails when the header is missing, malformed, or uses an undocumented media type.

When Content-Type header is missing:
```text
- Missing Content-Type header

The following media types are documented in the schema:
- application/json
```

When media type is malformed or undocumented:
```text
- Malformed media type

Media type for Response is incorrect

Received: text/html; charset==utf-8
Documented: text/html; charset=utf-8
```

Raises `Malformed media type` when parsing fails and `Undocumented Content-Type` when the header is outside the documented list. Wildcards such as `application/*` are respected.

---

### `response_headers_conformance`

Ensures response header values match their JSON Schema definitions.

When a required header is missing:
```text
- Missing required headers

The following required headers are missing from the response:
- `X-RateLimit-Remaining`
```

When a header value violates its schema:
```text
- Response header does not conform to the schema

Header 'X-RateLimit-Limit' does not conform to the schema

Value: "invalid"
Expected type: integer
```

Header values are coerced into appropriate JSON Schema types before validation, so numeric and boolean headers are validated as structured data rather than raw strings.

---

### `response_schema_conformance`

Validates the response body against its JSON Schema definition. Catches format mismatches, missing required properties, type errors, and more.

```text
- Response violates schema

  'message' is a required property

  Schema:
      {
          "required": ["message"],
          "type": "object"
      }
```

Errors are deduplicated per schema path. Use `[output]` configuration options to expand or truncate large payloads in failure messages.

---

## Input handling

### `negative_data_rejection`

Verifies the API properly rejects invalid input data. When Schemathesis generates a negative test case (invalid payload, missing required field, etc.), it expects the API to respond with an error status code.

```text
- API accepted schema-violating request

Invalid data should have been rejected
Expected: 400, 401, 403, 404, 422, 428, 5xx
Invalid component: Missing `Accept-Language` at header
```

The failure message includes the specific invalid component, helping you identify which validation logic is incorrect.

!!! note "Test mode"
    Requires `--mode negative` or `--mode all` (default)  

---

### `positive_data_acceptance`

Verifies the API accepts valid request data. When Schemathesis generates schema-compliant requests, it expects the API to respond with a success status code.

```text
- API rejected schema-compliant request

Valid data should have been accepted
Expected: 2xx, 401, 403, 404, 409, 5xx
```

By default, `expected-statuses` includes `401/403/404/409/5xx` to account for authentication requirements, missing resources, conflicts (e.g., duplicate entries), and downstream failures.

!!! note "Test mode"
    Requires `--mode positive` or `--mode all` (default)

---

### `missing_required_header`

Verifies the API rejects requests when required headers are missing. The API should respond with `406 Not Acceptable` (or `401 Unauthorized` for `Authorization` headers).

```text
- Missing header not rejected

Got 200 when missing required 'X-API-Key' header, expected 406
```

!!! note "Coverage phase"
    Triggered during coverage phase scenarios where required headers are deliberately omitted

---

### `unsupported_method`

Verifies the API properly rejects HTTP methods not defined for an endpoint. The API should return `405 Method Not Allowed` with an `Allow` header listing the supported methods (required by [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.6)).

```text
- Unsupported methods

Unsupported method PATCH returned 200, expected 405 Method Not Allowed

Return 405 for methods not listed in the OpenAPI spec
```

!!! note "Coverage phase"
    Triggered during coverage phase scenarios that test undocumented HTTP methods

---


## Stateful behavior

These checks verify API behavior across sequences of operations. They only trigger when links between operations are available. See the [stateful testing guide](../guides/stateful-testing.md) for details.

### `use_after_free`

Detects when deleted resources remain accessible. After a successful `DELETE`, subsequent requests to the same resource should return `404 Not Found`.

```text
- Use after free

The API did not return a `HTTP 404 Not Found` response (got `HTTP 200 OK`)
for a resource that was previously deleted.
```

The failure message lists both the DELETE call and the subsequent operation for manual reproduction.

---

### `ensure_resource_availability`

Verifies created resources are immediately accessible. After a successful `POST`, Schemathesis follows links to fetch the resource. The API should return the created resource, not `404 Not Found`.

```text
- Resource is not available after creation

Created with      : `POST /users`
Not available with: `GET /users/{id}`
The API returned `404 Not Found` for a resource that was just created.
```

---

## Security

### `ignored_auth`

Verifies authentication is properly enforced. When an operation declares authentication, Schemathesis tests whether the API accepts requests without credentials or with invalid credentials.

```text
- API accepts requests without authentication

Expected 401, got `200 OK` for `GET /protected-resource`
```

!!! warning "Additional requests"
    This check sends extra HTTP requests per operation (one without auth, one with invalid auth).
