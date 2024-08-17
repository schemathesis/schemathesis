Command Line Interface
======================

Installing Schemathesis installs the ``schemathesis`` script to your virtualenv, which you can use to test your APIs

.. note::

    To see the full list of CLI options & commands use the ``--help`` option or check the `Full list of CLI options`_.

Basic usage
-----------

To execute tests, use the ``st run`` command:

.. code:: text

    $ st run https://example.schemathesis.io/openapi.json

With this command, Schemathesis will load the schema from ``https://example.schemathesis.io/openapi.json`` and generate separate
test sets for each operation in this schema. Each test set includes up to 100 test cases by default, depending on the operation definition.

For example, if your API schema has three operations, then you will see a similar output:

.. code:: text

    ================ Schemathesis test session starts ===============
    Schema location: http://127.0.0.1:8081/schema.yaml
    Base URL: http://127.0.0.1:8081/api
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 3

    GET /api/path_variable/{key} .                             [ 33%]
    GET /api/success .                                         [ 66%]
    POST /api/users/ .                                         [100%]

    ============================ SUMMARY ============================

    Performed checks:
        not_a_server_error              201 / 201 passed       PASSED

    ======================= 3 passed in 1.77s =======================

The output style is inspired by `pytest <https://docs.pytest.org/en/stable/>`_ and provides necessary information about the
loaded API schema, processed operations, found errors, and used checks.

By default, Schemathesis works with schemas that do not conform to the Open API spec, but you can enable schema validation with ``--validate-schema=true``.

.. note:: Schemathesis supports colorless output via the `NO_COLOR <https://no-color.org/>` environment variable or the ``--no-color`` CLI option.

Narrowing the testing scope
---------------------------

By default, Schemathesis tests all operations in your API. However, you can fine-tune your test scope with various CLI options to include or exclude specific operations based on paths, methods, names, tags, and operation IDs.

Include and Exclude Options
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use the following format to include or exclude specific operations in your tests:

- ``--{include,exclude}-{path,method,name,tag,operation-id} TEXT``
- ``--{include,exclude}-{path,method,name,tag,operation-id}-regex TEXT``

The ``-regex`` suffix enables regular expression matching for the specified criteria. 
For example, ``--include-path-regex '^/users'`` matches any path starting with ``/users``. 
Without this suffix (e.g., ``--include-path '/users'``), the option performs an exact match. 
Use regex for flexible pattern matching and the non-regex version for precise, literal matching.

Additionally, you can exclude deprecated operations with:

- ``--exclude-deprecated``

.. note::

   The ``name`` property in Schemathesis refers to the full operation name. 
   For Open API, it is formatted as ``HTTP_METHOD PATH`` (e.g., ``GET /users``). 
   For GraphQL, it follows the pattern ``OperationType.field`` (e.g., ``Query.getBookings`` or ``Mutation.updateOrder``).

.. important::

   For GraphQL schemas, Schemathesis only supports filtration by the ``name`` property.

You also can filter API operations by an expression over operation's definition:

.. code:: text

    st run --include-by="/x-property == 42" https://example.schemathesis.io/openapi.json

The expression above will select only operations with the ``x-property`` field equal to ``42``.
Expressions have the following form:

.. code:: text

    "<pointer> <operator> <value>"

- ``<pointer>`` is a JSON Pointer to the value in the operation definition.
- ``<operator>`` is one of the following: ``==``, ``!=``.
- ``<value>`` is a JSON value to compare with. If it is not a valid JSON value, it is treated as a string.

Examples
~~~~~~~~

Include operations with paths starting with ``/api/users``:

.. code:: text

  $ st run --include-path-regex '^/api/users' https://example.schemathesis.io/openapi.json

Exclude POST method operations:

.. code:: text

  $ st run --exclude-method 'POST' https://example.schemathesis.io/openapi.json

Include operations with the ``admin`` tag:

