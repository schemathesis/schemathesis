# Schemathesis CLI Reference

This reference details all the command-line options available in the Schemathesis CLI. The documentation is organized by command and subcommand to provide a clear structure of available options and their usage.

## Global Options

These options apply to all Schemathesis commands:

#### `--no-color`

!!! note ""

    Disables ANSI color output in the terminal. Use this option when running in environments where color codes may cause issues or when redirecting output to files.

#### `--force-color`

!!! note ""

    Forces the use of ANSI color escape codes in terminal output, even in environments where Schemathesis would normally disable colors.

## `run`

The `run` command executes API tests against an API using a schema for test generation.

```console
$ st run [OPTIONS] SCHEMA
```

!!! note "Required Parameter"

    **SCHEMA**: Path to an OpenAPI (`.json`, `.yml`) or GraphQL SDL file, or a URL pointing to such specifications. This schema defines the API surface to test.

### Basic Options

#### `-u, --url URL`

!!! note ""

    **Type**: `String`  
    **Default**: `null`  

    Specifies the base URL for the API under test. Required for file-based schemas.

    ```console
    $ st run openapi.yaml --url https://api.example.com
    ```

#### `-w, --workers VALUE`

!!! note ""

    **Type**: `Integer or "auto"`  
    **Default**: `1`  
    **Range**: `1-64` or `auto`  

    Specifies the number of concurrent workers for running unit test phases (examples, coverage, fuzzing). Use "auto" to automatically adjust based on available CPU cores.

    ```console
    $ st run openapi.yaml --workers 4
    ```

#### `--phases PHASES`

!!! note ""

    **Type**: `Comma-separated list`  
    **Default**: All phases enabled  
    **Possible values**: `examples`, `coverage`, `fuzzing`, `stateful`  

    Specifies which test phases to run.

    ```console
    $ st run openapi.yaml --phases examples,fuzzing
    ```

#### `--suppress-health-check CHECKS`

!!! note ""

    **Type**: `Comma-separated list`  
    **Default**: `[]`  
    **Possible values**: `data_too_large`, `filter_too_much`, `too_slow`, `large_base_example`, `all`  

    Disables specified health checks during test execution. Health checks identify potential problems with test generation or performance and may stop tests early with an error to prevent Schemathesis from hanging (e.g., when processing extremely complex schemas).

    ```console
    $ st run openapi.yaml --suppress-health-check too_slow,data_too_large
    ```

#### `--wait-for-schema SECONDS`

!!! note ""

    **Type**: `Float`  
    **Default**: `null`  
    **Range**: `≥1.0`  

    Maximum time in seconds to wait for the API schema to become available. Useful when testing services that take time to start up.

    ```console
    $ st run https://api.example.com/openapi.json --wait-for-schema 5.0
    ```

#### `--warnings WARNINGS`

!!! note ""

    **Type**: `String or comma-separated list`  
    **Default**: All warnings enabled  
    **Possible values**: `off`, `missing_auth`, `missing_test_data`, `validation_mismatch`  

    Control which warnings are displayed during test execution. Warnings help identify test configuration issues but don't stop execution.

    ```console
    # Disable all warnings
    $ st run openapi.yaml --warnings off
    
    # Enable only authentication warnings
    $ st run openapi.yaml --warnings missing_auth
    
    # Enable multiple specific warnings
    $ st run openapi.yaml --warnings missing_auth,validation_mismatch
    ```

### Validation

#### `-c, --checks CHECKS`

!!! note ""

    **Type**: `Comma-separated list`  
    **Default**: All checks enabled  
    **Possible values**: `not_a_server_error`, `status_code_conformance`, `content_type_conformance`, `response_headers_conformance`, `response_schema_conformance`, `negative_data_rejection`, `positive_data_acceptance`, `use_after_free`, `ensure_resource_availability`, `ignored_auth`, `all`  

    Specifies which checks to run against API responses.

    ```console
    $ st run openapi.yaml --checks not_a_server_error,response_schema_conformance
    ```

#### `--exclude-checks CHECKS`

!!! note ""

    **Type**: `Comma-separated list`  
    **Default**: `[]`  
    **Possible values**: `not_a_server_error`, `status_code_conformance`, `content_type_conformance`, `response_headers_conformance`, `response_schema_conformance`, `negative_data_rejection`, `positive_data_acceptance`, `use_after_free`, `ensure_resource_availability`, `ignored_auth`, `all`  

    Specifies which checks to skip during testing.

    ```console
    $ st run openapi.yaml --checks all --exclude-checks response_schema_conformance
    ```

#### `--max-failures COUNT`

!!! note ""

    **Type**: `Integer`  
    **Default**: `null`  
    **Range**: `≥1`  

    Terminates the test suite after reaching a specified number of failures or errors.

    ```console
    $ st run openapi.yaml --max-failures 5
    ```

#### `--continue-on-failure`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    When enabled, continues executing all test cases within a scenario, even after encountering failures.

    ```console
    $ st run openapi.yaml --continue-on-failure
    ```

