Additional features
===================

Schemathesis ships a set of optional features that could help to tune your tests.

Unique data generation
~~~~~~~~~~~~~~~~~~~~~~

.. important::

    The ``--contrib-unique-data`` CLI option and the corresponding ``schemathesis.contrib.unique_data`` hook are **DEPRECATED**. The concept of this feature
    does not fit the core principles of Hypothesis where strategies are configurable on a per-example basis but this feature implies
    uniqueness across examples. This leads to cryptic error messages about external state and flaky test runs, therefore it will be removed in
    Schemathesis 4.0

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

Fill missing examples
~~~~~~~~~~~~~~~~~~~~~

The ``--contrib-openapi-fill-missing-examples`` option complements ``--hypothesis-phases=explicit`` by generating a random example for operations that lack explicit examples in the schema.

In CLI:

.. code:: text

    $ st run --contrib-openapi-fill-missing-examples --hypothesis-phases=explicit \
          https://example.schemathesis.io/openapi.json

In Python tests:

.. code:: python

    from schemathesis.contrib.openapi import fill_missing_examples

    fill_missing_examples.install()

This feature ensures that all operations, even those without specified examples, are tested when running with ``--hypothesis-phases=explicit``.

.. note::

    This option is designed for users transitioning from Dredd to Schemathesis.

UUID data for ``format: uuid`` in Open API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Open API 2.0 / 3.0 do not declare the ``uuid`` format as built-in, hence it is available as an extension:

.. code:: python

    from schemathesis.contrib.openapi import formats

    formats.uuid.install()

You could also enable it via the ``--contrib-openapi-formats-uuid`` CLI option.

.. note::

    If you enable the OpenAPI 3.1 experimental feature, the UUID format support is automatically enabled. Refer to the :ref:`Experimental Features <experimental-openapi-31>` section for more details.
