# Configuration Options

This reference covers all the configuration options available in `schemathesis.toml`. The settings are organized into two main categories:

- **Global**: These control CLI behavior, output formatting, and overall test execution. They are defined at the top level and affect the CLI invocation.
- **Per-Project**: These let you customize configurations for individual API projects. If project-level settings are placed at the top level (without a `[project.<name>]` namespace), they are used as defaults for any tested project.

## Configuration Resolution

Schemathesis applies settings in the following hierarchy (from highest to lowest precedence):

1. CLI options
2. Operation-specific phase settings
3. Global phase settings (e.g., `[phases.fuzzing]`)
4. Operation-level settings (e.g., `[[operations]]`)
5. Project-level settings (e.g., `[[projects]]`)
6. Global settings

## Environment Variable Substitution

Schemathesis supports using environment variables in configuration files with the `${VAR_NAME}` syntax:

```toml
base-url = "https://${API_HOST}/v1"
headers = { Authorization = "Bearer ${API_TOKEN}" }
```

This allows you to maintain a single configuration file across different environments by changing environment variables rather than the configuration itself.

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
```

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

#### `max-failures`

!!! note ""

    **Type**: `Integer (≥1)`  
    **Default**: `null`  

    Terminates the test run after the specified number of failures is reached.

    ```toml
    max-failures = 42
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

#### `output.sanitize`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Controls automatic sanitization of output data to obscure sensitive information.

    ```toml
    [output]
    sanitize = false
    ```

#### `output.truncate`

!!! note ""

    **Type**: `Boolean`  
    **Default**: `true`  

    Truncates long output in error messages for improved readability.

    ```toml
    [output]
    truncate = false
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
    # [[projects]]
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
    # [[projects]]
    # hooks = "myproject.payments.hooks"
    ```

#### `workers`

!!! note ""

    **Type:** `Integer`  
    **Default:** `Number of available CPUs`  

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

#### `exclude-deprecated`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    Skip deprecated API operations.

    ```toml
    exclude-deprecated = true
    ```

#### `continue-on-failure`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    When enabled, continues executing all test cases within a scenario, even after encountering failures.

    ```toml
    continue-on-failure = true
    ```

#### `max-response-time`

!!! note ""

    **Type:** `Float (>0)`  
    **Default:** `null`  

    Maximum allowed API response time in seconds. Responses exceeding this limit will be reported as failures.

    ```toml
    max-response-time = 2.0
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

#### `auth.bearer`

!!! note ""

    **Type:** `String`  
    **Default:** `null`  

    Specifies a bearer token for authentication.

    ```toml
    [auth]
    bearer = "${API_TOKEN}"
    ```

#### `auth.openapi`

!!! note ""

    **Type:** `Object`  
    **Default:** `null`  

    Defines authentication settings for OpenAPI security schemes. Each key in this object should match a security scheme defined in your OpenAPI specification. 

    Schemathesis resolves authentication in order: 

      - CLI options
      - Operation-specific auth
      - Global

    ```toml
    [auth.openapi]
    # Basic HTTP authentication
    BasicAuth = { username = "${USERNAME}", password = "${PASSWORD}" }

    # Bearer token authentication
    BearerAuth = { token = "${API_TOKEN}" }

    # API Key authentication
    ApiKeyAuth = { value = "${API_KEY}" }

    # OAuth2 authentication
    OAuth2 = { client_id = "${CLIENT_ID}", client_secret = "${CLIENT_SECRET}" }
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

    ```toml
    [checks]
    response_schema_conformance.enabled = false
    ```

#### `checks.<check>.expected-statuses`

!!! note ""

    **Type:** `Array[Integer]`  
    **Default:** `[200]`  

    Defines the HTTP status codes expected from the API for specific checks. Different checks may interpret this setting differently:

      - For `positive_data_acceptance`: Defines status codes that indicate the API has accepted valid data
      - For `negative_data_rejection`: Defines status codes that indicate the API has properly rejected invalid data
      - For `missing_required_header`: Defines status codes that indicate the API has properly rejected a call without required header
      - For `not_a_server_error`: Defines status codes that are not considered server errors within the 5xx range

    This allows you to customize validation for APIs that use non-standard success or error codes.

    ```toml
    [checks]
    positive_data_acceptance.expected-statuses = [200, 201, 202]
    ```

### Network

The following settings control how Schemathesis makes network requests to the API under test.

#### `header`

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

#### `generation.seed`

!!! note ""

    **Type:** `Integer`  
    **Default:** `null`  

    Random seed for reproducible test runs. Setting the same seed value will result in the same sequence of generated test cases.

    ```toml
    [generation]
    seed = 42
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

#### `generation.fill-missing-examples`

!!! note ""

    **Type:** `Boolean`  
    **Default:** `false`  

    Enable generation of random examples for API operations that do not have explicit examples in the OpenAPI schema.

    ```toml
    [generation]
    fill-missing-examples = true
    ```
