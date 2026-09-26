# Continuous Fuzzing

This guide shows how to fuzz an API for a fixed time window, such as an overnight session or a scheduled fuzzing pipeline, with `st run --max-time`.

With `--max-time`, `st run` first runs the examples and coverage phases once, then repeats the fuzzing and stateful phases, generating new test cases on every pass, until the time is spent. Values captured from API responses feed later passes, so the session reaches deeper into the API as it goes.

## Prerequisites

- Schemathesis installed or available via `uvx`
- An API with an OpenAPI or GraphQL schema that you can send many requests to

## 1. Start a session

```bash
uvx schemathesis run https://example.schemathesis.io/openapi.json \
  --max-time 3600 \
  --continue-on-failure
```

`--max-time` bounds the session by the clock: 3600 seconds is one hour. `--continue-on-failure` keeps testing an operation after it fails, so the session reports every distinct failure it finds instead of setting that operation aside at its first one.

The session ends with a summary like this (a 20-second window against the [CLI tutorial](../tutorials/cli.md)'s booking API):

```
Failures:
  ❌ Server error: 1
  ❌ Undocumented HTTP status code: 1

...

Test cases:
  3011 generated, 1 found 2 unique failures, 36 skipped
```

Press Ctrl+C to end a session early; the summary still covers everything tested so far.

## 2. Save results

```bash
uvx schemathesis run https://example.schemathesis.io/openapi.json \
  --max-time 3600 \
  --continue-on-failure \
  --report junit
```

All report formats are supported: `junit`, `vcr`, `har`, `ndjson`, `json`, `allure`. Report files are written to `schemathesis-report/` in the current directory. Every failing case is also saved for [`st replay`](crash-reproduction.md).

## 3. Put the settings in a config file

```toml
max-time = 3600
continue-on-failure = true

[reports.junit]
enabled = true
```

With this `schemathesis.toml` in place, a session is `uvx schemathesis run <schema>`. See [Optimizing for Maximum Bug Detection](config-optimization.md) for optional settings that change what the session looks for.

## `st fuzz`

`st fuzz` runs only multi-step scenarios across operations, without the examples and coverage phases and without feeding captured values back during the session. `st run --max-time` covers the same scenarios through its stateful phase and tests more of the API in the same time.

## Troubleshooting

**The session ends long before `--max-time`.** Only the fuzzing and stateful phases repeat. If every operation is skipped or errors out, nothing is left to repeat; check the errors and per-phase results in the summary.
