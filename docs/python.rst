Writing Python tests
====================

Include and Exclude Options
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``include`` and ``exclude`` methods on the schema to select specific operations in your tests:

.. code:: python

    @schema.include(tag="admin").exclude(method="POST").parametrize()
    def test_api(case):
        ...

Each method accepts the following arguments:

- ``path``. Path of the operation.
- ``method``. HTTP method.
- ``name``. Full operation name.
- ``tag``. Open API tag.
- ``operation_id``. ``operationId`` field value.

Each argument is either a single string or a list of strings. Note that all conditions within the same method call are combined with the logical AND operator.
Additionally, if you pass a list of strings, it means that the operation should match at least one of the provided values.

Every argument is accompanied with its version with the ``_regex`` suffix that enables regular expression matching for the specified criteria.
For example, ``include(path_regex="^/users")`` matches any path starting with ``/users``.
Without this suffix (e.g., ``include(path="/users")``), the option performs an exact match.
Use regex for flexible pattern matching and the non-regex version for precise, literal matching.

Additionally, you can exclude deprecated operations with:

- ``exclude(deprecated=True)``

.. note::

   The ``name`` property in Schemathesis refers to the full operation name.
   For Open API, it is formatted as ``HTTP_METHOD PATH`` (e.g., ``GET /users``).
   For GraphQL, it follows the pattern ``OperationType.field`` (e.g., ``Query.getBookings`` or ``Mutation.updateOrder``).

You also can filter API operations by a custom function:

.. code:: python

  def my_custom_filter(ctx):
      return ctx.operation.definition.resolved.get("x-property") == 42

  @schema.include(my_custom_filter).parametrize()
  def test_api(case):
      ...

Examples
~~~~~~~~

Include operations with paths starting with ``/api/users``:

.. code:: python

  @schema.include(path_regex="^/api/users").parametrize()
  def test_api(case):
      ...

Exclude POST method operations:

.. code:: python

  @schema.exclude(method="POST").parametrize()
  def test_api(case):
      ...

Include operations with the ``admin`` tag:

.. code:: python

  @schema.include(tag="admin").parametrize()
  def test_api(case):
      ...

Exclude deprecated operations:

.. code:: python

  @schema.exclude(deprecated=True).parametrize()
  def test_api(case):
      ...

Include ``GET /users`` and ``POST /orders``:

.. code:: python

  @schema.include(name=["GET /users", "POST /orders"]).parametrize()
  def test_api(case):
      ...

Overriding test data
--------------------

You can set specific values for Open API parameters in test cases, such as query parameters, headers and cookies.

This is particularly useful for scenarios where specific parameter values are required for deeper testing.
For instance, when dealing with values that represent data in a database, which Schemathesis might not automatically know or generate.

To override parameters, use the ``schema.override`` decorator that accepts ``query``, ``headers``, ``cookies``, or ``path_parameters`` arguments as dictionaries.
You can specify multiple overrides in a single command and each of them will be applied only to API operations that use such a parameter.

For example, to override a query parameter and path:

.. code:: python

    schema = ...  # Load the API schema here


    @schema.parametrize()
    @schema.override(path_parameters={"user_id": "42"}, query={"apiKey": "secret"})
    def test_api(case):

This decorator overrides the ``apiKey`` query parameter and ``user_id`` path parameter, using ``secret`` and ``42`` as their respective values in all applicable test cases.

.. note::

    Of course, you can override them inside the test function body, but it requires checking whether the ones you want to override valid for the tested endpoint, and it has a performance penalty.

Web applications
~~~~~~~~~~~~~~~~

Schemathesis natively supports testing of ASGI and WSGI compatible apps (e.g., FastAPI or Flask),
which is significantly faster since it doesn't involve the network.

.. code:: python

    from project import app

    # WSGI
    schema = schemathesis.openapi.from_wsgi("/api/openapi.json", app)
    # Or ASGI
    schema = schemathesis.openapi.from_asgi("/api/openapi.json", app)

Both loaders expect the relative schema path and an application instance.

Lazy loading
~~~~~~~~~~~~

Suppose you have a schema that is not available when the tests are collected if, for example, it is built with tools like ``apispec``.
This approach requires an initialized application instance to generate the API schema. You can parametrize the tests from a pytest fixture.

