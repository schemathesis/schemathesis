GraphQL
=======

Schemathesis provides capabilities for testing GraphQL-based applications.

Usage
~~~~~

.. code:: python

    import schemathesis
    from hypothesis import settings

    schema = schemathesis.graphql.from_url("https://bahnql.herokuapp.com/graphql")


    @schema.parametrize()
    @settings(deadline=None)
    def test(case):
        case.call_and_validate()

This test will load GraphQL schema from ``https://bahnql.herokuapp.com/graphql``, generate queries for it, send them to the server, and verify responses.

Or via CLI:

.. code:: text

    st run --hypothesis-deadline=None https://bahnql.herokuapp.com/graphql

Limitations
~~~~~~~~~~~

Current GraphQL support does **NOT** include the following:

- Custom scalar types as required arguments (it will produce an error);
- Mutations;
- Filters;
