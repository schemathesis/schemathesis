# Failure Codes

Every failure Schemathesis reports starts with a code, such as `ST101`. `st explain <code>` prints the same
information in the terminal.

## Response validation (ST1xx)

### ST101 Server error {#st101}

Reported by [`not_a_server_error`](checks.md#not_a_server_error).

5xx status for a request the API should have handled or rejected with a 4xx.

A 5xx fails even when the schema documents it. A 5xx by design, such as `503` when an integration is off, can be allowed for the operation:

```toml
# schemathesis.toml
[[operations]]
include-name = "POST /orders"
checks.not_a_server_error.expected-statuses = ["2xx", "3xx", "4xx", "503"]
```

`--exclude-checks not_a_server_error` turns it off, along with ST102, ST113.

### ST102 Server error on undeclared request Content-Type {#st102}

Reported by [`not_a_server_error`](checks.md#not_a_server_error).

5xx for a request sent with an undeclared `Content-Type`, while the same request with a declared one succeeds.

Schemathesis sends media types the operation doesn't declare, and multipart without a boundary. Reject them with `415` or `400` before the body parser runs.

`--exclude-checks not_a_server_error` turns it off, along with ST101, ST113.

### ST103 Undocumented status code {#st103}

Reported by [`status_code_conformance`](checks.md#status_code_conformance).

Status code missing from the operation's documented responses.

`401` or `403` usually means the run had no credentials: pass them with `--header` or `--auth`. Don't add a `default` response to silence it: it accepts every status and hides real mismatches.

`--exclude-checks status_code_conformance` turns it off.

### ST104 Missing Content-Type header {#st104}

Reported by [`content_type_conformance`](checks.md#content_type_conformance), [`response_schema_conformance`](checks.md#response_schema_conformance).

Response without a `Content-Type` header, though the schema documents a media type for its status.

For a response with no body, such as `204`, remove `content` from it in the schema.

`--exclude-checks content_type_conformance,response_schema_conformance` turns it off, along with ST105, ST106, ST109, ST110, ST111, ST114, ST115, ST116, ST117, ST118.

### ST105 Undocumented Content-Type {#st105}

Reported by [`content_type_conformance`](checks.md#content_type_conformance).

`Content-Type` that matches none of the documented media types.

Error handlers often send `application/problem+json`; document it on the error responses.

`--exclude-checks content_type_conformance` turns it off, along with ST106.

### ST106 Malformed media type {#st106}

Reported by [`content_type_conformance`](checks.md#content_type_conformance).

`Content-Type` or a schema media type that is not a valid `type/subtype`.

The message says which side is malformed. Usually a missing `/`, as in `application-json`.

`--exclude-checks content_type_conformance` turns it off, along with ST105.

### ST107 Missing required response headers {#st107}

Reported by [`response_headers_conformance`](checks.md#response_headers_conformance).

Required response header missing for this status.

A required header under `default` or a range such as `4XX` applies to error responses too.

`--exclude-checks response_headers_conformance` turns it off.

### ST108 Response violates schema {#st108}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance), [`response_headers_conformance`](checks.md#response_headers_conformance).

Response body or header value that its documented schema rejects.

If `format` alone causes most failures, skip format validation while you fix the structural ones:

```toml
# schemathesis.toml
[checks.response_schema_conformance]
validate-formats = false
```

`--exclude-checks response_schema_conformance,response_headers_conformance` turns it off, along with ST107, ST109, ST110, ST111, ST114, ST115, ST116, ST117, ST118.

### ST109 JSON deserialization error {#st109}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

Body that is not valid JSON, under a JSON `Content-Type` or with no `Content-Type`.

An empty body fails too (`Expecting value: line 1 column 1`): send JSON, or remove `content` from that response in the schema.

`--exclude-checks response_schema_conformance` turns it off, along with ST110, ST111, ST114, ST115, ST116, ST117, ST118.

### ST110 Content deserialization error {#st110}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

Body that the parser for its `Content-Type` cannot read, other than invalid JSON (ST109).

Errors raised by your `@schemathesis.deserializer` functions are reported here too.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST111, ST114, ST115, ST116, ST117, ST118.

### ST111 Unknown discriminator value {#st111}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

Discriminator value that matches no schema in the `discriminator` mapping.

One unknown value is reported per response, so rerunning after a fix can show the next one.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST114, ST115, ST116, ST117, ST118.

### ST112 Response time limit exceeded {#st112}

Reported by [`max_response_time`](checks.md#max_response_time).

Response slower than the configured response time limit.

Measured until the response headers arrive. `--checks all` turns it on with a 10s limit. Timings vary, so no replay case is stored: rerun `st run`. A slow-by-design operation can get its own limit, in seconds:

```toml
# schemathesis.toml
[[operations]]
include-name = "GET /reports"
checks.max_response_time = 30.0
```

`--exclude-checks max_response_time` turns it off.

### ST113 GraphQL server error {#st113}

Reported by [`not_a_server_error`](checks.md#not_a_server_error).

GraphQL `errors` entry reporting a field that failed on the server.

An `errors` entry that isn't a query validation error counts as a resolver failure. Return an expected outcome, such as a missing record, in `data`.

`--exclude-checks not_a_server_error` turns it off, along with ST101, ST102.

### ST114 Null in non-nullable GraphQL field {#st114}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

Null for a field the GraphQL schema declares non-nullable.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST111, ST115, ST116, ST117, ST118.

### ST115 Unexpected GraphQL response {#st115}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

GraphQL response that is not a JSON object, such as a list, a string or null.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST111, ST114, ST116, ST117, ST118.

### ST116 GraphQL data does not match the query {#st116}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

GraphQL `data` that does not match the query, such as a missing field or a value of the wrong type.

Checked only for successful responses without `errors`, whose `data` must be an object or `null`. Every requested field must be present under its alias, with no unrequested fields, a value of its declared type, and no `null` for a non-nullable field.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST111, ST114, ST115, ST117, ST118.

### ST117 Response does not decode as the declared message {#st117}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

gRPC response or status details that do not decode as the declared message.

A successful response must decode as the method's output message, and `grpc-status-details-bin` on an error must decode as `google.rpc.Status`. Regenerate the descriptor set if the server's protos changed.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST111, ST114, ST115, ST116, ST118.

### ST118 Response carries fields the schema does not declare {#st118}

Reported by [`response_schema_conformance`](checks.md#response_schema_conformance).

gRPC response with fields its message, as the server reflects it, does not declare.

Applies to schemas loaded through reflection only: the server sends fields its own reflected descriptors lack, so the reflection data is stale or the response is built from a different message.

`--exclude-checks response_schema_conformance` turns it off, along with ST109, ST110, ST111, ST114, ST115, ST116, ST117.

## Input handling (ST2xx)

### ST201 Schema-violating request accepted {#st201}

Reported by [`negative_data_rejection`](checks.md#negative_data_rejection).

Schema-violating request accepted.

`Invalid component:` in the failure names the broken constraint. For GraphQL, an invalid query must get an `errors` entry, whatever the status. A rejection with a status outside the list, such as `410` or `413`, can be allowed for the operation:

```toml
# schemathesis.toml
[[operations]]
include-name = "POST /orders"
checks.negative_data_rejection.expected-statuses = [
  "400", "401", "403", "404", "405", "406", "409", "415", "422", "428", "429", "5xx", "410",
]
```

`--exclude-checks negative_data_rejection` turns it off.

### ST202 Schema-valid request rejected {#st202}

Reported by [`positive_data_acceptance`](checks.md#positive_data_acceptance).

Schema-valid request rejected.

A `Hint:` line names undeclared properties the body carried. A rejection the schema can't express, such as an unknown id or a cross-field rule, can be allowed for the operation:

```toml
# schemathesis.toml
[[operations]]
include-name = "POST /orders"
checks.positive_data_acceptance.expected-statuses = [
  "2xx", "3xx", "401", "403", "404", "409", "429", "5xx", "422",
]
```

`--exclude-checks positive_data_acceptance` turns it off, along with ST206.

### ST203 Missing required header accepted {#st203}

Reported by [`missing_required_header`](checks.md#missing_required_header).

Request without a required header parameter accepted.

A missing `Authorization` must get `401`; the list below covers other headers. To make a header from `securitySchemes` optional, add `{}` to the operation's `security`. Another rejection status, such as `428` for `If-Match`, can be allowed for the operation:

```toml
# schemathesis.toml
[[operations]]
include-name = "POST /orders"
checks.missing_required_header.expected-statuses = ["400", "401", "403", "406", "415", "422", "428"]
```

`--exclude-checks missing_required_header` turns it off.

### ST204 Wrong response to undeclared method {#st204}

Reported by [`unsupported_method`](checks.md#unsupported_method).

Undeclared method answered with a status other than 405, or with 405 and no `Allow` header.

To stop probing methods the API answers differently on purpose, such as `501` for `QUERY`, narrow the list of methods Schemathesis sends:

```toml
# schemathesis.toml
[phases.coverage]
unexpected-methods = ["get", "put", "post", "delete", "options", "patch"]
```

`--exclude-checks unsupported_method` turns it off.

### ST205 Allow header mismatch {#st205}

Reported by [`allow_header_conformance`](checks.md#allow_header_conformance).

`Allow` in the `OPTIONS` response lists different methods than the schema declares for the path.

CORS middleware or a gateway that sends one `Allow` for every path causes this: fix it there, or remove `options` from `phases.coverage.unexpected-methods`.

`--exclude-checks allow_header_conformance` turns it off.

### ST206 GraphQL client error {#st206}

Reported by [`positive_data_acceptance`](checks.md#positive_data_acceptance).

Valid GraphQL query rejected by the server.

A `schemathesis.graphql.scalar` strategy producing values the server rejects shows up here too. A rule GraphQL can't express, such as `from` not exceeding `to`, can be skipped for that field:

```toml
# schemathesis.toml
[[operations]]
include-name = "Query.events"
checks.positive_data_acceptance.enabled = false
```

`--exclude-checks positive_data_acceptance` turns it off, along with ST202.

## Stateful behavior (ST3xx)

### ST301 Deleted resource still available {#st301}

Reported by [`use_after_free`](checks.md#use_after_free).

Resource still served after a successful `DELETE` removed it.

```text
POST    /items     201  {"id": 7}
DELETE  /items/7   204
GET     /items/7   200  {"id": 7}
```

A soft delete that keeps records readable should answer the deleted path with `404` or `410`.

`--exclude-checks use_after_free` turns it off.

### ST302 Created resource not found {#st302}

Reported by [`ensure_resource_availability`](checks.md#ensure_resource_availability).

404 for a resource that an earlier successful `POST` created.

```text
POST  /users        201  {"id": "u_42"}
GET   /users/u_42   404
```

Return the id in the format the path accepts, and read from the primary store right after a write. If the link passes the wrong field, such as `id` where the path expects a `slug`, declare the right link in the schema.

`--exclude-checks ensure_resource_availability` turns it off.

## Security (ST4xx)

### ST401 Authentication not enforced {#st401}

Reported by [`ignored_auth`](checks.md#ignored_auth).

Request with missing or invalid credentials accepted on an operation that requires authentication.

After a `2xx`, Schemathesis resends the request without credentials and with invalid ones. A public operation needs `security: []` in the schema.

`--exclude-checks ignored_auth` turns it off.

### ST402 Object-level authorization bypass {#st402}

Reported by [`object_level_authorization`](checks.md#object_level_authorization).

One user's object returned to another user who should not see it.

```text
POST /orders     as alice   201  {"id": 1, "item": "book"}
GET  /orders/1   as alice   200  {"id": 1, "item": "book"}
GET  /orders/1   as bob     200  {"id": 1, "item": "book"}
```

Check that the caller owns the object, and answer `403` or `404` otherwise. An object shared by design, such as a team resource, can be skipped for that operation:

```toml
# schemathesis.toml
[[operations]]
include-name = "GET /orders/{id}"
checks.object_level_authorization.enabled = false
```

`--exclude-checks object_level_authorization` turns it off.

## Custom checks (ST9xx)

### ST901 Custom check failed {#st901}

Reported by custom checks.

Response that breaks a rule a custom `@schemathesis.check` enforces.

The failure title names the check.

`--exclude-checks <name>` turns it off.
