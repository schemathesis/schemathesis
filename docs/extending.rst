Extending Schemathesis
======================

Often you need to modify certain aspects of Schemathesis behavior, adjust data generation, modify requests before
sending, and so on. Schemathesis offers multiple extension mechanisms.

Hooks
-----

The hook mechanism is similar to pytest's. Depending on the scope of the changes, there are three scopes of hooks:

- Global. These hooks applied to all schemas in the test run;
- Schema. Used only for specific schema instance;
- Test. Used only for a particular test function;

To register a new hook function, you need to use special decorators - ``hook`` for global and schema-local hooks and ``hooks.apply`` for test-specific ones:

.. code:: python

    import schemathesis


    @schemathesis.hook
    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())


    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")


    @schema.hook("before_generate_query")
    def schema_hook(context, strategy):
        return strategy.filter(lambda x: int(x["id"]) % 2 == 0)


    def before_generate_headers(context, strategy):
        return strategy.filter(lambda x: len(x["id"]) > 5)


    @schema.hooks.apply(before_generate_headers)
    @schema.parametrize()
    def test_api(case):
        ...

By default, ``register`` functions will check the registered hook name to determine when to run it
(see all hook specifications in the section below). Still, to avoid name collisions, you can provide a hook name as an argument to ``register``.

Also, these decorators will check the signature of your hook function to match the specification.
Each hook should accept ``context`` as the first argument, that provides additional context for hook execution.

.. important::

    Do not mutate ``context.operation`` in hook functions as Schemathesis relies on its immutability for caching purposes.
    Mutating it may lead to unpredictable problems.

Hooks registered on the same scope will be applied in the order of registration. When there are multiple hooks in the same hook location, then the global ones will be applied first.

These hooks can be applied both in CLI and in-code use cases.

``before_generate_*``
~~~~~~~~~~~~~~~~~~~~~

This group of six hooks shares the same purpose - adjust data generation for specific request's part or the whole request.

- ``before_generate_path_parameters``
- ``before_generate_headers``
- ``before_generate_cookies``
- ``before_generate_query``
- ``before_generate_body``
- ``before_generate_case``

They have the same signature that looks like this:

.. code:: python

    import hypothesis
    import schemathesis


    def before_generate_query(
        context: schemathesis.hooks.HookContext,
        strategy: hypothesis.strategies.SearchStrategy,
    ) -> hypothesis.strategies.SearchStrategy:
        pass

The ``strategy`` argument is a Hypothesis strategy that will generate a certain request part or the whole request (in case of the ``before_generate_case`` hook). For example, your API operation under test
expects ``id`` query parameter that is a number, and you'd like to have only values that have at least three occurrences of "1".
Then your hook might look like this:

.. code:: python

    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: str(x["id"]).count("1") >= 3)

To filter or modify the whole request:

.. code:: python

    def before_generate_case(context, strategy):
        op = context.operation

        def tune_case(case):
            if op.method == "PATCH" and op.path == "/users/{user_id}/":
                case.path_parameters["user_id"] = case.body["data"]["id"]
            return case

        return strategy.map(tune_case)

The example above will modify generated test cases for ``PATCH /users/{user_id}/`` by setting the ``user_id`` path parameter
to the value generated for payload.

``before_process_path``
~~~~~~~~~~~~~~~~~~~~~~~

This hook is called before each API path is processed (if filters select it). You can use it to modify the schema
before processing - set some parameters as constants, update schema syntax, etc.

Let's say you have the following schema:

.. code:: yaml

    /orders/{order_id}:
      get:
        parameters:
          - description: Order ID to retrieve
            in: path
            name: order_id
            required: true
            schema:
              format: int64
              type: integer

Then, with this hook, you can query the database for some existing order and set its ID as a constant in the API operation definition:

.. code:: python

    import schemathesis
    from typing import Any, Dict

    database = ...  # Init the DB


    def before_process_path(
        context: schemathesis.hooks.HookContext, path: str, methods: Dict[str, Any]
    ) -> None:
        if path == "/orders/{order_id}":
            order_id = database.get_orders().first().id
            methods["get"]["parameters"][0]["schema"]["const"] = order_id

``before_load_schema``
~~~~~~~~~~~~~~~~~~~~~~~

Called just before schema instance is created. Takes a raw schema representation as a dictionary:

