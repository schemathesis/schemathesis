Extending Schemathesis
======================

Schemathesis provides various extension mechanisms to adapt its default behavior to your specific testing needs. 
This might involve customizing data generation, modifying requests, or introducing new validation checks. 
In this section, we explore different ways to leverage these mechanisms to adjust Schemathesis to your API testing requirements.

Hooks
-----

Need to customize the data used in your tests?

Hooks in Schemathesis allow you to influence the generation of test data, enabling you to create more relevant and targeted tests. 
They can be used to limit the test input to certain criteria, modify the generated values, or establish relationships between different pieces of generated data.

.. code:: python

    import schemathesis


    @schemathesis.hook
    def filter_query(context, query):
        # Simple filtering to avoid a specific query parameter value
        return query["key"] != "42"

Hooks are identified and applied based on their function name, utilized through a decorator, like ``@schemathesis.hook``. 
The function name, such as ``filter_query``, indicates it's a hook to filter query parameters.

.. note::

    The second argument could be ``None`` if there are no parameters of such a kind or if it is optional.
    Keep it in mind when acessing the keys of the dictionary.

When dealing with multiple hooks that serve similar purposes, especially across different schemas within the same file, custom names can be assigned as the first argument in the decorator to avoid conflicts and maintain clarity.

.. code:: python

    import schemathesis


    @schemathesis.hook
    def filter_query(context, query):
        return query["key"] != "41"


    @schemathesis.hook("filter_query")
    def avoid_42(context, query):
        return query["key"] != "42"


    @schemathesis.hook("filter_query")
    def avoid_43(context, query):
        return query["key"] != "43"


In the code snippet above, the function names ``avoid_42`` and ``avoid_43`` don't directly indicate their role as hooks. 
However, by providing "filter_query" as an argument in the ``@schemathesis.hook`` decorator, both functions will serve as ``filter_query`` hooks, ensuring the right application while maintaining unique function names.

Many Schemathesis hooks accept a ``context`` argument, an instance of the ``HookContext`` class.
This context provides optional information about the API operation currently being tested, accessible via ``context.operation``.
This can be useful for conditional logic within your hooks.

Hooks are applied at different scopes: global, schema-specific, and test-specific. 
They execute in the order they are defined, with globally defined hooks executing first, followed by schema-specific hooks, and finally test-specific hooks.
 
**Note**: hooks in different scopes do not override each other but are applied sequentially.

.. code:: python

    import schemathesis


    @schemathesis.hook("filter_query")
    def global_hook(context, query):
        return query["key"] != "42"


    schema = schemathesis.from_uri("http://0.0.0.0:8080/swagger.json")


    @schema.hook("filter_query")
    def schema_hook(context, query):
        return query["key"] != "43"


    def function_hook(context, query):
        return query["key"] != "44"


    @schema.hooks.apply(function_hook)
    @schema.parametrize()
    def test_api(case):
        ...

.. tip::

    Be mindful of the sequence in which hooks are applied. The order can significantly impact the generated test data and subsequent API calls during testing. 
    Always validate the test data and requests to ensure that hooks are applied in the intended order and manner.

.. _enabling-extensions:

Enabling Extensions
~~~~~~~~~~~~~~~~~~~

For Schemathesis to utilize your custom hooks or other extensions, they need to be properly enabled.

For **CLI** usage, extensions should be placed in a separate Python module. 
Then, Schemathesis should be informed about this module via the ``SCHEMATHESIS_HOOKS`` environment variable:

.. code:: bash

    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run http://127.0.0.1/openapi.yaml

Also, depending on your setup, you might need to run this command with a custom ``PYTHONPATH`` environment variable like this:

.. code:: bash

    export PYTHONPATH=$(pwd)
    export SCHEMATHESIS_HOOKS=myproject.tests.hooks
    st run https://example.com/api/swagger.json

In the example above, the module is located at ``myproject/tests/hooks.py`` and the environment variable contains 
a path that could be used as an import in Python.

