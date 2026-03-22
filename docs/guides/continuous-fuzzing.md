# Continuous Fuzzing

`st fuzz` generates and tests continuously until it finds a failure, you stop it, or a time limit is reached. Use it when you want to dedicate a time window to fuzzing rather than running a bounded test suite.

```bash
st fuzz https://example.schemathesis.io/openapi.json
```

Without `--max-time`, this runs until the first failure or Ctrl+C.

```bash
st fuzz https://example.schemathesis.io/openapi.json --max-time 3600
```

`--max-time` stops fuzzing after the specified number of seconds.

## Finding more failures per session

By default, `st fuzz` stops on the first failure. Add `--continue-on-failure` to keep testing past failures — useful for longer sessions where you want to surface as many distinct issues as possible in one run:

```bash
st fuzz https://example.schemathesis.io/openapi.json \
  --max-time 3600 \
  --continue-on-failure
```

## Saving results

```bash
st fuzz https://example.schemathesis.io/openapi.json \
  --max-time 3600 \
  --report junit
```

All report formats are supported: `junit`, `vcr`, `har`, `ndjson`, `allure`.

## When to use `st fuzz` vs `st run`

Use `st run` when you want a test run that exits on completion - CI checks, PR gates, scheduled regression runs.

Use `st fuzz` when you want fuzzing to keep going: overnight sessions, dedicated fuzzing pipelines, or any context where longer execution is likely to find more bugs.
