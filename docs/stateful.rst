****************
Stateful testing
****************

Minimal example
---------------

Stateful tests could be added to your test suite by defining a test class:

.. code-block:: python

    import schemathesis

    schema = schemathesis.openapi.from_url("http://0.0.0.0/schema.yaml")

    APIWorkflow = schema.as_state_machine()
    TestAPI = APIWorkflow.TestCase

Besides loading an API schema, the example above contains two basic components:

- ``APIWorkflow``. A state machine that allows you to `customize behavior <#how-to-customize-tests>`_ on each test scenario.
- ``TestAPI``. A ``unittest``-style test case where you can add your ``pytest`` fixtures that will be applied to the whole set of scenarios.

Stateful tests work seamlessly with WSGI / ASGI applications - the state machine will automatically pick up the right way to make an API call.

The implementation is based on Hypothesis's `Rule-based state machines <https://hypothesis.readthedocs.io/en/latest/stateful.html>`_, and you can apply its features if you want to extend the default behavior.

Lazy schema loading
-------------------

It is also possible to use stateful testing without loading the API schema during test collection. For example, if your
application depends on some test fixtures, you might want to avoid loading the schema too early.

To do so, you need to create the state machine inside a ``pytest`` fixture and run it via :func:`run` inside a test function:

.. code-block:: python

    import pytest
    import schemathesis


    @pytest.fixture
    def state_machine():
        # You may use any schema loader here
        # or use any pytest fixtures
        schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")
        return schema.as_state_machine()


    def test_statefully(state_machine):
        state_machine.run()

How to customize tests
----------------------

If you want to change a single scenario's behavior, you need to extend the state machine. Each scenario
gets a freshly created state machine instance that runs a sequence of steps.

.. autoclass:: schemathesis.stateful.state_machine.APIStateMachine

    The following methods are executed only once per test scenario.

    .. automethod:: setup

    .. automethod:: teardown

    These methods might be called multiple times per test scenario.

    .. automethod:: before_call

    .. automethod:: get_call_kwargs

    .. automethod:: call

    .. automethod:: after_call

    .. automethod:: validate_response

If you load your schema lazily, you can extend the state machine inside the ``pytest`` fixture:

.. code-block:: python

    import pytest


    @pytest.fixture
    def state_machine():
        schema = schemathesis.openapi.from_url("https://example.schemathesis.io/openapi.json")

        class APIWorkflow(schema.as_state_machine()):
            def setup(self):
                ...  # your scenario setup

        return APIWorkflow

Using pytest fixtures
---------------------

In case if you need to customize the whole test run, then you can extend the test class:

.. code-block:: python

    schema = ...  # Load the API schema here

    APIWorkflow = schema.as_state_machine()


    class TestAPI(APIWorkflow.TestCase):
        def setUp(self):
            ...  # create a database

        def tearDown(self):
            ...  # drop the database

Or with explicit fixtures:

.. code-block:: python

    import pytest

    APIWorkflow = schema.as_state_machine()


    @pytest.fixture()
    def database():
        # create tables & data
        yield
        # drop tables


    @pytest.mark.usefixtures("database")
    class TestAPI(APIWorkflow.TestCase):
        pass

Note that for ``pytest`` or ``unittest``, there is a single test case, which is parametrized on the Hypothesis side.
Therefore, it will run only once, not for each test scenario.

Hypothesis configuration
------------------------

Hypothesis settings can be changed via the settings object on the ``TestCase`` class:

.. code-block:: python

    from hypothesis import settings

    schema = ...  # Load the API schema here

    TestCase = schema.as_state_machine().TestCase
    TestCase.settings = settings(max_examples=200, stateful_step_count=5)

If you load your schema lazily:

.. code-block:: python

    from hypothesis import settings
    import pytest


    @pytest.fixture
    def state_machine():
        ...


    def test_statefully(state_machine):
        state_machine.run(
            settings=settings(
                max_examples=200,
                stateful_step_count=5,
            )
        )

With this configuration, there will be twice more test cases with a maximum of five steps in each one.

How to provide initial data for test scenarios?
-----------------------------------------------

Often you might want to always make some API calls as a preparation for the test. For example, to create some
test data, like users in the system or items in the e-shop stock. It can provide good starting points for scenarios, which is
especially useful if your API expects specific input, which is hard to generate randomly.

The best way to do so is by using the Hypothesis's ``initialize`` decorator:

.. code-block:: python

    from hypothesis.stateful import initialize

    schema = ...  # Load the API schema here

    BaseAPIWorkflow = schema.as_state_machine()


    class APIWorkflow(BaseAPIWorkflow):
        @initialize(
            target=BaseAPIWorkflow.bundles["/users/"]["POST"],
            case=schema["/users/"]["POST"].as_strategy(),
        )
        def init_user(self, case):
            return self.step(case)

This rule will use the ``POST /users/`` operation strategy and generate random data as input and store the result in
a special bundle, where it will be used for dependent API calls. The state machine will run this rule at the beginning of any test scenario.

.. important::

    If you have multiple rules, they will run in arbitrary order, which may not be desired.
    If you need to run initialization code always at the beginning of each test scenario, use the :meth:`setup` hook instead.

If you need more control and you'd like to provide the whole payload to your API operation, then you can do it either by modifying
the generated case manually or by creating a new one via the :func:`APIOperation.make_case` function:

.. code-block:: python

    from hypothesis.stateful import initialize

    schema = ...  # Load the API schema here

    BaseAPIWorkflow = schema.as_state_machine()


    class APIWorkflow(BaseAPIWorkflow):
        @initialize(
            target=BaseAPIWorkflow.bundles["/users/"]["POST"],
        )
        def init_user(self):
            case = schema["/users/"]["POST"].make_case(body={"username": "Test"})
            return self.step(case)

Loading multiple entries of the same type is more verbose but still possible:

.. code-block:: python

    from hypothesis.stateful import initialize, multiple

    schema = ...  # Load the API schema here

    BaseAPIWorkflow = schema.as_state_machine()
    # These users will be created at the beginning of each scenario
    USERS = [
        {"is_admin": True, "username": "Admin"},
        {"is_admin": False, "username": "Customer"},
    ]


    class APIWorkflow(BaseAPIWorkflow):
        @initialize(
            target=BaseAPIWorkflow.bundles["/users/"]["POST"],
        )
        def init_users(self):
            result = []
            # Create each user via the API
            for user in USERS:
                case = schema["/users/"]["POST"].make_case(body=user)
                result.append(self.step(case))
            # Store them in the `POST /users/` bundle
            return multiple(*result)

Examples
--------

Here are more verbose examples of how you can adapt Schemathesis's stateful testing to some typical workflows.

API authorization
~~~~~~~~~~~~~~~~~

Login to an app and use its API token with each call:

.. code:: python

    import requests


    class APIWorkflow(schema.as_state_machine()):
        headers: dict

        def setup(self):
            # Make a login request
            response = requests.post(
                "http://0.0.0.0/api/login", json={"login": "test", "password": "password"}
            )
            # Parse the response and store the token in headers
            token = response.json()["auth_token"]
            self.headers = {"Authorization": f"Bearer {token}"}

        def get_call_kwargs(self, case):
            # Use stored headers
            return {"headers": self.headers}

Note that this example uses the ``setup`` hook. A similar hook could be implemented with the ``initialize`` decorator, but
there is a caveat with that.

You can have multiple initialization rules by using the ``initialize`` decorator, and they will be called in an arbitrary order.
In this example, such behavior may not be desired since the login request should run first, and then all following requests will use the received token.
If we'd use ``initialize`` to login with some additional ``initialize`` rules that depend on the API token, it won't work because of random execution order.
The ``setup`` method fits better here since it **always** is executed when the state machine starts.

Conditional validation
~~~~~~~~~~~~~~~~~~~~~~

Run different checks, depending on the result of the previous call:

.. code:: python

    def check_condition(response, case):
        if case.source is not None:
            # Run this check only for `GET /items/{id}`
            if case.method == "GET" and case.path == "/items/{id}":
                value = response.json()
                if case.source.response.status_code == 201:
                    assert value in ("IN_PROGRESS", "COMPLETE")
                if case.source.response.status_code == 400:
                    assert value == "REJECTED"


    class APIWorkflow(schema.as_state_machine()):
        def validate_response(self, response, case):
            # Run all default checks together with the new one
            super().validate_response(response, case, additional_checks=(check_condition,))

Extracting data from headers and query parameters
-------------------------------------------------

By default, Schemathesis allows you to extract data from the response body of an API endpoint, based on the provided schema.
However, sometimes you might need to extract data from other parts of the API response, such as headers, path or query parameters.

Schemathesis provides an additional feature that allows you to use regular expressions to extract data from the string values of headers and query parameters.
This can be particularly useful when the API response includes important information in these locations, and you need to use that data for further processing.

Here's an example of how to extract the user ID from the ``Location`` header of a ``201 Created`` response:

.. code-block::
   :emphasize-lines: 12-12

    paths:
      /users:
        post:
          ...
          responses:
            '201':
              ...
              links:
                GetUserByUserId:
                  operationId: getUser
                  parameters:
                    userId: '$response.header.Location#regex:/users/(.+)'

For example, if the ``Location`` header is ``/users/42``, the ``userId`` parameter will be set to ``42``.
The regular expression should be a valid Python regular expression and should contain a single capturing group.

If the regular expression does not match the value, the parameter will be set to empty.

Open API links limitations
--------------------------

Even though this feature appears only in Open API 3.0 specification, under Open API 2.0, you can use it
via the ``x-links`` extension, the syntax is the same, but you need to use the ``x-links`` keyword instead of ``links``.

The `runtime expressions <https://swagger.io/docs/specification/links/#runtime-expressions>`_ are supported with the
following restriction:

- Symbol ``}`` can not be used as a part of a JSON pointer even though it is a valid symbol.
  It is done due to ambiguity in the runtime expressions syntax, where ``}`` cannot be distinguished from an
  embedded runtime expression's closing bracket.

For building ``requestBody``, the Open API standard only allows for literal values or expressions:

    A literal value or {expression} to use as a request body when calling the target operation.

Schemathesis extends the Open API standard by allowing for the evaluation of runtime expressions within the ``requestBody`` object or array.

For example, the following requestBody definition is valid:

.. code-block:: json

  {
      "key": "$response.body#/key",
      "items": ["$response.body#/first", "literal", 42]
  }

In this example, the ``$response.body#/key`` and ``$response.body#/first`` expressions are used to dynamically retrieve values from the response body.

If the response body is ``{"key": "foo", "first": "bar"}``, then the resulting payload will be:

.. code-block:: json

  {
      "key": "foo",
      "items": ["bar", "literal", 42]
  }

This allows for building dynamic payloads where nested items are not hardcoded but instead evaluated at runtime.

**IMPORTANT**: Non-string object keys are converted to stringified JSON values during evaluation.

By default, Schemathesis merges the evaluated structure with a generated value, giving the evaluated value precedence.
