# Quick Start Guide

**Estimated time: 5 minutes**

Schemathesis automatically finds bugs in your APIs by generating thousands of test cases from your OpenAPI or GraphQL schema. It catches edge cases that manual testing typically misses.

During the quick-start run Schemathesis:

- Generates property-based inputs for each operation defined in your schema
- Runs core checks such as status-code conformance, response validation, and server error detection
- Reports reproduction-ready commands for any failing cases

## Try the demo

Test a sample API using [uv](https://docs.astral.sh/uv/){target=_blank}:

```bash
uvx schemathesis run https://example.schemathesis.io/openapi.json
```

`uvx` is from the uv package manager and runs Schemathesis in an isolated environment without a permanent install. Replace `uvx schemathesis` with `schemathesis` or `st` if you have it installed directly.

Example output:

```
_____________________ POST /improper-input-type-handling _____________________

- Server error

[500] Internal Server Error:

    `{"success":false,"error":"invalid literal for int() with base 10: '\\n'"}`

Reproduce with:

    curl -X POST -H 'Content-Type: application/json' \
      -d '{"number": "\n\udbcd." }' \
      https://example.schemathesis.io/improper-input-type-handling
```

## Test your own API

=== "With authentication"
    ```bash
    uvx schemathesis run https://your-api.com/openapi.json \
      --header 'Authorization: Bearer your-token'
    ```

    For token refresh, per-operation auth, or dynamic credentials, see [Authentication](guides/auth.md).

=== "Local development"
    ```bash
    uvx schemathesis run ./openapi.yaml --url http://localhost:8000
    ```

=== "pytest integration"
    ```python
    import schemathesis
    
    schema = schemathesis.openapi.from_url("https://your-api.com/openapi.json")
    
    @schema.parametrize()
    def test_api(case):
        # Automatically calls your API and validates the response
        case.call_and_validate()
    ```

## What's next?

!!! tip "Ready to dive deeper?"
    **[Complete Tutorial](tutorials/cli.md)** – a 15–20 minute workflow against a realistic booking API

Runs scale with schema size and server performance. For thorough release testing, see [Optimizing for Maximum Bug Detection](guides/config-optimization.md) to run longer, higher-coverage sessions.

**Reference guides:**

- **[Triaging Failures](guides/triage.md)** – systematic workflow when your first run produces many failures
- **[CI/CD Integration](guides/cicd.md)** – export results as JUnit XML, HAR, or VCR cassettes
- **[Using Schemathesis with Docker](guides/docker.md)** — run without installing Python
- **[CLI Reference](reference/cli.md)** – full list of options and checks
- **[Configuration Reference](reference/configuration.md)** – how to keep settings in `schemathesis.toml`
