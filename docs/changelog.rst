Changelog
=========

`Unreleased`_ - TBD
-------------------

`2.8.5`_ - 2020-12-15
---------------------

**Added**

- ``auto`` variant for the ``--workers`` CLI option that automatically detects the number of available CPU cores to run tests on. `#917`_

`2.8.4`_ - 2020-11-27
---------------------

**Fixed**

- Use ``--request-tls-verify`` during schema loading as well. `#897`_

`2.8.3`_ - 2020-11-27
---------------------

**Added**

- Display failed response payload in the error output for the ``pytest`` plugin. `#895`_

**Changed**

- In pytest plugin output, Schemathesis error classes use the `CheckFailed` name. Before, they had not readable "internal" names.
- Hypothesis falsifying examples. The code does not include ``Case`` attributes with default values to improve readability. `#886`_

`2.8.2`_ - 2020-11-25
---------------------

**Fixed**

- Internal error in CLI, when the ``base_url`` is an invalid IPv6. `#890`_
- Internal error in CLI, when a malformed regex is passed to ``-E`` / ``-M`` / ``-T`` / ``-O`` CLI options. `#889`_

`2.8.1`_ - 2020-11-24
---------------------

**Added**

- ``--force-schema-version`` CLI option to force Schemathesis to use the specific Open API spec version when parsing the schema. `#876`_

**Changed**

- The ``content_type_conformance`` check now raises a well-formed error message when encounters a malformed media type value. `#877`_

**Fixed**

- Internal error during verifying explicit examples if an example has no ``value`` key. `#882`_

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

`2.7.7`_ - 2020-11-13
---------------------

**Fixed**

- Missed ``headers`` in ``Endpoint.partial_deepcopy``.

`2.7.6`_ - 2020-11-12
---------------------

**Added**

- An option to set data generation methods. At the moment, it includes only "positive", which means that Schemathesis will
  generate data that matches the schema.

**Fixed**

- Pinned dependency on ``attrs`` that caused an error on fresh installations. `#858`_

`2.7.5`_ - 2020-11-09
---------------------

**Fixed**

- Invalid keyword in code samples that Schemathesis suggests to run to reproduce errors. `#851`_

`2.7.4`_ - 2020-11-07
---------------------

**Added**

- New ``relative_path`` property for ``BeforeExecution`` and ``AfterExecution`` events. It represents an operation
  path as it is in the schema definition.

`2.7.3`_ - 2020-11-05
---------------------

**Fixed**

- Internal error on malformed JSON when the ``response_conformance`` check is used. `#832`_

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

`2.7.1`_ - 2020-10-22
---------------------

**Fixed**

- Adding new Open API links via the ``add_link`` method, when the related PathItem contains a reference. `#824`_

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

`2.6.1`_ - 2020-10-19
---------------------

**Added**

- New method ``as_curl_command`` added to the ``Case`` class. `#689`_

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
- Ability to skip deprecated endpoints with ``--skip-deprecated-endpoints`` CLI option and ``skip_deprecated_endpoints=True`` argument to schema loaders. `#715`_

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

`2.5.1`_ - 2020-09-30
---------------------

This release contains only documentation updates which are necessary to upload to PyPI.

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

`2.4.1`_ - 2020-09-17
---------------------

**Changed**

- Hide ``Case.endpoint`` from representation. Its representation decreases the usability of the pytest's output. `#719`_
- Return registered functions from ``register_target`` and ``register_check`` decorators. `#721`_

**Fixed**


- Possible ``IndexError`` when a user-defined check raises an exception without a message. `#718`_

`2.4.0`_ - 2020-09-15
---------------------

**Added**


- Ability to register custom targets for targeted testing. `#686`_

**Changed**


- The ``AfterExecution`` event now has ``path`` and ``method`` fields, similar to the ``BeforeExecution`` one.
  The goal is to make these events self-contained, which improves their usability.

`2.3.4`_ - 2020-09-11
---------------------

**Changed**


- The default Hypothesis's ``deadline`` setting for tests with ``schema.parametrize`` is set to 500 ms for consistency with the CLI behavior. `#705`_

**Fixed**