If you run Schemathesis in Docker, make sure you mount a volume to the ``/app`` directory, so Schemathesis can find it within the container.

.. code:: bash

    docker run -v $(pwd):/app -e SCHEMATHESIS_HOOKS=hooks --network=host 
        schemathesis/schemathesis:stable run http://127.0.0.1:8081/schema.yaml

In this example, the hooks file is called ``hooks.py`` and is located in the current directory. 
The current directory is mounted to the ``/app`` directory in the container.

.. note::

    The name of the module is arbitrary but make sure it is a valid Python module name. Usually it is sufficient to put all your extensions in the ``hooks.py`` file in the current directory.

If you're using Schemathesis in Python tests, ensure to define your hooks in the test setup code.

Filtering Data
~~~~~~~~~~~~~~

Use ``filter`` hooks to exclude certain data values, creating tests that focus on more interesting or relevant inputs. 
For instance, to avoid testing with data that is known to be invalid or uninteresting:

.. code:: python

    @schemathesis.hook
    def filter_query(context, query):
        # Excluding a known test user ID from tests
        return query["user_id"] != 1

Modifying Data
~~~~~~~~~~~~~~

``map`` hooks alter generated data, useful for ensuring that tests include specific, predefined values. Note that you need to explicitly return the modified data.

.. code:: python

    @schemathesis.hook
    def map_query(context, query):
        # Always test with known test user ID
        query["user_id"] = 101
        return query

Generating Dependent Data
~~~~~~~~~~~~~~~~~~~~~~~~~

``flatmap`` hooks generate data with dependencies between different pieces, which can help produce more realistic data and enable deeper testing into the application logic:

.. code:: python

    import schemathesis
    from hypothesis import strategies as st


    @schemathesis.hook
    def flatmap_body(context, body):
        # Ensure 'permissions' align with 'role'
        role = body["role"]
        if role == "admin":
            permissions = [
                ["project:admin", "project:read"],
                ["organization:admin", "organization:read"],
            ]
        else:
            permissions = [["project:read"], ["organization:read"]]
        return st.sampled_from(permissions).map(lambda p: {"role": role, "permissions": p})

In this example, if the role is "admin", permissions might be chosen only from a specific set that is valid for admins.

Further customization
~~~~~~~~~~~~~~~~~~~~~

``before_generate`` hooks provide a means to apply intricate logic to data generation, allowing the combination of multiple maps, filters, and more within the same function, which can enhance readability and organization.

.. code:: python

    import schemathesis


    @schemathesis.hook
    def before_generate_query(context, strategy):
        # Only even 'id' values during test generation
        return strategy.filter(lambda x: x["id"] % 2 == 0).map(
            lambda x: {"id": x["id"] ** 2}
        )

Hook locations
~~~~~~~~~~~~~~

Hooks can be applied to various parts of a test case:

- ``query``: Affects the query parameters of a request.
- ``headers``: Affects the headers of a request.
- ``cookies``: Affects the cookies sent with a request.
- ``path_parameters``: Affects the parameters within the URL path.
- ``body``: Affects the body of a request.
- ``case``: Affects the entire test case, combining all the above.

GraphQL hooks
~~~~~~~~~~~~~

Hooks in Schemathesis can be applied to GraphQL schemas for customizing test data.
These hooks allow you to manipulate, filter, or generate dependent data, providing greater flexibility in how your tests interact with the GraphQL API.

In these hooks, the ``body`` parameter refers to a ``graphql.DocumentNode`` object from Python's ``graphql`` library that represents the GraphQL query,
which you can modify as needed. The ``case`` parameter is an instance of Schemathesis' ``Case`` class.

Here's an example using ``map_body`` to modify the GraphQL query:

.. code:: python

    @schema.hook
    def map_body(context, body):
        # Access the first node in the GraphQL query
        node = body.definitions[0].selection_set.selections[0]

        # Change the field name
        node.name.value = "addedViaHook"

        # Return the modified body
        return body

