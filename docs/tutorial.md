# Schemathesis Tutorial

This tutorial walks you through a complete API testing workflow with Schemathesis using a booking API. You'll see how property-based testing automatically finds bugs that manual testing typically misses.

You'll learn how to:

- Authenticate requests using an API token

- Focus testing on specific operations

- Interpret and reproduce failures

- Export results to JUnit

- Use Schemathesis configuration file

By the end of this tutorial, you'll understand how to integrate Schemathesis into your development workflow to catch crashes and schema violations before they reach production.

If you're new to Schemathesis, check the [Quick Start Guide](../quick-start.md) first.

!!! note ""

    This tutorial covers schemathesis CLI & pytest integration

## Prerequisites

 - [Docker Compose](https://docs.docker.com/get-docker/){target=_blank} - Install Docker Desktop which includes Docker Compose

 - [uv](https://docs.astral.sh/uv/getting-started/installation/){target=_blank} - Python package manager for installing and running Schemathesis

Verify your setup:

```console
docker compose version
uv --version
```

## API under test

We'll test an example booking API that handles hotel reservations - creating bookings, retrieving guest information, and managing room availability. This is a sample API designed to demonstrate common validation scenarios.

The API lives in the Schemathesis repository:

```console
git clone https://github.com/schemathesis/schemathesis.git
cd schemathesis/examples/basic
docker compose up -d
```
Open [http://localhost:8080/docs](http://localhost:8080/docs){target=_blank} in your browser to see the API documentation and available endpoints.

The API requires bearer token authentication. For testing purposes, use: `secret-token`

## Installation

=== "CLI"

    ```bash
    # Run Schemathesis directly without installation
    uvx schemathesis --version
    ```

=== "pytest"

    ```bash
    # Install in your Python environment
    uv pip install schemathesis
    ```

The CLI approach uses `uvx` to run Schemathesis on-demand, while the Python installation adds it to your current environment for use in test suites.

## First Test Run

Schemathesis requires only API schema to run tests

// TODO: uvx / pytest tabs - should use the url from docker compose. Ensure it includes api token
// + note that there are various auth options
```console
$ st run https://example.schemathesis.io/openapi.json
```

This command:

- Loads the API schema from the specified URL.
- Generates and runs test cases based on the API schema
- Automatically minimizes any failing test case


// TODO: cli / pytest output. Snip it so it focuses on the important parts
// TODO: Explain that schemathesis ran many check for each response + hint that stateful testing is also available
Example output:


```
Schemathesis dev
━━━━━━━━━━━━━━━━

 ✅  Loaded specification from https://schemathesis.io/openapi.json (in 0.32s)

     Base URL:         http://127.0.0.1/api
     Specification:    Open API 3.0.2
     Operations:       1 selected / 1 total

 ✅  API capabilities:

     Supports NULL byte in headers:    ✘

 ⏭   Examples (in 0.00s)

     ⏭  1 skipped

 ❌  Coverage (in 0.00s)

     ❌ 1 failed

 ❌  Fuzzing (in 0.00s)

     ❌ 1 failed

=================================== FAILURES ===================================
_________________________________ GET /success _________________________________
1. Test Case ID: <PLACEHOLDER>

- Response violates schema

    {} is not of type 'integer'

    Schema:

        {
            "type": "integer"
        }

    Value:

        {}

- Missing Content-Type header

    The following media types are documented in the schema:
    - `application/json`

[200] OK:

    `{}`

Reproduce with:

    curl -X GET http://127.0.0.1/api/success

=================================== SUMMARY ====================================

API Operations:
  Selected: 1/1
  Tested: 1

Test Phases:
  ✅ API probing
  ⏭  Examples
  ❌ Coverage
  ❌ Fuzzing
  ⏭  Stateful (not applicable)

Failures:
  ❌ Response violates schema: 1
  ❌ Missing Content-Type header: 1

Test cases:
  N generated, N found N unique failures

Seed: 42

============================= 2 failures in 1.00s ==============================
```

The output show two failed checks: the response body did not match the expected schema, and the required `Content-Type` header was missing. Both failures can be reproduced with the provided `curl` command:

// TODO: curl command

## Reporting

// TODO: explain available reporting options (show JUnit report that is imported into Allure)

## Fixing the bug

// TODO: Suggest a fix

## Re-running the tests

By default, Schemathesis tests all operations in your API schema. However, you can narrow down the testing scope to focus on specific parts of your API:

// TODO: Command with `--include-operation-id` for failing API operation
// Note that there are many ways to configure the testing scope

## Generating more test cases

// TODO: show `--max-examples` to generate more tests
// TODO: also show `--continue-on-failure` so schemathesis does not stop on first failure per API operations
// explain benefits

## Configuration File

// TODO: quick intro to the config file + store all the previous configs in it

Configuration files allow you to set default authentication or override it for specific API operations.

By default, Schemathesis automatically loads a `schemathesis.toml` file from the current directory or project root. To use a custom configuration file, specify its path with the `--config-file` option:

## What is next?

// TODO: Suggest what to do next

Should you wish to go beyond the discussed CLI usage you can check the following:

- See the [CLI Reference](../reference/cli.md) for a complete list of all available command-line options

- Check [Extension Mechanisms](../guides/extending.md) to implement custom checks, hooks, or data generators when standard functionality doesn't meet your requirements
