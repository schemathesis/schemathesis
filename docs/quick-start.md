# Quick Start Guide

**Estimated time: 5 minutes**

Schemathesis reads your OpenAPI or GraphQL schema, generates requests from it, and checks your API's responses. It catches edge cases that manual testing typically misses.

During a run Schemathesis:

- Generates inputs for each operation in your schema: schema examples, boundary values, and randomized data
- Runs checks such as server error detection, status-code conformance, and response schema validation
- Prints a `curl` command that reproduces each failure

## Try the demo

Test a deliberately buggy demo API using [uv](https://docs.astral.sh/uv/){target=_blank}:

```bash
uvx schemathesis run https://example.schemathesis.io/openapi.json
```

`uvx` is from the uv package manager and runs Schemathesis in an isolated environment without a permanent install. Replace `uvx schemathesis` with `schemathesis` or `st` if you have it installed directly.

The run takes about 20 seconds and ends with a list of failures and a summary:

```
...
______________ POST /internal-server-errors/exceeding-column-size ______________
1. Test Case ID: aN3Qa9

- Server error

[500] Internal Server Error:

    `{"success":false,"error":"argument of type 'NoneType' is not iterable"}`

Reproduce with:

    curl -X POST https://example.schemathesis.io/internal-server-errors/exceeding-column-size

    st replay aN3Qa9
...
Failures:
  ❌ API accepts requests without authentication: 1
  ❌ Server error: 2
  ❌ Response violates schema: 1
  ❌ API accepted schema-violating request: 3
  ❌ JSON deserialization error: 1
  ❌ Missing header not rejected: 1
  ❌ Undocumented Content-Type: 4
  ❌ Undocumented HTTP status code: 1
  ❌ Unsupported methods: 6

Test cases:
  59 generated, 18 found 20 unique failures

Seed: 273290938820225153183678967002087596304

============================ 20 failures in 18.78s =============================
```

Each failure names the broken check, shows the response, and gives a `curl` command to reproduce it. The test case ID lets you re-send the exact request later with `uvx schemathesis replay <ID>`. Failures and counts vary from run to run.

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
    Install Schemathesis and `pytest` into your project:

    ```console
    uv add --dev schemathesis pytest
    ```

    Or `pip install schemathesis pytest` in your virtual environment. Then create `test_api.py`:

    ```python
    import schemathesis

    schema = schemathesis.openapi.from_url("https://your-api.com/openapi.json")


    @schema.parametrize()
    def test_api(case):
        # Automatically calls your API and validates the response
        case.call_and_validate()
    ```

    Run it with `pytest test_api.py`. The [Pytest Tutorial](tutorials/pytest.md) walks through a complete example.

The CLI output has the same shape as the demo: a `FAILURES` section, then a `SUMMARY`. A run that finds nothing ends with `No issues found`. If your first run reports many failures, [Triaging Failures](guides/triage.md) shows how to work through them.

## What's next?

- **[CLI Tutorial](tutorials/cli.md)** - 20 minutes: find, diagnose, and fix a bug in a realistic booking API
- **[Pytest Tutorial](tutorials/pytest.md)** - 15 minutes: the same API, tested from a `pytest` suite

Runs scale with schema size and server performance. For thorough release testing, see [Optimizing for Maximum Bug Detection](guides/config-optimization.md) to run longer, higher-coverage sessions.

More guides and the full references:

- **[Testing multi-step workflows](guides/stateful-testing.md)** - when a bug only appears across a sequence like create -> fetch -> delete
- **[CI/CD Integration](guides/cicd.md)** - export results as JUnit XML, HAR, or VCR cassettes
- **[Using Schemathesis with Docker](guides/docker.md)** - run without installing Python
- **[CLI Reference](reference/cli.md)** - full list of options and checks
- **[Configuration Reference](reference/configuration.md)** - how to keep settings in `schemathesis.toml`
