****************
Stateful testing
****************

By default, Schemathesis takes all operations from your API and tests them separately by passing random input data and validating responses.
It works great when you need to quickly verify that your operations properly validate input and respond in conformance with the API schema.

With stateful testing, Schemathesis combines multiple API calls into a single test scenario and tries to find call sequences that fail.

Why is it useful?
-----------------

This approach allows your tests to reach deeper into your application logic and cover scenarios that are impossible to cover with independent tests.
You may compare Schemathesis's stateful and non-stateful testing the same way you would compare integration and unit tests.
Stateful testing checks how multiple API operations work in combination.

It solves the problem when your application produces a high number of "404 Not Found" responses during testing due to randomness in the input data.

**NOTE**. The number of received "404 Not Found" responses depends on the number of connections between different operations defined in the schema.
The more connections you have, the deeper tests can reach.

How to specify connections?
---------------------------

To specify how different operations depend on each other, we use a special syntax from the Open API specification - `Open API links <https://swagger.io/docs/specification/links/>`_.
It describes how the output from one operation can be used as input for other operations.
To define such connections, you need to extend your API schema with the ``links`` keyword:

.. code-block::
   :emphasize-lines: 16-20

    paths:
      /users:
        post:
          summary: Creates a user and returns the user ID
          operationId: createUser
          requestBody:
            required: true
            description: User object
            content:
              application/json:
                schema:
                  $ref: '#/components/schemas/User'
          responses:
            '201':
              ...
              links:
                GetUserByUserId:
                  operationId: getUser  # The target operation
                  parameters:
                    userId: '$response.body#/id'
      /users/{userId}:
        get:
          summary: Gets a user by ID
          operationId: getUser
          parameters:
            - in: path
              name: userId
              required: true
              schema:
                type: integer
                format: int64

In this schema, you define that the ``id`` value returned by the ``POST /users`` call can be used as a path parameter in the ``GET /users/{userId}`` call.

Schemathesis will use this connection during ``GET /users/{userId}`` parameters generation - everything that is not defined by links will be generated randomly.

If you don't want to modify your schema source, :func:`add_link <schemathesis.specs.openapi.schemas.BaseOpenAPISchema.add_link>`
allows you to define links between a pair of operations programmatically.

For CLI, you can use the :ref:`after_load_schema <after-load-schema-hook>` hook to attach links before tests.

.. automethod:: schemathesis.specs.openapi.schemas.BaseOpenAPISchema.add_link(source, target, status_code, parameters=None, request_body=None) -> None

With some `minor limitations <#open-api-links-limitations>`_, Schemathesis fully supports Open API links, including the `runtime expressions <https://swagger.io/docs/specification/links/#runtime-expressions>`_ syntax.

Minimal example
---------------

Stateful tests could be added to your test suite by defining a test class:

.. code-block:: python

    import schemathesis

    schema = schemathesis.from_uri("http://0.0.0.0/schema.yaml")

    APIWorkflow = schema.as_state_machine()
    TestAPI = APIWorkflow.TestCase

Besides loading an API schema, the example above contains two basic components:

- ``APIWorkflow``. A state machine that allows you to `customize behavior <#how-to-customize-tests>`_ on each test scenario.
- ``TestAPI``. A ``unittest``-style test case where you can add your ``pytest`` fixtures that will be applied to the whole set of scenarios.

Stateful tests work seamlessly with WSGI / ASGI applications - the state machine will automatically pick up the right way to make an API call.

The implementation is based on Hypothesis's `Rule-based state machines <https://hypothesis.readthedocs.io/en/latest/stateful.html>`_, and you can apply its features if you want to extend the default behavior.

.. note::

   Schemathesis's stateful testing uses `Swarm testing <https://www.cs.utah.edu/~regehr/papers/swarm12.pdf>`_ (via Hypothesis), which makes defect discovery much more effective.

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
        schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")
        return schema.as_state_machine()


    def test_statefully(state_machine):
        state_machine.run()

How it works behind the scenes?
-------------------------------

The whole concept consists of two important stages.

- State machine creation:
    - Each API operation has a separate bundle where Schemathesis put all responses received from that operation;
    - All links represent transitions of the state machine. Each one has a pre-condition - there should already be a response
      with the proper status code;
    - If an operation has no links, then Schemathesis creates a transition without a pre-condition and generates random
      data as input.