- Encoding error when writing a cassette on Windows. `#708`_

`2.3.3`_ - 2020-08-04
---------------------

**Fixed**


- ``KeyError`` during the ``content_type_conformance`` check if the response has no ``Content-Type`` header. `#692`_

`2.3.2`_ - 2020-08-04
---------------------

**Added**


- Run checks conditionally.

`2.3.1`_ - 2020-07-28
---------------------

**Fixed**


- ``IndexError`` when ``examples`` list is empty.

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

`2.2.1`_ - 2020-07-22
---------------------

**Fixed**


- Possible ``UnicodeEncodeError`` during generation of ``Authorization`` header values for endpoints with ``basic`` security scheme. `#656`_

`2.2.0`_ - 2020-07-14
---------------------

**Added**


- ``schemathesis.graphql.from_dict`` loader allows you to use GraphQL schemas represented as a dictionary for testing.
- ``before_load_schema`` hook for GraphQL schemas.

**Fixed**


- Serialization of non-string parameters. `#651`_

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

`1.10.0`_ - 2020-06-28
----------------------

**Added**


- ``loaders.from_asgi`` supports making calls to ASGI-compliant application (For example: FastAPI). `#521`_
- Support for GraphQL strategies.

**Fixed**


- Passing custom headers to schema loader for WSGI / ASGI apps. `#631`_

`1.9.1`_ - 2020-06-21
---------------------

**Fixed**


- Schema validation error on schemas containing numeric values in scientific notation without a dot. `#629`_

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

`1.8.0`_ - 2020-06-15
---------------------

**Fixed**


- Tests with invalid schemas are marked as failed instead of passed when ``hypothesis-jsonschema>=0.16`` is installed. `#614`_
- ``KeyError`` during creating an endpoint strategy if it contains a reference. `#612`_

**Changed**


- Require ``hypothesis-jsonschema>=0.16``. `#614`_
- Pass original ``InvalidSchema`` text to ``pytest.fail`` call.

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

`1.6.3`_ - 2020-05-26
---------------------

**Fixed**


- Support for a colon symbol (``:``) inside of a header value passed via CLI. `#596`_

`1.6.2`_ - 2020-05-15
---------------------

**Fixed**


- Partially generated explicit examples are always valid and can be used in requests. `#582`_

`1.6.1`_ - 2020-05-13
---------------------

**Changed**


- Look at the current working directory when loading hooks for CLI. `#586`_

`1.6.0`_ - 2020-05-10
---------------------

**Added**


- New ``before_add_examples`` hook. `#571`_
- New ``after_init_cli_run_handlers`` hook. `#575`_

**Fixed**


- Passing ``workers_num`` to ``ThreadPoolRunner`` leads to always using 2 workers in this worker kind. `#579`_

`1.5.1`_ - 2020-05-08
---------------------

**Fixed**


- Display proper headers in reproduction code when headers are overridden. `#566`_

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

`1.3.4`_ - 2020-04-30
---------------------

**Fixed**


- Validation of nullable properties in ``response_schema_conformance`` check introduced in ``1.3.0``. `#542`_

`1.3.3`_ - 2020-04-29
---------------------

**Changed**


- Update ``pytest-subtests`` pin to ``>=0.2.1,<1.0``. `#537`_

`1.3.2`_ - 2020-04-27
---------------------

**Added**


- Show exceptions if they happened during loading a WSGI application. Option ``--show-errors-tracebacks`` will display a
  full traceback.

`1.3.1`_ - 2020-04-27
---------------------

**Fixed**


- Packaging issue

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

`1.2.0`_ - 2020-04-15
---------------------

**Added**


- Per-test hooks for modification of data generation strategies. `#492`_
- Support for ``x-example`` vendor extension in Open API 2.0. `#504`_
- Sanity validation for the input schema & loader in ``runner.prepare``. `#499`_

`1.1.2`_ - 2020-04-14
---------------------

**Fixed**


- Support for custom loaders in ``runner``. Now all built-in loaders are supported as an argument to ``runner.prepare``. `#496`_
- ``from_wsgi`` loader accepts custom keyword arguments that will be passed to ``client.get`` when accessing the schema. `#497`_

