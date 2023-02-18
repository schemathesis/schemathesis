Changelog
=========

`Unreleased`_ - TBD
-------------------

.. _v3.18.5:

`3.18.5`_ - 2023-02-18
----------------------

**Added**

- Support for specifying the path to load hooks from via the ``SCHEMATHESIS_HOOKS`` environment variable. `#1702`.

**Deprecated**

- Use of the ``--pre-run`` CLI option for loading hooks. Use the ``SCHEMATHESIS_HOOKS`` environment variable instead.

.. _v3.18.4:

`3.18.4`_ - 2023-02-16
----------------------

**Changed**

- Support any Werkzeug 2.x in order to allow mitigation of `CVE-2023-25577 <https://github.com/advisories/GHSA-xg9f-g7g7-2323>`_. `#1695`_

.. _v3.18.3:

`3.18.3`_ - 2023-02-12
----------------------

**Added**

- ``APIStateMachine.run`` method to simplify running stateful tests.

**Changed**

- Improved quality of generated test sequences by updating state machines in Schemathesis to always run a minimum of two steps during testing. `#1627`_
  If you use ``hypothesis.stateful.run_state_machine_as_test`` to run your stateful tests, please use the ``run`` method on your state machine class instead.
  This change requires upgrading ``Hypothesis`` to at least version ``6.68.1``.

.. _v3.18.2:

`3.18.2`_ - 2023-02-08
----------------------

**Performance**

- Modify values in-place inside built-in ``map`` functions as there is no need to copy them.
- Update ``hypothesis-jsonschema`` to ``0.22.1`` for up to 30% faster data generation in some workflows.

.. _v3.18.1:

`3.18.1`_ - 2023-02-06
----------------------

**Changed**

- Stateful testing: Only make stateful requests when stateful data is available from another operation.
  This change significantly reduces the number of API calls that likely will fail because of absense of stateful data. `#1669`_

**Performance**

- Do not merge component schemas into the currently tested schema if they are not referenced by it. Originally all
  schemas were merged to make them visible to ``hypothesis-jsonschema``, but they imply significant overhead. `#1180`_
- Use a faster, specialized version of ``deepcopy``.

.. _v3.18.0:

`3.18.0`_ - 2023-02-01
----------------------

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
- Relax requirements for ``attrs``. `#1643`_
- Avoid occasional empty lines in cassettes.

**Deprecated**

- ``schemathesis.register_check`` in favor of ``schemathesis.check``.
- ``schemathesis.register_target`` in favor of ``schemathesis.target``.
- ``schemathesis.register_string_format`` in favor of ``schemathesis.openapi.format``.
- ``schemathesis.graphql.register_scalar`` in favor of ``schemathesis.graphql.scalar``.
- ``schemathesis.auth.register`` in favor of ``schemathesis.auth``.

**Fixed**

- Remove recursive references from the last reference resolution level.
  It works on the best effort basis and does not cover all possible cases. `#947`_
- Invalid cassettes when headers contain characters with a special meaning in YAML.
- Properly display flaky deadline errors.
- Internal error when the ``utf8_bom`` fixup is used for WSGI apps.
- Printing header that are set explicitly via ``get_call_kwargs`` in stateful testing. `#828`_
- Display all explicitly defined headers in the generated cURL command.
- Replace ``starlette.testclient.TestClient`` with ``starlette_testclient.TestClient`` to keep compatibility with newer
  ``starlette`` versions. `#1637`_

**Performance**

- Running negative tests filters out less data.
- Schema loading: Try a faster loader first if an HTTP response or a file is expected to be JSON.

.. _v3.17.5:

`3.17.5`_ - 2022-11-08
----------------------

**Added**

- Python 3.11 support. `#1632`_

**Fixed**

- Allow ``Werkzeug<=2.2.2``. `#1631`_

.. _v3.17.4:

`3.17.4`_ - 2022-10-19
----------------------

**Fixed**

- Appending an extra slash to the ``/`` path. `#1625`_

.. _v3.17.3:

`3.17.3`_ - 2022-10-10
----------------------

**Fixed**

- Missing ``httpx`` dependency. `#1614`_

.. _v3.17.2:

`3.17.2`_ - 2022-08-27
----------------------

**Fixed**

- Insufficient timeout for report uploads.

.. _v3.17.1:

`3.17.1`_ - 2022-08-19
----------------------

**Changed**

- Support ``requests==2.28.1``.

.. _v3.17.0:

`3.17.0`_ - 2022-08-17
----------------------

**Added**

- Support for exception groups in newer ``Hypothesis`` versions. `#1592`_
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

`3.16.5`_ - 2022-08-11
----------------------

**Fixed**

- CLI: Hanging on ``CTRL-C`` when ``--report`` is enabled.
- Internal error when GraphQL schema has its root types renamed. `#1591`_

.. _v3.16.4:

`3.16.4`_ - 2022-08-09
----------------------

**Changed**

- Suggest using ``--wait-for-schema`` if API schema is not available.

.. _v3.16.3:

`3.16.3`_ - 2022-08-08
----------------------

**Added**

- CLI: ``--max-failures=N`` option to exit after first ``N`` failures or errors. `#1580`_
- CLI: ``--wait-for-schema=N`` option to automatically retry schema loading for ``N`` seconds. `#1582`_
- CLI: Display old and new payloads in ``st replay`` when the ``-v`` option is passed. `#1584`_

**Fixed**

- Internal error on generating negative tests for query parameters with ``explode: true``.

.. _v3.16.2:

`3.16.2`_ - 2022-08-05
----------------------

**Added**

- CLI: Warning if **ALL** API responses are HTTP 404.
- The ``after_load_schema`` hook, which is designed for modifying the loaded API schema before running tests.
  For example, you can use it to add Open API links to your schema via ``schema.add_link``.
- New ``utf8_bom`` fixup. It helps to mitigate JSON decoding errors inside the ``response_schema_conformance`` check when payload contains BOM. `#1563`_

**Fixed**

- Description of ``-v`` or ``--verbosity`` option for CLI.

**Changed**

- Execute ``before_call`` / ``after_call`` hooks inside the ``call_*`` methods. It makes them available for the ``pytest`` integration.

.. _v3.16.1:

`3.16.1`_ - 2022-07-29
----------------------

**Added**

- CLI: Warning if the API returns too many HTTP 401.
- Add ``SCHEMATHESIS_BASE_URL`` environment variable for specifying ``--base-url`` in CLI.
- Collect anonymyzed CLI usage telemetry when reports are uploaded. We do not collect any free-form values you use in your CLI,
  except for header names. Instead, we measure how many times you use each free-form option in this command.
  Additionally we count all non-default hook types only by hook name.

.. important::

  You can disable usage this with the ``--schemathesis-io-telemetry=false`` CLI option or the ``SCHEMATHESIS_TELEMETRY=false`` environment variable.

.. _v3.16.0:

`3.16.0`_ - 2022-07-22
----------------------

**Added**

- Report uploading to Schemathesis.io via the ``--report`` CLI option.

**Changed**

- Do not validate schemas by default in the ``pytest`` integration.
- CLI: Display test run environment metadata only if ``-v`` is provided.
- CLI: Do not display headers automatically added by ``requests`` in code samples.

**Fixed**

- Do not report optional headers as missing.
- Compatibility with ``hypothesis>=6.49``. `#1538`_
- Handling of ``unittest.case.SkipTest`` emitted by newer Hypothesis versions.
- Generating invalid headers when their schema has ``array`` or ``object`` types.

**Removed**

- Previously, data was uploaded to Schemathesis.io when the proper credentials were specified. This release removes this behavior.
  From now on, every upload requires the explicit ``--report`` CLI option.
- Textual representation of HTTP requests in CLI output in order to decrease verbosity and avoid showing the same data
  in multiple places.

.. _v3.15.6:

`3.15.6`_ - 2022-06-23
----------------------

**Fixed**

- Do not discard dots (``.``) in OpenAPI expressions during parsing.

.. _v3.15.5:

`3.15.5`_ - 2022-06-21
----------------------

**Fixed**

- ``TypeError`` when using ``--auth-type=digest`` in CLI.

.. _v3.15.4:

`3.15.4`_ - 2022-06-06
----------------------

**Added**

- Support generating data for Open API request payloads with wildcard media types. `#1526`_

**Changed**

- Mark tests as skipped if there are no explicit examples and ``--hypothesis-phases=explicit`` is used. `#1323`_
- Parse all YAML mapping keys as strings, ignoring the YAML grammar rules. For example, ``on: true`` will be parsed as ``{"on": True}`` instead of ``{True: True}``.
  Even though YAML does not restrict keys to strings, in the Open API and JSON Schema context, this restriction is implied because the underlying data model
  comes from JSON.
- **INTERNAL**: Improve flexibility of event serialization.
- **INTERNAL**: Store request / response history in ``SerializedCheck``.

.. _v3.15.3:

`3.15.3`_ - 2022-05-28
----------------------

**Fixed**

- Deduplication of failures caused by malformed JSON payload. `#1518`_
- Do not re-raise ``InvalidArgument`` exception as ``InvalidSchema`` in non-Schemathesis tests. `#1514`_

.. _v3.15.2:

`3.15.2`_ - 2022-05-09
----------------------

**Fixed**

- Avoid generating negative query samples that ``requests`` will treat as an empty query.
- Editable installation via ``pip``.

.. _v3.15.1:

`3.15.1`_ - 2022-05-03
----------------------

**Added**

- **OpenAPI**: Expose ``APIOperation.get_security_requirements`` that returns a list of security requirements applied to the API operation
- Attach originally failed checks to "grouped" exceptions.

**Fixed**

- Internal error when Schemathesis doesn't have permission to create its ``hosts.toml`` file.
- Do not show internal Hypothesis warning multiple times when the Hypothesis database directory is not usable.
- Do not print not relevant Hypothesis reports when run in CI.
- Invalid ``verbose_name`` value in ``SerializedCase`` for GraphQL tests.

.. _v3.15.0:

`3.15.0`_ - 2022-05-01
----------------------

**Added**

- **GraphQL**: Mutations supports. Schemathesis will generate random mutations by default from now on.
- **GraphQL**: Support for registering strategies to generate custom scalars.
- Custom auth support for schemas created via ``from_pytest_fixture``.

**Changed**

- Do not encode payloads in cassettes as base64 by default. This change makes Schemathesis match the default Ruby's VCR behavior and
  leads to more human-readable cassettes. Use ``--cassette-preserve-exact-body-bytes`` to restore the old behavior. `#1413`_
- Bump ``hypothesis-graphql`` to ``0.9.0``.
- Avoid simultaneous authentication requests inside auth providers when caching is enabled.
- Reduce the verbosity of ``pytest`` output. A few internal frames and the "Falsifying example" block are removed from the output.
- Skip negative tests on API operations that are not possible to negate. `#1463`_
- Make it possible to generate negative tests if at least one parameter can be negated.
- Treat flaky errors as failures and display full report about the failure. `#1081`_
- Do not duplicate failing explicit example in the `HYPOTHESIS OUTPUT` CLI output section. `#881`_

**Fixed**

- **GraphQL**: Semantically invalid queries without aliases.
- **GraphQL**: Rare crashes on invalid schemas.
- Internal error inside ``BaseOpenAPISchema.validate_response`` on ``requests>=2.27`` when response body contains malformed JSON. `#1485`_
- ``schemathesis.from_pytest_fixture``: Display each failure if Hypothesis found multiple of them.

**Performance**

- **GraphQL**: Over 2x improvement from internal optimizations.

.. _v3.14.2:

`3.14.2`_ - 2022-04-21
----------------------

**Added**

- Support for auth customization & automatic refreshing. `#966`_

.. _v3.14.1:

`3.14.1`_ - 2022-04-18
----------------------

**Fixed**

- Using ``@schema.parametrize`` with test methods on ``pytest>=7.0``.

.. _v3.14.0:

`3.14.0`_ - 2022-04-17
----------------------

**Added**

- Open API link name customization via the ``name`` argument to ``schema.add_link``.
- ``st`` as an alias to the ``schemathesis`` command line entrypoint.
- ``st auth login`` / ``st auth logout`` to authenticate with Schemathesis.io.
- ``X-Schemathesis-TestCaseId`` header to help to distinguish test cases on the application side. `#1303`_
- Support for comma separated lists in the ``--checks`` CLI option. `#1373`_
- Hypothesis Database configuration for CLI via the ``--hypothesis-database`` option. `#1326`_
- Make the ``SCHEMA`` CLI argument accept API names from Schemathesis.io.

**Changed**

- Enable Open API links traversal by default. To disable it, use ``--stateful=none``.
- Do not validate API schema by default. To enable it back, use ``--validate-schema=true``.
- Add the ``api_name`` CLI argument to upload data to Schemathesis.io.
- Show response status code on failing checks output in CLI.
- Improve error message on malformed Open API path templates (like ``/foo}/``). `#1372`_
- Improve error message on malformed media types that appear in the schema or in response headers. `#1382`_
- Relax dependencies on ``pyyaml`` and ``click``.
- Add ``--cassette-path`` that is going to replace ``--store-network-log``. The old option is deprecated and will be removed in Schemathesis ``4.0``

**Fixed**

- Show the proper Hypothesis configuration in the CLI output. `#1445`_
- Missing ``source`` attribute in the ``Case.partial_deepcopy`` implementation. `#1429`_
- Duplicated failure message from ``content_type_conformance`` and ``response_schema_conformance`` checks when the checked response has no ``Content-Type`` header. `#1394`_
- Not copied ``case`` & ``response`` inside ``Case.validate_response``.
- Ignored ``pytest.mark`` decorators when they are applied before ``schema.parametrize`` if the schema is created via ``from_pytest_fixture``. `#1378`_

.. _v3.13.9:

`3.13.9`_ - 2022-04-14
----------------------

**Fixed**

- Compatibility with ``pytest-asyncio>=0.17.1``. `#1452`_

.. _v3.13.8:

`3.13.8`_ - 2022-04-05
----------------------

**Fixed**

- Missing ``media_type`` in the ``Case.partial_deepcopy`` implementation. It led to missing payload in failure reproduction code samples.

.. _v3.13.7:

`3.13.7`_ - 2022-04-02
----------------------

**Added**

- Support for ``Hypothesis>=6.41.0``. `#1425`_

.. _v3.13.6:

`3.13.6`_ - 2022-03-31
----------------------

**Changed**

- Deep-clone ``Response`` instances before passing to check functions.

.. _v3.13.5:

`3.13.5`_ - 2022-03-31
----------------------

**Changed**

- Deep-clone ``Case`` instances before passing to check functions.

.. _v3.13.4:

`3.13.4`_ - 2022-03-29
----------------------

**Added**

- Support for ``Werkzeug>=2.1.0``. `#1410`_

**Changed**

- Validate ``requests`` kwargs to catch cases when the ASGI integration is used, but the proper ASGI client is not supplied. `#1335`_

.. _v3.13.3:

`3.13.3`_ - 2022-02-20
----------------------

**Added**

- ``--request-tls-verify`` CLI option for the ``replay`` command. It controls whether Schemathesis verifies the server's TLS certificate.
  You can also pass the path to a CA_BUNDLE file for private certs. `#1395`_
- Support for client certificate authentication with ``--request-cert`` and ``--request-cert-key`` arguments for the ``replay`` command.

.. _v3.13.2:

`3.13.2`_ - 2022-02-16
----------------------

**Changed**

- Use Schemathesis default User-Agent when communicating with SaaS.

**Fixed**

- Use the same ``correlation_id`` in ``BeforeExecution`` and ``AfterExecution`` events if the API schema contains an error that
  causes an ``InvalidSchema`` exception during test execution.
- Use ``full_path`` in error messages in recoverable schema-level errors. It makes events generated in such cases consistent with usual events.

.. _v3.13.1:

`3.13.1`_ - 2022-02-10
----------------------

**Added**

- ``APIOperation.iter_parameters`` helper to iterate over all parameters.

**Fixed**

- Properly handle error if Open API parameter doesn't have ``content`` or ``schema`` keywords.

.. _v3.13.0:

`3.13.0`_ - 2022-02-09
----------------------

**Changed**

- Update integration with Schemathesis.io.
- Always show traceback for errors in Schemathesis.io integration.

.. _v3.12.3:

`3.12.3`_ - 2022-01-13
----------------------

**Fixed**

- Generating illegal unicode surrogates in queries. `#1370`_

.. _v3.12.2:

`3.12.2`_ - 2022-01-12
----------------------

**Fixed**

- Not-escaped single quotes in generated Python code samples. `#1359`_

.. _v3.12.1:

`3.12.1`_ - 2021-12-31
----------------------

**Fixed**

- Improper handling of ``base_url`` in ``call_asgi``, when the base URL has a non-empty base path. `#1366`_

.. _v3.12.0:

`3.12.0`_ - 2021-12-29
----------------------

**Changed**

- Upgrade ``typing-extensions`` to ``>=3.7,<5``.
- Upgrade ``jsonschema`` to ``^4.3.2``.
- Upgrade ``hypothesis-jsonschema`` to ``>=0.22.0``.

**Fixed**

- Generating values not compliant with the ECMAScript regex syntax. `#1350`_, `#1241`_.

**Removed**

- Support for Python 3.6.

.. _v3.11.7:

`3.11.7`_ - 2021-12-23
----------------------

**Added**

- Support for Python 3.10. `#1292`_

