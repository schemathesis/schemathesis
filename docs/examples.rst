Examples in API schemas
=======================

If the schema contains parameter examples, then Schemathesis will include them in the generated cases.

Schemathesis supports the use of both OpenAPI ``example`` and ``examples`` keywords (more information available in the `OpenAPI documentation <https://swagger.io/docs/specification/adding-examples/>`_).
Note that the ``examples`` keyword was added in OpenAPI 3, but Schemathesis supports this feature for OpenAPI 2 via the ``x-examples`` extension.

.. code:: yaml

    paths:
      get:
        parameters:
        - in: body
          name: body
          required: true
          schema: '#/definitions/Pet'

    definitions:
      Pet:
        additionalProperties: false
        example:
          name: Doggo
        properties:
          name:
            type: string
        required:
        - name
        type: object


With this Swagger schema example, there will be a case with body ``{"name": "Doggo"}``. Schemathesis handle explicit examples
with the ``hypothesis.example`` decorator. You can look up more info in the `Hypothesis documentation <https://hypothesis.readthedocs.io/en/latest/reproducing.html#providing-explicit-examples>`_.

Schemathesis also supports examples in individual properties.

.. code:: yaml

    ...
    paths:
      /users:
        parameters:
          - in: query
            name: foo
            schema:
              type: object
              properties:
                prop1:
                  type: string
                  example: prop1 example
        post:
          requestBody:
            content:
              application/json:
                schema:
                  type: object
                  properties:
                    foo:
                      type: string
                      example: bar

Don't worry if you don't have examples for all properties - Schemathesis will generate them for you.

External examples
~~~~~~~~~~~~~~~~~

Examples specified via the ``externalValue`` keyword are also supported:

.. code:: yaml

    content:
      application/json:
        schema:
          $ref: '#/components/schemas/MyObject'
        examples:
          jsonObject:
            summary: A sample object
            externalValue: 'http://example.com/examples/object-example.json'

Schemathesis will load external examples and cache them to avoid hitting the same URL multiple times during the same test run.
Note that such examples for ``multipart/form-data`` are not supported because they require the ``boundary`` directive to be present.
Schemathesis can't infer it solely from the response.
