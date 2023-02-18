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
    platform Linux -- Python 3.8.5, schemathesis-2.5.0, ...
    rootdir: /
    hypothesis profile 'default' -> ...
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

Testing specific operations
---------------------------

By default, Schemathesis runs tests for all operations, but you can select specific operations with the following CLI options:

- ``--endpoint / -E``. Operation path;
- ``--method / -M``. HTTP method;
- ``--tag / -T``. Open API tag;
- ``--operation-id / -O``. ``operationId`` field value;

Each option accepts a case-insensitive regex string and could be used multiple times in a single command.
For example, the following command will select all operations which paths start with ``/api/users``:

.. code:: text

    $ st run -E ^/api/users https://example.schemathesis.io/openapi.json

.. important::

    As filters are treated as regular expressions, ensure that they contain proper anchors.
    For example, `/users/` will match `/v1/users/orders/`, but `^/users/$` will match only `/users/`.

If your API contains deprecated operations (that have ``deprecated: true`` in their definition),
then you can skip them by passing ``--skip-deprecated-operations``:

.. code:: bash

    $ st run --skip-deprecated-operations ...

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
- ``response_headers_conformance``. The response headers does not contain all defined headers.

To make Schemathesis perform all built-in checks use ``--checks all`` CLI option:

.. code:: text

    $ st run --checks all https://example.schemathesis.io/openapi.json
    ================ Schemathesis test session starts ===============
    platform Linux -- Python 3.8.5, schemathesis-2.5.0, ...
    rootdir: /
    hypothesis profile 'default' -> ...
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

Additionally, you can define the response time limit with ``--max-response-time``.
If any response will take longer than the provided value (in milliseconds) than it will indicate a failure:

.. code:: text

    $ st run --max-response-time=50 ...
    ================ Schemathesis test session starts ===============
    platform Linux -- Python 3.8.5, schemathesis-2.5.0, ...
    rootdir: /
    hypothesis profile 'default' -> ...
    Schema location: https://example.schemathesis.io/openapi.json
    Base URL: https://example.schemathesis.io/api
    Specification version: Swagger 2.0
    Workers: 1
    Collected API operations: 1

    GET /api/slow F                                            [100%]

    ============================ FAILURES ===========================
    __________________________ GET /api/slow ________________________
    1. Response time exceeded the limit of 50 ms

    Run this Python code to reproduce this failure:

        requests.get('http://127.0.0.1:8081/api/slow')

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

To reproduce test failures Schemathesis generates code samples:

.. code:: python

    requests.get("http://127.0.0.1:8081/api/failure")

You can control these samples via the ``--code-sample-style`` CLI option. For example, passing ``curl`` will generate a cURL command like this:

.. code:: bash

    curl -X GET http://127.0.0.1:8081/api/failure

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

This command will create a new YAML file that will network interactions in `VCR format <https://relishapp.com/vcr/vcr/v/5-1-0/docs/cassettes/cassette-format>`_.
It might look like this:

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
    <testsuites disabled="0" errors="1" failures="1" tests="3" time="0.10743043999536894">
        <testsuite disabled="0" errors="1" failures="1" name="schemathesis" skipped="0" tests="3" time="0.10743043999536894" hostname="bespin">
            <testcase name="GET /api/failure" time="0.089057">
                <failure type="failure" message="2. Received a response with 5xx status code: 500"/>
            </testcase>
            <testcase name="GET /api/malformed_json" time="0.011977">
                <error type="error" message="json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
    ">Traceback (most recent call last):
      File &quot;/home/user/work/schemathesis/src/schemathesis/runner/impl/core.py&quot;, line 87, in run_test
        test(checks, targets, result, **kwargs)
      File &quot;/home/user/work/schemathesis/src/schemathesis/runner/impl/core.py&quot;, line 150, in network_test
        case: Case,
      File &quot;/home/user/.pyenv/versions/3.8.0/envs/schemathesis/lib/python3.8/site-packages/hypothesis/core.py&quot;, line 1095, in wrapped_test
        raise the_error_hypothesis_found
      File &quot;/home/user/work/schemathesis/src/schemathesis/runner/impl/core.py&quot;, line 165, in network_test
        run_checks(case, checks, result, response)
      File &quot;/home/user/work/schemathesis/src/schemathesis/runner/impl/core.py&quot;, line 133, in run_checks
        check(response, case)
      File &quot;/home/user/work/schemathesis/src/schemathesis/checks.py&quot;, line 87, in response_schema_conformance
        data = response.json()
      File &quot;/home/user/.pyenv/versions/3.8.0/envs/schemathesis/lib/python3.8/site-packages/requests/models.py&quot;, line 889, in json
        return complexjson.loads(
      File &quot;/home/user/.pyenv/versions/3.8.0/lib/python3.8/json/__init__.py&quot;, line 357, in loads
        return _default_decoder.decode(s)
      File &quot;/home/user/.pyenv/versions/3.8.0/lib/python3.8/json/decoder.py&quot;, line 337, in decode
        obj, end = self.raw_decode(s, idx=_w(s, 0).end())
      File &quot;/home/user/.pyenv/versions/3.8.0/lib/python3.8/json/decoder.py&quot;, line 353, in raw_decode
        obj, end = self.scan_once(s, idx)
    json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
    </error>
            </testcase>
            <testcase name="GET /api/success" time="0.006397"/>
        </testsuite>
    </testsuites>

Base URL configuration
----------------------

If your Open API schema defines ``servers`` (or ``basePath`` in Open API 2.0), these values will be used to
construct a full operation URL during testing. In the case of Open API 3.0, the first value from ``servers`` will be used.

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
Schemathesis can load your Python code via the ``SCHEMATHESIS_HOOKS`` environment variable:

.. code:: bash

    $ SCHEMATHESIS_HOOKS=test.setup
    $ st run https://example.com/api/swagger.json

**NOTE**. This option should be passed before the ``run`` subcommand.

Also, depending on your setup, you might need to run this command with a custom ``PYTHONPATH`` environment variable like this:

.. code:: bash

    $ PYTHONPATH=$(pwd)
    $ SCHEMATHESIS_HOOKS=test.setup
    $ st run https://example.com/api/swagger.json

The passed value will be treated as an importable Python path and imported before the test run.

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

Debugging
---------

If Schemathesis produces an internal error, its traceback is hidden. To show error tracebacks in the CLI output, use
the ``--show-errors-tracebacks`` option.

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

    docker run -v $(pwd):/mnt schemathesis/schemathesis:stable \
        run /mnt/spec.json

In the example above, the ``spec.json`` file from the current working directory is shared with the Schemathesis container.
Note, that ``$(pwd)`` is shell-specific and works in ``sh`` / ``bash`` / ``zsh``, but could be different in e.g. ``PowerShell``.

.. note:: See Docker volumes `documentation <https://docs.docker.com/storage/volumes/>`_ for more information.

Full list of CLI options
------------------------

.. click:: schemathesis.cli:schemathesis
   :prog: schemathesis
   :commands: run
   :nested: full
