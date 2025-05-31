# Warnings

Schemathesis emits warnings when tests only cover error paths (HTTP 4xx) instead of hitting business logic, highlighting situations where your test configuration may need adjustment.

Warnings appear in your CLI output and don't stop test execution but indicate areas for improvement.

!!! note ""
    Percentages are computed for each individual operation after all scenarios finish, so warnings fire per-endpoint when its error rate crosses the threshold

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
    # Emit only `validation_mismatch`
    warnings = ["validation_mismatch"]
    ```