.. _v3.11.6:

`3.11.6`_ - 2021-12-20
----------------------

**Added**

- Support for client certificate authentication with ``--request-cert`` and ``--request-cert-key`` arguments. `#1173`_
- Support for ``readOnly`` and ``writeOnly`` Open API keywords. `#741`_

.. _v3.11.5:

`3.11.5`_ - 2021-12-04
----------------------

**Changed**

- Generate tests for API operations with the HTTP ``TRACE`` method on Open API 2.0.

.. _v3.11.4:

`3.11.4`_ - 2021-12-03
----------------------

**Changed**

- Add `AfterExecution.data_generation_method`.
- Minor changes to the Schemathesis.io integration.

.. _v3.11.3:

`3.11.3`_ - 2021-12-02
----------------------

**Fixed**

- Silently failing to detect numeric status codes when the schema contains a shared ``parameters`` key. `#1343`_
- Not raising an error when tests generated by schemas loaded with ``from_pytest_fixture`` match no API operations. `#1342`_

.. _v3.11.2:

`3.11.2`_ - 2021-11-30
----------------------

**Changed**

- Use ``name`` & ``data_generation_method`` parameters to subtest context instead of ``path`` & ``method``.
  It allows the end-user to disambiguate among subtest reports.
- Raise an error if a test function wrapped with ``schema.parametrize`` matches no API operations. `#1336`_

**Fixed**

- Handle ``KeyboardInterrupt`` that happens outside of the main test loop inside the runner.
  It makes interrupt handling consistent, independent at what point it happens. `#1325`_
- Respect the ``data_generation_methods`` config option defined on a schema instance when it is loaded via ``from_pytest_fixture``. `#1331`_
- Ignored hooks defined on a schema instance when it is loaded via ``from_pytest_fixture``. `#1340`_

.. _v3.11.1:

`3.11.1`_ - 2021-11-20
----------------------

**Changed**

- Update ``click`` and ``PyYaml`` dependency versions. `#1328`_

.. _v3.11.0:

`3.11.0`_ - 2021-11-03
----------------------

**Changed**

- Show ``cURL`` code samples by default instead of Python. `#1269`_
- Improve reporting of ``jsonschema`` errors which are caused by non-string object keys.
- Store ``data_generation_method`` in ``BeforeExecution``.
- Use case-insensitive dictionary for ``Case.headers``. `#1280`_

**Fixed**

- Pass ``data_generation_method`` to ``Case`` for GraphQL schemas.
- Generation of invalid headers in some cases. `#1142`_
- Unescaped quotes in generated Python code samples on some schemas. `#1030`_

**Performance**

- Dramatically improve CLI startup performance for large API schemas.
- Open API 3: Inline only ``components/schemas`` before passing schemas to ``hypothesis-jsonschema``.
- Generate tests on demand when multiple workers are used during CLI runs. `#1287`_

.. _v3.10.1:

`3.10.1`_ - 2021-10-04
----------------------

**Added**

- ``DataGenerationMethod.all`` shortcut to get all possible enum variants.

**Fixed**

- Unresolvable dependency due to incompatible changes in the new ``hypothesis-jsonschema`` release. `#1290`_

.. _v3.10.0:

`3.10.0`_ - 2021-09-13
----------------------

**Added**

- Optional integration with Schemathesis.io.
- New ``before_init_operation`` hook.
- **INTERNAL**. ``description`` attribute for all parsed parameters inside ``APIOperation``.
- Timeouts when loading external schema components or external examples.

**Changed**

- Pin ``werkzeug`` to ``>=0.16.0``.
- **INTERNAL**. ``OpenAPI20CompositeBody.definition`` type to ``List[OpenAPI20Parameter]``.
- Open API schema loaders now also accept single ``DataGenerationMethod`` instances for the ``data_generation_methods`` argument. `#1260`_
- Improve error messages when the loaded API schema is not in JSON or YAML. `#1262`_

**Fixed**

- Internal error in ``make_case`` calls for GraphQL schemas.
- ``TypeError`` on ``case.call`` with bytes data on GraphQL schemas.
- Worker threads may not be immediately stopped on SIGINT. `#1066`_
- Re-used referenced objects during inlining. Now they are independent.
- Rewrite not resolved remote references to local ones. `#986`_
- Stop worker threads on failures with ``exit_first`` enabled. `#1204`_
- Properly report all failures when custom checks are passed to ``case.validate_response``.

**Performance**

- Avoid using filters for header values when is not necessary.

.. _v3.9.7:

`3.9.7`_ - 2021-07-26
---------------------

**Added**

- New ``process_call_kwargs`` CLI hook. `#1233`_

**Changed**

- Check non-string response status codes when Open API links are collected. `#1226`_

.. _v3.9.6:

`3.9.6`_ - 2021-07-15
---------------------

**Added**

- New ``before_call`` and ``after_call`` CLI hooks. `#1224`_, `#700`_

.. _v3.9.5:

`3.9.5`_ - 2021-07-14
---------------------

**Fixed**

- Preserve non-body parameter types in requests during Open API runtime expression evaluation.

.. _v3.9.4:

`3.9.4`_ - 2021-07-09
---------------------

**Fixed**

- ``KeyError`` when the ``response_schema_conformance`` check is executed against responses without schema definition. `#1220`_
- ``TypeError`` during negative testing on Open API schemas with parameters that have non-default ``style`` value. `#1208`_

.. _v3.9.3:

`3.9.3`_ - 2021-06-22
---------------------

**Added**

- ``ExecutionEvent.is_terminal`` attribute that indicates whether an event is the last one in the stream.

**Fixed**

- When ``EventStream.stop`` is called, the next event always is the last one.

.. _v3.9.2:

`3.9.2`_ - 2021-06-16
---------------------

**Changed**

- Return ``response`` from ``Case.call_and_validate``.

**Fixed**

- Incorrect deduplication applied to response schema conformance failures that happen to have the same failing validator but different input values. `#907`_

.. _v3.9.1:

`3.9.1`_ - 2021-06-13
---------------------

**Changed**

- ``ExecutionEvent.asdict`` adds the ``event_type`` field which is the event class name.
- Add API schema to the ``Initialized`` event.
- **Internal**: Add ``SerializedCase.cookies``
- Convert all ``FailureContext`` class attributes to instance attributes. For simpler serialization via ``attrs``.

.. _v3.9.0:

`3.9.0`_ - 2021-06-07
---------------------

**Added**

- GraphQL support in CLI. `#746`_
- A way to stop the Schemathesis runner's event stream manually via ``events.stop()`` / ``events.finish()`` methods. `#1202`_

**Changed**

- Avoid ``pytest`` warnings when internal Schemathesis classes are in the test module scope.

.. _v3.8.0:

`3.8.0`_ - 2021-06-03
---------------------

**Added**

- Negative testing. `#65`_
- ``Case.data_generation_method`` attribute that provides the information of the underlying data generation method (e.g. positive or negative)

**Changed**

- Raise ``UsageError`` if ``schema.parametrize`` or ``schema.given`` are applied to the same function more than once. `#1194`_
- Python values of ``True``, ``False`` and ``None`` are converted to their JSON equivalents when generated for path parameters or query. `#1166`_
- Bump ``hypothesis-jsonschema`` version. It allows the end-user to override known string formats.
- Bump ``hypothesis`` version.
- ``APIOperation.make_case`` behavior. If no ``media_type`` is passed along with ``body``, then it tries to infer the proper media type and raises an error if it is not possible. `#1094`_

**Fixed**

- Compatibility with ``hypothesis>=6.13.3``.

.. _v3.7.8:

`3.7.8`_ - 2021-06-02
---------------------

**Fixed**

- Open API ``style`` & ``explode`` for parameters derived from security definitions.

.. _v3.7.7:

`3.7.7`_ - 2021-06-01
---------------------

**Fixed**

- Apply the Open API's ``style`` & ``explode`` keywords to explicit examples. `#1190`_

.. _v3.7.6:

`3.7.6`_ - 2021-05-31
---------------------

**Fixed**

- Disable filtering optimization for headers when there are keywords other than ``type``. `#1189`_

.. _v3.7.5:

`3.7.5`_ - 2021-05-31
---------------------

**Fixed**

- Too much filtering in headers that have schemas with the ``pattern`` keyword. `#1189`_

.. _v3.7.4:

`3.7.4`_ - 2021-05-28
---------------------

**Changed**

- **Internal**: ``SerializedCase.path_template`` returns path templates as they are in the schema, without base path.

.. _v3.7.3:

`3.7.3`_ - 2021-05-28
---------------------

**Fixed**

- Invalid multipart payload generated for unusual schemas for the ``multipart/form-data`` media type.

**Performance**

- Reduce the amount of filtering needed to generate valid headers and cookies.

.. _v3.7.2:

`3.7.2`_ - 2021-05-27
---------------------

**Added**

- ``SerializedCase.media_type`` that stores the information about what media type was used for a particular case.

**Fixed**

- Internal error on unusual schemas for the ``multipart/form-data`` media type. `#1152`_
- Ignored explicit ``Content-Type`` override in ``Case.as_requests_kwargs``.

.. _v3.7.1:

`3.7.1`_ - 2021-05-23
---------------------

**Added**

- **Internal**: ``FailureContext.title`` attribute that gives a short failure description.
- **Internal**: ``FailureContext.message`` attribute that gives a longer failure description.

**Changed**

- Rename ``JSONDecodeErrorContext.message`` to ``JSONDecodeErrorContext.validation_message`` for consistency.
- Store the more precise ``schema`` & ``instance`` in ``ValidationErrorContext``.
- Rename ``ResponseTimeout`` to ``RequestTimeout``.

.. _v3.7.0:

`3.7.0`_ - 2021-05-23
---------------------

**Added**

- Additional context for each failure coming from the runner. It allows the end-user to customize failure formatting.

**Changed**

- Use different exception classes for ``not_a_server_error`` and ``status_code_conformance`` checks. It improves the variance of found errors.
- All network requests (not WSGI) now have the default timeout of 10 seconds. If the response is time-outing, Schemathesis will report it as a failure.
  It also solves the case when the tested app hangs. `#1164`_
- The default test duration deadline is extended to 15 seconds.

.. _v3.6.11:

`3.6.11`_ - 2021-05-20
----------------------

**Added**

- Internal: ``BeforeExecution.verbose_name`` & ``SerializedCase.verbose_name`` that reflect specification-specific API operation name.

.. _v3.6.10:

`3.6.10`_ - 2021-05-17
----------------------

**Changed**

- Explicitly add ``colorama`` to project's dependencies.
- Bump ``hypothesis-jsonschema`` version.

.. _v3.6.9:

`3.6.9`_ - 2021-05-14
---------------------

**Fixed**

- Ignored ``$ref`` keyword in schemas with deeply nested references. `#1167`_
- Ignored Open API specific keywords & types in schemas with deeply nested references. `#1162`_

.. _v3.6.8:

`3.6.8`_ - 2021-05-13
---------------------

**Changed**

- Relax dependency on ``starlette`` to ``>=0.13,<1``. `#1160`_

.. _v3.6.7:

`3.6.7`_ - 2021-05-12
---------------------

**Fixed**

- Missing support for the ``date`` string format (only ``full-date`` was supported).

.. _v3.6.6:

`3.6.6`_ - 2021-05-07
---------------------

**Changed**

- Improve error message for failing Hypothesis deadline healthcheck in CLI. `#880`_

.. _v3.6.5:

`3.6.5`_ - 2021-05-07
---------------------

**Added**

- Support for disabling ANSI color escape codes via the `NO_COLOR <https://no-color.org/>` environment variable or the ``--no-color`` CLI option. `#1153`_

**Changed**

- Generate valid header values for Bearer auth by construction rather than by filtering.

.. _v3.6.4:

`3.6.4`_ - 2021-04-30
---------------------

**Changed**

- Bump minimum ``hypothesis-graphql`` version to ``0.5.0``. It brings support for interfaces and unions and fixes a couple of bugs in query generation.

.. _v3.6.3:

`3.6.3`_ - 2021-04-20
---------------------

**Fixed**

- Bump minimum ``hypothesis-graphql`` version to ``0.4.1``. It fixes `a problem <https://github.com/Stranger6667/hypothesis-graphql/issues/30>`_ with generating queries with surrogate characters.
- ``UnicodeEncodeError`` when sending ``application/octet-stream`` payloads that have no ``format: binary`` in their schemas. `#1134`_

.. _v3.6.2:

`3.6.2`_ - 2021-04-15
---------------------

**Fixed**

- Windows: ``UnicodeDecodeError`` during schema loading via the ``from_path`` loader if it contains certain Unicode symbols.
  ``from_path`` loader defaults to `UTF-8` from now on.

.. _v3.6.1:

`3.6.1`_ - 2021-04-09
---------------------

**Fixed**

- Using parametrized ``pytest`` fixtures with the ``from_pytest_fixture`` loader. `#1121`_

.. _v3.6.0:

`3.6.0`_ - 2021-04-04
---------------------

**Added**

- Custom keyword arguments to ``schemathesis.graphql.from_url`` that are proxied to ``requests.post``.
- ``from_wsgi``, ``from_asgi``, ``from_path`` and ``from_file`` loaders for GraphQL apps. `#1097`_, `#1100`_
- Support for ``data_generation_methods`` and ``code_sample_style`` in all GraphQL loaders.
- Support for ``app`` & ``base_url`` arguments for the ``from_pytest_fixture`` runner.
- Initial support for GraphQL schemas in the Schemathesis runner.

.. code-block:: python

    import schemathesis

    # Load schema
    schema = schemathesis.graphql.from_url("http://localhost:8000/graphql")
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

- Add the missing ``@schema.given`` implementation for schemas created via the ``from_pytest_fixture`` loader. `#1093`_
- Silently ignoring some incorrect usages of ``@schema.given``.
- Fixups examples were using the incorrect fixup name.
- Return type of ``make_case`` for GraphQL schemas.
- Missed ``operation_id`` argument in ``from_asgi`` loader.

**Removed**

- Undocumented way to install fixups via the ``fixups`` argument for ``schemathesis.runner.prepare`` is removed.

.. _v3.5.3:

`3.5.3`_ - 2021-03-27
---------------------

**Fixed**

- Do not use `importlib-metadata==3.8` in dependencies as it causes ``RuntimeError``. Ref: https://github.com/python/importlib_metadata/issues/293

.. _v3.5.2:

`3.5.2`_ - 2021-03-24
---------------------

**Changed**

- Prefix worker thread names with ``schemathesis_``.

.. _v3.5.1:

`3.5.1`_ - 2021-03-23
---------------------

**Fixed**

- Encoding for response payloads displayed in the CLI output. `#1073`_
- Use actual charset (from ``flask.Response.mimetype_params``) when storing WSGI responses rather than defaulting to ``flask.Response.charset``.

.. _v3.5.0:

`3.5.0`_ - 2021-03-22
---------------------

**Added**

- ``before_generate_case`` hook, that allows the user to modify or filter generated ``Case`` instances. `#1067`_

**Fixed**

- Missing ``body`` parameters during Open API links processing in CLI. `#1069`_
- Output types for evaluation results of ``$response.body`` and ``$request.body`` runtime expressions. `#1068`_

.. _v3.4.1:

`3.4.1`_ - 2021-03-21
---------------------

**Added**

- ``event_type`` field to the debug output.

.. _v3.4.0:

`3.4.0`_ - 2021-03-20
---------------------

**Added**

- ``--debug-output-file`` CLI option to enable storing the underlying runner events in the JSON Lines format in a separate file for debugging purposes. `#1059`_

**Changed**

- Make ``Request.body``, ``Response.body`` and ``Response.encoding`` internal attributes optional. For ``Request``,
  it means that absent body will lead to ``Request.body`` to be ``None``. For ``Response``, ``body`` will be ``None``
  if the app response did not have any payload. Previously these values were empty strings, which was not distinguishable from the cases described above.
  For the end-user, it means that in VCR cassettes, fields ``request.body`` and ``response.body`` may be absent.
- ``models.Status`` enum now has string values for more readable representation.

.. _v3.3.1:

`3.3.1`_ - 2021-03-18
---------------------

**Fixed**

- Displaying wrong headers in the ``FAILURES`` block of the CLI output. `#792`_

.. _v3.3.0:

`3.3.0`_ - 2021-03-17
---------------------

**Added**

- Display failing response payload in the CLI output, similarly to the pytest plugin output. `#1050`_
- A way to control which code sample style to use - Python or cURL. `#908`_

**Fixed**

- ``UnicodeDecodeError`` when generating cURL commands for failed test case reproduction if the request's body contains non-UTF8 characters.

**Internal**

- Extra information to events, emitted by the Schemathesis runner.

.. _v3.2.2:

`3.2.2`_ - 2021-03-11
---------------------

**Added**

- Support for Hypothesis 6. `#1013`_

.. _v3.2.1:

`3.2.1`_ - 2021-03-10
---------------------

**Fixed**

- Wrong test results in some cases when the tested schema contains a media type that Schemathesis doesn't know how to work with. `#1046`_

.. _v3.2.0:

`3.2.0`_ - 2021-03-09
---------------------

**Performance**

- Add an internal caching layer for data generation strategies. It relies on the fact that the internal ``BaseSchema`` structure is not mutated over time.
  It is not directly possible through the public API and is discouraged from doing through hook functions.

**Changed**

