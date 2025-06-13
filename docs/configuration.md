# Configuration

Most Schemathesis usage works without configuration. Use a config file for authentication, adjusting test generation, or customizing behavior for specific API operations.

## Quick Start

### Creating Your First Config File

Create `schemathesis.toml` in your project directory:

```toml
# Run more tests for better coverage
generation.max-examples = 500

# Increase timeout for slow APIs
request-timeout = 10.0

# Basic authentication
[auth]
basic = { username = "${USERNAME}", password = "${PASSWORD}" }
```

Set environment variables:
```bash
export USERNAME="your_username"
export PASSWORD="your_password"
```

Run Schemathesis - it automatically finds and uses the config:
```bash
schemathesis run https://api.example.com/openapi.json
```

### Config File Location

Schemathesis looks for `schemathesis.toml` in:

1. Current directory
2. Parent directories (up to project root)
3. Path specified with `--config-file`

## Common Configuration Scenarios

### Authentication

**API Key in Headers:**
```toml
headers = { "X-API-Key" = "${API_KEY}" }
```

**Bearer Token:**
```toml
headers = { Authorization = "Bearer ${TOKEN}" }
```

**Basic Authentication:**
```toml
[auth]
basic = { username = "${USERNAME}", password = "${PASSWORD}" }
```

**Different Auth for Specific Operations:**
```toml
# Default auth for most operations
headers = { Authorization = "Bearer ${TOKEN}" }

# Special auth for admin operations
[[operations]]
include-tag = "admin"
headers = { Authorization = "Bearer ${ADMIN_TOKEN}" }
```

### Adjusting Test Generation

**Run More Tests (Better Coverage, Slower):**
```toml
generation.max-examples = 1000
```

**Run Fewer Tests (Faster Feedback):**
```toml
generation.max-examples = 50
```

**Thorough Testing for Important Endpoints:**
```toml
# Default: fast testing
generation.max-examples = 100

# Critical endpoints: more testing
[[operations]]
include-path-regex = "/(payments|users)/"
generation.max-examples = 500
```

### Handling Slow APIs

**Increase Timeouts:**
```toml
request-timeout = 30.0  # 30 seconds
```

**Different Timeouts by Operation:**
```toml
# Default timeout
request-timeout = 5.0

# Slow operations need more time
[[operations]]
include-tag = "slow"
request-timeout = 30.0
```

### Environment-Specific Configuration

**Different Base URLs per Environment:**
```toml
base-url = "https://${ENVIRONMENT}.api.example.com"
```

```bash
# Development
export ENVIRONMENT="dev"

# Production  
export ENVIRONMENT="prod"
```

**Skip Problematic Endpoints in Development:**
```toml
[[operations]]
include-path = "/billing/charge"
enabled = false
```

## Advanced Configuration

**Advanced Filtering with Expressions:**
```toml
# Operations without operationId
[[operations]]
include-by = "operationId == null"
enabled = false

# Operations with specific response descriptions
[[operations]]
exclude-by = "responses/200/description != 'Success'"
checks.response_schema_conformance.enabled = false
```

### Test Phase Control

**Disable Specific Testing Phases:**
```toml
[phases]
# Skip stateful testing for faster runs
stateful.enabled = false

# OR: Reduce examples for stateful tests
stateful.generation.max-examples = 10
```

**Phase-Specific Settings per Operation:**
```toml
[[operations]]
include-name = "POST /orders"
# More examples for fuzzing
phases.fuzzing.generation.max-examples = 500
# Fewer for stateful testing
phases.stateful.generation.max-examples = 20
```

### Multi-Project Configuration

**Testing Multiple APIs:**
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

### Custom Check Configuration

**Disable Problematic Checks:**
```toml
[checks]
# Disable globally
content_type_conformance.enabled = false

# Different expectations for specific operations
[[operations]]
include-name = "POST /uploads"
checks.positive_data_acceptance.expected-statuses = [200, 201, 202]
```

**Enable Only Specific Checks:**
```toml
[checks]
enabled = false
# Only check for server errors and schema compliance
not_a_server_error.enabled = true
response_schema_conformance.enabled = true
```

## What's next?

For a complete list of settings, see the [Configuration Reference](reference/configuration.md).