.. code:: python

    import schemathesis
    from typing import Any, Dict


    def before_load_schema(
        context: schemathesis.hooks.HookContext,
        raw_schema: Dict[str, Any],
    ) -> None:
        ...

This hook allows you to modify schema before loading.

.. _after-load-schema-hook:

``after_load_schema``
~~~~~~~~~~~~~~~~~~~~~

Called just after schema instance is created. Takes a loaded schema:

.. code:: python

    import schemathesis


    def after_load_schema(
        context: schemathesis.hooks.HookContext,
        schema: schemathesis.schemas.BaseSchema,
    ) -> None:
        ...

For example, with this hook you can programmatically add Open API links before tests.

``before_init_operation``
~~~~~~~~~~~~~~~~~~~~~~~~~

Allows you to modify just initialized API operation:

.. code:: python

    import schemathesis
    from schemathesis.models import APIOperation


    def before_init_operation(
        context: schemathesis.hooks.HookContext, operation: APIOperation
    ) -> None:
        # Overrides the existing schema
        operation.query[0].definition["schema"] = {"enum": [42]}

``before_add_examples``
~~~~~~~~~~~~~~~~~~~~~~~

With this hook, you can add additional test cases that will be executed in Hypothesis ``explicit`` phase:

.. code:: python

    import schemathesis
    from schemathesis import Case
    from typing import List


    def before_add_examples(
        context: schemathesis.hooks.HookContext,
        examples: List[Case],
    ) -> None:
        examples.append(Case(operation=context.operation, query={"foo": "bar"}))

To load CLI hooks, you need to put them into a separate module and pass an importable path via the ``SCHEMATHESIS_HOOKS`` environment variable.
For example, you have your hooks definition in ``myproject/hooks.py``, and ``myproject`` is importable:

.. code:: bash

    SCHEMATHESIS_HOOKS=myproject.hooks
    st run http://127.0.0.1/openapi.yaml

``after_init_cli_run_handlers``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This hook allows you to extend or redefine a list of CLI handlers that will be used to process runner events:

.. code:: python

    import click
    import schemathesis
    from schemathesis.cli.handlers import EventHandler
    from schemathesis.cli.context import ExecutionContext
    from schemathesis.runner import events
    from typing import List


    class SimpleHandler(EventHandler):
        def handle_event(self, context, event):
            if isinstance(event, events.Finished):
                click.echo("Done!")


    @schemathesis.hook
    def after_init_cli_run_handlers(
        context: HookContext,
        handlers: List[EventHandler],
        execution_context: ExecutionContext,
    ) -> None:
        handlers[:] = [SimpleHandler()]

With this simple handler, only ``Done!`` will be displayed at the end of the test run. For example, you can use this hook to:

- Send events over the network
- Store logs in a custom format
- Change the output visual style
- Display additional information in the output

``add_case``
~~~~~~~~~~~~

