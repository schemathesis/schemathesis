# Configuration Options

This reference covers all the configuration options available in `schemathesis.toml`. The settings are organized into two main categories:

- **Global**: These control CLI behavior, output formatting, and overall test execution. They are defined at the top level and affect the CLI invocation.
- **Per-Project**: These let you customize configurations for individual API projects. If project-level settings are placed at the top level (without a `[[project]]` namespace), they are used as defaults for any tested project.

## Configuration Resolution

Schemathesis applies settings in the following hierarchy (from highest to lowest precedence):

1. CLI options
2. Operation-specific phase settings
3. Global phase settings (e.g., `[phases.fuzzing]`)
4. Operation-level settings (e.g., `[[operations]]`)
5. Project-level settings (e.g., `[[project]]`)
6. Global settings

## Environment Variable Substitution

Schemathesis supports using environment variables in configuration files with the `${VAR_NAME}` syntax:

```toml
base-url = "https://${API_HOST}/v1"
headers = { Authorization = "Bearer ${API_TOKEN}" }
```

This allows you to maintain a single configuration file across different environments by changing environment variables rather than the configuration itself.

!!! note ""
    If you use `pytest`, environment variables are resolved when the `SchemathesisConfig` object is initialized, which usually happens inside schema loaders like `schemathesis.openapi.from_url`.

## Operation-Specific Configuration

Schemathesis allows applying custom configuration to specific API operations in a few ways:

```toml
[[operations]]
# By exact path
include-path = "/users"
# By HTTP method
exclude-method = "POST"
# By full operation name
# include-name = "POST /users/"
# By Open API tag
# include-tag = "admin"
# By Open API operation ID
# include-operation-id = "delete-user"
enabled = false
```

!!! note "Applying filters"
    The config above will disable all operations matching the set of filters.

## Parameter Overrides

Parameters can be overridden at the global or operation level:

```toml
# Global parameters
[parameters]
api_version = "v2"

# Operation-specific parameters
[[operations]]
include-name = "GET /users/"
parameters = { limit = 50, offset = 0 }

[[operations]]
include-name = "GET /users/{user_id}/"
# Disambiguate parameters with the same name
parameters = { "path.user_id" = 42, "query.user_id" = 100 }
```

## Global Settings

#### `color`

!!! note ""

    **Type**: `Boolean or None`  
    **Default**: `null`  

    Controls ANSI color output in the CLI. Schemathesis auto-detects color support by default. Set to `true` to force color output or `false` to disable it.

    ```toml
    color = false
    ```

#### `suppress-health-check`

!!! note ""

    **Type**: `Array[String]`  
    **Default**: `[]`  

    Specifies a list of health checks to disable during test execution. Possible values include: `data_too_large`, `filter_too_much`, `too_slow`, `large_base_example`, and `all`.

    ```toml
    suppress-health-check = ["too_slow", "data_too_large"]
    ```

#### `seed`

!!! note ""

    **Type:** `Integer`  
    **Default:** `null`  

    Random seed for reproducible test runs. Setting the same seed value will result in the same sequence of generated test cases.

    ```toml
    seed = 42
    ```

#### `max-failures`

!!! note ""

    **Type**: `Integer (≥1)`  
    **Default**: `null`  

    Terminates the test run after the specified number of failures is reached.

    ```toml
    max-failures = 42
    ```

#### `warnings`

!!! note ""

    **Type**: `Boolean` or `Array[String]`  
    **Default**: `true` (all warnings enabled)  

    Controls which warnings are displayed during test execution. Warnings highlight issues that could prevent tests from reaching your API's core logic.

    ```toml
    # Disable all warnings
    warnings = false
    ```

    ```toml
    # Enable specific warnings only
    warnings = ["missing_auth", "validation_mismatch"]
    ```

### Reporting

#### `reports.directory`

!!! note "" 

    **Type**: `String`  
    **Default**: `"schemathesis-report"`  

    Specifies the directory where all test reports are stored.

    ```toml
    [reports]
    directory = "test-results"
    ```

#### `reports.preserve-bytes`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `false`  

    Retains the exact byte sequences of payloads in reports, encoded as base64 when enabled.

    ```toml
    [reports]
    preserve-bytes = true
    ```

#### `reports.<format>.enabled`

!!! note "" 

    **Type**: `Boolean`  
    **Default**: `false`  

    Enables the generation of the specified report format. Replace `<format>` with one of: `junit`, `vcr`, or `har`.

    ```toml
    [reports.junit]
    enabled = true
    ```

#### `reports.<format>.path`