In this example, the ``map_body`` function modifies the GraphQL query by changing one of the field names to "addedViaHook".

For other request parts like ``query``, Schemathesis does not generate anything, but you can use hooks to provide some data yourself:

.. code:: python

    @schema.hook
    def map_query(context, query):
        return {"q": "42"}

The hook above always returns ``{"q": "42"}`` for the query value.
Note that the ``query`` argument to this function will always be ``None`` as Schemathesis does not generate query parameters for GraphQL requests.

You can also filter out certain queries:

.. code:: python

    @schema.hook
    def filter_body(context, body):
        node = body.definitions[0].selection_set.selections[0]
        return node.name.value != "excludeThisField"

For more complex scenarios, you can use ``flatmap_body`` to generate dependent data.

.. code:: python

    from hypothesis import strategies as st


    @schema.hook
    def flatmap_body(context, body):
        node = body.definitions[0].selection_set.selections[0]
        if node.name.value == "someField":
            return st.just(body).map(lambda b: modify_body(b, "someDependentField"))
        return body


    def modify_body(body, new_field_name):
        # Create a new field
        new_field = ...  # Create a new field node
        new_field.name.value = new_field_name

        # Add the new field to the query
        body.definitions[0].selection_set.selections.append(new_field)

        return body

Remember to return the modified ``body`` or ``case`` object from your hook functions for the changes to take effect.

Applying Hooks to Specific API Operations
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis offers a way to apply hooks to only a specific set of API operations during testing.
This is helpful when you need to run different hooks for different API operations.

Multiple filters can be combined and applied to include or exclude API operations based on exact values, regular expressions, or custom functions.
Here is how you can apply a hook to all API operations with the ``/users/`` path, but exclude the ``POST`` method.

.. code:: python

    import schemathesis


    @schemathesis.hook.apply_to(path="/users/").skip_for(method="POST")
    def filter_body(context, body):
        ...

.. note::

    This decorator syntax is supported only on Python 3.9+. For older Python versions you need to bind separate variables for each term.

Basic rules:

- ``apply_to`` applies the given hook to all API operations that match the filter term
- ``skip_for`` skips the given hook for all API operations that match the filter term
- All conditions within a filter term are combined with the ``AND`` logic
- Each ``apply_to`` and ``skip_for`` term is combined with the ``OR`` logic
- Both ``apply_to`` and ``skip_for`` use the same set of conditions as arguments

Conditions:

- ``path``: the path of the API operation without its ``basePath``.
- ``method``: the upper-cased HTTP method of the API operation
- ``name``: the name of the API operation, such as ``GET /users/`` or ``Query.getUsers``
- ``tag``: the tag assigned to the API operation. For Open API it comes from the ``tags`` field.
- ``operation_id``: the ID of an API operation. For Open API it comes from the ``operationId`` field.
- Each condition can take either a single string or a list of options as input
- You can also use a regular expression to match the conditions by adding ``_regex`` to the end of the condition and passing a string or a compiled regex.

.. code:: python

    import schemathesis


    @schemathesis.hook.apply_to(name="PATCH /items/{item_id}/")
    def map_case(context, case):
        case.path_parameters["item_id"] = case.body["data"]["id"]
        return case

In this example, the ``item_id`` path parameter is synchronized with the ``id`` value from the request body, but only for test cases targeting ``PATCH /items/{item_id}/``.

Filtering API Operations
~~~~~~~~~~~~~~~~~~~~~~~~

Schemathesis provides a ``filter_operations`` hook that allows you to selectively test specific API operations based on their attributes.
This hook can help you focus your tests on the most relevant parts of your API.

The hook should return a boolean value:
- Return ``True`` to include the operation in the tests
- Return ``False`` to skip the operation

Here's an Open API example that includes all operations except those using the POST method:

.. code:: python

    @schemathesis.hook
    def filter_operations(context):
        return context.operation.method != "POST"

Here's a GraphQL example that includes all queries:

