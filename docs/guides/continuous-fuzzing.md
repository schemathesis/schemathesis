# Continuous Fuzzing

`st fuzz` runs continuous, unbounded fuzzing against your API. Unlike `st run`, which executes a finite test suite and stops, `st fuzz` loops indefinitely — generating new test cases, replaying previously discovered inputs, and checking responses on every pass.

The CLI reference is the canonical source for all flags and option semantics:
[CLI Reference: `st fuzz`](../reference/cli.md#fuzz).

Use it for:

- Long-running overnight fuzzing campaigns
- Background fuzzing against a staging environment
- Scheduled security testing in CI with a bounded duration

## Quick start

```console
$ st fuzz https://api.example.com/openapi.json
```

Schemathesis loads the schema, then enters a fuzzing loop over all operations. Press **Ctrl+C** to stop cleanly.

## Bounding the run with `--max-time`

Set `--max-time` to stop automatically after a fixed number of seconds:

```console
$ st fuzz https://api.example.com/openapi.json --max-time 3600
```

When the time limit is reached, Schemathesis finishes the current fuzzing run, prints a stop reason, and reports the final summary.
For exact exit-code behavior, see [CLI Reference: Exit codes](../reference/cli.md#exit-codes).

## Parallel workers

Use `--workers` to run multiple fuzzing workers in parallel:

```console
$ st fuzz https://api.example.com/openapi.json --workers 4
```

All workers share a single corpus on disk. Interesting inputs found by one worker are replayed by the others on their next run, so the whole pool benefits from every discovery.

## Selecting checks

By default, all enabled checks run. Focus on specific ones with `-c`:

```console
$ st fuzz https://api.example.com/openapi.json -c response_schema_conformance,not_a_server_error
```

Or exclude checks you don't need:

```console
$ st fuzz https://api.example.com/openapi.json --exclude-checks ignored_auth
```

For failure-handling flags such as `--max-failures` and `--continue-on-failure`, see
[CLI Reference: `st fuzz`](../reference/cli.md#fuzz).

## Response-based input reuse

By default, `st fuzz` captures values from successful API responses and feeds them back as inputs for related operations. For example, if `POST /items` returns `{"id": "abc123"}`, subsequent requests to `GET /items/{item_id}` will try `"abc123"` as the path parameter — reaching code paths that only trigger with valid, existing IDs.

This is controlled by the `extra_data_sources.responses` setting and is enabled by default. To disable it, set `extra_data_sources.responses = false` in your [configuration file](../reference/configuration.md).

## Filtering operations

All filtering options from `st run` are supported:

```console
$ st fuzz https://api.example.com/openapi.json --include-path /api/v2 --exclude-method DELETE
```

See [Filtering options](../reference/cli.md#filtering-options) in the CLI reference for the full list.

## Reports and machine-readable output

`st fuzz` supports the same reporting options as `st run`, including NDJSON event streams:

```console
$ st fuzz https://api.example.com/openapi.json \
  --max-time 300 \
  --report=ndjson \
  --report-ndjson-path=./fuzz-events.ndjson
```

This is useful for archiving campaign data and post-processing failures in CI.
For all report and output flags, see [CLI Reference: `st fuzz`](../reference/cli.md#fuzz).

## Using in CI

Combine `--max-time` and `--workers` for scheduled fuzzing jobs:

=== "GitHub Actions"

    ```yaml
    - name: Fuzz API
      run: |
        st fuzz ${{ env.API_URL }}/openapi.json \
          --max-time 300 \
          --workers 4 \
          -c response_schema_conformance,not_a_server_error
    ```

=== "GitLab CI"

    ```yaml
    fuzz:
      script:
        - st fuzz $API_URL/openapi.json --max-time 300 --workers 4
      allow_failure: false
    ```

## How `st fuzz` differs from `st run`

| | `st run` | `st fuzz` |
|---|---|---|
| Stops | After all phases complete | On Ctrl+C or `--max-time` |
| Test phases | Examples -> Coverage -> Fuzzing -> Stateful | Fuzzing only |
| Use case | CI/CD gating on every commit | Scheduled campaigns, security testing |
| Workers | Share work across phases | Each worker runs the full operation set |
