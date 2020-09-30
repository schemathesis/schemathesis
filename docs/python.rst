Writing Python tests
====================

Schemathesis is written in Python and provides a Python interface that allows you to integrate it into your existing test suite.

Basic usage
-----------

The following test will load the API schema from ``http://0.0.0.0:8080/swagger.json`` and execute tests for all endpoints:


.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://example.com/swagger.json")

    @schema.parametrize()
    def test_api(case):
        response = case.call()
        case.validate_response(response)

Each test set includes up to 100 test cases by default, depending on the endpoint definition.

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

    test_api.py::test_api[GET:/api/path_variable/{key}] PASSED [ 33%]
    test_api.py::test_api[GET:/api/success] PASSED             [ 66%]
    test_api.py::test_api[POST:/api/users/] PASSED             [100%]

    ======================= 3 passed in 1.55s =======================

Running these tests requires your app running at ``http://0.0.0.0:8080/`` and a valid Open API schema available at ``http://example.com/swagger.json``.

By default, Schemathesis refuses to work with schemas that do not conform to the Open API spec, but you can disable this behavior by passing the ``validate_schema=False`` argument to the ``from_uri`` function.

Testing specific endpoints
--------------------------

By default, Schemathesis runs tests for all endpoints, but you can select specific endpoints by passing the following arguments to the ``parametrize`` function:

- ``endpoint``. Endpoint path;
- ``method``. HTTP method;
- ``tag``. Open API tag;
- ``operation_id``. ``operationId`` field value;

Each argument expects a case-insensitive regex string or a list of such strings.
Each regex will be matched with its corresponding value via Python's ``re.search`` function.

For example, the following test selects all endpoints which paths start with ``/api/users``:

.. code:: python

    @schema.parametrize(endpoint="^/api/users")
    def test_api(case):
        response = case.call()
        case.validate_response(response)

Tests configuration
-------------------

As Schemathesis tests are regular Hypothesis tests, you can use ``hypothesis.settings`` decorator with them.
For example, in the following test, Schemathesis will test each endpoint with up to 1000 test cases:

.. code:: python

    from hypothesis import settings, Phase

    ...
    @schema.parametrize()
    @settings(max_examples=1000)
    def test_api(case):

See the whole list of available options in the `Hypothesis documentation <https://hypothesis.readthedocs.io/en/latest/settings.html#available-settings>`_.

Lazy loading
------------

Suppose you have a schema that is not available when the tests are collected if, for example, it is built with tools like ``apispec``.
This approach requires an initialized application instance to generate the API schema. You can parametrize the tests from a pytest fixture.

.. code:: python

    import schemathesis

    schema = schemathesis.from_pytest_fixture("fixture_name")

    @schema.parametrize()
    def test_api(case):
        ...

In this case, the test body will be used as a sub-test via the ``pytest-subtests`` library.

**NOTE**: the used fixture should return a valid schema that could be created via ``schemathesis.from_dict`` or other
``schemathesis.from_`` variations.

How are responses checked?
--------------------------

When the received response is validated, Schemathesis runs the following checks:

- ``not_a_server_error``. The response has 5xx HTTP status;
- ``status_code_conformance``. The response status is not defined in the API schema;
- ``content_type_conformance``. The response content type is not defined in the API schema;
- ``response_schema_conformance``. The response content does not conform to the schema defined for this specific response.

Validation happens in the ``case.validate_response`` function, but you can add your code to verify the response conformance as you do in regular Python tests.

ASGI/WSGI applications support
------------------------------

Schemathesis supports making calls to ASGI and WSGI-compliant applications instead of real network calls;
in this case, the test execution will go much faster.

.. code:: python

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
            response = case.call()
            case.validate_response(response)

Anatomy of a test
-----------------

Schemathesis tests are very similar to regular tests you might write with ``pytest``. The main feature is that it
seamlessly combines your API schema with ``pytest``-style parametrization and property-based testing provided by `Hypothesis <http://hypothesis.works/>`_.

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://example.com/swagger.json")

    @schema.parametrize()
    def test_api(case):
        response = case.call()
        case.validate_response(response)

Such test consists of four main parts:

1. Schema preparation; In this case, the schema is loaded via the ``from_uri`` function.
2. Test parametrization; ``@schema.parametrize()`` generates separate tests for all endpoint/method combinations available in the schema.
3. A network call to the running application; ``case.call`` does it.
4. Verifying a property you'd like to test; In this example, we run all built-in checks.

Each test function where you use ``schema.parametrize`` should have the ``case`` fixture, representing a single test case.

Important ``Case`` attributes:

- ``method`` - HTTP method
- ``formatted_path`` - full endpoint path
- ``path_parameters`` - parameters that are used in ``formatted_path``
- ``headers`` - HTTP headers
- ``query`` - query parameters
- ``body`` - request body
- ``form_data`` - form payload

For convenience, you can explore the schemas and strategies manually:

.. code:: python

    >>> import schemathesis
    >>> schema = schemathesis.from_uri("http://api.com/schema.json")
    >>> endpoint = schema["/pet"]["POST"]
    >>> strategy = endpoint.as_strategy()
    >>> strategy.example()
    Case(
        path='/pet',
        method='POST',
        path_parameters={},
        headers={},
        cookies={},
        query={},
        body={
            'name': '\x15.\x13\U0008f42a',
            'photoUrls': ['\x08\U0009f29a', '']
        },
        form_data={}
    )

Schema instances implement the ``Mapping`` protocol.

**NOTE**. Paths are relative to the schema's base path (``host`` + ``basePath`` in Open API 2.0 and ``server.url`` in Open API 3.0):

.. code:: python

    # your ``basePath`` is ``/api/v1``
    >>> schema["/pet"]["POST"]  # VALID
    >>> schema["/api/v1/pet"]["POST"]  # INVALID