- ``APIOperation`` and subclasses of ``Parameter`` are now compared by their identity rather than by value.

.. _v3.1.3:

`3.1.3`_ - 2021-03-08
---------------------

**Added**

- ``count_operations`` boolean flag to ``runner.prepare``. In case of ``False`` value, Schemathesis won't count the total number of operations upfront.
  It improves performance for the direct ``runner`` usage, especially on large schemas.
  Schemathesis CLI will still use these calculations to display the progress during execution, but this behavior may become configurable in the future.

.. _v3.1.2:

`3.1.2`_ - 2021-03-08
---------------------

**Fixed**

- Percent-encode the generated ``.`` and ``..`` strings in path parameters to avoid resolving relative paths and changing the tested path structure. `#1036`_

.. _v3.1.1:

`3.1.1`_ - 2021-03-05
---------------------

**Fixed**

- Loosen ``importlib-metadata`` version constraint and update pyproject.toml `#1039`_

.. _v3.1.0:

`3.1.0`_ - 2021-02-11
---------------------

**Added**

- Support for external examples via the ``externalValue`` keyword. `#884`_

**Fixed**

- Prevent a small terminal width causing a crash (due to negative length used in an f-string) when printing percentage
- Support the latest ``cryptography`` version in Docker images. `#1033`_

.. _v3.0.9:

`3.0.9`_ - 2021-02-10
---------------------

**Fixed**

- Return a default terminal size to prevent crashes on systems with zero-width terminals (some CI/CD servers).

.. _v3.0.8:

`3.0.8`_ - 2021-02-04
---------------------

- This release updates the documentation to be in-line with the current state.

.. _v3.0.7:

`3.0.7`_ - 2021-01-31
---------------------

**Fixed**

- Docker tags for Buster-based images.

.. _v3.0.6:

`3.0.6`_ - 2021-01-31
---------------------

- Packaging-only release for Docker images based on Debian Buster. `#1028`_

.. _v3.0.5:

`3.0.5`_ - 2021-01-30
---------------------

**Fixed**

- Allow to use any iterable type for ``checks`` and ``additional_checks`` arguments to ``Case.validate_response``.

.. _v3.0.4:

`3.0.4`_ - 2021-01-19
---------------------

**Fixed**

- Generating stateful tests, with common parameters behind a reference. `#1020`_
- Programmatic addition of Open API links via ``add_link`` when schema validation is disabled and response status codes
  are noted as integers. `#1022`_

**Changed**

- When operations are resolved by ``operationId`` then the same reference resolving logic is applied as in other cases.
  This change leads to less reference inlining and lower memory consumption for deeply nested schemas. `#945`_

.. _v3.0.3:

`3.0.3`_ - 2021-01-18
---------------------

**Fixed**

- ``Flaky`` Hypothesis error during explicit examples generation. `#1018`_

.. _v3.0.2:

`3.0.2`_ - 2021-01-15
---------------------

**Fixed**

- Processing parameters common for multiple API operations if they are behind a reference. `#1015`_

.. _v3.0.1:

`3.0.1`_ - 2021-01-15
---------------------

**Added**

- YAML serialization for ``text/yaml``, ``text/x-yaml``, ``application/x-yaml`` and ``text/vnd.yaml`` media types. `#1010`_.

.. _v3.0.0:

`3.0.0`_ - 2021-01-14
---------------------

**Added**

- Support for sending ``text/plain`` payload as test data. Including variants with non-default ``charset``. `#850`_, `#939`_
- Generating data for all media types defined for an operation. `#690`_
- Support for user-defined media types serialization. You can define how Schemathesis should handle media types defined
  in your schema or customize existing (like ``application/json``).
- The `response_schema_conformance` check now runs on media types that are encoded with JSON. For example, ``application/problem+json``. `#920`_
- Base URL for GraphQL schemas. It allows you to load the schema from one place but send test requests to another one. `#934`_
- A helpful error message when an operation is not found during the direct schema access. `#812`_
- ``--dry-run`` CLI option. When applied, Schemathesis won't send any data to the server and won't perform any response checks. `#963`_
- A better error message when the API schema contains an invalid regular expression syntax. `#1003`_

**Changed**

- Open API parameters parsing to unblock supporting multiple media types per operation. Their definitions aren't converted
  to JSON Schema equivalents right away but deferred instead and stored as-is.
- Missing ``required: true`` in path parameters definition is now automatically enforced if schema validation is disabled.
  According to the Open API spec, the ``required`` keyword value should be ``true`` for path parameters.
  This change allows Schemathesis to generate test cases even for endpoints containing optional path parameters (which is not compliant with the spec). `#941`_
- Using ``--auth`` together with ``--header`` that sets the ``Authorization`` header causes a validation error.
  Before, the ``--header`` value was ignored in such cases, and the basic auth passed in ``--auth`` was used. `#911`_
- When ``hypothesis-jsonschema`` fails to resolve recursive references, the test is skipped with an error message that indicates why it happens.
- Shorter error messages when API operations have logical errors in their schema. For example, when the maximum is less than the minimum - ``{"type": "integer", "minimum": 5, "maximum": 4}``.
- If multiple non-check related failures happens during a test of a single API operation, they are displayed as is, instead of Hypothesis-level error messages about multiple found failures or flaky tests. `#975`_
- Catch schema parsing errors, that are caused by YAML parsing.
- The built-in test server now accepts ``--operations`` instead of ``--endpoints``.
- Display ``Collected API operations`` instead of ``collected endpoints`` in the CLI. `#869`_
- ``--skip-deprecated-endpoints`` is renamed to ``--skip-deprecated-operations``. `#869`_
- Rename various internal API methods that contained ``endpoint`` in their names. `#869`_
- Bump ``hypothesis-jsonschema`` version to ``0.19.0``. This version improves the handling of unsupported regular expression syntax and can generate data for a subset of schemas containing such regular expressions.
- Schemathesis doesn't stop testing on errors during schema parsing. These errors are handled the same way as other errors
  during the testing process. It allows Schemathesis to test API operations with valid definitions and report problematic operations instead of failing the whole run. `#999`_

**Fixed**

- Allow generating requests without payload if the schema does not require it. `#916`_
- Allow sending ``null`` as request payload if the schema expects it. `#919`_
- CLI failure if the tested operation is `GET` and has payload examples. `#925`_
- Excessive reference inlining that leads to out-of-memory for large schemas with deep references. `#945`_, `#671`_
- ``--exitfirst`` CLI option trims the progress bar output when a failure occurs. `#951`_
- Internal error if filling missing explicit examples led to ``Unsatisfiable`` errors. `#904`_
- Do not suggest to disable schema validation if it is already disabled. `#914`_
- Skip explicit examples generation if this phase is disabled via config. `#905`_
- ``Unsatisfiable`` error in stateful testing caused by all API operations having inbound links. `#965`_, `#822`_
- A possibility to override ``APIStateMachine.step``. `#970`_
- ``TypeError`` on nullable parameters during Open API specific serialization. `#980`_
- Invalid types in ``x-examples``. `#982`_
- CLI crash on schemas with operation names longer than the current terminal width. `#990`_
- Handling of API operations that contain reserved characters in their paths. `#992`_
- CLI execution stops on errors during example generation. `#994`_
- Fill missing properties in incomplete explicit examples for non-body parameters. `#1007`_

**Deprecated**

- ``HookContext.endpoint``. Use ``HookContext.operation`` instead.
- ``Case.endpoint``. Use ``Case.operation`` instead.

**Performance**

- Use compiled versions of Open API spec validators.
- Decrease CLI memory usage. `#987`_
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

`2.8.6`_ - 2022-03-29
---------------------

**Added**

- Support for Werkzeug>=2.1.0. `#1410`_

.. _v2.8.5:

`2.8.5`_ - 2020-12-15
---------------------

**Added**

- ``auto`` variant for the ``--workers`` CLI option that automatically detects the number of available CPU cores to run tests on. `#917`_

.. _v2.8.4:

`2.8.4`_ - 2020-11-27
---------------------

**Fixed**

- Use ``--request-tls-verify`` during schema loading as well. `#897`_

.. _v2.8.3:

`2.8.3`_ - 2020-11-27
---------------------

**Added**

- Display failed response payload in the error output for the ``pytest`` plugin. `#895`_

**Changed**

- In pytest plugin output, Schemathesis error classes use the `CheckFailed` name. Before, they had not readable "internal" names.
- Hypothesis falsifying examples. The code does not include ``Case`` attributes with default values to improve readability. `#886`_

.. _v2.8.2:

`2.8.2`_ - 2020-11-25
---------------------

**Fixed**

- Internal error in CLI, when the ``base_url`` is an invalid IPv6. `#890`_
- Internal error in CLI, when a malformed regex is passed to ``-E`` / ``-M`` / ``-T`` / ``-O`` CLI options. `#889`_

.. _v2.8.1:

`2.8.1`_ - 2020-11-24
---------------------

**Added**

- ``--force-schema-version`` CLI option to force Schemathesis to use the specific Open API spec version when parsing the schema. `#876`_

**Changed**

- The ``content_type_conformance`` check now raises a well-formed error message when encounters a malformed media type value. `#877`_

**Fixed**

- Internal error during verifying explicit examples if an example has no ``value`` key. `#882`_

.. _v2.8.0:

`2.8.0`_ - 2020-11-24
---------------------

**Added**

- ``--request-tls-verify`` CLI option, that controls whether Schemathesis verifies the server's TLS certificate.
  You can also pass the path to a CA_BUNDLE file for private certs. `#830`_

**Changed**

- In CLI, if an endpoint contains an invalid schema, show a message about the ``--validate-schema`` CLI option. `#855`_

**Fixed**

- Handling of 204 responses in the ``response_schema_conformance`` check. Before, all responses were required to have the
  ``Content-Type`` header. `#844`_
- Catch ``OverflowError`` when an invalid regex is passed to ``-E`` / ``-M`` / ``-T`` / ``-O`` CLI options. `#870`_
- Internal error in CLI, when the schema location is an invalid IPv6. `#872`_
- Collecting Open API links behind references via CLI. `#874`_

**Deprecated**

- Using of ``Case.form_data`` and ``Endpoint.form_data``. In the ``3.0`` release, you'll need to use relevant ``body`` attributes instead.
  This change includes deprecation of the ``before_generate_form_data`` hook, use ``before_generate_body`` instead.
  The reason for this is the upcoming unification of parameter handling and their serialization.
- ``--stateful-recursion-limit``. It will be removed in ``3.0`` as a part of removing the old stateful testing approach.
  This parameter is no-op.

.. _v2.7.7:

`2.7.7`_ - 2020-11-13
---------------------

**Fixed**

- Missed ``headers`` in ``Endpoint.partial_deepcopy``.

.. _v2.7.6:

`2.7.6`_ - 2020-11-12
---------------------

**Added**

- An option to set data generation methods. At the moment, it includes only "positive", which means that Schemathesis will
  generate data that matches the schema.

**Fixed**

- Pinned dependency on ``attrs`` that caused an error on fresh installations. `#858`_

.. _v2.7.5:

`2.7.5`_ - 2020-11-09
---------------------

**Fixed**

- Invalid keyword in code samples that Schemathesis suggests to run to reproduce errors. `#851`_

.. _v2.7.4:

`2.7.4`_ - 2020-11-07
---------------------

**Added**

- New ``relative_path`` property for ``BeforeExecution`` and ``AfterExecution`` events. It represents an operation
  path as it is in the schema definition.

.. _v2.7.3:

`2.7.3`_ - 2020-11-05
---------------------

**Fixed**

- Internal error on malformed JSON when the ``response_conformance`` check is used. `#832`_

.. _v2.7.2:

`2.7.2`_ - 2020-11-05
---------------------

**Added**

- Shortcut for response validation when Schemathesis's data generation is not used. `#485`_

**Changed**

- Improve the error message when the application can not be loaded from the value passed to the ``--app`` command-line option. `#836`_
- Security definitions are now serialized as other parameters. At the moment, it means that the generated values
  will be coerced to strings, which is a no-op. However, types of security definitions might be affected by
  the "Negative testing" feature in the future. Therefore this change is mostly for future-compatibility. `#841`_

**Fixed**

- Internal error when a "header" / "cookie" parameter were not coerced to a string before filtration. `#839`_

.. _v2.7.1:

`2.7.1`_ - 2020-10-22
---------------------

**Fixed**

- Adding new Open API links via the ``add_link`` method, when the related PathItem contains a reference. `#824`_

.. _v2.7.0:

`2.7.0`_ - 2020-10-21
---------------------

**Added**

- New approach to stateful testing, based on the Hypothesis's ``RuleBasedStateMachine``. `#737`_
- ``Case.validate_response`` accepts the new ``additional_checks`` argument. It provides a way to execute additional checks in addition to existing ones.

**Changed**

- The ``response_schema_conformance`` and ``content_type_conformance`` checks fail unconditionally if the input response has no ``Content-Type`` header. `#816`_

**Fixed**

- Failure reproduction code missing values that were explicitly passed to ``call_*`` methods during testing. `#814`_

**Deprecated**

- Using ``stateful=Stateful.links`` in schema loaders and ``parametrize``. Use ``schema.as_state_machine().TestCase`` instead.
  The old approach to stateful testing will be removed in ``3.0``.
  See the ``Stateful testing`` section of our documentation for more information.

.. _v2.6.1:

`2.6.1`_ - 2020-10-19
---------------------

**Added**

- New method ``as_curl_command`` added to the ``Case`` class. `#689`_

.. _v2.6.0:

`2.6.0`_ - 2020-10-06
---------------------

**Added**

- Support for passing Hypothesis strategies to tests created with ``schema.parametrize`` by using ``schema.given`` decorator. `#768`_
- Support for PEP561. `#748`_
- Shortcut for calling & validation. `#738`_
- New hook to pre-commit, ``rstcheck``, as well as updates to documentation based on rstcheck. `#734`_
- New check for maximum response time and corresponding CLI option ``--max-response-time``. `#716`_
- New ``response_headers_conformance`` check that verifies the presence of all headers defined for a response. `#742`_
- New field with information about executed checks in cassettes. `#702`_
- New ``port`` parameter added to ``from_uri()`` method. `#706`_
- A code snippet to reproduce a failed check when running Python tests. `#793`_
- Python 3.9 support. `#731`_
- Ability to skip deprecated endpoints with ``--skip-deprecated-endpoints`` CLI option and ``skip_deprecated_operations=True`` argument to schema loaders. `#715`_

**Fixed**

- ``User-Agent`` header overriding the passed one. `#757`_
- Default ``User-Agent`` header in ``Case.call``. `#717`_
- Status of individual interactions in VCR cassettes. Before this change, all statuses were taken from the overall test outcome,
  rather than from the check results for a particular response. `#695`_
- Escaping header values in VCR cassettes. `#783`_
- Escaping HTTP response message in VCR cassettes. `#788`_

**Changed**

- ``Case.as_requests_kwargs`` and ``Case.as_werkzeug_kwargs`` now return the ``User-Agent`` header.
  This change also affects code snippets for failure reproduction - all snippets will include the ``User-Agent`` header.

**Performance**

- Speed up generation of ``headers``, ``cookies``, and ``formData`` parameters when their schemas do not define the ``type`` keyword. `#795`_

.. _v2.5.1:

`2.5.1`_ - 2020-09-30
---------------------

This release contains only documentation updates which are necessary to upload to PyPI.

.. _v2.5.0:

`2.5.0`_ - 2020-09-27
---------------------

**Added**

- Stateful testing via Open API links for the ``pytest`` runner. `#616`_
- Support for GraphQL tests for the ``pytest`` runner. `#649`_

**Fixed**

- Progress percentage in the terminal output for "lazy" schemas. `#636`_

**Changed**

- Check name is no longer displayed in the CLI output, since its verbose message is already displayed. This change
  also simplifies the internal structure of the runner events.
- The ``stateful`` argument type in the ``runner.prepare`` is ``Optional[Stateful]`` instead of ``Optional[str]``. Use
  ``schemathesis.Stateful`` enum.

.. _v2.4.1:

`2.4.1`_ - 2020-09-17
---------------------

**Changed**

- Hide ``Case.endpoint`` from representation. Its representation decreases the usability of the pytest's output. `#719`_
- Return registered functions from ``register_target`` and ``register_check`` decorators. `#721`_

**Fixed**

- Possible ``IndexError`` when a user-defined check raises an exception without a message. `#718`_

.. _v2.4.0:

`2.4.0`_ - 2020-09-15
---------------------

**Added**

- Ability to register custom targets for targeted testing. `#686`_

**Changed**

- The ``AfterExecution`` event now has ``path`` and ``method`` fields, similar to the ``BeforeExecution`` one.
  The goal is to make these events self-contained, which improves their usability.

.. _v2.3.4:

`2.3.4`_ - 2020-09-11
---------------------

**Changed**

- The default Hypothesis's ``deadline`` setting for tests with ``schema.parametrize`` is set to 500 ms for consistency with the CLI behavior. `#705`_

**Fixed**

- Encoding error when writing a cassette on Windows. `#708`_

.. _v2.3.3:

`2.3.3`_ - 2020-08-04
---------------------

**Fixed**

- ``KeyError`` during the ``content_type_conformance`` check if the response has no ``Content-Type`` header. `#692`_

.. _v2.3.2:

`2.3.2`_ - 2020-08-04
---------------------

