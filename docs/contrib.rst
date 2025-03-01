Additional features
===================

Schemathesis ships a set of optional features that could help to tune your tests.

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