.. code:: text

  $ st run --include-tag 'admin' https://example.schemathesis.io/openapi.json

Exclude deprecated operations:

.. code:: text

  $ st run --exclude-deprecated https://example.schemathesis.io/openapi.json

Include ``GET /users`` and ``POST /orders``:

.. code:: text

  $ st run \
    --include-name 'GET /users' \
    --include-name 'POST /orders' \
    https://example.schemathesis.io/openapi.json

Include queries for ``getBook`` and ``updateBook`` operations in GraphQL:

.. code:: text

  $ st run \
    --include-name 'Query.getBook' \
    --include-name 'Mutation.updateBook' \
    https://example.schemathesis.io/graphql

Overriding test data
--------------------

You can set specific values for Open API parameters in test cases, such as query parameters, headers and cookies.

This is particularly useful for scenarios where specific parameter values are required for deeper testing.
For instance, when dealing with values that represent data in a database, which Schemathesis might not automatically know or generate.

Each override follows the general form of ``--set-[part] name=value``.
For Open API, the ``[part]`` corresponds to the ``in`` value of a parameter which is ``query``, ``header``, ``cookie``, or ``path``.
You can specify multiple overrides in a single command and each of them will be applied only to API operations that use such a parameter.

For example, to override a query parameter and path:

.. code:: bash

    $ st run --set-query apiKey=secret --set-path user_id=42 ...

This command overrides the ``apiKey`` query parameter and ``user_id`` path parameter, using ``secret`` and ``42`` as their respective values in all applicable test cases.

Tests configuration
-------------------

Schemathesis is built on top of the `Hypothesis <http://hypothesis.works/>`_ library and allows you to configure testing process in the same way.

We support all configuration options accepted by the ``hypothesis.settings`` decorator.
All of them are prefixed with ``--hypothesis-`` and underscores are replaced with dashes, for example:

- ``--hypothesis-max-examples=1000``. Generate up to 1000 test cases per API operation;
- ``--hypothesis-phases=explicit``. Run only examples, specified explicitly in the API schema;
- ``--hypothesis-suppress-health-check=too_slow``. Disables the ``too_slow`` health check and makes Schemathesis continue testing even if it is considered too slow.

See the whole list of available options via the ``st run --help`` command and in the `Hypothesis documentation <https://hypothesis.readthedocs.io/en/latest/settings.html#available-settings>`_.

How are responses checked?
--------------------------

For each API response received during the test, Schemathesis runs several checks to verify response conformance. By default,
it runs only one check that raises an error if the checked response has a 5xx HTTP status code.

There are four built-in checks you can use via the `--checks / -c` CLI option:

- ``not_a_server_error``. The response has 5xx HTTP status;
- ``status_code_conformance``. The response status is not defined in the API schema;
- ``content_type_conformance``. The response content type is not defined in the API schema;
- ``response_schema_conformance``. The response content does not conform to the schema defined for this specific response;
- ``negative_data_rejection``. The API accepts data that is invalid according to the schema;
- ``response_headers_conformance``. The response headers do not contain all defined headers or do not conform to their respective schemas.
- ``use_after_free``. The API returned a non-404 response a successful DELETE operation on a resource. **NOTE**: Only enabled for new-style stateful testing.
- ``ensure_resource_availability``. Freshly created resource is not available in related API operations. **NOTE**: Only enabled for new-style stateful testing.
- ``ignored_auth``. The API operation does not check the specified authentication.

To make Schemathesis perform all built-in checks use ``--checks all`` CLI option:

.. code:: text

    $ st run --checks all https://example.schemathesis.io/openapi.json
    ================ Schemathesis test session starts ===============
    Schema location: https://example.schemathesis.io/openapi.json
    Base URL: http://api.com/
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 3

    GET /api/path_variable/{key} .                             [ 33%]
    GET /api/success .                                         [ 66%]
    POST /api/users/ .                                         [100%]

    ============================ SUMMARY ============================

    Performed checks:
        not_a_server_error              201 / 201 passed       PASSED
        status_code_conformance         201 / 201 passed       PASSED
        content_type_conformance        201 / 201 passed       PASSED
        response_schema_conformance     201 / 201 passed       PASSED

    ======================= 3 passed in 1.69s =======================