- Running scenarios:
    - Each scenario step accepts a freshly generated random test case and randomly chosen data from the dependent operation.
      This data might be missing if there are no links to the current operation;
    - If there is data, then the generated case is updated according to the defined link rules;
    - The resulting test case is sent to the current operation then its response is validated and stored for future use.

As a result, Schemathesis can run arbitrary API call sequences and combine data generation with reusing responses.

How to customize tests
----------------------

If you want to change a single scenario's behavior, you need to extend the state machine. Each scenario
gets a freshly created state machine instance that runs a sequence of steps.

.. autoclass:: schemathesis.stateful.APIStateMachine

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
        schema = schemathesis.from_uri("https://example.schemathesis.io/openapi.json")

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

Reproducing failures
--------------------

When Schemathesis finds an erroneous API call sequence, it will provide executable Python code that reproduces the error.
It might look like this:

.. code-block:: python

    state = APIWorkflow()
    v1 = state.step(
        case=state.schema["/users/"]["POST"].make_case(body={"username": "000"}),
        previous=None,
    )
    state.step(
        case=state.schema["/users/{user_id}"]["PATCH"].make_case(
            path_parameters={"user_id": 0},
            query={"common": 0},
            body={"username": ""},
        ),
        previous=(
            v1,
            schema["/users/"]["POST"].links["201"]["UpdateUserById"],
        ),
    )
    state.teardown()

The ``APIWorkflow`` class in the example is your state machine class - change it accordingly if your state machine
class has a different name, or change it to ``state = schema.as_state_machine()()``. Besides the class naming, this code
is supposed to run without changes.

Corner cases
------------

Sometimes the API under test may behave in the way, so errors are not easily reproducible. For example, if there is
a mistake with caching that occurs only on the first call, and your test app is not entirely restarted on each run, then
Schemathesis will report that the error is flaky and can't be reliably reproduced.

If your stateful tests report an ``Unsatisfiable`` error, it means that Schemathesis can't do any API calls to satisfy
rules on your state machine. In most cases, it comes from custom pre-conditions and the underlying API schema, but if
you got this error, I suggest `reporting it <https://github.com/schemathesis/schemathesis/issues/new?assignees=Stranger6667&labels=Status%3A+Review+Needed%2C+Type%3A+Bug&template=bug_report.md&title=%5BBUG%5D>`_
so we can confirm the root cause.

Command Line Interface
----------------------

By default, stateful testing is enabled. You can disable it via the ``--stateful=none`` CLI option.
Please, note that we plan to implement more different algorithms for stateful testing in the future.

.. code:: bash

    st run http://0.0.0.0/schema.yaml

    ...

    POST /api/users/ .                                     [ 33%]
        -> GET /api/users/{user_id} .                      [ 50%]
            -> PATCH /api/users/{user_id} .                [ 60%]
        -> PATCH /api/users/{user_id} .                    [ 66%]
    GET /api/users/{user_id} .                             [ 83%]
        -> PATCH /api/users/{user_id} .                    [ 85%]
    PATCH /api/users/{user_id} .                           [100%]

    ...


Each additional test will be indented and prefixed with ``->`` in the CLI output.
You can specify recursive links if you want. The default recursion depth limit is **5** and can be changed with the
``--stateful-recursion-limit=<N>`` CLI option.

Schemathesis's CLI currently uses the old approach to stateful testing, not based on state machines.
We plan to use the new approach in CLI in the future. It may include slight changes to the visual
appearance and the way to configure it. It also means that using stateful testing in CLI is not yet as customizable
as in the in-code approach.

Open API links limitations
--------------------------

Even though this feature appears only in Open API 3.0 specification, under Open API 2.0, you can use it
via the ``x-links`` extension, the syntax is the same, but you need to use the ``x-links`` keyword instead of ``links``.

The `runtime expressions <https://swagger.io/docs/specification/links/#runtime-expressions>`_ are supported with the
following restriction:

- Symbol ``}`` can not be used as a part of a JSON pointer even though it is a valid symbol.
  It is done due to ambiguity in the runtime expressions syntax, where ``}`` cannot be distinguished from an
  embedded runtime expression's closing bracket.

**IMPORTANT**. The Open API standard defines ``requestBody`` keyword value in this way:

    A literal value or {expression} to use as a request body when calling the target operation.

It means you cannot use multiple runtime expressions for different parameters, and you always have to provide either a literal
or an expression.
