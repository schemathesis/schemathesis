# Triaging Failures

A first run surfaces everything at once: schema gaps, setup you have not supplied yet, and deliberate probes. Most findings are real, but few teams fix all of them on day one. Decide what is in scope, then work through the rest.

## Stop the Destructive Requests

Do this before pointing Schemathesis at anything whose data you care about.

The [coverage phase](coverage.md) probes each documented path with the methods it does *not* declare, to confirm your API answers `405 Method Not Allowed`. A path documented only with `GET` also receives `DELETE`, `PATCH`, `PUT`, `POST`, `TRACE`, `OPTIONS` and `QUERY`.

!!! danger "Disabling the check does not stop the requests"

    [`unsupported_method`](../reference/checks.md#unsupported_method) only decides whether the *response* is reported. Turning it off leaves the requests in place, and if your framework routes undeclared methods to a real handler — via a catch-all route or a permissive router — a `DELETE` deletes.

    A path documented as `GET` only, with the check excluded, still delivers `TRACE`, `QUERY`, `PUT`, `POST`, `PATCH`, `OPTIONS` and `DELETE` to the handler — and the run reports `No issues found`.

Disable the probing itself:

```toml
[phases.coverage]
unexpected-methods = []
```

To keep the `405` coverage without the destructive verbs, list only the safe ones:

```toml
[phases.coverage]
unexpected-methods = ["OPTIONS", "TRACE"]
```

In CI against a shared environment, make this a precondition of the job rather than a convention.

## Narrow the First Run

Four settings account for most of the volume. Each one silences a real class of finding, so turn on only the ones you have decided not to act on yet.

**Timestamps without a timezone.** A field declared `format: date-time` comes back as `2026-09-06T10:19:14.561638` instead of `2026-09-06T10:19:14.561638Z`. RFC 3339 requires the offset, and stacks whose default timestamp type is timezone-naive omit it. A real violation, and usually not the one you are hunting today.

```toml
[checks]
response_schema_conformance.validate-formats = false
```

*Trades:* `format` stops being asserted everywhere — a malformed `uuid` or `email` passes too, and a `oneOf` that leans on `format` to pick a branch may resolve differently.

**Missing credentials.** When your schema declares a security scheme and you supply no token, Schemathesis generates values for it. Every protected operation answers `401`, surfacing as undocumented status codes and `ignored_auth` failures, and everything behind the auth wall stays untested. Supply real credentials, then stop generating them:

```toml
[generation]
with-security-parameters = false
```

*Trades:* authentication stops being exercised. Both halves matter — the flag alone, with no real credentials, just makes every operation fail on `401` faster. See [Authentication](auth.md).

**NULL bytes.** Generated strings can contain `\x00`, which many servers, proxies and database drivers reject outright.

```toml
[generation]
allow-x00 = false
```

*Trades:* you stop testing null-byte handling, which is a real robustness question for anything that stores or forwards the string. Governs generated string values only; the coverage phase's malformed-body probes are unaffected.

**Undeclared methods.** `unexpected-methods = []`, above. *Trades:* the `405` check, and [`allow_header_conformance`](../reference/checks.md#allow_header_conformance) with it, since that one reads the coverage phase's `OPTIONS` response.

Together, in a `schemathesis.toml` next to where you run the command:

```toml
[checks]
response_schema_conformance.validate-formats = false

[phases.coverage]
unexpected-methods = []

[generation]
with-security-parameters = false
allow-x00 = false
```

Expect the remaining failures to differ from the original set, not just shrink: `401`s mask everything behind them.

## Checks Send Their Own Requests

[`ignored_auth`](../reference/checks.md#ignored_auth) verifies that an operation declaring authentication enforces it, by re-sending each successful request twice — once with credentials stripped, once with invalid ones. Your application therefore sees three distinct `Authorization` values, which reads like the tool dropping your token and is regularly reported as one ([#2779](https://github.com/schemathesis/schemathesis/issues/2779)). If the extra requests trip a rate limiter or a login lockout, exclude the check:

```bash
uvx schemathesis run https://api.example.com/openapi.json --exclude-checks ignored_auth
```

Coverage probes are attributed to their documented operation, so in a JUnit report a case named `GET /records/{name}` can fail with `Unsupported method TRACE returned 200`. One operation is the parent of several requests.

## What's Left

| What you see | What it means |
|---|---|
| Undocumented `400` | The framework rejected a malformed body before your handler ran |
| Undocumented `404` | A generated path parameter matching no row; the schema documents only the happy path |
| `Undocumented Content-Type` | A streaming or file response whose media type is missing from the schema |

These are gaps between what your application does and what its schema says it does — usually a one-line annotation to fix.

## Undocumented Status Codes

These dominate most first runs. Look for patterns: if every `GET` returns an undocumented `404`, add it in one pass. For framework-generated schemas that is a response annotation in the source, not an edit to the schema file (for Python stacks, see [Testing Python Web Applications](python-apps.md)). To defer them, `--exclude-checks status_code_conformance`.

## Response Schema Conformance

The response shape does not match the declaration. Fix one area at a time rather than the whole API at once:

```bash
uvx schemathesis run https://api.example.com/openapi.json --include-path /users
```

Or `--include-tag users` if your schema uses tags.

## Server Errors

Fewest and most severe. Run the reproduction `curl`, read the body, trace the minimal failing input back to its schema definition — there is no batch fix. After a fix, confirm with [`st replay`](crash-reproduction.md) instead of re-running the suite.

## What's Next

Revisit the settings you turned off — each was suppressing a class of bug, and a clean run is the right moment to switch them back on one at a time. Then see [Optimizing for Maximum Bug Detection](config-optimization.md).
