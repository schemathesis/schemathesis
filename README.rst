Schemathesis
============

|Build| |Coverage| |Version| |Python versions| |License|

Schemathesis is a tool for testing your web applications built with Open API / Swagger specifications.

It reads the application schema and generates test cases which will ensure that your application is compliant with its schema.

The application under test could be written in any language, the only thing you need is a valid API schema in a supported format.

**Supported specification versions**:

- Swagger 2.0
- Open API 3.0.x

More API specifications will be added in the future.

Built with:

- `hypothesis`_

- `hypothesis_jsonschema`_

- `pytest`_

Inspired by wonderful `swagger-conformance <https://github.com/olipratt/swagger-conformance>`_ project.

Installation
------------

To install Schemathesis via ``pip`` run the following command:

.. code:: bash

    pip install schemathesis

Gitter: https://gitter.im/kiwicom/schemathesis

Usage
-----

There are two basic ways to use Schemathesis:

- `Command Line Interface <https://github.com/kiwicom/schemathesis#command-line-interface>`_
- `Writing tests in Python <https://github.com/kiwicom/schemathesis#in-code>`_

CLI is pretty simple to use and requires no coding, in-code approach gives more flexibility.

Command Line Interface
~~~~~~~~~~~~~~~~~~~~~~

The ``schemathesis`` command can be used to perform Schemathesis test cases:

.. code:: bash

    schemathesis run https://example.com/api/swagger.json

.. image:: https://github.com/kiwicom/schemathesis/blob/master/img/schemathesis.gif

If your application requires authorization then you can use ``--auth`` option for Basic Auth and ``--header`` to specify
custom headers to be sent with each request.

To filter your tests by endpoint name, HTTP method or Open API tags you could use ``-E``, ``-M``, ``-T`` options respectively.

CLI supports passing options to ``hypothesis.settings``. All of them are prefixed with ``--hypothesis-``:

.. code:: bash

    schemathesis run --hypothesis-max-examples=1000 https://example.com/api/swagger.json

To speed up the testing process Schemathesis provides ``-w/--workers`` option for concurrent test execution:

.. code:: bash

    schemathesis run -w 8 https://example.com/api/swagger.json

In the example above all tests will be distributed among 8 worker threads.

If you'd like to test your web app (Flask or AioHTTP for example) then there is ``--app`` option for you:

.. code:: bash

    schemathesis run --app=importable.path:app /swagger.json

You need to specify an importable path to the module where your app instance resides and a variable name after ``:`` that points
to your app. **Note**, app factories are not supported. The schema location could be:

- A full URL;
- An existing filesystem path;
- In-app endpoint with schema.

This method is significantly faster for WSGI apps, since it doesn't involve network.

For the full list of options, run:

.. code:: bash

    schemathesis --help
    # Or
    schemathesis run --help

Docker
~~~~~~

Schemathesis CLI also available as a docker image

.. code:: bash

    docker run kiwicom/schemathesis:stable run http://example.com/schema.json

To run it against localhost server add ``--network=host`` parameter:

.. code:: bash

    docker run --network="host" kiwicom/schemathesis:stable run http://127.0.0.1/schema.json

Pre-run CLI hook
################

Sometimes you need to execute custom code before the CLI run, for example setup an environment,
register custom string format strategies or modify Schemathesis behavior in runtime you can use ``--pre-run`` hook:

.. code:: bash

    schemathesis --pre-run importable.path.to.module run https://example.com/api/swagger.json

**NOTE**. This option should be passed before the ``run`` part.

The passed value will be processed as an importable Python path, where you can execute your code.
An example - https://github.com/kiwicom/schemathesis#custom-string-strategies

Registering custom checks for CLI
#################################

To add a new check for the Schemathesis CLI there is a special function

.. code:: python

    import schemathesis

    @schemathesis.register_check
    def new_check(response, case):
        # some awesome assertions!
        pass

The registered check should accept a ``response`` with ``requests.Response`` / ``schemathesis.utils.WSGIResponse`` type and
``case`` with ``schemathesis.models.Case`` type.

After registration, your checks will be available in Schemathesis CLI and you can use them via ``-c`` command line option.

.. code:: bash

    schemathesis --pre-run module.with.checks run -c new_check https://example.com/api/swagger.json

In-code
~~~~~~~

To examine your application with Schemathesis you need to:

- Setup & run your application, so it is accessible via the network;
- Write a couple of tests in Python;
- Run the tests via ``pytest``.