#### `--max-response-time SECONDS`

!!! note ""

    **Type**: `Float`  
    **Default**: `null`  
    **Range**: `>0.0`  

    Maximum allowed API response time in seconds. Responses exceeding this limit will be reported as failures.

    ```console
    $ st run openapi.yaml --max-response-time 2.5
    ```

### Filtering

Schemathesis provides various ways to filter which operations are tested:

#### `--include-TYPE VALUE` / `--exclude-TYPE VALUE`

!!! note ""

    **Type**: `String`  

    Include or exclude operations by exact match on path, method, tag, or operation-id.

    ```console
    $ st run openapi.yaml --include-tag users
    $ st run openapi.yaml --exclude-method DELETE
    ```

#### `--include-TYPE-regex PATTERN` / `--exclude-TYPE-regex PATTERN`

!!! note ""

    **Type**: `String (regex pattern)`  

    Include or exclude operations matching a regular expression pattern on path, method, tag, or operation-id.

    ```console
    $ st run openapi.yaml --include-path-regex "/api/v1/.*"
    $ st run openapi.yaml --exclude-tag-regex "admin|internal"
    ```

#### `--include-by EXPR` / `--exclude-by EXPR`

!!! note ""

    **Type**: `String (expression)`  

    Include or exclude operations using a custom expression. The expression must start with a JSON Pointer.

    ```console
    $ st run openapi.yaml --include-by "/tags/0 == 'user'"
    ```

#### `--exclude-deprecated`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    Skip deprecated API operations.

    ```console
    $ st run openapi.yaml --exclude-deprecated
    ```

### Network

The following options control how Schemathesis makes network requests to the API under test:

#### `-H, --header NAME:VALUE`

!!! note ""

    **Type**: `String (multiple allowed)`  

    Add custom HTTP headers to all API requests. This option can be specified multiple times.

    ```console
    $ st run openapi.yaml \
      --header "X-API-Key: abcdef123456" \
      --header "Accept-Language: en-US"
    ```

#### `-a, --auth USER:PASS`

!!! note ""

    **Type**: `String`  

    Authenticate all API requests with basic authentication.

    ```console
    $ st run openapi.yaml --auth username:password
    ```

#### `--proxy URL`

!!! note ""

    **Type**: `String`  

    Set the proxy for all network requests.

    ```console
    $ st run openapi.yaml --proxy http://localhost:8080
    ```

#### `--tls-verify TEXT`

!!! note ""

    **Type**: `String or Boolean`  
    **Default**: `true`  

    Path to CA bundle for TLS verification, or 'false' to disable TLS verification.

    ```console
    $ st run openapi.yaml --tls-verify false
    $ st run openapi.yaml --tls-verify /path/to/ca-bundle.pem
    ```

#### `--rate-limit TEXT`

!!! note ""

    **Type**: `String`  
    **Format**: `<limit>/<duration>`  

    Specify a rate limit for test requests. Supports 's' (seconds), 'm' (minutes), and 'h' (hours) as duration units.

    ```console
    $ st run openapi.yaml --rate-limit 100/m
    $ st run openapi.yaml --rate-limit 5/s
    ```

#### `--max-redirects INTEGER`

!!! note ""

    **Type**: `Integer`  
    **Range**: `>=0`  

    Maximum number of redirects to follow for each network request during tests. Set to `0` to disable redirect following entirely.

    ```console
    $ st run openapi.yaml --max-redirects 5
    ```

#### `--request-timeout SECONDS`

!!! note ""

    **Type**: `Float`
    **Default**: `10.0`
    **Range**: `>0.0`

    Timeout limit, in seconds, for each network request during tests. This is the maximum time to wait for a response before aborting the request.

    ```console
    $ st run openapi.yaml --request-timeout 5.0
    ```

#### `--request-cert PATH`

!!! note ""

    **Type**: `String (file path)`  

    File path of unencrypted client certificate for authentication. The certificate can be bundled with a private key (e.g., PEM) or used with a separate private key.

    ```console
    $ st run openapi.yaml --request-cert /path/to/client-cert.pem
    ```

#### `--request-cert-key PATH`

!!! note ""

    **Type**: `String (file path)`  

    Specify the file path of the private key for the client certificate when not bundled together.

    ```console
    $ st run openapi.yaml \
      --request-cert /path/to/client-cert.crt \
      --request-cert-key /path/to/private-key.key
    ```

### Output

These options control the reporting and output format of test results:

#### `--report FORMAT`

!!! note ""

    **Type**: `Comma-separated list`  
    **Possible values**: `junit`, `vcr`, `har`  

    Generate test reports in specified formats as a comma-separated list.

    ```console
    $ st run openapi.yaml --report junit,har
    ```

#### `--report-dir DIRECTORY`

!!! note ""

    **Type**: `String`  
    **Default**: `schemathesis-report`  

    Directory to store all report files.

    ```console
    $ st run openapi.yaml --report junit --report-dir ./test-reports
    ```

#### `--report-junit-path FILENAME`

