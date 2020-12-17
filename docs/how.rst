How it works
============

This section describes various aspects of Schemathesis behavior.

Payload serialization
---------------------

When your API accepts a payload, requests should have a media type that is located in their ``Content-Type`` header.
In Open API 3.0 you may write something like this:

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

In this example, operation ```POST /pet`` expects ``application/json`` payload. For each defined media type Schemathesis
generates data according to the relevant schema (``{"type": "object"}`` in the example).

.. note:: This data is stored in the ``case`` fixture that you use in tests when you use our ``pytest`` integration.

Before sending this data should be serialized to the format, expected for the tested operation. Schemathesis supports
most common media types like ``application/json`` and ``text/plain`` out of the box and allows you to add support for other
media types via the ``serializers`` mechanism.

For example, it is possible to test the following API with CSV data:


.. code-block::
   :emphasize-lines: 6-21

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

Then a basic serializer may look like this:

.. code-block:: python

    import csv
    from io import StringIO

    import schemathesis

    @schemathesis.serializers.register("text/csv")
    class CSVSerializer:

        def as_requests(self, context, value):
            return {"data": to_csv(value)}

        def as_werkzeug(self, context, value):
            return {"data": to_csv(value)}


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

Please, note, that ``value`` will correspond to your schema in positive testing scenarios, and it is your responsibility
to handle errors during data serialization.
