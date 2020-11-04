************************
Working with API schemas
************************

To effectively test API schemas, you need to fulfill certain Schemathesis’s expectations about them.
This page describes how Schemathesis works with API schemas and how you can solve common problems and make testing more effective.

Validation
----------

By default, Schemathesis validates Open API schemas according to their
`official meta schemas <https://github.com/OAI/OpenAPI-Specification/tree/master/schemas>`_, defined in the JSON Schema format.

If the input schema contains an error, you'll see an error like this:

.. code-block::

    Error: jsonschema.exceptions.ValidationError: 'query' is not one of ['body']

    Failed validating 'enum' in schema[0]['properties']['in']:
        {'description': 'Determines the location of the parameter.',
         'enum': ['body'],
         'type': 'string'}

    On instance['in']:
        'query'