`1.1.1`_ - 2020-04-12
---------------------

**Fixed**


- Mistakenly applied Open API -> JSON Schema Draft 7 conversion. It should be Draft 4. `#489`_
- Using wrong validator in ``response_schema_conformance`` check. It should be Draft 4 validator. `#468`_

`1.1.0`_ - 2020-04-08
---------------------

**Fixed**


- Response schema check for recursive schemas. `#468`_

**Changed**


- App loading in ``runner``. Now it accepts application as an importable string, rather than an instance. It is done to make it possible to execute a runner in a subprocess. Otherwise, apps can't be easily serialized and transferred into another process.
- Runner events structure. All data in events is static from now. There are no references to ``BaseSchema``, ``Endpoint`` or similar objects that may calculate data dynamically. This is done to make events serializable and not tied to Python object, which decouples any ``runner`` consumer from implementation details. It will help make ``runner`` usable in more cases (e.g., web application) since events can be serialized to JSON and used in any environment.
  Another related change is that Python exceptions are not propagated anymore - they are replaced with the ``InternalError`` event that should be handled accordingly.

`1.0.5`_ - 2020-04-03
---------------------

**Fixed**


- Open API 3. Handling of endpoints that contain ``multipart/form-data`` media types.
  Previously only file upload endpoints were working correctly. `#473`_

`1.0.4`_ - 2020-04-03
---------------------

**Fixed**


- ``OpenApi30.get_content_types`` behavior, introduced in `8aeee1a <https://github.com/schemathesis/schemathesis/commit/8aeee1ab2c6c97d94272dde4790f5efac3951aed>`_. `#469`_

`1.0.3`_ - 2020-04-03
---------------------

**Fixed**


- Precedence of ``produces`` keywords for Swagger 2.0 schemas. Now, operation-level ``produces`` overrides schema-level ``produces`` as specified in the specification. `#463`_
- Content-type conformance check for Open API 3.0 schemas. `#461`_
- Pytest 5.4 warning for test functions without parametrization. `#451`_

`1.0.2`_ - 2020-04-02
---------------------

**Fixed**


- Handling of fields in ``paths`` that are not operations, but allowed by the Open API spec. `#457`_
- Pytest 5.4 warning about deprecated ``Node`` initialization usage. `#451`_

`1.0.1`_ - 2020-04-01
---------------------

**Fixed**


- Processing of explicit examples in Open API 3.0 when there are multiple parameters in the same location (e.g. ``path``)
  contain ``example`` value. They are properly combined now. `#450`_

`1.0.0`_ - 2020-03-31
---------------------

**Changed**


- Move processing of ``runner`` parameters to ``runner.prepare``. This change will provide better code reuse since all users of ``runner`` (e.g., if you extended it in your project) need some kind of input parameters handling, which was implemented only in Schemathesis CLI. It is not backward-compatible. If you didn't use ``runner`` directly, then this change should not have a visible effect on your use-case.

`0.28.0`_ - 2020-03-31
----------------------

**Fixed**


- Handling of schemas that use ``x-*`` custom properties. `#448`_

**Removed**


- Deprecated ``runner.execute``. Use ``runner.prepare`` instead.

`0.27.0`_ - 2020-03-31
----------------------

Deprecated


- ``runner.execute`` should not be used, since ``runner.prepare`` provides a more flexible interface to test execution.

**Removed**


- Deprecated ``Parametrizer`` class. Use ``schemathesis.from_path`` as a replacement for ``Parametrizer.from_path``.

`0.26.1`_ - 2020-03-24
----------------------

**Fixed**


- Limit recursion depth while resolving JSON schema to handle recursion without breaking. `#435`_

`0.26.0`_ - 2020-03-19
----------------------

**Fixed**


- Filter problematic path template variables containing ``"/"``, or ``"%2F"`` url encoded. `#440`_
- Filter invalid empty ``""`` path template variables. `#439`_
- Typo in a help message in the CLI output. `#436`_

`0.25.1`_ - 2020-03-09
----------------------

**Changed**


- Allow ``werkzeug`` >= 1.0.0. `#433`_

`0.25.0`_ - 2020-02-27
----------------------