.. code:: python

    @graphql_schema.hook
    def filter_operations(context):
        return context.operation.definition.is_query

In these examples, the ``filter_operations`` hook skips all ``POST`` methods in Open API and all mutations in GraphQL.
You can implement any custom logic within the ``filter_operations`` function to include or exclude specific API operations.

Extending CLI
~~~~~~~~~~~~~

This example demonstrates how to add a custom CLI option and an event handler that uses it:

.. code:: python

    from schemathesis import cli, runner


    cli.add_option("--custom-counter", type=int)


    @cli.handler()
    class EventCounter(cli.EventHandler):
        def __init__(self, *args, **params):
            self.counter = params["custom_counter"] or 0

        def handle_event(self, context, event) -> None:
            self.counter += 1
            if isinstance(event, runner.events.Finished):
                context.add_summary_line(
                    f"Counter: {self.counter}",
                )

The ``--custom-counter`` CLI option sets the initial value for the ``EventCounter`` handler. 
The handler increments the counter for each event and adds a summary line with the final count when the test run finishes.

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


    @schemathesis.hook
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


    @schemathesis.hook
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


    @schemathesis.hook
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


    @schemathesis.hook
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


    @schemathesis.hook
    def before_add_examples(
        context: schemathesis.hooks.HookContext,
        examples: List[Case],
    ) -> None:
        examples.append(Case(operation=context.operation, query={"foo": "bar"}))

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

    import schemathesis
    from schemathesis import Case, GenericResponse, hooks
    from typing import Optional


    @schemathesis.hook
    def add_case(
        context: hooks.HookContext, case: Case, response: GenericResponse
    ) -> Optional[Case]:
        case.headers["Content-Type"] = "application/json"
        return case

.. important:: The ``add_case`` hook works only in CLI.

If you only want to create another case conditionally, you may return None, and no additional test will be created. For example, you may only want to create
an additional test case if the original case received a successful response from the server.

.. code:: python

    import schemathesis
    from schemathesis import Case, GenericResponse, hooks
    from typing import Optional


    @schemathesis.hook
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

If you want to modify what keyword arguments will be given to ``case.call`` in CLI, then you can use this hook:

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

Checks in Schemathesis allow you to validate responses from your API, ensuring they adhere to both general and application-specific expectations. 
They can be particularly useful for checking behaviors that are specific to your application and go beyond the built-in checks provided by Schemathesis.

Define a check as a function taking two parameters: ``response`` and ``case``, and register it using the ``@schemathesis.check`` decorator.

.. code-block:: python

    import schemathesis


    @schemathesis.check
    def my_check(response, case) -> None:
        ...

- ``response`` is the API response, an instance of ``requests.Response`` or ``schemathesis.utils.WSGIResponse``, based on your integration method.
- ``case`` is the ``schemathesis.Case`` instance used to send data to the application.

Here’s an example of a check that ensures that when an ``item_id`` of 42 is used, the response contains the text "Answer to the Ultimate Question":

.. code-block:: python

    import schemathesis

    ANSWER = "Answer to the Ultimate Question"


    @schemathesis.check
    def my_check(response, case) -> None:
        if case.path_parameters.get("item_id") == 42 and ANSWER not in response.text:
            raise AssertionError("The ultimate answer not found!")

To signify a check failure, raise an ``AssertionError``. If the assertion fails, Schemathesis will report the assertion message in the output.

.. note::

    Explicitly raising ``AssertionError`` prevents ``pytest`` from altering assertion messages through its rewriting mechanism which is relevant in Python tests.

Generating strings for custom Open API formats
----------------------------------------------

In Open API, you may define custom string formats using the ``format`` keyword, specifying the expected format of a string property value. 
Schemathesis allows you to manage the generation of values for these custom formats by registering Hypothesis strategies.

While Schemathesis supports all built-in Open API formats out of the box, creating strategies for custom string formats enhances the precision of your generated test data.
When Schemathesis encounters a known custom format in the API schema, it utilizes the registered strategy to generate test data.
If a format is unrecognized, regular strings will be generated.

