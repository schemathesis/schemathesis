# CI/CD Integration Guide

Run Schemathesis in CI to verify your API against its schema on every change.

## Prerequisites

- A CI job that can start your API, for example with `docker compose up -d` or a service container
- The API's schema, served by the API or committed to the repository
- Credentials stored as CI secrets, if the API requires authentication

## Schema Access

=== "Live Schema"
    ```bash
    uvx schemathesis run http://api-host:port/openapi.json --wait-for-schema 30
    ```
    Test against the schema served by your running API. The `--wait-for-schema 30` waits up to 30 seconds for the API to become available.

=== "Static Schema"
    ```bash
    uvx schemathesis run ./openapi.json --url http://api-host:port
    ```
    Test using a schema file from your repository.

## GitHub Actions

The [Schemathesis GitHub Action](https://github.com/schemathesis/action) provides the simplest integration path.

```yaml
name: API Tests
on: [push, pull_request]

jobs:
  api-test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
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

The action posts a schema coverage summary as a pull request comment, which needs `pull-requests: write`; `contents: read` keeps `actions/checkout` working, since a `permissions` block sets every permission it does not list to `none`. The action waits 2 seconds for the schema by default; raise it with the `wait-for-schema` input if the API takes longer to start. See the [action's inputs](https://github.com/schemathesis/action/blob/v3/action.yml) for the rest.

The JUnit report is written to `schemathesis-report/junit-<timestamp>.xml` by default. The `upload-artifact` step above uploads the entire `schemathesis-report/` directory, which captures this file regardless of the timestamp in its name.

Allure reports are also supported — see [Allure Integration](allure.md).

## GitLab CI

Use the official Docker image for consistent environments. Store the token as a masked CI/CD variable named `API_TOKEN` (**Settings > CI/CD > Variables**, with **Mask variable** checked); GitLab exposes it to the job as `$API_TOKEN`.

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
uvx schemathesis run http://localhost:8080/openapi.json
```

## Exit Codes

Fail the CI job on a non-zero exit code:

| Exit code | Meaning |
|-----------|---------|
| `0` | All checks passed |
| `1` | At least one check failed or bug reported |
| `2` | Schemathesis could not run: config, schema or internal error, or nothing tested |
| `130` | Run interrupted (Ctrl+C) before it finished |

See the [CLI reference](../reference/cli.md#exit-codes) for the complete list of exit codes.

## Machine-Readable Results

A run that tests nothing exits `2`: the schema has no operations, no operation matched the filters, or every selected operation was skipped. The last line of the output names the cause. `--report json` writes the run's verdict as a single JSON document so a pipeline can tell a run that tested nothing from a configuration error:

```bash
uvx schemathesis run http://localhost:8080/openapi.json --report json
```

An excerpt from a run that found failures:

```json
{
  "exit_code": 1,
  "stop_reason": "completed",
  "operations": {"total": 4, "selected": 4, "tested": 4, "errored": 0, "skipped": 0, "skip_reasons": []},
  "test_cases": {"generated": 439, "with_failures": 3, "unique_failures": 5, "without_checks": 0},
  "phases": {
    "examples": {"status": "skip", "skip_reason": null},
    "coverage": {"status": "failure", "skip_reason": null},
    "fuzzing": {"status": "failure", "skip_reason": null},
    "stateful": {"status": "failure", "skip_reason": null}
  },
  "failures": [
    {
      "type": "ServerError",
      "title": "Server error",
      "severity": "critical",
      "count": 2,
      "operations": ["GET /items/{itemId}", "POST /orders"]
    },
    {
      "type": "UndefinedStatusCode",
      "title": "Undocumented HTTP status code",
      "severity": "medium",
      "count": 3,
      "operations": ["GET /items/{itemId}", "POST /items", "POST /orders"]
    }
  ],
  "errors": []
}
```

The full report also contains `schemathesis_version`, `command`, `seed`, `started_at`, `running_time`, `complete`, `baseline`, `filtered`, `valid_rates`, `auth`, and `warnings`, which lists every warning kind (`missing_auth`, `missing_test_data`, `unmatched_filter`, and others) with an empty list when that warning did not fire.

If the API already has failures you are not fixing yet, a [baseline](baseline.md) keeps CI red only for new ones.

A run that never finished has `complete: false` and a `stop_reason` of `interrupted` after Ctrl+C or `error` otherwise; a schema loading or internal error is listed in `errors`.

Gate on `operations.tested` to catch a run that graded nothing, and on `failures[].type` — the failure class name — to react to specific finding kinds. The report lands in `schemathesis-report/json-<timestamp>.json`; pass `--report-json-path` for a fixed name.

## Splitting a Run Across Jobs

When one run does not fit the pipeline's time budget, give each job a filter that selects part of the schema. Two jobs, one API, no overlap:

```bash
# Job 1
uvx schemathesis run http://localhost:8080/openapi.json \
    --include-path-regex '^/api/orders'

# Job 2
uvx schemathesis run http://localhost:8080/openapi.json \
    --exclude-path-regex '^/api/orders'
```

Each job reports the share it took, so you can check the groups add up:

```
     Operations:       2 selected / 4 total
```

Any filter can divide the schema - by tag, name, or operation ID as well as path. See [Filtering](../reference/cli.md#filtering) for the full set.

Every job writes its own report, and there is no merge step. Jobs on separate machines need no extra flags; jobs sharing one machine need a separate `--report-dir` each so they do not overwrite one another. Upload the directories as separate artifacts - CI systems that consume JUnit XML aggregate the files themselves.

### Split between resources, not through them

A job only knows about the operations it selected. Identifiers it never creates, it never sees - so a job that gets `GET /api/orders/{orderId}` without the operation that creates an order spends its budget on `404` responses and warns about it:

```
Missing test data: 1 operation repeatedly returned 404 Not Found, preventing tests from reaching your API's core logic

  - GET /api/orders/{orderId}

💡 Schemathesis found no operation that creates this data - create it outside the test run and supply the identifiers in your config file
```

Keep create-and-read chains in the same job, and draw the split along resource boundaries.

## Troubleshooting

**The job fails before any test runs because the schema is unreachable**: The API was not ready yet. Increase `--wait-for-schema` (the `wait-for-schema` input in the GitHub Action), or check the service's host name: inside GitLab services and Docker networks it is the service alias, not `localhost`.

**Every operation fails with `401`**: The secret did not reach the job. In GitHub Actions, secrets are not passed to workflows triggered from forks; in GitLab, a protected variable is only available on protected branches.

**The JUnit artifact is empty**: Pass `--report junit` or enable `[reports.junit]` in `schemathesis.toml`, and upload the whole `schemathesis-report/` directory, since the file name contains a timestamp.