!!! note ""

    **Type**: `String`  
    **Default**: `null`  

    Specifies a custom path for the report of the specified format. Replace `<format>` with one of: `junit`, `vcr`, or `har`.

    Setting this option automatically enables the report generation without requiring `enable = true`.

    ```toml
    [reports.junit]
    path = "./test-reports/schemathesis-results.xml"
    ```

### Output

#### `output.sanitization.enabled`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Controls automatic sanitization of output data to obscure sensitive information.

    ```toml
    [output.sanitization]
    enabled = false
    ```

#### `output.sanitization.keys-to-sanitize`

!!! note ""

    **Type**: `List[String]`  
    **Default**: List of common sensitive keys (auth tokens, passwords, etc.)  

    Specific keys that will be automatically sanitized in output data.

    ```toml
    [output.sanitization]
    keys-to-sanitize = ["password", "token", "auth"]
    ```

#### `output.sanitization.sensitive-markers`

!!! note ""

    **Type**: `List[String]`  
    **Default**: `["token", "key", "secret", "password", "auth", "session", "passwd", "credential"]`  

    Substrings that indicate a key might contain sensitive information requiring sanitization.

    ```toml
    [output.sanitization]
    sensitive-markers = ["secret", "auth", "key"]
    ```

#### `output.sanitization.replacement`

!!! note ""

    **Type**: `String`  
    **Default**: `"[Filtered]"`  

    The text used to replace sensitive data in output.

    ```toml
    [output.sanitization]
    replacement = "***REDACTED***"
    ```

#### `output.truncation.enabled`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Controls whether large output data should be truncated.

    ```toml
    [output.truncation]
    enabled = false
    ```

#### `output.truncation.max-payload-size`

!!! note ""

    **Type**: `Integer`  
    **Default**: `512`  

    Maximum size in bytes for payloads before truncation occurs.

    ```toml
    [output.truncation]
    max-payload-size = 1024
    ```

#### `output.truncation.max-lines`

!!! note ""

    **Type**: `Integer`  
    **Default**: `10`  

    Maximum number of lines to display for multi-line output.

    ```toml
    [output.truncation]
    max-lines = 20
    ```

#### `output.truncation.max-width`

!!! note ""

    **Type**: `Integer`  
    **Default**: `80`  

    Maximum width in characters for each line before horizontal truncation.

    ```toml
    [output.truncation]
    max-width = 100
    ```

## Project Settings

These settings can only be applied at the project level.

#### `base-url`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    Sets the base URL for the API under test. This setting is required when testing with a file-based schema.

    ```toml
    # Optionally under a named project
    # [[project]]
    base-url = "https://api.example.com"
    ```

#### `hooks`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    Specifies a Python module path where custom hooks for extending Schemathesis functionality are located. This allows you to define custom checks, adjust data generation, or extend CLI.

    ```toml
    # Global hooks for all projects
    hooks = "myproject.tests.hooks"
    
    # Or project-specific hooks
    # [[project]]
    # hooks = "myproject.payments.hooks"
    ```

#### `workers`

!!! note ""

    **Type:** `Integer or "auto"`  
    **Default:** `1`  

    Specifies the number of concurrent workers for running unit test phases.

    ```toml
    workers = 4       # Use exactly 4 workers
    ```

#### `wait-for-schema`

!!! note ""

    **Type:** `Number (≥1.0)`  
    **Default:** `null`  

    Maximum duration, in seconds, to wait for the API schema to become available. Useful when testing services that take time to start up.

    ```toml
    wait-for-schema = 5.0
    ```

#### `continue-on-failure`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    When enabled, continues executing all test cases within a scenario, even after encountering failures.

    ```toml
    continue-on-failure = true
    ```

### Phases

#### `phases.<phase>.enabled`

!!! note "" 

    **Type**: `Boolean`  
    **Default**: `true`  

    Enables a testing phase. Replace `<phase>` with one of: `examples`, `coverage`, `fuzzing`, or `stateful`.

    ```toml
    [phases.coverage]
    enabled = false
    ```

#### `phases.examples.fill-missing`

!!! note "" 

    **Type**: `Boolean`  
    **Default**: `false`  

    Enable generation of random examples for API operations that do not have explicit examples.

    ```toml
    [phases.examples]
    fill-missing = true
    ```

#### `phases.coverage.generate-duplicate-query-parameters`

!!! note "" 

    **Type**: `Boolean`  
    **Default**: `false`  

    When enabled, the coverage phase will emit duplicate query parameters in test requests.
    For example: `GET /items?page_num=1&page_num=2`

    ```toml
    [phases.coverage]
    generate-duplicate-query-parameters = true
    ```

#### `phases.coverage.unexpected-methods`

