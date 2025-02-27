# Configuration Guide

Schemathesis can be configured through a `schemathesis.toml` file.

## Configuration File Location

Schemathesis will look for configuration in the following locations, in order:

1. Path specified via `--config` CLI option.
2. `schemathesis.toml` in the current directory.
3. `schemathesis.toml` in parent directories (up to the project root).

!!! note "Configuration Preference"
    Schemathesis uses only one configuration file and does not merge settings from multiple files. If no `schemathesis.toml` file is found, Schemathesis will use its built-in defaults.

## Why Use a Configuration File?

While Schemathesis works well without explicit configuration, using a file offers several benefits:

- **Operation-Specific Settings**: Configure different behaviors for specific API operations. For example, run more tests or apply different validation rules
- **Validation Customization**: Adjust how API responses are validated. For example, trigger certain failures on non-default status codes.
- **Consistent Testing**: Share configuration across different environments and test runs.

!!! note "CLI and Python API Integration"
    While the configuration file sets default behavior, CLI options and the Python API can override any settings.

For a complete list of settings, see the [Configuration Reference](./reference/configuration.md).

## Basic Structure

Schemathesis configuration uses the TOML format with a hierarchical structure. Global settings serve as defaults and can be overridden by more specific ones such as operation-specific or project-specific configurations.

```toml
# Global settings
base-url = "https://api.example.com"
generation.max-examples = 100

# Operation-specific settings
[operation."GET /users"]
generation.max-examples = 200
request-timeout = 5.0
```
### Environment Variable Substitution

Schemathesis supports using environment variables in configuration files with the `${VAR_NAME}` syntax:

```toml
# Use environment variables for sensitive or environment-specific values
base-url = "https://${API_HOST}/v1"
headers = { "Authorization" = "Bearer ${API_TOKEN}" }

[operation."POST /payments"]
headers = { "X-API-Key" = "${PAYMENTS_API_KEY}" }
```

This allows you to maintain a single configuration file that works across different environments (development, staging) by changing environment variables rather than the configuration itself.