!!! note ""

    **Type**: `String`  

    Custom path for JUnit XML report.

    ```console
    $ st run openapi.yaml --report junit --report-junit-path ./custom-junit.xml
    ```

#### `--report-vcr-path FILENAME`

!!! note ""

    **Type**: `String`  

    Custom path for VCR cassette.

    ```console
    $ st run openapi.yaml --report vcr --report-vcr-path ./custom-vcr.yaml
    ```

#### `--report-har-path FILENAME`

!!! note ""

    **Type**: `String`  

    Custom path for HAR file.

    ```console
    $ st run openapi.yaml --report har --report-har-path ./custom-har.json
    ```

#### `--report-preserve-bytes`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    Retain exact byte sequence of payloads in cassettes, encoded as base64.

    ```console
    $ st run openapi.yaml --report vcr --report-preserve-bytes
    ```

#### `--output-sanitize BOOLEAN`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Enable or disable automatic output sanitization to obscure sensitive data.

    ```console
    $ st run openapi.yaml --output-sanitize false
    ```

#### `--output-truncate BOOLEAN`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Truncate schemas and responses in error messages for improved readability.

    ```console
    $ st run openapi.yaml --output-truncate false
    ```

### Data Generation

These options control how Schemathesis generates test data for API testing:

#### `-m, --mode MODE`

!!! note ""

    **Type**: `String`  
    **Default**: `positive`  
    **Possible values**: `positive`, `negative`, `all`  

    Test data generation mode. Controls whether Schemathesis generates valid data, invalid data, or both.

    ```console
    $ st run openapi.yaml --mode all
    ```

#### `-n, --max-examples COUNT`

!!! note ""

    **Type**: `Integer`  
    **Range**: `≥1`  

    Maximum number of test cases generated per API operation. Must be greater than or equal to 1.

    Schemathesis generates diverse examples based on your API schema, distributed across enabled generation modes (e.g., positive and negative test cases). See [Data Generation](../explanations/data-generation.md) for details.

    This setting has different effects depending on the test phase:

    - In **fuzzing** phase: Controls the maximum number of examples generated per API operation
    - In **stateful** phase: Determines the maximum number of distinct API call sequences
    - In **examples** and **coverage** phases: Has no effect, as these use predetermined test cases

    ```console
    $ st run openapi.yaml --max-examples 100
    ```

#### `--seed INTEGER`

!!! note ""

    **Type**: `Integer`  

    Random seed for reproducible test runs. Setting the same seed value will result in the same sequence of generated test cases.

    ```console
    $ st run openapi.yaml --seed 42
    ```

#### `--no-shrink`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    Disable test case shrinking. Makes test failures harder to debug but improves performance.

    ```console
    $ st run openapi.yaml --no-shrink
    ```

#### `--generation-deterministic`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    Enables deterministic mode, which eliminates random variation between tests.

    ```console
    $ st run openapi.yaml --generation-deterministic
    ```

#### `--generation-allow-x00 BOOLEAN`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Whether to allow the generation of 'NULL' bytes within strings.

    ```console
    $ st run openapi.yaml --generation-allow-x00 false
    ```

#### `--generation-codec TEXT`

!!! note ""

    **Type**: `String`  

    The codec used for generating strings. Defines the character encoding for string generation.

    ```console
    $ st run openapi.yaml --generation-codec ascii
    ```

#### `--generation-maximize METRIC`

!!! note ""

    **Type**: `String`  
    **Possible values**: `response_time`  

    Guide input generation to values more likely to expose bugs via targeted property-based testing.

    ```console
    $ st run openapi.yaml --generation-maximize response_time
    ```

#### `--generation-with-security-parameters BOOLEAN`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Whether to generate security parameters during testing.

    ```console
    $ st run openapi.yaml --generation-with-security-parameters false
    ```

#### `--generation-graphql-allow-null BOOLEAN`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Whether to use `null` values for optional arguments in GraphQL queries. Applicable only for GraphQL API testing.

    ```console
    $ st run openapi.yaml --generation-graphql-allow-null false
    ```

#### `--generation-database TEXT`

!!! note ""

    **Type**: `String`  
    **Default**: `.hypothesis/examples`  

    Storage for examples discovered by Schemathesis. Use `none` to disable, `:memory:` for temporary storage, or specify a file path for persistent storage.

    ```console
    $ st run openapi.yaml --generation-database ":memory:"
    $ st run openapi.yaml --generation-database ./schemathesis_examples.db
    ```

#### `--generation-unique-inputs`

!!! note ""

    **Type**: `Flag`  
    **Default**: `false`  

    Force the generation of unique test cases. When enabled, Schemathesis will ensure that no duplicate test inputs are used within a single test phase.

    ```console
    $ st run openapi.yaml --generation-unique-inputs
    ```

## Exit codes

Schemathesis uses predictable exit codes so automation can interpret results:

- `0` — All configured checks passed
- `1` — At least one check failed or a bug was reported
- `2` — The run was aborted due to configuration or schema errors