.. code:: python

    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    import pytest
    import schemathesis


    @pytest.fixture
    def web_app(db):
        @asynccontextmanager
        async def lifespan(_: FastAPI):
            await db.connect()
            yield
            await db.disconnect()

        # some dynamically built application
        # that depends on other fixtures
        app = FastAPI(lifespan=lifespan)

        return schemathesis.openapi.from_dict(app.openapi(), app)


    schema = schemathesis.pytest.from_fixture("web_app")


    @schema.parametrize()
    def test_api(case):
        ...

This approach is useful, when in your tests you need to initialize some pytest fixtures before loading the API schema.

In this case, the test body will be used as a sub-test via the ``pytest-subtests`` library.

**NOTE**: the used fixture should return a valid schema that could be created via ``schemathesis.openapi.from_dict`` or other
``schemathesis.openapi.from_`` variations.

How are responses checked?
--------------------------

Validation happens in the ``case.call_and_validate`` function, but you can add your code to verify the response conformance as you do in regular Python tests.
By default, all available checks will be applied, but you can customize it by passing a tuple of checks explicitly:

.. code-block:: python

    from schemathesis.checks import not_a_server_error

    ...


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate(checks=(not_a_server_error,))

The code above will run only the ``not_a_server_error`` check. Or a tuple of additional checks will be executed after ones from the ``checks`` argument:

.. code-block:: python

    ...


    def my_check(response, case):
        ...  # some awesome assertions


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate(additional_checks=(my_check,))

.. note::

    Learn more about writing custom checks :ref:`here <writing-custom-checks>`.

You can also use the ``excluded_checks`` argument to exclude chhecks from running:

.. code-block:: python

    from schemathesis.checks import not_a_server_error

    ...


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate(excluded_checks=(not_a_server_error,))

The code above will run the default checks, and any additional checks, excluding the ``not_a_server_error`` check.

If you don't use Schemathesis for data generation, you can still utilize response validation:

.. code-block:: python

    import requests

    schema = schemathesis.openapi.from_url("http://0.0.0.0/openapi.json")


    def test_api():
        response = requests.get("http://0.0.0.0/api/users")
        # Raises a validation error
        schema["/users"]["GET"].validate_response(response)
        # Returns a boolean value
        schema["/users"]["GET"].is_response_valid(response)

The response will be validated the same way as it is validated in the ``response_schema_conformance`` check.

ASGI & WSGI support
-------------------

Schemathesis supports making calls to `ASGI <https://asgi.readthedocs.io/en/latest/>`_ and `WSGI-compliant <https://docs.python.org/3/library/wsgiref.html>`_ applications instead of through real network calls,
significantly speeding up test execution.

Using Schemathesis with a Flask application (WSGI):

.. code:: python

    from flask import Flask
    import schemathesis

    app = Flask("test_app")


    @app.route("/schema.json")
    def schema():
        return {...}  # Your API schema


    @app.route("/v1/users", methods=["GET"])
    def users():
        return [{"name": "Robin"}]


    # Load the schema from the WSGI app
    schema = schemathesis.openapi.from_wsgi("/schema.json", app)


    @schema.parametrize()
    def test_api(case):
        # The test case will make a call to the application and validate the response
        # against the defined schema automatically.
        case.call_and_validate()

Running the example above with ``pytest`` will execute property-based tests against the Flask application.

Using Schemathesis with a FastAPI application (ASGI):

.. code:: python

    from fastapi import FastAPI
    import schemathesis

    app = FastAPI()


    @app.get("/v1/users")
    async def users():
        return [{"name": "Robin"}]


    # Load the schema from the ASGI app
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)


    @schema.parametrize()
    def test_api(case):
        # The test case will make a call to the application and validate the response
        # against the defined schema automatically.
        case.call_and_validate()

Note that Schemathesis currently tests ASGI applications synchronously.

Async support
-------------

Schemathesis supports asynchronous test functions executed via ``asyncio`` or ``trio``.
They work the same way as regular async tests and don't require any additional configuration beyond
installing ``pytest-asyncio`` or ``pytest-trio`` and following their usage guidelines.

.. code:: python

    import pytest
    import schemathesis

    schema = ...

    @pytest.mark.trio
    @schema.parametrize()
    async def test_api(case):
        ...