!!! note "" 

    **Type**: `Array[String]`  
    **Default**: `[]`  

    Lists the HTTP methods to use when generating test cases with methods not specified in the API during the **coverage** phase.  
    Schemathesis will limit negative testing of unexpected methods to those in the array; if omitted, all HTTP methods not specified in the spec are applied.

    ```toml
    [phases.coverage]
    unexpected-methods = ["PATCH"]
    ```

#### `phases.stateful.max-steps`

!!! note ""

    **Type**: `Integer (≥2)`  
    **Default**: `null`  

    Specifies the maximum number of stateful steps (i.e., transitions between states) to perform in the **stateful** phase. When set, Schemathesis will stop exploring new state transitions once this limit is reached, even if additional valid transitions are available.  

    ```toml
    [phases.stateful]
    max-steps = 50
    ```

#### `phases.<phase>.checks`

!!! note "" 

    **Type**: `Object`  
    **Default**: `{}`  

    Phase-specific overrides for checks.

    ```toml
    [phases.coverage.checks]
    not_a_server_error.enabled = false
    ```

### Authentication

#### `auth.basic`

!!! note ""

    **Type:** `Object`  
    **Default:** `null`  

    Provides basic authentication credentials. Define this object with `username` and `password` keys. This setting corresponds to the `--auth` CLI option.

    ```toml
    [auth]
    basic = { username = "${USERNAME}", password = "${PASSWORD}" }
    ```

### Checks

#### `checks.<check>.enabled`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `true`  

    Enables or disables the specified check. Replace `<check>` with one of the following:

      - `not_a_server_error`
      - `status_code_conformance`
      - `content_type_conformance`
      - `response_schema_conformance`
      - `positive_data_acceptance`
      - `negative_data_rejection`
      - `use_after_free`
      - `ensure_resource_availability`
      - `missing_required_header`
      - `ignored_auth`
      - `unsupported_method`

    ```toml
    [checks]
    response_schema_conformance.enabled = false
    ```

#### `checks.<check>.expected-statuses`

!!! note ""

    **Type:** `Array[Integer | String]`  
    **Default:** `[200]`  

    Defines the HTTP status codes expected from the API for specific checks. Different checks may interpret this setting differently:

      - For `positive_data_acceptance`: Defines status codes that indicate the API has accepted valid data
      - For `negative_data_rejection`: Defines status codes that indicate the API has properly rejected invalid data
      - For `missing_required_header`: Defines status codes that indicate the API has properly rejected a call without required header
      - For `not_a_server_error`: Defines status codes that are not considered server errors within the 5xx range

    This allows you to customize validation for APIs that use non-standard success or error codes.

    Status codes can be specified as exact integers (e.g., `200`) or as wildcard strings using the `X` character to match any digit (e.g., `"2XX"` to match all 2xx codes, `"4XX"` for all client error responses).

    ```toml
    [checks]
    positive_data_acceptance.expected-statuses = [200, 201, 202]
    ```

#### `checks.max_response_time`

!!! note ""

    **Type:** `Float (>0)`  
    **Default:** `null`  

    Maximum allowed API response time in seconds. Responses exceeding this limit will be reported as failures.

    ```toml
    [checks]
    max_response_time = 2.0
    ```

### Network

The following settings control how Schemathesis makes network requests to the API under test.

#### `headers`

!!! note ""

    **Type:** `Object`  
    **Default:** `{}`  

    Add custom HTTP headers to all API requests. Headers are specified as key-value pairs.

    Add a single header:

    ```toml
    headers = { "X-API-Key" = "${API_KEY}" }
    ```

    Add multiple headers:

    ```toml
    headers = { "X-API-Key" = "${API_KEY}", "Accept-Language" = "en-US" }
    ```

#### `proxy`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    Set the proxy URL for all network requests. Supports HTTP and HTTPS proxies.

    HTTP proxy:

    ```toml
    proxy = "http://localhost:8080"
    ```

    HTTPS proxy with authentication:

    ```toml
    proxy = "https://${USERNAME}:${PASSWORD}@proxy.example.com:8443"
    ```

#### `tls-verify`

!!! note ""

    **Type:** `String | Boolean`  
    **Default:** `true`  

    Control TLS certificate verification. Can be a boolean or a path to a CA bundle file.

    Disable TLS verification:

    ```toml
    tls-verify = false
    ```

    Use a custom CA bundle:

    ```toml
    tls-verify = "/path/to/ca-bundle.pem"
    ```

#### `rate-limit`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    Specify a rate limit for test requests in '<limit>/<duration>' format. Supports 's' (seconds), 'm' (minutes), and 'h' (hours) as duration units.

    100 requests per minute:

    ```toml
    rate-limit = "100/m"
    ```

    5 requests per second:

    ```toml
    rate-limit = "5/s"
    ```

    1000 requests per hour:

    ```toml
    rate-limit = "1000/h"
    ```

