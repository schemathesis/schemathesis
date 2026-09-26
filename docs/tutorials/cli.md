# Schemathesis CLI Tutorial

**Estimated time: 20 minutes**

In this tutorial you run Schemathesis against a small booking API, reproduce the server error it finds, fix the bug, and confirm the fix. By the end you will have a `schemathesis.toml` that replaces the long command line, a JUnit report for your CI system, and a time-boxed fuzzing session.

New to Schemathesis? The [Quick Start](../quick-start.md) takes 5 minutes.

## Prerequisites

- **[Git](https://git-scm.com/downloads){target=_blank}** - to clone the example API
- **[Docker Compose](https://docs.docker.com/get-docker/){target=_blank}** - included in Docker Desktop
- **[uv](https://docs.astral.sh/uv/getting-started/installation/){target=_blank}** - runs Schemathesis with `uvx`, without a permanent install
- **[curl](https://curl.se/download.html){target=_blank}** (optional) - to reproduce failures by hand

Verify your setup:

```console
git --version
docker compose version
uv --version
```

--8<-- "docs/tutorials/_booking-api.md"

## First test run

Run Schemathesis against the API:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false
```

`--output-sanitize false` keeps the token visible in reproduction commands; by default Schemathesis masks it.

The run ends with the failures it found and a summary:

```
...
=================================== FAILURES ===================================
________________________________ POST /bookings ________________________________
1. Test Case ID: Iicuq5

- Server error

- Undocumented HTTP status code

    Received: 500
    Documented: 200, 400, 422

[500] Internal Server Error:

    `Internal Server Error`

Reproduce with:

    curl -X POST -H 'Authorization: Bearer secret-token' -H 'Content-Type: application/json' -d '{"guest_name": "00", "room_type": "", "nights": 1}' http://127.0.0.1:8080/bookings

    st replay Iicuq5

...
Failures:
  ❌ Server error: 1
  ❌ Undocumented HTTP status code: 1

...
Warnings:
  ⚠️ Missing valid test data: 1 operation repeatedly returned 404 responses
  ...

Test cases:
  318 generated, 1 found 2 unique failures, 3 skipped

Seed: 82422991785393504163937752414868088611
...
```

The test case ID, counts, and seed differ between runs; the `POST /bookings` failure does not.

The missing test data warning concerns `GET /bookings/{booking_id}`: random IDs almost never match an existing booking, so the operation answers `404` and its lookup logic is barely exercised. See [Warnings](../reference/warnings.md) for how to supply real IDs.

Run the `curl` command from the failure. The response body is just `Internal Server Error`: FastAPI does not send exception details to clients. The cause is in the server log:

```console
docker compose logs booking-api
```

```
...
booking-api-1  |   File "/app/app.py", line 46, in create_booking
booking-api-1  |     price_per_night = room_prices[booking.room_type]
booking-api-1  |                       ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
booking-api-1  | KeyError: ''
```

Schemathesis has already reduced the input to the simplest case that still fails: an empty `room_type`.

## Reporting

Export the results as a JUnit XML report that Jenkins, GitLab CI, and other test tools can import:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --report junit
```

This writes `schemathesis-report/junit-<timestamp>.xml`, for example `junit-20260925T111221Z.xml`, with one test case per API operation plus one for the stateful tests. Failed operations carry the failure message and the `curl` command to reproduce it. Pass `--report-junit-path junit.xml` for a fixed file name. See [CI/CD Integration](../guides/cicd.md) for ready-made pipeline examples.

Other formats include VCR cassettes (`--report vcr`) and HAR files (`--report har`) for inspecting the HTTP traffic.

--8<-- "docs/tutorials/_booking-fix.md"

## Confirming the fix

Schemathesis saves every failing case to disk. Replay the saved cases against the rebuilt API:

```bash
uvx schemathesis replay
```

```
Replaying 1 case from .schemathesis/default/cache/crashes

  + FIXED  POST /bookings

Removed 2 crash files (pass --keep to retain).

=============================== 2 fixed in 0.04s ===============================
```

Replay re-sends only the exact requests that failed. To test the fixed operation with fresh data, run it alone:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --include-operation-id create_booking_bookings_post
```

```
...
Test cases:
  152 generated, 152 passed

...
=========================== No issues found in 0.60s ===========================
```

FastAPI generates operation IDs like `create_booking_bookings_post` automatically; find them in the schema at `/openapi.json`. You can also filter by HTTP method (`--include-method POST`) or path (`--include-path /bookings`). See [Replaying Failures](../guides/crash-reproduction.md) for more on `replay`.

## Generating more test cases

The default run is quick and stops testing an operation at its first failure. For a more thorough pass over the whole API:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --max-examples 500 \
  --continue-on-failure
```

`--max-examples` caps the cases generated by the fuzzing phase per operation and the scenarios in the stateful phase; the examples and coverage phases add their own cases. `--continue-on-failure` keeps testing an operation after its first failure, so one run can report several distinct bugs in the same operation.

With the bug fixed, this run should find nothing:

```
...
Test Phases:
  ⏭  Examples
  ✅ Coverage
  ✅ Fuzzing
  ✅ Stateful

Test cases:
  5152 generated, 5152 passed, 14 skipped

...
========================== No issues found in 18.04s ===========================
```

A clean run is the result you want: several thousand requests, including multi-step scenarios in the stateful phase, produced no server errors or schema violations. The warnings from the first run are gone as well. For longer release-testing sessions, see [Optimizing for Maximum Bug Detection](../guides/config-optimization.md).

## Configuration file

Instead of repeating these options, save them in `schemathesis.toml` in the directory you run Schemathesis from:

```toml
headers = { Authorization = "Bearer ${API_TOKEN}" }
continue-on-failure = true

[output.sanitization]
enabled = false

[generation]
max-examples = 500

[reports.junit]
enabled = true
```

`${API_TOKEN}` is read from the environment, which keeps the token out of the file:

```bash
export API_TOKEN=secret-token
uvx schemathesis run http://127.0.0.1:8080/openapi.json
```

Schemathesis looks for `schemathesis.toml` in the current directory and its parents, up to the repository root. To use a file elsewhere:

```bash
uvx schemathesis --config-file path/to/config.toml run http://127.0.0.1:8080/openapi.json
```

Command-line options override the config file, so you can still adjust a setting for a single run.

## Continuous fuzzing

By default, `run` stops once each operation has its share of test cases. With `--max-time`, it keeps generating new ones until the time limit expires or you press Ctrl+C.

Run it from the same directory and shell as the previous step: the `Authorization` header and `continue-on-failure = true` come from `schemathesis.toml`, and the token from the exported `API_TOKEN`. In a new shell, export `API_TOKEN` again first.

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json --max-time 30
```

For details, see [Continuous Fuzzing](../guides/continuous-fuzzing.md).

## What's next?

You have found, diagnosed, and fixed a bug, confirmed the fix with `replay`, and moved your settings into `schemathesis.toml`.

- **[Pytest Tutorial](pytest.md)** - run the same checks from your `pytest` suite
- **[Triaging Failures](../guides/triage.md)** - when your own API reports many failures at once
- **[CI/CD Integration](../guides/cicd.md)** - run Schemathesis on every pull request
- **[Testing multi-step workflows](../guides/stateful-testing.md)** - when a bug only appears across a sequence like create -> fetch -> delete
- **[CLI Reference](../reference/cli.md)** - all CLI options
