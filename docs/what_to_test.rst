.. _selecting-api-operations-to-test:

Selecting API operations to test
================================

TODO.

You could also use simple jq-like expressions in ``--include-by`` to filter by arbitrary schema field:

.. code-block:: text

    schemathesis run http://example.com/spec.json --include-by="x-groupName == Verification"
