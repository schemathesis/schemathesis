# Changelog

## [Unreleased](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.8...HEAD) - TBD

## [4.0.0-alpha.8](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.7...v4.0.0-alpha.8) - 2025-04-05

### :rocket: Added

- Additional `base_url` validation for `BaseSchema.configure` method.
- Temporary `SCHEMATHESIS_DISABLE_COVERAGE` environment variable so the coverage phase can be disabled for the pytest integration.

### :wrench: Changed

- **INTERNAL**: Use `str` in enums.

### :bug: Fixed

- Do not mutate `pattern` keywords in response schema validation. [#2749](https://github.com/schemathesis/schemathesis/issues/2749)
- Support for async schema fixtures in `schemathesis.pytest.from_fixture`.
- Generate empty header values during the coverage phase.
- Correctly handle second CTRL-C when waiting for worker threads.
- Generate negative values for `minItems` & `maxItems` during the coverage phase.
- Use `default` value as valid input during the coverage phase.
- Unknown `multipart/form-data` fields not added to the final test case payload.
- Generate a non-empty string for negative testing of `type: string` enums during the coverage phase.
- Export `HookContext` & `BaseSchema` as a part of the public Python API.

## [4.0.0-alpha.7](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.6...v4.0.0-alpha.7) - 2025-03-21

### :rocket: Added

-   `--experimental-coverage-unspecified-methods`
    CLI option that accept a comma separated list of HTTP methods to use
    when generating test cases with methods not specified in the API
    during the **coverage** phase.

## [4.0.0-alpha.6](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.5...v4.0.0-alpha.6) - 2025-03-20

### :bug: Fixed

-   Incorrect quantifiers merging for patterns involving single-element
    set of characters like `[+]`.
-   Internal error in the coverage phase due to incorrect example value
    extraction.
-   If the generated query parameter value is an empty object, send it
    as an empty string.

## [4.0.0-alpha.5](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.4...v4.0.0-alpha.5) - 2025-02-23

This release introduces a new phase management system for CLI that
simplifies test execution control and separates unit testing into
different stages.

Phase configuration changes:

-   `examples` (formerly `explicit`): Runs examples specified in the API
    schema
-   `fuzzing` (formerly `generate`): Testing with randomly generated
    test cases
-   `coverage`: Deterministic testing of schema constraints and boundary
    values
-   `reuse` and `shrink` remain enabled by default. Disable via
    `--generation-database=none` and `--no-shrink`.
-   `target` phase available via `--generation-maximize=<METRIC>`

**NOTE**: Pytest integration does not currently have a way to disable
the coverage phase. Python API support is planned for future releases.

### :rocket: Added

-   Warning for 4xx-only operations during unit tests to help identify
    configuration issues.

### :wrench: Changed

-   Separate `coverage` and `examples` into independent testing phases.
-   Replace `--hypothesis-phases` with `--phases`.
-   Do not report `unsupported_method` failure if the API returned HTTP
    200 on OPTIONS request.
-   Add HTTP 406 status to the list of status codes that are expected
    for negative test cases.
-   The experimental `--experimental-no-failfast` option has been
    stabilized as `--continue-on-failure`. This option ensures all test
    cases within a scenario are executed, even if failures occur.

### :bug: Fixed

-   **Coverage phase**: Missing test case metadata leading to some
    failures not being detected.
-   **Coverage phase**: Missing parameter overrides.
-   **Coverage phase**: Custom auth implementation not applied to test
    cases.
-   **Coverage phase**: Not applying parameter serialization rules
    during test generation.
-   Incorrect quantifiers merging for some patterns. [#2732](https://github.com/schemathesis/schemathesis/issues/2732)
-   Showing `1 errors` instead of `1 error` in CLI output.

### :fire: Removed

-   `--hypothesis-no-phases`.
-   `--exitfirst`. Use `--max-failures=1` instead.

## [4.0.0-alpha.4](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.3...v4.0.0-alpha.4) - 2025-02-03

### :rocket: Added

-   `--report` unified reporting system with multiple format support.
-   `--report-dir` for centralized report storage.
-   Display Open API link definition & extraction errors in CLI output.

### :wrench: Changed

-   Rename `--generation-optimize` to `--generation-maximize`
-   Rename `--generation-mode` to `-m/--mode`
-   Rename `--generation-max-examples` to `-n/--max-examples`
-   Rename `--junit-xml` to `--report=junit`
-   Rename `--cassette-*` options to `--report=vcr/har` with
    format-specific paths
-   Replace `shrink` in `--hypothesis-phases` with a separate
    `--no-shrink` option
-   Simplify help messages for filtering options.
-   Add display of selected operations count in CLI.
-   Improve state machine generation by prioritizing reliable API entry
    points over random operations.
-   Make operation filtering independent of API base path for more
    predictable behavior.
-   Improve error message for malformed JSON responses.
-   Return `UNRESOLVABLE` sentinel instead of an empty string when Open
    API runtime expressions can't be evaluated (e.g., when
    `$response.body#/id` is not found)
-   **BREAKING**: The `validate_response` method in state machines now
    accepts the same keyword arguments as `call`. If you've overridden
    this method, update its signature to include `**kwargs`.

### :bug: Fixed

-   Handling of multiple API links pointing to the same operation with
    different parameters.
-   **CLI**: Make exact method filters case-insensitive.
-   Internal error in coverage phase when a parameter mixes keywords for
    different types.
-   Skip irrelevant checks for "Unspecified HTTP method" coverage
    scenarios.
-   Handle `verify=False` properly when specified via `get_call_kwargs`
    on a state machine. [#2713](https://github.com/schemathesis/schemathesis/issues/2713)
-   Preserve test data when unit tests are interrupted via CTRL-C.

### :fire: Removed

-   `--cassette-format` (replaced by `--report`).

## [4.0.0-alpha.3](https://github.com/schemathesis/schemathesis/compare/v4.0.0-alpha.2...v4.0.0-alpha.3) - 2025-01-27

### :rocket: Added

-   Add `LoadingStarted` & `LoadingFinished` to the public API.
-   Display the random generator seed in CLI output.

### :wrench: Changed

-   Improve control over API calls in stateful testing to make test
    scenarios more diverse.
-   Improve error message in CLI when the schema has no Open API links.
-   Improve error message in CLI when the schema contains incorrect Open
    API links.
-   Display the number of covered, selected, and total Open API links in
    stateful testing output.
-   Vary random seed in subsequent state machine re-runs to avoid
    repeating all previous sequences.

### :bug: Fixed

-   Handling of complex regex patterns with multiple quantifiers to
    respect length constraints during test generation.
-   Internal error during the coverage phase if negated parameter has no
    `type`.
-   Internal error in CLI if run with `PYTHONIOENCODING` environment
    variable that is not `utf8`.
-   Overriding of multiple incoming links defined for the same status
    code in OpenAPI specification.
-   Support for API links where operations can form a cycle (e.g.,
    DELETE -\> POST -\> DELETE).
-   Incorrect Open API link selection when target operations are
    filtered out.
-   False positive in the `ensure_resource_availability` check.
-   Calculating the number of Open API links selected for testing.

## [4.0.0-alpha.2](https://github.com/schemathesis/schemathesis/compare/v4.0.0a1...v4.0.0-alpha.2) - 2025-01-20

### :rocket: Added

-   Improved visibility into Open API link extraction success/failure
    status [#823](https://github.com/schemathesis/schemathesis/issues/823)

### :wrench: Changed

-   Unified test progress display with multi-spinner interface and
    single progress bar [#2372](https://github.com/schemathesis/schemathesis/issues/2372)
-   Optimized stateful testing by generating only required test data for
    Open API links.
-   Cleaner display of schema loading errors.

### :bug: Fixed

-   More accurate exception deduplication based on source location
    instead of messages.
-   Stricter validation of `--include-*` and `--exclude-*` CLI options.

## [4.0.0a1](https://github.com/schemathesis/schemathesis/compare/v3.39.6...v4.0.0a1) - 2025-01-15

I'm releasing Schemathesis 4.0.0a1 - the biggest change in the project's
history. I've rewritten major parts of the core engine, Python API, and
pytest integration from scratch to enable features that were impossible
to implement before. While this means removing some functionality
temporarily, it was necessary to clean up four years of accumulated
hacks and create a more solid foundation.

This is an alpha release - expect breaking changes and missing features.
If you're using Schemathesis in production, stick with 3.x for now. The
documentation is outdated, and I'll update it as the new architecture
stabilizes.

I'd really appreciate your feedback at [this GitHub
Discussion](https://github.com/schemathesis/schemathesis/discussions/2677) -
it will help shape the path to stable 4.0. A detailed migration guide
and complete changelog will follow.

### :rocket: Added

-   New test phases system with `--phases`
    CLI option to control unit & stateful testing.

### :racing_car: Performance

-   Up to 3x faster test execution.
-   Up to 15x lower memory usage.

### :wrench: Changed

-   Schema loaders reorganized with namespaces:
    -   `schemathesis.from_uri` → `schemathesis.openapi.from_url`
    -   `schemathesis.from_pytest_fixture` →
        `schemathesis.pytest.from_fixture`
-   Response handling: Custom checks now receive Schemathesis'
    `Response` class instead of `requests.Response`.
-   Payload serialization: Decorators per transport replace single class
    implementation.
-   CLI: Updated header & summary style.
-   Sanitization: Direct arguments in
    `schemathesis.sanitization.configure` instead of `Config` instance.

**Renamed CLI Options**

-   `--data-generation-methods` →
    `--generation-mode`
-   `--targets` →
    `--generation-optimize`
-   `--hypothesis-derandomize` →
    `--generation-deterministic`
-   `--hypothesis-database` →
    `--generation-database`
-   `--hypothesis-seed` →
    `--generation-seed`
-   `--contrib-unique-data` →
    `--generation-unique-inputs`
-   `--hypothesis-max-examples` →
    `--generation-max-examples`
-   `--sanitize-output` →
    `--output-sanitize`
-   `--hypothesis-suppress-health-check` →
    `--suppress-health-check`

### :fire: Removed

-   `aiohttp` integration.
-   Old-style stateful runner (new one is now default).
-   Schemathesis.io integration & `--report` option (local HTML reports
    coming later).
-   FastAPI fixups.
-   Python code samples (only cURL now).
-   Python 3.8 support.
-   Support for `pytest<7.0`.
-   CLI Options: `--endpoint`, `--method`, `--tag`, `--operation-id`,
    `--skip-deprecated-operations`, `--show-trace`,
    `--debug-output-file`, `--hypothesis-deadline`,
    `--hypothesis-report-multiple-bugs`, `--hypothesis-verbosity`,
    `--store-network-log`, `--pre-run`, `--dry-run`,
    `--contrib-openapi-formats-uuid`, `--validate-schema`.
-   Most loader configuration moved to `schema.configure` method.
-   `add_case` hook.
-   `schemathesis.contrib.unique_data`.
-   Single argument `AuthProvider.get`.
-   `schemathesis.runner.prepare` (use
    `schemathesis.engine.from_schema`).
-   `schemathesis replay` command.
-   Stateful testing summary (coming later).
-   `SCHEMA_ANALYSIS` experimental feature.

## [3.39.9](https://github.com/schemathesis/schemathesis/compare/v3.39.8...v3.39.9) - 2025-02-01

### :bug: Fixed

-   Internal error in coverage phase when a parameter is mixing keywords
    for different types.
-   Do not run irrelevant checks on "Unspecified HTTP method" type of
    coverage scenarios.
-   Ignoring `verify=False` when specified via `get_call_kwargs` on a
    state machine. [#2713](https://github.com/schemathesis/schemathesis/issues/2713)

### :wrench: Changed

-   Slightly improve the error message on malformed JSON.

## [3.39.8](https://github.com/schemathesis/schemathesis/compare/v3.39.7...v3.39.8) - 2025-01-25

### :bug: Fixed

-   Handling of complex regex patterns with multiple quantifiers to
    respect length constraints during test generation.
-   Internal error during the coverage phase if negated parameter has no
    `type`.

## [3.39.7](https://github.com/schemathesis/schemathesis/compare/v3.39.6...v3.39.7) - 2025-01-16

### :wrench: Changed

-   Rebuild Docker `stable` with the v3 branch.

## [3.39.6](https://github.com/schemathesis/schemathesis/compare/v3.39.5...v3.39.6) - 2025-01-12

### :wrench: Changed

-   Add HTTP 428 status to the allowed status list of the
    `negative_data_rejection` check. [#2669](https://github.com/schemathesis/schemathesis/issues/2669)

## [3.39.5](https://github.com/schemathesis/schemathesis/compare/v3.39.4...v3.39.5) - 2024-12-27

### :rocket: Added

-   Experimental `unsupported_method` check.

### :wrench: Changed

-   Always expect HTTP 401 for the `Authorization` header in the
    experimental `missing_required_header` check.
-   Do not make HEAD request for "Unspecified HTTP method".

## [3.39.4](https://github.com/schemathesis/schemathesis/compare/v3.39.3...v3.39.4) - 2024-12-26

### :bug: Fixed

-   `TypeError` on extracting explicit examples.

## [3.39.3](https://github.com/schemathesis/schemathesis/compare/v3.39.2...v3.39.3) - 2024-12-24

### :bug: Fixed

-   Code sample containing incorrect HTTP method for the
    `Unspecified HTTP method` case in the coverage phase.
-   `TypeError` on some `x-www-form-urlencoded` payloads during the
    coverage phase.

## [3.39.2](https://github.com/schemathesis/schemathesis/compare/v3.39.1...v3.39.2) - 2024-12-23

### :wrench: Changed

-   Update upper bound on `pytest-subtests` to `<0.15.0`.
-   Adjust JUnit XML output so it is properly displayed by Jenkins.

### :bug: Fixed

-   Do not report 5XX responses in `use_after_free` as they don't
    indicate the presence of the previously deleted resource.
-   Deduplicate test cases in JUnit XML report.

### :racing_car: Performance

-   Faster iteration over API operations.

## [3.39.1](https://github.com/schemathesis/schemathesis/compare/v3.39.0...v3.39.1) - 2024-12-17

### :bug: Fixed

-   False positive in the `ensure_resource_availability` check.
-   XML serialization no longer produces duplicate attributes.

## [3.39.0](https://github.com/schemathesis/schemathesis/compare/v3.38.10...v3.39.0) - 2024-12-16

### :rocket: Added

-   `--experimental-no-failfast` CLI option to make Schemathesis
    continue testing an API operation after a failure is found.

### :bug: Fixed

-   Avoid writing unbounded prefixes in XML serialization.

### :wrench: Changed

-   Escape XML entities instead of rejecting invalid ones.

## [3.38.10](https://github.com/schemathesis/schemathesis/compare/v3.38.9...v3.38.10) - 2024-12-11

### :bug: Fixed

-   Ignored request-related configuration inside the `ignored_auth`
    check. [#2613](https://github.com/schemathesis/schemathesis/issues/2613)

## [3.38.9](https://github.com/schemathesis/schemathesis/compare/v3.38.8...v3.38.9) - 2024-12-02

### :bug: Fixed

-   `UnicodeEncodeError` when sending a request during the coverage
    phase.
-   Duplicated test cases for missing required headers during the
    coverage phase.

## [3.38.8](https://github.com/schemathesis/schemathesis/compare/v3.38.7...v3.38.8) - 2024-11-28

### :bug: Fixed

-   `UnicodeEncodeError` when sending a request during the coverage
    phase.
-   Duplicated test cases for missing required headers during the
    coverage phase.
-   Generating positive test cases when they are explicitly excluded via
    configuration during the coverage phase.

## [3.38.7](https://github.com/schemathesis/schemathesis/compare/v3.38.6...v3.38.7) - 2024-11-16

### :rocket: Added

-   Generating duplicate query parameters during the coverage phase.
-   Generating cases with arbitrary HTTP methods during the coverage
    phase.

### :bug: Fixed

-   Not sending negated query parameters in some cases during the
    coverage phase.
-   Incorrect `data_generation_method` reported during the coverage
    phase in some cases.

## [3.38.6](https://github.com/schemathesis/schemathesis/compare/v3.38.5...v3.38.6) - 2024-11-12

### :rocket: Added

-   Support arrays for headers & path parameters during the coverage
    phase.

### :wrench: Changed

-   Make the `ignored_auth` stricter by always checking for the 401
    status exactly instead of any non-200.

### :bug: Fixed

-   Missed generating booleans in some cases during the coverage phase.
-   Populate `meta.parameter` in more cases during the coverage phase.
-   Incorrect quantifiers merging for some regex patterns.

## [3.38.5](https://github.com/schemathesis/schemathesis/compare/v3.38.4...v3.38.5) - 2024-10-30

### :bug: Fixed

-   Compatibility with Hypothesis \> 6.115.6. [#2565](https://github.com/schemathesis/schemathesis/issues/2565)

## [3.38.4](https://github.com/schemathesis/schemathesis/compare/v3.38.3...v3.38.4) - 2024-10-29

### :wrench: Changed

-   Generate more negative combinations during the coverage phase.

## [3.38.3](https://github.com/schemathesis/schemathesis/compare/v3.38.2...v3.38.3) - 2024-10-26

### :wrench: Changed

-   Generate more negative combinations during the coverage phase.
-   Ensure text description is always present for test cases in the
    coverage phase.

## [3.38.2](https://github.com/schemathesis/schemathesis/compare/v3.38.1...v3.38.2) - 2024-10-22

### :bug: Fixed

-   Internal error on generating missed required path parameter during
    the coverage phase.

## [3.38.1](https://github.com/schemathesis/schemathesis/compare/v3.38.0...v3.38.1) - 2024-10-21

### :rocket: Added

-   Generating test cases with missing required parameters during the
    coverage phase.
-   Store information about what parameter is mutated during the
    coverage phase.

### :wrench: Changed

-   Adjust the `negative_data_rejection` config to include fewer 4XX
    status codes (400, 401, 403, 404, 422).

## [3.38.0](https://github.com/schemathesis/schemathesis/compare/v3.37.1...v3.38.0) - 2024-10-21

### :rocket: Added

-   Support negative cases for `items` and `patternProperties` during
    the coverage phase.
-   Location information for all negative values generated at the
    coverage phase.
-   Python 3.13 support.

### :wrench: Changed

-   Ensure `minLength` & `maxLength` are taken into account when
    generating negative cases with `pattern` during the coverage phase.

### :bug: Fixed

-   Passing `additional_checks` & `excluded_checks` to
    `Case.call_and_validate`.
-   Not generating some negative patterns during the coverage phase.
-   Internal error on unsupported regex in the coverage phase.
-   False positive in `ignored_auth` if auth is provided via
    `--set-query` or `--set-cookie`.
-   `ignored_auth` not working under `pytest`.

### :racing_car: Performance

-   Major speedup for the coverage phase.

## [3.37.1](https://github.com/schemathesis/schemathesis/compare/v3.37.0...v3.37.1) - 2024-10-17

### :bug: Fixed

-   Performance regression caused by adjusted pretty-printing logic in
    `Hypothesis`. [#2507](https://github.com/schemathesis/schemathesis/issues/2507)

## [3.37.0](https://github.com/schemathesis/schemathesis/compare/v3.36.4...v3.37.0) - 2024-10-09

### :rocket: Added

-   Support for `pytest-subtests` up to `0.14`.
-   Experimental "Positive Data Acceptance" check to verify that
    schema-conforming data receives 2xx status responses. Enable with
    `--experimental=positive_data_acceptance`
-   Experimental CLI options to configure the `negative_data_rejection`
    check.
-   More negative string combinations with patterns in the coverage
    phase.

### :bug: Fixed

-   Internal error in conditional hooks.
-   Negative test cases for patterns in the coverage phase.

## [3.36.4](https://github.com/schemathesis/schemathesis/compare/v3.36.3...v3.36.4) - 2024-10-05

### :bug: Fixed

-   False positive for `ignored_auth` when used in stateful test runner. [#2482](https://github.com/schemathesis/schemathesis/issues/2482)
-   Open Api 3.1 spec using `$ref` in a path is incorrectly validated as invalid. [#2484](https://github.com/schemathesis/schemathesis/issues/2484)
-   Properly serialize `seed` in cassettes if `--hypothesis-derandomize` is present.

### :racing_car: Performance

-   Improvements for the coverage phase.

## [3.36.3](https://github.com/schemathesis/schemathesis/compare/v3.36.2...v3.36.3) - 2024-09-29

### :rocket: Added

-   Meta information about generated data in the coverage phase.

## [3.36.2](https://github.com/schemathesis/schemathesis/compare/v3.36.1...v3.36.2) - 2024-09-26

### :wrench: Changed

-   Merge `minLength` & `maxLength` into `pattern` to avoid extremely
    slow generation in most popular cases.
-   Avoid generating `{` and `}` for path parameters.
-   Generate all negative types in the coverage phase.

### :bug: Fixed

-   Internal error on incorrect examples during the coverage phase.

## [3.36.1](https://github.com/schemathesis/schemathesis/compare/v3.36.0...v3.36.1) - 2024-09-23

### :wrench: Changed

-   Use `requestBody` examples as the source of valid inputs during the
    coverage phase.
-   Reuse top-level schema examples in more places during the coverage
    phase.
-   Generate more combinations of optional parameters during the
    coverage phase.

### :bug: Fixed

-   `ignored_auth` false positives on custom auth and explicit `--auth`
    CLI option. [#2462](https://github.com/schemathesis/schemathesis/issues/2462)
-   Avoid skipping string generation if they have `pattern` during the
    coverage phase.

## [3.36.0](https://github.com/schemathesis/schemathesis/compare/v3.35.5...v3.36.0) - 2024-09-15

### :rocket: Added

-   Reimplementation of test case deduplication in CLI. It effectively
    un-deprecates the `--contrib-unique-data` CLI option.
-   Extend `ignored_auth` to check for incorrect auth.
-   More `properties` combinations for the coverage phase.
-   Use the `default` field as a source of
    valid inputs during the coverage phase.

### :wrench: Changed

-   Add `ctx` as the first argument for all checks. This is a step
    towards checks that cover multiple responses at once.
-   Validate custom check function signatures.

### :wastebasket: Deprecated

-   Custom checks that do not accept `ctx` as the first argument.

### :bug: Fixed

-   Avoid running checks twice in new-style stateful tests.
-   Missing `timeout` in certain situations when loading the schema from
    the network.
-   Ignoring `with_security_parameters` in runner in some cases.

## [3.35.5](https://github.com/schemathesis/schemathesis/compare/v3.35.4...v3.35.5) - 2024-09-08

### :wrench: Changed

-   Extend explicit examples discovery mechanism by checking response
    examples.
-   Saving the generated data into a cassette when `--dry-run` is
    provided. [#1423](https://github.com/schemathesis/schemathesis/issues/1423)
-   Saving timeouted requests into a cassette.

### :bug: Fixed

-   Support non-Starlette ASGI apps in more places.

## [3.35.4](https://github.com/schemathesis/schemathesis/compare/v3.35.3...v3.35.4) - 2024-09-05

### :bug: Fixed

-   Missed `example` field in the coverage phase.

## [3.35.3](https://github.com/schemathesis/schemathesis/compare/v3.35.2...v3.35.3) - 2024-09-05

### :wrench: Changed

-   Use more explicit examples in the coverage phase.
-   Make CLI options help more readable.

### :bug: Fixed

-   Ignored `generation_config` in explicit example tests when it is
    explicitly passed to the test runner.
-   Incomplete header values in some serialization cases.

## [3.35.2](https://github.com/schemathesis/schemathesis/compare/v3.35.1...v3.35.2) - 2024-09-01

### :wrench: Changed

-   Restructure the `st run --help` output.
-   Use explicit examples in the coverage phase.

### :bug: Fixed

-   Ensure that the `-D` CLI option is respected in the coverage phase.
-   Prevent stateful tests failing with `Unsatisfiable` if it they
    previously had successfully generated test cases.

## [3.35.1](https://github.com/schemathesis/schemathesis/compare/v3.35.0...v3.35.1) - 2024-08-27

### :rocket: Added

-   New `phase` field to VCR cassettes to indicate the testing phase of
    each recorded test case.

### :bug: Fixed

-   Internal errors in the experimental "coverage" phase.
-   Missing `Case.data_generation_method` in test cases generated during
    the coverage phase.
-   Incorrect header values generated during the coverage phase.

## [3.35.0](https://github.com/schemathesis/schemathesis/compare/v3.34.3...v3.35.0) - 2024-08-25

### :rocket: Added

-   **EXPERIMENTAL**: New "coverage" phase in the test runner. It aims
    to explicitly cover common test scenarios like missing required
    properties, incorrect types, etc. Enable it with
    `--experimental=coverage-phase`
-   Extending CLI with custom options and CLI handlers via hooks.

## [3.34.3](https://github.com/schemathesis/schemathesis/compare/v3.34.2...v3.34.3) - 2024-08-24

### :wrench: Changed

-   Adjust the distribution of negative test cases in stateful tests so
    they are less likely to occur for starting transitions.

## [3.34.2](https://github.com/schemathesis/schemathesis/compare/v3.34.1...v3.34.2) - 2024-08-20

### :bug: Fixed

-   Not using the proper session in the `ignored_auth` check. [#2409](https://github.com/schemathesis/schemathesis/issues/2409)
-   WSGI support for `ignored_auth`.

## [3.34.1](https://github.com/schemathesis/schemathesis/compare/v3.34.0...v3.34.1) - 2024-08-20

### :bug: Fixed

-   Error in `response_header_conformance` if the header definition is
    behind `$ref`. [#2407](https://github.com/schemathesis/schemathesis/issues/2407)

## [3.34.0](https://github.com/schemathesis/schemathesis/compare/v3.33.3...v3.34.0) - 2024-08-17

### :rocket: Added

-   The `ensure_resource_availability` check. It verifies that a freshly
    created resource is available in related API operations.
-   The `ignored_auth` check. It verifies that the API operation
    requires the specified authentication.
-   Enable string format verification in response conformance checks. [#787](https://github.com/schemathesis/schemathesis/issues/787)
-   Control over cache key in custom auth implementation. [#1775](https://github.com/schemathesis/schemathesis/issues/1775)
-   The `--generation-graphql-allow-null` CLI option that controls
    whether `null` should be used for optional arguments in GraphQL
    queries. Enabled by default. [#1994](https://github.com/schemathesis/schemathesis/issues/1994)
-   Filters for hooks. [#1852](https://github.com/schemathesis/schemathesis/issues/1852)
-   Verify header schema conformance. [#796](https://github.com/schemathesis/schemathesis/issues/796)

### :wrench: Changed

-   Pass default stateful test runner config to `TestCase` used by
    `pytest` & `unittest` integration.
-   Rework transitions in stateful tests in order to reduce the number
    of unhelpful API calls.
-   Improve error message when `base_url` is missing for a schema loaded
    from a file.

### :bug: Fixed

-   Missing sanitization in new-style stateful tests.
-   Missing new-style stateful testing results in JUnit output.
-   Internal error when handling an exception inside a hook for a
    GraphQL schema.
-   Filters being ignored in the old-style stateful test runner. [#2376](https://github.com/schemathesis/schemathesis/issues/2376)
-   Missing sanitization for query parameters in code samples.

## [3.33.3](https://github.com/schemathesis/schemathesis/compare/v3.33.2...v3.33.3) - 2024-07-29

### :bug: Fixed

-   Incorrect default deadline for stateful tests in CLI.
-   Incorrect handling of `allOf` subschemas in testing explicit
    examples. [#2375](https://github.com/schemathesis/schemathesis/issues/2375)

### :wrench: Changed

-   Reduce the default stateful step count from 50 to 10. It increases
    the variety of the generated API call sequences.

## [3.33.2](https://github.com/schemathesis/schemathesis/compare/v3.33.1...v3.33.2) - 2024-07-27

### :bug: Fixed

-   Internal error in stateful testing.
-   Internal error in CLI output when some of test cases has no
    responses due to timeout. [#2373](https://github.com/schemathesis/schemathesis/issues/2373)

## [3.33.1](https://github.com/schemathesis/schemathesis/compare/v3.33.0...v3.33.1) - 2024-07-22

### :bug: Fixed

-   Ignoring nested examples. [#2358](https://github.com/schemathesis/schemathesis/issues/2358)

## [3.33.0](https://github.com/schemathesis/schemathesis/compare/v3.32.2...v3.33.0) - 2024-07-19

### :rocket: Added

-   A set of CLI options and a Python API for including and excluding
    what API operations to test. [#703](https://github.com/schemathesis/schemathesis/issues/703), [#819](https://github.com/schemathesis/schemathesis/issues/819), [#1398](https://github.com/schemathesis/schemathesis/issues/1398)
-   A way to filter API operations by an expression in CLI. [#1006](https://github.com/schemathesis/schemathesis/issues/1006)
-   Support for filtering GraphQL operations by `name`.

### :bug: Fixed

-   Missed `operation_id` & `tag` filter in some cases.
-   Broken compatibility with `Hypothesis<6.108`. [#2357](https://github.com/schemathesis/schemathesis/issues/2357)

### :wastebasket: Deprecated

-   `--method`, `--endpoint`, `--tag`, `--operation-id`,
    `--skip-deprecated-operations` CLI options in favor of the new
    `--include-*` and `--exclude-*` options. See more details in the CLI
    documentation.
-   `method`, `endpoint`, `tag`, `operation_id` and
    `skip_deprecated_operations` arguments in `schemathesis.from_*`
    loaders and the `parametrize` function in favor of the new `include`
    and `exclude` methods on `schema` instances.

## [3.32.2](https://github.com/schemathesis/schemathesis/compare/v3.32.1...v3.32.2) - 2024-07-17

### :bug: Fixed

-   Circular import in `schemathesis.runner.events`.

## [3.32.1](https://github.com/schemathesis/schemathesis/compare/v3.32.0...v3.32.1) - 2024-07-17

### :rocket: Added

-   Filtering by `operation_id` in conditional auth implementation.

### :bug: Fixed

-   Internal error when saving debug logs with
    `--experimental=stateful-test-runner` or
    `--experimental=schema-analysis` enabled. [#2353](https://github.com/schemathesis/schemathesis/issues/2353)

## [3.32.0](https://github.com/schemathesis/schemathesis/compare/v3.31.1...v3.32.0) - 2024-07-14

### :rocket: Added

-   Support for authentication via CLI arguments in new-style stateful
    tests.
-   Support for `--hypothesis-seed` in new-style stateful tests.
-   Support for `--set-*` CLI options in new-style stateful tests.
-   Support for `--max-response-time` in new-style stateful tests.
-   Support for targeted property-based testing in new-style stateful
    tests.
-   Support for `--request-*` CLI options in new-style stateful tests.
-   Support for `--generation-*` CLI options in new-style stateful
    tests.
-   Support for `--max-failures` in new-style stateful tests.
-   Support for `--dry-run` in new-style stateful tests.
-   `all` variant for the `--hypothesis-suppress-health-check` CLI
    option.
-   Support for Hypothesis \>= `6.108.0`.

### :bug: Fixed

-   WSGI support for new-style stateful tests.
-   Ignoring configured data generation methods in new-style stateful
    tests.
-   Using constant `data_generation_method` value for HTTP interactions
    in VCR cassettes.
-   Not reporting errors with `--experimental=stateful-only`. [#2326](https://github.com/schemathesis/schemathesis/issues/2326)
-   Internal error on CTRL-C during new-style stateful tests.
-   Use `--request-proxy` for API probing.
-   Fill the `seed` field in cassettes for new-style stateful tests.
-   Ignoring remote scope when getting API operation by reference.

### :wrench: Changed

-   Do not run new-style stateful tests if unit tests exited due to
    `--exitfirst`.
-   Display error details if API probing fails.

## [3.31.1](https://github.com/schemathesis/schemathesis/compare/v3.31.0...v3.31.1) - 2024-07-03

### :bug: Fixed

-   Generating negative test cases for path and query parameters. [#2312](https://github.com/schemathesis/schemathesis/issues/2312)

### :wrench: Changed

-   Do not consider ignoring additional parameters as a failure in
    `negative_data_rejection`.

## [3.31.0](https://github.com/schemathesis/schemathesis/compare/v3.30.4...v3.31.0) - 2024-06-30

### :rocket: Added

-   Storing cassettes in the HAR format via the `--cassette-format=har`
    CLI option. [#2299](https://github.com/schemathesis/schemathesis/issues/2299)
-   Support for cassettes in the new-style stateful test runner.
-   `--generation-with-security-parameters=false` CLI option to disable
    generation of security parameters (like tokens) in test cases.

### :bug: Fixed

-   Incorrect test case ids stored in VCR cassettes. [#2302](https://github.com/schemathesis/schemathesis/issues/2302)
-   Incorrect reference resolution scope for security schemes if the API
    operation has a different scope than the global security schemes. [#2300](https://github.com/schemathesis/schemathesis/issues/2300)
-   Properly display unresolvable reference if it comes from a missing
    file.

## [3.30.4](https://github.com/schemathesis/schemathesis/compare/v3.30.3...v3.30.4) - 2024-06-28

### :bug: Fixed

-   Missing overrides from `--set-*` CLI options in tests for explicit
    examples.

## [3.30.3](https://github.com/schemathesis/schemathesis/compare/v3.30.2...v3.30.3) - 2024-06-27

### :bug: Fixed

-   Internal error when piping stdout to a file in CLI on Windows.

## [3.30.2](https://github.com/schemathesis/schemathesis/compare/v3.30.1...v3.30.2) - 2024-06-27

### :bug: Fixed

-   Excessive `urllib3` warnings during testing `localhost` via `https`.
-   Misreporting of undocumented `Content-Type` when documented content
    types contain wildcards.
-   Incorrect test case reporting when code samples contain a single
    sanitized parameter. [#2294](https://github.com/schemathesis/schemathesis/issues/2294)

## [3.30.1](https://github.com/schemathesis/schemathesis/compare/v3.30.0...v3.30.1) - 2024-06-24

### :rocket: Added

-   `--output-truncate=false` CLI option to disable schema and response
    payload truncation in error messages.

### :wrench: Changed

-   More fine-grained events for stateful testing.

### :bug: Fixed

-   Internal error caused by an upstream race condition bug in
    Hypothesis. [#2269](https://github.com/schemathesis/schemathesis/issues/2269)
-   Do not output stateful tests sub-section in CLI if there are no
    stateful tests due to applied filters.

## [3.30.0](https://github.com/schemathesis/schemathesis/compare/v3.29.2...v3.30.0) - 2024-06-23

### :rocket: Added

-   **EXPERIMENTAL**: New stateful test runner in CLI. [#864](https://github.com/schemathesis/schemathesis/issues/864)
-   The `--experimental=stateful-only` CLI flag to run only stateful
    tests if the new test runner is enabled. Note that this feature is
    experimental and may change in future releases without notice.
-   Ability to extract values from headers, path, and query parameters
    using regular expressions in OpenAPI links.
-   The `negative_data_rejection` check. It ensures that the API rejects
    negative data as specified in the schema.
-   The `use_after_free` check. It ensures that the API returns a 404
    response after a successful DELETE operation on an object. At the
    moment, it is only available in state-machine-based stateful
    testing.
-   Support for building dynamic payloads via OpenAPI links. This allows
    for building objects or arrays where nested items are not hardcoded
    but dynamically evaluated.
-   `APIStateMachine.format_rules` method to format transition rules in
    a human-readable format.

``` 
POST /user
└── 201
    ├── GET /users/{ids}
    └── DELETE /user/{id}

GET /users/{ids}
└── 200
    └── PATCH /user

DELETE /user/{id}
└── 204
    └── DELETE /user/{id}
```

### :wrench: Changed

-   Enforce the `minLength` keyword on string path parameters to avoid
    the rejection of empty values later on. This improves the
    performance of data generation.
-   Rework building state machines for stateful testing to improve
    performance.
-   Improve error messages on `MaxRetryError`. [#2234](https://github.com/schemathesis/schemathesis/issues/2234)
-   Migrate to new-style `pytest` hooks. [#2181](https://github.com/schemathesis/schemathesis/issues/2181)
-   Filter out Hypothesis' warning about resetting the recursion limit
    in multi-worker tests.
-   Show sub-schema location in `response_schema_conformance` failure
    messages. [#2270](https://github.com/schemathesis/schemathesis/issues/2270)
-   Avoid collecting data for stateful tests in CLI when they are
    explicitly disabled.

### :bug: Fixed

-   Internal error during OpenAPI link resolution if the needed
    parameter is missing in the response.
-   Improper output when a JSON pointer can't be resolved during OpenAPI
    link resolution.
-   Generating invalid examples created by wrapping a named example
    value into another object. [#2238](https://github.com/schemathesis/schemathesis/issues/2238)
-   Distinguish more failures in stateful testing.
-   Generate different functions for state machine transitions to
    properly use swarm testing.
-   `RuntimeError` caused by a race condition when initializing
    Hypothesis' PRNG in multiple workers.
-   Missing body in `Case` if it is mutated after the `make_case` call. [#2208](https://github.com/schemathesis/schemathesis/issues/2208)
-   Internal error when a rate limiter hits its limit. [#2254](https://github.com/schemathesis/schemathesis/issues/2254)
-   Internal error during reference resolving when using relative file
    paths.
-   Ignoring property examples defined under the `example` key in Open
    API 2.0 schemas. [#2277](https://github.com/schemathesis/schemathesis/issues/2277)

### :fire: Removed

-   Support for `pytest<6.0`.

### :racing_car: Performance

-   Improve performance of copying schemas.

## [3.29.2](https://github.com/schemathesis/schemathesis/compare/v3.29.1...v3.29.2) - 2024-05-31

### :bug: Fixed

-   Remove temporary `print` calls.

## [3.29.1](https://github.com/schemathesis/schemathesis/compare/v3.29.0...v3.29.1) - 2024-05-31

### :bug: Fixed

-   Inlining too much in stateful testing.

## [3.29.0](https://github.com/schemathesis/schemathesis/compare/v3.28.1...v3.29.0) - 2024-05-30

**Changed**:

-   **INTERNAL**: Remove the ability to mutate components used in
    `schema["/path"]["METHOD"]` access patterns.

### :bug: Fixed

-   Not serializing shared parameters for an API operation.
-   `OperationNotFound` raised in `schema.get_operation_by_id` if the
    relevant path item is behind a reference.
-   Missing parameters shared under the same path in stateful testing if
    the path is behind a reference.
-   `KeyError` instead of `OperationNotFound` when the operation ID is
    not found in Open API 3.1 without path entries.
-   Not respecting `allow_x00=False` in headers and cookies. [#2220](https://github.com/schemathesis/schemathesis/issues/2220)
-   Internal error when building an error message for some
    network-related issues. [#2219](https://github.com/schemathesis/schemathesis/issues/2219)

### :racing_car: Performance

-   Optimize `schema["/path"]["METHOD"]` access patterns and reduce
    memory usage.
-   Optimize `get_operation_by_id` method performance and reduce memory
    usage.
-   Optimize `get_operation_by_reference` method performance.
-   Less copying during schema traversal.

## [3.28.1](https://github.com/schemathesis/schemathesis/compare/v3.28.0...v3.28.1) - 2024-05-11

### :bug: Fixed

-   Internal error on unresolvable Open API links during stateful
    testing.
-   Internal error when media type definition has only `example` or
    `examples` keys.

### :racing_car: Performance

-   Improve performance of `add_link` by avoiding unnecessary reference
    resolving.

## [3.28.0](https://github.com/schemathesis/schemathesis/compare/v3.27.1...v3.28.0) - 2024-05-10

### :rocket: Added

-   `Request.deserialize_body` and `Response.deserialize_body` helper
    methods to deserialize payloads to bytes from Base 64.
-   Support for `multipart/mixed` media type.

### :wrench: Changed

-   Do not show suggestion to show a traceback on Hypothesis'
    `Unsatisfiable` error.
-   Clarify error message on unsupported recursive references.
-   Report more details on some internal errors instead of "Unknown
    Schema Error".
-   Update error message on why Schemathesis can't generate test cases
    for some API operations.

### :bug: Fixed

-   Internal error on Windows when the CLI output is redirected to a
    file and code samples contain non CP1252 characters.
-   Properly check for nested recursive references inside combinators.
    This makes Schemathesis work with more schemas with recursive
    references.

## [3.27.1](https://github.com/schemathesis/schemathesis/compare/v3.27.0...v3.27.1) - 2024-04-29

### :rocket: Added

-   `GenerationConfig.headers.strategy` attribute for customizing header
    generation. [#2137](https://github.com/schemathesis/schemathesis/issues/2137)
-   Support for `python -m schemathesis.cli`. [#2142](https://github.com/schemathesis/schemathesis/issues/2142)
-   Support for `anyio>=4.0`. [#2081](https://github.com/schemathesis/schemathesis/issues/2081)

### :bug: Fixed

-   Supporting non-Starlette ASGI apps. [#2136](https://github.com/schemathesis/schemathesis/issues/2136)
-   Missing version metadata in ASGI client causing errors with
    ASGI3-only apps. [#2136](https://github.com/schemathesis/schemathesis/issues/2136)

## [3.27.0](https://github.com/schemathesis/schemathesis/compare/v3.26.2...v3.27.0) - 2024-04-14

### :rocket: Added

-   `Case.as_transport_kwargs` method to simplify the creation of
    transport-specific keyword arguments for sending requests.

### :wrench: Changed

-   Make `Case.call` work with `ASGI` & `WSGI` applications.
-   Extend the JUnit XML report format to match CLI output including
    skipped tests, code samples, and more.

### :wastebasket: Deprecated

-   `Case.call_wsgi` & `Case.call_asgi` in favor of `Case.call`.
-   `Case.as_requests_kwargs` & `Case.as_werkzeug_kwargs` in favor of
    `Case.as_transport_kwargs`.

## [3.26.2](https://github.com/schemathesis/schemathesis/compare/v3.26.1...v3.26.2) - 2024-04-06

### :rocket: Added

-   Support for `pyrate-limiter>=3.0`.

### :bug: Fixed

-   Excluding `\x00` bytes as a result of probes.

## [3.26.1](https://github.com/schemathesis/schemathesis/compare/v3.26.0...v3.26.1) - 2024-04-04

### :rocket: Added

-   Store time needed to generate each test case.

### :bug: Fixed

-   `InvalidArgument` when using `from_pytest_fixture` with parametrized
    pytest fixtures and Hypothesis settings. [#2115](https://github.com/schemathesis/schemathesis/issues/2115)

## [3.26.0](https://github.com/schemathesis/schemathesis/compare/v3.25.6...v3.26.0) - 2024-03-21

### :rocket: Added

-   Support for per-media type data generators. [#962](https://github.com/schemathesis/schemathesis/issues/962)
-   Support for `application/yaml` & `text/yml` media types in
    `YAMLSerializer`.
-   **EXPERIMENTAL**: Run automatic schema optimization & format
    inference if CLI is authenticated in Schemathesis.io.

### :bug: Fixed

-   Not resolving references in nested security schemes. [#2073](https://github.com/schemathesis/schemathesis/issues/2073)

### :wrench: Changed

-   Improve error message when the minimum possible example is too
    large.

## [3.25.6](https://github.com/schemathesis/schemathesis/compare/v3.25.5...v3.25.6) - 2024-03-02

### :bug: Fixed

-   Not respecting `allow_x00` and `codec` configs options during
    filling gaps in explicit examples.
-   Internal error when sending `multipart/form-data` requests when the
    schema defines the `*/*` content type.
-   Internal error when YAML payload definition contains nested `binary`
    format.
-   Internal error when an Open API 2.0 schema contains no `swagger` key
    and the schema version is forced.

### :wrench: Changed

-   Indicate API probing results in CLI.

## [3.25.5](https://github.com/schemathesis/schemathesis/compare/v3.25.4...v3.25.5) - 2024-02-29

### :bug: Fixed

-   Incorrect error message when the code inside the hook module raises
    `ImportError`. [#2074](https://github.com/schemathesis/schemathesis/issues/2074)
-   Compatibility with Hypothesis \>6.98.14
-   Not respecting `allow_x00` and `codec` configs options for data
    generation in some cases. [#2072](https://github.com/schemathesis/schemathesis/issues/2072)

## [3.25.4](https://github.com/schemathesis/schemathesis/compare/v3.25.3...v3.25.4) - 2024-02-25

### :wrench: Changed

-   Improve error message when the minimum possible example is too
    large.

## [3.25.3](https://github.com/schemathesis/schemathesis/compare/v3.25.2...v3.25.3) - 2024-02-22

### :rocket: Added

-   Added `__contains__` method to `ParameterSet` for easier parameter
    checks in hooks.

### :wrench: Changed

-   Suppress TLS-related warnings during API probing.

## [3.25.2](https://github.com/schemathesis/schemathesis/compare/v3.25.1...v3.25.2) - 2024-02-21

### :rocket: Added

-   Run automatic probes to detect the application capabilities before
    testing. They allow for more accurate data generation, reducing
    false positive test failures. [#1840](https://github.com/schemathesis/schemathesis/issues/1840)
-   Support running async Python tests with `trio`. [#1872](https://github.com/schemathesis/schemathesis/issues/1872)

### :bug: Fixed

-   Invalid spec detection if the experimental support for Open API 3.1
    is not explicit explicitly enabled.
-   Invalid spec detection if the input YAML contains not allowed
    characters.
-   `AttributeError` when using the experimental support for Open API
    3.1 with multiple workers.
-   Do not skip API operation if it is still possible to generate
    positive tests when `-D all` is passed.

## [3.25.1](https://github.com/schemathesis/schemathesis/compare/v3.25.0...v3.25.1) - 2024-02-10

### :wrench: Changed

-   **CLI**: Enhanced Open API 3.1.0 support messaging, now suggesting
    `--experimental=openapi-3.1` option for partial compatibility.

### :bug: Fixed

-   Not reporting errors during testing of explicit examples when data
    generation is flaky.

## [3.25.0](https://github.com/schemathesis/schemathesis/compare/v3.24.3...v3.25.0) - 2024-02-07

### :rocket: Added

-   `--hypothesis-no-phases` CLI option to disable Hypothesis testing
    phases. [#1324](https://github.com/schemathesis/schemathesis/issues/1324)
-   Support for loading GraphQL schemas from JSON files that contain the
    `__schema` key.
-   Response validation for GraphQL APIs.
-   Support `tag` in filters for custom auth.
-   Support for testing examples inside `anyOf` / `oneOf` / `allOf`
    keywords.
-   Support for the `text/xml` media type in `XMLSerializer`.
-   Support for the `text/json` media type in `JSONSerializer`.
-   Support for pytest 8.

### :wrench: Changed

-   **CLI**: Eagerly check for permissions when writing output to a
    file, including JUnit XML and other reports.
-   **Python**: Explicitly note that combining `@schema.given` with
    explicit examples from the spec is not supported. [#1217](https://github.com/schemathesis/schemathesis/issues/1217)
-   Clarify error message when a state machine has no transitions. [#1992](https://github.com/schemathesis/schemathesis/issues/1992)
-   Do not consider missing the `paths` key an error for Open API 3.1.
-   Improved formatting of multiple errors within the same API operation.
-   Allow arbitrary objects in array for `application/x-www-form-urlencoded` payloads.

### :wastebasket: Deprecated

-   The `--contrib-unique-data` CLI option and the corresponding
    `schemathesis.contrib.unique_data` hook. The concept of this feature
    does not fit the core principles of Hypothesis where strategies are
    configurable on a per-example basis but this feature implies
    uniqueness across examples. This leads to cryptic error messages
    about external state and flaky test runs, therefore it will be
    removed in Schemathesis 4.0

### :bug: Fixed

-   **CLI**: Do not duplicate the error message in the output when the
    error has no traceback and the `--show-trace` option is provided.
-   **Open API**: Internal error on path templates that contain `.`
    inside path parameters.
-   **Open API**: YAML serialization of data generated for schemas with
    `format: binary`.
-   Create parent directories when saving JUnit XML reports and other
    file-based output. [#1995](https://github.com/schemathesis/schemathesis/issues/1995)
-   Internal error when an API operation contains multiple parameters
    with the same name and some of them contain the `examples` keyword.
-   Internal error during query parameter generation on schemas that do
    not contain the `type` keyword.
-   Example generation for request body parameters using `$ref`.
-   Generating examples for properties that have deeply nested `$ref`.
-   Generating examples for properties with boolean sub-schemas.
-   Validating responses with boolean sub-schemas on Open API 3.1.
-   `TypeError` on non-string `pattern` values. This could happen on
    values in YAML, such that when not quoted, they are parsed as
    non-strings.
-   Testing examples requiring unsupported payload media types resulted
    in an internal error. These are now correctly reported as errors
-   Internal error on unsupported regular expressions in inside
    properties during example generation.
-   Missing XML definitions when the media type contains options like
    `application/xml; charset=utf-8`.
-   Unhandled timeout while reading the response payload.
-   Internal error when the header example in the schema is not a valid
    header.
-   Handle `KeyError` during state machine creation.
-   Deduplicate network errors that contain unique URLs in their
    messages.
-   Not reporting multiple errors of different kinds at the same API
    operation.
-   Group similar errors within the same API operation.

## [3.24.3](https://github.com/schemathesis/schemathesis/compare/v3.24.2...v3.24.3) - 2024-01-23

### :bug: Fixed

-   Incorrect base URL handling for GraphQL schemas. [#1987](https://github.com/schemathesis/schemathesis/issues/1987)

## [3.24.2](https://github.com/schemathesis/schemathesis/compare/v3.24.1...v3.24.2) - 2024-01-23

### :rocket: Added

-   **Python**: Shortcut to create strategies for all operations or a
    subset of them via `schema.as_strategy()` and
    `schema["/path/"].as_strategy()`. [#1982](https://github.com/schemathesis/schemathesis/issues/1982)

### :wrench: Changed

-   **Python**: Cleaner `repr` for GraphQL & Open API schemas.
-   **GraphQL**: Show suggestion when a field is not found in
    `schema["Query"][field_name]`.

### :bug: Fixed

-   Filter out test cases that can not be serialized when the API
    operation requires `application/x-www-form-urlencoded`. [#1306](https://github.com/schemathesis/schemathesis/issues/1306)

## [3.24.1](https://github.com/schemathesis/schemathesis/compare/v3.24.0...v3.24.1) - 2024-01-22

### :wrench: Changed

-   Cleanup SSL error messages.

### :bug: Fixed

-   Internal error when an unresolvable pointer occurs during data
    generation.
-   Internal errors when references lead to non-objects.
-   Missing `schema.override` on schemas created via the
    `from_pytest_fixture` loader.
-   Not calling hooks for `query` / `cookies` / `headers` in GraphQL
    schemas. [#1978](https://github.com/schemathesis/schemathesis/issues/1978)
-   Inability to access individual operations in GraphQL schema objects. [#1976](https://github.com/schemathesis/schemathesis/issues/1976)

## [3.24.0](https://github.com/schemathesis/schemathesis/compare/v3.23.1...v3.24.0) - 2024-01-21

### :rocket: Added

-   CLI options for overriding Open API parameters in test cases. [#1676](https://github.com/schemathesis/schemathesis/issues/1676)
-   A way to override Open API parameters the `pytest` integration with
    the `override` decorator. [#8](https://github.com/schemathesis/schemathesis/issues/8)
-   **Open API**: Support for the `examples` keyword inside individual
    property schemas. [#1730](https://github.com/schemathesis/schemathesis/issues/1730), [#1320](https://github.com/schemathesis/schemathesis/issues/1320)
-   **Open API**: Extract explicit examples from all defined media
    types. [#921](https://github.com/schemathesis/schemathesis/issues/921)

### :wrench: Changed

-   Raise an error if it is not possible to generate explicit examples. [#1771](https://github.com/schemathesis/schemathesis/issues/1771)
-   Avoid using the deprecated `cgi` module. [#1962](https://github.com/schemathesis/schemathesis/issues/1962)

### :bug: Fixed

-   **Open API**: Properly combine multiple explicit examples extracted
    from `examples` and `example` fields. [#1360](https://github.com/schemathesis/schemathesis/issues/1360)
-   **Open API**: Ignoring examples referenced via the `$ref` keyword. [#1692](https://github.com/schemathesis/schemathesis/issues/1692)

## [3.23.1](https://github.com/schemathesis/schemathesis/compare/v3.23.0...v3.23.1) - 2024-01-14

### :wrench: Changed

-   Do not auto-detect spec if the `--force-schema-version` CLI option
    is present.
-   Do not assume GraphQL when trying to auto-detect spec in an empty
    input file.

### :bug: Fixed

-   Internal error when the schema file is empty.

## [3.23.0](https://github.com/schemathesis/schemathesis/compare/v3.22.1...v3.23.0) - 2023-12-29

### :rocket: Added

-   New CLI option `--contrib-openapi-fill-missing-examples` to
    automatically generate random examples for API operations that lack
    explicit examples. [#1728](https://github.com/schemathesis/schemathesis/issues/1728), [#1376](https://github.com/schemathesis/schemathesis/issues/1376)
-   New CLI option `--request-proxy` to set HTTP(s) proxies for network
    calls. [#1723](https://github.com/schemathesis/schemathesis/issues/1723)

### :wrench: Changed

-   Validate `--generation-codec` values in CLI.
-   Do not deepcopy responses before passing to checks. They are not
    supposed to be mutated inside checks.
-   Pin `anyio` to `<4` due to incompatibility with
    `starlette-testclient`.

### :bug: Fixed

-   Internal error when the configured proxy is not available.
-   Not using `examples` from shared `parameters`. [#1729](https://github.com/schemathesis/schemathesis/issues/1729), [#1513](https://github.com/schemathesis/schemathesis/issues/1513)

## [3.22.1](https://github.com/schemathesis/schemathesis/compare/v3.22.0...v3.22.1) - 2023-12-04

### :bug: Fixed

-   Internal error during network error handling. [#1933](https://github.com/schemathesis/schemathesis/issues/1933)

## [3.22.0](https://github.com/schemathesis/schemathesis/compare/v3.21.2...v3.22.0) - 2023-12-03

### :rocket: Added

-   Support for `hypothesis-jsonschema==0.23`.
-   A way to control what characters are used for string generation. [#1142](https://github.com/schemathesis/schemathesis/issues/1142), [#1286](https://github.com/schemathesis/schemathesis/issues/1286), [#1562](https://github.com/schemathesis/schemathesis/issues/1562), [#1668](https://github.com/schemathesis/schemathesis/issues/1668)
-   Display the total number of collected links in the CLI output. [#1383](https://github.com/schemathesis/schemathesis/issues/1383)
-   `arm64` Docker builds. [#1740](https://github.com/schemathesis/schemathesis/issues/1740).
-   Use Python 3.12 in Docker images.
-   Store Docker image name in `Metadata`.
-   GraphQL scalar strategies for `Date`, `Time`, `DateTime`, `IP`,
    `IPv4`, `IPv6`, `Long`, `BigInt` and `UUID`. [#1690](https://github.com/schemathesis/schemathesis/issues/1690)

### :wrench: Changed

-   Bump the minimum supported Hypothesis version to `6.84.3`.
-   Bump the minimum supported `jsonschema` version to `4.18.0`.
-   Bump the minimum supported `hypothesis_graphql` version to `0.11.0`.
-   Use the same random seed for all tests in CLI. [#1384](https://github.com/schemathesis/schemathesis/issues/1384).
-   Improve serialization error messages in CLI.
-   Store skip reason in the runner events.
-   Build `bookworm`-based Debian Docker images instead of
    `buster`-based.
-   Improve error message on unknown scalar types in GraphQL.
-   Better auto-detection of GraphQL schemas.
-   Display parsing errors for schemas that are expected to be JSON or
    YAML.

### :wastebasket: Deprecated

-   Using the `--show-errors-tracebacks` CLI option. Use `--show-trace`
    instead.

### :bug: Fixed

-   Internal error when a non-existing schema file is passed together
    with `--base-url`. [#1912](https://github.com/schemathesis/schemathesis/issues/1912).
-   Internal error during schema loading from invalid URLs.
-   Ignore incompatible GraphQL checks in CLI rather than fail the whole
    test run. [#1918](https://github.com/schemathesis/schemathesis/issues/1918).

### :fire: Removed

-   Support for Python 3.7.
-   Unnecessary dependencies on `typing-extensions` and
    `importlib-metadata`.

## [3.21.2](https://github.com/schemathesis/schemathesis/compare/v3.21.1...v3.21.2) - 2023-11-27

### :rocket: Added

-   Support for `hypothesis>=6.90.1`.

## [3.21.1](https://github.com/schemathesis/schemathesis/compare/v3.21.0...v3.21.1) - 2023-11-16

### :rocket: Added

-   Basic support for `httpx` in `Case.validate_response`.

### :wrench: Changed

-   Restore the ability to import `NOT_SET` from `schemathesis.utils`. [#1890](https://github.com/schemathesis/schemathesis/issues/1890)

## [3.21.0](https://github.com/schemathesis/schemathesis/compare/v3.20.2...v3.21.0) - 2023-11-09

### :rocket: Added

-   Add Python 3.12 compatibility. [#1809](https://github.com/schemathesis/schemathesis/issues/1809)
-   Separate command for report upload.

### :wrench: Changed

-   Generated binary data inside `Case.body` is wrapped with a custom
    wrapper - `Binary` in order to simplify compatibility with
    `hypothesis-jsonschema`.
-   Do not modify `Case.body` inside `Case.as_requests_kwargs` when
    serializing multipart data.
-   **INTERNAL**: Moved heavy imports inside functions to improve CLI
    startup time by 4.3x, not affecting overall execution speed. [#1509](https://github.com/schemathesis/schemathesis/issues/1509)
-   Improved messaging for loading hooks and WSGI application issues.
-   Refined documentation strings for CLI options.
-   Added an error message if an internal error happened inside CLI
    event handler.
-   Unified CLI messages for errors arising from network, configuration,
    and Hypothesis-related issues. [#1600](https://github.com/schemathesis/schemathesis/issues/1600), [#1607](https://github.com/schemathesis/schemathesis/issues/1607), [#1782](https://github.com/schemathesis/schemathesis/issues/1782), [#1835](https://github.com/schemathesis/schemathesis/issues/1835)
-   Try to validate JSON data even if there is no proper `Content-Type`
    header. [#1787](https://github.com/schemathesis/schemathesis/issues/1787)
-   Refined failure reporting for clarity. [#1784](https://github.com/schemathesis/schemathesis/issues/1784), [#1785](https://github.com/schemathesis/schemathesis/issues/1785), [#1790](https://github.com/schemathesis/schemathesis/issues/1790), [#1799](https://github.com/schemathesis/schemathesis/issues/1799), [#1800](https://github.com/schemathesis/schemathesis/issues/1800)

## [3.20.2](https://github.com/schemathesis/schemathesis/compare/v3.20.1...v3.20.2) - 2023-10-27

### :bug: Fixed

-   Incorrect documentation & implementation for enabling experimental
    features in `pytest`.

## [3.20.1](https://github.com/schemathesis/schemathesis/compare/v3.20.0...v3.20.1) - 2023-10-20

### :wrench: Changed

-   Improved CLI error messages for missing or invalid arguments.

## [3.20.0](https://github.com/schemathesis/schemathesis/compare/v3.19.7...v3.20.0) - 2023-10-18

### :rocket: Added

-   Support for `application/xml` serialization based on Open API schema
    definitions. [#733](https://github.com/schemathesis/schemathesis/issues/733)
-   Hook shortcuts (`filter_query`, `map_header`, etc.) to minimize
    boilerplate in extensions. [#1673](https://github.com/schemathesis/schemathesis/issues/1673)
-   Support for colored output from docker container. [#1170](https://github.com/schemathesis/schemathesis/issues/1170)
-   A way to disable suggestion for visualizing test results via the
    `SCHEMATHESIS_REPORT_SUGGESTION=0` environment variable. [#1802](https://github.com/schemathesis/schemathesis/issues/1802)
-   Automatic FastAPI fixup injecting for ASGI loaders, eliminating the
    need for manual setup. [#1797](https://github.com/schemathesis/schemathesis/issues/1797)
-   Support for `body` hooks in GraphQL schemas, enabling custom
    filtering or modification of queries and mutations. [#1464](https://github.com/schemathesis/schemathesis/issues/1464)
-   New `filter_operations` hook to conditionally include or exclude
    specific API operations from being tested.
-   Added `contains` method to `ParameterSet` for easier parameter
    checks in hooks. [#1789](https://github.com/schemathesis/schemathesis/issues/1789)
-   Automatic sanitization of sensitive data in the output is now
    enabled by default. This feature can be disabled using the
    `--sanitize-output=false` CLI option. For more advanced
    customization, use `schemathesis.sanitizing.configure()`. [#1794](https://github.com/schemathesis/schemathesis/issues/1794)
-   `--experimental=openapi-3.1` CLI option for experimental support of
    OpenAPI 3.1. This enables compatible JSON Schema validation for
    responses, while data generation remains OpenAPI 3.0-compatible. [#1820](https://github.com/schemathesis/schemathesis/issues/1820)

**Note**: Experimental features can change or be removed in any minor
version release.

### :wrench: Changed

-   Support `Werkzeug>=3.0`. [#1819](https://github.com/schemathesis/schemathesis/issues/1819)
-   Refined generated reproduction code and shortened
    `X-Schemathesis-TestCaseId` for easier debugging. [#1801](https://github.com/schemathesis/schemathesis/issues/1801)
-   Add `case` as the first argument to `AuthContext.set`. Previous
    calling convention is still supported. [#1788](https://github.com/schemathesis/schemathesis/issues/1788)
-   Disable the 'explain' phase in Hypothesis to improve performance. [#1808](https://github.com/schemathesis/schemathesis/issues/1808)
-   Simplify Python code samples for failure reproduction.
-   Do not display `InsecureRequestWarning` in CLI output if the user
    explicitly provided `--request-tls-verify=false`. [#1780](https://github.com/schemathesis/schemathesis/issues/1780)
-   Enhance CLI output for schema loading and internal errors, providing
    clearer diagnostics and guidance. [#1781](https://github.com/schemathesis/schemathesis/issues/1781), [#1517](https://github.com/schemathesis/schemathesis/issues/1517), [#1472](https://github.com/schemathesis/schemathesis/issues/1472)

Before:

``` text
Failed to load schema from https://127.0.0.1:6423/openapi.json
You can use `--wait-for-schema=NUM` to wait for a maximum of NUM seconds on the API schema availability.

Error: requests.exceptions.SSLError: HTTPSConnectionPool(host='localhost', port=6423): Max retries exceeded with url: /openapi.json (Caused by SSLError(SSLCertVerificationError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:992)')))

Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks
```

After:

``` text
Schema Loading Error

SSL verification problem

    [SSL: WRONG_VERSION_NUMBER] wrong version number

Tip: Bypass SSL verification with `--request-tls-verify=false`.
```

### :wastebasket: Deprecated

-   Defining `AuthProvider.get` with a single `context` argument. The
    support will be removed in Schemathesis `4.0`.

### :bug: Fixed

-   Fixed type hint for `AuthProvider`. [#1776](https://github.com/schemathesis/schemathesis/issues/1776)
-   Do not skip negative tests if the generated value is `None`.
-   Lack of execution for ASGI events during testing. [#1305](https://github.com/schemathesis/schemathesis/issues/1305), [#1727](https://github.com/schemathesis/schemathesis/issues/1727)
-   Confusing error message when trying to load schema from a
    non-existing file. [#1602](https://github.com/schemathesis/schemathesis/issues/1602)
-   Reflect disabled TLS verification in generated code samples. [#1054](https://github.com/schemathesis/schemathesis/issues/1054)
-   Generated cURL commands now include the `Content-Type` header, which
    was previously omitted. [#1783](https://github.com/schemathesis/schemathesis/issues/1783)
-   Improperly serialized headers in
    `SerializedHistoryEntry.case.extra_headers`.

### :racing_car: Performance

-   Optimize event data emission by postponing code sample generation,
    resulting in a `~4%` reduction in the emitted events data size.

### :fire: Removed

-   Unused `SerializedError.example` attribute. It used to be populated
    for flaky errors before they became regular failures.
-   Unused `TestResult.overridden_headers` attribute.

## [3.19.7](https://github.com/schemathesis/schemathesis/compare/v3.19.6...v3.19.7) - 2023-09-03

### :bug: Fixed

-   `Unsatisfiable` error for multiple security schemes applied to the
    same API operation and an explicit `Authorization` header. [#1763](https://github.com/schemathesis/schemathesis/issues/1763)

## [3.19.6](https://github.com/schemathesis/schemathesis/compare/v3.19.5...v3.19.6) - 2023-08-14

### :bug: Fixed

-   Broken `--report` CLI argument under `click>=8.1.4`. [#1753](https://github.com/schemathesis/schemathesis/issues/1753)

## [3.19.5](https://github.com/schemathesis/schemathesis/compare/v3.19.4...v3.19.5) - 2023-06-03

### :bug: Fixed

-   Do not raise `Unsatisfiable` when explicit headers are provided for
    negative tests.
-   Do not raise `Unsatisfiable` when no headers can be negated.

## [3.19.4](https://github.com/schemathesis/schemathesis/compare/v3.19.3...v3.19.4) - 2023-06-03

### :bug: Fixed

-   Improved handling of negative test scenarios by not raising
    `Unsatisfiable` when path parameters cannot be negated but other
    parameters can be negated.

## [3.19.3](https://github.com/schemathesis/schemathesis/compare/v3.19.2...v3.19.3) - 2023-05-25

### :wrench: Changed

-   Support `requests<3`. [#1742](https://github.com/schemathesis/schemathesis/issues/1742)
-   Bump the minimum supported `Hypothesis` version to `6.31.6` to
    reflect requirement from `hypothesis-jsonschema`.

### :bug: Fixed

-   `HypothesisDeprecationWarning` regarding deprecated
    `HealthCheck.all()`. [#1739](https://github.com/schemathesis/schemathesis/issues/1739)

## [3.19.2](https://github.com/schemathesis/schemathesis/compare/v3.19.1...v3.19.2) - 2023-05-20

### :rocket: Added

-   You can now provide a tuple of checks to exclude when validating a
    response.

## [3.19.1](https://github.com/schemathesis/schemathesis/compare/v3.19.0...v3.19.1) - 2023-04-26

### :wrench: Changed

-   Support `requests<2.29`.

### :bug: Fixed

-   Passing `params` / `cookies` to `case.call` causing `TypeError`. [#1734](https://github.com/schemathesis/schemathesis/issues/1734)

### :fire: Removed

-   Direct dependency on `attrs`.

## [3.19.0](https://github.com/schemathesis/schemathesis/compare/v3.18.5...v3.19.0) - 2023-03-22

### :rocket: Added

-   Schemathesis now supports custom authentication mechanisms from the
    `requests` library. You can use
    `schemathesis.auth.set_from_requests` to set up Schemathesis CLI
    with any third-party authentication implementation that works with
    `requests`. [#1700](https://github.com/schemathesis/schemathesis/issues/1700)

``` python
import schemathesis
from requests_ntlm import HttpNtlmAuth

schemathesis.auth.set_from_requests(HttpNtlmAuth("domain\\username", "password"))
```

-   Ability to apply authentication conditionally to specific API
    operations using a combination of `@schemathesis.auth.apply_to()`
    and `@schemathesis.auth.skip_for()` decorators.

``` python
import schemathesis


# Apply auth only for operations that path starts with `/users/` but not the `POST` method
@schemathesis.auth().apply_to(path_regex="^/users/").skip_for(method="POST")
class MyAuth:
    ...
```

-   Add a convenience mapping-like interface to `OperationDefinition`
    including indexing access, the `get` method, and "in" support.
-   Request throttling via the `--rate-limit` CLI option. [#910](https://github.com/schemathesis/schemathesis/issues/910)

### :wrench: Changed

-   Unified Schemathesis custom authentication usage via the
    `schema.auth` decorator, replacing the previous
    `schema.auth.register` and `schema.auth.apply` methods:

``` python
import schemathesis

schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


# Schema-level auth
# Before: @schema.auth.register()
@schema.auth()
class MyAuth:
    ...


# Test-level auth
# Before: @schema.auth.apply(MyAuth)
@schema.auth(MyAuth)
@schema.parametrize()
def test_api(case):
    ...
```

### :bug: Fixed

-   Handling of query parameters and cookies passed to `case.call` and
    query parameters passed to `case.call_wsgi`. The user-provided
    values are now merged with the data generated by Schemathesis,
    instead of overriding it completely. [#1705](https://github.com/schemathesis/schemathesis/issues/1705)
-   Parameter definition takes precedence over security schemes with the
    same name.
-   `Unsatisfiable` error when explicit header name passed via CLI
    clashes with the header parameter name. [#1699](https://github.com/schemathesis/schemathesis/issues/1699)
-   Not using the `port` keyword argument in schema loaders during API
    schema loading. [#1721](https://github.com/schemathesis/schemathesis/issues/1721)

## [3.18.5](https://github.com/schemathesis/schemathesis/compare/v3.18.4...v3.18.5) - 2023-02-18

### :rocket: Added

-   Support for specifying the path to load hooks from via the
    `SCHEMATHESIS_HOOKS` environment variable.
    `[#1702](https://github.com/schemathesis/schemathesis/issues/1702)`.

### :wastebasket: Deprecated

-   Use of the `--pre-run` CLI option for loading hooks. Use the
    `SCHEMATHESIS_HOOKS` environment variable instead.

## [3.18.4](https://github.com/schemathesis/schemathesis/compare/v3.18.3...v3.18.4) - 2023-02-16

### :wrench: Changed

-   Support any Werkzeug 2.x in order to allow mitigation of
    [CVE-2023-25577](https://github.com/advisories/GHSA-xg9f-g7g7-2323). [#1695](https://github.com/schemathesis/schemathesis/issues/1695)

## [3.18.3](https://github.com/schemathesis/schemathesis/compare/v3.18.2...v3.18.3) - 2023-02-12

### :rocket: Added

-   `APIStateMachine.run` method to simplify running stateful tests.

### :wrench: Changed

-   Improved quality of generated test sequences by updating state
    machines in Schemathesis to always run a minimum of two steps during
    testing. [#1627](https://github.com/schemathesis/schemathesis/issues/1627) If you use
    `hypothesis.stateful.run_state_machine_as_test` to run your stateful
    tests, please use the `run` method on your state machine class
    instead. This change requires upgrading `Hypothesis` to at least
    version `6.68.1`.

## [3.18.2](https://github.com/schemathesis/schemathesis/compare/v3.18.1...v3.18.2) - 2023-02-08

### :racing_car: Performance

-   Modify values in-place inside built-in `map` functions as there is
    no need to copy them.
-   Update `hypothesis-jsonschema` to `0.22.1` for up to 30% faster data
    generation in some workflows.

## [3.18.1](https://github.com/schemathesis/schemathesis/compare/v3.18.0...v3.18.1) - 2023-02-06

### :wrench: Changed

-   Stateful testing: Only make stateful requests when stateful data is
    available from another operation. This change significantly reduces
    the number of API calls that likely will fail because of absence of
    stateful data. [#1669](https://github.com/schemathesis/schemathesis/issues/1669)

### :racing_car: Performance

-   Do not merge component schemas into the currently tested schema if
    they are not referenced by it. Originally all schemas were merged to
    make them visible to `hypothesis-jsonschema`, but they imply
    significant overhead. [#1180](https://github.com/schemathesis/schemathesis/issues/1180)
-   Use a faster, specialized version of `deepcopy`.

## [3.18.0](https://github.com/schemathesis/schemathesis/compare/v3.17.5...v3.18.0) - 2023-02-01

### :rocket: Added

-   Extra information to VCR cassettes.

-   The `--contrib-unique-data` CLI option that forces Schemathesis to
    generate unique test cases only. This feature is also available as a
    hook in `schemathesis.contrib.unique_data`.

-   A few decorators & functions that provide a simpler API to extend Schemathesis:  
    -   `schemathesis.auth()` for authentication providers;
    -   `schemathesis.check` for checks;
    -   `schemathesis.hook` & `BaseSchema.hook` for hooks;
    -   `schemathesis.serializer` for serializers;
    -   `schemathesis.target` for targets;
    -   `schemathesis.openapi.format` for custom OpenAPI formats.
    -   `schemathesis.graphql.scalar` for GraphQL scalars.

-   Open API: UUID format generation via the
    `schemathesis.contrib.openapi.formats.uuid` extension You could
    enable it via the `--contrib-openapi-formats-uuid` CLI option.

### :wrench: Changed

-   Build: Switch the build backend to [Hatch](https://hatch.pypa.io/).
-   Relax requirements for `attrs`. [#1643](https://github.com/schemathesis/schemathesis/issues/1643)
-   Avoid occasional empty lines in cassettes.

### :wastebasket: Deprecated

-   `schemathesis.register_check` in favor of `schemathesis.check`.
-   `schemathesis.register_target` in favor of `schemathesis.target`.
-   `schemathesis.register_string_format` in favor of
    `schemathesis.openapi.format`.
-   `schemathesis.graphql.register_scalar` in favor of
    `schemathesis.graphql.scalar`.
-   `schemathesis.auth.register` in favor of `schemathesis.auth`.

### :bug: Fixed

-   Remove recursive references from the last reference resolution
    level. It works on the best effort basis and does not cover all
    possible cases. [#947](https://github.com/schemathesis/schemathesis/issues/947)
-   Invalid cassettes when headers contain characters with a special
    meaning in YAML.
-   Properly display flaky deadline errors.
-   Internal error when the `utf8_bom` fixup is used for WSGI apps.
-   Printing header that are set explicitly via `get_call_kwargs` in
    stateful testing. [#828](https://github.com/schemathesis/schemathesis/issues/828)
-   Display all explicitly defined headers in the generated cURL
    command.
-   Replace `starlette.testclient.TestClient` with
    `starlette_testclient.TestClient` to keep compatibility with newer
    `starlette` versions. [#1637](https://github.com/schemathesis/schemathesis/issues/1637)

### :racing_car: Performance

-   Running negative tests filters out less data.
-   Schema loading: Try a faster loader first if an HTTP response or a
    file is expected to be JSON.

## [3.17.5](https://github.com/schemathesis/schemathesis/compare/v3.17.4...v3.17.5) - 2022-11-08

### :rocket: Added

-   Python 3.11 support. [#1632](https://github.com/schemathesis/schemathesis/issues/1632)

### :bug: Fixed

-   Allow `Werkzeug<=2.2.2`. [#1631](https://github.com/schemathesis/schemathesis/issues/1631)

## [3.17.4](https://github.com/schemathesis/schemathesis/compare/v3.17.3...v3.17.4) - 2022-10-19

### :bug: Fixed

-   Appending an extra slash to the `/` path. [#1625](https://github.com/schemathesis/schemathesis/issues/1625)

## [3.17.3](https://github.com/schemathesis/schemathesis/compare/v3.17.2...v3.17.3) - 2022-10-10

### :bug: Fixed

-   Missing `httpx` dependency. [#1614](https://github.com/schemathesis/schemathesis/issues/1614)

## [3.17.2](https://github.com/schemathesis/schemathesis/compare/v3.17.1...v3.17.2) - 2022-08-27

### :bug: Fixed

-   Insufficient timeout for report uploads.

## [3.17.1](https://github.com/schemathesis/schemathesis/compare/v3.17.0...v3.17.1) - 2022-08-19

### :wrench: Changed

-   Support `requests==2.28.1`.

## [3.17.0](https://github.com/schemathesis/schemathesis/compare/v3.16.5...v3.17.0) - 2022-08-17

### :rocket: Added

-   Support for exception groups in newer `Hypothesis` versions. [#1592](https://github.com/schemathesis/schemathesis/issues/1592)
-   A way to generate negative and positive test cases within the same
    CLI run via `-D all`.

### :bug: Fixed

-   Allow creating APIs in Schemathesis.io by name when the schema is
    passed as a file.
-   Properly trim tracebacks on `Hypothesis>=6.54.0`.
-   Skipping negative tests when they should not be skipped.

### :wrench: Changed

-   **pytest**: Generate positive & negative within the same test node.
-   **CLI**: Warning if there are too many HTTP 403 API responses.
-   **Runner**: `BeforeExecution.data_generation_method` and
    `AfterExecution.data_generation_method` changed to lists of
    `DataGenerationMethod` as the same test may contain data coming from
    different data generation methods.

## [3.16.5](https://github.com/schemathesis/schemathesis/compare/v3.16.4...v3.16.5) - 2022-08-11

### :bug: Fixed

-   CLI: Hanging on `CTRL-C` when `--report` is enabled.
-   Internal error when GraphQL schema has its root types renamed. [#1591](https://github.com/schemathesis/schemathesis/issues/1591)

## [3.16.4](https://github.com/schemathesis/schemathesis/compare/v3.16.3...v3.16.4) - 2022-08-09

### :wrench: Changed

-   Suggest using `--wait-for-schema` if API schema is not available.

## [3.16.3](https://github.com/schemathesis/schemathesis/compare/v3.16.2...v3.16.3) - 2022-08-08

### :rocket: Added

-   CLI: `--max-failures=N` option to exit after first `N` failures or
    errors. [#1580](https://github.com/schemathesis/schemathesis/issues/1580)
-   CLI: `--wait-for-schema=N` option to automatically retry schema
    loading for `N` seconds. [#1582](https://github.com/schemathesis/schemathesis/issues/1582)
-   CLI: Display old and new payloads in `st replay` when the `-v`
    option is passed. [#1584](https://github.com/schemathesis/schemathesis/issues/1584)

### :bug: Fixed

-   Internal error on generating negative tests for query parameters
    with `explode: true`.

## [3.16.2](https://github.com/schemathesis/schemathesis/compare/v3.16.1...v3.16.2) - 2022-08-05

### :rocket: Added

-   CLI: Warning if **ALL** API responses are HTTP 404.
-   The `after_load_schema` hook, which is designed for modifying the
    loaded API schema before running tests. For example, you can use it
    to add Open API links to your schema via `schema.add_link`.
-   New `utf8_bom` fixup. It helps to mitigate JSON decoding errors
    inside the `response_schema_conformance` check when payload contains
    BOM. [#1563](https://github.com/schemathesis/schemathesis/issues/1563)

### :bug: Fixed

-   Description of `-v` or `--verbosity` option for CLI.

### :wrench: Changed

-   Execute `before_call` / `after_call` hooks inside the `call_*`
    methods. It makes them available for the `pytest` integration.

## [3.16.1](https://github.com/schemathesis/schemathesis/compare/v3.16.0...v3.16.1) - 2022-07-29

### :rocket: Added

-   CLI: Warning if the API returns too many HTTP 401.
-   Add `SCHEMATHESIS_BASE_URL` environment variable for specifying
    `--base-url` in CLI.
-   Collect anonymized CLI usage telemetry when reports are uploaded. We
    do not collect any free-form values you use in your CLI, except for
    header names. Instead, we measure how many times you use each
    free-form option in this command. Additionally we count all
    non-default hook types only by hook name.

> [!IMPORTANT]
> You can disable usage this with the
> `--schemathesis-io-telemetry=false` CLI option or the
> `SCHEMATHESIS_TELEMETRY=false` environment variable.

## [3.16.0](https://github.com/schemathesis/schemathesis/compare/v3.15.6...v3.16.0) - 2022-07-22

### :rocket: Added

-   Report uploading to Schemathesis.io via the `--report` CLI option.

### :wrench: Changed

-   Do not validate schemas by default in the `pytest` integration.
-   CLI: Display test run environment metadata only if `-v` is provided.
-   CLI: Do not display headers automatically added by `requests` in
    code samples.

### :bug: Fixed

-   Do not report optional headers as missing.
-   Compatibility with `hypothesis>=6.49`. [#1538](https://github.com/schemathesis/schemathesis/issues/1538)
-   Handling of `unittest.case.SkipTest` emitted by newer Hypothesis
    versions.
-   Generating invalid headers when their schema has `array` or `object`
    types.

### :fire: Removed

-   Previously, data was uploaded to Schemathesis.io when the proper
    credentials were specified. This release removes this behavior. From
    now on, every upload requires the explicit `--report` CLI option.
-   Textual representation of HTTP requests in CLI output in order to
    decrease verbosity and avoid showing the same data in multiple
    places.

## [3.15.6](https://github.com/schemathesis/schemathesis/compare/v3.15.5...v3.15.6) - 2022-06-23

### :bug: Fixed

-   Do not discard dots (`.`) in OpenAPI expressions during parsing.

## [3.15.5](https://github.com/schemathesis/schemathesis/compare/v3.15.4...v3.15.5) - 2022-06-21

### :bug: Fixed

-   `TypeError` when using `--auth-type=digest` in CLI.

## [3.15.4](https://github.com/schemathesis/schemathesis/compare/v3.15.3...v3.15.4) - 2022-06-06

### :rocket: Added

-   Support generating data for Open API request payloads with wildcard
    media types. [#1526](https://github.com/schemathesis/schemathesis/issues/1526)

### :wrench: Changed

-   Mark tests as skipped if there are no explicit examples and
    `--hypothesis-phases=explicit` is used. [#1323](https://github.com/schemathesis/schemathesis/issues/1323)
-   Parse all YAML mapping keys as strings, ignoring the YAML grammar
    rules. For example, `on: true` will be parsed as `{"on": True}`
    instead of `{True: True}`. Even though YAML does not restrict keys
    to strings, in the Open API and JSON Schema context, this
    restriction is implied because the underlying data model comes from
    JSON.
-   **INTERNAL**: Improve flexibility of event serialization.
-   **INTERNAL**: Store request / response history in `SerializedCheck`.

## [3.15.3](https://github.com/schemathesis/schemathesis/compare/v3.15.2...v3.15.3) - 2022-05-28

### :bug: Fixed

-   Deduplication of failures caused by malformed JSON payload. [#1518](https://github.com/schemathesis/schemathesis/issues/1518)
-   Do not re-raise `InvalidArgument` exception as `InvalidSchema` in
    non-Schemathesis tests. [#1514](https://github.com/schemathesis/schemathesis/issues/1514)

## [3.15.2](https://github.com/schemathesis/schemathesis/compare/v3.15.1...v3.15.2) - 2022-05-09

### :bug: Fixed

-   Avoid generating negative query samples that `requests` will treat
    as an empty query.
-   Editable installation via `pip`.

## [3.15.1](https://github.com/schemathesis/schemathesis/compare/v3.15.0...v3.15.1) - 2022-05-03

### :rocket: Added

-   **OpenAPI**: Expose `APIOperation.get_security_requirements` that
    returns a list of security requirements applied to the API operation
-   Attach originally failed checks to "grouped" exceptions.

### :bug: Fixed

-   Internal error when Schemathesis doesn't have permission to create
    its `hosts.toml` file.
-   Do not show internal Hypothesis warning multiple times when the
    Hypothesis database directory is not usable.
-   Do not print not relevant Hypothesis reports when run in CI.
-   Invalid `verbose_name` value in `SerializedCase` for GraphQL tests.

## [3.15.0](https://github.com/schemathesis/schemathesis/compare/v3.14.2...v3.15.0) - 2022-05-01

### :rocket: Added

-   **GraphQL**: Mutations supports. Schemathesis will generate random
    mutations by default from now on.
-   **GraphQL**: Support for registering strategies to generate custom
    scalars.
-   Custom auth support for schemas created via `from_pytest_fixture`.

### :wrench: Changed

-   Do not encode payloads in cassettes as base64 by default. This
    change makes Schemathesis match the default Ruby's VCR behavior and
    leads to more human-readable cassettes. Use
    `--cassette-preserve-exact-body-bytes` to restore the old behavior. [#1413](https://github.com/schemathesis/schemathesis/issues/1413)
-   Bump `hypothesis-graphql` to `0.9.0`.
-   Avoid simultaneous authentication requests inside auth providers
    when caching is enabled.
-   Reduce the verbosity of `pytest` output. A few internal frames and
    the "Falsifying example" block are removed from the output.
-   Skip negative tests on API operations that are not possible to
    negate. [#1463](https://github.com/schemathesis/schemathesis/issues/1463)
-   Make it possible to generate negative tests if at least one
    parameter can be negated.
-   Treat flaky errors as failures and display full report about the
    failure. [#1081](https://github.com/schemathesis/schemathesis/issues/1081)
-   Do not duplicate failing explicit example in the
    `HYPOTHESIS OUTPUT` CLI output section. [#881](https://github.com/schemathesis/schemathesis/issues/881)

### :bug: Fixed

-   **GraphQL**: Semantically invalid queries without aliases.
-   **GraphQL**: Rare crashes on invalid schemas.
-   Internal error inside `BaseOpenAPISchema.validate_response` on
    `requests>=2.27` when response body contains malformed JSON. [#1485](https://github.com/schemathesis/schemathesis/issues/1485)
-   `schemathesis.from_pytest_fixture`: Display each failure if
    Hypothesis found multiple of them.

### :racing_car: Performance

-   **GraphQL**: Over 2x improvement from internal optimizations.

## [3.14.2](https://github.com/schemathesis/schemathesis/compare/v3.14.1...v3.14.2) - 2022-04-21

### :rocket: Added

-   Support for auth customization & automatic refreshing. [#966](https://github.com/schemathesis/schemathesis/issues/966)

## [3.14.1](https://github.com/schemathesis/schemathesis/compare/v3.14.0...v3.14.1) - 2022-04-18

### :bug: Fixed

-   Using `@schema.parametrize` with test methods on `pytest>=7.0`.

## [3.14.0](https://github.com/schemathesis/schemathesis/compare/v3.13.9...v3.14.0) - 2022-04-17

### :rocket: Added

-   Open API link name customization via the `name` argument to
    `schema.add_link`.
-   `st` as an alias to the `schemathesis` command line entrypoint.
-   `st auth login` / `st auth logout` to authenticate with
    Schemathesis.io.
-   `X-Schemathesis-TestCaseId` header to help to distinguish test cases
    on the application side. [#1303](https://github.com/schemathesis/schemathesis/issues/1303)
-   Support for comma separated lists in the `--checks` CLI option. [#1373](https://github.com/schemathesis/schemathesis/issues/1373)
-   Hypothesis Database configuration for CLI via the
    `--hypothesis-database` option. [#1326](https://github.com/schemathesis/schemathesis/issues/1326)
-   Make the `SCHEMA` CLI argument accept API names from
    Schemathesis.io.

### :wrench: Changed

-   Enable Open API links traversal by default. To disable it, use
    `--stateful=none`.
-   Do not validate API schema by default. To enable it back, use
    `--validate-schema=true`.
-   Add the `api_name` CLI argument to upload data to Schemathesis.io.
-   Show response status code on failing checks output in CLI.
-   Improve error message on malformed Open API path templates (like
    `/foo}/`). [#1372](https://github.com/schemathesis/schemathesis/issues/1372)
-   Improve error message on malformed media types that appear in the
    schema or in response headers. [#1382](https://github.com/schemathesis/schemathesis/issues/1382)
-   Relax dependencies on `pyyaml` and `click`.
-   Add `--cassette-path` that is going to replace
    `--store-network-log`. The old option is deprecated and will be
    removed in Schemathesis `4.0`

### :bug: Fixed

-   Show the proper Hypothesis configuration in the CLI output. [#1445](https://github.com/schemathesis/schemathesis/issues/1445)
-   Missing `source` attribute in the `Case.partial_deepcopy`
    implementation. [#1429](https://github.com/schemathesis/schemathesis/issues/1429)
-   Duplicated failure message from `content_type_conformance` and
    `response_schema_conformance` checks when the checked response has
    no `Content-Type` header. [#1394](https://github.com/schemathesis/schemathesis/issues/1394)
-   Not copied `case` & `response` inside `Case.validate_response`.
-   Ignored `pytest.mark` decorators when they are applied before
    `schema.parametrize` if the schema is created via
    `from_pytest_fixture`. [#1378](https://github.com/schemathesis/schemathesis/issues/1378)

## [3.13.9](https://github.com/schemathesis/schemathesis/compare/v3.13.8...v3.13.9) - 2022-04-14

### :bug: Fixed

-   Compatibility with `pytest-asyncio>=0.17.1`. [#1452](https://github.com/schemathesis/schemathesis/issues/1452)

## [3.13.8](https://github.com/schemathesis/schemathesis/compare/v3.13.7...v3.13.8) - 2022-04-05

### :bug: Fixed

-   Missing `media_type` in the `Case.partial_deepcopy` implementation.
    It led to missing payload in failure reproduction code samples.

## [3.13.7](https://github.com/schemathesis/schemathesis/compare/v3.13.6...v3.13.7) - 2022-04-02

### :rocket: Added

-   Support for `Hypothesis>=6.41.0`. [#1425](https://github.com/schemathesis/schemathesis/issues/1425)

## [3.13.6](https://github.com/schemathesis/schemathesis/compare/v3.13.5...v3.13.6) - 2022-03-31

### :wrench: Changed

-   Deep-clone `Response` instances before passing to check functions.

## [3.13.5](https://github.com/schemathesis/schemathesis/compare/v3.13.4...v3.13.5) - 2022-03-31

### :wrench: Changed

-   Deep-clone `Case` instances before passing to check functions.

## [3.13.4](https://github.com/schemathesis/schemathesis/compare/v3.13.3...v3.13.4) - 2022-03-29

### :rocket: Added

-   Support for `Werkzeug>=2.1.0`. [#1410](https://github.com/schemathesis/schemathesis/issues/1410)

### :wrench: Changed

-   Validate `requests` kwargs to catch cases when the ASGI integration
    is used, but the proper ASGI client is not supplied. [#1335](https://github.com/schemathesis/schemathesis/issues/1335)

## [3.13.3](https://github.com/schemathesis/schemathesis/compare/v3.13.2...v3.13.3) - 2022-02-20

### :rocket: Added

-   `--request-tls-verify` CLI option for the `replay` command. It
    controls whether Schemathesis verifies the server's TLS certificate.
    You can also pass the path to a CA_BUNDLE file for private certs. [#1395](https://github.com/schemathesis/schemathesis/issues/1395)
-   Support for client certificate authentication with `--request-cert`
    and `--request-cert-key` arguments for the `replay` command.

## [3.13.2](https://github.com/schemathesis/schemathesis/compare/v3.13.1...v3.13.2) - 2022-02-16

### :wrench: Changed

-   Use Schemathesis default User-Agent when communicating with SaaS.

### :bug: Fixed

-   Use the same `correlation_id` in `BeforeExecution` and
    `AfterExecution` events if the API schema contains an error that
    causes an `InvalidSchema` exception during test execution.
-   Use `full_path` in error messages in recoverable schema-level
    errors. It makes events generated in such cases consistent with
    usual events.

## [3.13.1](https://github.com/schemathesis/schemathesis/compare/v3.13.0...v3.13.1) - 2022-02-10

### :rocket: Added

-   `APIOperation.iter_parameters` helper to iterate over all
    parameters.

### :bug: Fixed

-   Properly handle error if Open API parameter doesn't have `content`
    or `schema` keywords.

## [3.13.0](https://github.com/schemathesis/schemathesis/compare/v3.12.3...v3.13.0) - 2022-02-09

### :wrench: Changed

-   Update integration with Schemathesis.io.
-   Always show traceback for errors in Schemathesis.io integration.

## [3.12.3](https://github.com/schemathesis/schemathesis/compare/v3.12.2...v3.12.3) - 2022-01-13

### :bug: Fixed

-   Generating illegal unicode surrogates in queries. [#1370](https://github.com/schemathesis/schemathesis/issues/1370)

## [3.12.2](https://github.com/schemathesis/schemathesis/compare/v3.12.1...v3.12.2) - 2022-01-12

### :bug: Fixed

-   Not-escaped single quotes in generated Python code samples. [#1359](https://github.com/schemathesis/schemathesis/issues/1359)

## [3.12.1](https://github.com/schemathesis/schemathesis/compare/v3.12.0...v3.12.1) - 2021-12-31

### :bug: Fixed

-   Improper handling of `base_url` in `call_asgi`, when the base URL
    has a non-empty base path. [#1366](https://github.com/schemathesis/schemathesis/issues/1366)

## [3.12.0](https://github.com/schemathesis/schemathesis/compare/v3.11.7...v3.12.0) - 2021-12-29

### :wrench: Changed

-   Upgrade `typing-extensions` to `>=3.7,<5`.
-   Upgrade `jsonschema` to `^4.3.2`.
-   Upgrade `hypothesis-jsonschema` to `>=0.22.0`.

### :bug: Fixed

-   Generating values not compliant with the ECMAScript regex syntax. [#1350](https://github.com/schemathesis/schemathesis/issues/1350), [#1241](https://github.com/schemathesis/schemathesis/issues/1241)

### :fire: Removed

-   Support for Python 3.6.

## [3.11.7](https://github.com/schemathesis/schemathesis/compare/v3.11.6...v3.11.7) - 2021-12-23

### :rocket: Added

-   Support for Python 3.10. [#1292](https://github.com/schemathesis/schemathesis/issues/1292)

## [3.11.6](https://github.com/schemathesis/schemathesis/compare/v3.11.5...v3.11.6) - 2021-12-20

### :rocket: Added

-   Support for client certificate authentication with `--request-cert`
    and `--request-cert-key` arguments. [#1173](https://github.com/schemathesis/schemathesis/issues/1173)
-   Support for `readOnly` and `writeOnly` Open API keywords. [#741](https://github.com/schemathesis/schemathesis/issues/741)

## [3.11.5](https://github.com/schemathesis/schemathesis/compare/v3.11.4...v3.11.5) - 2021-12-04

### :wrench: Changed

-   Generate tests for API operations with the HTTP `TRACE` method on
    Open API 2.0.

## [3.11.4](https://github.com/schemathesis/schemathesis/compare/v3.11.3...v3.11.4) - 2021-12-03

### :wrench: Changed

-   Add `AfterExecution.data_generation_method`.
-   Minor changes to the Schemathesis.io integration.

## [3.11.3](https://github.com/schemathesis/schemathesis/compare/v3.11.2...v3.11.3) - 2021-12-02

### :bug: Fixed

-   Silently failing to detect numeric status codes when the schema
    contains a shared `parameters` key. [#1343](https://github.com/schemathesis/schemathesis/issues/1343)
-   Not raising an error when tests generated by schemas loaded with
    `from_pytest_fixture` match no API operations. [#1342](https://github.com/schemathesis/schemathesis/issues/1342)

## [3.11.2](https://github.com/schemathesis/schemathesis/compare/v3.11.1...v3.11.2) - 2021-11-30

### :wrench: Changed

-   Use `name` & `data_generation_method` parameters to subtest context
    instead of `path` & `method`. It allows the end-user to disambiguate
    among subtest reports.
-   Raise an error if a test function wrapped with `schema.parametrize`
    matches no API operations. [#1336](https://github.com/schemathesis/schemathesis/issues/1336)

### :bug: Fixed

-   Handle `KeyboardInterrupt` that happens outside of the main test
    loop inside the runner. It makes interrupt handling consistent,
    independent at what point it happens. [#1325](https://github.com/schemathesis/schemathesis/issues/1325)
-   Respect the `data_generation_methods` config option defined on a
    schema instance when it is loaded via `from_pytest_fixture`. [#1331](https://github.com/schemathesis/schemathesis/issues/1331)
-   Ignored hooks defined on a schema instance when it is loaded via
    `from_pytest_fixture`. [#1340](https://github.com/schemathesis/schemathesis/issues/1340)

## [3.11.1](https://github.com/schemathesis/schemathesis/compare/v3.11.0...v3.11.1) - 2021-11-20

### :wrench: Changed

-   Update `click` and `PyYaml` dependency versions. [#1328](https://github.com/schemathesis/schemathesis/issues/1328)

## [3.11.0](https://github.com/schemathesis/schemathesis/compare/v3.10.1...v3.11.0) - 2021-11-03

### :wrench: Changed

-   Show `cURL` code samples by default instead of Python. [#1269](https://github.com/schemathesis/schemathesis/issues/1269)
-   Improve reporting of `jsonschema` errors which are caused by
    non-string object keys.
-   Store `data_generation_method` in `BeforeExecution`.
-   Use case-insensitive dictionary for `Case.headers`. [#1280](https://github.com/schemathesis/schemathesis/issues/1280)

### :bug: Fixed

-   Pass `data_generation_method` to `Case` for GraphQL schemas.
-   Generation of invalid headers in some cases. [#1142](https://github.com/schemathesis/schemathesis/issues/1142)
-   Unescaped quotes in generated Python code samples on some schemas. [#1030](https://github.com/schemathesis/schemathesis/issues/1030)

### :racing_car: Performance

-   Dramatically improve CLI startup performance for large API schemas.
-   Open API 3: Inline only `components/schemas` before passing schemas
    to `hypothesis-jsonschema`.
-   Generate tests on demand when multiple workers are used during CLI
    runs. [#1287](https://github.com/schemathesis/schemathesis/issues/1287)

## [3.10.1](https://github.com/schemathesis/schemathesis/compare/v3.10.0...v3.10.1) - 2021-10-04

### :rocket: Added

-   `DataGenerationMethod.all` shortcut to get all possible enum
    variants.

### :bug: Fixed

-   Unresolvable dependency due to incompatible changes in the new
    `hypothesis-jsonschema` release. [#1290](https://github.com/schemathesis/schemathesis/issues/1290)

## [3.10.0](https://github.com/schemathesis/schemathesis/compare/v3.9.7...v3.10.0) - 2021-09-13

### :rocket: Added

-   Optional integration with Schemathesis.io.
-   New `before_init_operation` hook.
-   **INTERNAL**. `description` attribute for all parsed parameters
    inside `APIOperation`.
-   Timeouts when loading external schema components or external
    examples.

### :wrench: Changed

-   Pin `werkzeug` to `>=0.16.0`.
-   **INTERNAL**. `OpenAPI20CompositeBody.definition` type to
    `List[OpenAPI20Parameter]`.
-   Open API schema loaders now also accept single
    `DataGenerationMethod` instances for the `data_generation_methods`
    argument. [#1260](https://github.com/schemathesis/schemathesis/issues/1260)
-   Improve error messages when the loaded API schema is not in JSON or
    YAML. [#1262](https://github.com/schemathesis/schemathesis/issues/1262)

### :bug: Fixed

-   Internal error in `make_case` calls for GraphQL schemas.
-   `TypeError` on `case.call` with bytes data on GraphQL schemas.
-   Worker threads may not be immediately stopped on SIGINT. [#1066](https://github.com/schemathesis/schemathesis/issues/1066)
-   Re-used referenced objects during inlining. Now they are
    independent.
-   Rewrite not resolved remote references to local ones. [#986](https://github.com/schemathesis/schemathesis/issues/986)
-   Stop worker threads on failures with `exit_first` enabled. [#1204](https://github.com/schemathesis/schemathesis/issues/1204)
-   Properly report all failures when custom checks are passed to
    `case.validate_response`.

### :racing_car: Performance

-   Avoid using filters for header values when is not necessary.

## [3.9.7](https://github.com/schemathesis/schemathesis/compare/v3.9.6...v3.9.7) - 2021-07-26

### :rocket: Added

-   New `process_call_kwargs` CLI hook. [#1233](https://github.com/schemathesis/schemathesis/issues/1233)

### :wrench: Changed

-   Check non-string response status codes when Open API links are
    collected. [#1226](https://github.com/schemathesis/schemathesis/issues/1226)

## [3.9.6](https://github.com/schemathesis/schemathesis/compare/v3.9.5...v3.9.6) - 2021-07-15

### :rocket: Added

-   New `before_call` and `after_call` CLI hooks. [#1224](https://github.com/schemathesis/schemathesis/issues/1224), [#700](https://github.com/schemathesis/schemathesis/issues/700)

## [3.9.5](https://github.com/schemathesis/schemathesis/compare/v3.9.4...v3.9.5) - 2021-07-14

### :bug: Fixed

-   Preserve non-body parameter types in requests during Open API
    runtime expression evaluation.

## [3.9.4](https://github.com/schemathesis/schemathesis/compare/v3.9.3...v3.9.4) - 2021-07-09

### :bug: Fixed

-   `KeyError` when the `response_schema_conformance` check is executed
    against responses without schema definition. [#1220](https://github.com/schemathesis/schemathesis/issues/1220)
-   `TypeError` during negative testing on Open API schemas with
    parameters that have non-default `style` value. [#1208](https://github.com/schemathesis/schemathesis/issues/1208)

## [3.9.3](https://github.com/schemathesis/schemathesis/compare/v3.9.2...v3.9.3) - 2021-06-22

### :rocket: Added

-   `ExecutionEvent.is_terminal` attribute that indicates whether an
    event is the last one in the stream.

### :bug: Fixed

-   When `EventStream.stop` is called, the next event always is the last
    one.

## [3.9.2](https://github.com/schemathesis/schemathesis/compare/v3.9.1...v3.9.2) - 2021-06-16

### :wrench: Changed

-   Return `response` from `Case.call_and_validate`.

### :bug: Fixed

-   Incorrect deduplication applied to response schema conformance
    failures that happen to have the same failing validator but
    different input values. [#907](https://github.com/schemathesis/schemathesis/issues/907)

## [3.9.1](https://github.com/schemathesis/schemathesis/compare/v3.9.0...v3.9.1) - 2021-06-13

### :wrench: Changed

-   `ExecutionEvent.asdict` adds the `event_type` field which is the
    event class name.
-   Add API schema to the `Initialized` event.
-   **Internal**: Add `SerializedCase.cookies`
-   Convert all `FailureContext` class attributes to instance
    attributes. For simpler serialization via `attrs`.

## [3.9.0](https://github.com/schemathesis/schemathesis/compare/v3.8.0...v3.9.0) - 2021-06-07

### :rocket: Added

-   GraphQL support in CLI. [#746](https://github.com/schemathesis/schemathesis/issues/746)
-   A way to stop the Schemathesis runner's event stream manually via
    `events.stop()` / `events.finish()` methods. [#1202](https://github.com/schemathesis/schemathesis/issues/1202)

### :wrench: Changed

-   Avoid `pytest` warnings when internal Schemathesis classes are in
    the test module scope.

## [3.8.0](https://github.com/schemathesis/schemathesis/compare/v3.7.8...v3.8.0) - 2021-06-03

### :rocket: Added

-   Negative testing. [#65](https://github.com/schemathesis/schemathesis/issues/65)
-   `Case.data_generation_method` attribute that provides the
    information of the underlying data generation method (e.g. positive
    or negative)

### :wrench: Changed

-   Raise `UsageError` if `schema.parametrize` or `schema.given` are
    applied to the same function more than once. [#1194](https://github.com/schemathesis/schemathesis/issues/1194)
-   Python values of `True`, `False` and `None` are converted to their
    JSON equivalents when generated for path parameters or query. [#1166](https://github.com/schemathesis/schemathesis/issues/1166)
-   Bump `hypothesis-jsonschema` version. It allows the end-user to
    override known string formats.
-   Bump `hypothesis` version.
-   `APIOperation.make_case` behavior. If no `media_type` is passed
    along with `body`, then it tries to infer the proper media type and
    raises an error if it is not possible. [#1094](https://github.com/schemathesis/schemathesis/issues/1094)

### :bug: Fixed

-   Compatibility with `hypothesis>=6.13.3`.

## [3.7.8](https://github.com/schemathesis/schemathesis/compare/v3.7.7...v3.7.8) - 2021-06-02

### :bug: Fixed

-   Open API `style` & `explode` for parameters derived from security
    definitions.

## [3.7.7](https://github.com/schemathesis/schemathesis/compare/v3.7.6...v3.7.7) - 2021-06-01

### :bug: Fixed

-   Apply the Open API's `style` & `explode` keywords to explicit
    examples. [#1190](https://github.com/schemathesis/schemathesis/issues/1190)

## [3.7.6](https://github.com/schemathesis/schemathesis/compare/v3.7.5...v3.7.6) - 2021-05-31

### :bug: Fixed

-   Disable filtering optimization for headers when there are keywords
    other than `type`. [#1189](https://github.com/schemathesis/schemathesis/issues/1189)

## [3.7.5](https://github.com/schemathesis/schemathesis/compare/v3.7.4...v3.7.5) - 2021-05-31

### :bug: Fixed

-   Too much filtering in headers that have schemas with the `pattern`
    keyword. [#1189](https://github.com/schemathesis/schemathesis/issues/1189)

## [3.7.4](https://github.com/schemathesis/schemathesis/compare/v3.7.3...v3.7.4) - 2021-05-28

### :wrench: Changed

-   **Internal**: `SerializedCase.path_template` returns path templates
    as they are in the schema, without base path.

## [3.7.3](https://github.com/schemathesis/schemathesis/compare/v3.7.2...v3.7.3) - 2021-05-28

### :bug: Fixed

-   Invalid multipart payload generated for unusual schemas for the
    `multipart/form-data` media type.

### :racing_car: Performance

-   Reduce the amount of filtering needed to generate valid headers and
    cookies.

## [3.7.2](https://github.com/schemathesis/schemathesis/compare/v3.7.1...v3.7.2) - 2021-05-27

### :rocket: Added

-   `SerializedCase.media_type` that stores the information about what
    media type was used for a particular case.

### :bug: Fixed

-   Internal error on unusual schemas for the `multipart/form-data`
    media type. [#1152](https://github.com/schemathesis/schemathesis/issues/1152)
-   Ignored explicit `Content-Type` override in
    `Case.as_requests_kwargs`.

## [3.7.1](https://github.com/schemathesis/schemathesis/compare/v3.7.0...v3.7.1) - 2021-05-23

### :rocket: Added

-   **Internal**: `FailureContext.title` attribute that gives a short
    failure description.
-   **Internal**: `FailureContext.message` attribute that gives a longer
    failure description.

### :wrench: Changed

-   Rename `JSONDecodeErrorContext.message` to
    `JSONDecodeErrorContext.validation_message` for consistency.
-   Store the more precise `schema` & `instance` in
    `ValidationErrorContext`.
-   Rename `ResponseTimeout` to `RequestTimeout`.

## [3.7.0](https://github.com/schemathesis/schemathesis/compare/v3.6.11...v3.7.0) - 2021-05-23

### :rocket: Added

-   Additional context for each failure coming from the runner. It
    allows the end-user to customize failure formatting.

### :wrench: Changed

-   Use different exception classes for `not_a_server_error` and
    `status_code_conformance` checks. It improves the variance of found
    errors.
-   All network requests (not WSGI) now have the default timeout of 10
    seconds. If the response is time-outing, Schemathesis will report it
    as a failure. It also solves the case when the tested app hangs. [#1164](https://github.com/schemathesis/schemathesis/issues/1164)
-   The default test duration deadline is extended to 15 seconds.

## [3.6.11](https://github.com/schemathesis/schemathesis/compare/v3.6.10...v3.6.11) - 2021-05-20

### :rocket: Added

-   Internal: `BeforeExecution.verbose_name` &
    `SerializedCase.verbose_name` that reflect specification-specific
    API operation name.

## [3.6.10](https://github.com/schemathesis/schemathesis/compare/v3.6.9...v3.6.10) - 2021-05-17

### :wrench: Changed

-   Explicitly add `colorama` to project's dependencies.
-   Bump `hypothesis-jsonschema` version.

## [3.6.9](https://github.com/schemathesis/schemathesis/compare/v3.6.8...v3.6.9) - 2021-05-14

### :bug: Fixed

-   Ignored `$ref` keyword in schemas with deeply nested references. [#1167](https://github.com/schemathesis/schemathesis/issues/1167)
-   Ignored Open API specific keywords & types in schemas with deeply
    nested references. [#1162](https://github.com/schemathesis/schemathesis/issues/1162)

## [3.6.8](https://github.com/schemathesis/schemathesis/compare/v3.6.7...v3.6.8) - 2021-05-13

### :wrench: Changed

-   Relax dependency on `starlette` to `>=0.13,<1`. [#1160](https://github.com/schemathesis/schemathesis/issues/1160)

## [3.6.7](https://github.com/schemathesis/schemathesis/compare/v3.6.6...v3.6.7) - 2021-05-12

### :bug: Fixed

-   Missing support for the `date` string format (only `full-date` was
    supported).

## [3.6.6](https://github.com/schemathesis/schemathesis/compare/v3.6.5...v3.6.6) - 2021-05-07

### :wrench: Changed

-   Improve error message for failing Hypothesis deadline healthcheck
    in CLI. [#880](https://github.com/schemathesis/schemathesis/issues/880)

## [3.6.5](https://github.com/schemathesis/schemathesis/compare/v3.6.4...v3.6.5) - 2021-05-07

### :rocket: Added

-   Support for disabling ANSI color escape codes via the
    `NO_COLOR \<https://no-color.org/\>`
    environment variable or the `--no-color` CLI option. [#1153](https://github.com/schemathesis/schemathesis/issues/1153)

### :wrench: Changed

-   Generate valid header values for Bearer auth by construction rather
    than by filtering.

## [3.6.4](https://github.com/schemathesis/schemathesis/compare/v3.6.3...v3.6.4) - 2021-04-30

### :wrench: Changed

-   Bump minimum `hypothesis-graphql` version to `0.5.0`. It brings
    support for interfaces and unions and fixes a couple of bugs in
    query generation.

## [3.6.3](https://github.com/schemathesis/schemathesis/compare/v3.6.2...v3.6.3) - 2021-04-20

### :bug: Fixed

-   Bump minimum `hypothesis-graphql` version to `0.4.1`. It fixes [a
    problem](https://github.com/Stranger6667/hypothesis-graphql/issues/30)
    with generating queries with surrogate characters.
-   `UnicodeEncodeError` when sending `application/octet-stream`
    payloads that have no `format: binary` in their schemas. [#1134](https://github.com/schemathesis/schemathesis/issues/1134)

## [3.6.2](https://github.com/schemathesis/schemathesis/compare/v3.6.1...v3.6.2) - 2021-04-15

### :bug: Fixed

-   Windows: `UnicodeDecodeError` during schema loading via the
    `from_path` loader if it contains certain Unicode symbols.
    `from_path` loader defaults to `UTF-8`
    from now on.

## [3.6.1](https://github.com/schemathesis/schemathesis/compare/v3.6.0...v3.6.1) - 2021-04-09

### :bug: Fixed

-   Using parametrized `pytest` fixtures with the `from_pytest_fixture`
    loader. [#1121](https://github.com/schemathesis/schemathesis/issues/1121)

## [3.6.0](https://github.com/schemathesis/schemathesis/compare/v3.5.3...v3.6.0) - 2021-04-04

### :rocket: Added

-   Custom keyword arguments to `schemathesis.graphql.from_url` that are
    proxied to `requests.post`.
-   `from_wsgi`, `from_asgi`, `from_path` and `from_file` loaders for
    GraphQL apps. [#1097](https://github.com/schemathesis/schemathesis/issues/1097), [#1100](https://github.com/schemathesis/schemathesis/issues/1100)
-   Support for `data_generation_methods` and `code_sample_style` in all
    GraphQL loaders.
-   Support for `app` & `base_url` arguments for the
    `from_pytest_fixture` runner.
-   Initial support for GraphQL schemas in the Schemathesis runner.

``` python
import schemathesis

# Load schema
schema = schemathesis.graphql.from_url("http://127.0.0.1:8000/graphql")
# Initialize runner
runner = schemathesis.runner.from_schema(schema)
# Emit events
for event in runner.execute():
    ...
```

**Breaking**

-   Loaders' signatures are unified. Most of the arguments became
    keyword-only. All except the first two for ASGI/WSGI, all except the
    first one for the others. It forces loader calls to be more
    consistent.

``` python
# BEFORE
schema = schemathesis.from_uri(
    "http://example.com/openapi.json", "http://127.0.0.1:8000/", "GET"
)
# NOW
schema = schemathesis.from_uri(
    "http://example.com/openapi.json", base_url="http://127.0.0.1:8000/", method="GET"
)
```

### :wrench: Changed

-   Schemathesis generates separate tests for each field defined in the
    GraphQL `Query` type. It makes the testing process unified for both
    Open API and GraphQL schemas.
-   IDs for GraphQL tests use the corresponding `Query` field instead of
    HTTP method & path.
-   Do not show overly verbose raw schemas in Hypothesis output for
    failed GraphQL tests.
-   The `schemathesis.graphql.from_url` loader now uses the usual
    Schemathesis User-Agent.
-   The Hypothesis database now uses separate entries for each API
    operation when executed via CLI. It increases its effectiveness when
    tests are re-run.
-   Module `schemathesis.loaders` is moved to
    `schemathesis.specs.openapi.loaders`.
-   Show a more specific exception on incorrect usage of the `from_path`
    loader in the Schemathesis runner.

### :wastebasket: Deprecated

-   `schemathesis.runner.prepare` will be removed in Schemathesis 4.0.
    Use `schemathesis.runner.from_schema` instead. With this change, the
    schema loading part goes to your code, similar to using the regular
    Schemathesis Python API. It leads to a unified user experience where
    the starting point is API schema loading, which is much clearer than
    passing a callback & keyword arguments to the `prepare` function.

### :bug: Fixed

-   Add the missing `@schema.given` implementation for schemas created
    via the `from_pytest_fixture` loader. [#1093](https://github.com/schemathesis/schemathesis/issues/1093)
-   Silently ignoring some incorrect usages of `@schema.given`.
-   Fixups examples were using the incorrect fixup name.
-   Return type of `make_case` for GraphQL schemas.
-   Missed `operation_id` argument in `from_asgi` loader.

### :fire: Removed

-   Undocumented way to install fixups via the `fixups` argument for
    `schemathesis.runner.prepare` is removed.

## [3.5.3](https://github.com/schemathesis/schemathesis/compare/v3.5.2...v3.5.3) - 2021-03-27

### :bug: Fixed

-   Do not use `importlib-metadata==3.8` in
    dependencies as it causes `RuntimeError`. Ref:
    <https://github.com/python/importlib_metadata/issues/293>

## [3.5.2](https://github.com/schemathesis/schemathesis/compare/v3.5.1...v3.5.2) - 2021-03-24

### :wrench: Changed

-   Prefix worker thread names with `schemathesis_`.

## [3.5.1](https://github.com/schemathesis/schemathesis/compare/v3.5.0...v3.5.1) - 2021-03-23

### :bug: Fixed

-   Encoding for response payloads displayed in the CLI output. [#1073](https://github.com/schemathesis/schemathesis/issues/1073)
-   Use actual charset (from `flask.Response.mimetype_params`) when
    storing WSGI responses rather than defaulting to
    `flask.Response.charset`.

## [3.5.0](https://github.com/schemathesis/schemathesis/compare/v3.4.1...v3.5.0) - 2021-03-22

### :rocket: Added

-   `before_generate_case` hook, that allows the user to modify or
    filter generated `Case` instances. [#1067](https://github.com/schemathesis/schemathesis/issues/1067)

### :bug: Fixed

-   Missing `body` parameters during Open API links processing in CLI. [#1069](https://github.com/schemathesis/schemathesis/issues/1069)
-   Output types for evaluation results of `$response.body` and
    `$request.body` runtime expressions. [#1068](https://github.com/schemathesis/schemathesis/issues/1068)

## [3.4.1](https://github.com/schemathesis/schemathesis/compare/v3.4.0...v3.4.1) - 2021-03-21

### :rocket: Added

-   `event_type` field to the debug output.

## [3.4.0](https://github.com/schemathesis/schemathesis/compare/v3.3.1...v3.4.0) - 2021-03-20

### :rocket: Added

-   `--debug-output-file` CLI option to enable storing the underlying
    runner events in the JSON Lines format in a separate file for
    debugging purposes. [#1059](https://github.com/schemathesis/schemathesis/issues/1059)

### :wrench: Changed

-   Make `Request.body`, `Response.body` and `Response.encoding`
    internal attributes optional. For `Request`, it means that absent
    body will lead to `Request.body` to be `None`. For `Response`,
    `body` will be `None` if the app response did not have any payload.
    Previously these values were empty strings, which was not
    distinguishable from the cases described above. For the end-user, it
    means that in VCR cassettes, fields `request.body` and
    `response.body` may be absent.
-   `models.Status` enum now has string values for more readable
    representation.

## [3.3.1](https://github.com/schemathesis/schemathesis/compare/v3.3.0...v3.3.1) - 2021-03-18

### :bug: Fixed

-   Displaying wrong headers in the `FAILURES` block of the CLI output. [#792](https://github.com/schemathesis/schemathesis/issues/792)

## [3.3.0](https://github.com/schemathesis/schemathesis/compare/v3.2.2...v3.3.0) - 2021-03-17

### :rocket: Added

-   Display failing response payload in the CLI output, similarly to the
    pytest plugin output. [#1050](https://github.com/schemathesis/schemathesis/issues/1050)
-   A way to control which code sample style to use - Python or cURL. [#908](https://github.com/schemathesis/schemathesis/issues/908)

### :bug: Fixed

-   `UnicodeDecodeError` when generating cURL commands for failed test
    case reproduction if the request's body contains non-UTF8
    characters.

**Internal**

-   Extra information to events, emitted by the Schemathesis runner.

## [3.2.2](https://github.com/schemathesis/schemathesis/compare/v3.2.1...v3.2.2) - 2021-03-11

### :rocket: Added

-   Support for Hypothesis 6. [#1013](https://github.com/schemathesis/schemathesis/issues/1013)

## [3.2.1](https://github.com/schemathesis/schemathesis/compare/v3.2.0...v3.2.1) - 2021-03-10

### :bug: Fixed

-   Wrong test results in some cases when the tested schema contains a
    media type that Schemathesis doesn't know how to work with. [#1046](https://github.com/schemathesis/schemathesis/issues/1046)

## [3.2.0](https://github.com/schemathesis/schemathesis/compare/v3.1.3...v3.2.0) - 2021-03-09

### :racing_car: Performance

-   Add an internal caching layer for data generation strategies. It
    relies on the fact that the internal `BaseSchema` structure is not
    mutated over time. It is not directly possible through the public
    API and is discouraged from doing through hook functions.

### :wrench: Changed

-   `APIOperation` and subclasses of `Parameter` are now compared by
    their identity rather than by value.

## [3.1.3](https://github.com/schemathesis/schemathesis/compare/v3.1.2...v3.1.3) - 2021-03-08

### :rocket: Added

-   `count_operations` boolean flag to `runner.prepare`. In case of
    `False` value, Schemathesis won't count the total number of
    operations upfront. It improves performance for the direct `runner`
    usage, especially on large schemas. Schemathesis CLI will still use
    these calculations to display the progress during execution, but
    this behavior may become configurable in the future.

## [3.1.2](https://github.com/schemathesis/schemathesis/compare/v3.1.1...v3.1.2) - 2021-03-08

### :bug: Fixed

-   Percent-encode the generated `.` and `..` strings in path parameters
    to avoid resolving relative paths and changing the tested path
    structure. [#1036](https://github.com/schemathesis/schemathesis/issues/1036)

## [3.1.1](https://github.com/schemathesis/schemathesis/compare/v3.1.0...v3.1.1) - 2021-03-05

### :bug: Fixed

-   Loosen `importlib-metadata` version constraint and update `pyproject.toml`. [#1039](https://github.com/schemathesis/schemathesis/issues/1039)

## [3.1.0](https://github.com/schemathesis/schemathesis/compare/v3.0.9...v3.1.0) - 2021-02-11

### :rocket: Added

-   Support for external examples via the `externalValue` keyword. [#884](https://github.com/schemathesis/schemathesis/issues/884)

### :bug: Fixed

-   Prevent a small terminal width causing a crash (due to negative
    length used in an f-string) when printing percentage
-   Support the latest `cryptography` version in Docker images. [#1033](https://github.com/schemathesis/schemathesis/issues/1033)

## [3.0.9](https://github.com/schemathesis/schemathesis/compare/v3.0.8...v3.0.9) - 2021-02-10

### :bug: Fixed

-   Return a default terminal size to prevent crashes on systems with
    zero-width terminals (some CI/CD servers).

## [3.0.8](https://github.com/schemathesis/schemathesis/compare/v3.0.7...v3.0.8) - 2021-02-04

-   This release updates the documentation to be in-line with the
    current state.

## [3.0.7](https://github.com/schemathesis/schemathesis/compare/v3.0.6...v3.0.7) - 2021-01-31

### :bug: Fixed

-   Docker tags for Buster-based images.

## [3.0.6](https://github.com/schemathesis/schemathesis/compare/v3.0.5...v3.0.6) - 2021-01-31

-   Packaging-only release for Docker images based on Debian Buster. [#1028](https://github.com/schemathesis/schemathesis/issues/1028)

## [3.0.5](https://github.com/schemathesis/schemathesis/compare/v3.0.4...v3.0.5) - 2021-01-30

### :bug: Fixed

-   Allow to use any iterable type for `checks` and `additional_checks`
    arguments to `Case.validate_response`.

## [3.0.4](https://github.com/schemathesis/schemathesis/compare/v3.0.3...v3.0.4) - 2021-01-19

### :bug: Fixed

-   Generating stateful tests, with common parameters behind a
    reference. [#1020](https://github.com/schemathesis/schemathesis/issues/1020)
-   Programmatic addition of Open API links via `add_link` when schema
    validation is disabled and response status codes are noted as
    integers. [#1022](https://github.com/schemathesis/schemathesis/issues/1022)

### :wrench: Changed

-   When operations are resolved by `operationId` then the same
    reference resolving logic is applied as in other cases. This change
    leads to less reference inlining and lower memory consumption for
    deeply nested schemas. [#945](https://github.com/schemathesis/schemathesis/issues/945)

## [3.0.3](https://github.com/schemathesis/schemathesis/compare/v3.0.2...v3.0.3) - 2021-01-18

### :bug: Fixed

-   `Flaky` Hypothesis error during explicit examples generation. [#1018](https://github.com/schemathesis/schemathesis/issues/1018)

## [3.0.2](https://github.com/schemathesis/schemathesis/compare/v3.0.1...v3.0.2) - 2021-01-15

### :bug: Fixed

-   Processing parameters common for multiple API operations if they are
    behind a reference. [#1015](https://github.com/schemathesis/schemathesis/issues/1015)

## [3.0.1](https://github.com/schemathesis/schemathesis/compare/v3.0.0...v3.0.1) - 2021-01-15

### :rocket: Added

-   YAML serialization for `text/yaml`, `text/x-yaml`,
    `application/x-yaml` and `text/vnd.yaml` media types. [#1010](https://github.com/schemathesis/schemathesis/issues/1010).

## [3.0.0](https://github.com/schemathesis/schemathesis/compare/v2.8.6...v3.0.0) - 2021-01-14

### :rocket: Added

-   Support for sending `text/plain` payload as test data. Including
    variants with non-default `charset`. [#850](https://github.com/schemathesis/schemathesis/issues/850), [#939](https://github.com/schemathesis/schemathesis/issues/939)
-   Generating data for all media types defined for an operation. [#690](https://github.com/schemathesis/schemathesis/issues/690)
-   Support for user-defined media types serialization. You can define
    how Schemathesis should handle media types defined in your schema or
    customize existing (like `application/json`).
-   The `response_schema_conformance` check
    now runs on media types that are encoded with JSON. For example,
    `application/problem+json`. [#920](https://github.com/schemathesis/schemathesis/issues/920)
-   Base URL for GraphQL schemas. It allows you to load the schema from
    one place but send test requests to another one. [#934](https://github.com/schemathesis/schemathesis/issues/934)
-   A helpful error message when an operation is not found during the
    direct schema access. [#812](https://github.com/schemathesis/schemathesis/issues/812)
-   `--dry-run` CLI option. When applied, Schemathesis won't send any
    data to the server and won't perform any response checks. [#963](https://github.com/schemathesis/schemathesis/issues/963)
-   A better error message when the API schema contains an invalid
    regular expression syntax. [#1003](https://github.com/schemathesis/schemathesis/issues/1003)

### :wrench: Changed

-   Open API parameters parsing to unblock supporting multiple media
    types per operation. Their definitions aren't converted to JSON
    Schema equivalents right away but deferred instead and stored as-is.
-   Missing `required: true` in path parameters definition is now
    automatically enforced if schema validation is disabled. According
    to the Open API spec, the `required` keyword value should be `true`
    for path parameters. This change allows Schemathesis to generate
    test cases even for endpoints containing optional path parameters
    (which is not compliant with the spec). [#941](https://github.com/schemathesis/schemathesis/issues/941)
-   Using `--auth` together with `--header` that sets the
    `Authorization` header causes a validation error. Before, the
    `--header` value was ignored in such cases, and the basic auth
    passed in `--auth` was used. [#911](https://github.com/schemathesis/schemathesis/issues/911)
-   When `hypothesis-jsonschema` fails to resolve recursive references,
    the test is skipped with an error message that indicates why it
    happens.
-   Shorter error messages when API operations have logical errors in
    their schema. For example, when the maximum is less than the
    minimum - `{"type": "integer", "minimum": 5, "maximum": 4}`.
-   If multiple non-check related failures happens during a test of a
    single API operation, they are displayed as is, instead of
    Hypothesis-level error messages about multiple found failures or
    flaky tests. [#975](https://github.com/schemathesis/schemathesis/issues/975)
-   Catch schema parsing errors, that are caused by YAML parsing.
-   The built-in test server now accepts `--operations` instead of
    `--endpoints`.
-   Display `Collected API operations` instead of `collected endpoints`
    in the CLI. [#869](https://github.com/schemathesis/schemathesis/issues/869)
-   `--skip-deprecated-endpoints` is renamed to
    `--skip-deprecated-operations`. [#869](https://github.com/schemathesis/schemathesis/issues/869)
-   Rename various internal API methods that contained `endpoint` in
    their names. [#869](https://github.com/schemathesis/schemathesis/issues/869)
-   Bump `hypothesis-jsonschema` version to `0.19.0`. This version
    improves the handling of unsupported regular expression syntax and
    can generate data for a subset of schemas containing such regular
    expressions.
-   Schemathesis doesn't stop testing on errors during schema parsing.
    These errors are handled the same way as other errors during the
    testing process. It allows Schemathesis to test API operations with
    valid definitions and report problematic operations instead of
    failing the whole run. [#999](https://github.com/schemathesis/schemathesis/issues/999)

### :bug: Fixed

-   Allow generating requests without payload if the schema does not
    require it. [#916](https://github.com/schemathesis/schemathesis/issues/916)
-   Allow sending `null` as request payload if the schema expects it. [#919](https://github.com/schemathesis/schemathesis/issues/919)
-   CLI failure if the tested operation is
    `GET` and has payload examples. [#925](https://github.com/schemathesis/schemathesis/issues/925)
-   Excessive reference inlining that leads to out-of-memory for large
    schemas with deep references. [#945](https://github.com/schemathesis/schemathesis/issues/945), [#671](https://github.com/schemathesis/schemathesis/issues/671)
-   `--exitfirst` CLI option trims the progress bar output when a
    failure occurs. [#951](https://github.com/schemathesis/schemathesis/issues/951)
-   Internal error if filling missing explicit examples led to
    `Unsatisfiable` errors. [#904](https://github.com/schemathesis/schemathesis/issues/904)
-   Do not suggest to disable schema validation if it is already
    disabled. [#914](https://github.com/schemathesis/schemathesis/issues/914)
-   Skip explicit examples generation if this phase is disabled via
    config. [#905](https://github.com/schemathesis/schemathesis/issues/905)
-   `Unsatisfiable` error in stateful testing caused by all API
    operations having inbound links. [#965](https://github.com/schemathesis/schemathesis/issues/965), [#822](https://github.com/schemathesis/schemathesis/issues/822)
-   A possibility to override `APIStateMachine.step`. [#970](https://github.com/schemathesis/schemathesis/issues/970)
-   `TypeError` on nullable parameters during Open API specific
    serialization. [#980](https://github.com/schemathesis/schemathesis/issues/980)
-   Invalid types in `x-examples`. [#982](https://github.com/schemathesis/schemathesis/issues/982)
-   CLI crash on schemas with operation names longer than the current
    terminal width. [#990](https://github.com/schemathesis/schemathesis/issues/990)
-   Handling of API operations that contain reserved characters in their
    paths. [#992](https://github.com/schemathesis/schemathesis/issues/992)
-   CLI execution stops on errors during example generation. [#994](https://github.com/schemathesis/schemathesis/issues/994)
-   Fill missing properties in incomplete explicit examples for non-body
    parameters. [#1007](https://github.com/schemathesis/schemathesis/issues/1007)

### :wastebasket: Deprecated

-   `HookContext.endpoint`. Use `HookContext.operation` instead.
-   `Case.endpoint`. Use `Case.operation` instead.

### :racing_car: Performance

-   Use compiled versions of Open API spec validators.
-   Decrease CLI memory usage. [#987](https://github.com/schemathesis/schemathesis/issues/987)
-   Various improvements relevant to processing of API operation
    definitions. It gives ~20% improvement on large schemas with many
    references.

### :fire: Removed

-   `Case.form_data`. Use `Case.body` instead.
-   `Endpoint.form_data`. Use `Endpoint.body` instead.
-   `before_generate_form_data` hook. Use `before_generate_body`
    instead.
-   Deprecated stateful testing integration from our `pytest` plugin.

> [!NOTE]
> This release features multiple backward-incompatible changes. The
> first one is removing `form_data` and hooks related to it -all payload
> related actions can be done via `body` and its hooks. The second one
> involves renaming the so-called "endpoint" to "operation". The main
> reason for this is to generalize terminology and make it applicable to
> GraphQL schemas, as all Schemathesis internals are more suited to work
> with semantically different API operations rather than with endpoints
> that are often connected with URLs and HTTP methods. It brings the
> possibility to reuse the same concepts for Open API and GraphQL - in
> the future, unit tests will cover individual API operations in
> GraphQL, rather than everything available under the same "endpoint".

## [2.8.6](https://github.com/schemathesis/schemathesis/compare/v2.8.5...v2.8.6) - 2022-03-29

### :rocket: Added

-   Support for Werkzeug\>=2.1.0. [#1410](https://github.com/schemathesis/schemathesis/issues/1410)

## [2.8.5](https://github.com/schemathesis/schemathesis/compare/v2.8.4...v2.8.5) - 2020-12-15

### :rocket: Added

-   `auto` variant for the `--workers` CLI option that automatically
    detects the number of available CPU cores to run tests on. [#917](https://github.com/schemathesis/schemathesis/issues/917)

## [2.8.4](https://github.com/schemathesis/schemathesis/compare/v2.8.3...v2.8.4) - 2020-11-27

### :bug: Fixed

-   Use `--request-tls-verify` during schema loading as well. [#897](https://github.com/schemathesis/schemathesis/issues/897)

## [2.8.3](https://github.com/schemathesis/schemathesis/compare/v2.8.2...v2.8.3) - 2020-11-27

### :rocket: Added

-   Display failed response payload in the error output for the `pytest`
    plugin. [#895](https://github.com/schemathesis/schemathesis/issues/895)

### :wrench: Changed

-   In pytest plugin output, Schemathesis error classes use the
    `CheckFailed` name. Before, they had
    not readable "internal" names.
-   Hypothesis falsifying examples. The code does not include `Case`
    attributes with default values to improve readability. [#886](https://github.com/schemathesis/schemathesis/issues/886)

## [2.8.2](https://github.com/schemathesis/schemathesis/compare/v2.8.1...v2.8.2) - 2020-11-25

### :bug: Fixed

-   Internal error in CLI, when the `base_url` is an invalid IPv6. [#890](https://github.com/schemathesis/schemathesis/issues/890)
-   Internal error in CLI, when a malformed regex is passed to `-E` /
    `-M` / `-T` / `-O` CLI options. [#889](https://github.com/schemathesis/schemathesis/issues/889)

## [2.8.1](https://github.com/schemathesis/schemathesis/compare/v2.8.0...v2.8.1) - 2020-11-24

### :rocket: Added

-   `--force-schema-version` CLI option to force Schemathesis to use the
    specific Open API spec version when parsing the schema. [#876](https://github.com/schemathesis/schemathesis/issues/876)

### :wrench: Changed

-   The `content_type_conformance` check now raises a well-formed error
    message when encounters a malformed media type value. [#877](https://github.com/schemathesis/schemathesis/issues/877)

### :bug: Fixed

-   Internal error during verifying explicit examples if an example has
    no `value` key. [#882](https://github.com/schemathesis/schemathesis/issues/882)

## [2.8.0](https://github.com/schemathesis/schemathesis/compare/v2.7.7...v2.8.0) - 2020-11-24

### :rocket: Added

-   `--request-tls-verify` CLI option, that controls whether
    Schemathesis verifies the server's TLS certificate. You can also
    pass the path to a CA_BUNDLE file for private certs. [#830](https://github.com/schemathesis/schemathesis/issues/830)

### :wrench: Changed

-   In CLI, if an endpoint contains an invalid schema, show a message
    about the `--validate-schema` CLI option. [#855](https://github.com/schemathesis/schemathesis/issues/855)

### :bug: Fixed

-   Handling of 204 responses in the `response_schema_conformance`
    check. Before, all responses were required to have the
    `Content-Type` header. [#844](https://github.com/schemathesis/schemathesis/issues/844)
-   Catch `OverflowError` when an invalid regex is passed to `-E` / `-M`
    / `-T` / `-O` CLI options. [#870](https://github.com/schemathesis/schemathesis/issues/870)
-   Internal error in CLI, when the schema location is an invalid IPv6. [#872](https://github.com/schemathesis/schemathesis/issues/872)
-   Collecting Open API links behind references via CLI. [#874](https://github.com/schemathesis/schemathesis/issues/874)

### :wastebasket: Deprecated

-   Using of `Case.form_data` and `Endpoint.form_data`. In the `3.0`
    release, you'll need to use relevant `body` attributes instead. This
    change includes deprecation of the `before_generate_form_data` hook,
    use `before_generate_body` instead. The reason for this is the
    upcoming unification of parameter handling and their serialization.
-   `--stateful-recursion-limit`. It will be removed in `3.0` as a part
    of removing the old stateful testing approach. This parameter is
    no-op.

## [2.7.7](https://github.com/schemathesis/schemathesis/compare/v2.7.6...v2.7.7) - 2020-11-13

### :bug: Fixed

-   Missed `headers` in `Endpoint.partial_deepcopy`.

## [2.7.6](https://github.com/schemathesis/schemathesis/compare/v2.7.5...v2.7.6) - 2020-11-12

### :rocket: Added

-   An option to set data generation methods. At the moment, it includes
    only "positive", which means that Schemathesis will generate data
    that matches the schema.

### :bug: Fixed

-   Pinned dependency on `attrs` that caused an error on fresh
    installations. [#858](https://github.com/schemathesis/schemathesis/issues/858)

## [2.7.5](https://github.com/schemathesis/schemathesis/compare/v2.7.4...v2.7.5) - 2020-11-09

### :bug: Fixed

-   Invalid keyword in code samples that Schemathesis suggests to run to
    reproduce errors. [#851](https://github.com/schemathesis/schemathesis/issues/851)

## [2.7.4](https://github.com/schemathesis/schemathesis/compare/v2.7.3...v2.7.4) - 2020-11-07

### :rocket: Added

-   New `relative_path` property for `BeforeExecution` and
    `AfterExecution` events. It represents an operation path as it is in
    the schema definition.

## [2.7.3](https://github.com/schemathesis/schemathesis/compare/v2.7.2...v2.7.3) - 2020-11-05

### :bug: Fixed

-   Internal error on malformed JSON when the `response_conformance`
    check is used. [#832](https://github.com/schemathesis/schemathesis/issues/832)

## [2.7.2](https://github.com/schemathesis/schemathesis/compare/v2.7.1...v2.7.2) - 2020-11-05

### :rocket: Added

-   Shortcut for response validation when Schemathesis's data generation
    is not used. [#485](https://github.com/schemathesis/schemathesis/issues/485)

### :wrench: Changed

-   Improve the error message when the application can not be loaded
    from the value passed to the `--app` command-line option. [#836](https://github.com/schemathesis/schemathesis/issues/836)
-   Security definitions are now serialized as other parameters. At the
    moment, it means that the generated values will be coerced to
    strings, which is a no-op. However, types of security definitions
    might be affected by the "Negative testing" feature in the future.
    Therefore this change is mostly for future-compatibility. [#841](https://github.com/schemathesis/schemathesis/issues/841)

### :bug: Fixed

-   Internal error when a "header" / "cookie" parameter were not coerced
    to a string before filtration. [#839](https://github.com/schemathesis/schemathesis/issues/839)

## [2.7.1](https://github.com/schemathesis/schemathesis/compare/v2.7.0...v2.7.1) - 2020-10-22

### :bug: Fixed

-   Adding new Open API links via the `add_link` method, when the
    related PathItem contains a reference. [#824](https://github.com/schemathesis/schemathesis/issues/824)

## [2.7.0](https://github.com/schemathesis/schemathesis/compare/v2.6.1...v2.7.0) - 2020-10-21

### :rocket: Added

-   New approach to stateful testing, based on the Hypothesis's
    `RuleBasedStateMachine`. [#737](https://github.com/schemathesis/schemathesis/issues/737)
-   `Case.validate_response` accepts the new `additional_checks`
    argument. It provides a way to execute additional checks in addition
    to existing ones.

### :wrench: Changed

-   The `response_schema_conformance` and `content_type_conformance`
    checks fail unconditionally if the input response has no
    `Content-Type` header. [#816](https://github.com/schemathesis/schemathesis/issues/816)

### :bug: Fixed

-   Failure reproduction code missing values that were explicitly passed
    to `call_*` methods during testing. [#814](https://github.com/schemathesis/schemathesis/issues/814)

### :wastebasket: Deprecated

-   Using `stateful=Stateful.links` in schema loaders and `parametrize`.
    Use `schema.as_state_machine().TestCase` instead. The old approach
    to stateful testing will be removed in `3.0`. See the
    `Stateful testing` section of our documentation for more
    information.

## [2.6.1](https://github.com/schemathesis/schemathesis/compare/v2.6.0...v2.6.1) - 2020-10-19

### :rocket: Added

-   New method `as_curl_command` added to the `Case` class. [#689](https://github.com/schemathesis/schemathesis/issues/689)

## [2.6.0](https://github.com/schemathesis/schemathesis/compare/v2.5.1...v2.6.0) - 2020-10-06

### :rocket: Added

-   Support for passing Hypothesis strategies to tests created with
    `schema.parametrize` by using `schema.given` decorator. [#768](https://github.com/schemathesis/schemathesis/issues/768)
-   Support for PEP561. [#748](https://github.com/schemathesis/schemathesis/issues/748)
-   Shortcut for calling & validation. [#738](https://github.com/schemathesis/schemathesis/issues/738)
-   New hook to pre-commit, `rstcheck`, as well as updates to
    documentation based on rstcheck. [#734](https://github.com/schemathesis/schemathesis/issues/734)
-   New check for maximum response time and corresponding CLI option
    `--max-response-time`. [#716](https://github.com/schemathesis/schemathesis/issues/716)
-   New `response_headers_conformance` check that verifies the presence
    of all headers defined for a response. [#742](https://github.com/schemathesis/schemathesis/issues/742)
-   New field with information about executed checks in cassettes. [#702](https://github.com/schemathesis/schemathesis/issues/702)
-   New `port` parameter added to `from_uri()` method. [#706](https://github.com/schemathesis/schemathesis/issues/706)
-   A code snippet to reproduce a failed check when running Python
    tests. [#793](https://github.com/schemathesis/schemathesis/issues/793)
-   Python 3.9 support. [#731](https://github.com/schemathesis/schemathesis/issues/731)
-   Ability to skip deprecated endpoints with
    `--skip-deprecated-endpoints` CLI option and
    `skip_deprecated_operations=True` argument to schema loaders. [#715](https://github.com/schemathesis/schemathesis/issues/715)

### :bug: Fixed

-   `User-Agent` header overriding the passed one. [#757](https://github.com/schemathesis/schemathesis/issues/757)
-   Default `User-Agent` header in `Case.call`. [#717](https://github.com/schemathesis/schemathesis/issues/717)
-   Status of individual interactions in VCR cassettes. Before this
    change, all statuses were taken from the overall test outcome,
    rather than from the check results for a particular response. [#695](https://github.com/schemathesis/schemathesis/issues/695)
-   Escaping header values in VCR cassettes. [#783](https://github.com/schemathesis/schemathesis/issues/783)
-   Escaping HTTP response message in VCR cassettes. [#788](https://github.com/schemathesis/schemathesis/issues/788)

### :wrench: Changed

-   `Case.as_requests_kwargs` and `Case.as_werkzeug_kwargs` now return
    the `User-Agent` header. This change also affects code snippets for
    failure reproduction - all snippets will include the `User-Agent`
    header.

### :racing_car: Performance

-   Speed up generation of `headers`, `cookies`, and `formData`
    parameters when their schemas do not define the `type` keyword. [#795](https://github.com/schemathesis/schemathesis/issues/795)

## [2.5.1](https://github.com/schemathesis/schemathesis/compare/v2.5.0...v2.5.1) - 2020-09-30

This release contains only documentation updates which are necessary to
upload to PyPI.

## [2.5.0](https://github.com/schemathesis/schemathesis/compare/v2.4.1...v2.5.0) - 2020-09-27

### :rocket: Added

-   Stateful testing via Open API links for the `pytest` runner. [#616](https://github.com/schemathesis/schemathesis/issues/616)
-   Support for GraphQL tests for the `pytest` runner. [#649](https://github.com/schemathesis/schemathesis/issues/649)

### :bug: Fixed

-   Progress percentage in the terminal output for "lazy" schemas. [#636](https://github.com/schemathesis/schemathesis/issues/636)

### :wrench: Changed

-   Check name is no longer displayed in the CLI output, since its
    verbose message is already displayed. This change also simplifies
    the internal structure of the runner events.
-   The `stateful` argument type in the `runner.prepare` is
    `Optional[Stateful]` instead of `Optional[str]`. Use
    `schemathesis.Stateful` enum.

## [2.4.1](https://github.com/schemathesis/schemathesis/compare/v2.4.0...v2.4.1) - 2020-09-17

### :wrench: Changed

-   Hide `Case.endpoint` from representation. Its representation
    decreases the usability of the pytest's output. [#719](https://github.com/schemathesis/schemathesis/issues/719)
-   Return registered functions from `register_target` and
    `register_check` decorators. [#721](https://github.com/schemathesis/schemathesis/issues/721)

### :bug: Fixed

-   Possible `IndexError` when a user-defined check raises an exception
    without a message. [#718](https://github.com/schemathesis/schemathesis/issues/718)

## [2.4.0](https://github.com/schemathesis/schemathesis/compare/v2.3.4...v2.4.0) - 2020-09-15

### :rocket: Added

-   Ability to register custom targets for targeted testing. [#686](https://github.com/schemathesis/schemathesis/issues/686)

### :wrench: Changed

-   The `AfterExecution` event now has `path` and `method` fields,
    similar to the `BeforeExecution` one. The goal is to make these
    events self-contained, which improves their usability.

## [2.3.4](https://github.com/schemathesis/schemathesis/compare/v2.3.3...v2.3.4) - 2020-09-11

### :wrench: Changed

-   The default Hypothesis's `deadline` setting for tests with
    `schema.parametrize` is set to 500 ms for consistency with the CLI
    behavior. [#705](https://github.com/schemathesis/schemathesis/issues/705)

### :bug: Fixed

-   Encoding error when writing a cassette on Windows. [#708](https://github.com/schemathesis/schemathesis/issues/708)

## [2.3.3](https://github.com/schemathesis/schemathesis/compare/v2.3.2...v2.3.3) - 2020-08-04

### :bug: Fixed

-   `KeyError` during the `content_type_conformance` check if the
    response has no `Content-Type` header. [#692](https://github.com/schemathesis/schemathesis/issues/692)

## [2.3.2](https://github.com/schemathesis/schemathesis/compare/v2.3.1...v2.3.2) - 2020-08-04

### :rocket: Added

-   Run checks conditionally.

## [2.3.1](https://github.com/schemathesis/schemathesis/compare/v2.3.0...v2.3.1) - 2020-07-28

### :bug: Fixed

-   `IndexError` when `examples` list is empty.

## [2.3.0](https://github.com/schemathesis/schemathesis/compare/v2.2.1...v2.3.0) - 2020-07-26

### :rocket: Added

-   Possibility to generate values for `in: formData` parameters that
    are non-bytes or contain non-bytes (e.g., inside an array). [#665](https://github.com/schemathesis/schemathesis/issues/665)

### :wrench: Changed

-   Error message for cases when a path parameter is in the template but
    is not defined in the parameters list or missing `required: true` in
    its definition. [#667](https://github.com/schemathesis/schemathesis/issues/667)
-   Bump minimum required `hypothesis-jsonschema` version to
    `0.17.0`. This allows Schemathesis to
    use the `custom_formats` argument in `from_schema` calls and avoid
    using its private API. [#684](https://github.com/schemathesis/schemathesis/issues/684)

### :bug: Fixed

-   `ValueError` during sending a request with test payload if the
    endpoint defines a parameter with `type: array` and `in: formData`. [#661](https://github.com/schemathesis/schemathesis/issues/661)
-   `KeyError` while processing a schema with nullable parameters and
    `in: body`. [#660](https://github.com/schemathesis/schemathesis/issues/660)
-   `StopIteration` during `requestBody` processing if it has empty
    "content" value. [#673](https://github.com/schemathesis/schemathesis/issues/673)
-   `AttributeError` during generation of "multipart/form-data"
    parameters that have no "type" defined. [#675](https://github.com/schemathesis/schemathesis/issues/675)
-   Support for properties named "$ref" in object schemas. Previously,
    it was causing `TypeError`. [#672](https://github.com/schemathesis/schemathesis/issues/672)
-   Generating illegal Unicode surrogates in the path. [#668](https://github.com/schemathesis/schemathesis/issues/668)
-   Invalid development dependency on `graphql-server-core` package. [#658](https://github.com/schemathesis/schemathesis/issues/658)

## [2.2.1](https://github.com/schemathesis/schemathesis/compare/v2.2.0...v2.2.1) - 2020-07-22

### :bug: Fixed

-   Possible `UnicodeEncodeError` during generation of `Authorization`
    header values for endpoints with `basic` security scheme. [#656](https://github.com/schemathesis/schemathesis/issues/656)

## [2.2.0](https://github.com/schemathesis/schemathesis/compare/v2.1.0...v2.2.0) - 2020-07-14

### :rocket: Added

-   `schemathesis.graphql.from_dict` loader allows you to use GraphQL
    schemas represented as a dictionary for testing.
-   `before_load_schema` hook for GraphQL schemas.

### :bug: Fixed

-   Serialization of non-string parameters. [#651](https://github.com/schemathesis/schemathesis/issues/651)

## [2.1.0](https://github.com/schemathesis/schemathesis/compare/v2.0.0...v2.1.0) - 2020-07-06

### :rocket: Added

-   Support for property-level examples. [#467](https://github.com/schemathesis/schemathesis/issues/467)

### :bug: Fixed

-   Content-type conformance check for cases when Open API 3.0 schemas
    contain "default" response definitions. [#641](https://github.com/schemathesis/schemathesis/issues/641)
-   Handling of multipart requests for Open API 3.0 schemas. [#640](https://github.com/schemathesis/schemathesis/issues/640)
-   Sending non-file form fields in multipart requests. [#647](https://github.com/schemathesis/schemathesis/issues/647)

### :fire: Removed

-   Deprecated `skip_validation` argument to `HookDispatcher.apply`.
-   Deprecated `_accepts_context` internal function.

## [2.0.0](https://github.com/schemathesis/schemathesis/compare/v1.10.0...v2.0.0) - 2020-07-01

### :wrench: Changed

-   **BREAKING**. Base URL handling. `base_url` now is treated as one
    with a base path included. You should pass a full base URL now
    instead:

``` bash
schemathesis run --base-url=http://127.0.0.1:8080/api/v2 ...
```

This value will override `basePath` / `servers[0].url` defined in your
schema if you use Open API 2.0 / 3.0 respectively. Previously if you
pass a base URL like the one above, it was concatenated with the base
path defined in the schema, which leads to a lack of ability to redefine
the base path. [#511](https://github.com/schemathesis/schemathesis/issues/511)

### :bug: Fixed

-   Show the correct URL in CLI progress when the base URL is
    overridden, including the path part. [#511](https://github.com/schemathesis/schemathesis/issues/511)
-   Construct valid URL when overriding base URL with base path. [#511](https://github.com/schemathesis/schemathesis/issues/511)

**Example**:

``` bash
Base URL in the schema         : http://0.0.0.0:8081/api/v1
`--base-url` value in CLI      : http://0.0.0.0:8081/api/v2
Full URLs before this change   : http://0.0.0.0:8081/api/v2/api/v1/users/  # INVALID!
Full URLs after this change    : http://0.0.0.0:8081/api/v2/users/         # VALID!
```

### :fire: Removed

-   Support for hooks without `context`
    argument in the first position.
-   Hooks registration by name and function. Use `register` decorators
    instead. For more details, see the "Customization" section in our
    documentation.
-   `BaseSchema.with_hook` and `BaseSchema.register_hook`. Use
    `BaseSchema.hooks.apply` and `BaseSchema.hooks.register` instead.

## [1.10.0](https://github.com/schemathesis/schemathesis/compare/v1.9.1...v1.10.0) - 2020-06-28

### :rocket: Added

-   `loaders.from_asgi` supports making calls to ASGI-compliant
    application (For example: FastAPI). [#521](https://github.com/schemathesis/schemathesis/issues/521)
-   Support for GraphQL strategies.

### :bug: Fixed

-   Passing custom headers to schema loader for WSGI / ASGI apps. [#631](https://github.com/schemathesis/schemathesis/issues/631)

## [1.9.1](https://github.com/schemathesis/schemathesis/compare/v1.9.0...v1.9.1) - 2020-06-21

### :bug: Fixed

-   Schema validation error on schemas containing numeric values in
    scientific notation without a dot. [#629](https://github.com/schemathesis/schemathesis/issues/629)

## [1.9.0](https://github.com/schemathesis/schemathesis/compare/v1.8.0...v1.9.0) - 2020-06-20

### :rocket: Added

-   Pass the original case's response to the `add_case` hook.
-   Support for multiple examples with OpenAPI `examples`. [#589](https://github.com/schemathesis/schemathesis/issues/589)
-   `--verbosity` CLI option to minimize the error output. [#598](https://github.com/schemathesis/schemathesis/issues/598)
-   Allow registering function-level hooks without passing their name as
    the first argument to `apply`. [#618](https://github.com/schemathesis/schemathesis/issues/618)
-   Support for hook usage via `LazySchema` / `from_pytest_fixture`. [#617](https://github.com/schemathesis/schemathesis/issues/617)

### :wrench: Changed

-   Tests with invalid schemas marked as errors, instead of failures. [#622](https://github.com/schemathesis/schemathesis/issues/622)

### :bug: Fixed

-   Crash during the generation of loosely-defined headers. [#621](https://github.com/schemathesis/schemathesis/issues/621)
-   Show exception information for test runs on invalid schemas with
    `--validate-schema=false` command-line option. Before, the output
    sections for invalid endpoints were empty. [#622](https://github.com/schemathesis/schemathesis/issues/622)

## [1.8.0](https://github.com/schemathesis/schemathesis/compare/v1.7.0...v1.8.0) - 2020-06-15

### :bug: Fixed

-   Tests with invalid schemas are marked as failed instead of passed
    when `hypothesis-jsonschema>=0.16` is installed. [#614](https://github.com/schemathesis/schemathesis/issues/614)
-   `KeyError` during creating an endpoint strategy if it contains a
    reference. [#612](https://github.com/schemathesis/schemathesis/issues/612)

### :wrench: Changed

-   Require `hypothesis-jsonschema>=0.16`. [#614](https://github.com/schemathesis/schemathesis/issues/614)
-   Pass original `InvalidSchema` text to `pytest.fail` call.

## [1.7.0](https://github.com/schemathesis/schemathesis/compare/v1.6.3...v1.7.0) - 2020-05-30

### :rocket: Added

-   Support for YAML files in references via HTTPS & HTTP schemas. [#600](https://github.com/schemathesis/schemathesis/issues/600)
-   Stateful testing support via `Open API links` syntax. [#548](https://github.com/schemathesis/schemathesis/issues/548)
-   New `add_case` hook. [#458](https://github.com/schemathesis/schemathesis/issues/458)
-   Support for parameter serialization formats in Open API 2 / 3. For
    example `pipeDelimited` or `deepObject`. [#599](https://github.com/schemathesis/schemathesis/issues/599)
-   Support serializing parameters with `application/json` content-type. [#594](https://github.com/schemathesis/schemathesis/issues/594)

### :wrench: Changed

-   The minimum required versions for `Hypothesis` and
    `hypothesis-jsonschema` are `5.15.0` and `0.11.1` respectively. The
    main reason is [this
    fix](https://github.com/HypothesisWorks/hypothesis/commit/4c7f3fbc55b294f13a503b2d2af0d3221fd37938)
    that is required for stability of Open API links feature when it is
    executed in multiple threads.

## [1.6.3](https://github.com/schemathesis/schemathesis/compare/v1.6.2...v1.6.3) - 2020-05-26

### :bug: Fixed

-   Support for a colon symbol (`:`) inside of a header value passed
    via CLI. [#596](https://github.com/schemathesis/schemathesis/issues/596)

## [1.6.2](https://github.com/schemathesis/schemathesis/compare/v1.6.1...v1.6.2) - 2020-05-15

### :bug: Fixed

-   Partially generated explicit examples are always valid and can be
    used in requests. [#582](https://github.com/schemathesis/schemathesis/issues/582)

## [1.6.1](https://github.com/schemathesis/schemathesis/compare/v1.6.0...v1.6.1) - 2020-05-13

### :wrench: Changed

-   Look at the current working directory when loading hooks for CLI. [#586](https://github.com/schemathesis/schemathesis/issues/586)

## [1.6.0](https://github.com/schemathesis/schemathesis/compare/v1.5.1...v1.6.0) - 2020-05-10

### :rocket: Added

-   New `before_add_examples` hook. [#571](https://github.com/schemathesis/schemathesis/issues/571)
-   New `after_init_cli_run_handlers` hook. [#575](https://github.com/schemathesis/schemathesis/issues/575)

### :bug: Fixed

-   Passing `workers_num` to `ThreadPoolRunner` leads to always using 2
    workers in this worker kind. [#579](https://github.com/schemathesis/schemathesis/issues/579)

## [1.5.1](https://github.com/schemathesis/schemathesis/compare/v1.5.0...v1.5.1) - 2020-05-08

### :bug: Fixed

-   Display proper headers in reproduction code when headers are
    overridden. [#566](https://github.com/schemathesis/schemathesis/issues/566)

## [1.5.0](https://github.com/schemathesis/schemathesis/compare/v1.4.0...v1.5.0) - 2020-05-06

### :rocket: Added

-   Display a suggestion to disable schema validation on schema loading
    errors in CLI. [#531](https://github.com/schemathesis/schemathesis/issues/531)
-   Filtration of endpoints by `operationId` via `operation_id`
    parameter to `schema.parametrize` or `-O` command-line option. [#546](https://github.com/schemathesis/schemathesis/issues/546)
-   Generation of security-related parameters. They are taken from
    `securityDefinitions` / `securitySchemes` and injected to the
    generated data. It supports generating API keys in headers or query
    parameters and generating data for HTTP authentication schemes. [#540](https://github.com/schemathesis/schemathesis/issues/540)

### :bug: Fixed

-   Overriding header values in CLI and runner when headers provided
    explicitly clash with ones defined in the schema. [#559](https://github.com/schemathesis/schemathesis/issues/559)
-   Nested references resolving in `response_schema_conformance` check. [#562](https://github.com/schemathesis/schemathesis/issues/562)
-   Nullable parameters handling when they are behind a reference. [#542](https://github.com/schemathesis/schemathesis/issues/542)

## [1.4.0](https://github.com/schemathesis/schemathesis/compare/v1.3.4...v1.4.0) - 2020-05-03

### :rocket: Added

-   `context` argument for hook functions to provide an additional
    context for hooks. A deprecation warning is emitted for hook
    functions that do not accept this argument.
-   A new hook system that allows generic hook dispatching. It comes
    with new hook locations. For more details, see the "Customization"
    section in our documentation.
-   New `before_process_path` hook.
-   Third-party compatibility fixups mechanism. Currently, there is one
    fixup for [FastAPI](https://github.com/tiangolo/fastapi). [#503](https://github.com/schemathesis/schemathesis/issues/503)

Deprecated

-   Hook functions that do not accept `context` as their first argument.
    They will become not be supported in Schemathesis 2.0.
-   Registering hooks by name and function. Use `register` decorators
    instead. For more details, see the "Customization" section in our
    documentation.
-   `BaseSchema.with_hook` and `BaseSchema.register_hook`. Use
    `BaseSchema.hooks.apply` and `BaseSchema.hooks.register` instead.

### :bug: Fixed

-   Add missing `validate_schema` argument to
    `loaders.from_pytest_fixture`.
-   Reference resolving during response schema conformance check. [#539](https://github.com/schemathesis/schemathesis/issues/539)

## [1.3.4](https://github.com/schemathesis/schemathesis/compare/v1.3.3...v1.3.4) - 2020-04-30

### :bug: Fixed

-   Validation of nullable properties in `response_schema_conformance`
    check introduced in `1.3.0`. [#542](https://github.com/schemathesis/schemathesis/issues/542)

## [1.3.3](https://github.com/schemathesis/schemathesis/compare/v1.3.2...v1.3.3) - 2020-04-29

### :wrench: Changed

-   Update `pytest-subtests` pin to `>=0.2.1,<1.0`. [#537](https://github.com/schemathesis/schemathesis/issues/537)

## [1.3.2](https://github.com/schemathesis/schemathesis/compare/v1.3.1...v1.3.2) - 2020-04-27

### :rocket: Added

-   Show exceptions if they happened during loading a WSGI application.
    Option `--show-errors-tracebacks` will display a full traceback.

## [1.3.1](https://github.com/schemathesis/schemathesis/compare/v1.3.0...v1.3.1) - 2020-04-27

### :bug: Fixed

-   Packaging issue

## [1.3.0](https://github.com/schemathesis/schemathesis/compare/v1.2.0...v1.3.0) - 2020-04-27

### :rocket: Added

-   Storing network logs with `--store-network-log=<filename.yaml>`. The
    stored cassettes are based on the [VCR
    format](https://relishapp.com/vcr/vcr/v/5-1-0/docs/cassettes/cassette-format)
    and contain extra information from the Schemathesis internals. [#379](https://github.com/schemathesis/schemathesis/issues/379)
-   Replaying of cassettes stored in VCR format. [#519](https://github.com/schemathesis/schemathesis/issues/519)
-   Targeted property-based testing in CLI and runner. It only supports
    the `response_time` target at the moment. [#104](https://github.com/schemathesis/schemathesis/issues/104)
-   Export CLI test results to JUnit.xml with
    `--junit-xml=<filename.xml>`. [#427](https://github.com/schemathesis/schemathesis/issues/427)

### :bug: Fixed

-   Code samples for schemas where `body` is defined as
    `{"type": "string"}`. [#521](https://github.com/schemathesis/schemathesis/issues/521)
-   Showing error causes on internal `jsonschema` errors during input
    schema validation. [#513](https://github.com/schemathesis/schemathesis/issues/513)
-   Recursion error in `response_schema_conformance` check. Because of
    this change, `Endpoint.definition` contains a definition where
    references are not resolved. In this way, it makes it possible to
    avoid recursion errors in `jsonschema` validation. [#468](https://github.com/schemathesis/schemathesis/issues/468)

### :wrench: Changed

-   Added indentation & section name to the `SUMMARY` CLI block.
-   Use C-extension for YAML loading when it is possible. It can cause
    more than 10x speedup on schema parsing. Do not show Click's
    "Aborted!" message when an error occurs during CLI schema loading.
-   Add a help message to the CLI output when an internal exception
    happens. [#529](https://github.com/schemathesis/schemathesis/issues/529)

## [1.2.0](https://github.com/schemathesis/schemathesis/compare/v1.1.2...v1.2.0) - 2020-04-15

### :rocket: Added

-   Per-test hooks for modification of data generation strategies. [#492](https://github.com/schemathesis/schemathesis/issues/492)
-   Support for `x-example` vendor extension in Open API 2.0. [#504](https://github.com/schemathesis/schemathesis/issues/504)
-   Sanity validation for the input schema & loader in `runner.prepare`. [#499](https://github.com/schemathesis/schemathesis/issues/499)

## [1.1.2](https://github.com/schemathesis/schemathesis/compare/v1.1.1...v1.1.2) - 2020-04-14

### :bug: Fixed

-   Support for custom loaders in `runner`. Now all built-in loaders are
    supported as an argument to `runner.prepare`. [#496](https://github.com/schemathesis/schemathesis/issues/496)
-   `from_wsgi` loader accepts custom keyword arguments that will be
    passed to `client.get` when accessing the schema. [#497](https://github.com/schemathesis/schemathesis/issues/497)

## [1.1.1](https://github.com/schemathesis/schemathesis/compare/v1.1.0...v1.1.1) - 2020-04-12

### :bug: Fixed

-   Mistakenly applied Open API -\> JSON Schema Draft 7 conversion. It
    should be Draft 4. [#489](https://github.com/schemathesis/schemathesis/issues/489)
-   Using wrong validator in `response_schema_conformance` check. It
    should be Draft 4 validator. [#468](https://github.com/schemathesis/schemathesis/issues/468)

## [1.1.0](https://github.com/schemathesis/schemathesis/compare/v1.0.5...v1.1.0) - 2020-04-08

### :bug: Fixed

-   Response schema check for recursive schemas. [#468](https://github.com/schemathesis/schemathesis/issues/468)

### :wrench: Changed

-   App loading in `runner`. Now it accepts application as an importable
    string, rather than an instance. It is done to make it possible to
    execute a runner in a subprocess. Otherwise, apps can't be easily
    serialized and transferred into another process.
-   Runner events structure. All data in events is static from now.
    There are no references to `BaseSchema`, `Endpoint` or similar
    objects that may calculate data dynamically. This is done to make
    events serializable and not tied to Python object, which decouples
    any `runner` consumer from implementation details. It will help make
    `runner` usable in more cases (e.g., web application) since events
    can be serialized to JSON and used in any environment. Another
    related change is that Python exceptions are not propagated
    anymore - they are replaced with the `InternalError` event that
    should be handled accordingly.

## [1.0.5](https://github.com/schemathesis/schemathesis/compare/v1.0.4...v1.0.5) - 2020-04-03

### :bug: Fixed

-   Open API 3. Handling of endpoints that contain `multipart/form-data`
    media types. Previously only file upload endpoints were working
    correctly. [#473](https://github.com/schemathesis/schemathesis/issues/473)

## [1.0.4](https://github.com/schemathesis/schemathesis/compare/v1.0.3...v1.0.4) - 2020-04-03

### :bug: Fixed

-   `OpenApi30.get_content_types` behavior, introduced in
    [8aeee1a](https://github.com/schemathesis/schemathesis/commit/8aeee1ab2c6c97d94272dde4790f5efac3951aed). [#469](https://github.com/schemathesis/schemathesis/issues/469)

## [1.0.3](https://github.com/schemathesis/schemathesis/compare/v1.0.2...v1.0.3) - 2020-04-03

### :bug: Fixed

-   Precedence of `produces` keywords for Swagger 2.0 schemas. Now,
    operation-level `produces` overrides schema-level `produces` as
    specified in the specification. [#463](https://github.com/schemathesis/schemathesis/issues/463)
-   Content-type conformance check for Open API 3.0 schemas. [#461](https://github.com/schemathesis/schemathesis/issues/461)
-   Pytest 5.4 warning for test functions without parametrization. [#451](https://github.com/schemathesis/schemathesis/issues/451)

## [1.0.2](https://github.com/schemathesis/schemathesis/compare/v1.0.1...v1.0.2) - 2020-04-02

### :bug: Fixed

-   Handling of fields in `paths` that are not operations, but allowed
    by the Open API spec. [#457](https://github.com/schemathesis/schemathesis/issues/457)
-   Pytest 5.4 warning about deprecated `Node` initialization usage. [#451](https://github.com/schemathesis/schemathesis/issues/451)

## [1.0.1](https://github.com/schemathesis/schemathesis/compare/v1.0.0...v1.0.1) - 2020-04-01

### :bug: Fixed

-   Processing of explicit examples in Open API 3.0 when there are
    multiple parameters in the same location (e.g. `path`) contain
    `example` value. They are properly combined now. [#450](https://github.com/schemathesis/schemathesis/issues/450)

## [1.0.0](https://github.com/schemathesis/schemathesis/compare/v0.28.0...v1.0.0) - 2020-03-31

### :wrench: Changed

-   Move processing of `runner` parameters to `runner.prepare`. This
    change will provide better code reuse since all users of `runner`
    (e.g., if you extended it in your project) need some kind of input
    parameters handling, which was implemented only in Schemathesis CLI.
    It is not backward-compatible. If you didn't use `runner` directly,
    then this change should not have a visible effect on your use-case.

## [0.28.0](https://github.com/schemathesis/schemathesis/compare/v0.27.0...v0.28.0) - 2020-03-31

### :bug: Fixed

-   Handling of schemas that use `x-*` custom properties. [#448](https://github.com/schemathesis/schemathesis/issues/448)

### :fire: Removed

-   Deprecated `runner.execute`. Use `runner.prepare` instead.

## [0.27.0](https://github.com/schemathesis/schemathesis/compare/v0.26.1...v0.27.0) - 2020-03-31

Deprecated

-   `runner.execute` should not be used, since `runner.prepare` provides
    a more flexible interface to test execution.

### :fire: Removed

-   Deprecated `Parametrizer` class. Use `schemathesis.from_path` as a
    replacement for `Parametrizer.from_path`.

## [0.26.1](https://github.com/schemathesis/schemathesis/compare/v0.26.0...v0.26.1) - 2020-03-24

### :bug: Fixed

-   Limit recursion depth while resolving JSON schema to handle
    recursion without breaking. [#435](https://github.com/schemathesis/schemathesis/issues/435)

## [0.26.0](https://github.com/schemathesis/schemathesis/compare/v0.25.1...v0.26.0) - 2020-03-19

### :bug: Fixed

-   Filter problematic path template variables containing `"/"`, or
    `"%2F"` url encoded. [#440](https://github.com/schemathesis/schemathesis/issues/440)
-   Filter invalid empty `""` path template variables. [#439](https://github.com/schemathesis/schemathesis/issues/439)
-   Typo in a help message in the CLI output. [#436](https://github.com/schemathesis/schemathesis/issues/436)

## [0.25.1](https://github.com/schemathesis/schemathesis/compare/v0.25.0...v0.25.1) - 2020-03-09

### :wrench: Changed

-   Allow `werkzeug` \>= 1.0.0. [#433](https://github.com/schemathesis/schemathesis/issues/433)

## [0.25.0](https://github.com/schemathesis/schemathesis/compare/v0.24.5...v0.25.0) - 2020-02-27

### :wrench: Changed

-   Handling of explicit examples from schemas. Now, if there are
    examples for multiple locations (e.g., for body and query) then they
    will be combined into a single example. [#424](https://github.com/schemathesis/schemathesis/issues/424)

## [0.24.5](https://github.com/schemathesis/schemathesis/compare/v0.24.4...v0.24.5) - 2020-02-26

### :bug: Fixed

-   Error during `pytest` collection on objects with custom
    `__getattr__` method and therefore pass `is_schemathesis` check. [#429](https://github.com/schemathesis/schemathesis/issues/429)

## [0.24.4](https://github.com/schemathesis/schemathesis/compare/v0.24.3...v0.24.4) - 2020-02-22

### :bug: Fixed

-   Resolving references when the schema is loaded from a file on
    Windows. [#418](https://github.com/schemathesis/schemathesis/issues/418)

## [0.24.3](https://github.com/schemathesis/schemathesis/compare/v0.24.2...v0.24.3) - 2020-02-10

### :bug: Fixed

-   Not copied `validate_schema` parameter in `BaseSchema.parametrize`.
    Regression after implementing [#383](https://github.com/schemathesis/schemathesis/issues/383)
-   Missing `app`, `location` and `hooks` parameters in schema when used
    with `BaseSchema.parametrize`. [#416](https://github.com/schemathesis/schemathesis/issues/416)

## [0.24.2](https://github.com/schemathesis/schemathesis/compare/v0.24.1...v0.24.2) - 2020-02-09

### :bug: Fixed

-   Crash on invalid regular expressions in `method`, `endpoint` and
    `tag` CLI options. [#403](https://github.com/schemathesis/schemathesis/issues/403)
-   Crash on a non-latin-1 encodable value in the `auth` CLI option. [#404](https://github.com/schemathesis/schemathesis/issues/404)
-   Crash on an invalid value in the `header` CLI option. [#405](https://github.com/schemathesis/schemathesis/issues/405)
-   Crash on some invalid URLs in the `schema` CLI option. [#406](https://github.com/schemathesis/schemathesis/issues/406)
-   Validation of `--request-timeout` parameter. [#407](https://github.com/schemathesis/schemathesis/issues/407)
-   Crash with `--hypothesis-deadline=0` CLI option. [#410](https://github.com/schemathesis/schemathesis/issues/410)
-   Crash with `--hypothesis-max-examples=0` CLI option. [#412](https://github.com/schemathesis/schemathesis/issues/412)

## [0.24.1](https://github.com/schemathesis/schemathesis/compare/v0.24.0...v0.24.1) - 2020-02-08

### :bug: Fixed

-   CLI crash on Windows and Python \< 3.8 when the schema path contains
    characters unrepresentable at the OS level. [#400](https://github.com/schemathesis/schemathesis/issues/400)

## [0.24.0](https://github.com/schemathesis/schemathesis/compare/v0.23.7...v0.24.0) - 2020-02-07

### :rocket: Added

-   Support for testing of examples in Parameter & Media Type objects in
    Open API 3.0. [#394](https://github.com/schemathesis/schemathesis/issues/394)
-   `--show-error-tracebacks` CLI option to display errors' tracebacks
    in the output. [#391](https://github.com/schemathesis/schemathesis/issues/391)
-   Support for schema behind auth. [#115](https://github.com/schemathesis/schemathesis/issues/115)

### :wrench: Changed

-   Schemas with GET endpoints accepting body are allowed now if schema
    validation is disabled (via `--validate-schema=false` for example).
    The use-case is for tools like ElasticSearch that use GET requests
    with non-empty bodies. [#383](https://github.com/schemathesis/schemathesis/issues/383)

### :bug: Fixed

-   CLI crash when an explicit example is specified in the endpoint
    definition. [#386](https://github.com/schemathesis/schemathesis/issues/386)

## [0.23.7](https://github.com/schemathesis/schemathesis/compare/v0.23.6...v0.23.7) - 2020-01-30

### :rocket: Added

-   `-x`/`--exitfirst` CLI option to exit after the first failed test. [#378](https://github.com/schemathesis/schemathesis/issues/378)

### :bug: Fixed

-   Handling examples of parameters in Open API 3.0. [#381](https://github.com/schemathesis/schemathesis/issues/381)

## [0.23.6](https://github.com/schemathesis/schemathesis/compare/v0.23.5...v0.23.6) - 2020-01-28

### :rocket: Added

-   `all` variant for `--checks` CLI option to use all available checks. [#374](https://github.com/schemathesis/schemathesis/issues/374)

### :wrench: Changed

-   Use built-in `importlib.metadata` on Python 3.8. [#376](https://github.com/schemathesis/schemathesis/issues/376)

## [0.23.5](https://github.com/schemathesis/schemathesis/compare/v0.23.4...v0.23.5) - 2020-01-24

### :bug: Fixed

-   Generation of invalid values in `Case.cookies`. [#371](https://github.com/schemathesis/schemathesis/issues/371)

## [0.23.4](https://github.com/schemathesis/schemathesis/compare/v0.23.3...v0.23.4) - 2020-01-22

### :bug: Fixed

-   Converting `exclusiveMinimum` & `exclusiveMaximum` fields to JSON
    Schema. [#367](https://github.com/schemathesis/schemathesis/issues/367)

## [0.23.3](https://github.com/schemathesis/schemathesis/compare/v0.23.2...v0.23.3) - 2020-01-21

### :bug: Fixed

-   Filter out surrogate pairs from the query string.

## [0.23.2](https://github.com/schemathesis/schemathesis/compare/v0.23.1...v0.23.2) - 2020-01-16

### :bug: Fixed

-   Prevent `KeyError` when the response does not have the
    "Content-Type" header. [#365](https://github.com/schemathesis/schemathesis/issues/365)

## [0.23.1](https://github.com/schemathesis/schemathesis/compare/v0.23.0...v0.23.1) - 2020-01-15

### :bug: Fixed

-   Dockerfile entrypoint was not working as per docs. [#361](https://github.com/schemathesis/schemathesis/issues/361)

## [0.23.0](https://github.com/schemathesis/schemathesis/compare/v0.22.0...v0.23.0) - 2020-01-15

### :rocket: Added

-   Hooks for strategy modification. [#313](https://github.com/schemathesis/schemathesis/issues/313)
-   Input schema validation. Use `--validate-schema=false` to disable it
    in CLI and `validate_schema=False` argument in loaders. [#110](https://github.com/schemathesis/schemathesis/issues/110)

## [0.22.0](https://github.com/schemathesis/schemathesis/compare/v0.21.0...v0.22.0) - 2020-01-11

### :rocket: Added

-   Show multiple found failures in the CLI output. [#266](https://github.com/schemathesis/schemathesis/issues/266) & [#207](https://github.com/schemathesis/schemathesis/issues/207)
-   Raise a proper exception when the given schema is invalid. [#308](https://github.com/schemathesis/schemathesis/issues/308)
-   Support for `None` as a value for `--hypothesis-deadline`. [#349](https://github.com/schemathesis/schemathesis/issues/349)

### :bug: Fixed

-   Handling binary request payloads in `Case.call`. [#350](https://github.com/schemathesis/schemathesis/issues/350)
-   Type of the second argument to all built-in checks set to proper
    `Case` instead of `TestResult`. The error was didn't affect built-in
    checks since both `Case` and `TestResult` had `endpoint` attribute,
    and only it was used. However, this fix is not backward-compatible
    with 3rd party checks.

## [0.21.0](https://github.com/schemathesis/schemathesis/compare/v0.20.5...v0.21.0) - 2019-12-20

### :rocket: Added

-   Support for AioHTTP applications in CLI. [#329](https://github.com/schemathesis/schemathesis/issues/329)

## [0.20.5](https://github.com/schemathesis/schemathesis/compare/v0.20.4...v0.20.5) - 2019-12-18

### :bug: Fixed

-   Compatibility with the latest release of `hypothesis-jsonschema` and
    setting its minimal required version to `0.9.13`. [#338](https://github.com/schemathesis/schemathesis/issues/338)

## [0.20.4](https://github.com/schemathesis/schemathesis/compare/v0.20.3...v0.20.4) - 2019-12-17

### :bug: Fixed

-   Handling `nullable` attribute in Open API schemas. [#335](https://github.com/schemathesis/schemathesis/issues/335)

## [0.20.3](https://github.com/schemathesis/schemathesis/compare/v0.20.2...v0.20.3) - 2019-12-17

### :bug: Fixed

-   Usage of the response status code conformance check with old
    `requests` version. [#330](https://github.com/schemathesis/schemathesis/issues/330)

## [0.20.2](https://github.com/schemathesis/schemathesis/compare/v0.20.1...v0.20.2) - 2019-12-14

### :bug: Fixed

-   Response schema conformance check for Open API 3.0. [#332](https://github.com/schemathesis/schemathesis/issues/332)

## [0.20.1](https://github.com/schemathesis/schemathesis/compare/v0.20.0...v0.20.1) - 2019-12-13

### :rocket: Added

-   Support for response code ranges. [#330](https://github.com/schemathesis/schemathesis/issues/330)

## [0.20.0](https://github.com/schemathesis/schemathesis/compare/v0.19.1...v0.20.0) - 2019-12-12

### :rocket: Added

-   WSGI apps support. [#31](https://github.com/schemathesis/schemathesis/issues/31)
-   `Case.validate_response` for running built-in checks against app's
    response. [#319](https://github.com/schemathesis/schemathesis/issues/319)

### :wrench: Changed

-   Checks receive `Case` instance as a second argument instead of
    `TestResult`. This was done for making checks usable in Python tests
    via `Case.validate_response`. Endpoint and schema are accessible via
    `case.endpoint` and `case.endpoint.schema`.

## [0.19.1](https://github.com/schemathesis/schemathesis/compare/v0.19.0...v0.19.1) - 2019-12-11

### :bug: Fixed

-   Compatibility with Hypothesis \>= 4.53.2. [#322](https://github.com/schemathesis/schemathesis/issues/322)

## [0.19.0](https://github.com/schemathesis/schemathesis/compare/v0.18.1...v0.19.0) - 2019-12-02

### :rocket: Added

-   Concurrent test execution in CLI / runner. [#91](https://github.com/schemathesis/schemathesis/issues/91)
-   update importlib_metadata pin to `^1.1`. [#315](https://github.com/schemathesis/schemathesis/issues/315)

## [0.18.1](https://github.com/schemathesis/schemathesis/compare/v0.18.0...v0.18.1) - 2019-11-28

### :bug: Fixed

-   Validation of the `base-url` CLI parameter. [#311](https://github.com/schemathesis/schemathesis/issues/311)

## [0.18.0](https://github.com/schemathesis/schemathesis/compare/v0.17.0...v0.18.0) - 2019-11-27

### :rocket: Added

-   Resolving references in `PathItem` objects. [#301](https://github.com/schemathesis/schemathesis/issues/301)

### :bug: Fixed

-   Resolving of relative paths in schemas. [#303](https://github.com/schemathesis/schemathesis/issues/303)
-   Loading string dates as `datetime.date` objects in YAML loader. [#305](https://github.com/schemathesis/schemathesis/issues/305)

## [0.17.0](https://github.com/schemathesis/schemathesis/compare/v0.16.0...v0.17.0) - 2019-11-21

### :rocket: Added

-   Resolving references that point to different files. [#294](https://github.com/schemathesis/schemathesis/issues/294)

### :wrench: Changed

-   Keyboard interrupt is now handled during the CLI run, and the
    summary is displayed in the output. [#295](https://github.com/schemathesis/schemathesis/issues/295)

## [0.16.0](https://github.com/schemathesis/schemathesis/compare/v0.15.0...v0.16.0) - 2019-11-19

### :rocket: Added

-   Display RNG seed in the CLI output to allow test reproducing. [#267](https://github.com/schemathesis/schemathesis/issues/267)
-   Allow specifying seed in CLI.
-   Ability to pass custom kwargs to the `requests.get` call in
    `loaders.from_uri`.

### :wrench: Changed

-   Refactor case generation strategies: strategy is not used to
    generate empty value. [#253](https://github.com/schemathesis/schemathesis/issues/253)
-   Improved error message for invalid path parameter declaration. [#255](https://github.com/schemathesis/schemathesis/issues/255)

### :bug: Fixed

-   Pytest fixture parametrization via `pytest_generate_tests`. [#280](https://github.com/schemathesis/schemathesis/issues/280)
-   Support for tests defined as methods. [#282](https://github.com/schemathesis/schemathesis/issues/282)
-   Unclosed `requests.Session` on calling `Case.call` without passing a
    session explicitly. [#286](https://github.com/schemathesis/schemathesis/issues/286)

## [0.15.0](https://github.com/schemathesis/schemathesis/compare/v0.14.0...v0.15.0) - 2019-11-15

### :rocket: Added

-   Support for OpenAPI 3.0 server variables (base_path). [#40](https://github.com/schemathesis/schemathesis/issues/40)
-   Support for `format: byte`. [#254](https://github.com/schemathesis/schemathesis/issues/254)
-   Response schema conformance check in CLI / Runner. [#256](https://github.com/schemathesis/schemathesis/issues/256)
-   Docker image for CLI. [#268](https://github.com/schemathesis/schemathesis/issues/268)
-   Pre-run hooks for CLI. [#147](https://github.com/schemathesis/schemathesis/issues/147)
-   A way to register custom checks for CLI via
    `schemathesis.register_check`. [#270](https://github.com/schemathesis/schemathesis/issues/270)

### :bug: Fixed

-   Not encoded path parameters. [#272](https://github.com/schemathesis/schemathesis/issues/272)

### :wrench: Changed

-   Verbose messages are displayed in the CLI on failed checks. [#261](https://github.com/schemathesis/schemathesis/issues/261)

## [0.14.0](https://github.com/schemathesis/schemathesis/compare/v0.13.2...v0.14.0) - 2019-11-09

### :rocket: Added

-   CLI: Support file paths in the `schema` argument. [#119](https://github.com/schemathesis/schemathesis/issues/119)
-   Checks to verify response status & content type in CLI / Runner. [#101](https://github.com/schemathesis/schemathesis/issues/101)

### :bug: Fixed

-   Custom base URL handling in CLI / Runner. [#248](https://github.com/schemathesis/schemathesis/issues/248)

### :wrench: Changed

-   Raise an error if the schema has a body for GET requests. [#218](https://github.com/schemathesis/schemathesis/issues/218)
-   Method names are case insensitive during direct schema access. [#246](https://github.com/schemathesis/schemathesis/issues/246)

## [0.13.2](https://github.com/schemathesis/schemathesis/compare/v0.13.1...v0.13.2) - 2019-11-05

### :bug: Fixed

-   `IndexError` when Hypothesis found inconsistent test results during
    the test execution in the runner. [#236](https://github.com/schemathesis/schemathesis/issues/236)

## [0.13.1](https://github.com/schemathesis/schemathesis/compare/v0.13.0...v0.13.1) - 2019-11-05

### :rocket: Added

-   Support for binary format [#197](https://github.com/schemathesis/schemathesis/issues/197)

### :bug: Fixed

-   Error that happens when there are no success checks in the statistic
    in CLI. [#237](https://github.com/schemathesis/schemathesis/issues/237)

## [0.13.0](https://github.com/schemathesis/schemathesis/compare/v0.12.2...v0.13.0) - 2019-11-03

### :rocket: Added

-   An option to configure request timeout for CLI / Runner. [#204](https://github.com/schemathesis/schemathesis/issues/204)
-   A help snippet to reproduce errors caught by Schemathesis. [#206](https://github.com/schemathesis/schemathesis/issues/206)
-   Total running time to the CLI output. [#181](https://github.com/schemathesis/schemathesis/issues/181)
-   Summary line in the CLI output with the number of passed / failed /
    errored endpoint tests. [#209](https://github.com/schemathesis/schemathesis/issues/209)
-   Extra information to the CLI output: schema address, spec version,
    and base URL. [#188](https://github.com/schemathesis/schemathesis/issues/188)

### :bug: Fixed

-   Compatibility with Hypothesis 4.42.4+ . [#212](https://github.com/schemathesis/schemathesis/issues/212)
-   Display flaky errors only in the "ERRORS" section and improve CLI
    output. [#215](https://github.com/schemathesis/schemathesis/issues/215)
-   Handling `formData` parameters in `Case.call`. [#196](https://github.com/schemathesis/schemathesis/issues/196)
-   Handling cookies in `Case.call`. [#211](https://github.com/schemathesis/schemathesis/issues/211)

### :wrench: Changed

-   More readable falsifying examples output. [#127](https://github.com/schemathesis/schemathesis/issues/127)
-   Show exceptions in a separate section of the CLI output. [#203](https://github.com/schemathesis/schemathesis/issues/203)
-   Error message for cases when it is not possible to satisfy schema
    parameters. It should be more clear now. [#216](https://github.com/schemathesis/schemathesis/issues/216)
-   Do not stop on schema errors related to a single endpoint. [#139](https://github.com/schemathesis/schemathesis/issues/139)
-   Display a proper error message when the schema is not available in
    CLI / Runner. [#214](https://github.com/schemathesis/schemathesis/issues/214)

## [0.12.2](https://github.com/schemathesis/schemathesis/compare/v0.12.1...v0.12.2) - 2019-10-30

### :bug: Fixed

-   Wrong handling of the `base_url` parameter in runner and `Case.call`
    if it has a trailing slash. [#194](https://github.com/schemathesis/schemathesis/issues/194) and [#199](https://github.com/schemathesis/schemathesis/issues/199)
-   Do not send any payload with GET requests. [#200](https://github.com/schemathesis/schemathesis/issues/200)

## [0.12.1](https://github.com/schemathesis/schemathesis/compare/v0.12.0...v0.12.1) - 2019-10-28

### :bug: Fixed

-   Handling for errors other than `AssertionError` and
    `HypothesisException` in the runner. [#189](https://github.com/schemathesis/schemathesis/issues/189)
-   CLI failing on the case when there are tests, but no checks were
    performed. [#191](https://github.com/schemathesis/schemathesis/issues/191)

### :wrench: Changed

-   Display the "SUMMARY" section in the CLI output for empty test
    suites.

## [0.12.0](https://github.com/schemathesis/schemathesis/compare/v0.11.0...v0.12.0) - 2019-10-28

### :rocket: Added

-   Display progress during the CLI run. [#125](https://github.com/schemathesis/schemathesis/issues/125)

### :bug: Fixed

-   Test server-generated wrong schema when the `endpoints` option is
    passed via CLI. [#173](https://github.com/schemathesis/schemathesis/issues/173)
-   Error message if the schema is not found in CLI. [#172](https://github.com/schemathesis/schemathesis/issues/172)

### :wrench: Changed

-   Continue running tests on hypothesis error. [#137](https://github.com/schemathesis/schemathesis/issues/137)

## [0.11.0](https://github.com/schemathesis/schemathesis/compare/v0.10.0...v0.11.0) - 2019-10-22

### :rocket: Added

-   LazySchema accepts filters. [#149](https://github.com/schemathesis/schemathesis/issues/149)
-   Ability to register strategies for custom string formats. [#94](https://github.com/schemathesis/schemathesis/issues/94)
-   Generator-based events in the `runner` module to improve control
    over the execution flow.
-   Filtration by tags. [#134](https://github.com/schemathesis/schemathesis/issues/134)

### :wrench: Changed

-   Base URL in schema instances could be reused when it is defined
    during creation. Now on, the `base_url` argument in `Case.call` is
    optional in such cases. [#153](https://github.com/schemathesis/schemathesis/issues/153)
-   Hypothesis deadline is set to 500ms by default. [#138](https://github.com/schemathesis/schemathesis/issues/138)
-   Hypothesis output is captured separately, without capturing the
    whole stdout during CLI run.
-   Disallow empty username in CLI `--auth` option.

### :bug: Fixed

-   User-agent during schema loading. [#144](https://github.com/schemathesis/schemathesis/issues/144)
-   Generation of invalid values in `Case.headers`. [#167](https://github.com/schemathesis/schemathesis/issues/167)

### :fire: Removed

-   Undocumented support for `file://` URI schema

## [0.10.0](https://github.com/schemathesis/schemathesis/compare/v0.9.0...v0.10.0) - 2019-10-14

### :rocket: Added

-   HTTP Digest Auth support. [#106](https://github.com/schemathesis/schemathesis/issues/106)
-   Support for Hypothesis settings in CLI & Runner. [#107](https://github.com/schemathesis/schemathesis/issues/107)
-   `Case.call` and `Case.as_requests_kwargs` convenience methods. [#109](https://github.com/schemathesis/schemathesis/issues/109)
-   Local development server. [#126](https://github.com/schemathesis/schemathesis/issues/126)

### :fire: Removed

-   Autogenerated `runner.StatsCollector.__repr__` to make Hypothesis
    output more readable.

## [0.9.0](https://github.com/schemathesis/schemathesis/compare/v0.8.1...v0.9.0) - 2019-10-09

### :rocket: Added

-   Test executor collects results of execution. [#29](https://github.com/schemathesis/schemathesis/issues/29)
-   CLI option `--base-url` for specifying base URL of API. [#118](https://github.com/schemathesis/schemathesis/issues/118)
-   Support for coroutine-based tests. [#121](https://github.com/schemathesis/schemathesis/issues/121)
-   User Agent to network requests in CLI & runner. [#130](https://github.com/schemathesis/schemathesis/issues/130)

### :wrench: Changed

-   CLI command `schemathesis run` prints result in a more readable way
    with a summary of passing checks.
-   Empty header names are forbidden for CLI.
-   Suppressed hypothesis exception about using `example`
    non-interactively. [#92](https://github.com/schemathesis/schemathesis/issues/92)

## [0.8.1](https://github.com/schemathesis/schemathesis/compare/v0.8.0...v0.8.1) - 2019-10-04

### :bug: Fixed

-   Wrap each test in `suppress` so the runner doesn't stop after the
    first test failure.

## [0.8.0](https://github.com/schemathesis/schemathesis/compare/v0.7.3...v0.8.0) - 2019-10-04

### :rocket: Added

-   CLI tool invoked by the `schemathesis` command. [#30](https://github.com/schemathesis/schemathesis/issues/30)
-   New arguments `api_options`, `loader_options` and `loader` for test
    executor. [#90](https://github.com/schemathesis/schemathesis/issues/90)
-   A mapping interface for schemas & convenience methods for direct
    strategy access. [#98](https://github.com/schemathesis/schemathesis/issues/98)

### :bug: Fixed

-   Runner stopping on the first falsifying example. [#99](https://github.com/schemathesis/schemathesis/issues/99)

## [0.7.3](https://github.com/schemathesis/schemathesis/compare/v0.7.2...v0.7.3) - 2019-09-30

### :bug: Fixed

-   Filtration in lazy loaders.

## [0.7.2](https://github.com/schemathesis/schemathesis/compare/v0.7.1...v0.7.2) - 2019-09-30

### :rocket: Added

-   Support for type "file" for Swagger 2.0. [#78](https://github.com/schemathesis/schemathesis/issues/78)
-   Support for filtering in loaders. [#75](https://github.com/schemathesis/schemathesis/issues/75)

### :bug: Fixed

-   Conflict for lazy schema filtering. [#64](https://github.com/schemathesis/schemathesis/issues/64)

## [0.7.1](https://github.com/schemathesis/schemathesis/compare/v0.7.0...v0.7.1) - 2019-09-27

### :rocket: Added

-   Support for `x-nullable` extension. [#45](https://github.com/schemathesis/schemathesis/issues/45)

## [0.7.0](https://github.com/schemathesis/schemathesis/compare/v0.6.0...v0.7.0) - 2019-09-26

### :rocket: Added

-   Support for the `cookie` parameter in OpenAPI 3.0 schemas. [#21](https://github.com/schemathesis/schemathesis/issues/21)
-   Support for the `formData` parameter in Swagger 2.0 schemas. [#6](https://github.com/schemathesis/schemathesis/issues/6)
-   Test executor. [#28](https://github.com/schemathesis/schemathesis/issues/28)

### :bug: Fixed

-   Using `hypothesis.settings` decorator with test functions created
    from `from_pytest_fixture` loader. [#69](https://github.com/schemathesis/schemathesis/issues/69)

## [0.6.0](https://github.com/schemathesis/schemathesis/compare/v0.5.0...v0.6.0) - 2019-09-24

### :rocket: Added

-   Parametrizing tests from a pytest fixture via `pytest-subtests`.
    [#58](https://github.com/schemathesis/schemathesis/issues/58)

### :wrench: Changed

-   Rename module `readers` to `loaders`.
-   Rename `parametrize` parameters. `filter_endpoint` to `endpoint` and
    `filter_method` to `method`.

### :fire: Removed

-   Substring match for method/endpoint filters. To avoid clashing with
    escaped chars in endpoints keys in schemas.

## [0.5.0](https://github.com/schemathesis/schemathesis/compare/v0.4.1...v0.5.0) - 2019-09-16

### :rocket: Added

-   Generating explicit examples from the schema. [#17](https://github.com/schemathesis/schemathesis/issues/17)

### :wrench: Changed

-   Schemas are loaded eagerly from now on. Using
    `schemathesis.from_uri` implies network calls.

Deprecated

-   Using `Parametrizer.from_{path,uri}` is deprecated, use
    `schemathesis.from_{path,uri}` instead.

### :bug: Fixed

-   Body resolving during test collection. [#55](https://github.com/schemathesis/schemathesis/issues/55)

## [0.4.1](https://github.com/schemathesis/schemathesis/compare/v0.4.0...v0.4.1) - 2019-09-11

### :bug: Fixed

-   Possibly unhandled exception during `hasattr` check in
    `is_schemathesis_test`.

## [0.4.0](https://github.com/schemathesis/schemathesis/compare/v0.3.0...v0.4.0) - 2019-09-10

### :bug: Fixed

-   Resolving all inner references in objects. [#34](https://github.com/schemathesis/schemathesis/issues/34)

### :wrench: Changed

-   `jsonschema.RefResolver` is now used for reference resolving. [#35](https://github.com/schemathesis/schemathesis/issues/35)

## [0.3.0](https://github.com/schemathesis/schemathesis/compare/v0.2.0...v0.3.0) - 2019-09-06

### :rocket: Added

-   `Parametrizer.from_uri` method to construct parametrizer instances
    from URIs. [#24](https://github.com/schemathesis/schemathesis/issues/24)

### :fire: Removed

-   Possibility to use `Parametrizer.parametrize` and custom
    `Parametrizer` kwargs for passing config options to
    `hypothesis.settings`. Use `hypothesis.settings` decorators on tests
    instead.

## [0.2.0](https://github.com/schemathesis/schemathesis/compare/v0.1.0...v0.2.0) - 2019-09-05

### :rocket: Added

-   Open API 3.0 support. [#10](https://github.com/schemathesis/schemathesis/issues/10)
-   "header" parameters. [#7](https://github.com/schemathesis/schemathesis/issues/7)

### :wrench: Changed

-   Handle errors during collection / executions as failures.
-   Use `re.search` for pattern matching in
    `filter_method`/`filter_endpoint` instead of `fnmatch`. [#18](https://github.com/schemathesis/schemathesis/issues/18)
-   `Case.body` contains properties from the target schema, without the
    extra level of nesting.

### :bug: Fixed

-   `KeyError` on collection when "basePath" is absent. [#16](https://github.com/schemathesis/schemathesis/issues/16)

## 0.1.0 - 2019-06-28

-   Initial public release
