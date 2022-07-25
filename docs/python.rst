Writing Python tests
====================

Schemathesis is written in Python and provides a Python interface that allows you to integrate it into your existing test suite.

Basic usage
-----------

The following test will load the API schema from ``http://0.0.0.0:8080/swagger.json`` and execute tests for all operations:


.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

Each test set includes up to 100 test cases by default, depending on the API operation definition.

We recommend running your tests with the latest `pytest <https://docs.pytest.org/en/stable/>`_ version.

.. code:: text

    $ pytest -v test_api.py
    ====================== test session starts ======================
    platform linux -- Python 3.8.5, pytest-5.4.3
    cachedir: .pytest_cache
    hypothesis profile 'default'
    rootdir: /tmp, inifile: pytest.ini
    plugins: hypothesis-5.23.0, schemathesis-2.5.0
    collected 3 items

    test_api.py::test_api[GET /api/path_variable/{key}] PASSED [ 33%]
    test_api.py::test_api[GET /api/success] PASSED             [ 66%]
    test_api.py::test_api[POST /api/users/] PASSED             [100%]

    ======================= 3 passed in 1.55s =======================

Running these tests requires your app running at ``http://0.0.0.0:8080/`` and a valid Open API schema available at ``https://example.schemathesis.io/openapi.json``.

By default, Schemathesis refuses to work with schemas that do not conform to the Open API spec, but you can disable this behavior by passing the ``validate_schema=False`` argument to the ``from_uri`` function.

Testing specific operations
---------------------------

By default, Schemathesis runs tests for all operations, but you can select specific operations by passing the following arguments to the ``parametrize`` function:

- ``endpoint``. Operation path;
- ``method``. HTTP method;
- ``tag``. Open API tag;
- ``operation_id``. ``operationId`` field value;

Each argument expects a case-insensitive regex string or a list of such strings.
Each regex will be matched with its corresponding value via Python's ``re.search`` function.

.. important::

    As filters are treated as regular expressions, ensure that they contain proper anchors.
    For example, `/users/` will match `/v1/users/orders/`, but `^/users/$` will match only `/users/`.

For example, the following test selects all operations which paths start with ``/api/users``:

.. code:: python

    schema = ...  # Load the API schema here


    @schema.parametrize(endpoint="^/api/users")
    def test_api(case):
        case.call_and_validate()

If your API contains deprecated operations (that have ``deprecated: true`` in their definition),
then you can skip them by passing ``skip_deprecated_operations=True`` to loaders or to the `schema.parametrize` call:

.. code:: python

    schema = schemathesis.from_uri(
        "https://example.schemathesis.io/openapi.json", skip_deprecated_operations=True
    )

Tests configuration
-------------------

As Schemathesis tests are regular Hypothesis tests, you can use ``hypothesis.settings`` decorator with them.
For example, in the following test, Schemathesis will test each API operation with up to 1000 test cases:

.. code:: python

    from hypothesis import settings, Phase

    schema = ...  # Load the API schema here


    @schema.parametrize()
    @settings(max_examples=1000)
    def test_api(case):
        ...

See the whole list of available options in the `Hypothesis documentation <https://hypothesis.readthedocs.io/en/latest/settings.html#available-settings>`_.

Loading schemas
---------------

To start testing, you need to load your API schema first.
It could be a file on your local machine or a web resource or a simple Python dictionary - Schemathesis supports loading API schemas from different location types.

Remote URL
~~~~~~~~~~

The most common way to load the API schema is from the running application via a network request.
If your application is running at ``http://app.com`` and the schema is available at the ``/api/openapi.json`` path, then
you can load it by using the ``schemathesis.from_uri`` loader:

.. code:: python

    schema = schemathesis.from_uri("http://app.com/api/openapi.json")

If you want to load the schema from one URL, but run tests against a URL which differs in port value,
then you can use the ``port`` argument:

.. code:: python

    schema = schemathesis.from_uri("http://app.com/api/openapi.json", port=8081)

This code will run tests against ``http://app.com:8081/api/openapi.json``.

Local path
~~~~~~~~~~

Sometimes you store the schema in a separate file, then it might be easier to load it from there, instead of a running application:

.. code:: python

    schema = schemathesis.from_path("/tmp/openapi.json")

Schemathesis will load the API schema from the ``/tmp/openapi.json`` file and will use ``host`` or ``servers`` keyword values to send requests to.
If you don't need this behavior, you can specify the ``base_url`` argument to send testing requests elsewhere.

For example, if you have the following Open API 2 schema:

.. code:: yaml

    swagger: "2.0"
    host: "petstore.swagger.io"
    basePath: "/v2"

But want to send requests to a local test server which is running at ``http://127.0.0.1:8000``, then your schema loading code may look like this:

.. code:: python

    schema = schemathesis.from_path(
        "/tmp/openapi.json", base_url="http://127.0.0.1:8000/v2"
    )