#### `request-timeout`

!!! note ""

    **Type:** `Float`  
    **Default:** `null`  

    Set a timeout limit in seconds for each network request during tests. Must be a positive number.

    5 second timeout:

    ```toml
    request-timeout = 5.0
    ```

    500 millisecond timeout:

    ```toml
    request-timeout = 0.5
    ```

#### `request-cert`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    File path to an unencrypted client certificate for authentication. The certificate can be bundled with a private key (e.g., PEM) or used with a separate private key specified by `request-cert-key`.

    ```toml
    # Client certificate with bundled private key
    request-cert = "/path/to/client-cert.pem"
    ```

#### `request-cert-key`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    File path to the private key for the client certificate when not bundled together.

    ```toml
    # Set certificate and separate private key
    request-cert = "/path/to/client-cert.crt"
    request-cert-key = "/path/to/private-key.key"
    ```

### Data Generation

The following settings control how Schemathesis generates test data for your API testing.

#### `generation.mode`

!!! note ""

    **Type:** `String`  
    **Default:** `"all"`  
    
    Test data generation mode. Controls whether Schemathesis generates valid data, invalid data, or both.
    
    Possible values:

    - `"positive"`: Generate only valid data according to the schema
    - `"negative"`: Generate only invalid data to test error handling
    - `"all"`: Generate both valid and invalid data
    
    ```toml
    [generation]
    mode = "negative"
    ```

#### `generation.max-examples`

!!! note ""

    **Type:** `Integer`  
    **Default:** `100`  

    Maximum number of test cases generated per API operation. Must be greater than or equal to 1.

    This setting has different effects depending on the test phase:

    - In **fuzzing** phase: Controls the maximum number of examples generated per API operation
    - In **stateful** phase: Determines the maximum number of distinct API call sequences
    - In **examples** and **coverage** phases: Has no effect, as these use predetermined test cases

    ```toml
    [generation]
    max-examples = 200
    ```

#### `generation.no-shrink`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    Disable test case shrinking. When enabled, Schemathesis won't attempt to simplify failing test cases. This improves performance but makes test failures harder to debug.

    ```toml
    [generation]
    no-shrink = true
    ```

#### `generation.deterministic`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    Enables deterministic mode, which eliminates random variation between tests. Useful for consistency in test outcomes.

    ```toml
    [generation]
    deterministic = true
    ```

#### `generation.allow-x00`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `true`  

    Controls whether to allow the generation of 'NULL' bytes (0x00) within strings. Some systems may not handle these bytes correctly.

    ```toml
    [generation]
    allow-x00 = false
    ```


#### `generation.exclude-header-characters`

!!! note ""

    **Type:** `String`  
    **Default:** `\r\n`  

    All characters from the given strings will be excluded from generated header values.

    ```toml
    [generation]
    exclude-header-characters = "\r\nABC"
    ```

#### `generation.codec`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    The codec used for generating strings. Defines the character encoding for string generation.

    ```toml
    [generation]
    codec = "ascii"
    ```

#### `generation.maximize`

!!! note ""

    **Type:** `String` or `Array[String]`  
    **Default:** `null`  
    
    Guide input generation to values more likely to expose bugs via targeted property-based testing.
    
    Possible values:

    - `"response_time"`: Focus on generating inputs that maximize response time
    
    ```toml
    [generation]
    maximize = "response_time"
    ```

#### `generation.with-security-parameters`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `true`  

    Controls whether to generate security parameters during testing. When enabled, Schemathesis will include appropriate security-related parameters in test data based on the API's security schemes defined in the schema.

    ```toml
    [generation]
    with-security-parameters = false
    ```

#### `generation.graphql-allow-null`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `true`  

    Controls whether to use `\x00` bytes in generated GraphQL queries. Applicable only for GraphQL API testing.

    ```toml
    [generation]
    graphql-allow-null = false
    ```

#### `generation.database`

!!! note ""

    **Type:** `String`  
    **Default:** `.hypothesis/examples`  
    
    Storage for examples discovered by Schemathesis. Options:

    - `"none"`: Disable storage
    - `":memory:"`: Use temporary in-memory storage
    - File path: For persistent storage in a custom location

    By default, Schemathesis creates a directory-based example database in your current working directory under `.hypothesis/examples`. If this location is unusable, Schemathesis will emit a warning and use an alternative.

    ```toml
    [generation]
    database = ":memory:"
    ```

    Or for persistent storage:

    ```toml
    [generation]
    database = "./.schemathesis/examples/"
    ```

#### `generation.unique-inputs`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    Force the generation of unique test cases. When enabled, Schemathesis will ensure that no duplicate test inputs are used within a single test phase.

    ```toml
    [generation]
    unique-inputs = true
    ```
