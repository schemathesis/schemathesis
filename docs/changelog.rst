.. _changelog:

Changelog
=========

`Unreleased`_
-------------

Added
~~~~~

- WSGI apps support. `#31`_
- ``Case.verify_response`` for running built-in checks against app's response. `#319`_

Changed
~~~~~~~

- Checks receive ``Case`` instance as a second argument instead of ``TestResult``.
  This was done for making checks usable in Python tests via ``Case.verify_response``.
  Endpoint and schema are accessible via `case.endpoint` and `case.endpoint.schema`.

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

.. _Unreleased: https://github.com/kiwicom/schemathesis/compare/v0.19.1...HEAD
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

.. _#322: https://github.com/kiwicom/schemathesis/issues/322
.. _#319: https://github.com/kiwicom/schemathesis/issues/319
.. _#315: https://github.com/kiwicom/schemathesis/issues/315
.. _#311: https://github.com/kiwicom/schemathesis/issues/311
.. _#314: https://github.com/kiwicom/schemathesis/issues/314
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