Note that you need to provide the full base URL, which includes the ``basePath`` part.
It works similarly for Open API 3, where the ``servers`` keyword contains a list of URLs:

.. code:: yaml

    openapi: 3.0.0
    servers:
      - url: https://petstore.swagger.io/v2
      - url: http://petstore.swagger.io/v2

With Open API 3, Schemathesis uses the first value from this list to send requests to.
To use another server, you need to provide it explicitly, the same way as in the example above.

Raw string
~~~~~~~~~~

This loader serves as a basic block for the previous two. It loads a schema from a string or generic IO handle (like one returned by the ``open`` call):

.. code:: python

    # The first argument is a valid Open API schema as a JSON string
    schema = schemathesis.from_file('{"paths": {}, ...}')

Python dictionary
~~~~~~~~~~~~~~~~~

If you maintain your API schema in Python code or your web framework (for example, Fast API) generates it this way, then you can load it directly to Schemathesis:

.. code:: python

    raw_schema = {
        "swagger": "2.0",
        "paths": {
            # Open API operations here
        },
    }
    schema = schemathesis.from_dict(raw_schema)

Web applications
~~~~~~~~~~~~~~~~

Schemathesis natively supports testing of ASGI and WSGI compatible apps (e.g., Flask or FastAPI),
which is significantly faster since it doesn't involve the network.

.. code:: python

    from project import app

    # WSGI
    schema = schemathesis.from_wsgi("/api/openapi.json", app)
    # Or ASGI
    schema = schemathesis.from_asgi("/api/openapi.json", app)

Both loaders expect the relative schema path and an application instance.

Also, we support ``aiohttp`` by implicitly starting an application in a separate thread:

.. code:: python

    schema = schemathesis.from_aiohttp("/api/openapi.json", app)

Lazy loading
~~~~~~~~~~~~

Suppose you have a schema that is not available when the tests are collected if, for example, it is built with tools like ``apispec``.
This approach requires an initialized application instance to generate the API schema. You can parametrize the tests from a pytest fixture.

.. code:: python

    from fastapi import FastAPI
    import pytest
    import schemathesis


    @pytest.fixture
    def web_app(db):
        # some dynamically built application
        # that depends on other fixtures
        app = FastAPI()

        @app.on_event("startup")
        async def startup():
            await db.connect()

        @app.on_event("shutdown")
        async def shutdown():
            await db.disconnect()

        return schemathesis.from_dict(app.openapi())


    schema = schemathesis.from_pytest_fixture("web_app")


    @schema.parametrize()
    def test_api(case):
        ...

This approach is useful, when in your tests you need to initialize some pytest fixtures before loading the API schema.

In this case, the test body will be used as a sub-test via the ``pytest-subtests`` library.

**NOTE**: the used fixture should return a valid schema that could be created via ``schemathesis.from_dict`` or other
``schemathesis.from_`` variations.

How are responses checked?
--------------------------

When the received response is validated, Schemathesis runs the following checks:

- ``not_a_server_error``. The response has 5xx HTTP status;
- ``status_code_conformance``. The response status is not defined in the API schema;
- ``content_type_conformance``. The response content type is not defined in the API schema;
- ``response_schema_conformance``. The response content does not conform to the schema defined for this specific response;
- ``response_headers_conformance``. The response headers does not contain all defined headers.

Validation happens in the ``case.validate_response`` function, but you can add your code to verify the response conformance as you do in regular Python tests.
By default, all available checks will be applied, but you can customize it by passing a tuple of checks explicitly:

.. code-block:: python

    from schemathesis.checks import not_a_server_error

    ...


    @schema.parametrize()
    def test_api(case):
        response = case.call()
        case.validate_response(response, checks=(not_a_server_error,))

The code above will run only the ``not_a_server_error`` check. Or a tuple of additional checks will be executed after ones from the ``checks`` argument:

.. code-block:: python

    ...


    def my_check(response, case):
        ...  # some awesome assertions


    @schema.parametrize()
    def test_api(case):
        response = case.call()
        case.validate_response(response, additional_checks=(my_check,))

.. note::

    Learn more about writing custom checks :ref:`here <writing-custom-checks>`.

If you don't use Schemathesis for data generation, you can still utilize response validation:

.. code-block:: python

    import requests

    schema = schemathesis.from_uri("http://0.0.0.0/openapi.json")


    def test_api():
        response = requests.get("http://0.0.0.0/api/users")
        # Raises a validation error
        schema["/users"]["GET"].validate_response(response)
        # Returns a boolean value
        schema["/users"]["GET"].is_response_valid(response)

The response will be validated the same way as it is validated in the ``response_schema_conformance`` check.

Using additional Hypothesis strategies
--------------------------------------

Hypothesis provides `many data generation strategies <https://hypothesis.readthedocs.io/en/latest/data.html>`_ that may be useful in tests for API schemas.
You can use it for:

- Generating auth tokens
- Adding wrong data to test negative scenarios
- Conditional data generation

