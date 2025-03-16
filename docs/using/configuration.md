# Configuration

Schemathesis can be configured through a `schemathesis.toml` file.

## Configuration File Location

Schemathesis will look for configuration in the following locations, in order:

1. Path specified via `--config-file` CLI option.
2. `schemathesis.toml` in the current directory.
3. `schemathesis.toml` in parent directories (up to the project root).

!!! note "Configuration Preference"
    Schemathesis uses only one configuration file and does not merge settings from multiple files. If no `schemathesis.toml` file is found, Schemathesis will use its built-in defaults.

## Why Use a Configuration File?

While Schemathesis works well without explicit configuration, using a file offers several benefits:

- **Operation-Specific Settings**: Configure different behaviors for specific API operations. For example, run more tests or apply different validation rules
- **Validation Customization**: Adjust how API responses are validated. For example, disable not relevant checks on certain API operations.
- **Consistent Testing**: Share configuration across different environments and test runs.

!!! note "CLI and Python API Integration"

    While the configuration file sets default behavior, CLI options and the Python API can override any settings.

For a complete list of settings, see the [Configuration Reference](../reference/configuration.md).

## Basic Structure

Schemathesis configuration uses the [TOML](https://toml.io/en/) format with a hierarchical structure. Global settings serve as defaults and can be overridden by more specific ones such as operation-specific or project-specific ones.

```toml
# Global settings
base-url = "https://api.example.com"
generation.max-examples = 100

# Operation-specific settings
[[operations]]
include-name = "GET /users"
generation.max-examples = 200
request-timeout = 5.0
```
### Environment Variable Substitution

Schemathesis supports using environment variables in configuration files with the `${VAR_NAME}` syntax:

```toml
# Use environment variables for sensitive or environment-specific values
base-url = "https://${API_HOST}/v1"
headers = { Authorization = "Bearer ${API_TOKEN}" }

[[operations]]
include-name = "POST /payments"
headers = { "X-API-Key" = "${API_KEY}" }
```

This allows you to maintain a single configuration file that works across different testing environments by changing environment variables rather than the configuration itself.

!!! tip "Multi-Project Support"

    Schemathesis also supports multi-project configurations, where you can define separate settings for different APIs within the same configuration file. See [Multi-Project Configuration](#multi-project-configuration) for details.

Most users won't need a configuration file at all. Configuration becomes valuable primarily for complex testing scenarios.

## Authentication

Schemathesis supports multiple authentication methods for API testing.

For simple authentication, use the global `[auth]` section:

```toml
[auth]
# Basic authentication
basic = { username = "${USERNAME}", password = "${PASSWORD}" }

# Bearer token authentication
bearer = "${TOKEN}"
```

The basic setting corresponds to the `--auth` CLI option, while bearer tokens can also be specified via CLI headers.

!!! tip "Operation-specific Authentication"

    You can also override auth on the [per-operation basic](#operation-specific-authentication).

### OpenAPI Security Schemes

For OpenAPI specifications with defined security schemes, configure them by name:

```toml
[auth.openapi]
# Basic HTTP authentication
BasicAuth = { username = "${USERNAME}", password = "${PASSWORD}" }

# Bearer token authentication
BearerAuth = { token = "${TOKEN}" }

# API Key authentication
ApiKeyAuth = { value = "${API_KEY}" }

# OAuth2 authentication
OAuth2 = { client_id = "${CLIENT_ID}", client_secret = "${CLIENT_SECRET}" }
```

These settings map directly to the `securitySchemes` in your OpenAPI specification and will be automatically used for API operations with corresponding security schemes.

### Authentication Resolution

When multiple methods are specified, Schemathesis resolves authentication in the following order:

- CLI options (`--auth` or `--header`)
- Operation-specific headers
- Specification-specific authentication (`[auth.openapi]`)
- Generic authentication (global `[auth]`)

This flexible resolution lets you override settings at different levels while keeping sensitive data in environment variables.

## Test Phases

Schemathesis runs tests in distinct phases, each with different approaches to generating and executing test cases.

### Available Phases

Schemathesis includes the following test phases:

- **examples**: Tests using examples explicitly defined in your API schema
- **coverage**: Tests using deterministic edge cases and boundary values
- **fuzzing**: Tests using randomly generated values
- **stateful**: Tests API operation sequences to find state-dependent issues

All phases are enabled by default.

### Phase Configuration

Customize test phases using the `[phases]` section in your configuration file. For example:

```toml
[phases]
# Disable a specific phase
coverage.enabled = false

# Phase-specific settings
# Maximum number of distinct API call sequences
stateful.generation.max-examples = 30
# Maximum examples per operation in fuzzing phase
fuzzing.generation.max-examples = 200
```

### Phase-Specific Behavior

Certain settings are interpreted differently across test phases. For instance, the `generation.max-examples` setting works as follows:

  - **fuzzing**: Controls the maximum number of examples generated per API operation.
  - **stateful**: Determines the maximum number of distinct API call sequences.

Phases that use predetermined test cases (such as the **examples** and **coverage** phases) are unaffected by the `generation.max-examples` setting.

Consider this example, which sets a global default while overriding it for the stateful phase:

```toml
# Global setting
generation.max-examples = 200

[phases]
# Override for stateful phase
stateful.generation.max-examples = 30
```

## Checks

Schemathesis validates API responses with a series of checks that verify various aspects of your API's behavior—from server availability to schema conformance.

### Available Checks

Schemathesis includes the following checks (all enabled by default):

- **not_a_server_error**: API doesn't return 5xx responses.
- **status_code_conformance**: Status code is defined in the schema.
- **content_type_conformance**: Response content type is defined in the schema.
- **response_schema_conformance**: Response body conforms to its schema.
- **positive_data_acceptance**: Valid data is accepted.
- **negative_data_rejection**: Invalid data is rejected.
- **use_after_free**: Resource is inaccessible after deletion.
- **ensure_resource_availability**: Resource is available after creation.
- **ignored_auth**: Authentication is properly enforced.

### Global Check Configuration

Configure checks globally using the `[checks]` section:

```toml
[checks]
# Disable checks globally
content_type_conformance.enabled = false

# Set check parameters
positive_data_acceptance.expected-statuses = [200, 201, 202]
negative_data_rejection.expected-statuses = [400, 422]
```

### Operation-Specific Check Configuration

Override check settings for a specific operation:

```toml
[[operations]]
include-name = "POST /users"
# Operation-specific check settings
checks.positive_data_acceptance.expected-statuses = [201]
checks.response_schema_conformance.enabled = false
```

### Disabling All Checks Except Selected Ones

To run only specific checks, disable all by default and then enable chosen ones:

```toml
[checks]
enabled = false

# Enable only selected checks
not_a_server_error.enabled = true
status_code_conformance.enabled = true
```

This configuration runs only the server error and status code checks.

### Check Resolution

Schemathesis applies check configurations in the following order:

- Operation
- Phase
- Global

This hierarchy lets you define global defaults while overriding settings for specific endpoints.

## Operation-Specific Configuration

Schemathesis allows you to apply custom configuration to specific API operations. You can include or exclude operations based on various criteria.

### Basic Configuration Structure

Use arrays of tables with inclusion and exclusion filters:

```toml
[[operations]]
include-path = "/users"
generation.max-examples = 200
request-timeout = 5.0

[[operations]]
include-tag = "admin"
enabled = false
```

### Multiple Values for Filters

For any filter type, you can provide an array of strings to match against multiple values. This works as an OR condition - if any value in the array matches, the filter applies:

```toml
[[operations]]
include-path = ["/users", "/accounts", "/profiles"]
generation.max-examples = 150

[[operations]]
include-tag = ["admin", "management"]
request-timeout = 3.0
```

!!! note ""

    This feature works with all filter types, except regex and custom expressions.

### Filter Types

You can filter operations using several criteria types:

#### Path

Matches the operation's URL path:

```toml
[[operations]]
include-path = "/users/{id}"
generation.max-examples = 150
```

#### Method

Matches the HTTP method (GET, POST, PUT, etc.):

```toml
[[operations]]
include-method = "POST"
request-timeout = 3.0
```

#### Name

Matches the operation's name, which varies by specification:

- For OpenAPI: `METHOD /path` (e.g., `GET /users`)
- For GraphQL: `Type.field` (e.g., `Query.getUser`)

```toml
[[operations]]
include-name = "GET /users"
generation.max-examples = 200

[[operations]]
include-name = "Query.getUser"  # For GraphQL
generation.max-examples = 200
```

#### Tag

Matches operations with specific tags defined in the API specification:

```toml
[[operations]]
include-tag = "payment"
generation.max-examples = 150
rate-limit = "20/s"
```

#### Operation ID

Matches the operationId field in OpenAPI specifications:

```toml
[[operations]]
include-operation-id = "getUserById"
generation.max-examples = 150
```

### Filtering with Regular Expressions

Add `-regex` suffix to any filter type to use regular expression patterns:

```toml
[[operations]]
include-path-regex = "/(users|orders)/"
generation.max-examples = 150

[[operations]]
include-method-regex = "(POST|PUT|PATCH)"
request-timeout = 3.0

[[operations]]
include-operation-id-regex = ".*User.*"
checks.not_a_server_error.enabled = false
```

### Exclusion Filters

Use `exclude-` prefix to skip operations matching specific criteria:

```toml
[[operations]]
exclude-path = "/internal/status"
enabled = false

[[operations]]
exclude-tag = "deprecated"
enabled = false
```

### Combining Multiple Criteria

You can combine multiple criteria within a single configuration entry:

```toml
[[operations]]
include-method = "POST"
# include-tag = "users"
request-timeout = 3.0
```

All criteria must match for the configuration to apply.

### Advanced Filtering with Expressions

For more precise control, Schemathesis supports targeting operations using JSONPath-like expressions that query specific fields within operation definitions:

```toml
[[operations]]
include-by = "tags/0 == 'user'"
generation.max-examples = 150

[[operations]]
include-by = "operationId == null"
enabled = false

[[operations]]
exclude-by = "responses/200/description != 'Success'"
checks.response_schema_conformance.enabled = false
```

Include-by and exclude-by expressions follow the pattern `<json-pointer> <operator> <value>` where:

- `<json-pointer>` is a path to a field in the operation definition
- `<operator>` is either `==` (equals) or `!=` (not equals)
- `<value>` can be a string, number, boolean, null, array, or object

This is particularly useful for filtering by operation metadata or extension fields.

### Configuration Precedence

When multiple configuration entries match an operation:

1. Earlier entries take precedence over later entries in the configuration file
2. If multiple criteria are specified within one entry, all must match for the configuration to apply
3. Exclusion takes precedence over inclusion when at the same level

This allows for granular control by ordering your configuration appropriately.

### Operation-specific Authentication

If you'd like to override auth for some API operations you can specify the `auth` key

```toml
[[operations]]
include-name = "POST /orders"
auth = { bearer = "${TOKEN}" }
```

## Phase-Specific Settings for Operations

Configure phase-specific settings within individual operations for fine-grained control over each phase's behavior:

```toml
[[operations]]
include-name = "GET /users"
# Default settings for this operation
generation.max-examples = 100
request-timeout = 5.0

# Phase-specific overrides for this operation
# Increase examples for fuzzing
phases.fuzzing.generation.max-examples = 200 
# Reduce examples for stateful tests
phases.stateful.generation.max-examples = 30
```
### Resolution Order for Phase-Operation Settings

When both operation-level and phase-level settings are defined, Schemathesis applies them in the following order:

- Operation-specific phase settings
- Operation (e.g., `[[operations]]`)
- Phase (e.g., `[phases.fuzzing]`)
- Global

## Parameter Overrides

Schemathesis allows you overriding API operation parameters for better test case control.

### Operation-Specific Parameters

Set parameter values for specific operations:

```toml
[[operations]]
include-name = "GET /users/{user_id}"
# Fixed value
parameters = { user_id = 42 }
```

```toml
[[operations]]
include-name = "GET /users/{user_id}"
# Multiple values for random selection
parameters = { user_id = [1, 42, 499] }
```

```toml
[[operations]]
include-name = "GET /users/{user_id}"
# Using an environment variable
parameters = { user_id = "${USER_ID}" }
```

```toml
[[operations]]
include-name = "GET /users/{user_id}"
# Disambiguate parameters with the same name
parameters = { "path.user_id" = 42, "query.user_id" = 100 }
```

### Global Parameter Overrides

Apply parameters across all operations:

```toml
[parameters]
api_version = "v2"
limit = 50
offset = 0
```

These values will only be used if an API operation uses parameters with those names.

### Parameter Resolution Order

Schemathesis resolves parameter values in this order:

- Operation overrides
- Global overrides
- Generated

### Parameter Type Disambiguation

When parameters share the same name, prefix them to indicate their location:

```toml
[parameters]
"path.id" = 42                      # Path parameter
"query.id" = 100                    # Query parameter
"header.X-API-Version" = "2.0"      # Header parameter
"cookie.session" = "${SESSION_ID}"  # Cookie parameter
```

### Using Parameter Arrays

Provide an array of values; Schemathesis will randomly select one per test case:

```toml
[[operations]]
include-name = "GET /users"
parameters = { role = ["admin", "user", "guest"] }
```

The example above distributes the specified roles across test cases.

## Multi-Project Configuration

Schemathesis lets you configure multiple API projects in one file—a handy feature for testing related APIs or different API versions.

### Defining Projects

Define projects in the `[[projects]]` section by matching the API's title:

```toml
[[projects]]
# Projects are matched by the API schema's `info.title``
title = "Payment Processing API"
base-url = "https://payments.example.com"
```

Schemathesis checks the `info.title` field of the API schema to apply the corresponding project settings.

### Project-Specific Settings

Override global defaults with project-specific settings:

```toml
[[projects]]
title = "Payment Processing API"
base-url = "https://payments.example.com"
workers = 4
generation.max-examples = 200
hooks = "test.config.hooks.example"

[[projects]]
title = "User Management API"
base-url = "https://users.example.com"
workers = 2
```

### Project-Specific Operations

Customize operations within a project:

```toml
[[projects]]
title = "Payment Processing API"

[[projects.operations]]
include-name = "POST /payments"
generation.max-examples = 80
headers = { "X-Idempotency-Key" = "${IDEMPOTENCY_KEY}" }

[[projects.operations]]
include-tag = "slow"
request-timeout = 10.0
```

### Configuration Resolution

When using projects, settings are applied in this order:

- Operation-specific settings within the project
- Project-level settings
- Default operation settings (unprefixed)
- Global settings

### Example: Complete Multi-Project Configuration

```toml
# Global defaults
generation.max-examples = 50
workers = 2

[[projects]]
title = "Payment Processing API"
base-url = "https://payments.example.com"
generation.max-examples = 100

# Operations for payments
[[projects.operations]]
include-name = "POST /payments"
generation.max-examples = 200
parameters = { amount = [10, 100, 1000] }
checks.positive_data_acceptance.expected-statuses = [200, 201]

# Users project settings
[[projects]]
title = "User Management API"
base-url = "https://users.example.com"
```

With this setup, each project uses its own settings while sharing defaults.

## Configuration Overrides

While the configuration file provides default settings, you can override most them via CLI options.

### CLI Overrides

Command-line arguments take precedence over configuration file settings:

```bash
# Override the max-examples setting
st run --max-examples=300 http://api.example.com/openapi.json

# Override phases
st run --phases=examples,fuzzing http://api.example.com/openapi.json

# Override check settings
st run --checks=not_a_server_error http://api.example.com/openapi.json
```
