Changelog
=========

:version:`Unreleased <v3.35.3...HEAD>` - TBD
--------------------------------------------

.. _v3.35.3:

:version:`3.35.3 <v3.35.2...v3.35.3>` - 2024-09-05
--------------------------------------------------

**Changed**

- Use more explicit examples in the coverage phase.
- Make CLI options help more readable.

**Fixed**

- Ignored ``generation_config`` in explicit example tests when it is explicitly passed to the test runner.
- Incomplete header values in some serialization cases.

.. _v3.35.2:

:version:`3.35.2 <v3.35.1...v3.35.2>` - 2024-09-01
--------------------------------------------------

**Changed**

- Restructure the ``st run --help`` output.
- Use explicit examples in the coverage phase.

**Fixed**

- Ensure that the ``-D`` CLI option is respected in the coverage phase.
- Prevent stateful tests failing with ``Unsatisfiable`` if it they previously had successfully generated test cases.

.. _v3.35.1:

:version:`3.35.1 <v3.35.0...v3.35.1>` - 2024-08-27
--------------------------------------------------

**Added**

- New ``phase`` field to VCR cassettes to indicate the testing phase of each recorded test case.

**Fixed**

- Internal errors in the experimental "coverage" phase.
- Missing ``Case.data_generation_method`` in test cases generated during the coverage phase.
- Incorrect header values generated during the coverage phase.

.. _v3.35.0:

:version:`3.35.0 <v3.34.3...v3.35.0>` - 2024-08-25
--------------------------------------------------

**Added**

- **EXPERIMENTAL**: New "coverage" phase in the test runner. It aims to explicitly cover common test scenarios like missing required properties, incorrect types, etc. Enable it with ``--experimental=coverage-phase``
- Extending CLI with custom options and CLI handlers via hooks.

.. _v3.34.3:

:version:`3.34.3 <v3.34.2...v3.34.3>` - 2024-08-24
--------------------------------------------------

**Changed**

- Adjust the distribution of negative test cases in stateful tests so they are less likely to occur for starting transitions.

.. _v3.34.2:

:version:`3.34.2 <v3.34.1...v3.34.2>` - 2024-08-20
--------------------------------------------------

**Fixed**

- Not using the proper session in the ``ignored_auth`` check. :issue:`2409`
- WSGI support for ``ignored_auth``.

.. _v3.34.1:

:version:`3.34.1 <v3.34.0...v3.34.1>` - 2024-08-20
--------------------------------------------------

**Fixed**

- Error in ``response_header_conformance`` if the header definition is behind ``$ref``. :issue:`2407`

.. _v3.34.0:

:version:`3.34.0 <v3.33.3...v3.34.0>` - 2024-08-17
--------------------------------------------------

**Added**

- The ``ensure_resource_availability`` check. It verifies that a freshly created resource is available in related API operations.
- The ``ignored_auth`` check. It verifies that the API operation requires the specified authentication.
- Enable string format verification in response conformance checks. :issue:`787`
- Control over cache key in custom auth implementation. :issue:`1775`
- The ``--generation-graphql-allow-null`` CLI option that controls whether ``null`` should be used for optional arguments in GraphQL queries. Enabled by default. :issue:`1994`
- Filters for hooks. :issue:`1852`
- Verify header schema conformance. :issue:`796`

**Changed**

- Pass default stateful test runner config to ``TestCase`` used by ``pytest`` & ``unittest`` integration.
- Rework transitions in stateful tests in order to reduce the number of unhelpful API calls.
- Improve error message when ``base_url`` is missing for a schema loaded from a file.

**Fixed**

- Missing sanitization in new-style stateful tests.
- Missing new-style stateful testing results in JUnit output.
- Internal error when handling an exception inside a hook for a GraphQL schema.
- Filters being ignored in the old-style stateful test runner. :issue:`2376`
- Missing sanitization for query parameters in code samples.

.. _v3.33.3:

:version:`3.33.3 <v3.33.2...v3.33.3>` - 2024-07-29
--------------------------------------------------

**Fixed**

- Incorrect default deadline for stateful tests in CLI.
- Incorrect handling of ``allOf`` subschemas in testing explicit examples. :issue:`2375`

**Changed**

- Reduce the default stateful step count from 50 to 10. It increases the variety of the generated API call sequences.

.. _v3.33.2:

:version:`3.33.2 <v3.33.1...v3.33.2>` - 2024-07-27
--------------------------------------------------

**Fixed**

- Internal error in stateful testing.
- Internal error in CLI output when some of test cases has no responses due to timeout. :issue:`2373`

.. _v3.33.1:

:version:`3.33.1 <v3.33.0...v3.33.1>` - 2024-07-22
--------------------------------------------------

**Fixed**

- Ignoring nested examples. :issue:`2358`

.. _v3.33.0:

:version:`3.33.0 <v3.32.2...v3.33.0>` - 2024-07-19
--------------------------------------------------

**Added**

- A set of CLI options and a Python API for including and excluding what API operations to test. :issue:`703`, :issue:`819`, :issue:`1398`
- A way to filter API operations by an expression in CLI. :issue:`1006`
- Support for filtering GraphQL operations by ``name``.

**Fixed**

- Missed ``operation_id`` & ``tag`` filter in some cases.
- Broken compatibility with ``Hypothesis<6.108``. :issue:`2357`

**Deprecated**

- ``--method``, ``--endpoint``, ``--tag``, ``--operation-id``, ``--skip-deprecated-operations`` CLI options in favor of the new ``--include-*`` and ``--exclude-*`` options. 
  See more details in the CLI documentation.
- ``method``, ``endpoint``, ``tag``, ``operation_id`` and ``skip_deprecated_operations`` arguments in ``schemathesis.from_*`` loaders and the ``parametrize`` function in favor of the new ``include`` and ``exclude`` methods on ``schema`` instances.

.. _v3.32.2:

:version:`3.32.2 <v3.32.1...v3.32.2>` - 2024-07-17
--------------------------------------------------

**Fixed**

- Circular import in ``schemathesis.runner.events``.

.. _v3.32.1:

:version:`3.32.1 <v3.32.0...v3.32.1>` - 2024-07-17
--------------------------------------------------

**Added**

- Filtering by ``operation_id`` in conditional auth implementation.

**Fixed**

- Internal error when saving debug logs with ``--experimental=stateful-test-runner`` or ``--experimental=schema-analysis`` enabled. :issue:`2353`

.. _v3.32.0:

:version:`3.32.0 <v3.31.1...v3.32.0>` - 2024-07-14
--------------------------------------------------

**Added**

- Support for authentication via CLI arguments in new-style stateful tests.
- Support for ``--hypothesis-seed`` in new-style stateful tests.
- Support for ``--set-*`` CLI options in new-style stateful tests.
- Support for ``--max-response-time`` in new-style stateful tests.
- Support for targeted property-based testing in new-style stateful tests.
- Support for ``--request-*`` CLI options in new-style stateful tests.
- Support for ``--generation-*`` CLI options in new-style stateful tests.
- Support for ``--max-failures`` in new-style stateful tests.
- Support for ``--dry-run`` in new-style stateful tests.
- ``all`` variant for the ``--hypothesis-suppress-health-check`` CLI option.
- Support for Hypothesis >= ``6.108.0``.

**Fixed**

- WSGI support for new-style stateful tests.
- Ignoring configured data generation methods in new-style stateful tests.
- Using constant ``data_generation_method`` value for HTTP interactions in VCR cassettes.
- Not reporting errors with ``--experimental=stateful-only``. :issue:`2326`
- Internal error on CTRL-C during new-style stateful tests.
- Use ``--request-proxy`` for API probing.
- Fill the ``seed`` field in cassettes for new-style stateful tests.
- Ignoring remote scope when getting API operation by reference.

**Changed**

- Do not run new-style stateful tests if unit tests exited due to ``--exitfirst``.
- Display error details if API probing fails.

.. _v3.31.1:

:version:`3.31.1 <v3.31.0...v3.31.1>` - 2024-07-03
--------------------------------------------------

**Fixed**

- Generating negative test cases for path and query parameters. :issue:`2312`

**Changed**

- Do not consider ignoring additional parameters as a failure in ``negative_data_rejection``.

.. _v3.31.0:

:version:`3.31.0 <v3.30.4...v3.31.0>` - 2024-06-30
--------------------------------------------------

**Added**

- Storing cassettes in the HAR format via the ``--cassette-format=har`` CLI option. :issue:`2299`
- Support for cassettes in the new-style stateful test runner.
- ``--generation-with-security-parameters=false`` CLI option to disable generation of security parameters (like tokens) in test cases.

**Fixed**

- Incorrect test case ids stored in VCR cassettes. :issue:`2302`
- Incorrect reference resolution scope for security schemes if the API operation has a different scope than the global security schemes. :issue:`2300`
- Properly display unresolvable reference if it comes from a missing file.

.. _v3.30.4:

:version:`3.30.4 <v3.30.3...v3.30.4>` - 2024-06-28
--------------------------------------------------

**Fixed**

- Missing overrides from ``--set-*`` CLI options in tests for explicit examples.

.. _v3.30.3:

:version:`3.30.3 <v3.30.2...v3.30.3>` - 2024-06-27
--------------------------------------------------

**Fixed**

- Internal error when piping stdout to a file in CLI on Windows.

.. _v3.30.2:

:version:`3.30.2 <v3.30.1...v3.30.2>` - 2024-06-27
--------------------------------------------------

**Fixed**

- Excessive ``urllib3`` warnings during testing ``localhost`` via ``https``.
- Misreporting of undocumented ``Content-Type`` when documented content types contain wildcards.
- Incorrect test case reporting when code samples contain a single sanitized parameter. :issue:`2294`

.. _v3.30.1:

:version:`3.30.1 <v3.30.0...v3.30.1>` - 2024-06-24
--------------------------------------------------

**Added**

- ``--output-truncate=false`` CLI option to disable schema and response payload truncation in error messages.

**Changed**

- More fine-grained events for stateful testing.

**Fixed**

- Internal error caused by an upstream race condition bug in Hypothesis. :issue:`2269`
- Do not output stateful tests sub-section in CLI if there are no stateful tests due to applied filters.

.. _v3.30.0:

:version:`3.30.0 <v3.29.2...v3.30.0>` - 2024-06-23
--------------------------------------------------

**Added**

- **EXPERIMENTAL**: New stateful test runner in CLI. :issue:`864`
- The ``--experimental=stateful-only`` CLI flag to run only stateful tests if the new test runner is enabled. Note that this feature is experimental and may change in future releases without notice.
- Ability to extract values from headers, path, and query parameters using regular expressions in OpenAPI links.
- The ``negative_data_rejection`` check. It ensures that the API rejects negative data as specified in the schema.
- The ``use_after_free`` check. It ensures that the API returns a 404 response after a successful DELETE operation on an object. At the moment, it is only available in state-machine-based stateful testing.
- Support for building dynamic payloads via OpenAPI links. This allows for building objects or arrays where nested items are not hardcoded but dynamically evaluated.
- ``APIStateMachine.format_rules`` method to format transition rules in a human-readable format.

.. code-block::

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

**Changed**

- Enforce the ``minLength`` keyword on string path parameters to avoid the rejection of empty values later on.
  This improves the performance of data generation.
- Rework building state machines for stateful testing to improve performance.
- Improve error messages on ``MaxRetryError``. :issue:`2234`
- Migrate to new-style ``pytest`` hooks. :issue:`2181`
- Filter out Hypothesis' warning about resetting the recursion limit in multi-worker tests.
- Show sub-schema location in ``response_schema_conformance`` failure messages. :issue:`2270`
- Avoid collecting data for stateful tests in CLI when they are explicitly disabled.

**Fixed**

- Internal error during OpenAPI link resolution if the needed parameter is missing in the response.
- Improper output when a JSON pointer can't be resolved during OpenAPI link resolution.
- Generating invalid examples created by wrapping a named example value into another object. :issue:`2238`
- Distinguish more failures in stateful testing.
- Generate different functions for state machine transitions to properly use swarm testing.
- ``RuntimeError`` caused by a race condition when initializing Hypothesis' PRNG in multiple workers.
- Missing body in ``Case`` if it is mutated after the ``make_case`` call. :issue:`2208`
- Internal error when a rate limiter hits its limit. :issue:`2254`
- Internal error during reference resolving when using relative file paths.
- Ignoring property examples defined under the ``example`` key in Open API 2.0 schemas. :issue:`2277`

**Removed**

- Support for ``pytest<6.0``.

**Performance**

- Improve performance of copying schemas.

.. _v3.29.2:

:version:`3.29.2 <v3.29.1...v3.29.2>` - 2024-05-31
--------------------------------------------------

**Fixed**

- Remove temporary ``print`` calls.

.. _v3.29.1:

:version:`3.29.1 <v3.29.0...v3.29.1>` - 2024-05-31
--------------------------------------------------

**Fixed**

- Inlining too much in stateful testing.

.. _v3.29.0:

:version:`3.29.0 <v3.28.1...v3.29.0>` - 2024-05-30
--------------------------------------------------

**Changed**:

- **INTERNAL**: Remove the ability to mutate components used in ``schema["/path"]["METHOD"]`` access patterns.

**Fixed**

- Not serializing shared parameters for an API operation.
- ``OperationNotFound`` raised in ``schema.get_operation_by_id`` if the relevant path item is behind a reference.
- Missing parameters shared under the same path in stateful testing if the path is behind a reference.
- ``KeyError`` instead of ``OperationNotFound`` when the operation ID is not found in Open API 3.1 without path entries.
- Not respecting ``allow_x00=False`` in headers and cookies. :issue:`2220`
- Internal error when building an error message for some network-related issues. :issue:`2219`

**Performance**

- Optimize ``schema["/path"]["METHOD"]`` access patterns and reduce memory usage.
- Optimize ``get_operation_by_id`` method performance and reduce memory usage.
- Optimize ``get_operation_by_reference`` method performance.
- Less copying during schema traversal.

.. _v3.28.1:

:version:`3.28.1 <v3.28.0...v3.28.1>` - 2024-05-11
--------------------------------------------------

**Fixed**

- Internal error on unresolvable Open API links during stateful testing.
- Internal error when media type definition has only ``example`` or ``examples`` keys.

**Performance**

- Improve performance of ``add_link`` by avoiding unnecessary reference resolving.

.. _v3.28.0:

:version:`3.28.0 <v3.27.1...v3.28.0>` - 2024-05-10
--------------------------------------------------

**Added**

- ``Request.deserialize_body`` and ``Response.deserialize_body`` helper methods to deserialize payloads to bytes from Base 64.
- Support for ``multipart/mixed`` media type.

**Changed**

- Do not show suggestion to show a traceback on Hypothesis' ``Unsatisfiable`` error.
- Clarify error message on unsupported recursive references.
- Report more details on some internal errors instead of "Unknown Schema Error".
- Update error message on why Schemathesis can't generate test cases for some API operations.

**Fixed**

- Internal error on Windows when the CLI output is redirected to a file and code samples contain non CP1252 characters.
- Properly check for nested recursive references inside combinators. This makes Schemathesis work with more schemas with recursive references.

.. _v3.27.1:

:version:`3.27.1 <v3.27.0...v3.27.1>` - 2024-04-29
--------------------------------------------------

**Added**

- ``GenerationConfig.headers.strategy`` attribute for customizing header generation. :issue:`2137`
- Support for ``python -m schemathesis.cli``. :issue:`2142`
- Support for ``anyio>=4.0``. :issue:`2081`

**Fixed**

- Supporting non-Starlette ASGI apps. :issue:`2136`
- Missing version metadata in ASGI client causing errors with ASGI3-only apps. :issue:`2136`

.. _v3.27.0:

:version:`3.27.0 <v3.26.2...v3.27.0>` - 2024-04-14
--------------------------------------------------

**Added**

- ``Case.as_transport_kwargs`` method to simplify the creation of transport-specific keyword arguments for sending requests.

**Changed**

- Make ``Case.call`` work with ``ASGI`` & ``WSGI`` applications.
- Extend the JUnit XML report format to match CLI output including skipped tests, code samples, and more.

**Deprecated**

- ``Case.call_wsgi`` & ``Case.call_asgi`` in favor of ``Case.call``.
- ``Case.as_requests_kwargs`` & ``Case.as_werkzeug_kwargs`` in favor of ``Case.as_transport_kwargs``.

.. _v3.26.2:

:version:`3.26.2 <v3.26.1...v3.26.2>` - 2024-04-06
--------------------------------------------------

**Added**

- Support for ``pyrate-limiter>=3.0``.

**Fixed**

- Excluding ``\x00`` bytes as a result of probes.

.. _v3.26.1:

:version:`3.26.1 <v3.26.0...v3.26.1>` - 2024-04-04
--------------------------------------------------

**Added**

- Store time needed to generate each test case.

**Fixed**

- ``InvalidArgument`` when using ``from_pytest_fixture`` with parametrized pytest fixtures and Hypothesis settings. :issue:`2115`

.. _v3.26.0:

:version:`3.26.0 <v3.25.6...v3.26.0>` - 2024-03-21
--------------------------------------------------

**Added**

- Support for per-media type data generators. :issue:`962`
- Support for ``application/yaml`` & ``text/yml`` media types in ``YAMLSerializer``.
- **EXPERIMENTAL**: Run automatic schema optimization & format inference if CLI is authenticated in Schemathesis.io.

**Fixed**

- Not resolving references in nested security schemes. :issue:`2073`

**Changed**

- Improve error message when the minimum possible example is too large.

.. _v3.25.6:

:version:`3.25.6 <v3.25.5...v3.25.6>` - 2024-03-02
--------------------------------------------------

**Fixed**

