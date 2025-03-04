# Schemathesis CLI

This guide demonstrates practical usage examples of the Schemathesis command-line interface, progressing from basic commands to advanced testing scenarios.

## First Test Run

Run default tests against an API schema:

```console
$ st run https://example.schemathesis.io/openapi.json
```

This command:

- Loads the API schema from the specified URL.
- Generates diverse test cases across multiple [test phases](#test-phases).
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

    For common issues with schema loading, authentication, or network connections, see the [Troubleshooting Guide](../troubleshooting.md).

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

This example tests only POST and PUT operations that don't have the "admin" tag.

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

## Authentication and Headers

Schemathesis supports various authentication methods: token-based headers, HTTP Basic Auth, and advanced multi-step flows. Authentication settings apply to all API requests made during testing.

### Token-based Authentication

#### API Keys

To provide an API key via header:

```console
$ st run openapi.yaml --header "X-API-Key: your-api-key-here"
```

!!! tip ""

    Store tokens in environment variables to avoid exposing them in command history:

    ```console
    $ st run openapi.yaml --header "X-API-Key: ${API_KEY}"
    ```

#### Bearer Tokens

For Bearer token authentication:

```console
$ st run openapi.yaml --header "Authorization: Bearer your-token-here"
```

!!! note ""

    Specify multiple headers by repeating the `--header` option:

    ```console
    $ st run openapi.yaml \
      --header "Authorization: Bearer ${TOKEN}" \
      --header "X-Tenant-ID: ${TENANT_ID}"
    ```

### Basic Authentication

For HTTP Basic Auth, use `--auth`:

```console
$ st run openapi.yaml --auth username:password
```

Schemathesis automatically encodes credentials in the `Authorization` header.

### Using Configuration Files

Configuration files allow you to set default authentication or override it for specific API operations. This flexible approach supports all authentication types, including OpenAPI security schemes, which are automatically applied when required by the API.

By default, Schemathesis automatically loads a `schemathesis.toml` file from the current directory or project root. To use a custom configuration file, specify its path with the `--config-file` option:

```console
$ st --config-file config.toml run openapi.yaml
```

For more details, see the [Authentication Configuration Reference](../reference/configuration.md#authentication) section. For example:

```toml
[auth]
bearer = "${TOKEN}"

[auth.openapi]
ApiKeyAuth = { value = "${API_KEY}" }
```

### Advanced Authentication

For complex or multi-step authentication flows that require custom logic, please refer to the [Extensions Guide](../extending/auth.md).


!!! tip "Third-Party Authentication"

    Python extensions allow you to use third-party libraries for specialized protocols. For example, you can use `requests_ntlm` for NTLM authentication

## Test Phases

Schemathesis divides testing into distinct phases—each designed to detect specific API issues.

### Available Phases

Schemathesis supports four test phases:

- **Examples**: Uses schema-defined examples for quick verification.  
- **Coverage**: Systematically tests boundary values, constraints, and edge cases.  
- **Fuzzing**: Uses randomized data to uncover unexpected edge cases.  
- **Stateful**: Tests sequences of API calls to assess stateful behavior.

By default, all phases are enabled.

!!! warning ""

    Note: The stateful phase can significantly increase test duration.

### Selecting Phases

Use the `--phases` option with a comma-separated list to specify which phases to run:

```console
$ st run openapi.yaml --phases examples,fuzzing
```

To run a single phase:

```console
$ st run openapi.yaml --phases coverage
```

!!! tip ""

    For more information about test phases, including how they work and when to use them, see the [Test Phases](../core-concepts.md#test-phases) section.

## Data Generation

Schemathesis generates test data for API operations based on your schema. These options let you control how test data is created.

### Test Data Modes

The `--mode` option determines whether Schemathesis generates valid data, invalid data, or both:

```console
$ st run openapi.yaml --mode positive
```

Available modes:

- `positive`: Generate only valid data that should be accepted by the API
- `negative`: Generate data that violates schema constraints to test error handling (slower generation)
- `all`: Generate both valid and invalid data (default)

!!! example ""

    In negative mode, if your schema has a constraint like `minimum: 1`, Schemathesis might generate values like `0` or `-5` to test how your API handles invalid inputs.

!!! tip ""

    Use `--mode positive` during initial API development to focus on core functionality before testing error handling.

### Number of Examples

The `--max-examples` option controls the maximum number of test cases:

```console
$ st run openapi.yaml --max-examples 50
```

By default, Schemathesis generates up to 100 test cases per operation.

!!! note ""

    - In unit testing phases (examples, coverage, fuzzing): Limits the number of test cases per operation
    - In stateful testing: Controls the number of API calls in a single sequence
    - Testing may finish earlier if Schemathesis finds a failure or exhausts all possible inputs

!!! example ""

    For a parameter with constraints like `minimum: 1, maximum: 10`, Schemathesis might generate fewer than your requested examples if it exhausts all meaningful test cases.


!!! tip ""

    - Lower values (10-50) provide faster feedback during development
    - Higher values (100+) find more edge cases but take longer to execute
    - Use `--continue-on-failure` to test all examples even after finding failures

### Reproducibility

Use the `--seed` option to make test data generation reproducible in the same environment:

```console
$ st run openapi.yaml --seed 42
```

With a fixed seed, Schemathesis generates the same sequence of test data within the same environment, which helps:

- Reproduce reported issues: "Test fails with seed 12345"
- Create predictable test runs in CI/CD environments

!!! warning ""

    Using the same seed only guarantees identical test data when other factors remain constant - including schema definition, API behavior, Schemathesis version, and Python version.

!!! tip ""

    For additional data generation options, see the [Data Generation Reference](../reference/configuration.md#data-generation).

## Checks

Checks are validations Schemathesis performs on API responses to verify correct behavior according to specifications.

!!! note ""

    All checks are enabled by default. Customize them to focus on schema compliance, server crashes or stateful issues as needed.

### Selecting Checks

Customize the test run by specifying the checks to include using the `--checks` option:

```console
$ st run openapi.yaml --checks not_a_server_error,response_schema_conformance
```

Disable specific checks while retaining others by using the `--exclude-checks` option:

```console
$ st run openapi.yaml --exclude-checks negative_data_rejection
```

!!! tip ""

    Use `--checks` to run only the listed checks, or `--exclude-checks` to run all checks except the ones you specify.

### Response Time Validation

Use the `--max-response-time` option to ensure API responses are received within a specified time frame:

```console
$ st run openapi.yaml --max-response-time 0.5
```
In this example, tests will fail for any API response that takes longer than 500 milliseconds, helping you identify slow endpoints.

### Common Check Combinations

Different testing goals require different check combinations. Here's a common scenario:

#### Schema Compliance Testing

For schema compliance testing, run only the checks that validate response status codes, content types, and schema structures:

```console
$ st run openapi.yaml --checks \
    status_code_conformance,content_type_conformance,response_schema_conformance
```

This combination ensures responses use the expected status codes, content types, and schema structures without testing additional behavior like authentication bypass.

!!! tip ""

    For more information about checks, including what they validate and when to use them, see the [Checks Reference](../reference/checks.md).

## Reporting Test Results

Schemathesis can generate structured reports of test results for integration with CI systems, sharing findings with your team, and analyzing API behavior.

### Report Types and Use Cases

Schemathesis supports three report formats, each serving different purposes:

- **JUnit XML**: Integration with CI systems like Jenkins or GitLab CI. Provides structured test results that can be visualized in test reporting dashboards.

- **VCR Cassettes**: Debugging API issues by preserving complete request and response details. Includes metadata like test case IDs and check results in YAML format.

- **HAR Files**: Analyzing HTTP traffic with browser developer tools or third-party applications. Provides a standard format compatible with HTTP analyzers.

### Generating and Storing Reports

By default, Schemathesis doesn't generate any reports. Use the `--report` option with a comma-separated list of formats:

```console
$ st run openapi.yaml --report junit,vcr
```

Reports are stored in the `schemathesis-report` directory by default. You can change this with `--report-dir`:

```console
$ st run openapi.yaml --report junit --report-dir ./test-results
```

!!! note ""

    Files in the report directory are overwritten on each run. Use unique directories or filenames for tests you want to preserve.

For specific report types, you can customize the output path:

```console
$ st run openapi.yaml --report-junit-path ./jenkins/schemathesis-results.xml
```

Similar options exist for other formats:

```console
$ st run openapi.yaml --report-vcr-path ./debug/api-responses.yaml
$ st run openapi.yaml --report-har-path ./analysis/http-archive.har
```

!!! note "Enable reports"

    Passing `--report-{format}-path` option automatically enables reporting in the given format. For detailed information about report structures and advanced usage, see the [Reporting Reference](../reference/reporting.md).


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

## Next Steps

Should you wish to go beyond the discussed CLI usage you can check the following:

- See the [CLI Reference](../reference/cli.md) for a complete list of all available command-line options

- Check [Extension Mechanisms](../extending/overview.md) to implement custom checks, hooks, or data generators when standard functionality doesn't meet your requirements