**Added**

- Run checks conditionally.

.. _v2.3.1:

`2.3.1`_ - 2020-07-28
---------------------

**Fixed**

- ``IndexError`` when ``examples`` list is empty.

.. _v2.3.0:

`2.3.0`_ - 2020-07-26
---------------------

**Added**

- Possibility to generate values for ``in: formData`` parameters that are non-bytes or contain non-bytes (e.g., inside an array). `#665`_

**Changed**

- Error message for cases when a path parameter is in the template but is not defined in the parameters list or missing ``required: true`` in its definition. `#667`_
- Bump minimum required ``hypothesis-jsonschema`` version to `0.17.0`. This allows Schemathesis to use the ``custom_formats`` argument in ``from_schema`` calls and avoid using its private API. `#684`_

**Fixed**

- ``ValueError`` during sending a request with test payload if the endpoint defines a parameter with ``type: array`` and ``in: formData``. `#661`_
- ``KeyError`` while processing a schema with nullable parameters and ``in: body``. `#660`_
- ``StopIteration`` during ``requestBody`` processing if it has empty "content" value. `#673`_
- ``AttributeError`` during generation of "multipart/form-data" parameters that have no "type" defined. `#675`_
- Support for properties named "$ref" in object schemas. Previously, it was causing ``TypeError``. `#672`_
- Generating illegal Unicode surrogates in the path. `#668`_
- Invalid development dependency on ``graphql-server-core`` package. `#658`_

.. _v2.2.1:

`2.2.1`_ - 2020-07-22
---------------------

**Fixed**

- Possible ``UnicodeEncodeError`` during generation of ``Authorization`` header values for endpoints with ``basic`` security scheme. `#656`_

.. _v2.2.0:

`2.2.0`_ - 2020-07-14
---------------------

**Added**

- ``schemathesis.graphql.from_dict`` loader allows you to use GraphQL schemas represented as a dictionary for testing.
- ``before_load_schema`` hook for GraphQL schemas.

**Fixed**

- Serialization of non-string parameters. `#651`_

.. _v2.1.0:

`2.1.0`_ - 2020-07-06
---------------------

**Added**

- Support for property-level examples. `#467`_

**Fixed**

- Content-type conformance check for cases when Open API 3.0 schemas contain "default" response definitions. `#641`_
- Handling of multipart requests for Open API 3.0 schemas. `#640`_
- Sending non-file form fields in multipart requests. `#647`_

**Removed**

- Deprecated ``skip_validation`` argument to ``HookDispatcher.apply``.
- Deprecated ``_accepts_context`` internal function.

.. _v2.0.0:

`2.0.0`_ - 2020-07-01
---------------------

**Changed**

- **BREAKING**. Base URL handling. ``base_url`` now is treated as one with a base path included.
  You should pass a full base URL now instead:

.. code:: bash

    schemathesis run --base-url=http://127.0.0.1:8080/api/v2 ...

This value will override ``basePath`` / ``servers[0].url`` defined in your schema if you use
Open API 2.0 / 3.0 respectively. Previously if you pass a base URL like the one above, it
was concatenated with the base path defined in the schema, which leads to a lack of ability
to redefine the base path. `#511`_

**Fixed**

- Show the correct URL in CLI progress when the base URL is overridden, including the path part. `#511`_
- Construct valid URL when overriding base URL with base path. `#511`_

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

`1.10.0`_ - 2020-06-28
----------------------

**Added**

- ``loaders.from_asgi`` supports making calls to ASGI-compliant application (For example: FastAPI). `#521`_
- Support for GraphQL strategies.

**Fixed**

- Passing custom headers to schema loader for WSGI / ASGI apps. `#631`_

.. _v1.9.1:

`1.9.1`_ - 2020-06-21
---------------------

**Fixed**

- Schema validation error on schemas containing numeric values in scientific notation without a dot. `#629`_

.. _v1.9.0:

`1.9.0`_ - 2020-06-20
---------------------

**Added**

- Pass the original case's response to the ``add_case`` hook.
- Support for multiple examples with OpenAPI ``examples``. `#589`_
- ``--verbosity`` CLI option to minimize the error output. `#598`_
- Allow registering function-level hooks without passing their name as the first argument to ``apply``. `#618`_
- Support for hook usage via ``LazySchema`` / ``from_pytest_fixture``. `#617`_

**Changed**

- Tests with invalid schemas marked as errors, instead of failures. `#622`_

**Fixed**

- Crash during the generation of loosely-defined headers. `#621`_
- Show exception information for test runs on invalid schemas with ``--validate-schema=false`` command-line option.
  Before, the output sections for invalid endpoints were empty. `#622`_

.. _v1.8.0:

`1.8.0`_ - 2020-06-15
---------------------

**Fixed**

- Tests with invalid schemas are marked as failed instead of passed when ``hypothesis-jsonschema>=0.16`` is installed. `#614`_
- ``KeyError`` during creating an endpoint strategy if it contains a reference. `#612`_

**Changed**

- Require ``hypothesis-jsonschema>=0.16``. `#614`_
- Pass original ``InvalidSchema`` text to ``pytest.fail`` call.

.. _v1.7.0:

`1.7.0`_ - 2020-05-30
---------------------

**Added**

- Support for YAML files in references via HTTPS & HTTP schemas. `#600`_
- Stateful testing support via ``Open API links`` syntax. `#548`_
- New ``add_case`` hook. `#458`_
- Support for parameter serialization formats in Open API 2 / 3. For example ``pipeDelimited`` or ``deepObject``. `#599`_
- Support serializing parameters with ``application/json`` content-type. `#594`_

**Changed**

- The minimum required versions for ``Hypothesis`` and ``hypothesis-jsonschema`` are ``5.15.0`` and ``0.11.1`` respectively.
  The main reason is `this fix <https://github.com/HypothesisWorks/hypothesis/commit/4c7f3fbc55b294f13a503b2d2af0d3221fd37938>`_ that is
  required for stability of Open API links feature when it is executed in multiple threads.

.. _v1.6.3:

`1.6.3`_ - 2020-05-26
---------------------

**Fixed**

- Support for a colon symbol (``:``) inside of a header value passed via CLI. `#596`_

.. _v1.6.2:

`1.6.2`_ - 2020-05-15
---------------------

**Fixed**

- Partially generated explicit examples are always valid and can be used in requests. `#582`_

.. _v1.6.1:

`1.6.1`_ - 2020-05-13
---------------------

**Changed**

- Look at the current working directory when loading hooks for CLI. `#586`_

.. _v1.6.0:

`1.6.0`_ - 2020-05-10
---------------------

**Added**

- New ``before_add_examples`` hook. `#571`_
- New ``after_init_cli_run_handlers`` hook. `#575`_

**Fixed**

- Passing ``workers_num`` to ``ThreadPoolRunner`` leads to always using 2 workers in this worker kind. `#579`_

.. _v1.5.1:

`1.5.1`_ - 2020-05-08
---------------------

**Fixed**

- Display proper headers in reproduction code when headers are overridden. `#566`_

.. _v1.5.0:

`1.5.0`_ - 2020-05-06
---------------------

**Added**

- Display a suggestion to disable schema validation on schema loading errors in CLI. `#531`_
- Filtration of endpoints by ``operationId`` via ``operation_id`` parameter to ``schema.parametrize`` or ``-O`` command-line option. `#546`_
- Generation of security-related parameters. They are taken from ``securityDefinitions`` / ``securitySchemes`` and injected
  to the generated data. It supports generating API keys in headers or query parameters and generating data for HTTP
  authentication schemes. `#540`_

**Fixed**

- Overriding header values in CLI and runner when headers provided explicitly clash with ones defined in the schema. `#559`_
- Nested references resolving in ``response_schema_conformance`` check. `#562`_
- Nullable parameters handling when they are behind a reference. `#542`_

.. _v1.4.0:

`1.4.0`_ - 2020-05-03
---------------------

**Added**

- ``context`` argument for hook functions to provide an additional context for hooks. A deprecation warning is emitted
  for hook functions that do not accept this argument.
- A new hook system that allows generic hook dispatching. It comes with new hook locations. For more details, see the "Customization" section in our documentation.
- New ``before_process_path`` hook.
- Third-party compatibility fixups mechanism. Currently, there is one fixup for `FastAPI <https://github.com/tiangolo/fastapi>`_. `#503`_

Deprecated


- Hook functions that do not accept ``context`` as their first argument. They will become not be supported in Schemathesis 2.0.
- Registering hooks by name and function. Use ``register`` decorators instead. For more details, see the "Customization" section in our documentation.
- ``BaseSchema.with_hook`` and ``BaseSchema.register_hook``. Use ``BaseSchema.hooks.apply`` and ``BaseSchema.hooks.register`` instead.

**Fixed**

- Add missing ``validate_schema`` argument to ``loaders.from_pytest_fixture``.
- Reference resolving during response schema conformance check. `#539`_

.. _v1.3.4:

`1.3.4`_ - 2020-04-30
---------------------

**Fixed**

- Validation of nullable properties in ``response_schema_conformance`` check introduced in ``1.3.0``. `#542`_

.. _v1.3.3:

`1.3.3`_ - 2020-04-29
---------------------

**Changed**

- Update ``pytest-subtests`` pin to ``>=0.2.1,<1.0``. `#537`_

.. _v1.3.2:

`1.3.2`_ - 2020-04-27
---------------------

**Added**

- Show exceptions if they happened during loading a WSGI application. Option ``--show-errors-tracebacks`` will display a
  full traceback.

.. _v1.3.1:

`1.3.1`_ - 2020-04-27
---------------------

**Fixed**

- Packaging issue

.. _v1.3.0:

`1.3.0`_ - 2020-04-27
---------------------

**Added**

- Storing network logs with ``--store-network-log=<filename.yaml>``.
  The stored cassettes are based on the `VCR format <https://relishapp.com/vcr/vcr/v/5-1-0/docs/cassettes/cassette-format>`_
  and contain extra information from the Schemathesis internals. `#379`_
- Replaying of cassettes stored in VCR format. `#519`_
- Targeted property-based testing in CLI and runner. It only supports the ``response_time`` target at the moment. `#104`_
- Export CLI test results to JUnit.xml with ``--junit-xml=<filename.xml>``. `#427`_

**Fixed**

- Code samples for schemas where ``body`` is defined as ``{"type": "string"}``. `#521`_
- Showing error causes on internal ``jsonschema`` errors during input schema validation. `#513`_
- Recursion error in ``response_schema_conformance`` check. Because of this change, ``Endpoint.definition`` contains a definition where references are not resolved. In this way, it makes it possible to avoid recursion errors in ``jsonschema`` validation. `#468`_

**Changed**

- Added indentation & section name to the ``SUMMARY`` CLI block.
- Use C-extension for YAML loading when it is possible. It can cause more than 10x speedup on schema parsing.
  Do not show Click's "Aborted!" message when an error occurs during CLI schema loading.
- Add a help message to the CLI output when an internal exception happens. `#529`_

.. _v1.2.0:

`1.2.0`_ - 2020-04-15
---------------------

**Added**

- Per-test hooks for modification of data generation strategies. `#492`_
- Support for ``x-example`` vendor extension in Open API 2.0. `#504`_
- Sanity validation for the input schema & loader in ``runner.prepare``. `#499`_

.. _v1.1.2:

`1.1.2`_ - 2020-04-14
---------------------

**Fixed**

- Support for custom loaders in ``runner``. Now all built-in loaders are supported as an argument to ``runner.prepare``. `#496`_
- ``from_wsgi`` loader accepts custom keyword arguments that will be passed to ``client.get`` when accessing the schema. `#497`_

.. _v1.1.1:

`1.1.1`_ - 2020-04-12
---------------------

**Fixed**

- Mistakenly applied Open API -> JSON Schema Draft 7 conversion. It should be Draft 4. `#489`_
- Using wrong validator in ``response_schema_conformance`` check. It should be Draft 4 validator. `#468`_

.. _v1.1.0:

`1.1.0`_ - 2020-04-08
---------------------

**Fixed**

- Response schema check for recursive schemas. `#468`_

**Changed**

- App loading in ``runner``. Now it accepts application as an importable string, rather than an instance. It is done to make it possible to execute a runner in a subprocess. Otherwise, apps can't be easily serialized and transferred into another process.
- Runner events structure. All data in events is static from now. There are no references to ``BaseSchema``, ``Endpoint`` or similar objects that may calculate data dynamically. This is done to make events serializable and not tied to Python object, which decouples any ``runner`` consumer from implementation details. It will help make ``runner`` usable in more cases (e.g., web application) since events can be serialized to JSON and used in any environment.
  Another related change is that Python exceptions are not propagated anymore - they are replaced with the ``InternalError`` event that should be handled accordingly.

.. _v1.0.5:

`1.0.5`_ - 2020-04-03
---------------------

**Fixed**

- Open API 3. Handling of endpoints that contain ``multipart/form-data`` media types.
  Previously only file upload endpoints were working correctly. `#473`_

.. _v1.0.4:

`1.0.4`_ - 2020-04-03
---------------------

**Fixed**

- ``OpenApi30.get_content_types`` behavior, introduced in `8aeee1a <https://github.com/schemathesis/schemathesis/commit/8aeee1ab2c6c97d94272dde4790f5efac3951aed>`_. `#469`_

.. _v1.0.3:

`1.0.3`_ - 2020-04-03
---------------------

**Fixed**

- Precedence of ``produces`` keywords for Swagger 2.0 schemas. Now, operation-level ``produces`` overrides schema-level ``produces`` as specified in the specification. `#463`_
- Content-type conformance check for Open API 3.0 schemas. `#461`_
- Pytest 5.4 warning for test functions without parametrization. `#451`_

.. _v1.0.2:

`1.0.2`_ - 2020-04-02
---------------------

**Fixed**

- Handling of fields in ``paths`` that are not operations, but allowed by the Open API spec. `#457`_
- Pytest 5.4 warning about deprecated ``Node`` initialization usage. `#451`_

.. _v1.0.1:

`1.0.1`_ - 2020-04-01
---------------------

**Fixed**

- Processing of explicit examples in Open API 3.0 when there are multiple parameters in the same location (e.g. ``path``)
  contain ``example`` value. They are properly combined now. `#450`_

.. _v1.0.0:

`1.0.0`_ - 2020-03-31
---------------------

**Changed**

- Move processing of ``runner`` parameters to ``runner.prepare``. This change will provide better code reuse since all users of ``runner`` (e.g., if you extended it in your project) need some kind of input parameters handling, which was implemented only in Schemathesis CLI. It is not backward-compatible. If you didn't use ``runner`` directly, then this change should not have a visible effect on your use-case.

.. _v0.28.0:

`0.28.0`_ - 2020-03-31
----------------------

**Fixed**

- Handling of schemas that use ``x-*`` custom properties. `#448`_

**Removed**

- Deprecated ``runner.execute``. Use ``runner.prepare`` instead.

.. _v0.27.0:

`0.27.0`_ - 2020-03-31
----------------------

Deprecated

- ``runner.execute`` should not be used, since ``runner.prepare`` provides a more flexible interface to test execution.

**Removed**

- Deprecated ``Parametrizer`` class. Use ``schemathesis.from_path`` as a replacement for ``Parametrizer.from_path``.

.. _v0.26.1:

`0.26.1`_ - 2020-03-24
----------------------

**Fixed**

- Limit recursion depth while resolving JSON schema to handle recursion without breaking. `#435`_

.. _v0.26.0:

`0.26.0`_ - 2020-03-19
----------------------

**Fixed**

- Filter problematic path template variables containing ``"/"``, or ``"%2F"`` url encoded. `#440`_
- Filter invalid empty ``""`` path template variables. `#439`_
- Typo in a help message in the CLI output. `#436`_

.. _v0.25.1:

`0.25.1`_ - 2020-03-09
----------------------

**Changed**

- Allow ``werkzeug`` >= 1.0.0. `#433`_

.. _v0.25.0:

`0.25.0`_ - 2020-02-27
----------------------

**Changed**

- Handling of explicit examples from schemas. Now, if there are examples for multiple locations
  (e.g., for body and query) then they will be combined into a single example. `#424`_

.. _v0.24.5:

`0.24.5`_ - 2020-02-26
----------------------

**Fixed**

- Error during ``pytest`` collection on objects with custom ``__getattr__`` method and therefore pass ``is_schemathesis`` check. `#429`_

.. _v0.24.4:

`0.24.4`_ - 2020-02-22
----------------------

**Fixed**

- Resolving references when the schema is loaded from a file on Windows. `#418`_

.. _v0.24.3:

`0.24.3`_ - 2020-02-10
----------------------

**Fixed**

- Not copied ``validate_schema`` parameter in ``BaseSchema.parametrize``. Regression after implementing `#383`_
- Missing ``app``, ``location`` and ``hooks`` parameters in schema when used with ``BaseSchema.parametrize``. `#416`_

.. _v0.24.2:

`0.24.2`_ - 2020-02-09
----------------------

**Fixed**

- Crash on invalid regular expressions in ``method``, ``endpoint`` and ``tag`` CLI options. `#403`_
- Crash on a non-latin-1 encodable value in the ``auth`` CLI option. `#404`_
- Crash on an invalid value in the ``header`` CLI option. `#405`_
- Crash on some invalid URLs in the ``schema`` CLI option. `#406`_
- Validation of ``--request-timeout`` parameter. `#407`_
- Crash with ``--hypothesis-deadline=0`` CLI option. `#410`_
- Crash with ``--hypothesis-max-examples=0`` CLI option. `#412`_