- Not respecting ``allow_x00`` and ``codec`` configs options during filling gaps in explicit examples.
- Internal error when sending ``multipart/form-data`` requests when the schema defines the ``*/*`` content type.
- Internal error when YAML payload definition contains nested ``binary`` format.
- Internal error when an Open API 2.0 schema contains no ``swagger`` key and the schema version is forced.

**Changed**

- Indicate API probing results in CLI.

.. _v3.25.5:

:version:`3.25.5 <v3.25.4...v3.25.5>` - 2024-02-29
--------------------------------------------------

**Fixed**

- Incorrect error message when the code inside the hook module raises ``ImportError``. :issue:`2074`
- Compatibility with Hypothesis >6.98.14
- Not respecting ``allow_x00`` and ``codec`` configs options for data generation in some cases. :issue:`2072`

.. _v3.25.4:

:version:`3.25.4 <v3.25.3...v3.25.4>` - 2024-02-25
--------------------------------------------------

**Changed**

- Improve error message when the minimum possible example is too large.

.. _v3.25.3:

:version:`3.25.3 <v3.25.2...v3.25.3>` - 2024-02-22
--------------------------------------------------

**Added**

- Added ``__contains__`` method to ``ParameterSet`` for easier parameter checks in hooks.

**Changed**

- Suppress TLS-related warnings during API probing.

.. _v3.25.2:

:version:`3.25.2 <v3.25.1...v3.25.2>` - 2024-02-21
--------------------------------------------------

**Added**

- Run automatic probes to detect the application capabilities before testing.
  They allow for more accurate data generation, reducing false positive test failures. :issue:`1840`
- Support running async Python tests with ``trio``. :issue:`1872`

**Fixed**

- Invalid spec detection if the experimental support for Open API 3.1 is not explicit explicitly enabled.
- Invalid spec detection if the input YAML contains not allowed characters.
- ``AttributeError`` when using the experimental support for Open API 3.1 with multiple workers.
- Do not skip API operation if it is still possible to generate positive tests when ``-D all`` is passed.  

.. _v3.25.1:

:version:`3.25.1 <v3.25.0...v3.25.1>` - 2024-02-10
--------------------------------------------------

**Changed**

- **CLI**: Enhanced Open API 3.1.0 support messaging, now suggesting ``--experimental=openapi-3.1`` option for partial compatibility.

**Fixed**

- Not reporting errors during testing of explicit examples when data generation is flaky.

.. _v3.25.0:

:version:`3.25.0 <v3.24.3...v3.25.0>` - 2024-02-07
--------------------------------------------------

**Added**

- ``--hypothesis-no-phases`` CLI option to disable Hypothesis testing phases. :issue:`1324`
- Support for loading GraphQL schemas from JSON files that contain the ``__schema`` key.
- Response validation for GraphQL APIs.
- Support ``tag`` in filters for custom auth.
- Support for testing examples inside ``anyOf`` / ``oneOf`` / ``allOf`` keywords.
- Support for the ``text/xml`` media type in ``XMLSerializer``.
- Support for the ``text/json`` media type in ``JSONSerializer``.
- Support for pytest 8.

**Changed**

- **CLI**: Eagerly check for permissions when writing output to a file, including JUnit XML and other reports.
- **Python**: Explicitly note that combining ``@schema.given`` with explicit examples from the spec is not supported. :issue:`1217`
- Clarify error message when a state machine has no transitions. :issue:`1992`
- Do not consider missing the ``paths`` key an error for Open API 3.1.
- Improved formatting of multiple errors within the same API operation.
- Allow arbitrary objects in array for ``application/x-www-form-urlencoded`` payloads.

**Deprecated**

- The ``--contrib-unique-data`` CLI option and the corresponding ``schemathesis.contrib.unique_data`` hook. The concept of this feature
  does not fit the core principles of Hypothesis where strategies are configurable on a per-example basis but this feature implies
  uniqueness across examples. This leads to cryptic error messages about external state and flaky test runs, therefore it will be removed in
  Schemathesis 4.0

**Fixed**

- **CLI**: Do not duplicate the error message in the output when the error has no traceback and the ``--show-trace`` option is provided.
- **Open API**: Internal error on path templates that contain ``.`` inside path parameters.
- **Open API**: YAML serialization of data generated for schemas with ``format: binary``.
- Create parent directories when saving JUnit XML reports and other file-based output. :issue:`1995`
- Internal error when an API operation contains multiple parameters with the same name and some of them contain the ``examples`` keyword.
- Internal error during query parameter generation on schemas that do not contain the ``type`` keyword.
- Example generation for request body parameters using ``$ref``.
- Generating examples for properties that have deeply nested ``$ref``. 
- Generating examples for properties with boolean sub-schemas.
- Validating responses with boolean sub-schemas on Open API 3.1.
- ``TypeError`` on non-string ``pattern`` values. This could happen on values in YAML, such that when not quoted, they are parsed
  as non-strings.
- Testing examples requiring unsupported payload media types resulted in an internal error. These are now correctly reported as errors 
- Internal error on unsupported regular expressions in inside properties during example generation.
- Missing XML definitions when the media type contains options like ``application/xml; charset=utf-8``.
- Unhandled timeout while reading the response payload.
- Internal error when the header example in the schema is not a valid header.
- Handle ``KeyError`` during state machine creation.
- Deduplicate network errors that contain unique URLs in their messages.
- Not reporting multiple errors of different kinds at the same API operation.
- Group similar errors within the same API operation.

.. _v3.24.3:

:version:`3.24.3 <v3.24.2...v3.24.3>` - 2024-01-23
--------------------------------------------------

**Fixed**

- Incorrect base URL handling for GraphQL schemas. :issue:`1987`

.. _v3.24.2:

:version:`3.24.2 <v3.24.1...v3.24.2>` - 2024-01-23
--------------------------------------------------

**Added**

- **Python**: Shortcut to create strategies for all operations or a subset of them via ``schema.as_strategy()`` and ``schema["/path/"].as_strategy()``. :issue:`1982`

**Changed**

- **Python**: Cleaner ``repr`` for GraphQL & Open API schemas.
- **GraphQL**: Show suggestion when a field is not found in ``schema["Query"][field_name]``.

**Fixed**

- Filter out test cases that can not be serialized when the API operation requires ``application/x-www-form-urlencoded``. :issue:`1306`

.. _v3.24.1:

:version:`3.24.1 <v3.24.0...v3.24.1>` - 2024-01-22
--------------------------------------------------

**Changed**

- Cleanup SSL error messages.

**Fixed**

- Internal error when an unresolvable pointer occurs during data generation.
- Internal errors when references lead to non-objects.
- Missing ``schema.override`` on schemas created via the ``from_pytest_fixture`` loader.
- Not calling hooks for ``query`` / ``cookies`` / ``headers`` in GraphQL schemas. :issue:`1978`
- Inability to access individual operations in GraphQL schema objects. :issue:`1976`

.. _v3.24.0:

:version:`3.24.0 <v3.23.1...v3.24.0>` - 2024-01-21
--------------------------------------------------

**Added**

- CLI options for overriding Open API parameters in test cases. :issue:`1676`
- A way to override Open API parameters the ``pytest`` integration with the ``override`` decorator. :issue:`8`
- **Open API**: Support for the ``examples`` keyword inside individual property schemas. :issue:`1730`, :issue:`1320`
- **Open API**: Extract explicit examples from all defined media types. :issue:`921`

**Changed**

- Raise an error if it is not possible to generate explicit examples. :issue:`1771`
- Avoid using the deprecated ``cgi`` module. :issue:`1962`

**Fixed**

- **Open API**: Properly combine multiple explicit examples extracted from ``examples`` and ``example`` fields. :issue:`1360`
- **Open API**: Ignoring examples referenced via the ``$ref`` keyword. :issue:`1692`

.. _v3.23.1:

:version:`3.23.1 <v3.23.0...v3.23.1>` - 2024-01-14
--------------------------------------------------

**Changed**

- Do not auto-detect spec if the ``--force-schema-version`` CLI option is present.
- Do not assume GraphQL when trying to auto-detect spec in an empty input file.

**Fixed**

- Internal error when the schema file is empty.

.. _v3.23.0:

:version:`3.23.0 <v3.22.1...v3.23.0>` - 2023-12-29
--------------------------------------------------

**Added**

- New CLI option ``--contrib-openapi-fill-missing-examples`` to automatically generate random examples for API operations that lack explicit examples. :issue:`1728`, :issue:`1376`
- New CLI option ``--request-proxy`` to set HTTP(s) proxies for network calls. :issue:`1723`

**Changed**

- Validate ``--generation-codec`` values in CLI.
- Do not deepcopy responses before passing to checks. They are not supposed to be mutated inside checks.
- Pin ``anyio`` to ``<4`` due to incompatibility with ``starlette-testclient``.

**Fixed**

- Internal error when the configured proxy is not available.
- Not using ``examples`` from shared ``parameters``. :issue:`1729`, :issue:`1513`

.. _v3.22.1:

:version:`3.22.1 <v3.22.0...v3.22.1>` - 2023-12-04
--------------------------------------------------

**Fixed**

- Internal error during network error handling. :issue:`1933`

.. _v3.22.0:

:version:`3.22.0 <v3.21.2...v3.22.0>` - 2023-12-03
--------------------------------------------------

**Added**

- Support for ``hypothesis-jsonschema==0.23``.
- A way to control what characters are used for string generation. :issue:`1142`, :issue:`1286`, :issue:`1562`, :issue:`1668`.
- Display the total number of collected links in the CLI output. :issue:`1383`.
- ``arm64`` Docker builds. :issue:`1740`.
- Use Python 3.12 in Docker images.
- Store Docker image name in ``Metadata``.
- GraphQL scalar strategies for ``Date``, ``Time``, ``DateTime``, ``IP``, ``IPv4``, ``IPv6``, ``Long``, ``BigInt`` and ``UUID``. :issue:`1690`

**Changed**

- Bump the minimum supported Hypothesis version to ``6.84.3``.
- Bump the minimum supported ``jsonschema`` version to ``4.18.0``.
- Bump the minimum supported ``hypothesis_graphql`` version to ``0.11.0``.
- Use the same random seed for all tests in CLI. :issue:`1384`.
- Improve serialization error messages in CLI.
- Store skip reason in the runner events.
- Build ``bookworm``-based Debian Docker images instead of ``buster``-based.
- Improve error message on unknown scalar types in GraphQL.
- Better auto-detection of GraphQL schemas.
- Display parsing errors for schemas that are expected to be JSON or YAML.

**Deprecated**

- Using the ``--show-errors-tracebacks`` CLI option. Use ``--show-trace`` instead.

**Fixed**

- Internal error when a non-existing schema file is passed together with ``--base-url``. :issue:`1912`.
- Internal error during schema loading from invalid URLs.
- Ignore incompatible GraphQL checks in CLI rather than fail the whole test run. :issue:`1918`.

**Removed**

- Support for Python 3.7.
- Unnecessary dependencies on ``typing-extensions`` and ``importlib-metadata``.

.. _v3.21.2:

:version:`3.21.2 <v3.21.1...v3.21.2>` - 2023-11-27
--------------------------------------------------

**Added**

- Support for ``hypothesis>=6.90.1``.

.. _v3.21.1:

:version:`3.21.1 <v3.21.0...v3.21.1>` - 2023-11-16
--------------------------------------------------

**Added**

- Basic support for ``httpx`` in ``Case.validate_response``.

**Changed**

- Restore the ability to import ``NOT_SET`` from ``schemathesis.utils``. :issue:`1890`

.. _v3.21.0:

:version:`3.21.0 <v3.20.2...v3.21.0>` - 2023-11-09
--------------------------------------------------

**Added**

- Add Python 3.12 compatibility. :issue:`1809`
- Separate command for report upload.

**Changed**

- Generated binary data inside ``Case.body`` is wrapped with a custom wrapper - ``Binary`` in order to simplify
  compatibility with ``hypothesis-jsonschema``.
- Do not modify ``Case.body`` inside ``Case.as_requests_kwargs`` when serializing multipart data.
- **INTERNAL**: Moved heavy imports inside functions to improve CLI startup time by 4.3x, not affecting overall execution speed. :issue:`1509`
- Improved messaging for loading hooks and WSGI application issues.
- Refined documentation strings for CLI options.
- Added an error message if an internal error happened inside CLI event handler.
- Unified CLI messages for errors arising from network, configuration, and Hypothesis-related issues. :issue:`1600`, :issue:`1607`, :issue:`1782`, :issue:`1835`
- Try to validate JSON data even if there is no proper ``Content-Type`` header. :issue:`1787`
- Refined failure reporting for clarity. :issue:`1784`, :issue:`1785`, :issue:`1790`, :issue:`1799`, :issue:`1800`

.. _v3.20.2:

:version:`3.20.2 <v3.20.1...v3.20.2>` - 2023-10-27
--------------------------------------------------

**Fixed**

- Incorrect documentation & implementation for enabling experimental features in ``pytest``.

.. _v3.20.1:

:version:`3.20.1 <v3.20.0...v3.20.1>` - 2023-10-20
--------------------------------------------------

**Changed**

- Improved CLI error messages for missing or invalid arguments.

.. _v3.20.0:

:version:`3.20.0 <v3.19.7...v3.20.0>` - 2023-10-18
--------------------------------------------------

**Added**

- Support for ``application/xml`` serialization based on Open API schema definitions. :issue:`733`
- Hook shortcuts (``filter_query``, ``map_header``, etc.) to minimize boilerplate in extensions. :issue:`1673`
- Support for colored output from docker container. :issue:`1170`
- A way to disable suggestion for visualizing test results via the ``SCHEMATHESIS_REPORT_SUGGESTION=0`` environment variable. :issue:`1802`
- Automatic FastAPI fixup injecting for ASGI loaders, eliminating the need for manual setup. :issue:`1797`
- Support for ``body`` hooks in GraphQL schemas, enabling custom filtering or modification of queries and mutations. :issue:`1464`
- New ``filter_operations`` hook to conditionally include or exclude specific API operations from being tested.
- Added ``contains`` method to ``ParameterSet`` for easier parameter checks in hooks. :issue:`1789`
- Automatic sanitization of sensitive data in the output is now enabled by default. This feature can be disabled using the ``--sanitize-output=false`` CLI option. For more advanced customization, use ``schemathesis.sanitizing.configure()``. :issue:`1794`
- ``--experimental=openapi-3.1`` CLI option for experimental support of OpenAPI 3.1. This enables compatible JSON Schema validation for responses, while data generation remains OpenAPI 3.0-compatible. :issue:`1820`

**Note**: Experimental features can change or be removed in any minor version release.

**Changed**

- Support ``Werkzeug>=3.0``. :issue:`1819`
- Refined generated reproduction code and shortened ``X-Schemathesis-TestCaseId`` for easier debugging. :issue:`1801`
- Add ``case`` as the first argument to ``AuthContext.set``. Previous calling convention is still supported. :issue:`1788`
- Disable the 'explain' phase in Hypothesis to improve performance. :issue:`1808`
- Simplify Python code samples for failure reproduction.
- Do not display ``InsecureRequestWarning`` in CLI output if the user explicitly provided ``--request-tls-verify=false``. :issue:`1780`
- Enhance CLI output for schema loading and internal errors, providing clearer diagnostics and guidance. :issue:`1781`, :issue:`1517`, :issue:`1472`

Before:

.. code:: text

    Failed to load schema from https://127.0.0.1:6423/openapi.json
    You can use `--wait-for-schema=NUM` to wait for a maximum of NUM seconds on the API schema availability.

    Error: requests.exceptions.SSLError: HTTPSConnectionPool(host='localhost', port=6423): Max retries exceeded with url: /openapi.json (Caused by SSLError(SSLCertVerificationError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number (_ssl.c:992)')))

    Add this option to your command line parameters to see full tracebacks: --show-errors-tracebacks

After:

.. code:: text

    Schema Loading Error

    SSL verification problem

        [SSL: WRONG_VERSION_NUMBER] wrong version number

    Tip: Bypass SSL verification with `--request-tls-verify=false`.

**Deprecated**

- Defining ``AuthProvider.get`` with a single ``context`` argument. The support will be removed in Schemathesis ``4.0``.

**Fixed**

- Fixed type hint for ``AuthProvider``. :issue:`1776`
- Do not skip negative tests if the generated value is ``None``.
- Lack of execution for ASGI events during testing. :issue:`1305`, :issue:`1727`
- Confusing error message when trying to load schema from a non-existing file. :issue:`1602`
- Reflect disabled TLS verification in generated code samples. :issue:`1054`
- Generated cURL commands now include the ``Content-Type`` header, which was previously omitted. :issue:`1783`
- Improperly serialized headers in ``SerializedHistoryEntry.case.extra_headers``.

**Performance**

- Optimize event data emission by postponing code sample generation, resulting in a ``~4%`` reduction in the emitted events data size.

**Removed**

- Unused ``SerializedError.example`` attribute. It used to be populated for flaky errors before they became regular failures.
- Unused ``TestResult.overridden_headers`` attribute.

.. _v3.19.7:

:version:`3.19.7 <v3.19.6...v3.19.7>` - 2023-09-03
--------------------------------------------------

**Fixed**

- ``Unsatisfiable`` error for multiple security schemes applied to the same API operation and an explicit ``Authorization`` header. :issue:`1763`

.. _v3.19.6:

:version:`3.19.6 <v3.19.5...v3.19.6>` - 2023-08-14
--------------------------------------------------

**Fixed**

- Broken ``--report`` CLI argument under ``click>=8.1.4``. :issue:`1753`

.. _v3.19.5:

:version:`3.19.5 <v3.19.4...v3.19.5>` - 2023-06-03
--------------------------------------------------

**Fixed**

- Do not raise ``Unsatisfiable`` when explicit headers are provided for negative tests.
- Do not raise ``Unsatisfiable`` when no headers can be negated.

.. _v3.19.4:

:version:`3.19.4 <v3.19.3...v3.19.4>` - 2023-06-03
--------------------------------------------------

**Fixed**

- Improved handling of negative test scenarios by not raising ``Unsatisfiable`` when path parameters cannot be negated but other parameters can be negated.

.. _v3.19.3:

:version:`3.19.3 <v3.19.2...v3.19.3>` - 2023-05-25
--------------------------------------------------

**Changed**

- Support ``requests<3``. :issue:`1742`
- Bump the minimum supported ``Hypothesis`` version to ``6.31.6`` to reflect requirement from ``hypothesis-jsonschema``.

**Fixed**

- ``HypothesisDeprecationWarning`` regarding deprecated ``HealthCheck.all()``. :issue:`1739`

.. _v3.19.2:

:version:`3.19.2 <v3.19.1...v3.19.2>` - 2023-05-20
--------------------------------------------------

**Added**

- You can now provide a tuple of checks to exclude when validating a response.

.. _v3.19.1:

:version:`3.19.1 <v3.19.0...v3.19.1>` - 2023-04-26
--------------------------------------------------

**Changed**

- Support ``requests<2.29``.

**Fixed**

- Passing ``params`` / ``cookies`` to ``case.call`` causing ``TypeError``. :issue:`1734`

**Removed**

- Direct dependency on ``attrs``.

.. _v3.19.0:

:version:`3.19.0 <v3.18.5...v3.19.0>` - 2023-03-22
--------------------------------------------------

**Added**

- Schemathesis now supports custom authentication mechanisms from the ``requests`` library.
  You can use ``schemathesis.auth.set_from_requests`` to set up Schemathesis CLI with any third-party authentication implementation that works with ``requests``. :issue:`1700`

.. code:: python

    import schemathesis
    from requests_ntlm import HttpNtlmAuth

    schemathesis.auth.set_from_requests(HttpNtlmAuth("domain\\username", "password"))

- Ability to apply authentication conditionally to specific API operations using a combination of ``@schemathesis.auth.apply_to()`` and ``@schemathesis.auth.skip_for()`` decorators.

.. code:: python

    import schemathesis


    # Apply auth only for operations that path starts with `/users/` but not the `POST` method
    @schemathesis.auth().apply_to(path_regex="^/users/").skip_for(method="POST")
    class MyAuth:
        ...

- Add a convenience mapping-like interface to ``OperationDefinition`` including indexing access, the ``get`` method, and "in" support.
- Request throttling via the ``--rate-limit`` CLI option. :issue:`910`

**Changed**

- Unified Schemathesis custom authentication usage via the ``schema.auth`` decorator, replacing the previous ``schema.auth.register`` and ``schema.auth.apply`` methods:

.. code:: python

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

**Fixed**

- Handling of query parameters and cookies passed to ``case.call`` and query parameters passed to ``case.call_wsgi``.
  The user-provided values are now merged with the data generated by Schemathesis, instead of overriding it completely. :issue:`1705`
- Parameter definition takes precedence over security schemes with the same name.
- ``Unsatisfiable`` error when explicit header name passed via CLI clashes with the header parameter name. :issue:`1699`
- Not using the ``port`` keyword argument in schema loaders during API schema loading. :issue:`1721`

.. _v3.18.5:

:version:`3.18.5 <v3.18.4...v3.18.5>` - 2023-02-18
--------------------------------------------------

**Added**

- Support for specifying the path to load hooks from via the ``SCHEMATHESIS_HOOKS`` environment variable. `#1702`.

**Deprecated**

- Use of the ``--pre-run`` CLI option for loading hooks. Use the ``SCHEMATHESIS_HOOKS`` environment variable instead.

.. _v3.18.4:

:version:`3.18.4 <v3.18.3...v3.18.4>` - 2023-02-16
--------------------------------------------------

**Changed**

- Support any Werkzeug 2.x in order to allow mitigation of `CVE-2023-25577 <https://github.com/advisories/GHSA-xg9f-g7g7-2323>`_. :issue:`1695`

.. _v3.18.3:

:version:`3.18.3 <v3.18.2...v3.18.3>` - 2023-02-12
--------------------------------------------------

**Added**

- ``APIStateMachine.run`` method to simplify running stateful tests.

**Changed**

- Improved quality of generated test sequences by updating state machines in Schemathesis to always run a minimum of two steps during testing. :issue:`1627`
  If you use ``hypothesis.stateful.run_state_machine_as_test`` to run your stateful tests, please use the ``run`` method on your state machine class instead.
  This change requires upgrading ``Hypothesis`` to at least version ``6.68.1``.

.. _v3.18.2:

:version:`3.18.2 <v3.18.1...v3.18.2>` - 2023-02-08
--------------------------------------------------

**Performance**

- Modify values in-place inside built-in ``map`` functions as there is no need to copy them.
- Update ``hypothesis-jsonschema`` to ``0.22.1`` for up to 30% faster data generation in some workflows.

.. _v3.18.1:

:version:`3.18.1 <v3.18.0...v3.18.1>` - 2023-02-06
--------------------------------------------------

**Changed**

- Stateful testing: Only make stateful requests when stateful data is available from another operation.
  This change significantly reduces the number of API calls that likely will fail because of absence of stateful data. :issue:`1669`

**Performance**

- Do not merge component schemas into the currently tested schema if they are not referenced by it. Originally all
  schemas were merged to make them visible to ``hypothesis-jsonschema``, but they imply significant overhead. :issue:`1180`
- Use a faster, specialized version of ``deepcopy``.

.. _v3.18.0:

:version:`3.18.0 <v3.17.5...v3.18.0>` - 2023-02-01
--------------------------------------------------

**Added**

- Extra information to VCR cassettes.
- The ``--contrib-unique-data`` CLI option that forces Schemathesis to generate unique test cases only.
  This feature is also available as a hook in ``schemathesis.contrib.unique_data``.
- A few decorators & functions that provide a simpler API to extend Schemathesis:
    - ``schemathesis.auth()`` for authentication providers;
    - ``schemathesis.check`` for checks;
    - ``schemathesis.hook`` & ``BaseSchema.hook`` for hooks;
    - ``schemathesis.serializer`` for serializers;
    - ``schemathesis.target`` for targets;
    - ``schemathesis.openapi.format`` for custom OpenAPI formats.
    - ``schemathesis.graphql.scalar`` for GraphQL scalars.
- Open API: UUID format generation via the ``schemathesis.contrib.openapi.formats.uuid`` extension
  You could enable it via the ``--contrib-openapi-formats-uuid`` CLI option.

**Changed**

- Build: Switch the build backend to `Hatch <https://hatch.pypa.io/>`_.
- Relax requirements for ``attrs``. :issue:`1643`
- Avoid occasional empty lines in cassettes.

**Deprecated**

- ``schemathesis.register_check`` in favor of ``schemathesis.check``.
- ``schemathesis.register_target`` in favor of ``schemathesis.target``.
- ``schemathesis.register_string_format`` in favor of ``schemathesis.openapi.format``.
- ``schemathesis.graphql.register_scalar`` in favor of ``schemathesis.graphql.scalar``.
- ``schemathesis.auth.register`` in favor of ``schemathesis.auth``.

**Fixed**

- Remove recursive references from the last reference resolution level.
  It works on the best effort basis and does not cover all possible cases. :issue:`947`
- Invalid cassettes when headers contain characters with a special meaning in YAML.
- Properly display flaky deadline errors.
- Internal error when the ``utf8_bom`` fixup is used for WSGI apps.
- Printing header that are set explicitly via ``get_call_kwargs`` in stateful testing. :issue:`828`
- Display all explicitly defined headers in the generated cURL command.
- Replace ``starlette.testclient.TestClient`` with ``starlette_testclient.TestClient`` to keep compatibility with newer
  ``starlette`` versions. :issue:`1637`

**Performance**

- Running negative tests filters out less data.
- Schema loading: Try a faster loader first if an HTTP response or a file is expected to be JSON.

.. _v3.17.5:

:version:`3.17.5 <v3.17.4...v3.17.5>` - 2022-11-08
--------------------------------------------------

**Added**

- Python 3.11 support. :issue:`1632`

**Fixed**

- Allow ``Werkzeug<=2.2.2``. :issue:`1631`

.. _v3.17.4:

:version:`3.17.4 <v3.17.3...v3.17.4>` - 2022-10-19
--------------------------------------------------

**Fixed**

- Appending an extra slash to the ``/`` path. :issue:`1625`

.. _v3.17.3:

:version:`3.17.3 <v3.17.2...v3.17.3>` - 2022-10-10
--------------------------------------------------

**Fixed**

- Missing ``httpx`` dependency. :issue:`1614`

.. _v3.17.2:

:version:`3.17.2 <v3.17.1...v3.17.2>` - 2022-08-27
--------------------------------------------------

**Fixed**

- Insufficient timeout for report uploads.

.. _v3.17.1:

:version:`3.17.1 <v3.17.0...v3.17.1>` - 2022-08-19
--------------------------------------------------

**Changed**

- Support ``requests==2.28.1``.

.. _v3.17.0:

:version:`3.17.0 <v3.16.5...v3.17.0>` - 2022-08-17
--------------------------------------------------

**Added**

- Support for exception groups in newer ``Hypothesis`` versions. :issue:`1592`
- A way to generate negative and positive test cases within the same CLI run via ``-D all``.

**Fixed**

- Allow creating APIs in Schemathesis.io by name when the schema is passed as a file.
- Properly trim tracebacks on ``Hypothesis>=6.54.0``.
- Skipping negative tests when they should not be skipped.

**Changed**

- **pytest**: Generate positive & negative within the same test node.
- **CLI**: Warning if there are too many HTTP 403 API responses.
- **Runner**: ``BeforeExecution.data_generation_method`` and ``AfterExecution.data_generation_method`` changed to
  lists of ``DataGenerationMethod`` as the same test may contain data coming from different data generation methods.

.. _v3.16.5:

:version:`3.16.5 <v3.16.4...v3.16.5>` - 2022-08-11
--------------------------------------------------

**Fixed**

- CLI: Hanging on ``CTRL-C`` when ``--report`` is enabled.
- Internal error when GraphQL schema has its root types renamed. :issue:`1591`

.. _v3.16.4:

:version:`3.16.4 <v3.16.3...v3.16.4>` - 2022-08-09
--------------------------------------------------

**Changed**

- Suggest using ``--wait-for-schema`` if API schema is not available.

.. _v3.16.3:

:version:`3.16.3 <v3.16.2...v3.16.3>` - 2022-08-08
--------------------------------------------------

**Added**

- CLI: ``--max-failures=N`` option to exit after first ``N`` failures or errors. :issue:`1580`
- CLI: ``--wait-for-schema=N`` option to automatically retry schema loading for ``N`` seconds. :issue:`1582`
- CLI: Display old and new payloads in ``st replay`` when the ``-v`` option is passed. :issue:`1584`

**Fixed**

- Internal error on generating negative tests for query parameters with ``explode: true``.

.. _v3.16.2:

:version:`3.16.2 <v3.16.1...v3.16.2>` - 2022-08-05
--------------------------------------------------

**Added**

- CLI: Warning if **ALL** API responses are HTTP 404.
- The ``after_load_schema`` hook, which is designed for modifying the loaded API schema before running tests.
  For example, you can use it to add Open API links to your schema via ``schema.add_link``.
- New ``utf8_bom`` fixup. It helps to mitigate JSON decoding errors inside the ``response_schema_conformance`` check when payload contains BOM. :issue:`1563`

**Fixed**

- Description of ``-v`` or ``--verbosity`` option for CLI.

**Changed**

- Execute ``before_call`` / ``after_call`` hooks inside the ``call_*`` methods. It makes them available for the ``pytest`` integration.

.. _v3.16.1:

:version:`3.16.1 <v3.16.0...v3.16.1>` - 2022-07-29
--------------------------------------------------

**Added**

- CLI: Warning if the API returns too many HTTP 401.
- Add ``SCHEMATHESIS_BASE_URL`` environment variable for specifying ``--base-url`` in CLI.
- Collect anonymyzed CLI usage telemetry when reports are uploaded. We do not collect any free-form values you use in your CLI,
  except for header names. Instead, we measure how many times you use each free-form option in this command.
  Additionally we count all non-default hook types only by hook name.

.. important::

  You can disable usage this with the ``--schemathesis-io-telemetry=false`` CLI option or the ``SCHEMATHESIS_TELEMETRY=false`` environment variable.

.. _v3.16.0:

:version:`3.16.0 <v3.15.6...v3.16.0>` - 2022-07-22
--------------------------------------------------

**Added**

- Report uploading to Schemathesis.io via the ``--report`` CLI option.

**Changed**

- Do not validate schemas by default in the ``pytest`` integration.
- CLI: Display test run environment metadata only if ``-v`` is provided.
- CLI: Do not display headers automatically added by ``requests`` in code samples.

**Fixed**

- Do not report optional headers as missing.
- Compatibility with ``hypothesis>=6.49``. :issue:`1538`
- Handling of ``unittest.case.SkipTest`` emitted by newer Hypothesis versions.
- Generating invalid headers when their schema has ``array`` or ``object`` types.

**Removed**

- Previously, data was uploaded to Schemathesis.io when the proper credentials were specified. This release removes this behavior.
  From now on, every upload requires the explicit ``--report`` CLI option.
- Textual representation of HTTP requests in CLI output in order to decrease verbosity and avoid showing the same data
  in multiple places.

.. _v3.15.6:

:version:`3.15.6 <v3.15.5...v3.15.6>` - 2022-06-23
--------------------------------------------------

**Fixed**

- Do not discard dots (``.``) in OpenAPI expressions during parsing.

.. _v3.15.5:

:version:`3.15.5 <v3.15.4...v3.15.5>` - 2022-06-21
--------------------------------------------------

**Fixed**

- ``TypeError`` when using ``--auth-type=digest`` in CLI.

.. _v3.15.4:

:version:`3.15.4 <v3.15.3...v3.15.4>` - 2022-06-06
--------------------------------------------------

**Added**

- Support generating data for Open API request payloads with wildcard media types. :issue:`1526`

**Changed**

- Mark tests as skipped if there are no explicit examples and ``--hypothesis-phases=explicit`` is used. :issue:`1323`
- Parse all YAML mapping keys as strings, ignoring the YAML grammar rules. For example, ``on: true`` will be parsed as ``{"on": True}`` instead of ``{True: True}``.
  Even though YAML does not restrict keys to strings, in the Open API and JSON Schema context, this restriction is implied because the underlying data model
  comes from JSON.
- **INTERNAL**: Improve flexibility of event serialization.
- **INTERNAL**: Store request / response history in ``SerializedCheck``.

.. _v3.15.3:

:version:`3.15.3 <v3.15.2...v3.15.3>` - 2022-05-28
--------------------------------------------------

**Fixed**

- Deduplication of failures caused by malformed JSON payload. :issue:`1518`
- Do not re-raise ``InvalidArgument`` exception as ``InvalidSchema`` in non-Schemathesis tests. :issue:`1514`

.. _v3.15.2:

:version:`3.15.2 <v3.15.1...v3.15.2>` - 2022-05-09
--------------------------------------------------

**Fixed**

- Avoid generating negative query samples that ``requests`` will treat as an empty query.
- Editable installation via ``pip``.

.. _v3.15.1:

:version:`3.15.1 <v3.15.0...v3.15.1>` - 2022-05-03
--------------------------------------------------

**Added**

- **OpenAPI**: Expose ``APIOperation.get_security_requirements`` that returns a list of security requirements applied to the API operation
- Attach originally failed checks to "grouped" exceptions.

**Fixed**

- Internal error when Schemathesis doesn't have permission to create its ``hosts.toml`` file.
- Do not show internal Hypothesis warning multiple times when the Hypothesis database directory is not usable.
- Do not print not relevant Hypothesis reports when run in CI.
- Invalid ``verbose_name`` value in ``SerializedCase`` for GraphQL tests.

.. _v3.15.0:

:version:`3.15.0 <v3.14.2...v3.15.0>` - 2022-05-01
--------------------------------------------------

**Added**

- **GraphQL**: Mutations supports. Schemathesis will generate random mutations by default from now on.
- **GraphQL**: Support for registering strategies to generate custom scalars.
- Custom auth support for schemas created via ``from_pytest_fixture``.

**Changed**

- Do not encode payloads in cassettes as base64 by default. This change makes Schemathesis match the default Ruby's VCR behavior and
  leads to more human-readable cassettes. Use ``--cassette-preserve-exact-body-bytes`` to restore the old behavior. :issue:`1413`
- Bump ``hypothesis-graphql`` to ``0.9.0``.
- Avoid simultaneous authentication requests inside auth providers when caching is enabled.
- Reduce the verbosity of ``pytest`` output. A few internal frames and the "Falsifying example" block are removed from the output.
- Skip negative tests on API operations that are not possible to negate. :issue:`1463`
- Make it possible to generate negative tests if at least one parameter can be negated.
- Treat flaky errors as failures and display full report about the failure. :issue:`1081`
- Do not duplicate failing explicit example in the `HYPOTHESIS OUTPUT` CLI output section. :issue:`881`

**Fixed**

- **GraphQL**: Semantically invalid queries without aliases.
- **GraphQL**: Rare crashes on invalid schemas.
- Internal error inside ``BaseOpenAPISchema.validate_response`` on ``requests>=2.27`` when response body contains malformed JSON. :issue:`1485`
- ``schemathesis.from_pytest_fixture``: Display each failure if Hypothesis found multiple of them.

**Performance**

- **GraphQL**: Over 2x improvement from internal optimizations.

.. _v3.14.2:

:version:`3.14.2 <v3.14.1...v3.14.2>` - 2022-04-21
--------------------------------------------------

**Added**

- Support for auth customization & automatic refreshing. :issue:`966`

.. _v3.14.1:

:version:`3.14.1 <v3.14.0...v3.14.1>` - 2022-04-18
--------------------------------------------------

**Fixed**

- Using ``@schema.parametrize`` with test methods on ``pytest>=7.0``.

.. _v3.14.0:

:version:`3.14.0 <v3.13.9...v3.14.0>` - 2022-04-17
--------------------------------------------------

**Added**

- Open API link name customization via the ``name`` argument to ``schema.add_link``.
- ``st`` as an alias to the ``schemathesis`` command line entrypoint.
- ``st auth login`` / ``st auth logout`` to authenticate with Schemathesis.io.
- ``X-Schemathesis-TestCaseId`` header to help to distinguish test cases on the application side. :issue:`1303`
- Support for comma separated lists in the ``--checks`` CLI option. :issue:`1373`
- Hypothesis Database configuration for CLI via the ``--hypothesis-database`` option. :issue:`1326`
- Make the ``SCHEMA`` CLI argument accept API names from Schemathesis.io.

**Changed**

- Enable Open API links traversal by default. To disable it, use ``--stateful=none``.
- Do not validate API schema by default. To enable it back, use ``--validate-schema=true``.
- Add the ``api_name`` CLI argument to upload data to Schemathesis.io.
- Show response status code on failing checks output in CLI.
- Improve error message on malformed Open API path templates (like ``/foo}/``). :issue:`1372`
- Improve error message on malformed media types that appear in the schema or in response headers. :issue:`1382`
- Relax dependencies on ``pyyaml`` and ``click``.
- Add ``--cassette-path`` that is going to replace ``--store-network-log``. The old option is deprecated and will be removed in Schemathesis ``4.0``

**Fixed**

- Show the proper Hypothesis configuration in the CLI output. :issue:`1445`
- Missing ``source`` attribute in the ``Case.partial_deepcopy`` implementation. :issue:`1429`
- Duplicated failure message from ``content_type_conformance`` and ``response_schema_conformance`` checks when the checked response has no ``Content-Type`` header. :issue:`1394`
- Not copied ``case`` & ``response`` inside ``Case.validate_response``.
- Ignored ``pytest.mark`` decorators when they are applied before ``schema.parametrize`` if the schema is created via ``from_pytest_fixture``. :issue:`1378`

.. _v3.13.9:

:version:`3.13.9 <v3.13.8...v3.13.9>` - 2022-04-14
--------------------------------------------------

**Fixed**

- Compatibility with ``pytest-asyncio>=0.17.1``. :issue:`1452`

.. _v3.13.8:

:version:`3.13.8 <v3.13.7...v3.13.8>` - 2022-04-05
--------------------------------------------------

**Fixed**

- Missing ``media_type`` in the ``Case.partial_deepcopy`` implementation. It led to missing payload in failure reproduction code samples.

.. _v3.13.7:

:version:`3.13.7 <v3.13.6...v3.13.7>` - 2022-04-02
--------------------------------------------------

**Added**

- Support for ``Hypothesis>=6.41.0``. :issue:`1425`

.. _v3.13.6:

:version:`3.13.6 <v3.13.5...v3.13.6>` - 2022-03-31
--------------------------------------------------

**Changed**

- Deep-clone ``Response`` instances before passing to check functions.

.. _v3.13.5:

:version:`3.13.5 <v3.13.4...v3.13.5>` - 2022-03-31
--------------------------------------------------

**Changed**

- Deep-clone ``Case`` instances before passing to check functions.

.. _v3.13.4:

:version:`3.13.4 <v3.13.3...v3.13.4>` - 2022-03-29
--------------------------------------------------

**Added**

- Support for ``Werkzeug>=2.1.0``. :issue:`1410`

**Changed**

- Validate ``requests`` kwargs to catch cases when the ASGI integration is used, but the proper ASGI client is not supplied. :issue:`1335`

.. _v3.13.3:

:version:`3.13.3 <v3.13.2...v3.13.3>` - 2022-02-20
--------------------------------------------------

**Added**

- ``--request-tls-verify`` CLI option for the ``replay`` command. It controls whether Schemathesis verifies the server's TLS certificate.
  You can also pass the path to a CA_BUNDLE file for private certs. :issue:`1395`
- Support for client certificate authentication with ``--request-cert`` and ``--request-cert-key`` arguments for the ``replay`` command.

.. _v3.13.2:

:version:`3.13.2 <v3.13.1...v3.13.2>` - 2022-02-16
--------------------------------------------------

**Changed**

- Use Schemathesis default User-Agent when communicating with SaaS.

**Fixed**

- Use the same ``correlation_id`` in ``BeforeExecution`` and ``AfterExecution`` events if the API schema contains an error that
  causes an ``InvalidSchema`` exception during test execution.
- Use ``full_path`` in error messages in recoverable schema-level errors. It makes events generated in such cases consistent with usual events.

.. _v3.13.1:

:version:`3.13.1 <v3.13.0...v3.13.1>` - 2022-02-10
--------------------------------------------------

**Added**

- ``APIOperation.iter_parameters`` helper to iterate over all parameters.

**Fixed**

- Properly handle error if Open API parameter doesn't have ``content`` or ``schema`` keywords.

.. _v3.13.0:

:version:`3.13.0 <v3.12.3...v3.13.0>` - 2022-02-09
--------------------------------------------------

**Changed**

- Update integration with Schemathesis.io.
- Always show traceback for errors in Schemathesis.io integration.

.. _v3.12.3:

:version:`3.12.3 <v3.12.2...v3.12.3>` - 2022-01-13
--------------------------------------------------

**Fixed**

- Generating illegal unicode surrogates in queries. :issue:`1370`

.. _v3.12.2:

:version:`3.12.2 <v3.12.1...v3.12.2>` - 2022-01-12
--------------------------------------------------

**Fixed**

- Not-escaped single quotes in generated Python code samples. :issue:`1359`

.. _v3.12.1:

:version:`3.12.1 <v3.12.0...v3.12.1>` - 2021-12-31
--------------------------------------------------

**Fixed**

- Improper handling of ``base_url`` in ``call_asgi``, when the base URL has a non-empty base path. :issue:`1366`

.. _v3.12.0:

:version:`3.12.0 <v3.11.7...v3.12.0>` - 2021-12-29
--------------------------------------------------

**Changed**

- Upgrade ``typing-extensions`` to ``>=3.7,<5``.
- Upgrade ``jsonschema`` to ``^4.3.2``.
- Upgrade ``hypothesis-jsonschema`` to ``>=0.22.0``.

**Fixed**

- Generating values not compliant with the ECMAScript regex syntax. :issue:`1350`, :issue:`1241`.

**Removed**

- Support for Python 3.6.

.. _v3.11.7:

:version:`3.11.7 <v3.11.6...v3.11.7>` - 2021-12-23
--------------------------------------------------

**Added**

- Support for Python 3.10. :issue:`1292`

.. _v3.11.6:

:version:`3.11.6 <v3.11.5...v3.11.6>` - 2021-12-20
--------------------------------------------------

**Added**

- Support for client certificate authentication with ``--request-cert`` and ``--request-cert-key`` arguments. :issue:`1173`
- Support for ``readOnly`` and ``writeOnly`` Open API keywords. :issue:`741`

.. _v3.11.5:

:version:`3.11.5 <v3.11.4...v3.11.5>` - 2021-12-04
--------------------------------------------------

**Changed**

- Generate tests for API operations with the HTTP ``TRACE`` method on Open API 2.0.

.. _v3.11.4:

:version:`3.11.4 <v3.11.3...v3.11.4>` - 2021-12-03
--------------------------------------------------

**Changed**

- Add ``AfterExecution.data_generation_method``.
- Minor changes to the Schemathesis.io integration.

.. _v3.11.3:

:version:`3.11.3 <v3.11.2...v3.11.3>` - 2021-12-02
--------------------------------------------------

**Fixed**

- Silently failing to detect numeric status codes when the schema contains a shared ``parameters`` key. :issue:`1343`
- Not raising an error when tests generated by schemas loaded with ``from_pytest_fixture`` match no API operations. :issue:`1342`

.. _v3.11.2:

:version:`3.11.2 <v3.11.1...v3.11.2>` - 2021-11-30
--------------------------------------------------

**Changed**

- Use ``name`` & ``data_generation_method`` parameters to subtest context instead of ``path`` & ``method``.
  It allows the end-user to disambiguate among subtest reports.
- Raise an error if a test function wrapped with ``schema.parametrize`` matches no API operations. :issue:`1336`

**Fixed**

- Handle ``KeyboardInterrupt`` that happens outside of the main test loop inside the runner.
  It makes interrupt handling consistent, independent at what point it happens. :issue:`1325`
- Respect the ``data_generation_methods`` config option defined on a schema instance when it is loaded via ``from_pytest_fixture``. :issue:`1331`
- Ignored hooks defined on a schema instance when it is loaded via ``from_pytest_fixture``. :issue:`1340`

.. _v3.11.1:

:version:`3.11.1 <v3.11.0...v3.11.1>` - 2021-11-20
--------------------------------------------------

**Changed**

- Update ``click`` and ``PyYaml`` dependency versions. :issue:`1328`

.. _v3.11.0:

:version:`3.11.0 <v3.10.1...v3.11.0>` - 2021-11-03
--------------------------------------------------

**Changed**

- Show ``cURL`` code samples by default instead of Python. :issue:`1269`
- Improve reporting of ``jsonschema`` errors which are caused by non-string object keys.
- Store ``data_generation_method`` in ``BeforeExecution``.
- Use case-insensitive dictionary for ``Case.headers``. :issue:`1280`

**Fixed**

- Pass ``data_generation_method`` to ``Case`` for GraphQL schemas.
- Generation of invalid headers in some cases. :issue:`1142`
- Unescaped quotes in generated Python code samples on some schemas. :issue:`1030`

**Performance**

- Dramatically improve CLI startup performance for large API schemas.
- Open API 3: Inline only ``components/schemas`` before passing schemas to ``hypothesis-jsonschema``.
- Generate tests on demand when multiple workers are used during CLI runs. :issue:`1287`

.. _v3.10.1:

:version:`3.10.1 <v3.10.0...v3.10.1>` - 2021-10-04
--------------------------------------------------

**Added**

- ``DataGenerationMethod.all`` shortcut to get all possible enum variants.

**Fixed**

- Unresolvable dependency due to incompatible changes in the new ``hypothesis-jsonschema`` release. :issue:`1290`

.. _v3.10.0:

:version:`3.10.0 <v3.9.7...v3.10.0>` - 2021-09-13
--------------------------------------------------

**Added**

- Optional integration with Schemathesis.io.
- New ``before_init_operation`` hook.
- **INTERNAL**. ``description`` attribute for all parsed parameters inside ``APIOperation``.
- Timeouts when loading external schema components or external examples.

**Changed**

- Pin ``werkzeug`` to ``>=0.16.0``.
- **INTERNAL**. ``OpenAPI20CompositeBody.definition`` type to ``List[OpenAPI20Parameter]``.
- Open API schema loaders now also accept single ``DataGenerationMethod`` instances for the ``data_generation_methods`` argument. :issue:`1260`
- Improve error messages when the loaded API schema is not in JSON or YAML. :issue:`1262`

**Fixed**

- Internal error in ``make_case`` calls for GraphQL schemas.
- ``TypeError`` on ``case.call`` with bytes data on GraphQL schemas.
- Worker threads may not be immediately stopped on SIGINT. :issue:`1066`
- Re-used referenced objects during inlining. Now they are independent.
- Rewrite not resolved remote references to local ones. :issue:`986`
- Stop worker threads on failures with ``exit_first`` enabled. :issue:`1204`
- Properly report all failures when custom checks are passed to ``case.validate_response``.

**Performance**

- Avoid using filters for header values when is not necessary.

.. _v3.9.7:

:version:`3.9.7 <v3.9.6...v3.9.7>` - 2021-07-26
-----------------------------------------------

**Added**

- New ``process_call_kwargs`` CLI hook. :issue:`1233`

**Changed**

- Check non-string response status codes when Open API links are collected. :issue:`1226`

.. _v3.9.6:

:version:`3.9.6 <v3.9.5...v3.9.6>` - 2021-07-15
-----------------------------------------------

**Added**

- New ``before_call`` and ``after_call`` CLI hooks. :issue:`1224`, :issue:`700`

.. _v3.9.5:

:version:`3.9.5 <v3.9.4...v3.9.5>` - 2021-07-14
-----------------------------------------------

**Fixed**

- Preserve non-body parameter types in requests during Open API runtime expression evaluation.

.. _v3.9.4:

:version:`3.9.4 <v3.9.3...v3.9.4>` - 2021-07-09
-----------------------------------------------

**Fixed**

- ``KeyError`` when the ``response_schema_conformance`` check is executed against responses without schema definition. :issue:`1220`
- ``TypeError`` during negative testing on Open API schemas with parameters that have non-default ``style`` value. :issue:`1208`

.. _v3.9.3:

:version:`3.9.3 <v3.9.2...v3.9.3>` - 2021-06-22
-----------------------------------------------

**Added**

- ``ExecutionEvent.is_terminal`` attribute that indicates whether an event is the last one in the stream.

**Fixed**

- When ``EventStream.stop`` is called, the next event always is the last one.

.. _v3.9.2:

:version:`3.9.2 <v3.9.1...v3.9.2>` - 2021-06-16
-----------------------------------------------

**Changed**

- Return ``response`` from ``Case.call_and_validate``.

**Fixed**

- Incorrect deduplication applied to response schema conformance failures that happen to have the same failing validator but different input values. :issue:`907`

.. _v3.9.1:

:version:`3.9.1 <v3.9.0...v3.9.1>` - 2021-06-13
-----------------------------------------------

**Changed**

- ``ExecutionEvent.asdict`` adds the ``event_type`` field which is the event class name.
- Add API schema to the ``Initialized`` event.
- **Internal**: Add ``SerializedCase.cookies``
- Convert all ``FailureContext`` class attributes to instance attributes. For simpler serialization via ``attrs``.

.. _v3.9.0:

:version:`3.9.0 <v3.8.0...v3.9.0>` - 2021-06-07
-----------------------------------------------

**Added**

- GraphQL support in CLI. :issue:`746`
- A way to stop the Schemathesis runner's event stream manually via ``events.stop()`` / ``events.finish()`` methods. :issue:`1202`

**Changed**

- Avoid ``pytest`` warnings when internal Schemathesis classes are in the test module scope.

.. _v3.8.0:

:version:`3.8.0 <v3.7.8...v3.8.0>` - 2021-06-03
-----------------------------------------------

**Added**

- Negative testing. :issue:`65`
- ``Case.data_generation_method`` attribute that provides the information of the underlying data generation method (e.g. positive or negative)

**Changed**

- Raise ``UsageError`` if ``schema.parametrize`` or ``schema.given`` are applied to the same function more than once. :issue:`1194`
- Python values of ``True``, ``False`` and ``None`` are converted to their JSON equivalents when generated for path parameters or query. :issue:`1166`
- Bump ``hypothesis-jsonschema`` version. It allows the end-user to override known string formats.
- Bump ``hypothesis`` version.
- ``APIOperation.make_case`` behavior. If no ``media_type`` is passed along with ``body``, then it tries to infer the proper media type and raises an error if it is not possible. :issue:`1094`

**Fixed**

- Compatibility with ``hypothesis>=6.13.3``.

.. _v3.7.8:

:version:`3.7.8 <v3.7.7...v3.7.8>` - 2021-06-02
-----------------------------------------------

**Fixed**

- Open API ``style`` & ``explode`` for parameters derived from security definitions.

.. _v3.7.7:

:version:`3.7.7 <v3.7.6...v3.7.7>` - 2021-06-01
-----------------------------------------------

**Fixed**

- Apply the Open API's ``style`` & ``explode`` keywords to explicit examples. :issue:`1190`

.. _v3.7.6:

:version:`3.7.6 <v3.7.5...v3.7.6>` - 2021-05-31
-----------------------------------------------

**Fixed**

- Disable filtering optimization for headers when there are keywords other than ``type``. :issue:`1189`

.. _v3.7.5:

:version:`3.7.5 <v3.7.4...v3.7.5>` - 2021-05-31
-----------------------------------------------

**Fixed**

- Too much filtering in headers that have schemas with the ``pattern`` keyword. :issue:`1189`

.. _v3.7.4:

:version:`3.7.4 <v3.7.3...v3.7.4>` - 2021-05-28
-----------------------------------------------

**Changed**

- **Internal**: ``SerializedCase.path_template`` returns path templates as they are in the schema, without base path.

.. _v3.7.3:

:version:`3.7.3 <v3.7.2...v3.7.3>` - 2021-05-28
-----------------------------------------------

**Fixed**

- Invalid multipart payload generated for unusual schemas for the ``multipart/form-data`` media type.

**Performance**

- Reduce the amount of filtering needed to generate valid headers and cookies.

.. _v3.7.2:

:version:`3.7.2 <v3.7.1...v3.7.2>` - 2021-05-27
-----------------------------------------------

**Added**

- ``SerializedCase.media_type`` that stores the information about what media type was used for a particular case.

**Fixed**

- Internal error on unusual schemas for the ``multipart/form-data`` media type. :issue:`1152`
- Ignored explicit ``Content-Type`` override in ``Case.as_requests_kwargs``.

.. _v3.7.1:

:version:`3.7.1 <v3.7.0...v3.7.1>` - 2021-05-23
-----------------------------------------------

**Added**

- **Internal**: ``FailureContext.title`` attribute that gives a short failure description.
- **Internal**: ``FailureContext.message`` attribute that gives a longer failure description.

**Changed**

- Rename ``JSONDecodeErrorContext.message`` to ``JSONDecodeErrorContext.validation_message`` for consistency.
- Store the more precise ``schema`` & ``instance`` in ``ValidationErrorContext``.
- Rename ``ResponseTimeout`` to ``RequestTimeout``.

.. _v3.7.0:

:version:`3.7.0 <v3.6.11...v3.7.0>` - 2021-05-23
------------------------------------------------

**Added**

- Additional context for each failure coming from the runner. It allows the end-user to customize failure formatting.

**Changed**

- Use different exception classes for ``not_a_server_error`` and ``status_code_conformance`` checks. It improves the variance of found errors.
- All network requests (not WSGI) now have the default timeout of 10 seconds. If the response is time-outing, Schemathesis will report it as a failure.
  It also solves the case when the tested app hangs. :issue:`1164`
- The default test duration deadline is extended to 15 seconds.

.. _v3.6.11:

:version:`3.6.11 <v3.6.10...v3.6.11>` - 2021-05-20
--------------------------------------------------

**Added**

- Internal: ``BeforeExecution.verbose_name`` & ``SerializedCase.verbose_name`` that reflect specification-specific API operation name.

.. _v3.6.10:

:version:`3.6.10 <v3.6.9...v3.6.10>` - 2021-05-17
--------------------------------------------------

**Changed**

- Explicitly add ``colorama`` to project's dependencies.
- Bump ``hypothesis-jsonschema`` version.

.. _v3.6.9:

:version:`3.6.9 <v3.6.8...v3.6.9>` - 2021-05-14
-----------------------------------------------

**Fixed**

- Ignored ``$ref`` keyword in schemas with deeply nested references. :issue:`1167`
- Ignored Open API specific keywords & types in schemas with deeply nested references. :issue:`1162`

.. _v3.6.8:

:version:`3.6.8 <v3.6.7...v3.6.8>` - 2021-05-13
-----------------------------------------------

**Changed**

- Relax dependency on ``starlette`` to ``>=0.13,<1``. :issue:`1160`

.. _v3.6.7:

:version:`3.6.7 <v3.6.6...v3.6.7>` - 2021-05-12
-----------------------------------------------

**Fixed**

- Missing support for the ``date`` string format (only ``full-date`` was supported).

.. _v3.6.6:

:version:`3.6.6 <v3.6.5...v3.6.6>` - 2021-05-07
-----------------------------------------------

**Changed**

- Improve error message for failing Hypothesis deadline healthcheck in CLI. :issue:`880`

.. _v3.6.5:

:version:`3.6.5 <v3.6.4...v3.6.5>` - 2021-05-07
-----------------------------------------------

**Added**

- Support for disabling ANSI color escape codes via the `NO_COLOR <https://no-color.org/>` environment variable or the ``--no-color`` CLI option. :issue:`1153`

**Changed**

- Generate valid header values for Bearer auth by construction rather than by filtering.

.. _v3.6.4:

:version:`3.6.4 <v3.6.3...v3.6.4>` - 2021-04-30
-----------------------------------------------

**Changed**

- Bump minimum ``hypothesis-graphql`` version to ``0.5.0``. It brings support for interfaces and unions and fixes a couple of bugs in query generation.

.. _v3.6.3:

:version:`3.6.3 <v3.6.2...v3.6.3>` - 2021-04-20
-----------------------------------------------

**Fixed**

- Bump minimum ``hypothesis-graphql`` version to ``0.4.1``. It fixes `a problem <https://github.com/Stranger6667/hypothesis-graphql/issues/30>`_ with generating queries with surrogate characters.
- ``UnicodeEncodeError`` when sending ``application/octet-stream`` payloads that have no ``format: binary`` in their schemas. :issue:`1134`

.. _v3.6.2:

:version:`3.6.2 <v3.6.1...v3.6.2>` - 2021-04-15
-----------------------------------------------

**Fixed**

- Windows: ``UnicodeDecodeError`` during schema loading via the ``from_path`` loader if it contains certain Unicode symbols.
  ``from_path`` loader defaults to `UTF-8` from now on.

.. _v3.6.1:

:version:`3.6.1 <v3.6.0...v3.6.1>` - 2021-04-09
-----------------------------------------------

**Fixed**

- Using parametrized ``pytest`` fixtures with the ``from_pytest_fixture`` loader. :issue:`1121`

.. _v3.6.0:

:version:`3.6.0 <v3.5.3...v3.6.0>` - 2021-04-04
-----------------------------------------------

**Added**

- Custom keyword arguments to ``schemathesis.graphql.from_url`` that are proxied to ``requests.post``.
- ``from_wsgi``, ``from_asgi``, ``from_path`` and ``from_file`` loaders for GraphQL apps. :issue:`1097`, :issue:`1100`
- Support for ``data_generation_methods`` and ``code_sample_style`` in all GraphQL loaders.
- Support for ``app`` & ``base_url`` arguments for the ``from_pytest_fixture`` runner.
- Initial support for GraphQL schemas in the Schemathesis runner.

.. code-block:: python

    import schemathesis

    # Load schema
    schema = schemathesis.graphql.from_url("http://127.0.0.1:8000/graphql")
    # Initialize runner
    runner = schemathesis.runner.from_schema(schema)
    # Emit events
    for event in runner.execute():
        ...

**Breaking**

- Loaders' signatures are unified. Most of the arguments became keyword-only. All except the first two for ASGI/WSGI, all except the first one for the others.
  It forces loader calls to be more consistent.

.. code-block:: python

    # BEFORE
    schema = schemathesis.from_uri(
        "http://example.com/openapi.json", "http://127.0.0.1:8000/", "GET"
    )
    # NOW
    schema = schemathesis.from_uri(
        "http://example.com/openapi.json", base_url="http://127.0.0.1:8000/", method="GET"
    )

**Changed**

- Schemathesis generates separate tests for each field defined in the GraphQL ``Query`` type. It makes the testing process
  unified for both Open API and GraphQL schemas.
- IDs for GraphQL tests use the corresponding ``Query`` field instead of HTTP method & path.
- Do not show overly verbose raw schemas in Hypothesis output for failed GraphQL tests.
- The ``schemathesis.graphql.from_url`` loader now uses the usual Schemathesis User-Agent.
- The Hypothesis database now uses separate entries for each API operation when executed via CLI. It increases its effectiveness when tests are re-run.
- Module ``schemathesis.loaders`` is moved to ``schemathesis.specs.openapi.loaders``.
- Show a more specific exception on incorrect usage of the ``from_path`` loader in the Schemathesis runner.

**Deprecated**

- ``schemathesis.runner.prepare`` will be removed in Schemathesis 4.0. Use ``schemathesis.runner.from_schema`` instead. With this change, the schema loading part
  goes to your code, similar to using the regular Schemathesis Python API. It leads to a unified user experience where the starting point is API schema loading, which is
  much clearer than passing a callback & keyword arguments to the ``prepare`` function.

**Fixed**

- Add the missing ``@schema.given`` implementation for schemas created via the ``from_pytest_fixture`` loader. :issue:`1093`
- Silently ignoring some incorrect usages of ``@schema.given``.
- Fixups examples were using the incorrect fixup name.
- Return type of ``make_case`` for GraphQL schemas.
- Missed ``operation_id`` argument in ``from_asgi`` loader.

**Removed**

- Undocumented way to install fixups via the ``fixups`` argument for ``schemathesis.runner.prepare`` is removed.

.. _v3.5.3:

:version:`3.5.3 <v3.5.2...v3.5.3>` - 2021-03-27
-----------------------------------------------

**Fixed**

- Do not use `importlib-metadata==3.8` in dependencies as it causes ``RuntimeError``. Ref: https://github.com/python/importlib_metadata/issues/293

.. _v3.5.2:

:version:`3.5.2 <v3.5.1...v3.5.2>` - 2021-03-24
-----------------------------------------------

**Changed**

- Prefix worker thread names with ``schemathesis_``.

.. _v3.5.1:

:version:`3.5.1 <v3.5.0...v3.5.1>` - 2021-03-23
-----------------------------------------------

**Fixed**

- Encoding for response payloads displayed in the CLI output. :issue:`1073`
- Use actual charset (from ``flask.Response.mimetype_params``) when storing WSGI responses rather than defaulting to ``flask.Response.charset``.

.. _v3.5.0:

:version:`3.5.0 <v3.4.1...v3.5.0>` - 2021-03-22
-----------------------------------------------

**Added**

- ``before_generate_case`` hook, that allows the user to modify or filter generated ``Case`` instances. :issue:`1067`

**Fixed**

- Missing ``body`` parameters during Open API links processing in CLI. :issue:`1069`
- Output types for evaluation results of ``$response.body`` and ``$request.body`` runtime expressions. :issue:`1068`

.. _v3.4.1:

:version:`3.4.1 <v3.4.0...v3.4.1>` - 2021-03-21
-----------------------------------------------

**Added**

- ``event_type`` field to the debug output.

.. _v3.4.0:

:version:`3.4.0 <v3.3.1...v3.4.0>` - 2021-03-20
-----------------------------------------------

**Added**

- ``--debug-output-file`` CLI option to enable storing the underlying runner events in the JSON Lines format in a separate file for debugging purposes. :issue:`1059`

**Changed**

- Make ``Request.body``, ``Response.body`` and ``Response.encoding`` internal attributes optional. For ``Request``,
  it means that absent body will lead to ``Request.body`` to be ``None``. For ``Response``, ``body`` will be ``None``
  if the app response did not have any payload. Previously these values were empty strings, which was not distinguishable from the cases described above.
  For the end-user, it means that in VCR cassettes, fields ``request.body`` and ``response.body`` may be absent.
- ``models.Status`` enum now has string values for more readable representation.

.. _v3.3.1:

:version:`3.3.1 <v3.3.0...v3.3.1>` - 2021-03-18
-----------------------------------------------

**Fixed**

- Displaying wrong headers in the ``FAILURES`` block of the CLI output. :issue:`792`

.. _v3.3.0:

:version:`3.3.0 <v3.2.2...v3.3.0>` - 2021-03-17
-----------------------------------------------

**Added**

- Display failing response payload in the CLI output, similarly to the pytest plugin output. :issue:`1050`
- A way to control which code sample style to use - Python or cURL. :issue:`908`

**Fixed**

- ``UnicodeDecodeError`` when generating cURL commands for failed test case reproduction if the request's body contains non-UTF8 characters.

**Internal**

- Extra information to events, emitted by the Schemathesis runner.

.. _v3.2.2:

:version:`3.2.2 <v3.2.1...v3.2.2>` - 2021-03-11
-----------------------------------------------

**Added**

- Support for Hypothesis 6. :issue:`1013`

.. _v3.2.1:

:version:`3.2.1 <v3.2.0...v3.2.1>` - 2021-03-10
-----------------------------------------------

**Fixed**

- Wrong test results in some cases when the tested schema contains a media type that Schemathesis doesn't know how to work with. :issue:`1046`

.. _v3.2.0:

:version:`3.2.0 <v3.1.3...v3.2.0>` - 2021-03-09
-----------------------------------------------

**Performance**

- Add an internal caching layer for data generation strategies. It relies on the fact that the internal ``BaseSchema`` structure is not mutated over time.
  It is not directly possible through the public API and is discouraged from doing through hook functions.

**Changed**

- ``APIOperation`` and subclasses of ``Parameter`` are now compared by their identity rather than by value.

.. _v3.1.3:

:version:`3.1.3 <v3.1.2...v3.1.3>` - 2021-03-08
-----------------------------------------------

**Added**

- ``count_operations`` boolean flag to ``runner.prepare``. In case of ``False`` value, Schemathesis won't count the total number of operations upfront.
  It improves performance for the direct ``runner`` usage, especially on large schemas.
  Schemathesis CLI will still use these calculations to display the progress during execution, but this behavior may become configurable in the future.

.. _v3.1.2:

:version:`3.1.2 <v3.1.1...v3.1.2>` - 2021-03-08
-----------------------------------------------

**Fixed**

- Percent-encode the generated ``.`` and ``..`` strings in path parameters to avoid resolving relative paths and changing the tested path structure. :issue:`1036`

.. _v3.1.1:

:version:`3.1.1 <v3.1.0...v3.1.1>` - 2021-03-05
-----------------------------------------------

**Fixed**

- Loosen ``importlib-metadata`` version constraint and update pyproject.toml :issue:`1039`

.. _v3.1.0:

:version:`3.1.0 <v3.0.9...v3.1.0>` - 2021-02-11
-----------------------------------------------

**Added**

- Support for external examples via the ``externalValue`` keyword. :issue:`884`

**Fixed**

- Prevent a small terminal width causing a crash (due to negative length used in an f-string) when printing percentage
- Support the latest ``cryptography`` version in Docker images. :issue:`1033`

.. _v3.0.9:

:version:`3.0.9 <v3.0.8...v3.0.9>` - 2021-02-10
-----------------------------------------------

**Fixed**

- Return a default terminal size to prevent crashes on systems with zero-width terminals (some CI/CD servers).

.. _v3.0.8:

:version:`3.0.8 <v3.0.7...v3.0.8>` - 2021-02-04
-----------------------------------------------

- This release updates the documentation to be in-line with the current state.

.. _v3.0.7:

:version:`3.0.7 <v3.0.6...v3.0.7>` - 2021-01-31
-----------------------------------------------

**Fixed**

- Docker tags for Buster-based images.

.. _v3.0.6:

:version:`3.0.6 <v3.0.5...v3.0.6>` - 2021-01-31
-----------------------------------------------

- Packaging-only release for Docker images based on Debian Buster. :issue:`1028`

.. _v3.0.5:

:version:`3.0.5 <v3.0.4...v3.0.5>` - 2021-01-30
-----------------------------------------------

**Fixed**

- Allow to use any iterable type for ``checks`` and ``additional_checks`` arguments to ``Case.validate_response``.

.. _v3.0.4:

:version:`3.0.4 <v3.0.3...v3.0.4>` - 2021-01-19
-----------------------------------------------

**Fixed**

- Generating stateful tests, with common parameters behind a reference. :issue:`1020`
- Programmatic addition of Open API links via ``add_link`` when schema validation is disabled and response status codes
  are noted as integers. :issue:`1022`

**Changed**

- When operations are resolved by ``operationId`` then the same reference resolving logic is applied as in other cases.
  This change leads to less reference inlining and lower memory consumption for deeply nested schemas. :issue:`945`

.. _v3.0.3:

:version:`3.0.3 <v3.0.2...v3.0.3>` - 2021-01-18
-----------------------------------------------

**Fixed**

- ``Flaky`` Hypothesis error during explicit examples generation. :issue:`1018`

.. _v3.0.2:

:version:`3.0.2 <v3.0.1...v3.0.2>` - 2021-01-15
-----------------------------------------------

**Fixed**

- Processing parameters common for multiple API operations if they are behind a reference. :issue:`1015`

.. _v3.0.1:

:version:`3.0.1 <v3.0.0...v3.0.1>` - 2021-01-15
-----------------------------------------------

**Added**

- YAML serialization for ``text/yaml``, ``text/x-yaml``, ``application/x-yaml`` and ``text/vnd.yaml`` media types. :issue:`1010`.

.. _v3.0.0:

:version:`3.0.0 <v2.8.6...v3.0.0>` - 2021-01-14
-----------------------------------------------

**Added**

- Support for sending ``text/plain`` payload as test data. Including variants with non-default ``charset``. :issue:`850`, :issue:`939`
- Generating data for all media types defined for an operation. :issue:`690`
- Support for user-defined media types serialization. You can define how Schemathesis should handle media types defined
  in your schema or customize existing (like ``application/json``).
- The `response_schema_conformance` check now runs on media types that are encoded with JSON. For example, ``application/problem+json``. :issue:`920`
- Base URL for GraphQL schemas. It allows you to load the schema from one place but send test requests to another one. :issue:`934`
- A helpful error message when an operation is not found during the direct schema access. :issue:`812`
- ``--dry-run`` CLI option. When applied, Schemathesis won't send any data to the server and won't perform any response checks. :issue:`963`
- A better error message when the API schema contains an invalid regular expression syntax. :issue:`1003`

**Changed**

- Open API parameters parsing to unblock supporting multiple media types per operation. Their definitions aren't converted
  to JSON Schema equivalents right away but deferred instead and stored as-is.
- Missing ``required: true`` in path parameters definition is now automatically enforced if schema validation is disabled.
  According to the Open API spec, the ``required`` keyword value should be ``true`` for path parameters.
  This change allows Schemathesis to generate test cases even for endpoints containing optional path parameters (which is not compliant with the spec). :issue:`941`
- Using ``--auth`` together with ``--header`` that sets the ``Authorization`` header causes a validation error.
  Before, the ``--header`` value was ignored in such cases, and the basic auth passed in ``--auth`` was used. :issue:`911`
- When ``hypothesis-jsonschema`` fails to resolve recursive references, the test is skipped with an error message that indicates why it happens.
- Shorter error messages when API operations have logical errors in their schema. For example, when the maximum is less than the minimum - ``{"type": "integer", "minimum": 5, "maximum": 4}``.
- If multiple non-check related failures happens during a test of a single API operation, they are displayed as is, instead of Hypothesis-level error messages about multiple found failures or flaky tests. :issue:`975`
- Catch schema parsing errors, that are caused by YAML parsing.
- The built-in test server now accepts ``--operations`` instead of ``--endpoints``.
- Display ``Collected API operations`` instead of ``collected endpoints`` in the CLI. :issue:`869`
- ``--skip-deprecated-endpoints`` is renamed to ``--skip-deprecated-operations``. :issue:`869`
- Rename various internal API methods that contained ``endpoint`` in their names. :issue:`869`
- Bump ``hypothesis-jsonschema`` version to ``0.19.0``. This version improves the handling of unsupported regular expression syntax and can generate data for a subset of schemas containing such regular expressions.
- Schemathesis doesn't stop testing on errors during schema parsing. These errors are handled the same way as other errors
  during the testing process. It allows Schemathesis to test API operations with valid definitions and report problematic operations instead of failing the whole run. :issue:`999`

**Fixed**

- Allow generating requests without payload if the schema does not require it. :issue:`916`
- Allow sending ``null`` as request payload if the schema expects it. :issue:`919`
- CLI failure if the tested operation is `GET` and has payload examples. :issue:`925`
- Excessive reference inlining that leads to out-of-memory for large schemas with deep references. :issue:`945`, :issue:`671`
- ``--exitfirst`` CLI option trims the progress bar output when a failure occurs. :issue:`951`
- Internal error if filling missing explicit examples led to ``Unsatisfiable`` errors. :issue:`904`
- Do not suggest to disable schema validation if it is already disabled. :issue:`914`
- Skip explicit examples generation if this phase is disabled via config. :issue:`905`
- ``Unsatisfiable`` error in stateful testing caused by all API operations having inbound links. :issue:`965`, :issue:`822`
- A possibility to override ``APIStateMachine.step``. :issue:`970`
- ``TypeError`` on nullable parameters during Open API specific serialization. :issue:`980`
- Invalid types in ``x-examples``. :issue:`982`
- CLI crash on schemas with operation names longer than the current terminal width. :issue:`990`
- Handling of API operations that contain reserved characters in their paths. :issue:`992`
- CLI execution stops on errors during example generation. :issue:`994`
- Fill missing properties in incomplete explicit examples for non-body parameters. :issue:`1007`

**Deprecated**

- ``HookContext.endpoint``. Use ``HookContext.operation`` instead.
- ``Case.endpoint``. Use ``Case.operation`` instead.

**Performance**

- Use compiled versions of Open API spec validators.
- Decrease CLI memory usage. :issue:`987`
- Various improvements relevant to processing of API operation definitions.
  It gives ~20% improvement on large schemas with many references.

**Removed**

- ``Case.form_data``. Use ``Case.body`` instead.
- ``Endpoint.form_data``. Use ``Endpoint.body`` instead.
- ``before_generate_form_data`` hook. Use ``before_generate_body`` instead.
- Deprecated stateful testing integration from our ``pytest`` plugin.

.. note::

    This release features multiple backward-incompatible changes. The first one is removing ``form_data`` and hooks related to it -
    all payload related actions can be done via ``body`` and its hooks. The second one involves renaming the so-called "endpoint" to "operation".
    The main reason for this is to generalize terminology and make it applicable to GraphQL schemas, as all Schemathesis internals
    are more suited to work with semantically different API operations rather than with endpoints that are often connected with URLs and HTTP methods.
    It brings the possibility to reuse the same concepts for Open API and GraphQL - in the future, unit tests will cover individual API operations
    in GraphQL, rather than everything available under the same "endpoint".

.. _v2.8.6:

:version:`2.8.6 <v2.8.5...v2.8.6>` - 2022-03-29
-----------------------------------------------

**Added**

- Support for Werkzeug>=2.1.0. :issue:`1410`

.. _v2.8.5:

:version:`2.8.5 <v2.8.4...v2.8.5>` - 2020-12-15
-----------------------------------------------

**Added**

- ``auto`` variant for the ``--workers`` CLI option that automatically detects the number of available CPU cores to run tests on. :issue:`917`

.. _v2.8.4:

:version:`2.8.4 <v2.8.3...v2.8.4>` - 2020-11-27
-----------------------------------------------

**Fixed**

- Use ``--request-tls-verify`` during schema loading as well. :issue:`897`

.. _v2.8.3:

:version:`2.8.3 <v2.8.2...v2.8.3>` - 2020-11-27
-----------------------------------------------

**Added**

- Display failed response payload in the error output for the ``pytest`` plugin. :issue:`895`

**Changed**

- In pytest plugin output, Schemathesis error classes use the `CheckFailed` name. Before, they had not readable "internal" names.
- Hypothesis falsifying examples. The code does not include ``Case`` attributes with default values to improve readability. :issue:`886`

.. _v2.8.2:

:version:`2.8.2 <v2.8.1...v2.8.2>` - 2020-11-25
-----------------------------------------------

**Fixed**

- Internal error in CLI, when the ``base_url`` is an invalid IPv6. :issue:`890`
- Internal error in CLI, when a malformed regex is passed to ``-E`` / ``-M`` / ``-T`` / ``-O`` CLI options. :issue:`889`

.. _v2.8.1:

:version:`2.8.1 <v2.8.0...v2.8.1>` - 2020-11-24
-----------------------------------------------

**Added**

- ``--force-schema-version`` CLI option to force Schemathesis to use the specific Open API spec version when parsing the schema. :issue:`876`

**Changed**

- The ``content_type_conformance`` check now raises a well-formed error message when encounters a malformed media type value. :issue:`877`

**Fixed**

- Internal error during verifying explicit examples if an example has no ``value`` key. :issue:`882`

.. _v2.8.0:

:version:`2.8.0 <v2.7.7...v2.8.0>` - 2020-11-24
-----------------------------------------------

**Added**

- ``--request-tls-verify`` CLI option, that controls whether Schemathesis verifies the server's TLS certificate.
  You can also pass the path to a CA_BUNDLE file for private certs. :issue:`830`

**Changed**

- In CLI, if an endpoint contains an invalid schema, show a message about the ``--validate-schema`` CLI option. :issue:`855`

**Fixed**

- Handling of 204 responses in the ``response_schema_conformance`` check. Before, all responses were required to have the
  ``Content-Type`` header. :issue:`844`
- Catch ``OverflowError`` when an invalid regex is passed to ``-E`` / ``-M`` / ``-T`` / ``-O`` CLI options. :issue:`870`
- Internal error in CLI, when the schema location is an invalid IPv6. :issue:`872`
- Collecting Open API links behind references via CLI. :issue:`874`

**Deprecated**

- Using of ``Case.form_data`` and ``Endpoint.form_data``. In the ``3.0`` release, you'll need to use relevant ``body`` attributes instead.
  This change includes deprecation of the ``before_generate_form_data`` hook, use ``before_generate_body`` instead.
  The reason for this is the upcoming unification of parameter handling and their serialization.
- ``--stateful-recursion-limit``. It will be removed in ``3.0`` as a part of removing the old stateful testing approach.
  This parameter is no-op.

.. _v2.7.7:

:version:`2.7.7 <v2.7.6...v2.7.7>` - 2020-11-13
-----------------------------------------------

**Fixed**

- Missed ``headers`` in ``Endpoint.partial_deepcopy``.

.. _v2.7.6:

:version:`2.7.6 <v2.7.5...v2.7.6>` - 2020-11-12
-----------------------------------------------

**Added**

- An option to set data generation methods. At the moment, it includes only "positive", which means that Schemathesis will
  generate data that matches the schema.

**Fixed**

- Pinned dependency on ``attrs`` that caused an error on fresh installations. :issue:`858`

.. _v2.7.5:

:version:`2.7.5 <v2.7.4...v2.7.5>` - 2020-11-09
-----------------------------------------------

**Fixed**

- Invalid keyword in code samples that Schemathesis suggests to run to reproduce errors. :issue:`851`

.. _v2.7.4:

:version:`2.7.4 <v2.7.3...v2.7.4>` - 2020-11-07
-----------------------------------------------

**Added**

- New ``relative_path`` property for ``BeforeExecution`` and ``AfterExecution`` events. It represents an operation
  path as it is in the schema definition.

.. _v2.7.3:

:version:`2.7.3 <v2.7.2...v2.7.3>` - 2020-11-05
-----------------------------------------------

**Fixed**

- Internal error on malformed JSON when the ``response_conformance`` check is used. :issue:`832`

.. _v2.7.2:

:version:`2.7.2 <v2.7.1...v2.7.2>` - 2020-11-05
-----------------------------------------------

**Added**

- Shortcut for response validation when Schemathesis's data generation is not used. :issue:`485`

**Changed**

- Improve the error message when the application can not be loaded from the value passed to the ``--app`` command-line option. :issue:`836`
- Security definitions are now serialized as other parameters. At the moment, it means that the generated values
  will be coerced to strings, which is a no-op. However, types of security definitions might be affected by
  the "Negative testing" feature in the future. Therefore this change is mostly for future-compatibility. :issue:`841`

**Fixed**

- Internal error when a "header" / "cookie" parameter were not coerced to a string before filtration. :issue:`839`

.. _v2.7.1:

:version:`2.7.1 <v2.7.0...v2.7.1>` - 2020-10-22
-----------------------------------------------

**Fixed**

- Adding new Open API links via the ``add_link`` method, when the related PathItem contains a reference. :issue:`824`

.. _v2.7.0:

:version:`2.7.0 <v2.6.1...v2.7.0>` - 2020-10-21
-----------------------------------------------

**Added**

- New approach to stateful testing, based on the Hypothesis's ``RuleBasedStateMachine``. :issue:`737`
- ``Case.validate_response`` accepts the new ``additional_checks`` argument. It provides a way to execute additional checks in addition to existing ones.

**Changed**

- The ``response_schema_conformance`` and ``content_type_conformance`` checks fail unconditionally if the input response has no ``Content-Type`` header. :issue:`816`

**Fixed**

- Failure reproduction code missing values that were explicitly passed to ``call_*`` methods during testing. :issue:`814`

**Deprecated**

- Using ``stateful=Stateful.links`` in schema loaders and ``parametrize``. Use ``schema.as_state_machine().TestCase`` instead.
  The old approach to stateful testing will be removed in ``3.0``.
  See the ``Stateful testing`` section of our documentation for more information.

.. _v2.6.1:

:version:`2.6.1 <v2.6.0...v2.6.1>` - 2020-10-19
-----------------------------------------------

**Added**

- New method ``as_curl_command`` added to the ``Case`` class. :issue:`689`

.. _v2.6.0:

:version:`2.6.0 <v2.5.1...v2.6.0>` - 2020-10-06
-----------------------------------------------

**Added**

- Support for passing Hypothesis strategies to tests created with ``schema.parametrize`` by using ``schema.given`` decorator. :issue:`768`
- Support for PEP561. :issue:`748`
- Shortcut for calling & validation. :issue:`738`
- New hook to pre-commit, ``rstcheck``, as well as updates to documentation based on rstcheck. :issue:`734`
- New check for maximum response time and corresponding CLI option ``--max-response-time``. :issue:`716`
- New ``response_headers_conformance`` check that verifies the presence of all headers defined for a response. :issue:`742`
- New field with information about executed checks in cassettes. :issue:`702`
- New ``port`` parameter added to ``from_uri()`` method. :issue:`706`
- A code snippet to reproduce a failed check when running Python tests. :issue:`793`
- Python 3.9 support. :issue:`731`
- Ability to skip deprecated endpoints with ``--skip-deprecated-endpoints`` CLI option and ``skip_deprecated_operations=True`` argument to schema loaders. :issue:`715`

**Fixed**

- ``User-Agent`` header overriding the passed one. :issue:`757`
- Default ``User-Agent`` header in ``Case.call``. :issue:`717`
- Status of individual interactions in VCR cassettes. Before this change, all statuses were taken from the overall test outcome,
  rather than from the check results for a particular response. :issue:`695`
- Escaping header values in VCR cassettes. :issue:`783`
- Escaping HTTP response message in VCR cassettes. :issue:`788`

**Changed**

- ``Case.as_requests_kwargs`` and ``Case.as_werkzeug_kwargs`` now return the ``User-Agent`` header.
  This change also affects code snippets for failure reproduction - all snippets will include the ``User-Agent`` header.

**Performance**

- Speed up generation of ``headers``, ``cookies``, and ``formData`` parameters when their schemas do not define the ``type`` keyword. :issue:`795`

.. _v2.5.1:

:version:`2.5.1 <v2.5.0...v2.5.1>` - 2020-09-30
-----------------------------------------------

This release contains only documentation updates which are necessary to upload to PyPI.

.. _v2.5.0:

:version:`2.5.0 <v2.4.1...v2.5.0>` - 2020-09-27
-----------------------------------------------

**Added**

- Stateful testing via Open API links for the ``pytest`` runner. :issue:`616`
- Support for GraphQL tests for the ``pytest`` runner. :issue:`649`

**Fixed**

- Progress percentage in the terminal output for "lazy" schemas. :issue:`636`

**Changed**

- Check name is no longer displayed in the CLI output, since its verbose message is already displayed. This change
  also simplifies the internal structure of the runner events.
- The ``stateful`` argument type in the ``runner.prepare`` is ``Optional[Stateful]`` instead of ``Optional[str]``. Use
  ``schemathesis.Stateful`` enum.

.. _v2.4.1:

:version:`2.4.1 <v2.4.0...v2.4.1>` - 2020-09-17
-----------------------------------------------

**Changed**

- Hide ``Case.endpoint`` from representation. Its representation decreases the usability of the pytest's output. :issue:`719`
- Return registered functions from ``register_target`` and ``register_check`` decorators. :issue:`721`

**Fixed**

- Possible ``IndexError`` when a user-defined check raises an exception without a message. :issue:`718`

.. _v2.4.0:

:version:`2.4.0 <v2.3.4...v2.4.0>` - 2020-09-15
-----------------------------------------------

**Added**

- Ability to register custom targets for targeted testing. :issue:`686`

**Changed**

- The ``AfterExecution`` event now has ``path`` and ``method`` fields, similar to the ``BeforeExecution`` one.
  The goal is to make these events self-contained, which improves their usability.

.. _v2.3.4:

:version:`2.3.4 <v2.3.3...v2.3.4>` - 2020-09-11
-----------------------------------------------

**Changed**

- The default Hypothesis's ``deadline`` setting for tests with ``schema.parametrize`` is set to 500 ms for consistency with the CLI behavior. :issue:`705`

**Fixed**

- Encoding error when writing a cassette on Windows. :issue:`708`

.. _v2.3.3:

:version:`2.3.3 <v2.3.2...v2.3.3>` - 2020-08-04
-----------------------------------------------

**Fixed**

- ``KeyError`` during the ``content_type_conformance`` check if the response has no ``Content-Type`` header. :issue:`692`

.. _v2.3.2:

:version:`2.3.2 <v2.3.1...v2.3.2>` - 2020-08-04
-----------------------------------------------

**Added**

- Run checks conditionally.

.. _v2.3.1:

:version:`2.3.1 <v2.3.0...v2.3.1>` - 2020-07-28
-----------------------------------------------

**Fixed**

- ``IndexError`` when ``examples`` list is empty.

.. _v2.3.0:

:version:`2.3.0 <v2.2.1...v2.3.0>` - 2020-07-26
-----------------------------------------------

**Added**

- Possibility to generate values for ``in: formData`` parameters that are non-bytes or contain non-bytes (e.g., inside an array). :issue:`665`

**Changed**

- Error message for cases when a path parameter is in the template but is not defined in the parameters list or missing ``required: true`` in its definition. :issue:`667`
- Bump minimum required ``hypothesis-jsonschema`` version to `0.17.0`. This allows Schemathesis to use the ``custom_formats`` argument in ``from_schema`` calls and avoid using its private API. :issue:`684`

**Fixed**

- ``ValueError`` during sending a request with test payload if the endpoint defines a parameter with ``type: array`` and ``in: formData``. :issue:`661`
- ``KeyError`` while processing a schema with nullable parameters and ``in: body``. :issue:`660`
- ``StopIteration`` during ``requestBody`` processing if it has empty "content" value. :issue:`673`
- ``AttributeError`` during generation of "multipart/form-data" parameters that have no "type" defined. :issue:`675`
- Support for properties named "$ref" in object schemas. Previously, it was causing ``TypeError``. :issue:`672`
- Generating illegal Unicode surrogates in the path. :issue:`668`
- Invalid development dependency on ``graphql-server-core`` package. :issue:`658`

.. _v2.2.1:

:version:`2.2.1 <v2.2.0...v2.2.1>` - 2020-07-22
-----------------------------------------------

**Fixed**

- Possible ``UnicodeEncodeError`` during generation of ``Authorization`` header values for endpoints with ``basic`` security scheme. :issue:`656`

.. _v2.2.0:

:version:`2.2.0 <v2.1.0...v2.2.0>` - 2020-07-14
-----------------------------------------------

**Added**

- ``schemathesis.graphql.from_dict`` loader allows you to use GraphQL schemas represented as a dictionary for testing.
- ``before_load_schema`` hook for GraphQL schemas.

**Fixed**

- Serialization of non-string parameters. :issue:`651`

.. _v2.1.0:

:version:`2.1.0 <v2.0.0...v2.1.0>` - 2020-07-06
-----------------------------------------------

**Added**

- Support for property-level examples. :issue:`467`

**Fixed**

- Content-type conformance check for cases when Open API 3.0 schemas contain "default" response definitions. :issue:`641`
- Handling of multipart requests for Open API 3.0 schemas. :issue:`640`
- Sending non-file form fields in multipart requests. :issue:`647`

**Removed**

- Deprecated ``skip_validation`` argument to ``HookDispatcher.apply``.
- Deprecated ``_accepts_context`` internal function.

.. _v2.0.0:

:version:`2.0.0 <v1.10.0...v2.0.0>` - 2020-07-01
------------------------------------------------

**Changed**

- **BREAKING**. Base URL handling. ``base_url`` now is treated as one with a base path included.
  You should pass a full base URL now instead:

.. code:: bash

    schemathesis run --base-url=http://127.0.0.1:8080/api/v2 ...

This value will override ``basePath`` / ``servers[0].url`` defined in your schema if you use
Open API 2.0 / 3.0 respectively. Previously if you pass a base URL like the one above, it
was concatenated with the base path defined in the schema, which leads to a lack of ability
to redefine the base path. :issue:`511`

**Fixed**

- Show the correct URL in CLI progress when the base URL is overridden, including the path part. :issue:`511`
- Construct valid URL when overriding base URL with base path. :issue:`511`

**Example**:

.. code:: bash

    Base URL in the schema         : http://0.0.0.0:8081/api/v1
    `--base-url` value in CLI      : http://0.0.0.0:8081/api/v2
    Full URLs before this change   : http://0.0.0.0:8081/api/v2/api/v1/users/  # INVALID!
    Full URLs after this change    : http://0.0.0.0:8081/api/v2/users/         # VALID!

**Removed**

- Support for hooks without `context` argument in the first position.
- Hooks registration by name and function. Use ``register`` decorators instead. For more details, see the "Customization" section in our documentation.
- ``BaseSchema.with_hook`` and ``BaseSchema.register_hook``. Use ``BaseSchema.hooks.apply`` and ``BaseSchema.hooks.register`` instead.

.. _v1.10.0:

:version:`1.10.0 <v1.9.1...v1.10.0>` - 2020-06-28
--------------------------------------------------

**Added**

- ``loaders.from_asgi`` supports making calls to ASGI-compliant application (For example: FastAPI). :issue:`521`
- Support for GraphQL strategies.

**Fixed**

- Passing custom headers to schema loader for WSGI / ASGI apps. :issue:`631`

.. _v1.9.1:

:version:`1.9.1 <v1.9.0...v1.9.1>` - 2020-06-21
-----------------------------------------------

**Fixed**

- Schema validation error on schemas containing numeric values in scientific notation without a dot. :issue:`629`

.. _v1.9.0:

:version:`1.9.0 <v1.8.0...v1.9.0>` - 2020-06-20
-----------------------------------------------

**Added**

- Pass the original case's response to the ``add_case`` hook.
- Support for multiple examples with OpenAPI ``examples``. :issue:`589`
- ``--verbosity`` CLI option to minimize the error output. :issue:`598`
- Allow registering function-level hooks without passing their name as the first argument to ``apply``. :issue:`618`
- Support for hook usage via ``LazySchema`` / ``from_pytest_fixture``. :issue:`617`

**Changed**

- Tests with invalid schemas marked as errors, instead of failures. :issue:`622`

**Fixed**

- Crash during the generation of loosely-defined headers. :issue:`621`
- Show exception information for test runs on invalid schemas with ``--validate-schema=false`` command-line option.
  Before, the output sections for invalid endpoints were empty. :issue:`622`

.. _v1.8.0:

:version:`1.8.0 <v1.7.0...v1.8.0>` - 2020-06-15
-----------------------------------------------

**Fixed**

- Tests with invalid schemas are marked as failed instead of passed when ``hypothesis-jsonschema>=0.16`` is installed. :issue:`614`
- ``KeyError`` during creating an endpoint strategy if it contains a reference. :issue:`612`

**Changed**

- Require ``hypothesis-jsonschema>=0.16``. :issue:`614`
- Pass original ``InvalidSchema`` text to ``pytest.fail`` call.

.. _v1.7.0:

:version:`1.7.0 <v1.6.3...v1.7.0>` - 2020-05-30
-----------------------------------------------

**Added**

- Support for YAML files in references via HTTPS & HTTP schemas. :issue:`600`
- Stateful testing support via ``Open API links`` syntax. :issue:`548`
- New ``add_case`` hook. :issue:`458`
- Support for parameter serialization formats in Open API 2 / 3. For example ``pipeDelimited`` or ``deepObject``. :issue:`599`
- Support serializing parameters with ``application/json`` content-type. :issue:`594`

**Changed**

- The minimum required versions for ``Hypothesis`` and ``hypothesis-jsonschema`` are ``5.15.0`` and ``0.11.1`` respectively.
  The main reason is `this fix <https://github.com/HypothesisWorks/hypothesis/commit/4c7f3fbc55b294f13a503b2d2af0d3221fd37938>`_ that is
  required for stability of Open API links feature when it is executed in multiple threads.

.. _v1.6.3:

:version:`1.6.3 <v1.6.2...v1.6.3>` - 2020-05-26
-----------------------------------------------

**Fixed**

- Support for a colon symbol (``:``) inside of a header value passed via CLI. :issue:`596`

.. _v1.6.2:

:version:`1.6.2 <v1.6.1...v1.6.2>` - 2020-05-15
-----------------------------------------------

**Fixed**

- Partially generated explicit examples are always valid and can be used in requests. :issue:`582`

.. _v1.6.1:

:version:`1.6.1 <v1.6.0...v1.6.1>` - 2020-05-13
-----------------------------------------------

**Changed**

- Look at the current working directory when loading hooks for CLI. :issue:`586`

.. _v1.6.0:

:version:`1.6.0 <v1.5.1...v1.6.0>` - 2020-05-10
-----------------------------------------------

**Added**

- New ``before_add_examples`` hook. :issue:`571`
- New ``after_init_cli_run_handlers`` hook. :issue:`575`

**Fixed**

- Passing ``workers_num`` to ``ThreadPoolRunner`` leads to always using 2 workers in this worker kind. :issue:`579`

.. _v1.5.1:

:version:`1.5.1 <v1.5.0...v1.5.1>` - 2020-05-08
-----------------------------------------------

**Fixed**

- Display proper headers in reproduction code when headers are overridden. :issue:`566`

.. _v1.5.0:

:version:`1.5.0 <v1.4.0...v1.5.0>` - 2020-05-06
-----------------------------------------------

**Added**

- Display a suggestion to disable schema validation on schema loading errors in CLI. :issue:`531`
- Filtration of endpoints by ``operationId`` via ``operation_id`` parameter to ``schema.parametrize`` or ``-O`` command-line option. :issue:`546`
- Generation of security-related parameters. They are taken from ``securityDefinitions`` / ``securitySchemes`` and injected
  to the generated data. It supports generating API keys in headers or query parameters and generating data for HTTP
  authentication schemes. :issue:`540`

**Fixed**

- Overriding header values in CLI and runner when headers provided explicitly clash with ones defined in the schema. :issue:`559`
- Nested references resolving in ``response_schema_conformance`` check. :issue:`562`
- Nullable parameters handling when they are behind a reference. :issue:`542`

.. _v1.4.0:

:version:`1.4.0 <v1.3.4...v1.4.0>` - 2020-05-03
-----------------------------------------------

**Added**

- ``context`` argument for hook functions to provide an additional context for hooks. A deprecation warning is emitted
  for hook functions that do not accept this argument.
- A new hook system that allows generic hook dispatching. It comes with new hook locations. For more details, see the "Customization" section in our documentation.
- New ``before_process_path`` hook.
- Third-party compatibility fixups mechanism. Currently, there is one fixup for `FastAPI <https://github.com/tiangolo/fastapi>`_. :issue:`503`

Deprecated


- Hook functions that do not accept ``context`` as their first argument. They will become not be supported in Schemathesis 2.0.
- Registering hooks by name and function. Use ``register`` decorators instead. For more details, see the "Customization" section in our documentation.
- ``BaseSchema.with_hook`` and ``BaseSchema.register_hook``. Use ``BaseSchema.hooks.apply`` and ``BaseSchema.hooks.register`` instead.

**Fixed**

- Add missing ``validate_schema`` argument to ``loaders.from_pytest_fixture``.
- Reference resolving during response schema conformance check. :issue:`539`

.. _v1.3.4:

:version:`1.3.4 <v1.3.3...v1.3.4>` - 2020-04-30
-----------------------------------------------

**Fixed**

- Validation of nullable properties in ``response_schema_conformance`` check introduced in ``1.3.0``. :issue:`542`

.. _v1.3.3:

:version:`1.3.3 <v1.3.2...v1.3.3>` - 2020-04-29
-----------------------------------------------

**Changed**

- Update ``pytest-subtests`` pin to ``>=0.2.1,<1.0``. :issue:`537`

.. _v1.3.2:

:version:`1.3.2 <v1.3.1...v1.3.2>` - 2020-04-27
-----------------------------------------------

**Added**

- Show exceptions if they happened during loading a WSGI application. Option ``--show-errors-tracebacks`` will display a
  full traceback.

.. _v1.3.1:

:version:`1.3.1 <v1.3.0...v1.3.1>` - 2020-04-27
-----------------------------------------------

**Fixed**

- Packaging issue

.. _v1.3.0:

:version:`1.3.0 <v1.2.0...v1.3.0>` - 2020-04-27
-----------------------------------------------

**Added**

- Storing network logs with ``--store-network-log=<filename.yaml>``.
  The stored cassettes are based on the `VCR format <https://relishapp.com/vcr/vcr/v/5-1-0/docs/cassettes/cassette-format>`_
  and contain extra information from the Schemathesis internals. :issue:`379`
- Replaying of cassettes stored in VCR format. :issue:`519`
- Targeted property-based testing in CLI and runner. It only supports the ``response_time`` target at the moment. :issue:`104`
- Export CLI test results to JUnit.xml with ``--junit-xml=<filename.xml>``. :issue:`427`

**Fixed**

- Code samples for schemas where ``body`` is defined as ``{"type": "string"}``. :issue:`521`
- Showing error causes on internal ``jsonschema`` errors during input schema validation. :issue:`513`
- Recursion error in ``response_schema_conformance`` check. Because of this change, ``Endpoint.definition`` contains a definition where references are not resolved. In this way, it makes it possible to avoid recursion errors in ``jsonschema`` validation. :issue:`468`

**Changed**

- Added indentation & section name to the ``SUMMARY`` CLI block.
- Use C-extension for YAML loading when it is possible. It can cause more than 10x speedup on schema parsing.
  Do not show Click's "Aborted!" message when an error occurs during CLI schema loading.
- Add a help message to the CLI output when an internal exception happens. :issue:`529`

.. _v1.2.0:

:version:`1.2.0 <v1.1.2...v1.2.0>` - 2020-04-15
-----------------------------------------------

**Added**

- Per-test hooks for modification of data generation strategies. :issue:`492`
- Support for ``x-example`` vendor extension in Open API 2.0. :issue:`504`
- Sanity validation for the input schema & loader in ``runner.prepare``. :issue:`499`

.. _v1.1.2:

:version:`1.1.2 <v1.1.1...v1.1.2>` - 2020-04-14
-----------------------------------------------

**Fixed**

- Support for custom loaders in ``runner``. Now all built-in loaders are supported as an argument to ``runner.prepare``. :issue:`496`
- ``from_wsgi`` loader accepts custom keyword arguments that will be passed to ``client.get`` when accessing the schema. :issue:`497`

.. _v1.1.1:

:version:`1.1.1 <v1.1.0...v1.1.1>` - 2020-04-12
-----------------------------------------------

**Fixed**

- Mistakenly applied Open API -> JSON Schema Draft 7 conversion. It should be Draft 4. :issue:`489`
- Using wrong validator in ``response_schema_conformance`` check. It should be Draft 4 validator. :issue:`468`

.. _v1.1.0:

:version:`1.1.0 <v1.0.5...v1.1.0>` - 2020-04-08
-----------------------------------------------

**Fixed**

- Response schema check for recursive schemas. :issue:`468`

**Changed**

- App loading in ``runner``. Now it accepts application as an importable string, rather than an instance. It is done to make it possible to execute a runner in a subprocess. Otherwise, apps can't be easily serialized and transferred into another process.
- Runner events structure. All data in events is static from now. There are no references to ``BaseSchema``, ``Endpoint`` or similar objects that may calculate data dynamically. This is done to make events serializable and not tied to Python object, which decouples any ``runner`` consumer from implementation details. It will help make ``runner`` usable in more cases (e.g., web application) since events can be serialized to JSON and used in any environment.
  Another related change is that Python exceptions are not propagated anymore - they are replaced with the ``InternalError`` event that should be handled accordingly.

.. _v1.0.5:

:version:`1.0.5 <v1.0.4...v1.0.5>` - 2020-04-03
-----------------------------------------------

**Fixed**

- Open API 3. Handling of endpoints that contain ``multipart/form-data`` media types.
  Previously only file upload endpoints were working correctly. :issue:`473`

.. _v1.0.4:

:version:`1.0.4 <v1.0.3...v1.0.4>` - 2020-04-03
-----------------------------------------------

**Fixed**

- ``OpenApi30.get_content_types`` behavior, introduced in `8aeee1a <https://github.com/schemathesis/schemathesis/commit/8aeee1ab2c6c97d94272dde4790f5efac3951aed>`_. :issue:`469`

.. _v1.0.3:

:version:`1.0.3 <v1.0.2...v1.0.3>` - 2020-04-03
-----------------------------------------------

**Fixed**

- Precedence of ``produces`` keywords for Swagger 2.0 schemas. Now, operation-level ``produces`` overrides schema-level ``produces`` as specified in the specification. :issue:`463`
- Content-type conformance check for Open API 3.0 schemas. :issue:`461`
- Pytest 5.4 warning for test functions without parametrization. :issue:`451`

.. _v1.0.2:

:version:`1.0.2 <v1.0.1...v1.0.2>` - 2020-04-02
-----------------------------------------------

**Fixed**

- Handling of fields in ``paths`` that are not operations, but allowed by the Open API spec. :issue:`457`
- Pytest 5.4 warning about deprecated ``Node`` initialization usage. :issue:`451`

.. _v1.0.1:

:version:`1.0.1 <v1.0.0...v1.0.1>` - 2020-04-01
-----------------------------------------------

**Fixed**

- Processing of explicit examples in Open API 3.0 when there are multiple parameters in the same location (e.g. ``path``)
  contain ``example`` value. They are properly combined now. :issue:`450`

.. _v1.0.0:

:version:`1.0.0 <v0.28.0...v1.0.0>` - 2020-03-31
------------------------------------------------

**Changed**

- Move processing of ``runner`` parameters to ``runner.prepare``. This change will provide better code reuse since all users of ``runner`` (e.g., if you extended it in your project) need some kind of input parameters handling, which was implemented only in Schemathesis CLI. It is not backward-compatible. If you didn't use ``runner`` directly, then this change should not have a visible effect on your use-case.

.. _v0.28.0:

:version:`0.28.0 <v0.27.0...v0.28.0>` - 2020-03-31
--------------------------------------------------

**Fixed**

- Handling of schemas that use ``x-*`` custom properties. :issue:`448`

**Removed**

- Deprecated ``runner.execute``. Use ``runner.prepare`` instead.

.. _v0.27.0:

:version:`0.27.0 <v0.26.1...v0.27.0>` - 2020-03-31
--------------------------------------------------

Deprecated

- ``runner.execute`` should not be used, since ``runner.prepare`` provides a more flexible interface to test execution.

**Removed**

- Deprecated ``Parametrizer`` class. Use ``schemathesis.from_path`` as a replacement for ``Parametrizer.from_path``.

.. _v0.26.1:

:version:`0.26.1 <v0.26.0...v0.26.1>` - 2020-03-24
--------------------------------------------------

**Fixed**

- Limit recursion depth while resolving JSON schema to handle recursion without breaking. :issue:`435`

.. _v0.26.0:

:version:`0.26.0 <v0.25.1...v0.26.0>` - 2020-03-19
--------------------------------------------------

**Fixed**

- Filter problematic path template variables containing ``"/"``, or ``"%2F"`` url encoded. :issue:`440`
- Filter invalid empty ``""`` path template variables. :issue:`439`
- Typo in a help message in the CLI output. :issue:`436`

.. _v0.25.1:

:version:`0.25.1 <v0.25.0...v0.25.1>` - 2020-03-09
--------------------------------------------------

**Changed**

- Allow ``werkzeug`` >= 1.0.0. :issue:`433`

.. _v0.25.0:

:version:`0.25.0 <v0.24.5...v0.25.0>` - 2020-02-27
--------------------------------------------------

**Changed**

- Handling of explicit examples from schemas. Now, if there are examples for multiple locations
  (e.g., for body and query) then they will be combined into a single example. :issue:`424`

.. _v0.24.5:

:version:`0.24.5 <v0.24.4...v0.24.5>` - 2020-02-26
--------------------------------------------------

**Fixed**

- Error during ``pytest`` collection on objects with custom ``__getattr__`` method and therefore pass ``is_schemathesis`` check. :issue:`429`

.. _v0.24.4:

:version:`0.24.4 <v0.24.3...v0.24.4>` - 2020-02-22
--------------------------------------------------

**Fixed**

- Resolving references when the schema is loaded from a file on Windows. :issue:`418`

.. _v0.24.3:

:version:`0.24.3 <v0.24.2...v0.24.3>` - 2020-02-10
--------------------------------------------------

**Fixed**

- Not copied ``validate_schema`` parameter in ``BaseSchema.parametrize``. Regression after implementing :issue:`383`
- Missing ``app``, ``location`` and ``hooks`` parameters in schema when used with ``BaseSchema.parametrize``. :issue:`416`

.. _v0.24.2:

:version:`0.24.2 <v0.24.1...v0.24.2>` - 2020-02-09
--------------------------------------------------

**Fixed**

- Crash on invalid regular expressions in ``method``, ``endpoint`` and ``tag`` CLI options. :issue:`403`
- Crash on a non-latin-1 encodable value in the ``auth`` CLI option. :issue:`404`
- Crash on an invalid value in the ``header`` CLI option. :issue:`405`
- Crash on some invalid URLs in the ``schema`` CLI option. :issue:`406`
- Validation of ``--request-timeout`` parameter. :issue:`407`
- Crash with ``--hypothesis-deadline=0`` CLI option. :issue:`410`
- Crash with ``--hypothesis-max-examples=0`` CLI option. :issue:`412`

.. _v0.24.1:

:version:`0.24.1 <v0.24.0...v0.24.1>` - 2020-02-08
--------------------------------------------------

**Fixed**

- CLI crash on Windows and Python < 3.8 when the schema path contains characters unrepresentable at the OS level. :issue:`400`

.. _v0.24.0:

:version:`0.24.0 <v0.23.7...v0.24.0>` - 2020-02-07
--------------------------------------------------

**Added**

- Support for testing of examples in Parameter & Media Type objects in Open API 3.0. :issue:`394`
- ``--show-error-tracebacks`` CLI option to display errors' tracebacks in the output. :issue:`391`
- Support for schema behind auth. :issue:`115`

**Changed**

- Schemas with GET endpoints accepting body are allowed now if schema validation is disabled (via ``--validate-schema=false`` for example).
  The use-case is for tools like ElasticSearch that use GET requests with non-empty bodies. :issue:`383`

**Fixed**

- CLI crash when an explicit example is specified in the endpoint definition. :issue:`386`

.. _v0.23.7:

:version:`0.23.7 <v0.23.6...v0.23.7>` - 2020-01-30
--------------------------------------------------

**Added**

- ``-x``/``--exitfirst`` CLI option to exit after the first failed test. :issue:`378`

**Fixed**

- Handling examples of parameters in Open API 3.0. :issue:`381`

.. _v0.23.6:

:version:`0.23.6 <v0.23.5...v0.23.6>` - 2020-01-28
--------------------------------------------------

**Added**

- ``all`` variant for ``--checks`` CLI option to use all available checks. :issue:`374`

**Changed**

- Use built-in ``importlib.metadata`` on Python 3.8. :issue:`376`

.. _v0.23.5:

:version:`0.23.5 <v0.23.4...v0.23.5>` - 2020-01-24
--------------------------------------------------

**Fixed**

- Generation of invalid values in ``Case.cookies``. :issue:`371`

.. _v0.23.4:

:version:`0.23.4 <v0.23.3...v0.23.4>` - 2020-01-22
--------------------------------------------------

**Fixed**

- Converting ``exclusiveMinimum`` & ``exclusiveMaximum`` fields to JSON Schema. :issue:`367`

.. _v0.23.3:

:version:`0.23.3 <v0.23.2...v0.23.3>` - 2020-01-21
--------------------------------------------------

**Fixed**

- Filter out surrogate pairs from the query string.

.. _v0.23.2:

:version:`0.23.2 <v0.23.1...v0.23.2>` - 2020-01-16
--------------------------------------------------

**Fixed**

- Prevent ``KeyError`` when the response does not have the "Content-Type" header. :issue:`365`

.. _v0.23.1:

:version:`0.23.1 <v0.23.0...v0.23.1>` - 2020-01-15
--------------------------------------------------

**Fixed**

- Dockerfile entrypoint was not working as per docs. :issue:`361`

.. _v0.23.0:

:version:`0.23.0 <v0.22.0...v0.23.0>` - 2020-01-15
--------------------------------------------------

**Added**

- Hooks for strategy modification. :issue:`313`
- Input schema validation. Use ``--validate-schema=false`` to disable it in CLI and ``validate_schema=False`` argument in loaders. :issue:`110`

.. _v0.22.0:

:version:`0.22.0 <v0.21.0...v0.22.0>` - 2020-01-11
--------------------------------------------------

**Added**

- Show multiple found failures in the CLI output. :issue:`266` & :issue:`207`
- Raise a proper exception when the given schema is invalid. :issue:`308`
- Support for ``None`` as a value for ``--hypothesis-deadline``. :issue:`349`

**Fixed**

- Handling binary request payloads in ``Case.call``. :issue:`350`
- Type of the second argument to all built-in checks set to proper ``Case`` instead of ``TestResult``.
  The error was didn't affect built-in checks since both ``Case`` and ``TestResult`` had ``endpoint`` attribute, and only it was used. However, this fix is not backward-compatible with 3rd party checks.

.. _v0.21.0:

:version:`0.21.0 <v0.20.5...v0.21.0>` - 2019-12-20
--------------------------------------------------

**Added**

- Support for AioHTTP applications in CLI. :issue:`329`

.. _v0.20.5:

:version:`0.20.5 <v0.20.4...v0.20.5>` - 2019-12-18
--------------------------------------------------

**Fixed**

- Compatibility with the latest release of ``hypothesis-jsonschema`` and setting its minimal required version to ``0.9.13``. :issue:`338`

.. _v0.20.4:

:version:`0.20.4 <v0.20.3...v0.20.4>` - 2019-12-17
--------------------------------------------------

**Fixed**

- Handling ``nullable`` attribute in Open API schemas. :issue:`335`

.. _v0.20.3:

:version:`0.20.3 <v0.20.2...v0.20.3>` - 2019-12-17
--------------------------------------------------

**Fixed**

- Usage of the response status code conformance check with old ``requests`` version. :issue:`330`

.. _v0.20.2:

:version:`0.20.2 <v0.20.1...v0.20.2>` - 2019-12-14
--------------------------------------------------

**Fixed**

- Response schema conformance check for Open API 3.0. :issue:`332`

.. _v0.20.1:

:version:`0.20.1 <v0.20.0...v0.20.1>` - 2019-12-13
--------------------------------------------------

**Added**

- Support for response code ranges. :issue:`330`

.. _v0.20.0:

:version:`0.20.0 <v0.19.1...v0.20.0>` - 2019-12-12
--------------------------------------------------

**Added**

- WSGI apps support. :issue:`31`
- ``Case.validate_response`` for running built-in checks against app's response. :issue:`319`

**Changed**

- Checks receive ``Case`` instance as a second argument instead of ``TestResult``.
  This was done for making checks usable in Python tests via ``Case.validate_response``.
  Endpoint and schema are accessible via ``case.endpoint`` and ``case.endpoint.schema``.

.. _v0.19.1:

:version:`0.19.1 <v0.19.0...v0.19.1>` - 2019-12-11
--------------------------------------------------

**Fixed**

- Compatibility with Hypothesis >= 4.53.2. :issue:`322`

.. _v0.19.0:

:version:`0.19.0 <v0.18.1...v0.19.0>` - 2019-12-02
--------------------------------------------------

**Added**

- Concurrent test execution in CLI / runner. :issue:`91`
- update importlib_metadata pin to ``^1.1``. :issue:`315`

.. _v0.18.1:

:version:`0.18.1 <v0.18.0...v0.18.1>` - 2019-11-28
--------------------------------------------------

**Fixed**

- Validation of the ``base-url`` CLI parameter. :issue:`311`

.. _v0.18.0:

:version:`0.18.0 <v0.17.0...v0.18.0>` - 2019-11-27
--------------------------------------------------

**Added**

- Resolving references in ``PathItem`` objects. :issue:`301`

**Fixed**

- Resolving of relative paths in schemas. :issue:`303`
- Loading string dates as ``datetime.date`` objects in YAML loader. :issue:`305`

.. _v0.17.0:

:version:`0.17.0 <v0.16.0...v0.17.0>` - 2019-11-21
--------------------------------------------------

**Added**

- Resolving references that point to different files. :issue:`294`

**Changed**

- Keyboard interrupt is now handled during the CLI run, and the summary is displayed in the output. :issue:`295`

.. _v0.16.0:

:version:`0.16.0 <v0.15.0...v0.16.0>` - 2019-11-19
--------------------------------------------------

**Added**

- Display RNG seed in the CLI output to allow test reproducing. :issue:`267`
- Allow specifying seed in CLI.
- Ability to pass custom kwargs to the ``requests.get`` call in ``loaders.from_uri``.

**Changed**

- Refactor case generation strategies: strategy is not used to generate empty value. :issue:`253`
- Improved error message for invalid path parameter declaration. :issue:`255`

**Fixed**

- Pytest fixture parametrization via ``pytest_generate_tests``. :issue:`280`
- Support for tests defined as methods. :issue:`282`
- Unclosed ``requests.Session`` on calling ``Case.call`` without passing a session explicitly. :issue:`286`

.. _v0.15.0:

:version:`0.15.0 <v0.14.0...v0.15.0>` - 2019-11-15
--------------------------------------------------

**Added**

- Support for OpenAPI 3.0 server variables (base_path). :issue:`40`
- Support for ``format: byte``. :issue:`254`
- Response schema conformance check in CLI / Runner. :issue:`256`
- Docker image for CLI. :issue:`268`
- Pre-run hooks for CLI. :issue:`147`
- A way to register custom checks for CLI via ``schemathesis.register_check``. :issue:`270`

**Fixed**

- Not encoded path parameters. :issue:`272`

**Changed**

- Verbose messages are displayed in the CLI on failed checks. :issue:`261`

.. _v0.14.0:

:version:`0.14.0 <v0.13.2...v0.14.0>` - 2019-11-09
--------------------------------------------------

**Added**

- CLI: Support file paths in the ``schema`` argument. :issue:`119`
- Checks to verify response status & content type in CLI / Runner. :issue:`101`

**Fixed**

- Custom base URL handling in CLI / Runner. :issue:`248`

**Changed**

- Raise an error if the schema has a body for GET requests. :issue:`218`
- Method names are case insensitive during direct schema access. :issue:`246`

.. _v0.13.2:

:version:`0.13.2 <v0.13.1...v0.13.2>` - 2019-11-05
--------------------------------------------------

**Fixed**

- ``IndexError`` when Hypothesis found inconsistent test results during the test execution in the runner. :issue:`236`

.. _v0.13.1:

:version:`0.13.1 <v0.13.0...v0.13.1>` - 2019-11-05
--------------------------------------------------

**Added**

- Support for binary format :issue:`197`

**Fixed**

- Error that happens when there are no success checks in the statistic in CLI. :issue:`237`

.. _v0.13.0:

:version:`0.13.0 <v0.12.2...v0.13.0>` - 2019-11-03
--------------------------------------------------

**Added**

- An option to configure request timeout for CLI / Runner. :issue:`204`
- A help snippet to reproduce errors caught by Schemathesis. :issue:`206`
- Total running time to the CLI output. :issue:`181`
- Summary line in the CLI output with the number of passed / failed / errored endpoint tests. :issue:`209`
- Extra information to the CLI output: schema address, spec version, and base URL. :issue:`188`

**Fixed**

- Compatibility with Hypothesis 4.42.4+ . :issue:`212`
- Display flaky errors only in the "ERRORS" section and improve CLI output. :issue:`215`
- Handling ``formData`` parameters in ``Case.call``. :issue:`196`
- Handling cookies in ``Case.call``. :issue:`211`

**Changed**

- More readable falsifying examples output. :issue:`127`
- Show exceptions in a separate section of the CLI output. :issue:`203`
- Error message for cases when it is not possible to satisfy schema parameters. It should be more clear now. :issue:`216`
- Do not stop on schema errors related to a single endpoint. :issue:`139`
- Display a proper error message when the schema is not available in CLI / Runner. :issue:`214`

.. _v0.12.2:

:version:`0.12.2 <v0.12.1...v0.12.2>` - 2019-10-30
--------------------------------------------------

**Fixed**

- Wrong handling of the ``base_url`` parameter in runner and ``Case.call`` if it has a trailing slash. :issue:`194` and :issue:`199`
- Do not send any payload with GET requests. :issue:`200`

.. _v0.12.1:

:version:`0.12.1 <v0.12.0...v0.12.1>` - 2019-10-28
--------------------------------------------------

**Fixed**

- Handling for errors other than ``AssertionError`` and ``HypothesisException`` in the runner. :issue:`189`
- CLI failing on the case when there are tests, but no checks were performed. :issue:`191`

**Changed**

- Display the "SUMMARY" section in the CLI output for empty test suites.

.. _v0.12.0:

:version:`0.12.0 <v0.11.0...v0.12.0>` - 2019-10-28
--------------------------------------------------

**Added**

- Display progress during the CLI run. :issue:`125`

**Fixed**

- Test server-generated wrong schema when the ``endpoints`` option is passed via CLI. :issue:`173`
- Error message if the schema is not found in CLI. :issue:`172`

**Changed**

- Continue running tests on hypothesis error. :issue:`137`

.. _v0.11.0:

:version:`0.11.0 <v0.10.0...v0.11.0>` - 2019-10-22
--------------------------------------------------

**Added**

- LazySchema accepts filters. :issue:`149`
- Ability to register strategies for custom string formats. :issue:`94`
- Generator-based events in the ``runner`` module to improve control over the execution flow.
- Filtration by tags. :issue:`134`

**Changed**

- Base URL in schema instances could be reused when it is defined during creation.
  Now on, the ``base_url`` argument in ``Case.call`` is optional in such cases. :issue:`153`
- Hypothesis deadline is set to 500ms by default. :issue:`138`
- Hypothesis output is captured separately, without capturing the whole stdout during CLI run.
- Disallow empty username in CLI ``--auth`` option.

**Fixed**

- User-agent during schema loading. :issue:`144`
- Generation of invalid values in ``Case.headers``. :issue:`167`

**Removed**

- Undocumented support for ``file://`` URI schema

.. _v0.10.0:

:version:`0.10.0 <v0.9.0...v0.10.0>` - 2019-10-14
--------------------------------------------------

**Added**

- HTTP Digest Auth support. :issue:`106`
- Support for Hypothesis settings in CLI & Runner. :issue:`107`
- ``Case.call`` and ``Case.as_requests_kwargs`` convenience methods. :issue:`109`
- Local development server. :issue:`126`

**Removed**

- Autogenerated ``runner.StatsCollector.__repr__`` to make Hypothesis output more readable.

.. _v0.9.0:

:version:`0.9.0 <v0.8.1...v0.9.0>` - 2019-10-09
-----------------------------------------------

**Added**

- Test executor collects results of execution. :issue:`29`
- CLI option ``--base-url`` for specifying base URL of API. :issue:`118`
- Support for coroutine-based tests. :issue:`121`
- User Agent to network requests in CLI & runner. :issue:`130`

**Changed**

- CLI command ``schemathesis run`` prints result in a more readable way with a summary of passing checks.
- Empty header names are forbidden for CLI.
- Suppressed hypothesis exception about using ``example`` non-interactively. :issue:`92`

.. _v0.8.1:

:version:`0.8.1 <v0.8.0...v0.8.1>` - 2019-10-04
-----------------------------------------------

**Fixed**

- Wrap each test in ``suppress`` so the runner doesn't stop after the first test failure.

.. _v0.8.0:

:version:`0.8.0 <v0.7.3...v0.8.0>` - 2019-10-04
-----------------------------------------------

**Added**

- CLI tool invoked by the ``schemathesis`` command. :issue:`30`
- New arguments ``api_options``, ``loader_options`` and ``loader`` for test executor. :issue:`90`
- A mapping interface for schemas & convenience methods for direct strategy access. :issue:`98`

**Fixed**

- Runner stopping on the first falsifying example. :issue:`99`

.. _v0.7.3:

:version:`0.7.3 <v0.7.2...v0.7.3>` - 2019-09-30
-----------------------------------------------

**Fixed**

- Filtration in lazy loaders.

.. _v0.7.2:

:version:`0.7.2 <v0.7.1...v0.7.2>` - 2019-09-30
-----------------------------------------------

**Added**

- Support for type "file" for Swagger 2.0. :issue:`78`
- Support for filtering in loaders. :issue:`75`

**Fixed**

- Conflict for lazy schema filtering. :issue:`64`

.. _v0.7.1:

:version:`0.7.1 <v0.7.0...v0.7.1>` - 2019-09-27
-----------------------------------------------

**Added**

- Support for ``x-nullable`` extension. :issue:`45`

.. _v0.7.0:

:version:`0.7.0 <v0.6.0...v0.7.0>` - 2019-09-26
-----------------------------------------------

**Added**

- Support for the ``cookie`` parameter in OpenAPI 3.0 schemas. :issue:`21`
- Support for the ``formData`` parameter in Swagger 2.0 schemas. :issue:`6`
- Test executor. :issue:`28`

**Fixed**

- Using ``hypothesis.settings`` decorator with test functions created from ``from_pytest_fixture`` loader. :issue:`69`

.. _v0.6.0:

:version:`0.6.0 <v0.5.0...v0.6.0>` - 2019-09-24
-----------------------------------------------

**Added**

- Parametrizing tests from a pytest fixture via ``pytest-subtests``. :issue:`58`

**Changed**

- Rename module ``readers`` to ``loaders``.
- Rename ``parametrize`` parameters. ``filter_endpoint`` to ``endpoint`` and ``filter_method`` to ``method``.

**Removed**

- Substring match for method/endpoint filters. To avoid clashing with escaped chars in endpoints keys in schemas.

.. _v0.5.0:

:version:`0.5.0 <v0.4.1...v0.5.0>` - 2019-09-16
-----------------------------------------------

**Added**

- Generating explicit examples from the schema. :issue:`17`

**Changed**

- Schemas are loaded eagerly from now on. Using ``schemathesis.from_uri`` implies network calls.

Deprecated


- Using ``Parametrizer.from_{path,uri}`` is deprecated, use ``schemathesis.from_{path,uri}`` instead.

**Fixed**

- Body resolving during test collection. :issue:`55`

.. _v0.4.1:

:version:`0.4.1 <v0.4.0...v0.4.1>` - 2019-09-11
-----------------------------------------------

**Fixed**

- Possibly unhandled exception during ``hasattr`` check in ``is_schemathesis_test``.

.. _v0.4.0:

:version:`0.4.0 <v0.3.0...v0.4.0>` - 2019-09-10
-----------------------------------------------

**Fixed**

- Resolving all inner references in objects. :issue:`34`

**Changed**

- ``jsonschema.RefResolver`` is now used for reference resolving. :issue:`35`

.. _v0.3.0:

:version:`0.3.0 <v0.2.0...v0.3.0>` - 2019-09-06
-----------------------------------------------

**Added**

- ``Parametrizer.from_uri`` method to construct parametrizer instances from URIs. :issue:`24`

**Removed**

- Possibility to use ``Parametrizer.parametrize`` and custom ``Parametrizer`` kwargs for passing config options
  to ``hypothesis.settings``. Use ``hypothesis.settings`` decorators on tests instead.

.. _v0.2.0:

:version:`0.2.0 <v0.1.0...v0.2.0>` - 2019-09-05
-----------------------------------------------

**Added**

- Open API 3.0 support. :issue:`10`
- "header" parameters. :issue:`7`

**Changed**

- Handle errors during collection / executions as failures.
- Use ``re.search`` for pattern matching in ``filter_method``/``filter_endpoint`` instead of ``fnmatch``. :issue:`18`
- ``Case.body`` contains properties from the target schema, without the extra level of nesting.

**Fixed**

- ``KeyError`` on collection when "basePath" is absent. :issue:`16`

.. _v0.1.0:

0.1.0 - 2019-06-28
------------------

- Initial public release
