Additional features
===================

Schemathesis ships a set of optional features that could help to tune your tests.

Unique data generation
~~~~~~~~~~~~~~~~~~~~~~

By default, Schemathesis may generate the same test cases as all data is randomized. If this behavior does not match your expectations, or
your test budges, you can force Schemathesis to generate unique test cases.

In CLI:

.. code:: text

    $ st run --contrib-unique-data https://example.schemathesis.io/openapi.json

In Python tests:

.. code:: python

    from schemathesis import contrib

    # This is a global hook that will affect all the tests
    contrib.unique_data.install()

Uniqueness is determined by the following parts of the generated data:

- ``media_type``
- ``path_parameters``
- ``headers``
- ``cookies``
- ``query``
- ``body``

UUID data for ``format: uuid`` in Open API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open API 2.0 / 3.0 do not declare the ``uuid`` format as built-in, hence it is available as an extension:

.. code:: python

    from schemathesis.contrib.openapi import formats

    formats.uuid.install()

You could also enable it via the ``--contrib-openapi-formats-uuid`` CLI option.
