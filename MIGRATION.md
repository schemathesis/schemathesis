# Upgrading from v3 to v4

Schemathesis v4 is a major reimplementation of the core engine, Python API, and pytest integration. This rebuild enables new features that weren't possible in previous versions while simplifying the overall architecture.

Some functionality has been removed, either replaced with better alternatives or discontinued entirely.

The following sections cover migration for both major Schemathesis use cases: CLI and pytest integration.

The major behavior changes include:

- Running all data generation modes by default
- Running all available checks by default
- Measuring `--request-timeout` and `--max-response-time` in seconds rather than milliseconds

## Command-Line Interface

### Removed

There are a few commands that were removed:

- `schemathesis auth` & `schemathesis upload`. Removed as Schemathesis.io has been discontinued.

`schemathesis replay` still exists, but replays something different. In v3 it replayed a VCR cassette recorded with `--store-network-log`. In v4, `st replay` re-runs the crash files that `st run` and `st fuzz` record automatically for failing cases:

```bash
st replay                    # replay every recorded crash for the project
st replay Xt9Kp2             # replay a single case by ID
```

To replay a v3-style cassette, re-run the operations from the schema instead. See [Replaying Failures](https://schemathesis.readthedocs.io/en/stable/guides/crash-reproduction/).

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
- `--schemathesis-io-{token,url,telemetry}`, `--hosts-file` and optional `API_NAME` argument - The Schemathesis.io service has been discontinued. The `--report` option now provides different reporting options.
- `--verbosity`. It didn't really do anything.

The following options have alternatives:

| Removed Option | Alternative |
|----------------|-----------------|
| `-b` / `--base-url` | `-u` / `--url` |
| `--cassette-format` | See `--report` below |
| `--cassette-preserve-exact-body-bytes` | `--report-preserve-bytes` |
| `--contrib-openapi-fill-missing-examples` | Use `[phases.examples]` configuration table instead |
| `--contrib-unique-data` | `--generation-unique-inputs` |
| `--data-generation-methods` | `--mode` |
| `--endpoint` | `--include-path` / `--exclude-path` |
| `--exitfirst` | Use `--max-failures=1` instead |
| `--experimental-no-failfast` | `--continue-on-failure` |
| `--hypothesis-database` | `--generation-database` |
| `--hypothesis-derandomize` | `--generation-deterministic` |
| `--hypothesis-max-examples` | `--max-examples` |
| `--hypothesis-phases` / `--hypothesis-no-phases` | See `--phases` below |
| `--hypothesis-seed` | `--seed` |
| `--hypothesis-suppress-health-check` | `--suppress-health-check` |
| `--junit-xml` | `--report=junit` |
| `--method` | `--include-method` / `--exclude-method` |
| `--operation-id` | `--include-operation-id` / `--exclude-operation-id` |
| `--pre-run` | Use the `SCHEMATHESIS_HOOKS` environment variable instead |
| `--request-proxy` | `--proxy` |
| `--request-tls-verify` | `--tls-verify` |
| `--sanitize-output` | `--output-sanitize` |
| `--set-{header,cookie,path,query}` | Use parameter overrides in the configuration file instead |
| `--skip-deprecated-operations` | `--exclude-deprecated` |
| `--stateful` | `--phases=stateful` |
| `--store-network-log` / `--cassette-path` | `--report-vcr-path` |
| `--tag` | `--include-tag` / `--exclude-tag` |
| `--targets` | `--generation-maximize` |

### Changed

`--request-timeout` and `--max-response-time` are measured in seconds. In v3 both took milliseconds, and both still accept the old values without an error:

```bash
# v3: 5 seconds. v4: 5000 seconds.
schemathesis run --request-timeout=5000 <SCHEMA_URL>
```

Divide any value carried over from v3 by 1000. The same applies to `request-timeout` in `schemathesis.toml`.

### Reports

The `--report` CLI option now controls the report formats generated by Schemathesis:

```bash
schemathesis run --report=vcr,har <SCHEMA_URL>
```

### Test Phases

The new `--phases` option now controls what test phases are executed by Schemathesis:

- `examples` (formerly `explicit`): Runs examples specified in the API schema
- `fuzzing` (formerly `generate`): Testing with randomly generated test cases
- `coverage`: Deterministic testing of schema constraints and boundary values
- `stateful`: Chains real API calls, passing data from response to request

Control over other phases have been replaced by different CLI options:

- `reuse` and `shrink` remain enabled by default. Disable via `--generation-database=none` and `--no-shrink`.
- `target` phase available via `--generation-maximize=<METRIC>`

```bash
schemathesis run --phases=examples,coverage <SCHEMA_URL>
```

## Python API

### Removed

- `Schema.add_link`. Adjust your API schema manually instead.
- `SCHEMATHESIS_VERSION`. Use `__version__` instead.
- `schemathesis.register_check`. Use `schemathesis.check` instead.
- `schemathesis.register_target`. Use `schemathesis.metric` instead.
- `schemathesis.register_string_format`. Use `schemathesis.openapi.format` instead.
- `schemathesis.GenericResponse`. Use `schemathesis.Response` instead.
- `schemathesis.from_aiohttp`. Support for `aiohttp` has been removed.
- `schemathesis.GenerationConfig`. Use config file instead.
- `schemathesis.HeaderConfig`. Use config file instead.
- `schemathesis.runner`. The Schemathesis engine is not a public API anymore.
- `add_case` hook.
- `process_call_kwargs` hook. Use `before_call` instead.
- `SCHEMA_ANALYSIS` experimental feature. Parts of this feature will be open sourced in the future.
- `as_requests_kwargs` / `as_werkzeug_kwargs`. Use `as_transport_kwargs` instead.

Schema loaders are moved to namespaces:

| v3 | v4 |
| -- | -- |
| `schemathesis.from_asgi` | `schemathesis.openapi.from_asgi` |
| `schemathesis.from_dict` | `schemathesis.openapi.from_dict` |
| `schemathesis.from_file` | `schemathesis.openapi.from_file` |
| `schemathesis.from_path` | `schemathesis.openapi.from_path` |
| `schemathesis.from_wsgi` | `schemathesis.openapi.from_wsgi` |
| `schemathesis.from_uri` | `schemathesis.openapi.from_url` |
| `schemathesis.from_pytest_fixture` | `schemathesis.pytest.from_fixture` |

### Renamed

| v3 | v4 |
| -- | -- |
| `schemathesis.DataGenerationMethod` | `schemathesis.GenerationMode` |
| `schemathesis.target` | `schemathesis.metric` |
| `schemathesis.TargetContext` | `schemathesis.MetricContext` |

### Changed

- `AuthProvider.get` now always accepts two arguments (`case`, `ctx`).
- `@schemathesis.serializer` now expects a function that accepts `ctx` & `value` and returns bytes.
