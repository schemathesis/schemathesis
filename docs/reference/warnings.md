# Warnings

Schemathesis emits warnings when tests only cover error paths (HTTP 4xx) instead of hitting business logic, highlighting situations where your test configuration may need adjustment.

Warnings appear in your CLI output and don't stop test execution but indicate areas for improvement.

!!! note ""
    Percentages are computed for each individual operation after all scenarios finish, so warnings fire per-endpoint when its error rate crosses the threshold

| Warning | Signals | Quick fix |
| --- | --- | --- |
| `missing_auth` | Most interactions returned 401/403 | Provide valid credentials via `--auth`, custom headers, or config |
| `missing_test_data` | Generated parameters hit non-existent resources (404) | Seed known IDs / payloads in your config file |
| `validation_mismatch` | Schema constraints differ from real validation (lots of 4xx) | Tighten schema or extend generators to match runtime rules |
| `missing_deserializer` | Structured responses lack a registered deserializer | Register one via `@schemathesis.deserializer` or align `content` types with actual formats |
| `unused_openapi_auth` | Configured OpenAPI auth scheme doesn't exist in schema | Check scheme name matches `securitySchemes` (check for typos) |
| `method_not_allowed` | Operation consistently returned `405 Method Not Allowed` | Verify the server accepts this method, or remove the operation from the schema |
| `constants_extraction` | A registered `@schemathesis.python.constants` source could not be scanned | Return your app or importable modules from the source |

## Available Warnings

### `missing_auth`

```
Missing authentication: 1 operation returned authentication errors

401 Unauthorized (1 operation):
  - GET /basic

💡 Use --auth or -H to provide authentication credentials
```

**Trigger**: At least 90% of requests returned HTTP 401 or 403.

In this situation, most likely the credentials are missing or invalid/insufficient. Re-check if you provided proper auth.

### `missing_test_data`

```
Missing test data: 2 operations repeatedly returned 404 Not Found, preventing 
tests from reaching your API's core logic

  - GET /users/{user_id}
  - PATCH /users/{user_id}

💡 Provide realistic parameter values in your config file so tests can access 
existing resources
```

**Trigger**: At least 10% of requests returned HTTP 404

When API returns HTTP 404, it likely means that some resource was not found and it happens most often with purely generated data. To force Schemathesis to use known valid parameters, you can provide them via a config file:

```toml
[[operations]]
include-name = "GET /users/{user_id}"
parameters = { user_id = 42 }
```

### `validation_mismatch`

```
Schema validation mismatch: 1 operation mostly rejected generated data due 
to validation errors, indicating schema constraints don't match API validation

  - GET /test

💡 Check your schema constraints - API validation may be stricter 
than documented
```

**Trigger**: At least 10% of requests returned HTTP 4XX, excluding 401, 403, and 404

The tested API rejects a lot of data - while technically it is a valid behavior, it means that Schemathesis' tests don't reach deep into the API's business logic and cover mostly the validation layer.

As Schemathesis uses API schema to generate data, the most probable cause is that the schema is too rough and does not match the real API behavior, which leads to rejecting the generated data. 

To mitigate it, re-check the real validation rules and update your API schema so they match. Alternatively you can [extend](../guides/extending.md) Schemathesis so it generates data which is more likely to pass validation.

### `missing_deserializer`

```
Schema validation skipped: 1 operation cannot validate responses due to missing deserializers

  - GET /reports
    Cannot validate response 200: no deserializer registered for application/xml
```

!!! tip
    Register a deserializer with [@schemathesis.deserializer](../guides/custom-response-deserializers.md) to enable validation

**Trigger**: Operation responses declare structured schemas (objects / arrays) for a media type, but Schemathesis has no deserializer registered for that `content-type`.

When this warning appears, Schemathesis skips validation because it cannot deserialize the response body. Restore validation by:

- Registering a deserializer for the media type via `@schemathesis.deserializer()` (or `schemathesis.deserializer.register`) so the payload is converted into Python data.
- Updating the schema to advertise the actual media type (for example `application/json`) if the server already returns JSON.
- Omitting structured schemas for truly binary responses; without a schema, Schemathesis won't expect to validate those payloads.

### `unused_openapi_auth`

```
Unused OpenAPI auth: 1 configured auth scheme not used in the schema

  'ApiKeyHeadr' - Did you mean 'ApiKeyHeader'?
```

**Trigger**: Configured OpenAPI auth scheme is not defined in the schema's `securitySchemes`.

This warning appears when `[auth.openapi.<scheme>]` references a scheme that doesn't exist in your OpenAPI spec. Verify the scheme name matches your schema's `securitySchemes` exactly - Schemathesis will suggest corrections for likely typos.

See the [Authentication Guide](../guides/auth.md#openapi-aware-authentication) for details.

### `method_not_allowed`

```
Method Not Allowed: 1 operation consistently returned `405 Method Not Allowed` — skipped from later phases

  - POST /missing

💡 Verify the server actually accepts these methods, or remove them from the schema if unsupported
```

**Trigger**: An operation produced a streak of `405 Method Not Allowed` responses with no other status codes, and the schema does not declare `405` (or a `4XX`/`default` family covering it) as a documented response.

Schemathesis stops scheduling the operation in subsequent phases to free budget for operations that can actually be tested. The streak length required is small — a single non-405 response anywhere cancels the streak, so this fires only when an operation never returns anything else.

Common causes: a typo in the path, a method declared in the schema that the server doesn't implement, or an environment-specific routing layer that 405s for the configured base URL. If `405` is a legitimate documented response for the operation, list it under `responses:` in the schema and the warning will not fire.

### `constants_extraction`

```
Constant reuse skipped: 1 registered source could not be scanned

  - `my_constants` resolved to no modules to scan

💡 Check that each @schemathesis.python.constants source returns your app or modules
```

**Trigger**: A source registered with `@schemathesis.python.constants` either raised while running or resolved to nothing importable, so no literals could be harvested from it.

This fires only for explicitly registered sources — automatic extraction from an app loaded via `from_asgi`/`from_wsgi` stays silent when it simply finds no reusable values. Common causes: the source raises at call time, or names a module that fails to import. A source that resolves to real modules with no reusable literals is not reported.

## Configuring Warnings

By default, all warnings are enabled. You can disable them entirely or enable only a subset via the CLI or your config file:

=== "CLI"

    ```bash
    # Disable all warnings
    schemathesis run ... --warnings=off
    # Emit only `validation_mismatch`
    schemathesis run ... --warnings=validation_mismatch
    ```

=== "Config File"

    ```toml
    # Disable all warnings
    warnings = false
    ```

    ```toml
    # Emit only `validation_mismatch`
    warnings = ["validation_mismatch"]
    ```

### Advanced Configuration

For more control, use the object format to display warnings while making specific ones cause test failure:

```toml
[warnings]
# Control which warnings to display
display = ["missing_auth", "missing_test_data", "validation_mismatch"]
# Make specific warnings fail the test suite (exit code 1)
fail-on = ["validation_mismatch"]
```

Set `fail-on = true` to fail on all displayed warnings:

```toml
[warnings]
fail-on = true  # Fail on any warning
```

When `fail-on` is configured, Schemathesis will exit with code 1 if any of the specified warnings are encountered, even if all checks pass. This is useful for CI/CD pipelines that should fail when configuration or test data issues are detected.

See [Configuration Reference](configuration.md#warnings) for complete details.
