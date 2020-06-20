.. customization:

Customization
=============

Often you need to modify certain aspects of Schemathesis behavior, adjust data generation, modify requests before
sending, and so on. Schemathesis offers a hook mechanism which is similar to the pytest's one.

Basing on the scope of the changes there are three scopes of hooks:

- Global. These hooks applied to all schemas in the test run;
- Schema. Applied only for specific schema instance;
- Test. Applied only for a specific test function;

To register a new hook function you need to use special decorators - ``register`` for global and schema-local hooks and ``apply`` for test-specific ones:

.. code:: python

    import schemathesis

    @schemathesis.hooks.register
    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: x["id"].isdigit())

    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")

    @schema.hooks.register("before_generate_query")
    def schema_hook(context, strategy):
        return strategy.filter(lambda x: int(x["id"]) % 2 == 0)

    def before_generate_headers(context, strategy):
        return strategy.filter(lambda x: len(x["id"]) > 5)

    @schema.hooks.apply(before_generate_headers)
    @schema.parametrize()
    def test_api(case):
        ...

By default ``register`` functions will check the registered hook name to determine when to run it
(see all hook specifications in the section below), but to avoid name collisions you can provide a hook name as an argument to ``register``.

Also, these decorators will check the signature of your hook function to match the specification.
Each hook should accept ``context`` as the first argument, that provides additional context for hook execution.

Hooks registered on the same scope will be applied in the order of registration. When there are multiple hooks in the same hook location, then the global ones will be applied first.

Common hooks
------------

These hooks can be applied both in CLI and in-code use cases.

``before_generate_*``
~~~~~~~~~~~~~~~~~~~~~

This is a group of six hooks that share the same purpose - adjust data generation for specific request's part.

- ``before_generate_path_parameters``
- ``before_generate_headers``
- ``before_generate_cookies``
- ``before_generate_query``
- ``before_generate_body``
- ``before_generate_form_data``

They have the same signature that looks like this:

.. code:: python

    def before_generate_query(
        context: schemathesis.hooks.HookContext,
        strategy: hypothesis.strategies.SearchStrategy,
    ) -> hypothesis.strategies.SearchStrategy:
        pass

``strategy`` is a Hypothesis strategy that will generate a certain request part. For example, your endpoint under test
expects ``id`` query parameter that is a number and you'd like to have only values that have at least three occurrences of "1".
Then your hook might look like this:

.. code:: python

    def before_generate_query(context, strategy):
        return strategy.filter(lambda x: str(x["id"]).count("1") >= 3)

``before_process_path``
~~~~~~~~~~~~~~~~~~~~~~~

This hook is called before each API path is processed (if it is selected by filters). You can use it to modify the schema
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

Then, with this hook you can query the database for some existing order and set its ID as a constant in the endpoint definition:

.. code:: python

    def before_process_path(
        context: schemathesis.hooks.HookContext,
        path: str,
        methods: Dict[str, Any]
    ) -> None:
        if path == "/orders/{order_id}":
            order_id = database.get_orders().first().id
            methods["get"]["parameters"][0]["schema"]["const"] = order_id

``before_load_schema``
~~~~~~~~~~~~~~~~~~~~~~~

Called just before schema instance is created. Takes a raw schema representation as a dictionary:

.. code:: python

    def before_load_schema(
        context: schemathesis.hooks.HookContext,
        raw_schema: Dict[str, Any],
    ) -> None:
        ...

This hook allows you to modify schema before loading.


``before_add_examples``
~~~~~~~~~~~~~~~~~~~~~~~

With this hook you can add additional test cases that will be executed in Hypothesis ``explicit`` phase:

.. code:: python

    def before_add_examples(
        context: schemathesis.hooks.HookContext,
        examples: List[Case],
    ) -> None:
        examples.append(
            Case(endpoint=context.endpoint, query={"foo": "bar"})
        )

CLI hooks
---------

To load CLI hooks you need to put them into a separate module and pass an importable path to it in ``--pre-run`` CLI option.
For example, you have your hooks definition in ``myproject/hooks.py``, and ``myproject`` is importable:

.. code:: bash

    schemathesis --pre-run myproject.hooks run http://127.0.0.1/openapi.yaml


``after_init_cli_run_handlers``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This hook allows you to extend or redefine a list of CLI handlers that will be used to process runner events:

.. code:: python

    import click
    import schemathesis
    from schemathesis.cli.handlers import EventHandler
    from schemathesis.runner import events

    class SimpleHandler(EventHandler):

        def handle_event(self, context, event):
            if isinstance(event, events.Finished):
                click.echo("Done!")

    @schemathesis.hooks.register
    def after_init_cli_run_handlers(
        context: HookContext,
        handlers: List[EventHandler],
        execution_context: ExecutionContext
    ) -> None:
        handlers[:] = [SimpleHandler()]

With this simple handler only ``Done!`` will be displayed at the end of the test run. For example, you can use this hook to:

- Send events over the network
- Store logs in a custom format
- Change the output visual style
- Display additional information in the output

``add_case``
~~~~~~~~~~~~

For each ``add_case`` hook and for each endpoint, we create an additional, duplicate test case. We pass the Case object from the duplicate test to the ``add_case`` hook.
The user may change the Case object (and therefore the request data) before the request is sent to the server. The ``add_case`` allows the user to target specific
behavior in the API by changing specific details of the duplicate request.

.. code:: python

    def add_case(context: HookContext, case: Case, response: GenericResponse) -> Optional[Case]:
        case.headers["Content-Type"] = "application/json"
        return case

If you only want to create another case conditionally, you may return None, and no additional test will be created. For example, you may only want to create
an additional test case if the original case received a successful response from the server.

.. code:: python

    def add_case(context: HookContext, case: Case, response: GenericResponse) -> Optional[Case]:
        if 200 <= response.status_code < 300:
            # if the original case was successful, see if an invalid content type header produces a failure
            case.headers["Content-Type"] = "invalid/content/type"
            return case
        else:
            # original case produced non-2xx response, do not create additional test case
            return None

Note: A partial deep copy of the ``Case`` object is passed to each ``add_case`` hook. ``Case.endpoint.app`` is a reference to the original ``app``, 
and ``Case.endpoint.schema`` is a shallow copy, so changes to these fields will be reflected in other tests.