- **Create a Hypothesis Strategy**: Create a strategy that generates strings compliant with your custom format.
- **Register the Strategy**: Make it known to Schemathesis using ``schemathesis.openapi.format``.

.. code-block:: python

    from hypothesis import strategies as st
    import schemathesis


    # Example Luhn algorithm validator
    def luhn_validator(card_number: str) -> bool:
        # Actual validation logic is omitted for brevity
        return True


    # Strategy generating a 16-digit number, starting with "4"
    strategy = st.from_regex(r"\A4[0-9]{15}\Z").filter(luhn_validator)

    # Registering the strategy for "card_number" format
    schemathesis.openapi.format("card_number", strategy)

In the example above, when Schemathesis detects a string with the "card_number" format in the API schema, it uses the registered strategy to generate appropriate test data.

For more details about creating strategies, refer to the `Hypothesis documentation <https://hypothesis.readthedocs.io/en/latest/data.html>`_.

Adjusting header generation
---------------------------

By default, Schemathesis generates headers based on the schema definition falling back to the set of characters defined in `RFC 7230 <https://datatracker.ietf.org/doc/html/rfc7230#section-3.2>`_, but you can adjust this behavior to suit your needs.

To customize header generation, you can extend the ``GenerationConfig`` class and modify its ``headers`` attribute. 
The ``headers`` attribute accepts a ``HeaderConfig`` object, which allows you to specify a custom Hypothesis strategy for generating header values.

Here's an example of how you can adjust header generation:

.. code-block:: python

    import schemathesis
    from schemathesis import GenerationConfig, HeaderConfig
    from hypothesis import strategies as st

    schema = schemathesis.from_uri(
        "https://example.schemathesis.io/openapi.json",
        generation_config=GenerationConfig(
            headers=HeaderConfig(
                strategy=st.text(
                    alphabet=st.characters(
                        min_codepoint=1, 
                        max_codepoint=127,
                    ).map(str.strip)
                )
            )
        ),
    )

In the code above, we use the ``st.text`` strategy from Hypothesis to restrict the characters used in the generated headers to codepoints 1 to 127.

Alternatively, you can customize header generation using the ``after_load_schema`` hook:

.. code-block:: python

    import schemathesis
    from hypothesis import strategies as st

    @schemathesis.hook
    def after_load_schema(context, schema) -> None:
        schema.generation_config.headers.strategy = st.text(
            alphabet=st.characters(min_codepoint=1, max_codepoint=127)
        )

Generating payloads for unknown media types
-------------------------------------------

Each request payload in Open API is associated with a media type, which defines the format of the payload content.
Schemathesis allows you to manage the generation of payloads by registering Hypothesis strategies for specific media types.

Schemathesis generates request payload, it first checks whether there is a custom generation strategy registered for the media type.
If a strategy is registered, it will be used to generate the payload content; otherwise, it will generate payloads based on the schema.

- **Create a Hypothesis Strategy**: Create a strategy that generates binary payloads compliant with the media type.
- **Register the Strategy**: Make it known to Schemathesis using ``schemathesis.openapi.media_type``.

.. code-block:: python

    from hypothesis import strategies as st
    import schemathesis

    # Define your own strategy for generating PDFs
    # NOTE: This is a simplified example, actual PDF generation is much more complex
    pdfs = st.sampled_from([b"%PDF-1.5...", b"%PDF-1.6..."])

    # Register the strategy for "application/pdf" media type
    schemathesis.openapi.media_type("application/pdf", pdfs)
    # You can also specify one or more additional aliases for the media type
    schemathesis.openapi.media_type("application/pdf", pdfs, aliases=["application/x-pdf"])

In this example, ``pdfs`` would be a Hypothesis strategy that generates binary data compliant with the PDF format.
When Schemathesis encounters a request payload with the "application/pdf" media type, it uses the registered strategy to generate the payload content.

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