**Changed**


- Handling of explicit examples from schemas. Now, if there are examples for multiple locations
  (e.g., for body and query) then they will be combined into a single example. `#424`_

`0.24.5`_ - 2020-02-26
----------------------

**Fixed**


- Error during ``pytest`` collection on objects with custom ``__getattr__`` method and therefore pass ``is_schemathesis`` check. `#429`_

`0.24.4`_ - 2020-02-22
----------------------

**Fixed**


- Resolving references when the schema is loaded from a file on Windows. `#418`_

`0.24.3`_ - 2020-02-10
----------------------

**Fixed**


- Not copied ``validate_schema`` parameter in ``BaseSchema.parametrize``. Regression after implementing `#383`_
- Missing ``app``, ``location`` and ``hooks`` parameters in schema when used with ``BaseSchema.parametrize``. `#416`_

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

`0.24.1`_ - 2020-02-08
----------------------

**Fixed**


- CLI crash on Windows and Python < 3.8 when the schema path contains characters unrepresentable at the OS level. `#400`_

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

`0.23.7`_ - 2020-01-30
----------------------

**Added**


- ``-x``/``--exitfirst`` CLI option to exit after the first failed test. `#378`_

**Fixed**


- Handling examples of parameters in Open API 3.0. `#381`_

`0.23.6`_ - 2020-01-28
----------------------

**Added**


- ``all`` variant for ``--checks`` CLI option to use all available checks. `#374`_

**Changed**


- Use built-in ``importlib.metadata`` on Python 3.8. `#376`_

`0.23.5`_ - 2020-01-24
----------------------

**Fixed**


- Generation of invalid values in ``Case.cookies``. `#371`_

`0.23.4`_ - 2020-01-22
----------------------

**Fixed**


- Converting ``exclusiveMinimum`` & ``exclusiveMaximum`` fields to JSON Schema. `#367`_

`0.23.3`_ - 2020-01-21
----------------------

**Fixed**


- Filter out surrogate pairs from the query string.

`0.23.2`_ - 2020-01-16
----------------------

**Fixed**


- Prevent ``KeyError`` when the response does not have the "Content-Type" header. `#365`_

`0.23.1`_ - 2020-01-15
----------------------

**Fixed**


- Dockerfile entrypoint was not working as per docs. `#361`_

`0.23.0`_ - 2020-01-15
----------------------

**Added**


- Hooks for strategy modification. `#313`_
- Input schema validation. Use ``--validate-schema=false`` to disable it in CLI and ``validate_schema=False`` argument in loaders. `#110`_

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

`0.21.0`_ - 2019-12-20
----------------------

**Added**


- Support for AioHTTP applications in CLI. `#329`_

`0.20.5`_ - 2019-12-18
----------------------

**Fixed**


- Compatibility with the latest release of ``hypothesis-jsonschema`` and setting its minimal required version to ``0.9.13``. `#338`_

`0.20.4`_ - 2019-12-17
----------------------

**Fixed**


- Handling ``nullable`` attribute in Open API schemas. `#335`_

`0.20.3`_ - 2019-12-17
----------------------

**Fixed**


- Usage of the response status code conformance check with old ``requests`` version. `#330`_

`0.20.2`_ - 2019-12-14
----------------------

**Fixed**


- Response schema conformance check for Open API 3.0. `#332`_

`0.20.1`_ - 2019-12-13
----------------------

**Added**


- Support for response code ranges. `#330`_

`0.20.0`_ - 2019-12-12
----------------------

**Added**


- WSGI apps support. `#31`_
- ``Case.validate_response`` for running built-in checks against app's response. `#319`_

**Changed**


- Checks receive ``Case`` instance as a second argument instead of ``TestResult``.
  This was done for making checks usable in Python tests via ``Case.validate_response``.
  Endpoint and schema are accessible via ``case.endpoint`` and ``case.endpoint.schema``.

`0.19.1`_ - 2019-12-11
----------------------

**Fixed**


- Compatibility with Hypothesis >= 4.53.2. `#322`_

`0.19.0`_ - 2019-12-02
----------------------

**Added**