.. _v0.24.1:

`0.24.1`_ - 2020-02-08
----------------------

**Fixed**

- CLI crash on Windows and Python < 3.8 when the schema path contains characters unrepresentable at the OS level. `#400`_

.. _v0.24.0:

`0.24.0`_ - 2020-02-07
----------------------

**Added**

- Support for testing of examples in Parameter & Media Type objects in Open API 3.0. `#394`_
- ``--show-error-tracebacks`` CLI option to display errors' tracebacks in the output. `#391`_
- Support for schema behind auth. `#115`_

**Changed**

- Schemas with GET endpoints accepting body are allowed now if schema validation is disabled (via ``--validate-schema=false`` for example).
  The use-case is for tools like ElasticSearch that use GET requests with non-empty bodies. `#383`_

**Fixed**

- CLI crash when an explicit example is specified in the endpoint definition. `#386`_

.. _v0.23.7:

`0.23.7`_ - 2020-01-30
----------------------

**Added**

- ``-x``/``--exitfirst`` CLI option to exit after the first failed test. `#378`_

**Fixed**

- Handling examples of parameters in Open API 3.0. `#381`_

.. _v0.23.6:

`0.23.6`_ - 2020-01-28
----------------------

**Added**

- ``all`` variant for ``--checks`` CLI option to use all available checks. `#374`_

**Changed**

- Use built-in ``importlib.metadata`` on Python 3.8. `#376`_

.. _v0.23.5:

`0.23.5`_ - 2020-01-24
----------------------

**Fixed**

- Generation of invalid values in ``Case.cookies``. `#371`_

.. _v0.23.4:

`0.23.4`_ - 2020-01-22
----------------------

**Fixed**

- Converting ``exclusiveMinimum`` & ``exclusiveMaximum`` fields to JSON Schema. `#367`_

.. _v0.23.3:

`0.23.3`_ - 2020-01-21
----------------------

**Fixed**

- Filter out surrogate pairs from the query string.

.. _v0.23.2:

`0.23.2`_ - 2020-01-16
----------------------

**Fixed**

- Prevent ``KeyError`` when the response does not have the "Content-Type" header. `#365`_

.. _v0.23.1:

`0.23.1`_ - 2020-01-15
----------------------

**Fixed**

- Dockerfile entrypoint was not working as per docs. `#361`_

.. _v0.23.0:

`0.23.0`_ - 2020-01-15
----------------------

**Added**

- Hooks for strategy modification. `#313`_
- Input schema validation. Use ``--validate-schema=false`` to disable it in CLI and ``validate_schema=False`` argument in loaders. `#110`_

.. _v0.22.0:

`0.22.0`_ - 2020-01-11
----------------------

**Added**

- Show multiple found failures in the CLI output. `#266`_ & `#207`_
- Raise a proper exception when the given schema is invalid. `#308`_
- Support for ``None`` as a value for ``--hypothesis-deadline``. `#349`_

**Fixed**

- Handling binary request payloads in ``Case.call``. `#350`_
- Type of the second argument to all built-in checks set to proper ``Case`` instead of ``TestResult``.
  The error was didn't affect built-in checks since both ``Case`` and ``TestResult`` had ``endpoint`` attribute, and only it was used. However, this fix is not backward-compatible with 3rd party checks.

.. _v0.21.0:

`0.21.0`_ - 2019-12-20
----------------------

**Added**

- Support for AioHTTP applications in CLI. `#329`_

.. _v0.20.5:

`0.20.5`_ - 2019-12-18
----------------------

**Fixed**

- Compatibility with the latest release of ``hypothesis-jsonschema`` and setting its minimal required version to ``0.9.13``. `#338`_

.. _v0.20.4:

`0.20.4`_ - 2019-12-17
----------------------

**Fixed**

- Handling ``nullable`` attribute in Open API schemas. `#335`_

.. _v0.20.3:

`0.20.3`_ - 2019-12-17
----------------------

**Fixed**

- Usage of the response status code conformance check with old ``requests`` version. `#330`_

.. _v0.20.2:

`0.20.2`_ - 2019-12-14
----------------------

**Fixed**

- Response schema conformance check for Open API 3.0. `#332`_

.. _v0.20.1:

`0.20.1`_ - 2019-12-13
----------------------

**Added**

- Support for response code ranges. `#330`_

.. _v0.20.0:

`0.20.0`_ - 2019-12-12
----------------------

**Added**

- WSGI apps support. `#31`_
- ``Case.validate_response`` for running built-in checks against app's response. `#319`_

**Changed**

- Checks receive ``Case`` instance as a second argument instead of ``TestResult``.
  This was done for making checks usable in Python tests via ``Case.validate_response``.
  Endpoint and schema are accessible via ``case.endpoint`` and ``case.endpoint.schema``.

.. _v0.19.1:

`0.19.1`_ - 2019-12-11
----------------------

**Fixed**

- Compatibility with Hypothesis >= 4.53.2. `#322`_

.. _v0.19.0:

`0.19.0`_ - 2019-12-02
----------------------

**Added**

- Concurrent test execution in CLI / runner. `#91`_
- update importlib_metadata pin to ``^1.1``. `#315`_

.. _v0.18.1:

`0.18.1`_ - 2019-11-28
----------------------

**Fixed**

- Validation of the ``base-url`` CLI parameter. `#311`_

.. _v0.18.0:

`0.18.0`_ - 2019-11-27
----------------------

**Added**

- Resolving references in ``PathItem`` objects. `#301`_

**Fixed**

- Resolving of relative paths in schemas. `#303`_
- Loading string dates as ``datetime.date`` objects in YAML loader. `#305`_

.. _v0.17.0:

`0.17.0`_ - 2019-11-21
----------------------

**Added**

- Resolving references that point to different files. `#294`_

**Changed**

- Keyboard interrupt is now handled during the CLI run, and the summary is displayed in the output. `#295`_

.. _v0.16.0:

`0.16.0`_ - 2019-11-19
----------------------

**Added**

- Display RNG seed in the CLI output to allow test reproducing. `#267`_
- Allow specifying seed in CLI.
- Ability to pass custom kwargs to the ``requests.get`` call in ``loaders.from_uri``.

**Changed**

- Refactor case generation strategies: strategy is not used to generate empty value. `#253`_
- Improved error message for invalid path parameter declaration. `#255`_

**Fixed**

- Pytest fixture parametrization via ``pytest_generate_tests``. `#280`_
- Support for tests defined as methods. `#282`_
- Unclosed ``requests.Session`` on calling ``Case.call`` without passing a session explicitly. `#286`_

.. _v0.15.0:

`0.15.0`_ - 2019-11-15
----------------------

**Added**

- Support for OpenAPI 3.0 server variables (base_path). `#40`_
- Support for ``format: byte``. `#254`_
- Response schema conformance check in CLI / Runner. `#256`_
- Docker image for CLI. `#268`_
- Pre-run hooks for CLI. `#147`_
- A way to register custom checks for CLI via ``schemathesis.register_check``. `#270`_

**Fixed**

- Not encoded path parameters. `#272`_

**Changed**

- Verbose messages are displayed in the CLI on failed checks. `#261`_

.. _v0.14.0:

`0.14.0`_ - 2019-11-09
----------------------

**Added**

- CLI: Support file paths in the ``schema`` argument. `#119`_
- Checks to verify response status & content type in CLI / Runner. `#101`_

**Fixed**

- Custom base URL handling in CLI / Runner. `#248`_

**Changed**

- Raise an error if the schema has a body for GET requests. `#218`_
- Method names are case insensitive during direct schema access. `#246`_

.. _v0.13.2:

`0.13.2`_ - 2019-11-05
----------------------

**Fixed**

- ``IndexError`` when Hypothesis found inconsistent test results during the test execution in the runner. `#236`_

.. _v0.13.1:

`0.13.1`_ - 2019-11-05
----------------------

**Added**

- Support for binary format `#197`_

**Fixed**

- Error that happens when there are no success checks in the statistic in CLI. `#237`_

.. _v0.13.0:

`0.13.0`_ - 2019-11-03
----------------------

**Added**

- An option to configure request timeout for CLI / Runner. `#204`_
- A help snippet to reproduce errors caught by Schemathesis. `#206`_
- Total running time to the CLI output. `#181`_
- Summary line in the CLI output with the number of passed / failed / errored endpoint tests. `#209`_
- Extra information to the CLI output: schema address, spec version, and base URL. `#188`_

**Fixed**

- Compatibility with Hypothesis 4.42.4+ . `#212`_
- Display flaky errors only in the "ERRORS" section and improve CLI output. `#215`_
- Handling ``formData`` parameters in ``Case.call``. `#196`_
- Handling cookies in ``Case.call``. `#211`_

**Changed**

- More readable falsifying examples output. `#127`_
- Show exceptions in a separate section of the CLI output. `#203`_
- Error message for cases when it is not possible to satisfy schema parameters. It should be more clear now. `#216`_
- Do not stop on schema errors related to a single endpoint. `#139`_
- Display a proper error message when the schema is not available in CLI / Runner. `#214`_

.. _v0.12.2:

`0.12.2`_ - 2019-10-30
----------------------

**Fixed**

- Wrong handling of the ``base_url`` parameter in runner and ``Case.call`` if it has a trailing slash. `#194`_ and `#199`_
- Do not send any payload with GET requests. `#200`_

.. _v0.12.1:

`0.12.1`_ - 2019-10-28
----------------------

**Fixed**

- Handling for errors other than ``AssertionError`` and ``HypothesisException`` in the runner. `#189`_
- CLI failing on the case when there are tests, but no checks were performed. `#191`_

**Changed**

- Display the "SUMMARY" section in the CLI output for empty test suites.

.. _v0.12.0:

`0.12.0`_ - 2019-10-28
----------------------

**Added**

- Display progress during the CLI run. `#125`_

**Fixed**

- Test server-generated wrong schema when the ``endpoints`` option is passed via CLI. `#173`_
- Error message if the schema is not found in CLI. `#172`_

**Changed**

- Continue running tests on hypothesis error. `#137`_

.. _v0.11.0:

`0.11.0`_ - 2019-10-22
----------------------

**Added**

- LazySchema accepts filters. `#149`_
- Ability to register strategies for custom string formats. `#94`_
- Generator-based events in the ``runner`` module to improve control over the execution flow.
- Filtration by tags. `#134`_

**Changed**

- Base URL in schema instances could be reused when it is defined during creation.
  Now on, the ``base_url`` argument in ``Case.call`` is optional in such cases. `#153`_
- Hypothesis deadline is set to 500ms by default. `#138`_
- Hypothesis output is captured separately, without capturing the whole stdout during CLI run.
- Disallow empty username in CLI ``--auth`` option.

**Fixed**

- User-agent during schema loading. `#144`_
- Generation of invalid values in ``Case.headers``. `#167`_

**Removed**

- Undocumented support for ``file://`` URI schema

.. _v0.10.0:

`0.10.0`_ - 2019-10-14
----------------------

**Added**

- HTTP Digest Auth support. `#106`_
- Support for Hypothesis settings in CLI & Runner. `#107`_
- ``Case.call`` and ``Case.as_requests_kwargs`` convenience methods. `#109`_
- Local development server. `#126`_

**Removed**

- Autogenerated ``runner.StatsCollector.__repr__`` to make Hypothesis output more readable.

.. _v0.9.0:

`0.9.0`_ - 2019-10-09
---------------------

**Added**

- Test executor collects results of execution. `#29`_
- CLI option ``--base-url`` for specifying base URL of API. `#118`_
- Support for coroutine-based tests. `#121`_
- User Agent to network requests in CLI & runner. `#130`_

**Changed**

- CLI command ``schemathesis run`` prints result in a more readable way with a summary of passing checks.
- Empty header names are forbidden for CLI.
- Suppressed hypothesis exception about using ``example`` non-interactively. `#92`_

.. _v0.8.1:

`0.8.1`_ - 2019-10-04
---------------------

**Fixed**

- Wrap each test in ``suppress`` so the runner doesn't stop after the first test failure.

.. _v0.8.0:

`0.8.0`_ - 2019-10-04
---------------------

**Added**

- CLI tool invoked by the ``schemathesis`` command. `#30`_
- New arguments ``api_options``, ``loader_options`` and ``loader`` for test executor. `#90`_
- A mapping interface for schemas & convenience methods for direct strategy access. `#98`_

**Fixed**

- Runner stopping on the first falsifying example. `#99`_

.. _v0.7.3:

`0.7.3`_ - 2019-09-30
---------------------

**Fixed**

- Filtration in lazy loaders.

.. _v0.7.2:

`0.7.2`_ - 2019-09-30
---------------------

**Added**

- Support for type "file" for Swagger 2.0. `#78`_
- Support for filtering in loaders. `#75`_

**Fixed**

- Conflict for lazy schema filtering. `#64`_

.. _v0.7.1:

`0.7.1`_ - 2019-09-27
---------------------

**Added**

- Support for ``x-nullable`` extension. `#45`_

.. _v0.7.0:

`0.7.0`_ - 2019-09-26
---------------------

**Added**

- Support for the ``cookie`` parameter in OpenAPI 3.0 schemas. `#21`_
- Support for the ``formData`` parameter in Swagger 2.0 schemas. `#6`_
- Test executor. `#28`_

**Fixed**

- Using ``hypothesis.settings`` decorator with test functions created from ``from_pytest_fixture`` loader. `#69`_

.. _v0.6.0:

`0.6.0`_ - 2019-09-24
---------------------

**Added**

- Parametrizing tests from a pytest fixture via ``pytest-subtests``. `#58`_

**Changed**

- Rename module ``readers`` to ``loaders``.
- Rename ``parametrize`` parameters. ``filter_endpoint`` to ``endpoint`` and ``filter_method`` to ``method``.

**Removed**

- Substring match for method/endpoint filters. To avoid clashing with escaped chars in endpoints keys in schemas.

.. _v0.5.0:

`0.5.0`_ - 2019-09-16
---------------------

**Added**

- Generating explicit examples from the schema. `#17`_

**Changed**

- Schemas are loaded eagerly from now on. Using ``schemathesis.from_uri`` implies network calls.

Deprecated


- Using ``Parametrizer.from_{path,uri}`` is deprecated, use ``schemathesis.from_{path,uri}`` instead.

**Fixed**

- Body resolving during test collection. `#55`_

.. _v0.4.1:

`0.4.1`_ - 2019-09-11
---------------------

**Fixed**

- Possibly unhandled exception during ``hasattr`` check in ``is_schemathesis_test``.

.. _v0.4.0:

`0.4.0`_ - 2019-09-10
---------------------

**Fixed**

- Resolving all inner references in objects. `#34`_

**Changed**

- ``jsonschema.RefResolver`` is now used for reference resolving. `#35`_

.. _v0.3.0:

`0.3.0`_ - 2019-09-06
---------------------

**Added**

- ``Parametrizer.from_uri`` method to construct parametrizer instances from URIs. `#24`_

**Removed**

- Possibility to use ``Parametrizer.parametrize`` and custom ``Parametrizer`` kwargs for passing config options
  to ``hypothesis.settings``. Use ``hypothesis.settings`` decorators on tests instead.

.. _v0.2.0:

`0.2.0`_ - 2019-09-05
---------------------

**Added**

- Open API 3.0 support. `#10`_
- "header" parameters. `#7`_

**Changed**

- Handle errors during collection / executions as failures.
- Use ``re.search`` for pattern matching in ``filter_method``/``filter_endpoint`` instead of ``fnmatch``. `#18`_
- ``Case.body`` contains properties from the target schema, without the extra level of nesting.

**Fixed**

- ``KeyError`` on collection when "basePath" is absent. `#16`_

.. _v0.1.0:

0.1.0 - 2019-06-28
------------------

- Initial public release

