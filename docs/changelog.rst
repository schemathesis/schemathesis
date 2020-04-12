.. _changelog:

Changelog
=========

`Unreleased`_
-------------

Fixed
~~~~~

- Mistakenly applied Open API -> JSON Schema Draft 7 conversion. It should be Draft 4. `#489`_
- Using wrong validator in ``response_schema_conformance`` check. It should be Draft 4 validator. `#468`_

`1.1.0`_ - 2020-04-08
---------------------

Fixed
~~~~~

- Response schema check for recursive schemas. `#468`_

Changed
~~~~~~~

- App loading in ``runner``. Now it accepts application as an importable string, rather than an instance. It is done
  to make it possible to execute runner in a subprocess. Otherwise apps can't be easily serialized and transferred into
  another process.
- Runner events structure. All data in events is static from now, there are no references to ``BaseSchema``, ``Endpoint`` or
  similar objects that may calculate data dynamically. This is done to make events serializable and not tied to Python
  object which decouples any ``runner`` consumer from implementation details and will help make ``runner`` usable in
  more cases (e.g. web application), since events can be serialized to JSON and used in any environment.
  Another related change is that Python exceptions are not propagated anymore - they are replaced with ``InternalError``
  event that should be handled accordingly.

`1.0.5`_ - 2020-04-03
---------------------

Fixed
~~~~~

- Open API 3. Handling of endpoints that contain ``multipart/form-data`` media types.
  Previously only file upload endpoints were working correctly. `#473`_

`1.0.4`_ - 2020-04-03
---------------------

Fixed
~~~~~

- ``OpenApi30.get_content_types`` behavior, introduced in `8aeee1a <https://github.com/kiwicom/schemathesis/commit/8aeee1ab2c6c97d94272dde4790f5efac3951aed>`_. `#469`_

`1.0.3`_ - 2020-04-03
---------------------

Fixed
~~~~~

- Precedence of ``produces`` keywords for Swagger 2.0 schemas. Now, operation-level ``produces`` overrides
  schema-level ``produces`` as specified in the specification. `#463`_
- Content Type conformance check for Open API 3.0 schemas. `#461`_
- Pytest 5.4 warning for test functions without parametrization. `#451`_

`1.0.2`_ - 2020-04-02
---------------------

Fixed
~~~~~

- Handling of fields in ``paths`` that are not operations, but allowed by the Open API spec. `#457`_
- Pytest 5.4 warning about deprecated ``Node`` initialization usage. `#451`_

`1.0.1`_ - 2020-04-01
---------------------

Fixed
~~~~~

- Processing of explicit examples in Open API 3.0 when there are multiple parameters in the same location (e.g. ``path``)
  contain ``example`` value. They are properly combined now. `#450`_

`1.0.0`_ - 2020-03-31
---------------------

Changed
~~~~~~~

