# Schemathesis CLI

This guide demonstrates practical usage examples of the Schemathesis command-line interface, progressing from basic commands to advanced testing scenarios.

## First Test Run

Run default tests against an API schema:

```console
$ st run https://example.schemathesis.io/openapi.json
```

This command:

- Loads the API schema from the specified URL.
- Generates diverse test cases across multiple test phases:
    - **examples:** Using schema-defined examples.
    - **coverage:** Using deterministic edge cases and boundary values.
    - **fuzzing:** Using randomly generated values.
    - **stateful:** Testing API operation sequences.
- Executes the test cases and runs a suite of checks against the API responses.
- Automatically minimizes any failing test case to help pinpoint the underlying issue.

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

The output reveals that the endpoint failed two checks: the response body did not match the expected schema, and the required `Content-Type` header was missing. A handy curl command is provided to reproduce the failure for debugging.

!!! note "Troubleshooting"

    For common issues with schema loading, authentication, or network connections, see the [Troubleshooting Guide](./troubleshooting.md).

### Testing with a Local Schema File

When testing with a local schema file, use the `--url` flag to specify the appropriate test server:
```console
$ st run ./openapi.json --url http://localhost:8000
```

## Changing the Test Scope

By default, Schemathesis tests all operations in your API schema. However, you can narrow down the testing scope to focus on specific parts of your API, which is particularly useful for:

- Testing only recently changed endpoints
- Focusing on critical paths during quick validation
- Excluding endpoints that are known to be problematic
- Running separate test suites for different API areas

### Basic Filtering

Filter operations by HTTP method:

```console
$ st run --include-method GET ...
```

Filter by path pattern using regular expressions:

```console
$ st run --include-path-regex '^/users' ...
```

### Filtering Options Reference

Schemathesis offers a comprehensive set of filtering options using this pattern:

```
--{include,exclude}-{path,method,name,tag,operation-id} TEXT
--{include,exclude}-{path,method,name,tag,operation-id}-regex TEXT
```

Examples:

```console
# Test only POST endpoints
$ st run --include-method POST ...

# Exclude all user-related endpoints
$ st run --exclude-path-regex '^/users' ...

# Test only endpoints with 'admin' tag
$ st run --include-tag admin ...

# Focus on specific operations by ID
$ st run --include-operation-id createUser --include-operation-id getUser ...
```

!!! note

    The `name` property in Schemathesis refers to the full operation name.
    For OpenAPI, it is formatted as `METHOD PATH` (e.g., `GET /users`).
    For GraphQL, it follows the pattern `OperationType.field` (e.g., `Query.getBookings`).

### Advanced Filtering

You can combine multiple filters to create precise test scopes:

```console
$ st run --include-method POST --include-method PUT --exclude-tag admin ...
```

This example tests only POST and PUT operations that don't have the "deprecated" tag.

### Filtering by Schema Properties

For more advanced scenarios, filter operations based on their schema definition:

```console
$ st run --include-by="/x-priority == 'high'" ...
```

This selects operations where the `x-priority` custom property equals "high". The expression follows this format:

```
"<pointer> <operator> <value>"
```

Where:

- `<pointer>` is a JSON Pointer to a value in the operation definition
- `<operator>` can be `==` or `!=`
- `<value>` is the value to compare against (treated as a string if not valid JSON)

### Excluding Deprecated Operations

To skip all operations marked as deprecated in the schema:

```console
$ st run --exclude-deprecated ...
```

!!! important "GraphQL Support"

    For GraphQL schemas, Schemathesis only supports filtration by the `name` property.

## :whale: Docker Usage

Schemathesis is available as a Docker image, allowing you to run API tests without installing the CLI directly on your system.

### Basic Docker Command

The simplest way to run Schemathesis via Docker is to use a remote schema URL:

```console
$ docker run schemathesis/schemathesis:stable \
    run http://api.example.com/openapi.json
```

!!! tip "Enabling Color Output"

    By default, Docker containers don't enable color output. Use the `--force-color` option if your terminal supports colors.


### Network Configuration by Platform

Network configuration varies by platform when testing local APIs:

#### 🐧 Linux

On Linux, use the `--network=host` parameter to access services running on your local machine:

```console
$ docker run --network=host schemathesis/schemathesis:stable \
    run http://localhost:8000/openapi.json
```

#### 🍎 macOS

On macOS, Docker cannot directly access the host's `localhost`. Use the special DNS name `host.docker.internal` instead:

```console
$ docker run schemathesis/schemathesis:stable \
    run http://host.docker.internal:8000/openapi.json
```

#### 🪟 Windows

On Windows, similar to macOS, use `host.docker.internal` to access services on your host machine:

```console
$ docker run schemathesis/schemathesis:stable \
   run http://host.docker.internal:8000/openapi.json
```

### Volume Mounting for Local Files

If your API schema is stored locally, you can mount it into the container:


For shells like bash, zsh, or sh:

```console
$ docker run -v $(pwd):/app schemathesis/schemathesis:stable \
    run /app/openapi.json
```

In these examples, the current working directory is mounted to `/app` inside the container, making local files accessible to Schemathesis.

When using volume mounting, you can also output reports to the local filesystem:

```console
$ docker run -v $(pwd):/app schemathesis/schemathesis:stable \
    run /app/openapi.json \
    --report junit --report-dir /app/test-results
```

This command will generate the JUnit report in the `test-results` directory on your local machine.