Schemathesis automatically applies ``hypothesis.given`` to the wrapped test, and you can't use it explicitly in your test, since it will raise an error.
You can provide additional strategies with ``schema.given`` that proxies all arguments to ``hypothesis.given``.

In the following example we test a hypothetical ``/api/auth/password/reset/`` operation that expects some token in the payload body:

.. code-block:: python

    from hypothesis import strategies as st

    schema = ...  # Load the API schema here


    @schema.parametrize(endpoint="/api/auth/password/reset/")
    @schema.given(data=st.data())
    def test_password_reset(data, case, user):
        if data.draw(st.booleans()):
            case.body["token"] = data.draw(
                (st.emails() | st.just(user.email)).map(create_reset_password_token)
            )
        response = case.call_asgi(app=app)
        case.validate_response(response)

Here we use the special `data strategy <https://hypothesis.readthedocs.io/en/latest/data.html#drawing-interactively-in-tests>`_ to change the ``case`` data in ~50% cases.
The additional strategy in the conditional branch creates a valid password reset token from the given email.

This trick allows the test to cover three different situations where the input token is:

- a random string (generated by default)
- valid for a random email
- valid for an existing email

Using custom Hypothesis strategies allows you to expand the testing surface significantly.

ASGI / WSGI support
-------------------

Schemathesis supports making calls to ASGI and WSGI-compliant applications instead of real network calls;
in this case, the test execution will go much faster.

.. code:: python

    from flask import Flask
    import schemathesis

    app = Flask("test_app")


    @app.route("/schema.json")
    def schema():
        return {...}  # Your API schema


    @app.route("/v1/users", methods=["GET"])
    def users():
        return jsonify([{"name": "Robin"}])


    schema = schemathesis.from_wsgi("/schema.json", app)


    @schema.parametrize()
    def test_api(case):
        response = case.call_wsgi()
        case.validate_response(response)

If you don't supply the ``app`` argument to the loader, make sure you pass your test client when running tests:

.. code-block:: python

    @pytest.fixture()
    def app_schema(client):
        openapi = client.app.openapi()
        return schemathesis.from_dict(openapi)


    schema = schemathesis.from_pytest_fixture("app_schema")


    @schema.parametrize()
    def test_api(case, client):
        # The `session` argument must be supplied.
        case.call_and_validate(session=client)

Unittest support
----------------

Schemathesis supports Python's built-in ``unittest`` framework out of the box.
You only need to specify strategies for ``hypothesis.given``:

.. code-block:: python

    from unittest import TestCase
    from hypothesis import given
    import schemathesis

    schema = schemathesis.from_uri("http://0.0.0.0:8080/schema.json")
    new_pet_strategy = schema["/v2/pet"]["POST"].as_strategy()


    class TestAPI(TestCase):
        @given(case=new_pet_strategy)
        def test_pets(self, case):
            case.call_and_validate()

Anatomy of a test
-----------------

Schemathesis tests are very similar to regular tests you might write with ``pytest``. The main feature is that it
seamlessly combines your API schema with ``pytest``-style parametrization and property-based testing provided by `Hypothesis <http://hypothesis.works/>`_.

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

Such test consists of four main parts:

1. Schema preparation; In this case, the schema is loaded via the ``from_uri`` function.
2. Test parametrization; ``@schema.parametrize()`` generates separate tests for all path/method combinations available in the schema.
3. A network call to the running application; ``case.call_and_validate()`` does it.
4. Verifying a property you'd like to test; In this example, we run all built-in checks.

Each test function where you use ``schema.parametrize`` should have the ``case`` fixture, representing a single test case.

.. note::

    Data generation happens outside of the test function body. It means that the ``case`` object is final, and any modifications on it
    won't trigger data-generation. If you want to update it partially (e.g., replacing a single field in the payload), keep in mind that
    it may require some sort of "merging" logic.


Important ``Case`` attributes:

- ``method`` - HTTP method
- ``formatted_path`` - full API operation path
- ``path_parameters`` - parameters that are used in ``formatted_path``
- ``headers`` - HTTP headers
- ``query`` - query parameters
- ``body`` - request body

For convenience, you can explore the schemas and strategies manually:

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://api.com/schema.json")

    operation = schema["/pet"]["POST"]
    strategy = operation.as_strategy()
    print(strategy.example())
    # Case(
    #     path='/pet',
    #     method='POST',
    #     path_parameters={},
    #     headers={},
    #     cookies={},
    #     query={},
    #     body={
    #         'name': '\x15.\x13\U0008f42a',
    #         'photoUrls': ['\x08\U0009f29a', '']
    #     },
    # )

Schema instances implement the ``Mapping`` protocol.

**NOTE**. Paths are relative to the schema's base path (``host`` + ``basePath`` in Open API 2.0 and ``server.url`` in Open API 3.0):

.. code:: python

    # your ``basePath`` is ``/api/v1``
    schema["/pet"]["POST"]  # VALID
    schema["/api/v1/pet"]["POST"]  # INVALID