.. _Unreleased: https://github.com/schemathesis/schemathesis/compare/v3.18.5...HEAD
.. _3.18.5: https://github.com/schemathesis/schemathesis/compare/v3.18.4...v3.18.5
.. _3.18.4: https://github.com/schemathesis/schemathesis/compare/v3.18.3...v3.18.4
.. _3.18.3: https://github.com/schemathesis/schemathesis/compare/v3.18.2...v3.18.3
.. _3.18.2: https://github.com/schemathesis/schemathesis/compare/v3.18.1...v3.18.2
.. _3.18.1: https://github.com/schemathesis/schemathesis/compare/v3.18.0...v3.18.1
.. _3.18.0: https://github.com/schemathesis/schemathesis/compare/v3.17.5...v3.18.0
.. _3.17.5: https://github.com/schemathesis/schemathesis/compare/v3.17.4...v3.17.5
.. _3.17.4: https://github.com/schemathesis/schemathesis/compare/v3.17.3...v3.17.4
.. _3.17.3: https://github.com/schemathesis/schemathesis/compare/v3.17.2...v3.17.3
.. _3.17.2: https://github.com/schemathesis/schemathesis/compare/v3.17.1...v3.17.2
.. _3.17.1: https://github.com/schemathesis/schemathesis/compare/v3.17.0...v3.17.1
.. _3.17.0: https://github.com/schemathesis/schemathesis/compare/v3.16.5...v3.17.0
.. _3.16.5: https://github.com/schemathesis/schemathesis/compare/v3.16.4...v3.16.5
.. _3.16.4: https://github.com/schemathesis/schemathesis/compare/v3.16.3...v3.16.4
.. _3.16.3: https://github.com/schemathesis/schemathesis/compare/v3.16.2...v3.16.3
.. _3.16.2: https://github.com/schemathesis/schemathesis/compare/v3.16.1...v3.16.2
.. _3.16.1: https://github.com/schemathesis/schemathesis/compare/v3.16.0...v3.16.1
.. _3.16.0: https://github.com/schemathesis/schemathesis/compare/v3.15.6...v3.16.0
.. _3.15.6: https://github.com/schemathesis/schemathesis/compare/v3.15.5...v3.15.6
.. _3.15.5: https://github.com/schemathesis/schemathesis/compare/v3.15.4...v3.15.5
.. _3.15.4: https://github.com/schemathesis/schemathesis/compare/v3.15.3...v3.15.4
.. _3.15.3: https://github.com/schemathesis/schemathesis/compare/v3.15.2...v3.15.3
.. _3.15.2: https://github.com/schemathesis/schemathesis/compare/v3.15.1...v3.15.2
.. _3.15.1: https://github.com/schemathesis/schemathesis/compare/v3.15.0...v3.15.1
.. _3.15.0: https://github.com/schemathesis/schemathesis/compare/v3.14.2...v3.15.0
.. _3.14.2: https://github.com/schemathesis/schemathesis/compare/v3.14.1...v3.14.2
.. _3.14.1: https://github.com/schemathesis/schemathesis/compare/v3.14.0...v3.14.1
.. _3.14.0: https://github.com/schemathesis/schemathesis/compare/v3.13.9...v3.14.0
.. _3.13.9: https://github.com/schemathesis/schemathesis/compare/v3.13.8...v3.13.9
.. _3.13.8: https://github.com/schemathesis/schemathesis/compare/v3.13.7...v3.13.8
.. _3.13.7: https://github.com/schemathesis/schemathesis/compare/v3.13.6...v3.13.7
.. _3.13.6: https://github.com/schemathesis/schemathesis/compare/v3.13.5...v3.13.6
.. _3.13.5: https://github.com/schemathesis/schemathesis/compare/v3.13.4...v3.13.5
.. _3.13.4: https://github.com/schemathesis/schemathesis/compare/v3.13.3...v3.13.4
.. _3.13.3: https://github.com/schemathesis/schemathesis/compare/v3.13.2...v3.13.3
.. _3.13.2: https://github.com/schemathesis/schemathesis/compare/v3.13.1...v3.13.2
.. _3.13.1: https://github.com/schemathesis/schemathesis/compare/v3.13.0...v3.13.1
.. _3.13.0: https://github.com/schemathesis/schemathesis/compare/v3.12.3...v3.13.0
.. _3.12.3: https://github.com/schemathesis/schemathesis/compare/v3.12.2...v3.12.3
.. _3.12.2: https://github.com/schemathesis/schemathesis/compare/v3.12.1...v3.12.2
.. _3.12.1: https://github.com/schemathesis/schemathesis/compare/v3.12.0...v3.12.1
.. _3.12.0: https://github.com/schemathesis/schemathesis/compare/v3.11.7...v3.12.0
.. _3.11.7: https://github.com/schemathesis/schemathesis/compare/v3.11.6...v3.11.7
.. _3.11.6: https://github.com/schemathesis/schemathesis/compare/v3.11.5...v3.11.6
.. _3.11.5: https://github.com/schemathesis/schemathesis/compare/v3.11.4...v3.11.5
.. _3.11.4: https://github.com/schemathesis/schemathesis/compare/v3.11.3...v3.11.4
.. _3.11.3: https://github.com/schemathesis/schemathesis/compare/v3.11.2...v3.11.3
.. _3.11.2: https://github.com/schemathesis/schemathesis/compare/v3.11.1...v3.11.2
.. _3.11.1: https://github.com/schemathesis/schemathesis/compare/v3.11.0...v3.11.1
.. _3.11.0: https://github.com/schemathesis/schemathesis/compare/v3.10.1...v3.11.0
.. _3.10.1: https://github.com/schemathesis/schemathesis/compare/v3.10.0...v3.10.1
.. _3.10.0: https://github.com/schemathesis/schemathesis/compare/v3.9.7...v3.10.0
.. _3.9.7: https://github.com/schemathesis/schemathesis/compare/v3.9.6...v3.9.7
.. _3.9.6: https://github.com/schemathesis/schemathesis/compare/v3.9.5...v3.9.6
.. _3.9.5: https://github.com/schemathesis/schemathesis/compare/v3.9.4...v3.9.5
.. _3.9.4: https://github.com/schemathesis/schemathesis/compare/v3.9.3...v3.9.4
.. _3.9.3: https://github.com/schemathesis/schemathesis/compare/v3.9.2...v3.9.3
.. _3.9.2: https://github.com/schemathesis/schemathesis/compare/v3.9.1...v3.9.2
.. _3.9.1: https://github.com/schemathesis/schemathesis/compare/v3.9.0...v3.9.1
.. _3.9.0: https://github.com/schemathesis/schemathesis/compare/v3.8.0...v3.9.0
.. _3.8.0: https://github.com/schemathesis/schemathesis/compare/v3.7.8...v3.8.0
.. _3.7.8: https://github.com/schemathesis/schemathesis/compare/v3.7.7...v3.7.8
.. _3.7.7: https://github.com/schemathesis/schemathesis/compare/v3.7.6...v3.7.7
.. _3.7.6: https://github.com/schemathesis/schemathesis/compare/v3.7.5...v3.7.6
.. _3.7.5: https://github.com/schemathesis/schemathesis/compare/v3.7.4...v3.7.5
.. _3.7.4: https://github.com/schemathesis/schemathesis/compare/v3.7.3...v3.7.4
.. _3.7.3: https://github.com/schemathesis/schemathesis/compare/v3.7.2...v3.7.3
.. _3.7.2: https://github.com/schemathesis/schemathesis/compare/v3.7.1...v3.7.2
.. _3.7.1: https://github.com/schemathesis/schemathesis/compare/v3.7.0...v3.7.1
.. _3.7.0: https://github.com/schemathesis/schemathesis/compare/v3.6.11...v3.7.0
.. _3.6.11: https://github.com/schemathesis/schemathesis/compare/v3.6.10...v3.6.11
.. _3.6.10: https://github.com/schemathesis/schemathesis/compare/v3.6.9...v3.6.10
.. _3.6.9: https://github.com/schemathesis/schemathesis/compare/v3.6.8...v3.6.9
.. _3.6.8: https://github.com/schemathesis/schemathesis/compare/v3.6.7...v3.6.8
.. _3.6.7: https://github.com/schemathesis/schemathesis/compare/v3.6.6...v3.6.7
.. _3.6.6: https://github.com/schemathesis/schemathesis/compare/v3.6.5...v3.6.6
.. _3.6.5: https://github.com/schemathesis/schemathesis/compare/v3.6.4...v3.6.5
.. _3.6.4: https://github.com/schemathesis/schemathesis/compare/v3.6.3...v3.6.4
.. _3.6.3: https://github.com/schemathesis/schemathesis/compare/v3.6.2...v3.6.3
.. _3.6.2: https://github.com/schemathesis/schemathesis/compare/v3.6.1...v3.6.2
.. _3.6.1: https://github.com/schemathesis/schemathesis/compare/v3.6.0...v3.6.1
.. _3.6.0: https://github.com/schemathesis/schemathesis/compare/v3.5.3...v3.6.0
.. _3.5.3: https://github.com/schemathesis/schemathesis/compare/v3.5.2...v3.5.3
.. _3.5.2: https://github.com/schemathesis/schemathesis/compare/v3.5.1...v3.5.2
.. _3.5.1: https://github.com/schemathesis/schemathesis/compare/v3.5.0...v3.5.1
.. _3.5.0: https://github.com/schemathesis/schemathesis/compare/v3.4.1...v3.5.0
.. _3.4.1: https://github.com/schemathesis/schemathesis/compare/v3.4.0...v3.4.1
.. _3.4.0: https://github.com/schemathesis/schemathesis/compare/v3.3.1...v3.4.0
.. _3.3.1: https://github.com/schemathesis/schemathesis/compare/v3.3.0...v3.3.1
.. _3.3.0: https://github.com/schemathesis/schemathesis/compare/v3.2.2...v3.3.0
.. _3.2.2: https://github.com/schemathesis/schemathesis/compare/v3.2.1...v3.2.2
.. _3.2.1: https://github.com/schemathesis/schemathesis/compare/v3.2.0...v3.2.1
.. _3.2.0: https://github.com/schemathesis/schemathesis/compare/v3.1.3...v3.2.0
.. _3.1.3: https://github.com/schemathesis/schemathesis/compare/v3.1.2...v3.1.3
.. _3.1.2: https://github.com/schemathesis/schemathesis/compare/v3.1.1...v3.1.2
.. _3.1.1: https://github.com/schemathesis/schemathesis/compare/v3.1.0...v3.1.1
.. _3.1.0: https://github.com/schemathesis/schemathesis/compare/v3.0.9...v3.1.0
.. _3.0.9: https://github.com/schemathesis/schemathesis/compare/v3.0.8...v3.0.9
.. _3.0.8: https://github.com/schemathesis/schemathesis/compare/v3.0.7...v3.0.8
.. _3.0.7: https://github.com/schemathesis/schemathesis/compare/v3.0.6...v3.0.7
.. _3.0.6: https://github.com/schemathesis/schemathesis/compare/v3.0.5...v3.0.6
.. _3.0.5: https://github.com/schemathesis/schemathesis/compare/v3.0.4...v3.0.5
.. _3.0.4: https://github.com/schemathesis/schemathesis/compare/v3.0.3...v3.0.4
.. _3.0.3: https://github.com/schemathesis/schemathesis/compare/v3.0.2...v3.0.3
.. _3.0.2: https://github.com/schemathesis/schemathesis/compare/v3.0.1...v3.0.2
.. _3.0.1: https://github.com/schemathesis/schemathesis/compare/v3.0.0...v3.0.1
.. _3.0.0: https://github.com/schemathesis/schemathesis/compare/v2.8.5...v3.0.0
.. _2.8.6: https://github.com/schemathesis/schemathesis/compare/v2.8.5...v2.8.6
.. _2.8.5: https://github.com/schemathesis/schemathesis/compare/v2.8.4...v2.8.5
.. _2.8.4: https://github.com/schemathesis/schemathesis/compare/v2.8.3...v2.8.4
.. _2.8.3: https://github.com/schemathesis/schemathesis/compare/v2.8.2...v2.8.3
.. _2.8.2: https://github.com/schemathesis/schemathesis/compare/v2.8.1...v2.8.2
.. _2.8.1: https://github.com/schemathesis/schemathesis/compare/v2.8.0...v2.8.1
.. _2.8.0: https://github.com/schemathesis/schemathesis/compare/v2.7.7...v2.8.0
.. _2.7.7: https://github.com/schemathesis/schemathesis/compare/v2.7.6...v2.7.7
.. _2.7.6: https://github.com/schemathesis/schemathesis/compare/v2.7.5...v2.7.6
.. _2.7.5: https://github.com/schemathesis/schemathesis/compare/v2.7.4...v2.7.5
.. _2.7.4: https://github.com/schemathesis/schemathesis/compare/v2.7.3...v2.7.4
.. _2.7.3: https://github.com/schemathesis/schemathesis/compare/v2.7.2...v2.7.3
.. _2.7.2: https://github.com/schemathesis/schemathesis/compare/v2.7.1...v2.7.2
.. _2.7.1: https://github.com/schemathesis/schemathesis/compare/v2.7.0...v2.7.1
.. _2.7.0: https://github.com/schemathesis/schemathesis/compare/v2.6.1...v2.7.0
.. _2.6.1: https://github.com/schemathesis/schemathesis/compare/v2.6.0...v2.6.1
.. _2.6.0: https://github.com/schemathesis/schemathesis/compare/v2.5.1...v2.6.0
.. _2.5.1: https://github.com/schemathesis/schemathesis/compare/v2.5.0...v2.5.1
.. _2.5.0: https://github.com/schemathesis/schemathesis/compare/v2.4.1...v2.5.0
.. _2.4.1: https://github.com/schemathesis/schemathesis/compare/v2.4.0...v2.4.1
.. _2.4.0: https://github.com/schemathesis/schemathesis/compare/v2.3.4...v2.4.0
.. _2.3.4: https://github.com/schemathesis/schemathesis/compare/v2.3.3...v2.3.4
.. _2.3.3: https://github.com/schemathesis/schemathesis/compare/v2.3.2...v2.3.3
.. _2.3.2: https://github.com/schemathesis/schemathesis/compare/v2.3.1...v2.3.2
.. _2.3.1: https://github.com/schemathesis/schemathesis/compare/v2.3.0...v2.3.1
.. _2.3.0: https://github.com/schemathesis/schemathesis/compare/v2.2.1...v2.3.0
.. _2.2.1: https://github.com/schemathesis/schemathesis/compare/v2.2.0...v2.2.1
.. _2.2.0: https://github.com/schemathesis/schemathesis/compare/v2.1.0...v2.2.0
.. _2.1.0: https://github.com/schemathesis/schemathesis/compare/v2.0.0...v2.1.0
.. _2.0.0: https://github.com/schemathesis/schemathesis/compare/v1.10.0...v2.0.0
.. _1.10.0: https://github.com/schemathesis/schemathesis/compare/v1.9.1...v1.10.0
.. _1.9.1: https://github.com/schemathesis/schemathesis/compare/v1.9.0...v1.9.1
.. _1.9.0: https://github.com/schemathesis/schemathesis/compare/v1.8.0...v1.9.0
.. _1.8.0: https://github.com/schemathesis/schemathesis/compare/v1.7.0...v1.8.0
.. _1.7.0: https://github.com/schemathesis/schemathesis/compare/v1.6.3...v1.7.0
.. _1.6.3: https://github.com/schemathesis/schemathesis/compare/v1.6.2...v1.6.3
.. _1.6.2: https://github.com/schemathesis/schemathesis/compare/v1.6.1...v1.6.2
.. _1.6.1: https://github.com/schemathesis/schemathesis/compare/v1.6.0...v1.6.1
.. _1.6.0: https://github.com/schemathesis/schemathesis/compare/v1.5.1...v1.6.0
.. _1.5.1: https://github.com/schemathesis/schemathesis/compare/v1.5.0...v1.5.1
.. _1.5.0: https://github.com/schemathesis/schemathesis/compare/v1.4.0...v1.5.0
.. _1.4.0: https://github.com/schemathesis/schemathesis/compare/v1.3.4...v1.4.0
.. _1.3.4: https://github.com/schemathesis/schemathesis/compare/v1.3.3...v1.3.4
.. _1.3.3: https://github.com/schemathesis/schemathesis/compare/v1.3.2...v1.3.3
.. _1.3.2: https://github.com/schemathesis/schemathesis/compare/v1.3.1...v1.3.2
.. _1.3.1: https://github.com/schemathesis/schemathesis/compare/v1.3.0...v1.3.1
.. _1.3.0: https://github.com/schemathesis/schemathesis/compare/v1.2.0...v1.3.0
.. _1.2.0: https://github.com/schemathesis/schemathesis/compare/v1.1.2...v1.2.0
.. _1.1.2: https://github.com/schemathesis/schemathesis/compare/v1.1.1...v1.1.2
.. _1.1.1: https://github.com/schemathesis/schemathesis/compare/v1.1.0...v1.1.1
.. _1.1.0: https://github.com/schemathesis/schemathesis/compare/v1.0.5...v1.1.0
.. _1.0.5: https://github.com/schemathesis/schemathesis/compare/v1.0.4...v1.0.5
.. _1.0.4: https://github.com/schemathesis/schemathesis/compare/v1.0.3...v1.0.4
.. _1.0.3: https://github.com/schemathesis/schemathesis/compare/v1.0.2...v1.0.3
.. _1.0.2: https://github.com/schemathesis/schemathesis/compare/v1.0.1...v1.0.2
.. _1.0.1: https://github.com/schemathesis/schemathesis/compare/v1.0.0...v1.0.1
.. _1.0.0: https://github.com/schemathesis/schemathesis/compare/v0.28.0...v1.0.0
.. _0.28.0: https://github.com/schemathesis/schemathesis/compare/v0.27.0...v0.28.0
.. _0.27.0: https://github.com/schemathesis/schemathesis/compare/v0.26.1...v0.27.0
.. _0.26.1: https://github.com/schemathesis/schemathesis/compare/v0.26.0...v0.26.1
.. _0.26.0: https://github.com/schemathesis/schemathesis/compare/v0.25.1...v0.26.0
.. _0.25.1: https://github.com/schemathesis/schemathesis/compare/v0.25.0...v0.25.1
.. _0.25.0: https://github.com/schemathesis/schemathesis/compare/v0.24.5...v0.25.0
.. _0.24.5: https://github.com/schemathesis/schemathesis/compare/v0.24.4...v0.24.5
.. _0.24.4: https://github.com/schemathesis/schemathesis/compare/v0.24.3...v0.24.4
.. _0.24.3: https://github.com/schemathesis/schemathesis/compare/v0.24.2...v0.24.3
.. _0.24.2: https://github.com/schemathesis/schemathesis/compare/v0.24.1...v0.24.2
.. _0.24.1: https://github.com/schemathesis/schemathesis/compare/v0.24.0...v0.24.1
.. _0.24.0: https://github.com/schemathesis/schemathesis/compare/v0.23.7...v0.24.0
.. _0.23.7: https://github.com/schemathesis/schemathesis/compare/v0.23.6...v0.23.7
.. _0.23.6: https://github.com/schemathesis/schemathesis/compare/v0.23.5...v0.23.6
.. _0.23.5: https://github.com/schemathesis/schemathesis/compare/v0.23.4...v0.23.5
.. _0.23.4: https://github.com/schemathesis/schemathesis/compare/v0.23.3...v0.23.4
.. _0.23.3: https://github.com/schemathesis/schemathesis/compare/v0.23.2...v0.23.3
.. _0.23.2: https://github.com/schemathesis/schemathesis/compare/v0.23.1...v0.23.2
.. _0.23.1: https://github.com/schemathesis/schemathesis/compare/v0.23.0...v0.23.1
.. _0.23.0: https://github.com/schemathesis/schemathesis/compare/v0.22.0...v0.23.0
.. _0.22.0: https://github.com/schemathesis/schemathesis/compare/v0.21.0...v0.22.0
.. _0.21.0: https://github.com/schemathesis/schemathesis/compare/v0.20.5...v0.21.0
.. _0.20.5: https://github.com/schemathesis/schemathesis/compare/v0.20.4...v0.20.5
.. _0.20.4: https://github.com/schemathesis/schemathesis/compare/v0.20.3...v0.20.4
.. _0.20.3: https://github.com/schemathesis/schemathesis/compare/v0.20.2...v0.20.3
.. _0.20.2: https://github.com/schemathesis/schemathesis/compare/v0.20.1...v0.20.2
.. _0.20.1: https://github.com/schemathesis/schemathesis/compare/v0.20.0...v0.20.1
.. _0.20.0: https://github.com/schemathesis/schemathesis/compare/v0.19.1...v0.20.0
.. _0.19.1: https://github.com/schemathesis/schemathesis/compare/v0.19.1...v0.19.1
.. _0.19.0: https://github.com/schemathesis/schemathesis/compare/v0.18.1...v0.19.0
.. _0.18.1: https://github.com/schemathesis/schemathesis/compare/v0.18.0...v0.18.1
.. _0.18.0: https://github.com/schemathesis/schemathesis/compare/v0.17.0...v0.18.0
.. _0.17.0: https://github.com/schemathesis/schemathesis/compare/v0.16.0...v0.17.0
.. _0.16.0: https://github.com/schemathesis/schemathesis/compare/v0.15.0...v0.16.0
.. _0.15.0: https://github.com/schemathesis/schemathesis/compare/v0.14.0...v0.15.0
.. _0.14.0: https://github.com/schemathesis/schemathesis/compare/v0.13.2...v0.14.0
.. _0.13.2: https://github.com/schemathesis/schemathesis/compare/v0.13.1...v0.13.2
.. _0.13.1: https://github.com/schemathesis/schemathesis/compare/v0.13.0...v0.13.1
.. _0.13.0: https://github.com/schemathesis/schemathesis/compare/v0.12.2...v0.13.0
.. _0.12.2: https://github.com/schemathesis/schemathesis/compare/v0.12.1...v0.12.2
.. _0.12.1: https://github.com/schemathesis/schemathesis/compare/v0.12.0...v0.12.1
.. _0.12.0: https://github.com/schemathesis/schemathesis/compare/v0.11.0...v0.12.0
.. _0.11.0: https://github.com/schemathesis/schemathesis/compare/v0.10.0...v0.11.0
.. _0.10.0: https://github.com/schemathesis/schemathesis/compare/v0.9.0...v0.10.0
.. _0.9.0: https://github.com/schemathesis/schemathesis/compare/v0.8.1...v0.9.0
.. _0.8.1: https://github.com/schemathesis/schemathesis/compare/v0.8.0...v0.8.1
.. _0.8.0: https://github.com/schemathesis/schemathesis/compare/v0.7.3...v0.8.0
.. _0.7.3: https://github.com/schemathesis/schemathesis/compare/v0.7.2...v0.7.3
.. _0.7.2: https://github.com/schemathesis/schemathesis/compare/v0.7.1...v0.7.2
.. _0.7.1: https://github.com/schemathesis/schemathesis/compare/v0.7.0...v0.7.1
.. _0.7.0: https://github.com/schemathesis/schemathesis/compare/v0.6.0...v0.7.0
.. _0.6.0: https://github.com/schemathesis/schemathesis/compare/v0.5.0...v0.6.0
.. _0.5.0: https://github.com/schemathesis/schemathesis/compare/v0.4.1...v0.5.0
.. _0.4.1: https://github.com/schemathesis/schemathesis/compare/v0.4.0...v0.4.1
.. _0.4.0: https://github.com/schemathesis/schemathesis/compare/v0.3.0...v0.4.0
.. _0.3.0: https://github.com/schemathesis/schemathesis/compare/v0.2.0...v0.3.0
.. _0.2.0: https://github.com/schemathesis/schemathesis/compare/v0.1.0...v0.2.0