!!! tip "Multi-Project Support"
    Schemathesis also supports multi-project configurations, where you can define separate settings for different APIs within the same configuration file. See [Multi-Project Configuration](#multi-project-configuration) for details.

Most users won't need a configuration file at all. Configuration becomes valuable primarily for complex testing scenarios or multi-API environments.

## Authentication Configuration

Schemathesis supports multiple authentication methods for API testing.

For simple authentication, use the top-level `[auth]` section:

```toml
[auth]
# Basic authentication
basic = { username = "${USERNAME}", password = "${PASSWORD}" }

# Bearer token authentication
bearer = "${TOKEN}"
```

The basic setting corresponds to the `--auth` CLI option, while bearer tokens can also be specified via CLI headers.

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
OAuth2 = { 
  client_id = "${CLIENT_ID}", 
  client_secret = "${CLIENT_SECRET}",
  scopes = ["read", "write"]
}
```

These settings map directly to the `securitySchemes` in your OpenAPI specification.

### Authentication Resolution

When multiple methods are specified, Schemathesis resolves authentication in the following order:

- CLI options (`--auth` or `--header`)
- Operation-specific headers
- Specification-specific authentication (`[auth.openapi]`)
- Generic authentication (top-level `[auth]`)

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

## Check Configuration

Schemathesis validates API responses with a series of checks that verify various aspects of your API's behavior—from server availability to schema conformance.

### Available Checks

Schemathesis includes the following checks (all enabled by default):

- **not_a_server_error**: Ensures the API doesn't return 5xx responses.
- **status_code_conformance**: Confirms status codes match the schema.
- **content_type_conformance**: Validates response content types against the schema.
- **response_schema_conformance**: Checks that response bodies conform to their defined schemas.
- **positive_data_acceptance**: Verifies that valid data is accepted.
- **negative_data_rejection**: Ensures that invalid data is rejected.
- **use_after_free**: Checks that resources are inaccessible after deletion.
- **ensure_resource_availability**: Verifies that resources are available post-creation.
- **ignored_auth**: Ensures authentication is properly enforced.

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
[operation."POST /users"]
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

- Operation-specific check configuration
- Global check configuration
- Default check behavior

This hierarchy lets you define global defaults while overriding settings for specific endpoints.

## Operation Targeting

Schemathesis lets you target specific API operations for custom configuration. Note that different specifications use different operation identifiers.

### Targeting by Exact Path

Specify an operation using its exact identifier:

```toml
# OpenAPI: HTTP method and path
[operation."GET /users"]
generation.max-examples = 200
request-timeout = 5.0

# GraphQL: type and field name
[operation."Query.getUser"]
generation.max-examples = 200
```

### Targeting with Regular Expressions

Select multiple operations matching a pattern:

```toml
# Match operations with paths containing "users"
[operation.regex."GET /users/.*"]
generation.max-examples = 150

# Match operations that modify users
[operation.regex."(POST|PUT|PATCH) /users.*"]
request-timeout = 3.0
```

!!! note "Regex patterns"
    Regex patterns work with schema path templates, not resolved URLs.

### Targeting by Tag

Apply settings to all operations with a specific tag:

```toml
[operation.tag."admin"]
enabled = false  # Skip all admin-tagged operations

[operation.tag."payment"]
generation.max-examples = 150
rate-limit = "20/s"
```

### Targeting with Expressions

For more precise control, Schemathesis supports targeting operations using JSONPath-like expressions that query specific fields within operation definitions:

```toml
[operation.expr."tags/0 == 'user'"]
generation.max-examples = 150

[operation.expr."operationId == null"]
enabled = false

[operation.expr."responses/200/description != 'Success'"]
checks.response_schema_conformance.enabled = false
```

Expressions follow the pattern `<json-pointer> <operator> <value>` where:

- `<json-pointer>` is a path to a field in the operation definition
- `<operator>` is either `==` (equals) or `!=` (not equals)
- `<value>` can be a string, number, boolean, null, array, or object

This is particularly useful for:

- Targeting operations based on description content
- Selecting operations with specific response codes
- Filtering by operation metadata or extension fields

#### Examples

```toml
# Target operations that include 'admin' in their summary
[operation.expr."summary == 'Admin operations'"]
enabled = false

# Target operations that handle file uploads
[operation.expr."requestBody/content/multipart\\/form-data != null"]
generation.max-examples = 50

# Target operations without tags
[operation.expr."tags == null"]
checks.response_schema_conformance.enabled = false
```

Expression-based targeting is particularly useful with complex API specifications where tag-based or path-based selection isn't specific enough.

### Operation Resolution

When multiple selectors match an endpoint, Schemathesis applies them in this order:

- Exact path selectors (highest precedence)
- Tag selectors
- Regex selectors (lowest precedence)

Within each category, more specific selectors override general ones.

## Phase-Specific Settings for Operations

Configure phase-specific settings within individual operations for fine-grained control over each phase's behavior:

```toml
[operation."GET /users"]
# Default settings for this operation
generation.max-examples = 100
request-timeout = 5.0

# Phase-specific overrides for this operation
[operation."GET /users".phases]
fuzzing.generation.max-examples = 200  # Increase examples for fuzzing
stateful.generation.max-examples = 30   # Reduce examples for stateful tests
```
### Resolution Order for Phase-Operation Settings

When both operation-level and phase-level settings are defined, Schemathesis applies them in the following order:

- Operation-specific phase settings (e.g., `[operation."GET /users".phases.fuzzing]`)
- Global phase settings (e.g., `[phases.fuzzing]`)
- Operation-level settings (e.g., `[operation."GET /users"]`)
- Global settings (top level)

This hierarchy allows you to set defaults at higher levels while overriding specific phases as needed.

## Parameter Overrides

Override parameter values at different levels to control test data.

### Operation-Specific Parameters

Set parameter values for specific operations:

```toml
[operation."GET /users/{user_id}"]
# Fixed value
parameters = { "user_id" = 42 }

# Multiple values for random selection
parameters = { "user_id" = [1, 42, 499] }

# Using an environment variable
parameters = { "user_id" = "${USER_ID}" }

# Disambiguate parameters with the same name
parameters = { "path.user_id" = 42, "query.user_id" = 100 }
```

### Global Parameter Overrides

Apply parameters across all operations:

```toml
[parameters]
"api_version" = "v2"
"limit" = 50
"offset" = 0
```

### Parameter Resolution Order

Schemathesis resolves parameter values in this order:

- Operation-specific values
- Global values
- Generated values from the schema

This cascading mechanism ensures that more specific settings override general ones.

### Parameter Type Disambiguation

When parameters share the same name, prefix them to indicate their location:

```toml
parameters = {
  "path.id" = 42,            # Path parameter
  "query.id" = 100,          # Query parameter
  "header.X-API-Version" = "2.0",  # Header parameter
  "cookie.session" = "${SESSION_ID}"  # Cookie parameter
}
```

### Using Parameter Arrays

Provide an array of values; Schemathesis will randomly select one per test case:

```toml
[operation."GET /users"]
parameters = { "role" = ["admin", "user", "guest"] }
```

This configuration distributes the specified roles across test cases.

## Multi-Project Configuration

Schemathesis lets you configure multiple API projects in one file—a handy feature for testing related APIs or different API versions.

### Defining Projects

Define projects in the `[projects]` section by matching the API's title:

```toml
[projects]
# Projects are matched by the API schema's info.title
payments = { title = "Payment Processing API" }
users = { title = "User Management Service" }
```

Schemathesis checks the `info.title` field of the API schema to apply the corresponding project settings.

### Project-Specific Settings

Override global defaults with project-specific settings:

```toml
[projects.payments]
base-url = "https://payments.example.com"
workers = 4
generation.max-examples = 200
hooks = "payment_hooks.py"

[projects.users]
base-url = "https://users.example.com"
workers = 2
```

### Project-Specific Operations

Customize operations within a project:

```toml
[projects.payments.operation."POST /payments"]
generation.max-examples = 80
headers = { "X-Idempotency-Key" = "${IDEMPOTENCY_KEY}" }

[projects.payments.operation.tag."critical"]
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
workers = "auto"

[projects]
# Define projects by API title
payments = { title = "Payment Processing API" }
users = { title = "User Management API" }

# Payments project settings
[projects.payments]
base-url = "https://payments.example.com"
generation.max-examples = 100

# Operations for payments
[projects.payments.operation."POST /payments"]
generation.max-examples = 200
parameters = { "amount" = [10, 100, 1000] }
checks.positive_data_acceptance.expected-statuses = [200, 201]

# Users project settings
[projects.users]
base-url = "https://users.example.com"
```

With this setup, each project uses its own settings while sharing a common configuration structure.

## Configuration Overrides

While the configuration file provides default settings, you can override them via CLI options.

### CLI Overrides

Command-line arguments take precedence over configuration file settings:

```bash
# Override the max-examples setting
schemathesis run --max-examples=300 http://api.example.com/openapi.json

# Override phases
schemathesis run --phases=examples,fuzzing http://api.example.com/openapi.json

# Override check settings
schemathesis run --checks=not_a_server_error http://api.example.com/openapi.json
```

Most configuration option can be overridden via the corresponding CLI flag.
