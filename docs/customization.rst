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

    def function_hook(context, strategy):
        return strategy.filter(lambda x: len(x["id"]) > 5)

    @schema.hooks.apply("before_generate_query", function_hook)
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
