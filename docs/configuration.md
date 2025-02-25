# Configuration Guide

Schemathesis can be configured through a `schemathesis.toml` file, allowing you to customize how your API testing works.

## Configuration File Location

Schemathesis will look for configuration in the following locations, in order:

1. Path specified via `--config` CLI option: If provided, this file is used exclusively.
2. `schemathesis.toml` in the current directory: Schemathesis checks the current working directory.
3. `schemathesis.toml` in parent directories (up to the project root): The search continues upward through parent directories.

!!! note "Configuration Preference"
    Only one configuration file is used. Schemathesis does not merge settings from multiple configuration files. If no `schemathesis.toml` file is found, Schemathesis will use its built-in defaults.

## Why Use a Configuration File?

While Schemathesis works well without explicit configuration, using a configuration file offers several advantages:

- **Operation-Specific Settings**: Configure different behaviors for specific endpoints.
- **Validation Customization**: Define how response checks should apply validation.
- **Consistent Testing**: Share configuration across different environments and test runs.

!!! note "CLI and Python API Integration"
    While the configuration file sets default behavior, CLI options and the Python API can override any settings.

## Basic Structure

Schemathesis configuration uses the TOML format with a hierarchical structure. Global settings serve as defaults and can be overridden by more specific operation or project settings.

```toml
# Global settings
base-url = "https://api.example.com"
max-examples = 100

# Operation-specific settings
[operation."GET /users"]
max-examples = 200
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

This allows you to maintain a single configuration file that works across different environments (development, staging, production) by changing environment variables rather than the configuration itself.

!!! tip "Multi-Project Support"
    Schemathesis supports multi-project configurations, enabling you to define separate settings for different APIs within the same configuration file. See [Multi-Project Support](#multi-project-support) for details.

Most users won't need a configuration file at all. Configuration becomes valuable primarily for complex testing scenarios or multi-API environments.

## Multi-Project Support
