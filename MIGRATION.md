# Upgrading from v3 to v4

Schemathesis v4 is a major reimplementation of the core engine, Python API, and pytest integration. This rebuild enables new features that weren't possible in previous versions while simplifying the overall architecture.

Some functionality has been removed, either replaced with better alternatives or discontinued entirely.

The following sections cover migration for both major Schemathesis use cases: CLI and pytest integration.

## Command-Line Interface

### Removed

The following CLI options were removed without replacement:

- `--app`. Removed to simplify the core engine architecture.
- `-A` / `--auth-type`. HTTP Digest authentication was removed. Use `--auth` for HTTP Basic authentication.
- `--contrib-openapi-formats-uuid`. The `uuid` format is now enabled by default.
- `--code-sample-style`. Only cURL code samples are now supported.
- `--debug-output-file`.
- `--dry-run`. This functionality will be available via a separate CLI command for generating test data.
- `--experimental`. All experimental features have been stabilized.
- `--fixups`.
- `--force-schema-version`.
- Not useful Hypothesis options - `--hypothesis-{deadline,report-multiple-bugs,verbosity,no-phases}`. Tuning them was not that useful and v4 configures them in the way they don't interfere with the CLI output or testing process.
- `--show-trace`. Stack traces are now always included in error reports.
- `--validate-schema`. The output was based on JSON Schema validation errors and was confusing and not helpful most of the time. There are other tools that do this job way better that Schemathesis used to do.
- `--schemathesis-io-{token,url,telemetry}` and optional `API_NAME` argument - The Schemathesis.io service has been discontinued. The `--report` option now provides different reporting options.
- `--verbosity`. It didn't really do anything.

The following options have alternatives:

| Removed Option | Alternative |
|----------------|-----------------|
| `--endpoint` | `--include-path` / `--exclude-path` |
| `--method` | `--include-method` / `--exclude-method` |
| `--tag` | `--include-tag` / `--exclude-tag` |
| `--operation-id` | `--include-operation-id` / `--exclude-operation-id` |
| `--skip-deprecated-operations` | `--exclude-deprecated` |
| `--pre-run` | Use the `SCHEMATHESIS_HOOKS` environment variable instead |