For each ``add_case`` hook and each API operation, we create an additional, duplicate test case. We pass the Case object from the duplicate test to the ``add_case`` hook.
The user may change the Case object (and therefore the request's data) before the request is sent to the server. The ``add_case`` allows the user to target specific
behavior in the API by changing the duplicate request's specific details.

.. code:: python

    from schemathesis import Case, GenericResponse, hooks
    from typing import Optional


    def add_case(
        context: hooks.HookContext, case: Case, response: GenericResponse
    ) -> Optional[Case]:
        case.headers["Content-Type"] = "application/json"
        return case

.. important:: The ``add_case`` hook works only in CLI.

If you only want to create another case conditionally, you may return None, and no additional test will be created. For example, you may only want to create
an additional test case if the original case received a successful response from the server.

.. code:: python

    from schemathesis import Case, GenericResponse, hooks
    from typing import Optional


    def add_case(
        context: hooks.HookContext, case: Case, response: GenericResponse
    ) -> Optional[Case]:
        if 200 <= response.status_code < 300:
            # if the original case was successful, see if an invalid content type header produces a failure
            case.headers["Content-Type"] = "invalid/content/type"
            return case
        else:
            # original case produced non-2xx response, do not create additional test case
            return None

Note: A partial deep copy of the ``Case`` object is passed to each ``add_case`` hook. ``Case.operation.app`` is a reference to the original ``app``,
and ``Case.operation.schema`` is a shallow copy, so changes to these fields will be reflected in other tests.

.. _hooks_before_call:

``before_call``
~~~~~~~~~~~~~~~

Called right before any test request during CLI runs. With this hook, you can modify generated cases in-place:

.. code:: python

    import schemathesis


    @schemathesis.hook
    def before_call(context, case):
        case.query = {"q": "42"}

``after_call``
~~~~~~~~~~~~~~

Called right after any successful test request during CLI runs. With this hook, you can inspect (and modify in-place if you want) the received responses and their source cases:

.. code:: python

    import json
    import schemathesis


    @schemathesis.hook
    def after_call(context, case, response):
        parsed = response.json()
        response._content = json.dumps({"my-wrapper": parsed}).encode()

.. important:: Won't be called if request times-out.

Depending on whether you use your Python app in-process, you might get different types for the ``response`` argument.
For the WSGI case, it will be ``schemathesis.utils.WSGIResponse``.

``process_call_kwargs``
~~~~~~~~~~~~~~~~~~~~~~~

If you want to modify what keyword arguments will be given to ``case.call`` / ``case.call_wsgi`` / ``case.call_asgi`` in CLI, then you can use this hook:

.. code:: python

    import schemathesis


    @schemathesis.hook
    def process_call_kwargs(context, case, kwargs):
        kwargs["allow_redirects"] = False

.. important:: The ``process_call_kwargs`` hook works only in CLI.

If you test your app via the real network, then the hook above will disable resolving redirects during network calls.
For WSGI integration, the keywords are different. See the documentation for ``werkzeug.Client.open``.

.. _writing-custom-checks:

Checks
------

Schemathesis provides a way to check app responses via user-defined functions called "checks".
Each check is a function that accepts two arguments:

.. code-block:: python

    def my_check(response, case):
        ...

The first one is the app response, which is ``requests.Response`` or ``schemathesis.utils.WSGIResponse``, depending on
whether you used the WSGI integration or not. The second one is the :class:`~schemathesis.Case` instance that was used to
send data to the tested application.

To indicate a failure, you need to raise ``AssertionError`` explicitly:

.. code-block:: python

    def my_check(response, case):
        if response.text == "I am a teapot":
            raise AssertionError("It is a teapot!")

If the assertion fails, you'll see the assertion message in Schemathesis output. In the case of missing
assertion message, Schemathesis will report "Check `my_check` failed".

.. note::

    If you use the ``assert`` statement and ``pytest`` as the test runner, then ``pytest`` may rewrite assertions which
    affects error messages.

Custom string strategies
------------------------

Open API allows you to set a custom string format for a property via the ``format`` keyword.
For example, you may use the ``card_number`` format and validate input with the Luhn algorithm.

You can teach Schemathesis to generate values that fit this format by registering a custom Hypothesis strategy:

1. Create a Hypothesis strategy that generates valid string values
2. Register it via ``schemathesis.openapi.format``

.. code-block:: python

    from hypothesis import strategies as st
    import schemathesis

    strategy = st.from_regex(r"\A4[0-9]{15}\Z").filter(luhn_validator)
    schemathesis.openapi.format("visa_cards", strategy)

Schemathesis test runner
------------------------

If you're looking for a way to extend Schemathesis or reuse it in your own application, then the ``runner`` module might help you.
It can run tests against the given schema URI and will do some simple checks for you.

.. code:: python

    import schemathesis

    schema = schemathesis.from_uri("http://127.0.0.1:8080/swagger.json")

    runner = schemathesis.runner.from_schema(schema)
    for event in runner.execute():
        ...  # do something with event

``runner.execute`` creates a generator that yields events of different kinds - ``BeforeExecution``, ``AfterExecution``, etc.
They provide a lot of useful information about what happens during tests, but your responsibility is handling these events.
You can take some inspiration from Schemathesis `CLI implementation <https://github.com/schemathesis/schemathesis/blob/master/src/schemathesis/cli/__init__.py#L230>`_.
See the full description of events in the `source code <https://github.com/schemathesis/schemathesis/blob/master/src/schemathesis/runner/events.py>`_.

You can provide your custom checks to the execute function; the check is a callable that accepts one argument of ``requests.Response`` type.

.. code:: python

    from datetime import timedelta
    from schemathesis import runner, models


    def not_too_long(response, case: models.Case):
        assert response.elapsed < timedelta(milliseconds=300)


    schema = schemathesis.from_uri("http://127.0.0.1:8080/swagger.json")
    runner = schemathesis.runner.from_schema(schema, checks=[not_too_long])
    for event in runner.execute():
        ...  # do something with event