.. _#1702: https://github.com/schemathesis/schemathesis/issues/1702
.. _#1695: https://github.com/schemathesis/schemathesis/issues/1695
.. _#1669: https://github.com/schemathesis/schemathesis/issues/1669
.. _#1643: https://github.com/schemathesis/schemathesis/issues/1643
.. _#1637: https://github.com/schemathesis/schemathesis/issues/1637
.. _#1632: https://github.com/schemathesis/schemathesis/issues/1632
.. _#1631: https://github.com/schemathesis/schemathesis/issues/1631
.. _#1627: https://github.com/schemathesis/schemathesis/issues/1627
.. _#1625: https://github.com/schemathesis/schemathesis/issues/1625
.. _#1614: https://github.com/schemathesis/schemathesis/issues/1614
.. _#1592: https://github.com/schemathesis/schemathesis/issues/1592
.. _#1591: https://github.com/schemathesis/schemathesis/issues/1591
.. _#1584: https://github.com/schemathesis/schemathesis/issues/1584
.. _#1582: https://github.com/schemathesis/schemathesis/issues/1582
.. _#1580: https://github.com/schemathesis/schemathesis/issues/1580
.. _#1563: https://github.com/schemathesis/schemathesis/issues/1563
.. _#1538: https://github.com/schemathesis/schemathesis/issues/1538
.. _#1526: https://github.com/schemathesis/schemathesis/issues/1526
.. _#1518: https://github.com/schemathesis/schemathesis/issues/1518
.. _#1514: https://github.com/schemathesis/schemathesis/issues/1514
.. _#1485: https://github.com/schemathesis/schemathesis/issues/1485
.. _#1463: https://github.com/schemathesis/schemathesis/issues/1463
.. _#1452: https://github.com/schemathesis/schemathesis/issues/1452
.. _#1445: https://github.com/schemathesis/schemathesis/issues/1445
.. _#1429: https://github.com/schemathesis/schemathesis/issues/1429
.. _#1425: https://github.com/schemathesis/schemathesis/issues/1425
.. _#1413: https://github.com/schemathesis/schemathesis/issues/1413
.. _#1410: https://github.com/schemathesis/schemathesis/issues/1410
.. _#1395: https://github.com/schemathesis/schemathesis/issues/1395
.. _#1394: https://github.com/schemathesis/schemathesis/issues/1394
.. _#1382: https://github.com/schemathesis/schemathesis/issues/1382
.. _#1378: https://github.com/schemathesis/schemathesis/issues/1378
.. _#1373: https://github.com/schemathesis/schemathesis/issues/1373
.. _#1372: https://github.com/schemathesis/schemathesis/issues/1372
.. _#1370: https://github.com/schemathesis/schemathesis/issues/1370
.. _#1366: https://github.com/schemathesis/schemathesis/issues/1366
.. _#1359: https://github.com/schemathesis/schemathesis/issues/1359
.. _#1350: https://github.com/schemathesis/schemathesis/issues/1350
.. _#1343: https://github.com/schemathesis/schemathesis/issues/1343
.. _#1342: https://github.com/schemathesis/schemathesis/issues/1342
.. _#1340: https://github.com/schemathesis/schemathesis/issues/1340
.. _#1336: https://github.com/schemathesis/schemathesis/issues/1336
.. _#1335: https://github.com/schemathesis/schemathesis/issues/1335
.. _#1331: https://github.com/schemathesis/schemathesis/issues/1331
.. _#1328: https://github.com/schemathesis/schemathesis/issues/1328
.. _#1326: https://github.com/schemathesis/schemathesis/issues/1326
.. _#1325: https://github.com/schemathesis/schemathesis/issues/1325
.. _#1323: https://github.com/schemathesis/schemathesis/issues/1323
.. _#1303: https://github.com/schemathesis/schemathesis/issues/1303
.. _#1292: https://github.com/schemathesis/schemathesis/issues/1292
.. _#1290: https://github.com/schemathesis/schemathesis/issues/1290
.. _#1287: https://github.com/schemathesis/schemathesis/issues/1287
.. _#1280: https://github.com/schemathesis/schemathesis/issues/1280
.. _#1269: https://github.com/schemathesis/schemathesis/issues/1269
.. _#1262: https://github.com/schemathesis/schemathesis/issues/1262
.. _#1260: https://github.com/schemathesis/schemathesis/issues/1260
.. _#1241: https://github.com/schemathesis/schemathesis/issues/1241
.. _#1233: https://github.com/schemathesis/schemathesis/issues/1233
.. _#1226: https://github.com/schemathesis/schemathesis/issues/1226
.. _#1224: https://github.com/schemathesis/schemathesis/issues/1224
.. _#1220: https://github.com/schemathesis/schemathesis/issues/1220
.. _#1208: https://github.com/schemathesis/schemathesis/issues/1208
.. _#1204: https://github.com/schemathesis/schemathesis/issues/1204
.. _#1202: https://github.com/schemathesis/schemathesis/issues/1202
.. _#1194: https://github.com/schemathesis/schemathesis/issues/1194
.. _#1190: https://github.com/schemathesis/schemathesis/issues/1190
.. _#1189: https://github.com/schemathesis/schemathesis/issues/1189
.. _#1180: https://github.com/schemathesis/schemathesis/issues/1180
.. _#1173: https://github.com/schemathesis/schemathesis/issues/1173
.. _#1167: https://github.com/schemathesis/schemathesis/issues/1167
.. _#1166: https://github.com/schemathesis/schemathesis/issues/1166
.. _#1164: https://github.com/schemathesis/schemathesis/issues/1164
.. _#1162: https://github.com/schemathesis/schemathesis/issues/1162
.. _#1160: https://github.com/schemathesis/schemathesis/issues/1160
.. _#1153: https://github.com/schemathesis/schemathesis/issues/1153
.. _#1152: https://github.com/schemathesis/schemathesis/issues/1152
.. _#1142: https://github.com/schemathesis/schemathesis/issues/1142
.. _#1134: https://github.com/schemathesis/schemathesis/issues/1134
.. _#1121: https://github.com/schemathesis/schemathesis/issues/1121
.. _#1100: https://github.com/schemathesis/schemathesis/issues/1100
.. _#1097: https://github.com/schemathesis/schemathesis/issues/1097
.. _#1094: https://github.com/schemathesis/schemathesis/issues/1094
.. _#1093: https://github.com/schemathesis/schemathesis/issues/1093
.. _#1081: https://github.com/schemathesis/schemathesis/issues/1081
.. _#1073: https://github.com/schemathesis/schemathesis/issues/1073
.. _#1069: https://github.com/schemathesis/schemathesis/issues/1069
.. _#1068: https://github.com/schemathesis/schemathesis/issues/1068
.. _#1066: https://github.com/schemathesis/schemathesis/issues/1066
.. _#1067: https://github.com/schemathesis/schemathesis/issues/1067
.. _#1059: https://github.com/schemathesis/schemathesis/issues/1059
.. _#1050: https://github.com/schemathesis/schemathesis/issues/1050
.. _#1046: https://github.com/schemathesis/schemathesis/issues/1046
.. _#1039: https://github.com/schemathesis/schemathesis/issues/1039
.. _#1036: https://github.com/schemathesis/schemathesis/issues/1036
.. _#1033: https://github.com/schemathesis/schemathesis/issues/1033
.. _#1030: https://github.com/schemathesis/schemathesis/issues/1030
.. _#1028: https://github.com/schemathesis/schemathesis/issues/1028
.. _#1022: https://github.com/schemathesis/schemathesis/issues/1022
.. _#1020: https://github.com/schemathesis/schemathesis/issues/1020
.. _#1018: https://github.com/schemathesis/schemathesis/issues/1018
.. _#1015: https://github.com/schemathesis/schemathesis/issues/1015
.. _#1013: https://github.com/schemathesis/schemathesis/issues/1013
.. _#1010: https://github.com/schemathesis/schemathesis/issues/1010
.. _#1007: https://github.com/schemathesis/schemathesis/issues/1007
.. _#1003: https://github.com/schemathesis/schemathesis/issues/1003
.. _#999: https://github.com/schemathesis/schemathesis/issues/999
.. _#994: https://github.com/schemathesis/schemathesis/issues/994
.. _#992: https://github.com/schemathesis/schemathesis/issues/992
.. _#990: https://github.com/schemathesis/schemathesis/issues/990
.. _#987: https://github.com/schemathesis/schemathesis/issues/987
.. _#986: https://github.com/schemathesis/schemathesis/issues/986
.. _#982: https://github.com/schemathesis/schemathesis/issues/982
.. _#980: https://github.com/schemathesis/schemathesis/issues/980
.. _#975: https://github.com/schemathesis/schemathesis/issues/975
.. _#970: https://github.com/schemathesis/schemathesis/issues/970
.. _#966: https://github.com/schemathesis/schemathesis/issues/966
.. _#965: https://github.com/schemathesis/schemathesis/issues/965
.. _#963: https://github.com/schemathesis/schemathesis/issues/963
.. _#951: https://github.com/schemathesis/schemathesis/issues/951
.. _#947: https://github.com/schemathesis/schemathesis/issues/947
.. _#945: https://github.com/schemathesis/schemathesis/issues/945
.. _#941: https://github.com/schemathesis/schemathesis/issues/941
.. _#939: https://github.com/schemathesis/schemathesis/issues/939
.. _#934: https://github.com/schemathesis/schemathesis/issues/934
.. _#925: https://github.com/schemathesis/schemathesis/issues/925
.. _#920: https://github.com/schemathesis/schemathesis/issues/920
.. _#919: https://github.com/schemathesis/schemathesis/issues/919
.. _#917: https://github.com/schemathesis/schemathesis/issues/917
.. _#916: https://github.com/schemathesis/schemathesis/issues/916
.. _#914: https://github.com/schemathesis/schemathesis/issues/914
.. _#911: https://github.com/schemathesis/schemathesis/issues/911
.. _#908: https://github.com/schemathesis/schemathesis/issues/908
.. _#907: https://github.com/schemathesis/schemathesis/issues/907
.. _#905: https://github.com/schemathesis/schemathesis/issues/905
.. _#904: https://github.com/schemathesis/schemathesis/issues/904
.. _#897: https://github.com/schemathesis/schemathesis/issues/897
.. _#895: https://github.com/schemathesis/schemathesis/issues/895
.. _#890: https://github.com/schemathesis/schemathesis/issues/890
.. _#889: https://github.com/schemathesis/schemathesis/issues/889
.. _#886: https://github.com/schemathesis/schemathesis/issues/886
.. _#884: https://github.com/schemathesis/schemathesis/issues/884
.. _#882: https://github.com/schemathesis/schemathesis/issues/882
.. _#881: https://github.com/schemathesis/schemathesis/issues/881
.. _#880: https://github.com/schemathesis/schemathesis/issues/880
.. _#877: https://github.com/schemathesis/schemathesis/issues/877
.. _#876: https://github.com/schemathesis/schemathesis/issues/876
.. _#874: https://github.com/schemathesis/schemathesis/issues/874
.. _#872: https://github.com/schemathesis/schemathesis/issues/872
.. _#870: https://github.com/schemathesis/schemathesis/issues/870
.. _#869: https://github.com/schemathesis/schemathesis/issues/869
.. _#858: https://github.com/schemathesis/schemathesis/issues/858
.. _#855: https://github.com/schemathesis/schemathesis/issues/855
.. _#851: https://github.com/schemathesis/schemathesis/issues/851
.. _#850: https://github.com/schemathesis/schemathesis/issues/850
.. _#844: https://github.com/schemathesis/schemathesis/issues/844
.. _#841: https://github.com/schemathesis/schemathesis/issues/841
.. _#839: https://github.com/schemathesis/schemathesis/issues/839
.. _#836: https://github.com/schemathesis/schemathesis/issues/836
.. _#832: https://github.com/schemathesis/schemathesis/issues/832
.. _#830: https://github.com/schemathesis/schemathesis/issues/830
.. _#828: https://github.com/schemathesis/schemathesis/issues/828
.. _#824: https://github.com/schemathesis/schemathesis/issues/824
.. _#822: https://github.com/schemathesis/schemathesis/issues/822
.. _#816: https://github.com/schemathesis/schemathesis/issues/816
.. _#814: https://github.com/schemathesis/schemathesis/issues/814
.. _#812: https://github.com/schemathesis/schemathesis/issues/812
.. _#795: https://github.com/schemathesis/schemathesis/issues/795
.. _#793: https://github.com/schemathesis/schemathesis/issues/793
.. _#792: https://github.com/schemathesis/schemathesis/issues/792
.. _#788: https://github.com/schemathesis/schemathesis/issues/788
.. _#783: https://github.com/schemathesis/schemathesis/issues/783
.. _#768: https://github.com/schemathesis/schemathesis/issues/768
.. _#757: https://github.com/schemathesis/schemathesis/issues/757
.. _#748: https://github.com/schemathesis/schemathesis/issues/748
.. _#746: https://github.com/schemathesis/schemathesis/issues/746
.. _#742: https://github.com/schemathesis/schemathesis/issues/742
.. _#741: https://github.com/schemathesis/schemathesis/issues/741
.. _#738: https://github.com/schemathesis/schemathesis/issues/738
.. _#737: https://github.com/schemathesis/schemathesis/issues/737
.. _#734: https://github.com/schemathesis/schemathesis/issues/734
.. _#731: https://github.com/schemathesis/schemathesis/issues/731
.. _#721: https://github.com/schemathesis/schemathesis/issues/721
.. _#719: https://github.com/schemathesis/schemathesis/issues/719
.. _#717: https://github.com/schemathesis/schemathesis/issues/717
.. _#718: https://github.com/schemathesis/schemathesis/issues/718
.. _#716: https://github.com/schemathesis/schemathesis/issues/716
.. _#715: https://github.com/schemathesis/schemathesis/issues/715
.. _#708: https://github.com/schemathesis/schemathesis/issues/708
.. _#706: https://github.com/schemathesis/schemathesis/issues/706
.. _#705: https://github.com/schemathesis/schemathesis/issues/705
.. _#702: https://github.com/schemathesis/schemathesis/issues/702
.. _#700: https://github.com/schemathesis/schemathesis/issues/700
.. _#695: https://github.com/schemathesis/schemathesis/issues/695
.. _#692: https://github.com/schemathesis/schemathesis/issues/692
.. _#690: https://github.com/schemathesis/schemathesis/issues/690
.. _#689: https://github.com/schemathesis/schemathesis/issues/689
.. _#686: https://github.com/schemathesis/schemathesis/issues/686
.. _#684: https://github.com/schemathesis/schemathesis/issues/684
.. _#675: https://github.com/schemathesis/schemathesis/issues/675
.. _#673: https://github.com/schemathesis/schemathesis/issues/673
.. _#672: https://github.com/schemathesis/schemathesis/issues/672
.. _#671: https://github.com/schemathesis/schemathesis/issues/671
.. _#668: https://github.com/schemathesis/schemathesis/issues/668
.. _#667: https://github.com/schemathesis/schemathesis/issues/667
.. _#665: https://github.com/schemathesis/schemathesis/issues/665
.. _#661: https://github.com/schemathesis/schemathesis/issues/661
.. _#660: https://github.com/schemathesis/schemathesis/issues/660
.. _#658: https://github.com/schemathesis/schemathesis/issues/658
.. _#656: https://github.com/schemathesis/schemathesis/issues/656
.. _#651: https://github.com/schemathesis/schemathesis/issues/651
.. _#649: https://github.com/schemathesis/schemathesis/issues/649
.. _#647: https://github.com/schemathesis/schemathesis/issues/647
.. _#641: https://github.com/schemathesis/schemathesis/issues/641
.. _#640: https://github.com/schemathesis/schemathesis/issues/640
.. _#636: https://github.com/schemathesis/schemathesis/issues/636
.. _#631: https://github.com/schemathesis/schemathesis/issues/631
.. _#629: https://github.com/schemathesis/schemathesis/issues/629
.. _#622: https://github.com/schemathesis/schemathesis/issues/622
.. _#621: https://github.com/schemathesis/schemathesis/issues/621
.. _#618: https://github.com/schemathesis/schemathesis/issues/618
.. _#617: https://github.com/schemathesis/schemathesis/issues/617
.. _#616: https://github.com/schemathesis/schemathesis/issues/616
.. _#614: https://github.com/schemathesis/schemathesis/issues/614
.. _#612: https://github.com/schemathesis/schemathesis/issues/612
.. _#600: https://github.com/schemathesis/schemathesis/issues/600
.. _#599: https://github.com/schemathesis/schemathesis/issues/599
.. _#598: https://github.com/schemathesis/schemathesis/issues/598
.. _#596: https://github.com/schemathesis/schemathesis/issues/596
.. _#594: https://github.com/schemathesis/schemathesis/issues/594
.. _#589: https://github.com/schemathesis/schemathesis/issues/589
.. _#586: https://github.com/schemathesis/schemathesis/issues/586
.. _#582: https://github.com/schemathesis/schemathesis/issues/582
.. _#579: https://github.com/schemathesis/schemathesis/issues/579
.. _#575: https://github.com/schemathesis/schemathesis/issues/575
.. _#571: https://github.com/schemathesis/schemathesis/issues/571
.. _#566: https://github.com/schemathesis/schemathesis/issues/566
.. _#562: https://github.com/schemathesis/schemathesis/issues/562
.. _#559: https://github.com/schemathesis/schemathesis/issues/559
.. _#548: https://github.com/schemathesis/schemathesis/issues/548
.. _#546: https://github.com/schemathesis/schemathesis/issues/546
.. _#542: https://github.com/schemathesis/schemathesis/issues/542
.. _#540: https://github.com/schemathesis/schemathesis/issues/540
.. _#539: https://github.com/schemathesis/schemathesis/issues/539
.. _#537: https://github.com/schemathesis/schemathesis/issues/537
.. _#531: https://github.com/schemathesis/schemathesis/issues/531
.. _#529: https://github.com/schemathesis/schemathesis/issues/529
.. _#521: https://github.com/schemathesis/schemathesis/issues/521
.. _#519: https://github.com/schemathesis/schemathesis/issues/519
.. _#513: https://github.com/schemathesis/schemathesis/issues/513
.. _#511: https://github.com/schemathesis/schemathesis/issues/511
.. _#504: https://github.com/schemathesis/schemathesis/issues/504
.. _#503: https://github.com/schemathesis/schemathesis/issues/503
.. _#499: https://github.com/schemathesis/schemathesis/issues/499
.. _#497: https://github.com/schemathesis/schemathesis/issues/497
.. _#496: https://github.com/schemathesis/schemathesis/issues/496
.. _#492: https://github.com/schemathesis/schemathesis/issues/492
.. _#489: https://github.com/schemathesis/schemathesis/issues/489
.. _#485: https://github.com/schemathesis/schemathesis/issues/485
.. _#473: https://github.com/schemathesis/schemathesis/issues/473
.. _#469: https://github.com/schemathesis/schemathesis/issues/469
.. _#468: https://github.com/schemathesis/schemathesis/issues/468
.. _#467: https://github.com/schemathesis/schemathesis/issues/467
.. _#463: https://github.com/schemathesis/schemathesis/issues/463
.. _#461: https://github.com/schemathesis/schemathesis/issues/461
.. _#458: https://github.com/schemathesis/schemathesis/issues/458
.. _#457: https://github.com/schemathesis/schemathesis/issues/457
.. _#451: https://github.com/schemathesis/schemathesis/issues/451
.. _#450: https://github.com/schemathesis/schemathesis/issues/450
.. _#448: https://github.com/schemathesis/schemathesis/issues/448
.. _#440: https://github.com/schemathesis/schemathesis/issues/440
.. _#439: https://github.com/schemathesis/schemathesis/issues/439
.. _#436: https://github.com/schemathesis/schemathesis/issues/436
.. _#435: https://github.com/schemathesis/schemathesis/issues/435
.. _#433: https://github.com/schemathesis/schemathesis/issues/433
.. _#429: https://github.com/schemathesis/schemathesis/issues/429
.. _#427: https://github.com/schemathesis/schemathesis/issues/427
.. _#424: https://github.com/schemathesis/schemathesis/issues/424
.. _#418: https://github.com/schemathesis/schemathesis/issues/418
.. _#416: https://github.com/schemathesis/schemathesis/issues/416
.. _#412: https://github.com/schemathesis/schemathesis/issues/412
.. _#410: https://github.com/schemathesis/schemathesis/issues/410
.. _#407: https://github.com/schemathesis/schemathesis/issues/407
.. _#406: https://github.com/schemathesis/schemathesis/issues/406
.. _#405: https://github.com/schemathesis/schemathesis/issues/405
.. _#404: https://github.com/schemathesis/schemathesis/issues/404
.. _#403: https://github.com/schemathesis/schemathesis/issues/403
.. _#400: https://github.com/schemathesis/schemathesis/issues/400
.. _#394: https://github.com/schemathesis/schemathesis/issues/394
.. _#391: https://github.com/schemathesis/schemathesis/issues/391
.. _#386: https://github.com/schemathesis/schemathesis/issues/386
.. _#383: https://github.com/schemathesis/schemathesis/issues/383
.. _#381: https://github.com/schemathesis/schemathesis/issues/381
.. _#379: https://github.com/schemathesis/schemathesis/issues/379
.. _#378: https://github.com/schemathesis/schemathesis/issues/378
.. _#376: https://github.com/schemathesis/schemathesis/issues/376
.. _#374: https://github.com/schemathesis/schemathesis/issues/374
.. _#371: https://github.com/schemathesis/schemathesis/issues/371
.. _#367: https://github.com/schemathesis/schemathesis/issues/367
.. _#365: https://github.com/schemathesis/schemathesis/issues/365
.. _#361: https://github.com/schemathesis/schemathesis/issues/361
.. _#350: https://github.com/schemathesis/schemathesis/issues/350
.. _#349: https://github.com/schemathesis/schemathesis/issues/349
.. _#338: https://github.com/schemathesis/schemathesis/issues/338
.. _#335: https://github.com/schemathesis/schemathesis/issues/335
.. _#332: https://github.com/schemathesis/schemathesis/issues/332
.. _#330: https://github.com/schemathesis/schemathesis/issues/330
.. _#329: https://github.com/schemathesis/schemathesis/issues/329
.. _#322: https://github.com/schemathesis/schemathesis/issues/322
.. _#319: https://github.com/schemathesis/schemathesis/issues/319
.. _#315: https://github.com/schemathesis/schemathesis/issues/315
.. _#313: https://github.com/schemathesis/schemathesis/issues/313
.. _#311: https://github.com/schemathesis/schemathesis/issues/311
.. _#308: https://github.com/schemathesis/schemathesis/issues/308
.. _#305: https://github.com/schemathesis/schemathesis/issues/305
.. _#303: https://github.com/schemathesis/schemathesis/issues/303
.. _#301: https://github.com/schemathesis/schemathesis/issues/301
.. _#295: https://github.com/schemathesis/schemathesis/issues/295
.. _#294: https://github.com/schemathesis/schemathesis/issues/294
.. _#286: https://github.com/schemathesis/schemathesis/issues/286
.. _#282: https://github.com/schemathesis/schemathesis/issues/282
.. _#280: https://github.com/schemathesis/schemathesis/issues/280
.. _#272: https://github.com/schemathesis/schemathesis/issues/272
.. _#270: https://github.com/schemathesis/schemathesis/issues/270
.. _#268: https://github.com/schemathesis/schemathesis/issues/268
.. _#267: https://github.com/schemathesis/schemathesis/issues/267
.. _#266: https://github.com/schemathesis/schemathesis/issues/266
.. _#261: https://github.com/schemathesis/schemathesis/issues/261
.. _#256: https://github.com/schemathesis/schemathesis/issues/256
.. _#255: https://github.com/schemathesis/schemathesis/issues/255
.. _#254: https://github.com/schemathesis/schemathesis/issues/254
.. _#253: https://github.com/schemathesis/schemathesis/issues/253
.. _#248: https://github.com/schemathesis/schemathesis/issues/248
.. _#246: https://github.com/schemathesis/schemathesis/issues/246
.. _#237: https://github.com/schemathesis/schemathesis/issues/237
.. _#236: https://github.com/schemathesis/schemathesis/issues/236
.. _#218: https://github.com/schemathesis/schemathesis/issues/218
.. _#216: https://github.com/schemathesis/schemathesis/issues/216
.. _#215: https://github.com/schemathesis/schemathesis/issues/215
.. _#214: https://github.com/schemathesis/schemathesis/issues/214
.. _#212: https://github.com/schemathesis/schemathesis/issues/212
.. _#211: https://github.com/schemathesis/schemathesis/issues/211
.. _#209: https://github.com/schemathesis/schemathesis/issues/209
.. _#207: https://github.com/schemathesis/schemathesis/issues/207
.. _#206: https://github.com/schemathesis/schemathesis/issues/206
.. _#204: https://github.com/schemathesis/schemathesis/issues/204
.. _#203: https://github.com/schemathesis/schemathesis/issues/203
.. _#200: https://github.com/schemathesis/schemathesis/issues/200
.. _#199: https://github.com/schemathesis/schemathesis/issues/199
.. _#197: https://github.com/schemathesis/schemathesis/issues/197
.. _#196: https://github.com/schemathesis/schemathesis/issues/196
.. _#194: https://github.com/schemathesis/schemathesis/issues/194
.. _#191: https://github.com/schemathesis/schemathesis/issues/191
.. _#189: https://github.com/schemathesis/schemathesis/issues/189
.. _#188: https://github.com/schemathesis/schemathesis/issues/188
.. _#181: https://github.com/schemathesis/schemathesis/issues/181
.. _#173: https://github.com/schemathesis/schemathesis/issues/173
.. _#172: https://github.com/schemathesis/schemathesis/issues/172
.. _#167: https://github.com/schemathesis/schemathesis/issues/167
.. _#153: https://github.com/schemathesis/schemathesis/issues/153
.. _#149: https://github.com/schemathesis/schemathesis/issues/149
.. _#147: https://github.com/schemathesis/schemathesis/issues/147
.. _#144: https://github.com/schemathesis/schemathesis/issues/144
.. _#139: https://github.com/schemathesis/schemathesis/issues/139
.. _#138: https://github.com/schemathesis/schemathesis/issues/138
.. _#137: https://github.com/schemathesis/schemathesis/issues/137
.. _#134: https://github.com/schemathesis/schemathesis/issues/134
.. _#130: https://github.com/schemathesis/schemathesis/issues/130
.. _#127: https://github.com/schemathesis/schemathesis/issues/127
.. _#126: https://github.com/schemathesis/schemathesis/issues/126
.. _#125: https://github.com/schemathesis/schemathesis/issues/125
.. _#121: https://github.com/schemathesis/schemathesis/issues/121
.. _#119: https://github.com/schemathesis/schemathesis/issues/119
.. _#118: https://github.com/schemathesis/schemathesis/issues/118
.. _#115: https://github.com/schemathesis/schemathesis/issues/115
.. _#110: https://github.com/schemathesis/schemathesis/issues/110
.. _#109: https://github.com/schemathesis/schemathesis/issues/109
.. _#107: https://github.com/schemathesis/schemathesis/issues/107
.. _#106: https://github.com/schemathesis/schemathesis/issues/106
.. _#104: https://github.com/schemathesis/schemathesis/issues/104
.. _#101: https://github.com/schemathesis/schemathesis/issues/101
.. _#99: https://github.com/schemathesis/schemathesis/issues/99
.. _#98: https://github.com/schemathesis/schemathesis/issues/98
.. _#94: https://github.com/schemathesis/schemathesis/issues/94
.. _#92: https://github.com/schemathesis/schemathesis/issues/92
.. _#91: https://github.com/schemathesis/schemathesis/issues/91
.. _#90: https://github.com/schemathesis/schemathesis/issues/90
.. _#78: https://github.com/schemathesis/schemathesis/issues/78
.. _#75: https://github.com/schemathesis/schemathesis/issues/75
.. _#69: https://github.com/schemathesis/schemathesis/issues/69
.. _#65: https://github.com/schemathesis/schemathesis/issues/65
.. _#64: https://github.com/schemathesis/schemathesis/issues/64
.. _#58: https://github.com/schemathesis/schemathesis/issues/58
.. _#55: https://github.com/schemathesis/schemathesis/issues/55
.. _#45: https://github.com/schemathesis/schemathesis/issues/45
.. _#40: https://github.com/schemathesis/schemathesis/issues/40
.. _#35: https://github.com/schemathesis/schemathesis/issues/35
.. _#34: https://github.com/schemathesis/schemathesis/issues/34
.. _#31: https://github.com/schemathesis/schemathesis/issues/31
.. _#30: https://github.com/schemathesis/schemathesis/issues/30
.. _#29: https://github.com/schemathesis/schemathesis/issues/29
.. _#28: https://github.com/schemathesis/schemathesis/issues/28
.. _#24: https://github.com/schemathesis/schemathesis/issues/24
.. _#21: https://github.com/schemathesis/schemathesis/issues/21
.. _#18: https://github.com/schemathesis/schemathesis/issues/18
.. _#17: https://github.com/schemathesis/schemathesis/issues/17
.. _#16: https://github.com/schemathesis/schemathesis/issues/16
.. _#10: https://github.com/schemathesis/schemathesis/issues/10
.. _#7: https://github.com/schemathesis/schemathesis/issues/7
.. _#6: https://github.com/schemathesis/schemathesis/issues/6