Suppose you have your application running on ``http://0.0.0.0:8080`` and its
schema is available at ``http://0.0.0.0:8080/swagger.json``.

A basic test, that will verify that any data, that fit into the schema will not cause any internal server error could
look like this:

.. code:: python

    # test_api.py
    import requests
    import schemathesis

    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")

    @schema.parametrize()
    def test_no_server_errors(case):
        # `requests` will make an appropriate call under the hood
        response = case.call()  # use `call_wsgi` if you used `schemathesis.from_wsgi`
        # You could use built-in checks
        case.validate_response(response)
        # Or assert the response manually
        assert response.status_code < 500


It consists of four main parts:

1. Schema preparation; ``schemathesis`` package provides multiple ways to initialize the schema - ``from_path``, ``from_dict``, ``from_uri``, ``from_file`` and ``from_wsgi``.

2. Test parametrization; ``@schema.parametrize()`` generates separate tests for all endpoint/method combination available in the schema.

3. A network call to the running application; ``case.call`` does it.

4. Verifying a property you'd like to test; In the example, we verify that any app response will not indicate a server-side error (HTTP codes 5xx).

**NOTE**. Look for ``from_wsgi`` usage `below <https://github.com/kiwicom/schemathesis#wsgi>`_

Run the tests:

.. code:: bash

    pytest test_api.py

**Other properties that could be tested**:

- Any call will be processed in <50 ms - you can verify the app performance;
- Any unauthorized access will end with 401 HTTP response code;

Each test function should have the ``case`` fixture, that represents a single test case.

Important ``Case`` attributes:

- ``method`` - HTTP method
- ``formatted_path`` - full endpoint path
- ``headers`` - HTTP headers
- ``query`` - query parameters
- ``body`` - request body

You can use them manually in network calls or can convert to a dictionary acceptable by ``requests.request``:

.. code:: python

    import requests

    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")

    @schema.parametrize()
    def test_no_server_errors(case):
        kwargs = case.as_requests_kwargs()
        response = requests.request(**kwargs)


For each test, Schemathesis will generate a bunch of random inputs acceptable by the schema.
This data could be used to verify that your application works in the way as described in the schema or that schema describes expected behavior.

By default, there will be 100 test cases per endpoint/method combination.
To limit the number of examples you could use ``hypothesis.settings`` decorator on your test functions:

.. code:: python

    from hypothesis import settings

    @schema.parametrize()
    @settings(max_examples=5)
    def test_something(client, case):
        ...

To narrow down the scope of the schemathesis tests it is possible to filter by method or endpoint:

.. code:: python

    @schema.parametrize(method="GET", endpoint="/pet")
    def test_no_server_errors(case):
        ...

The acceptable values are regexps or list of regexps (matched with ``re.search``).

WSGI applications support
~~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis supports making calls to WSGI-compliant applications instead of real network calls, in this case
the test execution will go much faster.

.. code:: python

    app = Flask("test_app")

    @app.route("/schema.json")
    def schema():
        return {...}

    @app.route("/v1/users", methods=["GET"])
    def users():
        return jsonify([{"name": "Robin"}])

    schema = schemathesis.from_wsgi("/schema.json", app)

    @schema.parametrize()
    def test_no_server_errors(case):
        response = case.call_wsgi()
        assert response.status_code < 500

Explicit examples
~~~~~~~~~~~~~~~~~

If the schema contains parameters examples, then they will be additionally included in the generated cases.

.. code:: yaml

    paths:
      get:
        parameters:
        - in: body
          name: body
          required: true
          schema: '#/definitions/Pet'

    definitions:
      Pet:
        additionalProperties: false
        example:
          name: Doggo
        properties:
          name:
            type: string
        required:
        - name
        type: object


With this Swagger schema example, there will be a case with body ``{"name": "Doggo"}``.  Examples handled with
``example`` decorator from Hypothesis, more info about its behavior is `here`_.

NOTE. Schemathesis supports only examples in ``parameters`` at the moment, examples of individual properties are not supported.

Direct strategies access
~~~~~~~~~~~~~~~~~~~~~~~~

For convenience you can explore the schemas and strategies manually:

.. code:: python

    >>> import schemathesis
    >>> schema = schemathesis.from_uri("http://0.0.0.0:8080/petstore.json")
    >>> endpoint = schema["/v2/pet"]["POST"]
    >>> strategy = endpoint.as_strategy()
    >>> strategy.example()
    Case(
        path='/v2/pet',
        method='POST',
        path_parameters={},
        headers={},
        cookies={},
        query={},
        body={
            'name': '\x15.\x13\U0008f42a',
            'photoUrls': ['\x08\U0009f29a', '\U000abfd6\U000427c4', '']
        },
        form_data={}
    )

Schema instances implement `Mapping` protocol.

If you want to customize how data is generated, then you can use hooks of two types:

- Global, which are applied to all schemas;
- Schema-local, which are applied only for specific schema instance;

Each hook accepts a Hypothesis strategy and should return a Hypothesis strategy:

.. code:: python

    import schemathesis

    def global_hook(strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    schemathesis.hooks.register("query", hook)

    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")

    def schema_hook(strategy):
        return strategy.filter(lambda x: int(x["id"]) % 2 == 0)

    schema.register_hook("query", schema_hook)

There are 6 places, where hooks can be applied and you need to pass it as the first argument to ``schemathesis.hooks.register`` or ``schema.register_hook``:

- path_parameters
- headers
- cookies
- query
- body
- form_data

It might be useful if you want to exclude certain cases that you don't want to test, or modify the generated data, so it
will be more meaningful for the application - add existing IDs from the database, custom auth header, etc.

**NOTE**. Global hooks are applied first.

Lazy loading
~~~~~~~~~~~~

If you have a schema that is not available when the tests are collected, for example it is build with tools
like ``apispec`` and requires an application instance available, then you can parametrize the tests from a pytest fixture.

.. code:: python

    # test_api.py
    import schemathesis

    schema = schemathesis.from_pytest_fixture("fixture_name")

    @schema.parametrize()
    def test_api(case):
        ...

In this case the test body will be used as a sub-test via ``pytest-subtests`` library.

**NOTE**: the used fixture should return a valid schema that could be created via ``schemathesis.from_dict`` or other
``schemathesis.from_`` variations.

Extending schemathesis
~~~~~~~~~~~~~~~~~~~~~~

If you're looking for a way to extend ``schemathesis`` or reuse it in your own application, then ``runner`` module might be helpful for you.
It can run tests against the given schema URI and will do some simple checks for you.

.. code:: python

    from schemathesis import runner

    runner.execute("http://127.0.0.1:8080/swagger.json")

The built-in checks list includes the following:

- Not a server error. Asserts that response's status code is less than 500;
- Status code conformance. Asserts that response's status code is listed in the schema;
- Content type conformance. Asserts that response's content type is listed in the schema;
- Response schema conformance. Asserts that response's content conforms to the declared schema;

You can provide your custom checks to the execute function, the check is a callable that accepts one argument of ``requests.Response`` type.

.. code:: python

    from datetime import timedelta
    from schemathesis import runner, models

    def not_too_long(response, result: models.TestResult):
        assert response.elapsed < timedelta(milliseconds=300)

    runner.execute("http://127.0.0.1:8080/swagger.json", checks=[not_too_long])

Custom string strategies
########################

Some string fields could use custom format and validators,
e.g. ``card_number`` and Luhn algorithm validator.

For such cases it is possible to register custom strategies:

1. Create ``hypothesis.strategies.SearchStrategy`` object
2. Optionally provide predicate function to filter values
3. Register it via ``schemathesis.register_string_format``

.. code-block:: python

    strategy = strategies.from_regex(r"\A4[0-9]{15}\Z").filter(luhn_validator)
    schemathesis.register_string_format("visa_cards", strategy)

Unittest support
################

Schemathesis supports Python's built-in ``unittest`` framework out of the box,
you only need to specify strategies for ``hypothesis.given``:

.. code-block:: python

    from unittest import TestCase
    from hypothesis import given
    import schemathesis

    schema = schemathesis.from_uri("http://0.0.0.0:8080/petstore.json")
    new_pet_strategy = schema["/v2/pet"]["POST"].as_strategy()

    class TestSchema(TestCase):

        @given(case=new_pet_strategy)
        def test_pets(self, case):
            response = case.call()
            assert response.status_code < 500

Schema validation
#################

To avoid obscure and hard to debug errors during test runs Schemathesis validates input schemas for conformance with the relevant spec.
If you'd like to disable this behavior use ``--validate-schema=false`` in CLI and ``validate_schema=False`` argument in loaders.

Documentation
-------------

For the full documentation, please see https://schemathesis.readthedocs.io/en/latest/ (WIP)

Or you can look at the ``docs/`` directory in the repository.

Local development
-----------------

First, you need to prepare a virtual environment with `poetry`_.
Install ``poetry`` (check out the `installation guide`_) and run this command inside the project root:

.. code:: bash

    poetry install

For simpler local development Schemathesis includes a ``aiohttp``-based server with the following endpoints in Swagger 2.0 schema:

- ``/api/success`` - always returns ``{"success": true}``
- ``/api/failure`` - always returns 500
- ``/api/slow`` - always returns ``{"slow": true}`` after 250 ms delay
- ``/api/unsatisfiable`` - parameters for this endpoint are impossible to generate
- ``/api/invalid`` - invalid parameter definition. Uses ``int`` instead of ``integer``
- ``/api/flaky`` - returns 1/1 ratio of 200/500 responses
- ``/api/multipart`` - accepts multipart data
- ``/api/teapot`` - returns 418 status code, that is not listed in the schema
- ``/api/text`` - returns ``plain/text`` responses, which are not declared in the schema
- ``/api/malformed_json`` - returns malformed JSON with ``application/json`` content type header


To start the server:

.. code:: bash

    ./test_server.sh 8081

It is possible to configure available endpoints via ``--endpoints`` option.
The value is expected to be a comma separated string with endpoint names (``success``, ``failure``, ``slow``, etc):

.. code:: bash

    ./test_server.sh 8081 --endpoints=success,slow

Then you could use CLI against this server:

.. code:: bash

    schemathesis run http://127.0.0.1:8081/swagger.yaml
    ================================== Schemathesis test session starts =================================
    platform Linux -- Python 3.7.4, schemathesis-0.12.2, hypothesis-4.39.0, hypothesis_jsonschema-0.9.8
    rootdir: /
    hypothesis profile 'default' -> database=DirectoryBasedExampleDatabase('/.hypothesis/examples')
    Schema location: http://127.0.0.1:8081/swagger.yaml
    Base URL: http://127.0.0.1:8081
    Specification version: Swagger 2.0
    collected endpoints: 2

    GET /api/slow .                                                                               [ 50%]
    GET /api/success .                                                                            [100%]

    ============================================== SUMMARY ==============================================

    not_a_server_error            2 / 2 passed          PASSED

    ========================================= 2 passed in 0.29s =========================================


Running tests
~~~~~~~~~~~~~

You could run tests via ``tox``:

.. code:: bash

    tox -p all -o

or ``pytest`` in your current environment:

.. code:: bash

    pytest test/ -n auto

Contributing
------------

Any contribution in development, testing or any other area is highly appreciated and useful to the project.

Please, see the `CONTRIBUTING.rst`_ file for more details.

Python support
--------------

Schemathesis supports Python 3.6, 3.7 and 3.8.

License
-------

The code in this project is licensed under `MIT license`_.
By contributing to ``schemathesis``, you agree that your contributions
will be licensed under its MIT license.

.. |Build| image:: https://github.com/kiwicom/schemathesis/workflows/build/badge.svg
   :target: https://github.com/kiwicom/schemathesis/actions
.. |Coverage| image:: https://codecov.io/gh/kiwicom/schemathesis/branch/master/graph/badge.svg
   :target: https://codecov.io/gh/kiwicom/schemathesis/branch/master
   :alt: codecov.io status for master branch
.. |Version| image:: https://img.shields.io/pypi/v/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |Python versions| image:: https://img.shields.io/pypi/pyversions/schemathesis.svg
   :target: https://pypi.org/project/schemathesis/
.. |License| image:: https://img.shields.io/pypi/l/schemathesis.svg
   :target: https://opensource.org/licenses/MIT

.. _hypothesis: https://hypothesis.works/
.. _hypothesis_jsonschema: https://github.com/Zac-HD/hypothesis-jsonschema
.. _pytest: http://pytest.org/en/latest/
.. _poetry: https://github.com/sdispater/poetry
.. _installation guide: https://github.com/sdispater/poetry#installation
.. _here: https://hypothesis.readthedocs.io/en/latest/reproducing.html#providing-explicit-examples
.. _CONTRIBUTING.rst: https://github.com/kiwicom/schemathesis/blob/master/CONTRIBUTING.rst
.. _MIT license: https://opensource.org/licenses/MIT