- Concurrent test execution in CLI / runner. `#91`_
- update importlib_metadata pin to ``^1.1``. `#315`_

`0.18.1`_ - 2019-11-28
----------------------

**Fixed**


- Validation of the ``base-url`` CLI parameter. `#311`_

`0.18.0`_ - 2019-11-27
----------------------

**Added**


- Resolving references in ``PathItem`` objects. `#301`_

**Fixed**


- Resolving of relative paths in schemas. `#303`_
- Loading string dates as ``datetime.date`` objects in YAML loader. `#305`_

`0.17.0`_ - 2019-11-21
----------------------

**Added**


- Resolving references that point to different files. `#294`_

**Changed**


- Keyboard interrupt is now handled during the CLI run, and the summary is displayed in the output. `#295`_

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

`0.13.2`_ - 2019-11-05
----------------------

**Fixed**


- ``IndexError`` when Hypothesis found inconsistent test results during the test execution in the runner. `#236`_

`0.13.1`_ - 2019-11-05
----------------------

**Added**


- Support for binary format `#197`_

**Fixed**


- Error that happens when there are no success checks in the statistic in CLI. `#237`_

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

`0.12.2`_ - 2019-10-30
----------------------

**Fixed**


- Wrong handling of the ``base_url`` parameter in runner and ``Case.call`` if it has a trailing slash. `#194`_ and `#199`_
- Do not send any payload with GET requests. `#200`_

`0.12.1`_ - 2019-10-28
----------------------

**Fixed**


- Handling for errors other than ``AssertionError`` and ``HypothesisException`` in the runner. `#189`_
- CLI failing on the case when there are tests, but no checks were performed. `#191`_

**Changed**


- Display the "SUMMARY" section in the CLI output for empty test suites.

`0.12.0`_ - 2019-10-28
----------------------

**Added**


- Display progress during the CLI run. `#125`_

**Fixed**


- Test server-generated wrong schema when the ``endpoints`` option is passed via CLI. `#173`_
- Error message if the schema is not found in CLI. `#172`_

**Changed**


- Continue running tests on hypothesis error. `#137`_

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

`0.10.0`_ - 2019-10-14
----------------------

**Added**


- HTTP Digest Auth support. `#106`_
- Support for Hypothesis settings in CLI & Runner. `#107`_
- ``Case.call`` and ``Case.as_requests_kwargs`` convenience methods. `#109`_
- Local development server. `#126`_

**Removed**


- Autogenerated ``runner.StatsCollector.__repr__`` to make Hypothesis output more readable.

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

`0.8.1`_ - 2019-10-04
---------------------

**Fixed**


- Wrap each test in ``suppress`` so the runner doesn't stop after the first test failure.

`0.8.0`_ - 2019-10-04
---------------------

**Added**


- CLI tool invoked by the ``schemathesis`` command. `#30`_
- New arguments ``api_options``, ``loader_options`` and ``loader`` for test executor. `#90`_
- A mapping interface for schemas & convenience methods for direct strategy access. `#98`_

**Fixed**


- Runner stopping on the first falsifying example. `#99`_

`0.7.3`_ - 2019-09-30
---------------------

**Fixed**


- Filtration in lazy loaders.

`0.7.2`_ - 2019-09-30
---------------------

**Added**


- Support for type "file" for Swagger 2.0. `#78`_
- Support for filtering in loaders. `#75`_

**Fixed**


- Conflict for lazy schema filtering. `#64`_

`0.7.1`_ - 2019-09-27
---------------------

**Added**


- Support for ``x-nullable`` extension. `#45`_

`0.7.0`_ - 2019-09-26
---------------------

**Added**


- Support for the ``cookie`` parameter in OpenAPI 3.0 schemas. `#21`_
- Support for the ``formData`` parameter in Swagger 2.0 schemas. `#6`_
- Test executor. `#28`_

**Fixed**


- Using ``hypothesis.settings`` decorator with test functions created from ``from_pytest_fixture`` loader. `#69`_

`0.6.0`_ - 2019-09-24
---------------------

**Added**


- Parametrizing tests from a pytest fixture via ``pytest-subtests``. `#58`_

**Changed**