You can also define a list of checks to exclude using the ``--exclude-checks`` CLI option:

.. code:: text

    $ st run --checks all --exclude-checks not_a_server_error https://example.schemathesis.io/openapi.json
    ================ Schemathesis test session starts ===============
    Schema location: https://example.schemathesis.io/openapi.json
    Base URL: http://api.com/
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 3

    GET /api/path_variable/{key} .                             [ 33%]
    GET /api/success .                                         [ 66%]
    POST /api/users/ .                                         [100%]

    ============================ SUMMARY ============================

    Performed checks:
        status_code_conformance         201 / 201 passed       PASSED
        content_type_conformance        201 / 201 passed       PASSED
        response_schema_conformance     201 / 201 passed       PASSED

    ======================= 3 passed in 1.69s =======================

Additionally, you can define the response time limit with ``--max-response-time``.
If any response will take longer than the provided value (in milliseconds) than it will indicate a failure:

.. code:: text

    $ st run --max-response-time=50 ...
    ================ Schemathesis test session starts ===============
    Schema location: https://example.schemathesis.io/openapi.json
    Base URL: https://example.schemathesis.io/api
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 1

    GET /api/slow F                                            [100%]

    ============================ FAILURES ===========================
    __________________________ GET /api/slow ________________________
    1. Test Case ID: 9Yjzd8

    - Response time limit exceeded

        Actual: 101.92ms
        Limit: 50.00ms

    [200] OK:

        `{"success": true}`

    Reproduce with:

        curl -X GET http://127.0.0.1:8081/api/slow

    Or add this option to your command line parameters:
        --hypothesis-seed=103697217851787640556597810346466192664
    ============================ SUMMARY ============================

    Performed checks:
        not_a_server_error                  2 / 2 passed       PASSED
        max_response_time                   0 / 2 passed       FAILED

    ======================= 1 failed in 0.29s =======================

Concurrent testing
------------------

In some cases, you can speed up the testing process by distributing all tests among multiple threads via the ``-w / --workers`` option:

.. code:: bash

    st run --workers 8 https://example.com/api/swagger.json

In the example above, all tests will be distributed among eight worker threads.
Note that it is not guaranteed to improve performance because it depends on your application behavior.

Code samples style
------------------

To reproduce test failures Schemathesis generates cURL commands:

.. code:: python

    curl -X GET http://127.0.0.1:8081/api/failure

You can control these samples via the ``--code-sample-style`` CLI option. For example, passing ``python`` will generate a Python snippet like this:

.. code:: bash

    requests.get("http://127.0.0.1:8081/api/failure")

Output verbosity
----------------

Sometimes the output contains parts of your API schema or responses in order to provide more context.
By default, Schemathesis truncates these parts to make the output more readable. However, you can control this behavior with:

- ``--output-truncate=false``. Disables schema and response truncation in error messages.

ASGI / WSGI support
-------------------

Schemathesis natively supports testing of ASGI and WSGI compatible apps (e.g., Flask or FastAPI), which is significantly faster since it doesn't involve the network.

To test your app with this approach, you need to pass a special "path" to your application instance via the ``--app`` CLI option. This path consists of two parts, separated by ``:``.
The first one is an importable path to the module with your app. The second one is the variable name that points to your app. Example: ``--app=project.wsgi:app``.

Then your schema location could be:

- A full URL;
- An existing filesystem path;
- In-app path with the schema.

For example:

.. code:: bash

    st run --app=src.wsgi:app /swagger.json

**NOTE**. Depending on your setup, you might need to run this command with a custom ``PYTHONPATH`` environment variable like this:

