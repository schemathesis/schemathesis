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
    The `max_response_time` check is **disabled by default**. Enable it with `--max-response-time <seconds>` or `max_response_time = <seconds>` under `[checks]` in configuration. It is independent from `--checks`/`--exclude-checks`.

## Response timing

### `max_response_time`

Failure codes: [ST112](failures.md#st112).

Catches responses that take longer than the configured limit.

```text
- ST112 Response time limit exceeded

Actual: 2500.00ms
Limit: 1500.00ms
```

This check validates response times against your threshold. The actual HTTP request timeout is controlled separately by `--request-timeout` (defaults to 10 seconds).

---

## Core response validation

### `not_a_server_error`

Failure codes: [ST101](failures.md#st101), [ST102](failures.md#st102), [ST113](failures.md#st113).

Catches server-side errors: 5xx status codes, and GraphQL `errors` arrays from failed resolvers.

During negative coverage, this is the only response check applied to malformed or undeclared request `Content-Type` probes.

```text
- ST101 Server error

[500] Internal Server Error:
    `Server got itself in trouble`
```

Accepts `expected-statuses` (default `["2xx", "3xx", "4xx"]`) to define which status codes are not treated as server errors. To allow a 5xx that only some operations return by design, see [operations that return a documented 5xx](../configuration.md#operations-that-return-a-documented-5xx).

---

### `status_code_conformance`

Failure codes: [ST103](failures.md#st103).

Verifies the response status code is documented in the schema for the operation (or a `default` response is defined).

```text
- ST103 Undocumented status code

Received: 403
Documented: 200, 404
```

Wildcards such as `2XX` in the schema are expanded automatically. To silence failures for intentional extra status codes, add them to the schema or use a `default` response.

---

### `content_type_conformance`

Failure codes: [ST104](failures.md#st104), [ST105](failures.md#st105), [ST106](failures.md#st106).

Validates the `Content-Type` header matches the schema. Fails when the header is missing, malformed, or uses an undocumented media type.

When Content-Type header is missing:
```text
- ST104 Missing Content-Type header

The following media types are documented in the schema:
- application/json
```

When media type is malformed or undocumented:
```text
- ST106 Malformed media type

Media type for Response is incorrect

Received: text/html; charset==utf-8
Documented: text/html; charset=utf-8
```

Raises `Malformed media type` when parsing fails and `Undocumented Content-Type` when the header is outside the documented list. Wildcards such as `application/*` are respected.

---

### `response_headers_conformance`

Failure codes: [ST107](failures.md#st107), [ST108](failures.md#st108).

Ensures response header values match their JSON Schema definitions.

When a required header is missing:
```text
- ST107 Missing required response headers

The following required headers are missing from the response:
- `X-RateLimit-Remaining`
```

When a header value violates its schema:
```text
- ST108 Response header does not conform to the schema

Header 'X-RateLimit-Limit' does not conform to the schema

Value: "invalid"
Expected type: integer
```

Header values are coerced into appropriate JSON Schema types before validation, so numeric and boolean headers are validated as structured data rather than raw strings.

---

### `response_schema_conformance`

Failure codes: [ST104](failures.md#st104), [ST108](failures.md#st108), [ST109](failures.md#st109), [ST110](failures.md#st110), [ST111](failures.md#st111), [ST114](failures.md#st114), [ST115](failures.md#st115), [ST116](failures.md#st116), [ST117](failures.md#st117), [ST118](failures.md#st118).

Validates the response body against its JSON Schema definition. Catches format mismatches, missing required properties, type errors, and more.

```text
- ST108 Response violates schema

  'message' is a required property

  Schema:
      {
          "required": ["message"],
          "type": "object"
      }
```

Errors are deduplicated per schema path. Use `[output]` configuration options to expand or truncate large payloads in failure messages.

Accepts [`validate-formats`](configuration.md#checksresponse_schema_conformancevalidate-formats) (default `true`) to control whether `format` keywords are enforced. Turning it off is a common first step when format violations crowd out structural ones — see [Triaging Failures](../guides/triage.md).

#### Server-Sent Events

For `text/event-stream` responses, Schemathesis validates each event individually against the `itemSchema` defined in the OpenAPI 3.2 response. A `schema` next to it is checked once against the whole stream, read as an array of events. Each failure is prefixed with its event index:

```text
- ST108 SSE event payload violates content schema

  Event #1: "not_an_integer" is not of type "integer"
```

When `itemSchema` properties use `contentMediaType` and `contentSchema`, the embedded payload is deserialized and validated against the nested schema. For example, a `data` field with `contentMediaType: application/json` and `contentSchema: {type: object}` causes Schemathesis to parse the JSON payload and validate it — not just treat it as a raw string.

For schema examples, polymorphic events, and current limitations, see [Testing Server-Sent Events](../guides/server-sent-events.md).

#### GraphQL

For GraphQL APIs, this check fails on a body that is not JSON, a JSON value that is not an object, a `null` returned for a non-nullable field, and `data` that is missing or does not match the query: a missing or unrequested field, or a value of the wrong type.

---

## Input handling

### `negative_data_rejection`

Failure codes: [ST201](failures.md#st201).

Verifies the API properly rejects invalid input data. When Schemathesis generates a negative test case (invalid payload, missing required field, etc.), it expects the API to respond with an error status code.

```text
- ST201 Schema-violating request accepted

Invalid data should have been rejected
Expected: 400, 401, 403, 404, 405, 406, 409, 415, 422, 428, 429, 5xx
Invalid component: Missing `Accept-Language` at header
```

The failure message includes the specific invalid component, helping you identify which validation logic is incorrect.

For GraphQL APIs, an invalid query must get an `errors` entry; the status code does not matter.

!!! note "Test mode"
    Requires `--mode negative` or `--mode all` (default)  

---

### `positive_data_acceptance`

Failure codes: [ST202](failures.md#st202), [ST206](failures.md#st206).

Verifies the API accepts valid request data. When Schemathesis generates schema-compliant requests, it expects the API to respond with a success status code.

```text
- ST202 Schema-valid request rejected

Valid data should have been accepted
Expected: 2xx, 3xx, 401, 403, 404, 409, 429, 5xx
```

By default, `expected-statuses` includes `3xx/401/403/404/409/429/5xx` to account for redirects (e.g., to a sign-in page or an OAuth provider), authentication requirements, missing resources, conflicts (e.g., duplicate entries), rate limiting, and downstream failures.

For GraphQL APIs, the check fails when the server rejects a valid query with a validation error. Rejections for exceeding the server's depth, cost, alias, directive or token limits do not count.

!!! note "Test mode"
    Requires `--mode positive` or `--mode all` (default)

---

### `missing_required_header`

Failure codes: [ST203](failures.md#st203).

Verifies the API rejects requests when required headers are missing.

By default, non-`Authorization` headers are considered correctly rejected with `400`, `401`, `403`, `406`, `415`, or `422`. `Authorization` is stricter and expects `401 Unauthorized`.

```text
- ST203 Missing required header accepted

Got 200 when missing required 'X-API-Key' header, expected 400, 401, 403, 406, 415, 422
```

!!! note "Coverage phase"
    Triggered during coverage phase scenarios where required headers are deliberately omitted

---

### `unsupported_method`

Failure codes: [ST204](failures.md#st204).

Verifies the API properly rejects HTTP methods not defined for an endpoint. The API should return `405 Method Not Allowed` with an `Allow` header listing the supported methods (required by [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.6)).

```text
- ST204 Wrong response to undeclared method

Unsupported method PATCH returned 200, expected 405 Method Not Allowed

Return 405 for methods not listed in the OpenAPI spec
```

!!! note "Coverage phase"
    Triggered during coverage phase scenarios that test undocumented HTTP methods.
    Disabling this check only suppresses failure reporting — requests with unsupported methods are still sent.
    To stop sending these requests entirely, use [`phases.coverage.unexpected-methods`](configuration.md#phasescoverageunexpected-methods).

---

### `allow_header_conformance`

Failure codes: [ST205](failures.md#st205).

Verifies the `Allow` header returned for `OPTIONS` requests lists the same methods as the schema. A mismatch means clients are told the resource supports a different set of operations than the one documented; extra methods may expose endpoints that were never meant to be public.

```text
- ST205 Allow header mismatch

`Allow` header does not match the schema — missing documented methods: POST

List exactly the methods this resource supports in `Allow`
```

`HEAD` and `OPTIONS` are ignored on both sides, since HTTP frameworks usually handle them without a schema declaration.

!!! note "Coverage phase"
    Uses the `OPTIONS` request the coverage phase already sends, so it costs no extra calls.
    It is skipped if `OPTIONS` is removed from [`phases.coverage.unexpected-methods`](configuration.md#phasescoverageunexpected-methods) or if the response carries no `Allow` header.

---


## Stateful behavior

These checks verify API behavior across sequences of operations. They only trigger when links between operations are available. See the [stateful testing guide](../guides/stateful-testing.md) for details.

They need a multi-step scenario, so they run under `st run`'s stateful phase and under `schema.as_state_machine()`. A single `case.call_and_validate()` has no scenario history, so these two never fire there.

### `use_after_free`

Failure codes: [ST301](failures.md#st301).

Detects when deleted resources remain accessible. After a successful `DELETE`, subsequent requests to the same resource should return `404 Not Found`.

```text
- ST301 Deleted resource still available

The API did not return a `HTTP 404 Not Found` response (got `HTTP 200 OK`)
for a resource that was previously deleted.
```

The failure message lists both the DELETE call and the subsequent operation for manual reproduction.

---

### `ensure_resource_availability`

Failure codes: [ST302](failures.md#st302).

Verifies created resources are immediately accessible. After a successful `POST`, Schemathesis follows links to fetch the resource. The API should return the created resource, not `404 Not Found`.

```text
- ST302 Created resource not found

Created with      : `POST /users`
Not available with: `GET /users/{id}`
The API returned `404 Not Found` for a resource that was just created.
```

---

## Security

### `ignored_auth`

Failure codes: [ST401](failures.md#st401).

Verifies authentication is properly enforced. When an operation declares authentication, Schemathesis tests whether the API accepts requests without credentials or with invalid credentials.

`401`, `403`, or a redirect to another page, such as a sign-in page, counts as enforcement. Test cases with invalid path parameters are not judged, as they may miss the operation's route entirely.

```text
- ST401 API accepts requests without authentication

Expected 401 or 403, got `200 OK` for `GET /protected-resource`
```

!!! warning "Additional requests"
    For each operation, this check sends one request without credentials and one with invalid credentials per security scheme, until the operation rejects them once.

The check gives no verdict when an explicitly configured `Authorization` header (e.g. `-H 'Authorization: ...'`) carries credentials the schema's security schemes do not declare: the probes cannot remove them, so a successful response proves nothing. While such a header is configured, an API that ignores the declared scheme entirely is not reported for that operation.

For gRPC, a successful call that carried `authorization` metadata is resent once per method without it, and must fail with `UNAUTHENTICATED` or `PERMISSION_DENIED`.

### `object_level_authorization`

Failure codes: [ST402](failures.md#st402).

Verifies that one user cannot read an object another user owns (OWASP API1, Broken Object Level Authorization). Runs only when [`[auth.wfc]`](configuration.md#authwfc) lists at least two `peers`: users whose objects must stay private to each other; see [Testing Object-Level Authorization](../guides/object-level-authorization.md) for setup. Operations the schema declares public (no requirement under a schema-wide `security`) are skipped; when the schema declares no `security` at all, every operation is checked, since the server may still require credentials.

A peer owns an identifier when:

- its own earlier write (e.g. a `POST`) returned it, or
- a list it requested returned it, and the same list requested by another peer does not.

When a `GET` with an owned identifier succeeds, Schemathesis repeats it as each other peer. The check fails when another peer gets a `2xx` response with the same object: the identifier sits at the same place in both bodies, every field present in both bodies has the same value, and at least one such field is not the identifier. A field only one body holds is ignored. Only fields the response schema declares count; where it declares none, every field counts.

The check passes when the other peer gets:

- different field values, e.g. masked ones, or an empty list
- only the identifier
- the same body as an unauthenticated request, which makes the object public
- the full object from a list it can request, which makes the object shared

```text
- ST402 Object-level authorization bypass

    `bob` received the `Order` that `alice` owns

    Owner: GET /api/orders/1 as alice -> 200
    Peer:  GET /api/orders/1 as bob -> 200 (equivalent body)
```

!!! warning "Additional requests"
    For each operation and resource, this check sends one request per other peer, plus one unauthenticated request when a peer receives the object.
