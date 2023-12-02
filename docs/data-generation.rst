Data generation
===============

This section describes how Schemathesis generates test examples and their serialization process.

Schemathesis converts Open API schemas to compatible JSON Schemas and passes them to ``hypothesis-jsonschema``, which generates data for those schemas.

.. important::

    If the API schema is complex or deeply nested, data generation may be slow or produce data without much variance.
    It is a known behavior and caused by the way Hypothesis works internally.
    There are many tradeoffs in this process, and Hypothesis tries to give reasonable defaults for a typical case
    and not be too slow for pathological cases.

Generating strings
------------------

In Schemathesis, you can control how strings are generated:

- ``allow_x00`` (default ``True``): Determines whether to allow the generation of ``\x00`` bytes within strings. It is useful to avoid rejecting tests as invalid by some web servers.
- ``codec`` (default ``utf-8``): Specifies the codec used for generating strings. It helps if you need to restrict the inputs to, for example, the ASCII range.

Global configuration
~~~~~~~~~~~~~~~~~~~~

CLI:

.. code:: text

    $ st run --generation-allow-x00=false ...
    $ st run --generation-codec=ascii ...

Python:

.. code:: python

    import schemathesis
    from schemathesis import GenerationConfig

    schema = schemathesis.from_uri(
        "https://example.schemathesis.io/openapi.json",
        generation_config=GenerationConfig(allow_x00=False, codec='ascii'),
    )

This configuration sets the string generation to disallow ``\x00`` bytes and use the ASCII codec for all strings.

Negative testing
----------------

By default, Schemathesis generates data that matches the input schema. Alternatively it can generate the contrary - examples that do not match the input schema.

CLI:

.. code:: text

    $ st run -D negative https://example.schemathesis.io/openapi.json

Python:

.. code:: python

    import schemathesis
    from schemathesis import DataGenerationMethod

    schema = schemathesis.from_uri(
        "https://example.schemathesis.io/openapi.json",
        data_generation_methods=[DataGenerationMethod.negative],
    )


    @schema.parametrize()
    def test_api(case):
        case.call_and_validate()

.. note:: At this moment, negative testing is significantly slower than positive testing.

Payload serialization
---------------------

When your API accepts a payload, requests should have a media type located in their ``Content-Type`` header.
In Open API 3.0, you may write something like this:

.. code-block::
   :emphasize-lines: 7

    openapi: 3.0.0
    paths:
      /pet:
        post:
          requestBody:
            content:
              application/json:
                schema:
                  type: object
            required: true

In this example, operation ``POST /pet`` expects ``application/json`` payload. For each defined media type Schemathesis
generates data according to the relevant schema (``{"type": "object"}`` in the example).

.. note:: This data is stored in the ``case`` fixture you use in tests when you use our ``pytest`` integration.

Before sending, this data should be serialized to the format expected by the tested operation. Schemathesis supports
most common media types like ``application/json``, ``application/xml`` and ``text/plain`` out of the box and allows you to add support for other
media types via the ``serializers`` mechanism.

Schemathesis uses ``requests`` to send API requests over network and ``werkzeug.Client`` for direct WSGI integration.
Serializers define the process of transforming generated Python objects into structures that can be sent by these tools.

If Schemathesis is unable to serialize data for a media type, the generated samples will be rejected.
If an API operation does not define media types that Schemathesis can serialize, you will see a ``Unsatisfiable`` error.

If the operation under tests considers payload to be optional, these cases are still generated by Schemathesis, but
not passed to serializers.

XML serialization
~~~~~~~~~~~~~~~~~

Schemathesis supports the ``application/xml`` content type, facilitating the testing of APIs that communicate through XML.
This feature leverages Open API schema definitions to accurately convert between JSON Schema types and their XML representations.

.. note::

    In the serialization process, tags are derived from schema definitions. In cases where they are unspecified, the system defaults to using "data" as the tag.

To illustrate, consider the following example showcasing the relation between an Open API definition, the generated data, and the serialized XML:

.. code-block::

   /upload:
      post:
        requestBody:
          content:
            application/xml:
              schema:
                additionalProperties: false
                properties:
                  id:
                    type: integer
                    xml:
                      # Specifies that 'id' should be serialized as an attribute
                      attribute: true
                required:
                - id
                type: object
          required: true

In this example, the generated data sample could look like this:

.. code-block:: python

    {"id": 42}

And the corresponding serialized XML data would be:

.. code-block::

    <PropertyAsAttribute id="42"></PropertyAsAttribute>

For more details on representing XML through Open API, refer to the `official documentation <https://swagger.io/docs/specification/data-models/representing-xml/>`_.

CSV data example
~~~~~~~~~~~~~~~~

In this example, we will define an operation that expects CSV data and setup a serializer for it.

Even though, Open API does not define a standard way to describe the structure of CSV payload, we can use the ``array``
type to describe it:

.. code-block::
   :emphasize-lines: 8-21

    paths:
      /csv:
        post:
          requestBody:
            content:
              text/csv:
                schema:
                  items:
                    additionalProperties: false
                    properties:
                      first_name:
                        pattern: \A[A-Za-z]*\Z
                        type: string
                      last_name:
                        pattern: \A[A-Za-z]*\Z
                        type: string
                    required:
                    - first_name
                    - last_name
                    type: object
                  type: array
            required: true
          responses:
            '200':
              description: OK

This schema describes a CSV structure with two string fields - ``first_name`` and ``last_name``. Schemathesis will
generate lists of Python dictionaries that can be serialized by ``csv.DictWriter``.

You are free to write a schema of any complexity, but be aware that Schemathesis may generate uncommon data
that your serializer will need to handle. In this example we restrict string characters only to ASCII letters
to avoid handling Unicode symbols for simplicity.

First, let's define a function that will transform lists of dictionaries to CSV strings:

.. code-block:: python

    import csv
    from io import StringIO


    def to_csv(data):
        if not data:
            # Empty CSV file
            return ""
        output = StringIO()
        # Assume all items have the same fields
        field_names = sorted(data[0].keys())
        writer = csv.DictWriter(output, field_names)
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()

.. note::

    You can take a look at the official `csv module documentation <https://docs.python.org/3/library/csv.html>`_ for more examples of CSV serialization.

Second, register a serializer class via the ``schemathesis.serializer`` decorator:

.. code-block:: python
   :emphasize-lines: 4

    import schemathesis


    @schemathesis.serializer("text/csv")
    class CSVSerializer:
        ...

This decorator requires the name of the media type you need to handle and optionally accepts additional media types via its ``aliases`` keyword argument.

Third, the serializer should have two methods - ``as_requests`` and ``as_werkzeug``.

.. code-block:: python

    ...


    class CSVSerializer:
        def as_requests(self, context, value):
            if isinstance(value, bytes):
                return {"data": value}
            return {"data": to_csv(value)}

        def as_werkzeug(self, context, value):
            if isinstance(value, bytes):
                return {"data": value}
            return {"data": to_csv(value)}

They should return dictionaries of keyword arguments that will be passed to ``requests.request`` and ``werkzeug.Client.open``, respectively.
With the CSV example, we create payload with the ``to_csv`` function defined earlier and return it as ``data``, which is valid for both cases.

Note that both methods explicitly handle binary data - for non-binary media types, it may happen if the API schema contains examples via the ``externalValue`` keyword.
In these cases, the loaded example is passed directly as binary data.

Additionally, you have ``context`` where you can access the current test case via ``context.case``.

.. important::

    Please, note that ``value`` will match your schema in positive testing scenarios, and it is your responsibility
    to handle errors during data serialization.