- Move processing of ``runner`` parameters to ``runner.prepare``. This change will provide better code reusage, since
  all users of ``runner`` (e.g. if you extended it in your project`) need some kind of input parameters handling, which
  was implemented only in Schemathesis CLI. It is not backward-compatible. If you didn't use ``runner`` directly, then
  this change should not have a visible effect for your use-case.

`0.28.0`_ - 2020-03-31
----------------------

Fixed
~~~~~

- Handling of schemas, that use ``x-*`` custom properties. `#448`_

Removed
~~~~~~~

- Deprecated ``runner.execute``. Use ``runner.prepare`` instead.

`0.27.0`_ - 2020-03-31
----------------------

Deprecated
~~~~~~~~~~

- ``runner.execute`` should not be used, since ``runner.prepare`` provides a more flexible interface to test execution.

Removed
~~~~~~~

- Deprecated ``Parametrizer`` class. Use ``schemathesis.from_path`` as a replacement for ``Parametrizer.from_path``.

`0.26.1`_ - 2020-03-24
----------------------

Fixed
~~~~~

- Limit recursion depth while resolving JSON schema to handle recursion without breaking. `#435`_

`0.26.0`_ - 2020-03-19
----------------------

Fixed
~~~~~

- Filter problematic path template variables containing ``"/"``, or ``"%2F"`` url encoded. `#440`_
- Filter invalid empty ``""`` path template variables. `#439`_
- Typo in a help message in the CLI output. `#436`_

`0.25.1`_ - 2020-03-09
----------------------

Changed
~~~~~~~

- Allow ``werkzeug`` >= 1.0.0. `#433`_

`0.25.0`_ - 2020-02-27
----------------------

Changed
~~~~~~~

- Handling of explicit examples from schemas. Now if there are examples for multiple locations
  (e.g. for body and for query) then they will be combined into a single example. `#424`_

`0.24.5`_ - 2020-02-26
----------------------

Fixed
~~~~~

- Error during ``pytest`` collection on objects that have custom ``__getattr__`` method and therefore pass ``is_schemathesis`` check. `#429`_

`0.24.4`_ - 2020-02-22
----------------------

Fixed
~~~~~

- Resolving references when schema is loaded from a file on Windows. `#418`_

`0.24.3`_ - 2020-02-10
----------------------

Fixed
~~~~~

- Not copied ``validate_schema`` parameter in ``BaseSchema.parametrize``. Regression after implementing `#383`_
- Missing ``app``, ``location`` and ``hooks`` parameters in schema when used with ``BaseSchema.parametrize``. `#416`_

`0.24.2`_ - 2020-02-09
----------------------

Fixed
~~~~~

- Crash on invalid regular expressions in ``method``, ``endpoint`` and ``tag`` CLI options. `#403`_
- Crash on non latin-1 encodable value in ``auth`` CLI option. `#404`_
- Crash on invalid value in ``header`` CLI options. `#405`_
- Crash on some invalid URLs in ``schema`` CLI option. `#406`_
- Validation of ``--request-timeout`` parameter. `#407`_
- Crash with ``--hypothesis-deadline=0`` CLI option. `#410`_
- Crash with ``--hypothesis-max-examples=0`` CLI option. `#412`_

`0.24.1`_ - 2020-02-08
----------------------

Fixed
~~~~~

- CLI crash on Windows and Python < 3.8 when the schema path contains characters unrepresentable at the OS level. `#400`_

`0.24.0`_ - 2020-02-07
----------------------

Added
~~~~~

- Support for testing of examples in Parameter & Media Type objects in Open API 3.0. `#394`_
- ``--show-error-tracebacks`` CLI option to display errors' tracebacks in the output. `#391`_
- Support for schema behind auth. `#115`_

Changed
~~~~~~~

- Schemas with GET endpoints accepting body are allowed now if schema validation is disabled (via ``--validate-schema=false`` for example).
  The usecase is for tools like ElasticSearch that use GET requests with non empty bodies. `#383`_

Fixed
~~~~~

- CLI crash when an explicit example is specified in endpoint definition. `#386`_

`0.23.7`_ - 2020-01-30
----------------------

Added
~~~~~

- ``-x``/``--exitfirst`` CLI option to exit after first failed test. `#378`_

Fixed
~~~~~

- Handling examples of parameters in Open API 3.0. `#381`_

`0.23.6`_ - 2020-01-28
----------------------

Added
~~~~~

- ``all`` variant for ``--checks`` CLI option to use all available checks. `#374`_

Changed
~~~~~~~

- Use built-in ``importlib.metadata`` on Python 3.8. `#376`_

`0.23.5`_ - 2020-01-24
----------------------

Fixed
~~~~~

- Generation of invalid values in ``Case.cookies``. `#371`_

`0.23.4`_ - 2020-01-22
----------------------

Fixed
~~~~~

- Converting ``exclusiveMinimum`` & ``exclusiveMaximum`` fields to JSON Schema. `#367`_

`0.23.3`_ - 2020-01-21
----------------------

Fixed
~~~~~

- Filter out surrogate pairs from query string.

`0.23.2`_ - 2020-01-16
----------------------

Fixed
~~~~~

- Prevent ``KeyError`` when response does not have Content-Type. `#365`_

`0.23.1`_ - 2020-01-15
----------------------

Fixed
~~~~~

- Dockerfile entrypoint was not working as per docs. `#361`_

`0.23.0`_ - 2020-01-15
----------------------

Added
~~~~~

- Hooks for strategy modification. `#313`_
- Input schema validation. Use ``--validate-schema=false`` to disable it in CLI and ``validate_schema=False`` argument in loaders. `#110`_

`0.22.0`_ - 2020-01-11
----------------------

Added
~~~~~

- Show multiple found failures in the CLI output. `#266`_ & `#207`_
- Raise proper exception when the given schema is invalid. `#308`_
- Support for ``None`` as a value for ``--hypothesis-deadline``. `#349`_

Fixed
~~~~~

- Handling binary request payloads in ``Case.call``. `#350`_
- Type of the second argument to all built-in checks set to proper ``Case`` instead of ``TestResult``.
  The error was didn't affect built-in checks since both ``Case`` and ``TestResult`` had ``endpoint`` attribute and only
  it was used. However this fix is not backward-compatible with 3rd party checks.

`0.21.0`_ - 2019-12-20
----------------------

Added
~~~~~

- Support for AioHTTP applications in CLI. `#329`_

`0.20.5`_ - 2019-12-18
----------------------

Fixed
~~~~~

- Compatibility with the latest release of ``hypothesis-jsonschema`` and setting its minimal required version to ``0.9.13``. `#338`_

`0.20.4`_ - 2019-12-17
----------------------

Fixed
~~~~~

- Handling ``nullable`` attribute in Open API schemas. `#335`_

`0.20.3`_ - 2019-12-17
----------------------

Fixed
~~~~~

- Response status code conformance check applicability for old ``requests`` version. `#330`_

`0.20.2`_ - 2019-12-14
----------------------

Fixed
~~~~~

- Response schema conformance check for Open API 3.0. `#332`_

`0.20.1`_ - 2019-12-13
----------------------

Added
~~~~~

- Support for response code ranges. `#330`_

`0.20.0`_ - 2019-12-12
----------------------

Added
~~~~~

- WSGI apps support. `#31`_
- ``Case.validate_response`` for running built-in checks against app's response. `#319`_

Changed
~~~~~~~

- Checks receive ``Case`` instance as a second argument instead of ``TestResult``.
  This was done for making checks usable in Python tests via ``Case.validate_response``.
  Endpoint and schema are accessible via ``case.endpoint`` and ``case.endpoint.schema``.

`0.19.1`_ - 2019-12-11
----------------------

Fixed
~~~~~

- Compatibility with Hypothesis >= 4.53.2. `#322`_

`0.19.0`_ - 2019-12-02
----------------------

Added
~~~~~

- Concurrent test execution in CLI / runner. `#91`_
- update importlib_metadata pin to ``^1.1``. `#315`_

`0.18.1`_ - 2019-11-28
----------------------

Fixed
~~~~~

- Validation of ``base-url`` CLI parameter. `#311`_

`0.18.0`_ - 2019-11-27
----------------------

Added
~~~~~

- Resolving references in ``PathItem`` objects. `#301`_

Fixed
~~~~~

- Resolving of relative paths in schemas. `#303`_
- Loading string dates as ``datetime.date`` objects in YAML loader. `#305`_

`0.17.0`_ - 2019-11-21
----------------------

Added
~~~~~

- Resolving references that point to different files. `#294`_

Changed
~~~~~~~

- Keyboard interrupt is now handled during the CLI run and the summary is displayed in the output. `#295`_

`0.16.0`_ - 2019-11-19
----------------------

Added
~~~~~

- Display RNG seed in the CLI output to allow test reproducing. `#267`_
- Allow to specify seed in CLI.
- Ability to pass custom kwargs to the ``requests.get`` call in ``loaders.from_uri``.

Changed
~~~~~~~

- Refactor case generation strategies: strategy is not used to generate empty value. `#253`_
- Improved error message for invalid path parameter declaration. `#255`_

Fixed
~~~~~

- Pytest fixture parametrization via ``pytest_generate_tests``. `#280`_
- Support for tests defined as methods. `#282`_
- Unclosed ``requests.Session`` on calling ``Case.call`` without passing a session explicitly. `#286`_

`0.15.0`_ - 2019-11-15
----------------------

Added
~~~~~

- Support for OpenAPI 3.0 server variables (base_path). `#40`_
- Support for ``format: byte``. `#254`_
- Response schema conformance check in CLI / Runner. `#256`_
- Docker image for CLI. `#268`_
- Pre-run hooks for CLI. `#147`_
- A way to register custom checks for CLI via ``schemathesis.register_check``. `#270`_

Fixed
~~~~~

- Not encoded path parameters. `#272`_

Changed
~~~~~~~

- Verbose messages are displayed in the CLI on failed checks. `#261`_

`0.14.0`_ - 2019-11-09
----------------------

Added
~~~~~

- CLI: Support file paths in ``schema`` argument. `#119`_
- Checks to verify response status & content type in CLI / Runner. `#101`_

Fixed
~~~~~

- Custom base URL handling in CLI / Runner. `#248`_

Changed
~~~~~~~

- Raise an error if schema has body for GET requests. `#218`_
- Method names are case insensitive during direct schema access. `#246`_

`0.13.2`_ - 2019-11-05
----------------------

Fixed
~~~~~

- ``IndexError`` when Hypothesis found inconsistent test results during the test execution in runner. `#236`_

`0.13.1`_ - 2019-11-05
----------------------

Added
~~~~~

- Support for binary format `#197`_

Fixed
~~~~~

- Error that happens when there are no success checks in the statistic in CLI. `#237`_

`0.13.0`_ - 2019-11-03
----------------------

Added
~~~~~

- An option to configure request timeout for CLI / Runner. `#204`_
- A help snippet to reproduce errors caught by Schemathesis. `#206`_
- Total running time to the CLI output. `#181`_
- Summary line in the CLI output with the number of passed / failed / errored endpoint tests. `#209`_
- Extra information to the CLI output: schema address, spec version and base url. `#188`_

Fixed
~~~~~

- Compatibility with Hypothesis 4.42.4+ . `#212`_
- Display flaky errors only in the "ERRORS" section and improve CLI output. `#215`_
- Handling ``formData`` parameters in ``Case.call``. `#196`_
- Handling cookies in ``Case.call``. `#211`_

Changed
~~~~~~~

- More readable falsifying examples output. `#127`_
- Show exceptions in a separate section of the CLI output. `#203`_
- Error message for cases when it is not possible to satisfy schema parameters. It should be more clear now. `#216`_
- Do not stop on schema errors related to single endpoint. `#139`_
- Display a proper error message when schema is not available in CLI / Runner. `#214`_

`0.12.2`_ - 2019-10-30
----------------------

Fixed
~~~~~

- Wrong handling of ``base_url`` parameter in runner and ``Case.call`` if it has a trailing slash. `#194`_ and `#199`_
- Do not send any payload with GET requests. `#200`_

`0.12.1`_ - 2019-10-28
----------------------

Fixed
~~~~~

- Handling for errors other than ``AssertionError`` and ``HypothesisException`` in the runner. `#189`_
- CLI failing on the case when there are tests, but no checks were performed. `#191`_

Changed
~~~~~~~

- Display "SUMMARY" section in the CLI output for empty test suites.

`0.12.0`_ - 2019-10-28
----------------------

Added
~~~~~

- Display progress during the CLI run. `#125`_

Fixed
~~~~~

- Test server generated wrong schema when ``endpoints`` option is passed via CLI. `#173`_
- Error message if schema is not found in CLI. `#172`_

Changed
~~~~~~~

- Continue running tests on hypothesis error. `#137`_

`0.11.0`_ - 2019-10-22
----------------------

Added
~~~~~

- LazySchema accepts filters. `#149`_
- Ability to register strategies for custom string formats. `#94`_
- Generator-based events in ``runner`` module to improve control over the execution flow.
- Filtration by tags. `#134`_

Changed
~~~~~~~

- Base URL in schema instances could be reused when it is defined during creation.
  Now on, ``base_url`` argument in ``Case.call`` is optional in such cases. `#153`_
- Hypothesis deadline is set to 500ms by default. `#138`_
- Hypothesis output is captured separately, without capturing the whole stdout during CLI run.
- Disallow empty username in CLI ``--auth`` option.

Fixed
~~~~~

- User agent during schema loading. `#144`_
- Generation of invalid values in ``Case.headers``. `#167`_

Removed
~~~~~~~

- Undocumented support for ``file://`` uri schema

`0.10.0`_ - 2019-10-14
----------------------

Added
~~~~~

- HTTP Digest Auth support. `#106`_
- Support for Hypothesis settings in CLI & Runner. `#107`_
- ``Case.call`` and ``Case.as_requests_kwargs`` convenience methods. `#109`_
- Local development server. `#126`_

Removed
~~~~~~~

- Autogenerated ``runner.StatsCollector.__repr__`` to make Hypothesis output more readable.

`0.9.0`_ - 2019-10-09
---------------------

Added
~~~~~

- Test executor collects results of execution. `#29`_
- CLI option ``--base-url`` for specifying base URL of API. `#118`_
- Support for coroutine-based tests. `#121`_
- User Agent to network requests in CLI & runner. `#130`_

Changed
~~~~~~~

- CLI command ``schemathesis run`` prints results in a more readable way with a summary of passing checks.
- Empty header names are forbidden for CLI.
- Suppressed hypothesis exception about using ``example`` non-interactively. `#92`_

`0.8.1`_ - 2019-10-04
---------------------

Fixed
~~~~~

- Wrap each individual test in ``suppress`` so the runner doesn't stop after the first test failure.

`0.8.0`_ - 2019-10-04
---------------------

Added
~~~~~

- CLI tool invoked by the ``schemathesis`` command. `#30`_
- New arguments ``api_options``, ``loader_options`` and ``loader`` for test executor. `#90`_
- A mapping interface for schemas & convenience methods for direct strategies access. `#98`_

Fixed
~~~~~

- Runner stopping on the first falsifying example. `#99`_

`0.7.3`_ - 2019-09-30
---------------------

Fixed
~~~~~

- Filtration in lazy loaders.

`0.7.2`_ - 2019-09-30
---------------------

Added
~~~~~

- Support for type "file" for Swagger 2.0. `#78`_
- Support for filtering in loaders. `#75`_

Fixed
~~~~~

- Conflict for lazy schema filtering. `#64`_

`0.7.1`_ - 2019-09-27
---------------------

Added
~~~~~

- Support for ``x-nullable`` extension. `#45`_

`0.7.0`_ - 2019-09-26
---------------------

Added
~~~~~

- Support for ``cookie`` parameter in OpenAPI 3.0 schemas. `#21`_
- Support for ``formData`` parameter in Swagger 2.0 schemas. `#6`_
- Test executor. `#28`_

Fixed
~~~~~

- Using ``hypothesis.settings`` decorator with test functions created from ``from_pytest_fixture`` loader. `#69`_

`0.6.0`_ - 2019-09-24
---------------------

Added
~~~~~

- Parametrizing tests from a pytest fixture via ``pytest-subtests``. `#58`_

Changed
~~~~~~~

- Rename module ``readers`` to ``loaders``.
- Rename ``parametrize`` parameters. ``filter_endpoint`` to ``endpoint`` and ``filter_method`` to ``method``.

Removed
~~~~~~~

- Substring match for method / endpoint filters. To avoid clashing with escaped chars in endpoints keys in schemas.

`0.5.0`_ - 2019-09-16
---------------------

Added
~~~~~

- Generating explicit examples from schema. `#17`_

Changed
~~~~~~~

- Schemas are loaded eagerly from now on. Using ``schemathesis.from_uri`` implies network calls.

Deprecated
~~~~~~~~~~

- Using ``Parametrizer.from_{path,uri}`` is deprecated, use ``schemathesis.from_{path,uri}`` instead.

Fixed
~~~~~

- Body resolving during test collection. `#55`_

`0.4.1`_ - 2019-09-11
---------------------

Fixed
~~~~~

- Possibly unhandled exception during ``hasattr`` check in ``is_schemathesis_test``.

`0.4.0`_ - 2019-09-10
---------------------

Fixed
~~~~~

- Resolving all inner references in objects. `#34`_

Changed
~~~~~~~

- ``jsonschema.RefResolver`` is now used for reference resolving. `#35`_

`0.3.0`_ - 2019-09-06
---------------------

Added
~~~~~

- ``Parametrizer.from_uri`` method to construct parametrizer instances from URIs. `#24`_

Removed
~~~~~~~

- Possibility to use ``Parametrizer.parametrize`` and custom ``Parametrizer`` kwargs for passing config options
  to ``hypothesis.settings``. Use ``hypothesis.settings`` decorators on tests instead.

`0.2.0`_ - 2019-09-05
---------------------

Added
~~~~~

- Open API 3.0 support. `#10`_
- "header" parameters. `#7`_

Changed
~~~~~~~

- Handle errors during collection / executions as failures.
- Use ``re.search`` for pattern matching in ``filter_method``/``filter_endpoint`` instead of ``fnmatch``. `#18`_
- ``Case.body`` contains properties from the target schema, without extra level of nesting.

Fixed
~~~~~

- ``KeyError`` on collection when "basePath" is absent. `#16`_

0.1.0 - 2019-06-28
------------------

- Initial public release

.. _Unreleased: https://github.com/kiwicom/schemathesis/compare/v1.1.0...HEAD
.. _1.1.0: https://github.com/kiwicom/schemathesis/compare/v1.0.5...v1.1.0
.. _1.0.5: https://github.com/kiwicom/schemathesis/compare/v1.0.4...v1.0.5
.. _1.0.4: https://github.com/kiwicom/schemathesis/compare/v1.0.3...v1.0.4
.. _1.0.3: https://github.com/kiwicom/schemathesis/compare/v1.0.2...v1.0.3
.. _1.0.2: https://github.com/kiwicom/schemathesis/compare/v1.0.1...v1.0.2
.. _1.0.1: https://github.com/kiwicom/schemathesis/compare/v1.0.0...v1.0.1
.. _1.0.0: https://github.com/kiwicom/schemathesis/compare/v0.28.0...v1.0.0
.. _0.28.0: https://github.com/kiwicom/schemathesis/compare/v0.27.0...v0.28.0
.. _0.27.0: https://github.com/kiwicom/schemathesis/compare/v0.26.1...v0.27.0
.. _0.26.1: https://github.com/kiwicom/schemathesis/compare/v0.26.0...v0.26.1
.. _0.26.0: https://github.com/kiwicom/schemathesis/compare/v0.25.1...v0.26.0
.. _0.25.1: https://github.com/kiwicom/schemathesis/compare/v0.25.0...v0.25.1
.. _0.25.0: https://github.com/kiwicom/schemathesis/compare/v0.24.5...v0.25.0
.. _0.24.5: https://github.com/kiwicom/schemathesis/compare/v0.24.4...v0.24.5
.. _0.24.4: https://github.com/kiwicom/schemathesis/compare/v0.24.3...v0.24.4
.. _0.24.3: https://github.com/kiwicom/schemathesis/compare/v0.24.2...v0.24.3
.. _0.24.2: https://github.com/kiwicom/schemathesis/compare/v0.24.1...v0.24.2
.. _0.24.1: https://github.com/kiwicom/schemathesis/compare/v0.24.0...v0.24.1
.. _0.24.0: https://github.com/kiwicom/schemathesis/compare/v0.23.7...v0.24.0
.. _0.23.7: https://github.com/kiwicom/schemathesis/compare/v0.23.6...v0.23.7
.. _0.23.6: https://github.com/kiwicom/schemathesis/compare/v0.23.5...v0.23.6
.. _0.23.5: https://github.com/kiwicom/schemathesis/compare/v0.23.4...v0.23.5
.. _0.23.4: https://github.com/kiwicom/schemathesis/compare/v0.23.3...v0.23.4
.. _0.23.3: https://github.com/kiwicom/schemathesis/compare/v0.23.2...v0.23.3
.. _0.23.2: https://github.com/kiwicom/schemathesis/compare/v0.23.1...v0.23.2
.. _0.23.1: https://github.com/kiwicom/schemathesis/compare/v0.23.0...v0.23.1
.. _0.23.0: https://github.com/kiwicom/schemathesis/compare/v0.22.0...v0.23.0
.. _0.22.0: https://github.com/kiwicom/schemathesis/compare/v0.21.0...v0.22.0
.. _0.21.0: https://github.com/kiwicom/schemathesis/compare/v0.20.5...v0.21.0
.. _0.20.5: https://github.com/kiwicom/schemathesis/compare/v0.20.4...v0.20.5
.. _0.20.4: https://github.com/kiwicom/schemathesis/compare/v0.20.3...v0.20.4
.. _0.20.3: https://github.com/kiwicom/schemathesis/compare/v0.20.2...v0.20.3
.. _0.20.2: https://github.com/kiwicom/schemathesis/compare/v0.20.1...v0.20.2
.. _0.20.1: https://github.com/kiwicom/schemathesis/compare/v0.20.0...v0.20.1
.. _0.20.0: https://github.com/kiwicom/schemathesis/compare/v0.19.1...v0.20.0
.. _0.19.1: https://github.com/kiwicom/schemathesis/compare/v0.19.1...v0.19.1
.. _0.19.0: https://github.com/kiwicom/schemathesis/compare/v0.18.1...v0.19.0
.. _0.18.1: https://github.com/kiwicom/schemathesis/compare/v0.18.0...v0.18.1
.. _0.18.0: https://github.com/kiwicom/schemathesis/compare/v0.17.0...v0.18.0
.. _0.17.0: https://github.com/kiwicom/schemathesis/compare/v0.16.0...v0.17.0
.. _0.16.0: https://github.com/kiwicom/schemathesis/compare/v0.15.0...v0.16.0
.. _0.15.0: https://github.com/kiwicom/schemathesis/compare/v0.14.0...v0.15.0
.. _0.14.0: https://github.com/kiwicom/schemathesis/compare/v0.13.2...v0.14.0
.. _0.13.2: https://github.com/kiwicom/schemathesis/compare/v0.13.1...v0.13.2
.. _0.13.1: https://github.com/kiwicom/schemathesis/compare/v0.13.0...v0.13.1
.. _0.13.0: https://github.com/kiwicom/schemathesis/compare/v0.12.2...v0.13.0
.. _0.12.2: https://github.com/kiwicom/schemathesis/compare/v0.12.1...v0.12.2
.. _0.12.1: https://github.com/kiwicom/schemathesis/compare/v0.12.0...v0.12.1
.. _0.12.0: https://github.com/kiwicom/schemathesis/compare/v0.11.0...v0.12.0
.. _0.11.0: https://github.com/kiwicom/schemathesis/compare/v0.10.0...v0.11.0
.. _0.10.0: https://github.com/kiwicom/schemathesis/compare/v0.9.0...v0.10.0
.. _0.9.0: https://github.com/kiwicom/schemathesis/compare/v0.8.1...v0.9.0
.. _0.8.1: https://github.com/kiwicom/schemathesis/compare/v0.8.0...v0.8.1
.. _0.8.0: https://github.com/kiwicom/schemathesis/compare/v0.7.3...v0.8.0
.. _0.7.3: https://github.com/kiwicom/schemathesis/compare/v0.7.2...v0.7.3
.. _0.7.2: https://github.com/kiwicom/schemathesis/compare/v0.7.1...v0.7.2
.. _0.7.1: https://github.com/kiwicom/schemathesis/compare/v0.7.0...v0.7.1
.. _0.7.0: https://github.com/kiwicom/schemathesis/compare/v0.6.0...v0.7.0
.. _0.6.0: https://github.com/kiwicom/schemathesis/compare/v0.5.0...v0.6.0
.. _0.5.0: https://github.com/kiwicom/schemathesis/compare/v0.4.1...v0.5.0
.. _0.4.1: https://github.com/kiwicom/schemathesis/compare/v0.4.0...v0.4.1
.. _0.4.0: https://github.com/kiwicom/schemathesis/compare/v0.3.0...v0.4.0
.. _0.3.0: https://github.com/kiwicom/schemathesis/compare/v0.2.0...v0.3.0
.. _0.2.0: https://github.com/kiwicom/schemathesis/compare/v0.1.0...v0.2.0

.. _#489: https://github.com/kiwicom/schemathesis/issues/489
.. _#473: https://github.com/kiwicom/schemathesis/issues/473
.. _#469: https://github.com/kiwicom/schemathesis/issues/469
.. _#468: https://github.com/kiwicom/schemathesis/issues/468
.. _#463: https://github.com/kiwicom/schemathesis/issues/463
.. _#461: https://github.com/kiwicom/schemathesis/issues/461
.. _#457: https://github.com/kiwicom/schemathesis/issues/457
.. _#451: https://github.com/kiwicom/schemathesis/issues/451
.. _#450: https://github.com/kiwicom/schemathesis/issues/450
.. _#448: https://github.com/kiwicom/schemathesis/issues/448
.. _#440: https://github.com/kiwicom/schemathesis/issues/440
.. _#439: https://github.com/kiwicom/schemathesis/issues/439
.. _#436: https://github.com/kiwicom/schemathesis/issues/436
.. _#435: https://github.com/kiwicom/schemathesis/issues/435
.. _#433: https://github.com/kiwicom/schemathesis/issues/433
.. _#429: https://github.com/kiwicom/schemathesis/issues/429
.. _#424: https://github.com/kiwicom/schemathesis/issues/424
.. _#418: https://github.com/kiwicom/schemathesis/issues/418
.. _#416: https://github.com/kiwicom/schemathesis/issues/416
.. _#412: https://github.com/kiwicom/schemathesis/issues/412
.. _#410: https://github.com/kiwicom/schemathesis/issues/410
.. _#407: https://github.com/kiwicom/schemathesis/issues/407
.. _#406: https://github.com/kiwicom/schemathesis/issues/406
.. _#405: https://github.com/kiwicom/schemathesis/issues/405
.. _#404: https://github.com/kiwicom/schemathesis/issues/404
.. _#403: https://github.com/kiwicom/schemathesis/issues/403
.. _#400: https://github.com/kiwicom/schemathesis/issues/400
.. _#394: https://github.com/kiwicom/schemathesis/issues/394
.. _#391: https://github.com/kiwicom/schemathesis/issues/391
.. _#386: https://github.com/kiwicom/schemathesis/issues/386
.. _#383: https://github.com/kiwicom/schemathesis/issues/383
.. _#381: https://github.com/kiwicom/schemathesis/issues/381
.. _#378: https://github.com/kiwicom/schemathesis/issues/378
.. _#376: https://github.com/kiwicom/schemathesis/issues/376
.. _#374: https://github.com/kiwicom/schemathesis/issues/374
.. _#371: https://github.com/kiwicom/schemathesis/issues/371
.. _#367: https://github.com/kiwicom/schemathesis/issues/367
.. _#365: https://github.com/kiwicom/schemathesis/issues/365
.. _#361: https://github.com/kiwicom/schemathesis/issues/361
.. _#350: https://github.com/kiwicom/schemathesis/issues/350
.. _#349: https://github.com/kiwicom/schemathesis/issues/349
.. _#338: https://github.com/kiwicom/schemathesis/issues/338
.. _#335: https://github.com/kiwicom/schemathesis/issues/335
.. _#332: https://github.com/kiwicom/schemathesis/issues/332
.. _#330: https://github.com/kiwicom/schemathesis/issues/330
.. _#329: https://github.com/kiwicom/schemathesis/issues/329
.. _#322: https://github.com/kiwicom/schemathesis/issues/322
.. _#319: https://github.com/kiwicom/schemathesis/issues/319
.. _#315: https://github.com/kiwicom/schemathesis/issues/315
.. _#314: https://github.com/kiwicom/schemathesis/issues/314
.. _#313: https://github.com/kiwicom/schemathesis/issues/313
.. _#311: https://github.com/kiwicom/schemathesis/issues/311
.. _#308: https://github.com/kiwicom/schemathesis/issues/308
.. _#305: https://github.com/kiwicom/schemathesis/issues/305
.. _#303: https://github.com/kiwicom/schemathesis/issues/303
.. _#301: https://github.com/kiwicom/schemathesis/issues/301
.. _#295: https://github.com/kiwicom/schemathesis/issues/295
.. _#294: https://github.com/kiwicom/schemathesis/issues/294
.. _#286: https://github.com/kiwicom/schemathesis/issues/286
.. _#282: https://github.com/kiwicom/schemathesis/issues/282
.. _#280: https://github.com/kiwicom/schemathesis/issues/280
.. _#272: https://github.com/kiwicom/schemathesis/issues/272
.. _#270: https://github.com/kiwicom/schemathesis/issues/270
.. _#268: https://github.com/kiwicom/schemathesis/issues/268
.. _#267: https://github.com/kiwicom/schemathesis/issues/267
.. _#266: https://github.com/kiwicom/schemathesis/issues/266
.. _#261: https://github.com/kiwicom/schemathesis/issues/261
.. _#256: https://github.com/kiwicom/schemathesis/issues/256
.. _#255: https://github.com/kiwicom/schemathesis/issues/255
.. _#254: https://github.com/kiwicom/schemathesis/issues/254
.. _#253: https://github.com/kiwicom/schemathesis/issues/253
.. _#248: https://github.com/kiwicom/schemathesis/issues/248
.. _#246: https://github.com/kiwicom/schemathesis/issues/246
.. _#237: https://github.com/kiwicom/schemathesis/issues/237
.. _#236: https://github.com/kiwicom/schemathesis/issues/236
.. _#218: https://github.com/kiwicom/schemathesis/issues/218
.. _#216: https://github.com/kiwicom/schemathesis/issues/216
.. _#215: https://github.com/kiwicom/schemathesis/issues/215
.. _#214: https://github.com/kiwicom/schemathesis/issues/214
.. _#212: https://github.com/kiwicom/schemathesis/issues/212
.. _#211: https://github.com/kiwicom/schemathesis/issues/211
.. _#209: https://github.com/kiwicom/schemathesis/issues/209
.. _#207: https://github.com/kiwicom/schemathesis/issues/207
.. _#206: https://github.com/kiwicom/schemathesis/issues/206
.. _#204: https://github.com/kiwicom/schemathesis/issues/204
.. _#203: https://github.com/kiwicom/schemathesis/issues/203
.. _#200: https://github.com/kiwicom/schemathesis/issues/200
.. _#199: https://github.com/kiwicom/schemathesis/issues/199
.. _#197: https://github.com/kiwicom/schemathesis/issues/197
.. _#196: https://github.com/kiwicom/schemathesis/issues/196
.. _#194: https://github.com/kiwicom/schemathesis/issues/194
.. _#191: https://github.com/kiwicom/schemathesis/issues/191
.. _#189: https://github.com/kiwicom/schemathesis/issues/189
.. _#188: https://github.com/kiwicom/schemathesis/issues/188
.. _#181: https://github.com/kiwicom/schemathesis/issues/181
.. _#173: https://github.com/kiwicom/schemathesis/issues/173
.. _#172: https://github.com/kiwicom/schemathesis/issues/172
.. _#167: https://github.com/kiwicom/schemathesis/issues/167
.. _#153: https://github.com/kiwicom/schemathesis/issues/153
.. _#149: https://github.com/kiwicom/schemathesis/issues/149
.. _#147: https://github.com/kiwicom/schemathesis/issues/147
.. _#144: https://github.com/kiwicom/schemathesis/issues/144
.. _#139: https://github.com/kiwicom/schemathesis/issues/139
.. _#138: https://github.com/kiwicom/schemathesis/issues/138
.. _#137: https://github.com/kiwicom/schemathesis/issues/137
.. _#134: https://github.com/kiwicom/schemathesis/issues/134
.. _#130: https://github.com/kiwicom/schemathesis/issues/130
.. _#127: https://github.com/kiwicom/schemathesis/issues/127
.. _#126: https://github.com/kiwicom/schemathesis/issues/126
.. _#125: https://github.com/kiwicom/schemathesis/issues/125
.. _#121: https://github.com/kiwicom/schemathesis/issues/121
.. _#119: https://github.com/kiwicom/schemathesis/issues/119
.. _#118: https://github.com/kiwicom/schemathesis/issues/118
.. _#115: https://github.com/kiwicom/schemathesis/issues/115
.. _#110: https://github.com/kiwicom/schemathesis/issues/110
.. _#109: https://github.com/kiwicom/schemathesis/issues/109
.. _#107: https://github.com/kiwicom/schemathesis/issues/107
.. _#106: https://github.com/kiwicom/schemathesis/issues/106
.. _#101: https://github.com/kiwicom/schemathesis/issues/101
.. _#99: https://github.com/kiwicom/schemathesis/issues/99
.. _#98: https://github.com/kiwicom/schemathesis/issues/98
.. _#94: https://github.com/kiwicom/schemathesis/issues/94
.. _#92: https://github.com/kiwicom/schemathesis/issues/92
.. _#91: https://github.com/kiwicom/schemathesis/issues/91
.. _#90: https://github.com/kiwicom/schemathesis/issues/90
.. _#78: https://github.com/kiwicom/schemathesis/issues/78
.. _#75: https://github.com/kiwicom/schemathesis/issues/75
.. _#69: https://github.com/kiwicom/schemathesis/issues/69
.. _#64: https://github.com/kiwicom/schemathesis/issues/64
.. _#58: https://github.com/kiwicom/schemathesis/issues/58
.. _#55: https://github.com/kiwicom/schemathesis/issues/55
.. _#45: https://github.com/kiwicom/schemathesis/issues/45
.. _#40: https://github.com/kiwicom/schemathesis/issues/40
.. _#35: https://github.com/kiwicom/schemathesis/issues/35
.. _#34: https://github.com/kiwicom/schemathesis/issues/34
.. _#31: https://github.com/kiwicom/schemathesis/issues/31
.. _#30: https://github.com/kiwicom/schemathesis/issues/30
.. _#29: https://github.com/kiwicom/schemathesis/issues/29
.. _#28: https://github.com/kiwicom/schemathesis/issues/28
.. _#24: https://github.com/kiwicom/schemathesis/issues/24
.. _#21: https://github.com/kiwicom/schemathesis/issues/21
.. _#18: https://github.com/kiwicom/schemathesis/issues/18
.. _#17: https://github.com/kiwicom/schemathesis/issues/17
.. _#16: https://github.com/kiwicom/schemathesis/issues/16
.. _#10: https://github.com/kiwicom/schemathesis/issues/10
.. _#7: https://github.com/kiwicom/schemathesis/issues/7
.. _#6: https://github.com/kiwicom/schemathesis/issues/6