- Rename module ``readers`` to ``loaders``.
- Rename ``parametrize`` parameters. ``filter_endpoint`` to ``endpoint`` and ``filter_method`` to ``method``.

**Removed**


- Substring match for method/endpoint filters. To avoid clashing with escaped chars in endpoints keys in schemas.

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

`0.4.1`_ - 2019-09-11
---------------------

**Fixed**


- Possibly unhandled exception during ``hasattr`` check in ``is_schemathesis_test``.

`0.4.0`_ - 2019-09-10
---------------------

**Fixed**


- Resolving all inner references in objects. `#34`_

**Changed**


- ``jsonschema.RefResolver`` is now used for reference resolving. `#35`_

`0.3.0`_ - 2019-09-06
---------------------

**Added**


- ``Parametrizer.from_uri`` method to construct parametrizer instances from URIs. `#24`_

**Removed**


- Possibility to use ``Parametrizer.parametrize`` and custom ``Parametrizer`` kwargs for passing config options
  to ``hypothesis.settings``. Use ``hypothesis.settings`` decorators on tests instead.

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

0.1.0 - 2019-06-28
------------------

- Initial public release

.. _Unreleased: https://github.com/schemathesis/schemathesis/compare/v2.8.5...HEAD
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

.. _#917: https://github.com/schemathesis/schemathesis/issues/917
.. _#897: https://github.com/schemathesis/schemathesis/issues/897
.. _#895: https://github.com/schemathesis/schemathesis/issues/895
.. _#890: https://github.com/schemathesis/schemathesis/issues/890
.. _#889: https://github.com/schemathesis/schemathesis/issues/889
.. _#886: https://github.com/schemathesis/schemathesis/issues/886
.. _#882: https://github.com/schemathesis/schemathesis/issues/882
.. _#877: https://github.com/schemathesis/schemathesis/issues/877
.. _#876: https://github.com/schemathesis/schemathesis/issues/876
.. _#874: https://github.com/schemathesis/schemathesis/issues/874
.. _#872: https://github.com/schemathesis/schemathesis/issues/872
.. _#870: https://github.com/schemathesis/schemathesis/issues/870
.. _#858: https://github.com/schemathesis/schemathesis/issues/858
.. _#855: https://github.com/schemathesis/schemathesis/issues/855
.. _#851: https://github.com/schemathesis/schemathesis/issues/851
.. _#844: https://github.com/schemathesis/schemathesis/issues/844
.. _#841: https://github.com/schemathesis/schemathesis/issues/841
.. _#839: https://github.com/schemathesis/schemathesis/issues/839
.. _#836: https://github.com/schemathesis/schemathesis/issues/836
.. _#832: https://github.com/schemathesis/schemathesis/issues/832
.. _#830: https://github.com/schemathesis/schemathesis/issues/830
.. _#824: https://github.com/schemathesis/schemathesis/issues/824
.. _#816: https://github.com/schemathesis/schemathesis/issues/816
.. _#814: https://github.com/schemathesis/schemathesis/issues/814
.. _#795: https://github.com/schemathesis/schemathesis/issues/795
.. _#793: https://github.com/schemathesis/schemathesis/issues/793
.. _#788: https://github.com/schemathesis/schemathesis/issues/788
.. _#783: https://github.com/schemathesis/schemathesis/issues/783
.. _#768: https://github.com/schemathesis/schemathesis/issues/768
.. _#757: https://github.com/schemathesis/schemathesis/issues/757
.. _#748: https://github.com/schemathesis/schemathesis/issues/748
.. _#742: https://github.com/schemathesis/schemathesis/issues/742
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
.. _#695: https://github.com/schemathesis/schemathesis/issues/695
.. _#692: https://github.com/schemathesis/schemathesis/issues/692
.. _#689: https://github.com/schemathesis/schemathesis/issues/689
.. _#686: https://github.com/schemathesis/schemathesis/issues/686
.. _#684: https://github.com/schemathesis/schemathesis/issues/684
.. _#675: https://github.com/schemathesis/schemathesis/issues/675
.. _#673: https://github.com/schemathesis/schemathesis/issues/673
.. _#672: https://github.com/schemathesis/schemathesis/issues/672
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
