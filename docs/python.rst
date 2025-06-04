Writing Python tests
====================

Overriding test data
--------------------

You can set specific values for Open API parameters in test cases, such as query parameters, headers and cookies.

This is particularly useful for scenarios where specific parameter values are required for deeper testing.
For instance, when dealing with values that represent data in a database, which Schemathesis might not automatically know or generate.

To override parameters, use the ``schema.override`` decorator that accepts ``query``, ``headers``, ``cookies``, or ``path_parameters`` arguments as dictionaries.
You can specify multiple overrides in a single command and each of them will be applied only to API operations that use such a parameter.

For example, to override a query parameter and path:

.. code:: python

    schema = ...  # Load the API schema here


    @schema.parametrize()
    @schema.override(path_parameters={"user_id": "42"}, query={"apiKey": "secret"})
    def test_api(case):

This decorator overrides the ``apiKey`` query parameter and ``user_id`` path parameter, using ``secret`` and ``42`` as their respective values in all applicable test cases.

.. note::

    Of course, you can override them inside the test function body, but it requires checking whether the ones you want to override valid for the tested endpoint, and it has a performance penalty.