.. code:: bash

    $ PYTHONPATH=$(pwd) st run --app=src.wsgi:app /swagger.json

Storing and replaying test cases
--------------------------------

It can be useful for debugging purposes to store all requests generated by Schemathesis and all responses from the app into a separate file.
Schemathesis allows you to do this with the ``--cassette-path`` command-line option:

.. code:: bash

    $ st run --cassette-path cassette.yaml http://127.0.0.1/schema.yaml

Schemathesis supports `VCR <https://relishapp.com/vcr/vcr/v/5-1-0/docs/cassettes/cassette-format>`_ and `HAR <http://www.softwareishard.com/blog/har-12-spec/>`_ formats and stores all network interactions in a YAML file.

HAR format
~~~~~~~~~~

HTTP Archive (HAR) is a JSON-based format used for tracking HTTP requests and responses. Schemathesis uses a simplified version of this format that does not include page-related information:

.. code:: json

    {
        "log": {
            "version": "1.2",
            "creator": {
                "name": "harfile",
                "version": "0.2.0"
            },
            "browser": {
                "name": "",
                "version": ""
            },
            "entries": [
                {
                    "startedDateTime": "2024-06-29T20:10:29.254107+02:00",
                    "time": 0.88,
                    "request": {"method": "GET", "url": "http://127.0.0.1:8081/api/basic", "httpVersion": "HTTP/1.1", "cookies": [], "headers": [{"name": "User-Agent", "value": "schemathesis/3.30.4"}, {"name": "Accept-Encoding", "value": "gzip, deflate"}, {"name": "Accept", "value": "*/*"}, {"name": "Connection", "value": "keep-alive"}, {"name": "Authorization", "value": "[Filtered]"}, {"name": "X-Schemathesis-TestCaseId", "value": "ScU88H"}], "queryString": [], "headersSize": 164, "bodySize": 0},
                    "response": {"status": 401, "statusText": "Unauthorized", "httpVersion": "HTTP/1.1", "cookies": [], "headers": [{"name": "Content-Type", "value": "application/json; charset=utf-8"}, {"name": "Content-Length", "value": "26"}, {"name": "Date", "value": "Sat, 29 Jun 2024 18:10:29 GMT"}, {"name": "Server", "value": "Python/3.11 aiohttp/3.9.3"}], "content": {"size": 26, "mimeType": "application/json; charset=utf-8", "text": "{\"detail\": \"Unauthorized\"}"}, "redirectURL": "", "headersSize": 139, "bodySize": 26},
                    "timings": {"send": 0, "wait": 0, "receive": 0.88, "blocked": 0, "dns": 0, "connect": 0, "ssl": 0},
                    "cache": {}
                },
                {

To view the content of a HAR file, you can use this `HAR viewer <http://www.softwareishard.com/har/viewer/>`_.

VCR format
~~~~~~~~~~

The content of a VCR cassette looks like this:

.. code:: yaml

    command: 'st run --cassette-path=cassette.yaml http://127.0.0.1/schema.yaml'
    recorded_with: 'Schemathesis 1.2.0'
    http_interactions:
    - id: '0'
      status: 'FAILURE'
      seed: '1'
      elapsed: '0.00123'
      recorded_at: '2020-04-22T17:52:51.275318'
      checks:
        - name: 'not_a_server_error'
          status: 'FAILURE'
          message: 'Received a response with 5xx status code: 500'
      request:
        uri: 'http://127.0.0.1/api/failure'
        method: 'GET'
        headers:
          ...
        body:
          encoding: 'utf-8'
          string: ''
      response:
        status:
          code: '500'
          message: 'Internal Server Error'
        headers:
          ...
        body:
          encoding: 'utf-8'
          string: '500: Internal Server Error'
        http_version: '1.1'

Schemathesis provides the following extra fields:

- ``command``. Full CLI command used to run Schemathesis.
- ``http_interactions.id``. A numeric interaction ID within the current cassette.
- ``http_interactions.status``. Type of test outcome is one of ``SUCCESS``, ``FAILURE``. The status value is calculated from individual checks statuses - if any check failed, then the final status is ``FAILURE``.
- ``http_interactions.seed``. The Hypothesis seed used in that particular case could be used as an argument to ``--hypothesis-seed`` CLI option to reproduce this request.
- ``http_interactions.elapsed``. Time in seconds that a request took.
- ``http_interactions.checks``. A list of executed checks and and their status.
- ``http_interactions.data_generation_method``. The way test case was generated - ``positive`` or ``negative``.
- ``http_interactions.thread_id``. Unique integer that identifies the thread where a test case was used.
- ``http_interactions.correlation_id``. A unique ID which connects events that happen during testing of the same API operation

By default, payloads are converted to strings, but similar to the original Ruby's VCR, Schemathesis supports preserving exact body bytes via the ``--cassette-preserve-exact-body-bytes`` option.

To work with the cassette, you could use `yq <https://github.com/mikefarah/yq>`_ or any similar tool.
Show response body content of first failed interaction:

.. code:: bash

    $ yq '.http_interactions.[] | select(.status == "FAILURE") | .response.body.string' foo.yaml | head -n 1
    500: Internal Server Error

Check payload in requests to ``/api/upload_file``:

.. code:: bash

    $ yq '.http_interactions.[] | select(.request.uri == "http://127.0.0.1:8081/api/upload_file").request.body.string'
    --7d4db38ad065994d913cb02b2982e3ba
    Content-Disposition: form-data; name="data"; filename="data"


    --7d4db38ad065994d913cb02b2982e3ba--

If you use ``--cassette-preserve-exact-body-bytes`` then you need to look for the ``base64_string`` field and decode it:

.. code:: bash

    $ yq '.http_interactions.[] | select(.status == "FAILURE") | .response.body.base64_string' foo.yaml | head -n 1 | base64 -d
    500: Internal Server Error

Saved cassettes can be replayed with ``st replay`` command. Additionally, you may filter what interactions to
replay by these parameters:

- ``id``. Specific, unique ID;
- ``status``. Replay only interactions with this status (``SUCCESS`` or ``FAILURE``);
- ``uri``. A regular expression for request URI;
- ``method``. A regular expression for request method;

During replaying, Schemathesis will output interactions being replayed together with the response codes from the initial and
current execution:

.. code:: bash

    $ st replay foo.yaml --status=FAILURE
    Replaying cassette: foo.yaml
    Total interactions: 4005

      ID              : 0
      URI             : http://127.0.0.1:8081/api/failure
      Old status code : 500
      New status code : 500

      ID              : 1
      URI             : http://127.0.0.1:8081/api/failure
      Old status code : 500
      New status code : 500

JUnit support
-------------

It is possible to export test results to format, acceptable by such tools as Jenkins.

.. code:: bash

    $ st run --junit-xml=/path/junit.xml http://127.0.0.1/schema.yaml

This command will create an XML at a given path, as in the example below.

.. code:: xml

    <?xml version="1.0" ?>
    <testsuites disabled="0" errors="0" failures="4" tests="4" time="1.7481054730014876">
            <testsuite disabled="0" errors="0" failures="4" name="schemathesis" skipped="0" tests="4" time="1.7481054730014876" hostname="midgard">
                    <testcase name="GET /response-conformance/missing-field" time="0.859204">
                            <failure type="failure" message="1. Test Case ID: JA63GZ

    - Response violates schema

        'age' is a required property

        Schema:

            {
                &quot;type&quot;: &quot;object&quot;,
                &quot;properties&quot;: {
                    &quot;id&quot;: {
                        &quot;type&quot;: &quot;string&quot;
                    },
                    &quot;name&quot;: {
                        &quot;type&quot;: &quot;string&quot;
                    },
                    &quot;age&quot;: {
                        &quot;type&quot;: &quot;integer&quot;
                    }
                },
                &quot;required&quot;: [
                    &quot;id&quot;,
                    &quot;name&quot;,
                    &quot;age&quot;
                ]
            }

        Value:

            {
                &quot;id&quot;: &quot;123&quot;,
                &quot;name&quot;: &quot;Alice&quot;
            }

    [200] OK:

        `{&quot;id&quot;:&quot;123&quot;,&quot;name&quot;:&quot;Alice&quot;}`

    Reproduce with:

        curl -X GET https://example.schemathesis.io/response-conformance/missing-field"/>
                    </testcase>
                    <testcase name="GET /response-conformance/malformed-json" time="0.068179">
                            <failure type="failure" message="1. Test Case ID: Vn5hfI

    - JSON deserialization error

        Expecting property name enclosed in double quotes: line 1 column 2 (char 1)

    [200] OK:

        `{success: true}`

    Reproduce with:

        curl -X GET https://example.schemathesis.io/response-conformance/malformed-json"/>
                    </testcase>
                    <testcase name="GET /response-conformance/undocumented-status-code" time="0.756355">
                            <failure type="failure" message="1. Test Case ID: jm2nOs

    - Undocumented HTTP status code

        Received: 404
        Documented: 200, 400

    [404] Not Found:

        `{&quot;error&quot;:&quot;Not Found&quot;}`

    Reproduce with:

        curl -X GET 'https://example.schemathesis.io/response-conformance/undocumented-status-code?id=1'"/>
                    </testcase>
                    <testcase name="GET /response-conformance/incorrect-content-type" time="0.064367">
                            <failure type="failure" message="1. Test Case ID: Sveexo

    - Undocumented Content-Type

        Received: text/plain
        Documented: application/json

    [200] OK:

        `Success!`

    Reproduce with:

        curl -X GET https://example.schemathesis.io/response-conformance/incorrect-content-type"/>
                    </testcase>
            </testsuite>
    </testsuites>

Base URL configuration
----------------------

If your Open API schema defines ``servers`` (or ``basePath`` in Open API 2.0), these values will be used to
construct a full operation URL during testing. In the case of Open API 3, the first value from ``servers`` will be used.

However, you may want to run tests against a different base URL. To do this, you need to pass the ``--base-url`` option in CLI
or provide ``base_url`` argument to a loader/runner if you use Schemathesis in your code:

.. code:: bash

    st run --base-url=http://127.0.0.1:8080/api/v2 http://production.com/api/openapi.json

And if your schema defines ``servers`` like this:

.. code:: yaml

    servers:
      - url: https://production.com/api/{basePath}
        variables:
          basePath:
            default: v1

Then the tests will be executed against ``/api/v2`` base path.

The ``--base-url`` argument is also used if you wish to load the OpenAPI specification from a local file.

.. code:: bash

    st run --base-url=http://127.0.0.1:8080/api/v1 path/to/openapi.json

.. _extend-cli:

Extending CLI
-------------

To fit Schemathesis to your workflows, you might want to extend it with your custom checks or setup environment before the test run.

Extensions should be placed in a separate Python module. 
Then, Schemathesis should be informed about this module via the ``SCHEMATHESIS_HOOKS`` environment variable:

.. code:: bash

    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run http://127.0.0.1/openapi.yaml

Also, depending on your setup, you might need to run this command with a custom ``PYTHONPATH`` environment variable like this:

.. code:: bash

    export PYTHONPATH=$(pwd)
    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run https://example.com/api/swagger.json

The passed value will be treated as an importable Python path and imported before the test run.

.. note::

    You can find more details on how to extend Schemathesis in the :ref:`Extending Schemathesis <enabling-extensions>` section.

Registering custom checks
~~~~~~~~~~~~~~~~~~~~~~~~~

To use your custom checks with Schemathesis CLI, you need to register them via the ``schemathesis.check`` decorator:

.. code:: python

    import schemathesis


    @schemathesis.check
    def new_check(response, case):
        # some awesome assertions!
        pass

The registered check should accept a ``response`` with ``requests.Response`` / ``schemathesis.utils.WSGIResponse`` type and
``case`` with ``schemathesis.models.Case`` type. This code should be placed in the module you pass to the ``SCHEMATHESIS_HOOKS`` environment variable.

Then your checks will be available in Schemathesis CLI, and you can use them via the ``-c`` command-line option.

.. code:: bash

    $ SCHEMATHESIS_HOOKS=module.with.checks
    $ st run -c new_check https://example.com/api/swagger.json

Additionally, checks may return ``True`` to skip the check under certain conditions. For example, you may only want to run checks when the
response code is ``200``.

.. code:: python

    import schemathesis


    @schemathesis.check
    def conditional_check(response, case):
        if response.status_code == 200:
            ...  # some awesome assertions!
        else:
            # check not relevant to this response, skip test
            return True

Skipped check calls will not be reported in the run summary.

.. note::

    Learn more about writing custom checks :ref:`here <writing-custom-checks>`.

Rate limiting
-------------

APIs implement rate limiting to prevent misuse of their resources.
Schemathesis CLI's ``--rate-limit`` option can be used to set the maximum number of requests per second, minute, hour, or day during testing to avoid hitting these limits.

.. code:: bash

    # 3 requests per second
    st run --rate-limit=3/s
    # 100 requests per minute
    st run --rate-limit=100/m
    # 1000 requests per hour
    st run --rate-limit=1000/h
    # 10000 requests per day
    st run --rate-limit=10000/d

Debugging
---------

If Schemathesis produces an internal error, its traceback is hidden. To show error tracebacks in the CLI output, use
the ``--show-trace`` option.

Additionally you can dump all internal events to a JSON Lines file with the ``--debug-output-file`` CLI option.

Running CLI via Docker
----------------------

Schemathesis CLI is also available as a Docker image:

.. code-block:: bash

    docker run schemathesis/schemathesis:stable \
        run http://api.com/schema.json

To run it against the localhost server, add ``--network=host`` parameter:

.. code-block:: bash

    docker run --network="host" schemathesis/schemathesis:stable \
        run http://127.0.0.1/schema.json

If your API spec is stored in a file, you could use it too by specifying a Docker volume:

.. code-block:: bash

    docker run -v $(pwd):/app schemathesis/schemathesis:stable \
        run /app/spec.json

In the example above, the ``spec.json`` file from the current working directory is shared with the Schemathesis container.
Note, that ``$(pwd)`` is shell-specific and works in ``sh`` / ``bash`` / ``zsh``, but could be different in e.g. ``PowerShell``.

When running from Docker, by default color output is not present. You can use ``--force-color`` if you know that the host's terminal supports colors. 
Note that ``--force-color`` and ``--no-color`` are not compatible with each other.

.. note:: See Docker volumes `documentation <https://docs.docker.com/storage/volumes/>`_ for more information.

Docker on MacOS
~~~~~~~~~~~~~~~

Due to the networking behavior of Docker on MacOS, the containerized application cannot directly reach ``localhost`` of the host machine.
To address this, MacOS users should use the special DNS name ``host.docker.internal`` when referring to the host within Docker.

.. code-block:: bash

    docker run schemathesis/schemathesis:stable \
        run http://host.docker.internal:8080/swagger.json

.. note:: See `Docker on MacOS documentation <https://docs.docker.com/desktop/networking/#i-want-to-connect-from-a-container-to-a-service-on-the-host>`_ for more details

Full list of CLI options
------------------------

.. click:: schemathesis.cli:schemathesis
   :prog: schemathesis
   :commands: run
   :nested: full
