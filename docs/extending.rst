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
