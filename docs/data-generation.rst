Data generation
===============

This section describes how Schemathesis generates test examples and their serialization process.

Schemathesis converts Open API schemas to compatible JSON Schemas and passes them to ``hypothesis-jsonschema``, which generates data for those schemas.

.. important::

    If the API schema is complex or deeply nested, data generation may be slow or produce data without much variance.
    It is a known behavior and caused by the way Hypothesis works internally.
    There are many tradeoffs in this process, and Hypothesis tries to give reasonable defaults for a typical case
    and not be too slow for pathological cases.

.. _data-generation-overview:

Overview
--------

For each API operation, Schemathesis follows a strict order in its test generation process, which includes several phases: explicit examples usage, rerunning previously failed tests, random test generation, and test case minimization (shrinking).
Each phase targets distinct testing aspects:

- **Explicit examples** (`explicit`): Uses predefined examples from the schema.
- **Rerunning known failures** (`reuse`): Reuses previously failed test cases to check whether they are still failing.
- **Random generation** (`generate`): Generates random test cases based on the schema.
- **Shrinking** (`shrink`): Minimizes the failing test cases to make them easier to understand and debug.

These phases can be selectively enabled or disabled to configure the testing process:

.. code:: shell

    # To disable the shrinking phase
    --hypothesis-no-phases=shrink

    # To only include the generate and shrink phases
    --hypothesis-phases=generate,shrink

Additionally, you can control the upper limit of generated test cases (only for the ``generate`` phase) via the ``--hypothesis-max-examples`` option.

.. code:: shell

    # Raise the upper cap for generated test cases per operation
    --hypothesis-max-examples=1000

The generation process is inherently randomized and is designed for efficient testing, favoring speed and maintaining reasonable coverage over exhaustive testing. 
It also means that Schemathesis does not guarantee the full coverage of all possible variants, but will do its best to generate a diverse set of test cases with minimal duplication.

The number of generated tests is affected by the schema's complexity and the distinctness of possible test cases.
Simple schemas with a clear set of distinct values, like a boolean, naturally limit the total number of unique test cases.
Conversely, complex schemas with many nested objects and arrays can produce a large number of unique test cases.

When Schemathesis finds a minimal failing test case, it stops the test case generation process and verifies whether the failure could be consistently reproduced by rerunning it one more time.
It also caches distinct minimized failures for reuse in subsequent runs, aiding in catching regressions.

Explicit examples
~~~~~~~~~~~~~~~~~

This phase uses examples directly from the API schema, filling missing parts with random data.
If the schema specifies multiple examples for a parameter, then Schemathesis will use a round-robin strategy to ensure all examples are tested.

.. code:: shell

   # Schema
  {
    "type": "object",
    "properties": {
      "name": {"type": "string", "example": "John"},
      "age": {"type": "integer", "examples": [42, 43]},
      "street": {"type": "string"}
    }
  }
  # Test cases
  {"name": "John", "age": 42, "street": "<RANDOM STRING>"}
  {"name": "John", "age": 43, "street": "<ANOTHER RANDOM STRING>"}

Reusing known failures
~~~~~~~~~~~~~~~~~~~~~~

Schemathesis stores failed test cases in a cache (the ``.hypothesis`` directory) and reruns them in subsequent runs to detect regressions.
This phase may reduce the total number of generated test cases as Schemathesis may find failures earlier.

Generating random test cases
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The generation process is governed by the ``--data-generation-method`` CLI option, and creates valid (``positive``) or guaranteed invalid (``negative``) test cases based on the schema. 
If negative testing is inapplicable (e.g., when any input is accepted), the tests are skipped.

Example:

.. code:: shell

    # Schema
    {
      "type": "object",
      "properties": {
        "name": {"type": "string"}
      }
    }

    # Positive testing
    {"name": "John"}

    # Negative testing
    {"name": 42}

The upper limit of generated test cases could be controlled via the ``--hypothesis-max-examples`` option.

Test case minimization
~~~~~~~~~~~~~~~~~~~~~~

This phase focuses on reducing the complexity of failing test cases to make them easier to understand and debug.

While beneficial for isolating issues in complex schemas, it can be time-consuming.
Disabling shrinking (``--hypothesis-no-phases=shrink``) may be advantageous when the source of an error is apparent and can be debugged straightforwardly.

.. code:: shell

    # Schema
    {
      "type": "object",
      "properties": {
        "name": {"type": "string", "minLength": 5}
      }
    }

    # Failing test case
    {"name": "Very long name"}

    # Minimized test case
    {"name": "aaaaa"}

Shrinking works for arbitrary complex structures allowing to avoid digging through large payloads.

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
