# Continuous Integration

This guide explains how to integrate Schemathesis into CI/CD pipelines for automated API testing. Automated tests catch API regressions early and help maintain consistent API quality across releases.

## General Integration Pattern

Schemathesis integration in CI follows this pattern:

1. Start the API service.
2. Wait for the API to become available.
3. Run Schemathesis against the API schema.
4. Process test results and generate reports.

## Schema Access Strategies

### URL-Based Schemas

For APIs that expose their schema through an endpoint:

```console
$ st run http://api-host:port/openapi.json
```

This approach ensures you're testing against the actual deployed schema but requires the API to be running.

### File-Based Schemas

For APIs with separately maintained schemas, run tests against a local schema file. Ensure the schema file is kept in sync with the API.

```console
$ st run ./path/to/openapi.json --url http://api-host:port
```

This approach lets you test against a local schema file by specifying the target URL, ensuring tests run against the intended API.

## Using Configuration Files

For CI environments, create a dedicated configuration file (`schemathesis.toml`) with your testing settings. Schemathesis automatically loads this file from the current directory or project root.

Since the config file supports environment variable substitution, you can use the same file across different environments by supplying different environment variables. To use a custom file, specify its path with `--config-file`.

```toml
[auth.openapi]
ApiKeyAuth = { value = "${API_KEY}" }

[checks]
response_schema_conformance.enabled = false

[reports.junit]
path = "${JUNIT_REPORT_PATH}"
```

Then in your CI workflow, simply reference this configuration:

```console
$ st --config-file my-config.toml run http://api-host:port/openapi.json
```

!!! info "Configuration Reference"

    See the [Configuration Guide](../using/configuration.md) for usage instructions and the [Configuration Reference](../reference/configuration.md) for all available options.

## Exit Codes

Schemathesis returns these exit codes that you should handle in your CI job:

- `0`: All tests passed
- `1`: Tests failed
- `2`: Invalid schema or configuration error

## Platform-Specific Integration

For detailed setup instructions on specific CI platforms:

- [GitHub Actions](./github-actions.md)
- [GitLab CI](./gitlab-ci.md)

Each platform guide includes concrete examples, authentication patterns, and troubleshooting advice specific to that environment.
