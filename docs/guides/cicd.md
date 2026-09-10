# CI/CD Integration Guide

Run Schemathesis in CI to verify your API against its schema on every change.

## Schema Access

=== "Live Schema"
    ```bash
    schemathesis run http://api-host:port/openapi.json --wait-for-schema 30
    ```
    Test against the schema served by your running API. The `--wait-for-schema 30` waits up to 30 seconds for the API to become available.

=== "Static Schema"
    ```bash
    schemathesis run ./openapi.json --url http://api-host:port
    ```
    Test using a schema file from your repository.

## GitHub Actions

The [Schemathesis GitHub Action](https://github.com/schemathesis/action) provides the simplest integration path.

**Basic workflow:**

```yaml
name: API Tests
on: [push, pull_request]

jobs:
  api-test:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - uses: actions/checkout@v6

      - name: Start services
        run: docker compose up -d

      - uses: schemathesis/action@v3
        with:
          schema: 'http://localhost:8080/openapi.json'
          authorization: 'Bearer ${{ secrets.API_TOKEN }}'
          args: '--report junit'

      - name: Upload test results
        uses: actions/upload-artifact@v7
        if: always()
        with:
          name: schemathesis-results
          path: schemathesis-report/

      - name: Cleanup
        if: always()
        run: docker compose down
```

The JUnit report is written to `schemathesis-report/junit-<timestamp>.xml` by default. The `upload-artifact` step above uploads the entire `schemathesis-report/` directory, which captures this file regardless of the timestamp in its name.

Allure reports are also supported — see [Allure Integration](allure.md).

## GitLab CI

Use the official Docker image for consistent environments.

**Complete workflow example:**
```yaml
stages:
  - test

api-tests:
  stage: test
  image: 
    name: schemathesis/schemathesis:stable
    entrypoint: [""]
  services:
    - name: your-api:latest
      alias: api
  variables:
    API_TOKEN: "your-secret-token"
  script:
    - >
      schemathesis run http://api:8080/openapi.json
      --header "Authorization: Bearer $API_TOKEN"
      --wait-for-schema 60
      --report junit
  artifacts:
    when: always
    reports:
      junit: schemathesis-report/junit-*.xml
    paths:
      - schemathesis-report/
```

## Using Configuration Files

Create `schemathesis.toml` to avoid repeating options and maintain consistent settings:

```toml
# Authentication
headers = { Authorization = "Bearer ${API_TOKEN}" }

# Continue testing after failures to find more issues
continue-on-failure = true

# Generate reports
[reports.junit]
enabled = true
```

Then run with just:

```bash
schemathesis run http://localhost:8080/openapi.json
```

| Exit code | Meaning |
|-----------|---------|
| `0` | All checks passed |
| `1` | At least one check failed or bug reported |
| `2` | Run aborted due to config or schema error |

See the [CLI reference](../reference/cli.md#exit-codes) for the complete list of exit codes.

## Machine-Readable Results

An exit code says whether something failed, not what ran — a run that selected zero operations exits `1` just like a run with real findings. `--report json` writes the run's verdict as a single JSON document so a pipeline can tell those apart:

```bash
schemathesis run http://localhost:8080/openapi.json --report json
```

```json
{
  "exit_code": 1,
  "stop_reason": "completed",
  "operations": { "total": 42, "selected": 40, "tested": 38, "errored": 1, "skipped": 1, "skip_reasons": [] },
  "test_cases": { "generated": 3800, "with_failures": 12, "unique_failures": 4, "without_checks": 0 },
  "phases": { "fuzzing": { "status": "success", "skip_reason": null } },
  "failures": [],
  "errors": [],
  "warnings": {}
}
```

Gate on `operations.tested` to catch a run that graded nothing, and on `failures[].type` — the failure class name — to react to specific finding kinds. The report lands in `schemathesis-report/json-<timestamp>.json`; pass `--report-json-path` for a fixed name.
