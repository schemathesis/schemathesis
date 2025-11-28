# Configuration

You don’t need a config file to get started. Add one when you need auth, different test volumes, per-operation overrides, or environment-specific settings.

## Quick Start

### Create your first config file

Create `schemathesis.toml` in your project directory:

```toml
# Run more tests for better coverage
generation.max-examples = 500

# Increase timeout for slow APIs
request-timeout = 10.0  # seconds

# Basic authentication
[auth]
basic = { username = "${USERNAME}", password = "${PASSWORD}" }
```

!!! note ""
    Variables like `${USERNAME}` are read from your environment when Schemathesis loads the config.

Set environment variables:
```bash
export USERNAME="your_username"
export PASSWORD="your_password"
```

Run Schemathesis. It automatically loads `schemathesis.toml`.
```bash
schemathesis run https://api.example.com/openapi.json
```

### Config file location

Schemathesis looks for `schemathesis.toml` in:

1. Current directory
2. Parent directories (up to repository root)

To use a config file from a different location, pass `--config-file` before the `run` command:

```bash
schemathesis --config-file /path/to/config.toml run https://api.example.com/openapi.json
```

## Common configuration scenarios

### Authentication

API key in a header:
```toml
headers = { "X-API-Key" = "${API_KEY}" }
```

Bearer token in the `Authorization` header:
```toml
headers = { Authorization = "Bearer ${TOKEN}" }
```

Basic auth:
```toml
[auth]
basic = { username = "${USERNAME}", password = "${PASSWORD}" }
```

Different auth for specific operations:
```toml
# Default auth for most operations
headers = { Authorization = "Bearer ${TOKEN}" }

# Special auth for admin operations
[[operations]]
include-tag = "admin"
headers = { Authorization = "Bearer ${ADMIN_TOKEN}" }
```

### Adjusting test generation

More tests (better coverage, slower):
```toml
generation.max-examples = 1000
```

Fewer tests (faster feedback):
```toml
generation.max-examples = 50
```

Deeper testing for important API operations:
```toml
# Default: fast feedback
generation.max-examples = 100

# Important operations: more depth
[[operations]]
include-path-regex = "/(payments|users)/"
generation.max-examples = 500
```

### Slow APIs

If you see timeouts or flaky failures, increase per-operation timeouts.

Increase timeouts:
```toml
request-timeout = 30.0  # seconds
```

Different timeouts by operation:
```toml
# Default timeout
request-timeout = 5.0

# Give slow operations more time
[[operations]]
include-tag = "slow"
request-timeout = 30.0
```

### Environment-specific configuration

Different base URLs per environment:
```toml
# Switch base URL by environment variable
base-url = "https://${ENVIRONMENT}.api.example.com"
```

```bash
# Development
export ENVIRONMENT="dev"

# Production
export ENVIRONMENT="prod"
```

Skip problematic endpoints in development:
```toml
[[operations]]
include-path = "/billing/charge"
enabled = false
```

## Advanced configuration

Advanced filtering with expressions:
```toml
# Ignore operations that lack operationId
[[operations]]
include-by = "operationId == null"
enabled = false

# Operations with specific response descriptions
[[operations]]
exclude-by = "responses/200/description != 'Success'"
checks.response_schema_conformance.enabled = false
```

### Test phase control

Disable specific testing phases:
```toml
[phases]
# Skip stateful testing for faster runs
stateful.enabled = false

# OR: Reduce examples for stateful tests
stateful.generation.max-examples = 10
```

Phase-specific settings per operation:
```toml
[[operations]]
include-name = "POST /orders"
# More examples for fuzzing
phases.fuzzing.generation.max-examples = 500
# Fewer for stateful testing
phases.stateful.generation.max-examples = 20
```

### Multi-project configuration

Top-level options act as defaults for all projects. Each `[[project]]` can override them.

```toml
# Global defaults
generation.max-examples = 100

[[project]]
title = "Payment API"
base-url = "https://payments.example.com"
generation.max-examples = 200

[[project.operations]]
include-tag = "critical"
generation.max-examples = 500

[[project]]
title = "User API" 
base-url = "https://users.example.com"
request-timeout = 2.0
```

### Custom check configuration

Disable noisy checks:
```toml
[checks]
# Disable globally
content_type_conformance.enabled = false

# Different expectations for specific operations
[[operations]]
include-name = "POST /uploads"
checks.positive_data_acceptance.expected-statuses = [200, 201, 202]
```

Enable only specific checks:
```toml
[checks]
enabled = false
# Only check for server errors and schema compliance
not_a_server_error.enabled = true
response_schema_conformance.enabled = true
```

## What's next?

For a complete list of settings, see the [Configuration Reference](reference/configuration.md).
